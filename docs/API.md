# T04 HTTP 接口

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

消息角色由服务端固定为 user；请求即使传 `role:"user"` 也因额外字段返回 400，assistant/system/tool 同样不允许。成功只保存原文，不插入回复、摘要或候选；用户主动再次发送同一文本是另一条有效消息。网页在请求期间锁定发送按钮并绑定点击时的会话 ID，失败保留文本，较晚的旧会话读取不会覆盖新会话。

消息结果包含 id、conversation_id、sequence、role、content 和 created_at。摘要保留 version、first_sequence、last_sequence、content；Overview 保留 version、content 和 summary_ids。它们都是历史解释，不代表最新 SP。HTTP 不提供原始消息修改、摘要/Overview 写入或自动摘要入口。网页使用 textContent 安全显示原文，不把原文作为 HTML。

## 财务候选

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/profile-changes` | 全部候选，含 operations、来源 ID、base_version、status、创建/处理时间及 applied_version |
| `POST /api/profile-changes/<id>/confirm` | JSON `{}`，明确确认，返回处理结果 |
| `POST /api/profile-changes/<id>/reject` | JSON `{}`，明确拒绝，返回处理结果 |

列表额外组织 `source_conversation`（会话对象）与 `source_message`（来源用户消息），便于界面直接展示依据；确认/拒绝结果沿用服务对象，不附加这些展示字段。没有 HTTP 创建候选或通用编辑器，本轮演示使用 seed-demo 的已有记录。

只有 pending 可进入 confirmed/rejected。重复确认 confirmed 或重复拒绝 rejected 返回原结果，不重复执行。拒绝后确认、确认后拒绝均为 409；基于旧 SP 版本的候选确认返回 version_conflict，仍保留待确认状态。浏览器确认前再次列出具体修改，成功后读取当前档案和候选状态；仅查看不处理候选。

## 模拟行情

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/market/assets` | 有限目录，含完整 asset_id、display_name、类型、市场、代码及 metrics 的单位/币种，无数据库也可读取 |
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

默认 TTL 300 秒，`fetched_at <= now < expires_at` 才 fresh，到期即 stale。fresh 不等于实时，必须显示模拟标记、数据时间和来源；基金只有日期时保留日期语义。响应错误时旧记录、获取时间和过期时间不变；有旧缓存则返回旧值及错误，无旧值则 data=null、missing、failed，不用 0 替代。superseded 返回已经保存的缓存及提示，不自动循环重试。提供器异常详情替换为安全中文提示，错误 code 与状态不丢失。

## 上手与检查

初始化、演示、启动和隔离人工检查步骤统一见 [README](../README.md#网页使用)。本轮结构版本仍为 2，001/002 未改；没有重置、清库、执行任意命令、注册登录、模型调用、真实行情或交易接口。
