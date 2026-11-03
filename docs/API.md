# 业务 HTTP 接口

所有业务地址使用同源 `/api` 前缀，无宽泛 CORS。接口复用 Store 和 MDService，不在路由中实现 SQL、版本更新或候选状态转换。`GET /` 返回网页，`GET /health` 仅表示 Web 存活，两者不连接业务数据库或提供器。

## 通用约定

成功响应为 `{"data": ...}`；新建会话/消息为 201，其余成功为 200。错误响应为 `{"error":{"code":"...","message":"中文提示"}}`。业务响应使用 `Cache-Control: no-store`。

写接口仅接受 JSON 对象，必须使用 `Content-Type: application/json`，字段必须符合白名单，无字段的确认/拒绝也须提交 `{}`。错误类型、缺失/额外字段和非法版本不自动纠正。金额和数量建议传十进制字符串，未知值用 `null`；成功返回的金额、数量和行情值均为字符串，不使用二进制浮点数。版本、序号等仍为整数，模拟标记为布尔值。精度与有限操作详见 [数据模型](DATA_MODEL.md)。

| 状态码 | code 示例 | 含义 |
| --- | --- | --- |
| 400 | `invalid_input` / `invalid_json` | 请求内容、角色字段、精度、版本类型、资产或指标无效 |
| 404 | `not_found` | 对象或接口不存在 |
| 405 | `method_not_allowed` | 接口不允许该方法，例如写入历史摘要 |
| 409 | `version_conflict` / `invalid_state` | 档案版本冲突，或候选状态不允许此操作 |
| 503 | `database_not_ready` | 未初始化或待升级，提示显式运行 init-db，不自动建库 |
| 503 | `database_schema_error` / `storage_unavailable` | 未知结构/较新版本或存储不可用，保留数据并检查 |
| 500 | `internal_error` | 未预期错误，仅返回通用提示，无堆栈、内部路径或敏感详情 |

MD 刷新尝试失败属于结构化业务结果，HTTP 仍为 200；调用方必须查看内层 `refresh_status` 和 `error`，不能只检查 HTTP 成功。

## 财务档案

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/profile` | 当前档案，包含 version、updated_at、基础字段、goals 和 holdings |
| `PATCH /api/profile` | 必须包含 expected_version 与 changes，返回保存后的当前档案 |

PATCH 示例（虚构修改，需使用实际读取到的版本）：

```json
{
  "expected_version": 2,
  "changes": [
    {"op":"set_finances","currency":"CNY","values":{"investment_budget":"800.00","debt":"0.00","income":null}},
    {"op":"set_risk","value":"medium"}
  ]
}
```

`expected_version` 必须是非负整数，布尔值、字符串、小数均拒绝。`changes` 使用已有的 set_finances、set_risk、set_goal、remove_goal、set_holding、remove_holding 操作。一次有效保存至多增加一个档案版本，子项修改也计入版本；无变化不加版本。冲突、非法操作或中途错误不会产生部分写入。客户端保留原输入，需人工重新读取核对，不自动替换版本重试。

已确定的基础币种不能直接修改，不做隐含换汇。网页输入读取原始字符串，空白映射为 null，明确的零不丢弃。

## 会话、原文与历史摘要

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/conversations` | 会话列表，按已有服务的稳定顺序返回 |
| `POST /api/conversations` | 仅 `{"title":"虚构预算讨论"}`，返回新会话，201 |
| `GET /api/conversations/<id>/messages` | 按会话内 sequence 排序的原始消息列表 |
| `POST /api/conversations/<id>/messages` | 仅 `{"content":"用户原文"}`，返回消息，201 |
| `GET /api/conversations/<id>/summaries` | 按版本排列的已有摘要，含覆盖序号闭区间和内容结构 |
| `GET /api/overview` | 最新 CA Overview，无记录时 data 为 null |

原文 POST 的角色仍由服务端固定为 user；请求即使传 role:user 也返回 400，成功只保存原文，不调用模型或生成摘要/候选。用户主动再次发送同一文本是另一条原始消息。会话有未完成聊天时，原文接口返回 409 防止插队。网页聊天改用下述专用接口，避免重复保存用户消息。

消息结果包含 id、conversation_id、sequence、role、content 和 created_at。摘要保留 version、first_sequence、last_sequence、content；Overview 保留 version、content 和 summary_ids。它们都是历史解释，不代表最新 SP。HTTP 不提供原始消息修改、摘要/Overview 写入或自动摘要入口。网页使用 textContent 安全显示原文，不把原文作为 HTML。

## 模型聊天

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/conversations/<id>/chat` | 返回 messages（稳定序号）和 turns（聊天请求状态），只读，不读取密钥或调用模型 |
| `POST /api/conversations/<id>/chat` | 仅 `{"content":"问题","request_id":"稳定标识"}`，保存问题、调用模型并保存回复 |
| `POST /api/conversations/<id>/chat/<request_id>/retry` | JSON `{}`，明确重试失败/占用期已结束的原问题，不新增用户消息 |

业务层是 `ChatService`。content 最多 4000 字符；请求 ID 使用原有 1–100 字符标识规则，网页生成 UUID。客户端不能指定 role、system、模型、密钥或任意上下文。用户问题只保存一次，调用模型期间不持写事务，助手消息和成功状态在同一事务保存。成功的相同请求或重复重试直接返回保存结果；同 ID 不同会话/文本为 409。新请求遇到未完成问题同样 409，不同会话可并行。

成功 HTTP 包装 `{"data":turn}`；turn 包含 id、conversation_id、status、can_retry、真实 user_message、assistant_message（尚无则 null）、model、usage、error、lease_until、updated_at。status 为 running/failed/succeeded，GET 将占用期结束但未确认完成的 running 显示为 interrupted；不在 GET 中更新数据库。usage 仅服务端提供的输入/输出/总计，可能 null，不估算。

模型失败或回复保存失败仍可返回 HTTP 200 的 turn，必须检查 status/error，不能把 HTTP 成功当聊天成功。failed 保留原文且不存假助手消息；save_failed 明确“已取得模型回复但未保存”，重试可能再次计费。若处理状态也无法保存，则返回 503/chat_storage_failed，须重读会话确认，不反复发送。running 默认至少占用 180 秒，未到期同请求不再调用；过期后必须明确重试，新 attempt_id 阻止迟到旧回复覆盖。没有自动重试、后台恢复或精确的外部计费一次性保证。

上下文为后端固定 system、精简 SP、可选最新 Overview、当前会话近期消息与本次问题；限制和配置见 README。SP/Overview 以数据消息提供，不提升为 system，不混入其他会话原文。原有 SP/摘要/Overview/候选状态不因聊天改变。模型异常不记录原始响应或认证信息，输出密钥会替换脱敏；文本用 textContent 渲染，不作为 HTML。

## 财务候选

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/profile-changes` | 全部候选，含 operations、来源 ID、base_version、status、创建/处理时间及 applied_version |
| `POST /api/profile-changes/<id>/confirm` | JSON `{}`，明确确认，返回处理结果 |
| `POST /api/profile-changes/<id>/reject` | JSON `{}`，明确拒绝，返回处理结果 |

列表额外组织 `source_conversation`（会话对象）与 `source_message`（来源用户消息），便于界面直接展示依据；确认/拒绝结果沿用服务对象，不附加这些展示字段。没有 HTTP 创建候选或通用编辑器，本轮演示使用 seed-demo 的已有记录。

只有 pending 可进入 confirmed/rejected。重复确认 confirmed 或重复拒绝 rejected 返回原结果，不重复执行。拒绝后确认、确认后拒绝均为 409；基于旧 SP 版本的候选确认返回 version_conflict，仍保留待确认状态。浏览器确认前再次列出具体修改，成功后读取当前档案和候选状态；仅查看不处理候选。

## 市场数据（mock / AKShare）

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/market/assets` | 当前提供器有限目录，含完整 asset_id、display_name、类型、市场、代码、metrics 的单位/币种，以及 provider_id、is_simulated，无数据库也可读取 |
| `GET /api/market/cache?asset_id=...&metric=...` | 只读已有缓存；两项参数各出现一次，不接受额外参数 |
| `POST /api/market/refresh` | 仅 asset_id 与 metric，显式调用 refresh |

刷新请求示例：

```json
{"asset_id":"demo:fund:DEMO_FUNDS:DEMO001","metric":"nav"}
```

返回形状示例（值和时间仅用于说明）：

```json
{
  "data": {
    "data": {
      "asset_id":"demo:fund:DEMO_FUNDS:DEMO001",
      "metric":"nav","value":"1.23456789","currency":"CNY","unit":"currency_per_unit",
      "provider_id":"mock-fixed-v1","is_simulated":true,
      "data_time_kind":"date","data_time":"2026-01-14",
      "fetched_at":"2026-10-28T00:00:00.000000Z",
      "expires_at":"2026-10-28T00:05:00.000000Z","revision":1
    },
    "cache_status":"fresh","refresh_status":"updated","error":null
  }
}
```

外层 data 是统一 HTTP 包装，内层 data 是 MD 记录或 null。保留三种缓存状态 fresh/stale/missing 与五种刷新状态 not_requested/not_needed/updated/failed/superseded；错误仍含 code/message。GET 不获取、不延长有效期，页面渲染也不会偷偷调用刷新。股票价格为货币/股、基金净值为货币/份、指数点位为 points 且 currency=null，不相互冒充。

三个接口统一通过 `MDService.from_app(app)` 选择目录和提供器；配置默认 `MD_PROVIDER_ID=mock-fixed-v1`，显式选择 `akshare-real-daily-v1` 后返回三个真实资产。配置错误在应用创建时明确报错，不回退。环境变量切换、重启和 CLI 查看方法见 [README](../README.md#akshare-真实日线切换刷新与查看)。请求体不允许临时指定其他提供器，须使用当前目录完整 ID；跨模式的 ID 返回 400。

真实刷新示例：`{"asset_id":"cn:stock:SHA:600519","metric":"price"}`。真实记录 `provider_id=akshare-real-daily-v1`、`is_simulated=false`、`data_time_kind=date`。茅台为东方财富不复权日线收盘价，上证为新浪日线收盘点位，001186 为天天基金/东方财富正式单位净值；页面说明对应上游。15:00 前排除股票/指数未完成当日 K 线，基金允许公布延迟。数据日期与 fetched_at 分开，不保证日期等于今天。

默认 TTL 300 秒，`fetched_at <= now < expires_at` 才 fresh，到期即 stale。fresh 不等于实时，必须显示模拟标记、数据时间和来源；基金只有日期时保留日期语义。响应错误时旧记录、获取时间和过期时间不变；有旧缓存则返回旧值及错误，无旧值则 data=null、missing、failed，不用 0 替代。superseded 返回已经保存的缓存及提示，不自动循环重试。提供器异常详情替换为安全中文提示，错误 code 与状态不丢失。

## 上手与检查

初始化、演示、启动和隔离人工检查步骤统一见 [README](../README.md#网页使用)。结构版本为 3，仅新增 003 聊天迁移，001/002 未改；无重置、清库、执行任意命令、注册登录或交易接口。
