# SP/CA 数据模型与服务

当前为单用户、单份有效档案，使用标准库 `sqlite3`，没有 ORM 或业务 HTTP 接口。唯一真实状态来源是 SP；摘要与 Overview 属于历史解释，保存它们不会写入 SP。

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

`facts` 是观察到的重要事实；`plans` 是假设或未来打算；`confirmed` 是摘要作者记录的当时确认状态，仍不具有修改当前 SP 的权限。摘要由明确调用写入，不自动生成，也不检验陈述的金融真实性。

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

当前仅 `app/migrations/001_initial.sql`，结构版本 1 与 SP 档案版本是两回事。初始化重复执行不丢失数据；启动和健康检查不连接业务库。命令没有清库选项。

新增结构时按顺序添加 `002_说明性英文名.sql`（文件名使用小写英文/数字/下划线），保留旧 SQL 原样。每个语句以分号结束，语句之间换行；不要写 `BEGIN/COMMIT/ROLLBACK`、自行修改版本记录或关闭外键，事务由 Python 管理。迁移须同时适用于已有库与从 001 顺序创建的新库。同步修改服务、输入校验和文档，并在独立临时库验证升级与失败回滚。

初始化会核对版本、迁移文件 SHA-256（按 UTF-8 文本统一为 LF 后计算）、实际表/索引/约束结构及外键。发现未知旧表、结构被手改、迁移记录不匹配或版本高于代码时，停止并保留数据，需人工核对或编写兼容迁移，不能删库解决。SQL 迁移随代码复制并纳入 Git，数据库和备份不纳入 Git。

实际升级已初始化的旧版本前，先取得写事务锁，再通过 SQLite backup API 生成 `runtime/backups/` 中唯一命名的有效快照并检查完整性；备份内容是升级前版本。备份成功才执行迁移。所有待执行 SQL 与版本记录在同一事务中提交；失败回滚并保留备份，错误提示包含备份路径。首次空库或无待执行迁移时不备份；首次迁移失败可能留下一个无业务表的空文件，可再次显式初始化，程序不会自动删除它。

虚构演示整批写入也在单个事务中；重复运行按种子标记返回而不写入，遇到非空业务数据或已编辑 SP 拒绝填充。真实开发数据存在时应保留，测试始终使用独立路径。
