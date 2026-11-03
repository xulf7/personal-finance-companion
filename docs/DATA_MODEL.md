# SP/CA/MD 数据模型与服务

当前为单用户、单份有效档案，使用标准库 `sqlite3`，没有 ORM。网页与 HTTP 接口通过服务读写；T05 新增聊天请求状态，T07 新增显式归档状态，当前结构版本 4。唯一真实状态来源是 SP；摘要与 Overview 属于历史解释，保存它们不会写入 SP。

MD 独立保存有限资产定义与当前行情缓存，默认 mock 固定虚构数据，也支持显式选择 AKShare 真实日线；均不保证实时性。T07 仅新增 004，不改写 001–003 或 SP/CA/MD 确认规则。

## 表关系与字段

| 表 | 职责与关键字段 |
| --- | --- |
| `schema_migrations` | 已应用的结构版本、SQL 文件名、SHA-256、应用时间；同时维护 `PRAGMA user_version` |
| `profile` | 单例 `id=1`，档案 `version`、`updated_at`、基础财务币种、收入/支出/现金/负债/储蓄预算/投资预算、风险偏好 |
| `goals` | 关联 profile；稳定 `id`、名称、目标金额、币种、目标日期 |
| `holdings` | 关联 profile；稳定 `id`、资产标识、数量、单位成本、币种；不计算实时市值 |
| `conversations` | 稳定 ID、标题、创建时间 |
| `messages` | 稳定 ID、会话 ID、会话内 `sequence`、角色、原始文本、时间；会话内序号唯一 |
| `summaries` | 稳定 ID、会话 ID、会话内摘要版本、起止消息序号、结构化内容、创建时间 |
| `overviews` | 稳定 ID、全局版本、结构化内容、输入哈希、创建时间 |
| `overview_sources` | Overview 与来源摘要的多对多关联，保留具体历史版本 |
| `profile_changes` | 结构化操作、来源用户消息及会话、基于的 SP 版本、状态、创建/处理时间、应用后版本 |
| `demo_seeds` | 虚构演示的完成标记；在整个填充事务成功后才写入 |
| `md_assets` | 完整内部资产 ID、显示名称、资产类型、市场和代码；成功写入首条行情时保存定义 |
| `md_cache` | 每个资产/指标/提供器/模拟标记的一条当前缓存，关联 `md_assets`，不关联或修改 SP/CA |
| `chat_requests` | 稳定请求 ID、会话、用户/助手消息关联、运行状态、尝试令牌、占用期限、错误、响应模型与实际用量 |
| `archive_requests` | 会话和消息范围唯一，归档状态/尝试令牌/占用期限，首次SP精简快照，摘要/Overview关联，逐项消息引用、新建候选数、模型用量与固定错误 |
| `archive_evidence` | 原始用户消息唯一，关联其所属会话、归档请求及候选；防止同一证据产生重复候选，包括已拒绝记录 |

会话 → 消息/摘要是一对多；摘要通过 `(conversation_id, first_sequence/last_sequence)` 外键关联消息端点，覆盖同会话内闭区间。候选通过复合外键关联所属会话的来源消息，服务额外验证其角色为 `user`。所有服务连接均启用外键，未提供删除原始消息、覆盖历史摘要或删除会话的服务。

实体 ID 由服务生成 UUID 十六进制文本；目标/持仓 ID 由调用方提供稳定字符串（1–100 字符，字母或数字开头，可含 `_.-`）。会话按创建时间、ID 排序；消息始终按会话内序号排序，不依赖时间戳分辨率。写入消息时在事务内分配递增序号。时间均为 UTC ISO 8601 文本，以 `Z` 结尾；目标日期为 `YYYY-MM-DD` 或未知。

## 精确数值与 SP 版本

- 金额、单位成本使用 `Decimal` 校验并以固定两位小数 `TEXT` 保存；数量固定八位小数。整数部分最多 18 位，当前不接受负数。超出精度且需要舍入的输入会报错，额外的尾随零可接受。
- 输入接受十进制字符串、整数或 `Decimal`，拒绝 `float`、布尔值、NaN 和无穷值。返回值仍为十进制文本，计算时应显式构造 `Decimal`。
- 未知值使用 SQL `NULL`，Python 返回 `None`；`"0.00"` 是明确的零，二者不能互换。新档案所有财务值、币种和风险偏好均未知，档案版本为 0。
- `profile.currency` 是基础财务字段的共同币种，首次 `set_finances` 确定；之后不支持直接改币种。目标和持仓各自保存币种，不做隐含汇率转换。收入、支出及两个预算按月记录，现金/负债为当前余额；保持同一口径，由调用方提供已确认的值。
- 风险偏好为 `None`、`low`、`medium`、`high`。
- 一次明确编辑或候选确认，无论修改多少主字段/目标/持仓，只要最终有效内容发生变化，SP 版本加 1 并更新时间。完全无变化时不加版本；即使无变化也必须校验基于的版本。
- 当前不保存完整 SP 快照历史；变化解释依靠原始消息、摘要与候选记录。明确表单编辑不是自动提取，调用方须先取得用户明确保存意图。

## 服务入口与修改结构

`from app.storage import Store`。默认 `Store()` 使用代码根目录下的 `runtime/data/app.sqlite3`；`Store.from_app(app)` 使用配置中的数据库路径。相对路径也以代码根目录解释；持久化路径限定在项目文件夹内。每次普通调用独立打开、提交/回滚并关闭连接。

| 服务方法 | 用途 |
| --- | --- |
| `get_profile()` | 读取 SP，包含按 ID 排序的 `goals` 和 `holdings` |
| `edit_profile(changes, expected_version=...)` | 用户在表单明确保存；校验版本后原子应用 |
| `create_conversation(title)` / `list_conversations()` / `get_conversation(id)` | 创建、列出、读取会话 |
| `add_message(conversation_id, role, content)` | 保存原文；角色仅 `user/assistant/system/tool` |
| `get_message(id)` / `list_messages(conversation_id)` | 读取消息，列表按稳定序号排列 |
| `save_summary(conversation_id, first_message_id, last_message_id, content)` | 明确写入会话摘要，校验起止范围 |
| `get_summary(id)` / `list_summaries(conversation_id)` | 读取某版摘要或按版本列出 |
| `save_overview(content, summary_ids)` | 写入关联具体摘要版本的 Overview |
| `get_overview(version=None)` | 默认最新版本；未保存时返回 `None`，指定不存在版本报错 |
| `create_change(conversation_id, source_message_id, changes, base_version=...)` | 创建候选，不改 SP |
| `get_change(id)` / `list_changes(status=None)` | 读取候选，可按状态筛选 |
| `confirm_change(id)` / `reject_change(id)` | 明确确认或拒绝候选 |

输入的 `changes` 是 1–100 项操作列表。仅支持以下完整结构（JSON 中的 `null` 对应 Python `None`）：

```json
[
  {"op":"set_finances","currency":"CNY","values":{"income":"6500.00","expenses":"4000.00","cash":"20000.00","debt":"0.00","savings_budget":null,"investment_budget":"800.00"}},
  {"op":"set_risk","value":"medium"},
  {"op":"set_goal","goal":{"id":"emergency","name":"虚构应急储蓄","target_amount":"12000.00","currency":"CNY","target_date":"2027-12-31"}},
  {"op":"set_holding","holding":{"id":"demo","asset_code":"FICTIONAL_ASSET","quantity":"1.25000000","unit_cost":"10.20","currency":"USD"}}
]
```

`set_finances.values` 可只提供其中需要修改的字段；其余保留。`set_goal`/`set_holding` 按 ID 新增或完整替换子项，示例所列字段全部必需，允许未知的金额/数量/日期字段使用 `None`。删除用 `{"op":"remove_goal","id":"emergency"}` 或 `{"op":"remove_holding","id":"demo"}`，不存在时报错并回滚整次修改。未知操作、额外字段和任意字段名均拒绝；外部值通过参数化 SQL 传入，不接受 SQL 或代码。

## 摘要、Overview 与幂等规则

摘要和 Overview 采用同一有界结构，七个字段均必需且均为文本列表；每列表最多 30 项，每项 1–2000 字符：

```json
{
  "facts": ["虚构示例：用户本次说明支出减少"],
  "decisions": ["用户决定调整每月投资预算"],
  "reasons": ["每月可支配余额增加"],
  "plans": ["讨论过将来提高到 1000，尚未确认"],
  "confirmed": ["本次已明确确认投资预算为 CNY 800.00"],
  "todos": [],
  "unresolved": []
}
```

`facts` 是观察到的重要事实；`plans` 是假设或未来打算；`confirmed` 是摘要作者记录的当时确认状态，仍不具有修改当前 SP 的权限。摘要由明确服务调用或用户点击归档后生成，不在普通聊天中自动生成；不检验陈述的金融真实性。

摘要按会话独立递增版本并保留旧版，覆盖范围必须来自该会话，起点不得晚于终点。同会话、同闭区间、规范化后相同内容再次保存，返回原摘要，不产生新版本；内容或范围变化才新增。读取 `list_summaries()` 的最后一项可取得最新版本。

Overview 使用全局递增版本，至少引用一个存在的摘要，最多传入 100 个标识；来源去重并排序。相同内容和同一组来源（传入顺序无关）再次保存返回原版本。重复提交某个旧输入不会把它重新提升为最新 Overview；读取旧版可指定 `version`。原始消息、旧摘要和旧 Overview 始终保留，保存摘要/Overview 不修改 SP。

## 候选状态与错误

候选从 `pending` 进入 `confirmed` 或 `rejected`。创建时验证来源消息存在、属于指定会话且角色为用户；不接受未来的 SP 版本，旧版本候选可以保存但确认时会冲突。

确认在同一个 `BEGIN IMMEDIATE` 事务中重新读取当前 SP、校验 `base_version`、应用有限操作、按需更新档案版本、最后写候选状态及 `applied_version`/处理时间。任何一步失败，SP、子项、版本和候选状态整体回滚。并发确认同版本的不同候选时，后执行者必须面对更新后的版本。

重复确认已确认候选直接返回原结果，不再次应用。拒绝不改 SP，重复拒绝返回原结果；已拒绝候选不能确认，已确认候选不能拒绝。需要修正时创建新候选或明确表单编辑，不改写历史。

`app.errors` 提供 `ValidationError`（无效输入）、`NotFound`、`VersionConflict`、`InvalidState`、`InitializationRequired`、`SchemaError`，均属于 `StorageError`。底层 SQL/文件系统错误仍会抛出并回滚，不会吞掉后返回成功。

## 初始化、备份与新增迁移

```powershell
python -m flask --app app:create_app init-db
python -m flask --app app:create_app seed-demo
python -B -m unittest discover -s tests -v
```

当前 001 建立 SP/CA、002 新增 MD、003 新增聊天请求、004 新增归档工作流，结构版本为 4，与 SP 档案版本是两回事。初始化重复执行不丢失数据；已有版本 3 先备份再升级，只新增结构，不重填数据。启动和健康检查不连接业务库或模型。命令没有清库选项。

下一次新增结构按顺序添加 `005_说明性英文名.sql`（文件名使用小写英文/数字/下划线），保留旧 SQL 原样。每个语句以分号结束，语句之间换行；不要写 BEGIN/COMMIT/ROLLBACK、自行修改版本记录或关闭外键，事务由 Python 管理。迁移须同时适用于已有库与从 001 顺序创建的新库；在独立临时库验证升级与失败回滚。

初始化会核对版本、迁移文件 SHA-256（按 UTF-8 文本统一为 LF 后计算）、实际表/索引/约束结构及外键。发现未知旧表、结构被手改、迁移记录不匹配或版本高于代码时，停止并保留数据，需人工核对或编写兼容迁移，不能删库解决。SQL 迁移随代码复制并纳入 Git，数据库和备份不纳入 Git。

实际升级已初始化的旧版本前，先取得写事务锁，再通过 SQLite backup API 生成 `runtime/backups/` 中唯一命名的有效快照并检查完整性；备份内容是升级前版本。备份成功才执行迁移。所有待执行 SQL 与版本记录在同一事务中提交；失败回滚并保留备份，错误提示包含备份路径。首次空库或无待执行迁移时不备份；首次迁移失败可能留下一个无业务表的空文件，可再次显式初始化，程序不会自动删除它。

虚构演示整批写入也在单个事务中；重复运行按种子标记返回而不写入，遇到非空业务数据或已编辑 SP 拒绝填充。真实开发数据存在时应保留，测试始终使用独立路径。

## MD 资产与规范化记录

`app/md_provider.py` 的有限目录定义完整内部 ID、名称、类型、市场、代码、支持指标、单位和币种。相同短代码可属于不同类型或市场，服务不猜测映射；只接受目录中的完整 ID 与指标。当前每个资产支持一个指标，后续按实际接口需要扩展目录。

| 资产 ID | 类型 | 指标 | 单位 / 币种 | 固定虚构值 |
| --- | --- | --- | --- | --- |
| `demo:stock:DEMO_EXCHANGE:DEMO001` | stock | price | currency_per_share / CNY | 123.45678901 |
| `demo:fund:DEMO_FUNDS:DEMO001` | fund | nav | currency_per_unit / CNY | 1.23456789 |
| `demo:index:DEMO_EXCHANGE:DEMO001` | index | index_level | points / NULL | 3456.78901234 |

名称均包含“虚构演示”，市场为 `DEMO_EXCHANGE` 或 `DEMO_FUNDS`，短代码均为 `DEMO001`。`md_assets` 对 `(asset_type, market, code)` 设唯一约束；资产定义与目录不一致时拒绝覆盖。

提供器提供 `provider_id`、布尔型 `is_simulated`、有限 `assets` 目录和 `fetch(asset, metric)`。服务仍兼容未声明 assets 的已有模拟替身（使用 ASSETS），真实实现显式声明 REAL_ASSETS。返回字典必须包含以下全部字段，不接受额外字段：

| 字段 | 语义 |
| --- | --- |
| `asset_id` / `metric` | 必须与请求一致 |
| `value` | 非负有限精确值；字符串、整数或 Decimal，最多 18 位整数和 8 位有效小数 |
| `currency` / `unit` | 必须匹配该资产的指标定义；指数为 `NULL` 和 `points` |
| `provider_id` / `is_simulated` | 必须匹配注入的提供器声明，布尔值不能用 0/1 冒充 |
| `data_time_kind` | `date` 或 `datetime` |
| `data_time` | 数据本身的日期或带时区时间，与获取时间不同 |

MD 的 `value` 规范化后保存为八位小数文本，不接受浮点数、布尔值、NaN、无穷值或需要舍入的数值；额外尾随零可接受。缺失字段或 `None` 值视为无效响应，不写入缓存、不用零替代；明确提供的零是合法值。MD 复用数值校验的八位参数，未修改 SP 两位金额精度或业务规则。

日期必须是 `YYYY-MM-DD`，基金样例固定为 `2026-01-14`，不补造时间。日期时间须带时区，保存为 UTC、六位小数、`Z` 结尾的 ISO 8601 文本；股票/指数样例固定为 `2026-01-15T07:00:00.000000Z`。刷新不会把样例数据时间改成当前时间。真实适配器的三类数据均保留中国市场源日期，见下节。

### AKShare 真实适配

保留 `AkShareProvider` 与 `REAL_ASSETS`，来源 ID `akshare-real-daily-v1`、模拟标记 False：

| 完整 ID | 指标 | 单位 / 币种 | 上游及语义 |
| --- | --- | --- | --- |
| cn:stock:SHA:600519 | price | currency_per_share / CNY | 东方财富，贵州茅台不复权日线收盘价 |
| cn:index:SHS:000001 | index_level | points / NULL | 新浪，上证指数日线收盘点位 |
| cn:fund:OEF:001186 | nav | currency_per_unit / CNY | 天天基金/东方财富，正式单位净值 |

不按短代码猜测资产。`provider_for_id()` 为唯一选择映射；环境变量 MD_PROVIDER_ID 由 Config 在应用创建时读取，默认 mock，未知 ID 报错；`from_app()` 使用应用配置，直接 `MDService()` 使用当前环境。显式 provider 注入优先，clock 注入同时用于真实适配器的市场日期计算。目录是实例方法 `service.list_assets()`，增加 provider_id/is_simulated 元数据，API、CLI 与缓存使用同一实例配置。

股票请求以中国 UTC+8 日期计算最近 45 个自然日窗口；15:00 前截止前一天，15:00 起允许当天。指数同样排除当日未完成 K 线。基金使用已公布源日期，允许延迟。校验每行日期与数值，按最大合格日期选择，不依赖排序；未来日期、非法日期、同日冲突值、缺列、空表均失败，不跳过坏数据伪装成功。接口返回历史表仅在内存中挑最新项，不落历史库；超出股票窗口无数据则失败，不隐式扩大请求。

适配器边界允许上游 str/实数/Decimal，以 `Decimal(str(value))` 转换再调用原有 `decimal_text(..., places=8)`；八位内补齐尾零只是输出格式，不掩盖缺失或进行舍入。上游 float 无法恢复原始精度，超精度、布尔值、NaN/Infinity、未知值均拒绝，零合法。SP 数值规则不变。适配校验错误返回 invalid_response；导入/网络等异常返回 provider_error，均由 MDService 保留缓存。

锁定 AKShare 1.20.18，三函数签名不支持 timeout 参数，使用其原生 HTTP 默认连接/读取超时 (10, 60) 秒，不全局 monkeypatch、无额外重试或自动 mock 回退。延迟导入仅发生在 fetch；列目录、只读缓存、Web 创建和首页/health 无外部请求。

## MD 缓存服务与并发规则

入口为 `app.md.MDService(database_path=Config.DATABASE_PATH, provider=None, clock=utc_now, ttl_seconds=300)`，或 `MDService.from_app(app, **kwargs)`。未指定配置时提供器是 `MockProvider`，ID 为 `mock-fixed-v1`、模拟标记为 `True`。构造、列目录和 Flask 应用创建均不连接数据库或获取行情；读缓存和查询要求数据库已初始化至当前结构版本。

| 方法 | 行为 |
| --- | --- |
| `list_assets()` | 返回有限目录及各资产支持的指标、单位和币种，不访问数据库/提供器 |
| `read_cache(asset_id, metric)` | 只读缓存，永不获取 |
| `query(asset_id, metric, force_refresh=False)` | 有效缓存优先，缺失或过期才尝试获取 |
| `refresh(asset_id, metric)` | 显式强刷，等价于 `force_refresh=True` |

每个结果包含 `data`（记录或 `None`）、`cache_status`、`refresh_status`、`error`（`None` 或 code/message）。不存在的资产/指标抛出 `UnsupportedAsset`，是 `ValidationError` 的子类；未初始化/待升级使用现有初始化异常。数据库和文件系统错误正常抛出，不伪装成提供器成功。

`md_cache` 以 `(asset_id, metric, provider_id, is_simulated)` 为复合主键，模拟与未来真实数据不会因相同提供器名混用。记录除规范化字段外还保存：

- `fetched_at`：服务获取并校验成功后的 UTC 时间，不由提供器声明。
- `expires_at`：本次 `fetched_at + TTL`，SQL 约束要求晚于获取时间。
- `revision`：缓存写入版本，从 1 开始，每次成功写入加 1，用于并发比较。

`Config.MD_CACHE_TTL_SECONDS` 默认 300 秒，允许 1–86400 秒整数；配置变化仅影响下次成功刷新。只有 `fetched_at <= now < expires_at` 时 `cache_status=fresh`，到期瞬间为 `stale`，时钟回退到获取时间以前也标为 `stale`；无记录为 `missing`。新鲜只说明缓存最近获取，使用者仍须展示数据时间与模拟标记。

`refresh_status` 分别表示 `not_requested`（只读）、`not_needed`（有效命中）、`updated`（成功写入）、`failed`（提供器/校验/时钟错误）、`superseded`（并发变化或更旧数据被拒绝）。获取失败或无效响应不会修改旧值、版本、获取时间或过期时间；服务重读已有缓存并携带错误，旧缓存可能仍然 fresh，也可能 stale。无旧值则 `data=None`、`missing`、`failed`，不编造数值。错误信息在本次返回，不通过失败写入延长缓存寿命。

查询先用短读事务取得缓存版本并关闭连接，再调用提供器和校验响应，最后用短写事务原子保存资产定义和一条完整缓存。写事务会重新比较 revision：若其他刷新已经提交，保守地保留先完成的结果，返回 `cache_changed`，不会让重叠的迟到请求覆盖；调用方可稍后显式重试。即使没有并发，来源数据时间倒退、日期/日期时间语义变化或获取时间倒退也拒绝覆盖，返回 `older_data`。同一来源时间的再次成功获取允许更新 fetched_at/TTL。获取期间时钟回退则返回 `clock_error`。不同时间语义之间不猜测先后次序，未来提供器改变语义时应明确迁移或采用新提供器版本标识。

```powershell
python -m flask --app app:create_app init-db
python -m flask --app app:create_app md-refresh
python -m flask --app app:create_app md-show
```

`md-refresh` 逐项强制获取当前配置目录，默认 mock；`md-refresh-real` 显式获取真实目录但不修改环境配置。每项独立原子提交，部分失败时保留已成功的项，并以非零退出码和逐项 error 说明结果。`md-show` 仅查看当前配置对应缓存和完整定义，始终只读。三者都不修改 SP/CA，也不改变原有 `seed-demo` 的幂等和保护行为；仅有 MD 数据不会阻止空 SP/CA 的显式演示填充。没有后台线程、自动刷新、交易日历或行情历史库。切换/查看命令见 [README](../README.md#akshare-真实日线切换刷新与查看)。

## T04 界面与 HTTP 适配

`app/api.py` 复用 Store/MDService/ChatService/ArchiveService，不复制 SQL 或业务状态规则；接口见 [API.md](API.md)。原始消息 POST 仅接受 content，服务端固定 user；摘要/Overview 保留只读接口，生成走 T07 显式归档，不接受任意模型内容写库。档案表单映射为已有有限操作，携带编辑基于的版本，子项删除先确认、统一保存；409 时保留草稿且不自动换版本重试。候选确认继续使用原子版本校验与幂等规则。

市场页 GET 只调用 read_cache，POST 才调用 refresh；完整保留缓存/刷新状态和数据时间，提供器异常的详细信息在 HTTP 层换成安全中文提示。页面和健康检查返回不依赖数据库，浏览器通过 API 接收初始化或升级指引。001–003 保持原样，当前结构版本为 4。

## T05 聊天请求与服务

`ChatService.from_app(app)` 或 `ChatService(database_path, model=替身, clock=时钟)`；方法为 send(conversation_id, content, request_id)、retry(conversation_id, request_id)、history(conversation_id)。Store 新增 recent_messages，只从当前会话按序号逆序取有界 user/assistant 消息后正序返回，不扫描全部历史作为模型输入。普通 list_messages 接口不变。

chat_requests.id 为调用方稳定标识，全局唯一；conversation_id/user_message_id 和 assistant_message_id 分别通过复合外键确保消息属于同一会话，用户消息与助手消息各有唯一约束。服务固定对应角色，不允许客户端指定角色。status 为 running/failed/succeeded；成功必须关联助手消息，非成功无助手关联。model 与 usage_json 只记录响应实际提供的元数据，无密钥或完整提示词快照。

部分唯一索引 chat_one_unfinished 保证同会话最多一个非成功请求。创建请求与用户消息同事务；明确重试复用用户消息、更新 attempt_id 和 lease_until。外部模型调用在事务外，成功后用短事务校验尝试令牌并原子插入助手消息及成功状态。失败只写固定错误 code/message，不把错误写为助手消息；回复保存失败整体回滚并记录 save_failed。相同成功请求幂等读取，不再次调用或累加消息。Store.add_message 在存在未完成聊天时拒绝其他写入，只有持本次内部令牌的助手保存可继续，旧原文接口无模型调用。

占用期限为 max(180, 3×请求超时) 秒。GET 对过期 running 仅计算 interrupted/can_retry，不改库；明确重试换新令牌，迟到旧尝试不能覆盖。重启无自动恢复或后台任务，外部可能已经处理并计费，明确重试可能再次收费。跨进程保护来自 SQLite，不依赖仅当前 Python 进程的锁；不同会话可并行。聊天不修改档案、摘要、Overview 或候选，历史仍由 CA 原文解释。

## T07 归档工作流

入口为 `app.archive.ArchiveService`，status/generate 与 HTTP 契约见 [API.md](API.md)。服务依赖现有 Store，模型复用 llm.complete，支持 model、clock、max_messages 和 max_chars 注入。默认每批20条消息、正文24000字符，单轮不拆分；输出2048 tokens、超时60秒，不自动重试。配置集中在 Config.ARCHIVE_MAX_MESSAGES/ARCHIVE_MAX_CHARS。

archive_requests 的唯一键为 `(conversation_id,first_sequence,last_sequence)`；范围端点通过复合外键关联同会话消息。第一次发起时 profile_json 保存 version、currency、risk_preference 和六个金额字段，作为可重试的候选校验基准，不保存密钥、系统提示或模型输入转储。成功时关联 summary_id、overview_id，保存 citations_json（七类、与条目逐项对应的消息ID列表）、candidate_count、model/usage。引用只允许本批实际读取的消息，角色/ID/序号由后端提供。

status 为 running/failed/succeeded；成功必须有关联摘要和Overview。部分唯一索引保证一个会话只有一个未完成归档。第一次发起在短事务中固定范围和SP快照；重试只针对原ID，更新 attempt_id 及180秒 lease_until。GET 对过期 running 显示 interrupted，不写库。提交时核验尝试令牌；迟到旧结果仅返回已保存状态，不生成新摘要或候选。新消息可在等待期间写入，原批次范围保持不变。不同会话提交时在各自短事务中读取当时最新Overview，避免丢失其他会话的并发结果。

范围在本会话最后一次成功归档的 last_sequence 之后定位下一完整段，读取有界消息，按相邻 user/assistant 成对组装；T05 请求必须为 succeeded 且助手ID相符。无请求记录的旧相邻user/assistant对也可归档；可在前置未配对或其他角色之后定位完整段，明确排除这些消息；选定段遇到间隙即停止，不把间隙或未读范围标为成功。超长单轮报明确提示而不截断。客户端使用GET所得范围，重复相同范围复用结果；失败不会自动尝试下一段。

整体模型响应只接受严格JSON，重复键、额外顶层字段、过长输出、空摘要或无效摘要引用使归档失败。summary七类条目各最多8项、每项500字符和1–6个本批消息引用，转为原有文本列表后通过 summary_content 再校验；完整引用保存在归档记录中。单条候选另行校验，无效时跳过，不使合法摘要或其他候选失败。保留旧摘要版本、原消息及原Overview，不改写历史。模型摘要本身仍可能有语义误差，引用和结构校验不等于金融真实性验证；当前值以SP为准。

Overview由后端确定性合并，无需另一次模型调用。旧来源仅当同会话、更高摘要版本的成功归档范围并集完整覆盖它时才被替代；本次尚在提交的范围一起参与覆盖计算，其余范围读取archive_requests的成功记录。不能把#1–#4仅因新#1–#2相交而删除；等#3–#4也成功后才替代。覆盖中存在缺口时继续保留旧来源。原始消息、历史摘要及旧Overview不删除。

完成替代判断后最多保留100个来源：本次摘要固定第一，其余按 `(created_at,conversation_id,version,id)` 倒序选择前99个；即使时钟回退或时间相同，本次摘要仍进入来源集合。退出集合的旧摘要仍在summaries与旧overview_sources中。各类按会话轮流取条目，本次会话及本次摘要优先，其他摘要按版本倒序；完全相同文本去重，每类最多30项，不承诺识别所有同义重复。来源达到100不再产生memory_limit失败，旧失败批次可原ID明确重试。overview_sources关联实际保留的具体版本，有限记忆不等于完整历史。

模型根据本批对话与当前SP理解意图并提取六个基础金额字段和风险偏好，不要求固定句式；提示词要求省略假设、举例、转述、助手建议和不能确定意图/金额/币种的情况。由模型把完整金额表达规范化为币种基本单位十进制字符串，currency独立返回：CNY下“900，单位是万元”应输出"9000000"，零输出"0"，明确未知输出null。

后端不再包含原文关键词表或正则提取器。`ArchiveService._candidate` 只检查候选契约：evidence等于已保存用户全文、来源在本批且为user，复用operations/Decimal校验操作、字段、金额非负有限/精度/范围及币种格式；JSON金额须为字符串或null，不接受数值/符号/千分位/倍率。基础币种已确定时必须一致；同一候选不允许混合币种或重复设置字段，仅支持set_finances/set_risk。每条不符合契约的候选独立跳过，合法结果一起入库；来源重复取首个合法候选，无变化字段过滤。目标、持仓和删除仍不自动提取。

来源检查证明引用确实存在于该用户消息，不证明模型对原文意图、倍率或币种的理解正确。结构合法但语义错误的候选也可能进入pending；卡片和确认弹窗提供拟写值、币种及原文，用户必须核对，可拒绝，明确确认后才改变SP。模型替身不验证真实意图识别或金额转换能力。没有增加模型调用或数据库迁移。

archive_evidence.source_message_id 为主键，绑定change_id及archive_id，拒绝/确认后仍保留，不因重新归档同一证据再生候选。已有相同原文和操作的人工候选会复用，candidate_count只算新建。创建候选基于首次SP快照版本；确认仍由 Store.confirm_change 校验当前版本、原子应用，不通过归档绕过冲突。归档不改变SP或现有候选状态。

同批次合并确认复用archive_evidence、archive_requests与profile_changes，无新表或迁移。`Store.confirm_change_batch` 在单个BEGIN IMMEDIATE事务中验证成功批次、会话归属、精确的全部剩余pending集合及首次SP版本；拒绝项不会被过滤后悄悄应用，客户端必须重新提交明确核对的剩余项。仅合并基础金额与风险偏好；同字段同值去重，不同值/币种冲突要求先拒绝。调用原 `_apply` 一次，再将全部候选写为confirmed，processed_at与applied_version一致；失败则SP和所有状态整体回滚。已确认集合的相同applied_version支持幂等返回，无需新增请求记录；不自动重设基准，单条流程或其他编辑造成的真实版本冲突继续保留。

外部调用在数据库事务外；校验成功后摘要、Overview、候选、来源去重和成功状态一次提交。任何保存失败全部回滚，再用独立短事务记录固定中文错误；原聊天和先前归档不变。状态保存也失败时抛 ArchiveSaveError，由HTTP返回503，重新读取后再判断，不伪装成功。没有后台归档、无限重试、行情/历史查询工具或目标计算。
