# 业务 HTTP 接口

所有业务地址使用同源 `/api` 前缀，无宽泛 CORS。接口复用 Store、MDService、ChatService 和 ArchiveService，不在路由中实现 SQL、版本更新或候选状态转换。`GET /` 返回网页，`GET /health` 仅表示 Web 存活，两者不连接业务数据库或提供器。

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

消息结果包含 id、conversation_id、sequence、role、content 和 created_at。摘要保留 version、first_sequence、last_sequence、content；Overview 保留 version、content 和 summary_ids。它们都是历史解释，不代表最新 SP。HTTP 不提供原始消息修改或任意摘要/Overview 写入，生成只能走下述显式归档。网页使用 textContent 安全显示原文，不把原文作为 HTML。

## 模型聊天

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/conversations/<id>/chat` | 返回 messages（稳定序号）和 turns（聊天请求状态），只读，不读取密钥或调用模型 |
| `POST /api/conversations/<id>/chat` | 仅 `{"content":"问题","request_id":"稳定标识"}`，保存问题、调用模型并保存回复 |
| `POST /api/conversations/<id>/chat/<request_id>/retry` | JSON `{}`，明确重试失败/占用期已结束的原问题，不新增用户消息 |

业务层是 `ChatService`。content 最多 4000 字符；请求 ID 使用原有 1–100 字符标识规则，网页生成 UUID。客户端不能指定 role、system、模型、密钥或任意上下文。用户问题只保存一次，调用模型期间不持写事务，助手消息和成功状态在同一事务保存。成功的相同请求或重复重试直接返回保存结果；同 ID 不同会话/文本为 409。新请求遇到未完成问题同样 409，不同会话可并行。

成功 HTTP 包装 `{"data":turn}`；turn 包含 id、conversation_id、status、can_retry、真实 user_message、assistant_message（尚无则 null）、model、usage、error、lease_until、updated_at。status 为 running/failed/succeeded，GET 将占用期结束但未确认完成的 running 显示为 interrupted；不在 GET 中更新数据库。usage 仅服务端提供的输入/输出/总计，可能 null，不估算。

模型失败或回复保存失败仍可返回 HTTP 200 的 turn，必须检查 status/error，不能把 HTTP 成功当聊天成功。failed 保留原文且不存假助手消息；save_failed 明确“已取得模型回复但未保存”，重试可能再次计费。若处理状态也无法保存，则返回 503/chat_storage_failed，须重读会话确认，不反复发送。T08 工具版默认占用510秒，未到期同请求不再调用；过期后必须明确重试，新 attempt_id 阻止迟到旧回复覆盖。没有自动重试、后台恢复或精确的外部计费一次性保证。

上下文为后端固定 system、精简 SP、可选最新 Overview、当前会话近期消息与本次问题；限制和配置见 README。SP/Overview 以数据消息提供，不提升为 system，不混入其他会话原文。原有 SP/摘要/Overview/候选状态不因聊天改变。模型异常不记录原始响应或认证信息，输出密钥会替换脱敏；文本用 textContent 渲染，不作为 HTML。

### 聊天工具（T08，无新增HTTP地址）

沿用上述 POST chat/retry；浏览器仍只发送问题和 request_id，不能指定工具、SQL、URL、角色或认证信息。默认模型路径经 `ChatTools` 执行白名单工具，普通文本回复无额外工具轮次；归档 JSON 路径不注册工具。工具调用和结果按 tool_call_id 配对，仅用于当次模型上下文，不写入 messages，不影响 T07 完整轮次识别。

| 工具 | 必填参数与边界 | 结果 |
| --- | --- | --- |
| search_history | keyword非空文本1–80字符；limit整数1–5 | ok/matched/records，最新覆盖版本的摘要摘录，含id、conversation_id、version、first/last_sequence、created_at及会话标题 |
| query_market | asset_id为当前目录完整ID；metric为该资产支持指标 | ok、asset、data、display_unit、cache_status、refresh_status、error、notice；data沿用MD规范，无数据为null |
| calculate_savings | target/saved/monthly三个输入对象，见下例 | inputs含来源/币种/SP版本、gap精确文本、months整数或null、status、缺项/口径说明 |

计算工具模型参数示例（不是新的公开请求体）：

```json
{"target":{"source":"user","value":"12000","currency":"CNY","goal_id":null},"saved":{"source":"user","value":"3000","currency":"CNY","goal_id":null},"monthly":{"source":"user","value":"1500","currency":"CNY","goal_id":null}}
```

source=user仅表示用户明确情景，value为基本单位十进制字符串或null，currency为三位大写币种或null，goal_id必须null。source=sp_goal仅用于target，提供goal_id、value/currency必须null；source=sp_savings_budget仅用于monthly，其余三字段必须null。后端读取当前SP，不信任模型伪造SP值；没有目标已存字段，saved只接受用户输入，不能默认为现金。输出status为calculated/reached/unreachable/missing_input/currency_mismatch；未知不作零，币种不同不计算，不计收益或换汇，已达标无需月存入也返回0个月。

missing_input、currency_mismatch、unreachable表示工具正常执行但没有可用月数（months=null），并非模型/网络异常。后端根据结构化状态生成固定中文说明：列出缺少的具体金额/币种、币种不一致且不换汇，或尚有差额但月存入为零；不把这些情况显示为0个月。该说明通过原有message字段交给后续模型，不增加工具结果字段。最新计算无可用月数且没有其他可用结果时，最终assistant回复直接使用后端说明，不保存模型猜测的月数。其他结果是否可用复用来源提取逻辑：历史须有实际命中的records，行情须有可展示的data（包括标记为stale的旧缓存）；空结果、失败且无数据、因工具上限跳过均不算可用。确有可用历史或行情时保留模型回答和其他工具依据，另列“储蓄计算（后端结果）”；无匹配历史或无可用行情可追加固定提示。正常计算和已达标同样附后端确定结果；已达标明确为0个月。模型补齐参数再算时，以最后一次结构合法的计算结果为准，最终状态说明不累积已经解决的缺项。接口返回与聊天历史保存相同最终正文；原有最终回复事务、成功重放、模型失败与明确重试规则不变。

所有对象拒绝多余字段、重复JSON键、错误类型/精度及非有限数值。每次参数最多4000字符，单项结果最多6000字符，工具后的序列化消息最多CHAT_CONTEXT_CHARS+32000字符。历史查询使用参数化SQL和json_each，仅对摘要各类别数组中的实际文本条目做字面子串匹配（英文不区分大小写，%和_不是通配符），不匹配JSON字段名，也不搜索其他会话原文。同会话/同覆盖范围仅选择最新版本；筛选正文后再按稳定倒序取limit条，不能先截取若干摘要再过滤。正文确实包含confirmed等词时仍可匹配。摘录优先含关键词的条目，最多500个正文字符/记录，带is_excerpt=true和原有来源标识，不承诺完整历史检索。

默认2轮工具、4次执行（无效调用也占预算）、至多3次模型请求。用完后禁止继续调用，若仍返回工具则turn为failed/error.code=tool_limit，无伪造回复。未知工具/非法参数/工具异常返回结构化error给模型，最终文本附固定工具提示；行情失败有旧值时保留旧值、状态和错误。历史/行情成功结果在最终assistant文本追加后端生成的“工具依据”，不依赖模型转述来源、单位和状态的准确性；这是最终回复的一部分，非额外工具消息。MD可识别的HTTP429为rate_limit，其余无明确状态的错误不猜测限流。工具不写SP/CA，只允许MD正常缓存更新；失败重试可能重做工具和外部请求，成功重放始终读取最终已保存结果。usage为每轮都实际提供的同名字段之和，缺失轮次不估算。配置和演示见[README](../README.md#聊天工具与-mvp-演示t08)。

## 显式对话归档

业务入口为 `ArchiveService.from_app(app)`，或可注入 model/clock 的 `ArchiveService(database_path)`；status(conversation_id) 只读，generate(conversation_id, first_sequence=..., last_sequence=...) 发起归档，generate(conversation_id, retry_id=...) 明确重试。

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/conversations/<id>/archive` | 返回 latest（最新成功结果）、pending（未完成结果）、can_archive、next_range、notice；不读取密钥/联网/写库 |
| `POST /api/conversations/<id>/archive` | 仅 `{"first_sequence":1,"last_sequence":2}`，使用 GET 的 next_range；后端核验为下一完整分段或已有范围 |
| `POST /api/conversations/<id>/archive/<archive_id>/retry` | JSON `{}`，明确重试失败/占用期结束的原范围 |

消息范围为正整数闭区间，不接受布尔值。客户端不能传摘要、候选、提示词、角色、密钥或任意历史。可归档内容及边界见 README；可选 Python 直接调用不传范围时由后端选择下一段，HTTP 强制携带范围，以便相同批次重复点击幂等。过时范围为409；对象不存在404。

返回 `data` 为归档结果：id、conversation_id、first_sequence、last_sequence、status、can_retry、lease_until、summary、overview_id、citations（与摘要各类条目一一对应的消息 ID 列表）、candidate_count（本次新建数）、change_ids（关联候选，含复用的旧候选）、model、usage、error。status 为 running/failed/succeeded，过期 running 只读显示 interrupted。失败仍可能 HTTP200，必须看 status/error；候选后续确认/拒绝的当前状态应从候选接口读取。

成功的相同范围返回保存结果，不调用模型；失败须原 ID 的明确重试。未到180秒占用期限不再次请求，过期换尝试令牌，迟到回复不写入。不同会话并行完成时，在短事务内基于最新 Overview 来源合并，不覆盖先完成的其他会话。模型或校验失败仅保存固定错误，提交失败整体回滚；状态也不能保存时为503/archive_storage_failed。外部可能已计费，不承诺计费恰好一次。

归档仅创建pending候选，复用确认/拒绝流程。模型负责理解意图与规范化数值；后端检查单条候选的操作/字段/类型/精度/币种契约，以及本批已保存的user消息ID和完整原文引用，不从正文再次提取金额或意图。非法字段、金额、币种或来源仅跳过该候选，其他合法候选与摘要继续保存；整个响应或摘要结构/引用无效仍走失败流程。来源真实不等于模型语义正确，必须由用户在确认界面同时核对拟写值和原文。

候选模型输出沿用 `{source_message_id,evidence,operations}`。仅支持set_finances和set_risk；金额为币种基本单位的十进制字符串或null，currency单独返回。例如CNY下“900，单位是万元”应由模型返回 `{"op":"set_finances","currency":"CNY","values":{"investment_budget":"9000000"}}`。后端不会从原文验证这次换算是否正确；会通过既有Decimal规则转为 `"9000000.00"` 待用户确认。金额字符串不能含符号、千分位、倍率或需要舍入的精度，不接受JSON数值；未知为null、零为 `"0"`。重复来源保留首个通过契约校验的候选（含无变化项），无变化字段过滤，数据库来源去重继续有效。

模型输入只含本次完成轮次和精简确认SP，不扫描其他会话原文。摘要/Overview/合法候选一次提交，原始消息和SP不变；GET或普通聊天均不会自动归档。Overview部分覆盖和100来源保留规则见数据模型；历史memory_limit失败记录可按同一ID明确重试，无需改库。HTTP确认接口与版本、事务规则不变。

## 财务候选

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/profile-changes` | 全部候选，含 operations、来源 ID、base_version、status、创建/处理时间及 applied_version |
| `POST /api/profile-changes/<id>/confirm` | JSON `{}`，明确确认，返回处理结果 |
| `POST /api/profile-changes/<id>/reject` | JSON `{}`，明确拒绝，返回处理结果 |
| `POST /api/profile-change-batches/<archive_id>/confirm` | 仅 `{"change_ids":["候选ID1","候选ID2"]}`，合并确认指定归档批次全部剩余待确认项 |

列表额外组织 `source_conversation`（会话对象）、`source_message`（来源用户消息）及 `archive_id`（成功归档批次，无关联时null），便于界面直接展示依据并分组；单条确认/拒绝结果沿用服务对象，不附加这些展示字段。没有任意创建候选或通用编辑器接口；候选可来自既有 seed-demo 或显式归档，后者须经过来源引用与输出契约校验，不证明模型语义正确。

只有 pending 可进入 confirmed/rejected。重复确认 confirmed 或重复拒绝 rejected 返回原结果，不重复执行。拒绝后确认、确认后拒绝均为 409；基于旧 SP 版本的候选确认返回 version_conflict，仍保留待确认状态。浏览器确认前再次列出具体修改，成功后读取当前档案和候选状态；仅查看不处理候选。

合并服务入口 `Store.confirm_change_batch(archive_id, change_ids)`；列表关联来自只读 `Store.change_archive_ids()`。change_ids为1–100个不重复标识，必须属于成功的同一归档批次；除幂等读取已确认集合外，必须恰好等于当前全部pending项。用户不想应用的项应先用原拒绝接口处理，再读取列表并提交余项。重复ID/跨批次为400，批次不存在404，状态或选择过时、同字段不同目标值为409/invalid_state，SP基准已变化为409/version_conflict。

成功返回 `data:{archive_id,applied_version,changes,profile}`。changes为本次集合的已确认候选；profile是响应时读取的当前SP。相同字段/目标值合并去重，互不冲突字段一起通过原 `_apply` 更新，SP有效变化只增加一个版本，所有项获得相同applied_version。确认过程中任意写入失败则全部回滚。相同集合重复提交时若已确认且applied_version一致，返回已应用结果，不因SP后来变化再次覆盖；返回profile可能已被后续操作更新，历史结果以applied_version为准。单条接口不变，不自动重基准；已单条确认部分候选后，其余旧版本仍受保护。

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

初始化、演示、启动和隔离人工检查步骤统一见 [README](../README.md#网页使用)。结构版本为 4，本轮仅新增 004 归档迁移，001–003 未改；无重置、清库、执行任意命令、注册登录或交易接口。
