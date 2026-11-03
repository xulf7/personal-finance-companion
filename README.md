# Personal Finance Companion

MIS2001 小组项目：结合个人财务档案、历史决策与市场数据的个人金融规划助手。采用 Flask、HTML/CSS/JavaScript 与本地 SQLite 的模块化单体结构，支持非流式模型聊天和独立连通检查。

## 设计与当前能力

- **SP（Structured Profile）**：保存当前已确认的结构化财务档案，表示当前有效状态。
- **CA（Conversation Archive）**：保存原始聊天、会话摘要与历史重要决策概览 CA Overview，解释历史变化。
- **MD（Market Data）**：保存规范化、有明确来源和时间的市场数据。

后续每轮默认提供精简 SP、CA Overview 和近期消息；详细历史与行情由后端工具按需读取。当前方案不使用 RAG、Embedding 或向量数据库。

**T01–T04 已实现**：四个中文业务页签与 `/api` JSON 接口，可查看/编辑财务档案、目标和持仓，创建会话并保存用户消息，读取原文/摘要/CA Overview，确认或拒绝已有财务候选，查看和手动刷新模拟行情。沿用已有 Python 服务、SQLite 显式初始化/备份/版本迁移和 CLI。

**T06 已接入**：保留 AKShare 方案，支持贵州茅台不复权日线收盘价、上证指数日线收盘点位、基金 001186 正式单位净值。默认仍为固定虚构行情；真实模式须显式选择，刷新需要外网，无需密钥。两种模式均非实时行情。

**T05 已实现**：网页会话发送、模型回答及 SQLite 持久化，失败重试与重复请求保护；通过 OpenAI 官方 Python SDK 连接百炼兼容接口，复用外部配置与独立连通检查。

**尚未实现**：自然语言候选提取、自动摘要、Overview 自动更新、聊天中的市场数据工具、流式回复、后台调度、登录/多用户、交易和完整 Dashboard。聊天不会自动修改 SP、摘要、Overview 或候选。`create_app()`、首页 HTML 和 `/health` 不读取密钥、不调用模型或业务数据库；GET 仅查询，不触发模型。

## 安装与运行

本轮在 Windows、Python **3.11.5**、SQLite **3.42.0**、Flask **3.1.1**、AKShare **1.20.18** 上验证。建议先使用 Python 3.11；其他 Python 版本尚未验证。`sqlite3` 随 Python 提供。

在 VS Code 打开项目文件夹，选择自己已有的 Python 解释器，在项目根目录新建终端。环境名称不限，不强制创建 Conda 或虚拟环境；用下面命令确认终端实际使用的解释器：

```powershell
python -c "import sys, sqlite3; print(sys.executable); print(sys.version); print(sqlite3.sqlite_version)"
python -c "from importlib.metadata import version; print(version('Flask'))"
python -m pip show akshare
```

若缺少依赖或需要对齐版本，再执行安装；已有兼容版本时不要批量升级环境：

```powershell
python -m pip install -r requirements.txt -c constraints.txt
```

直接运行依赖为 `Flask==3.1.1`、`akshare==1.20.18`、`openai==2.8.1`。沿用兼容的官方 SDK 2.8.1，复用已有 httpx/anyio；未为了追新升级现有依赖。AKShare 与模型 SDK 均延迟导入，纯页面/mock 行情不需模型密钥；完整离线测试需 SDK 及其依赖，但模型网络由替身处理，测试不产生费用。仅修改文档无需安装运行依赖。

`requirements.txt` 列三个直接依赖；[constraints.txt](constraints.txt) 记录当前 **Windows / Python 3.11.5** 下全部 **43 个运行时间接依赖**的已安装版本，包括 Flask、AKShare、OpenAI SDK 的逐层依赖及共用包。清单依据包元数据和当前平台条件核对，不使用整个环境的 pip freeze，不记录无关包、可选 extras 或构建工具。约束文件只限制实际需要安装的包版本，详见 [pip 官方说明](https://pip.pypa.io/en/stable/user_guide/#constraints-files)。

这是一份已验证平台的版本快照，不是跨平台或带文件哈希的完整锁文件。colorama、mini-racer 保留 Windows 条件；Linux 专有依赖尚未验证和锁定，其他 Python/操作系统也尚未验证。新增直接依赖或调整版本后，应重新核对依赖链和约束，并运行项目测试。安装前可用 `python -m pip install --dry-run -r requirements.txt -c constraints.txt` 查看是否会调整已有包，存在冲突时先检查影响或使用独立环境，不批量升级。

启动本地开发服务：

```powershell
python run.py
```

打开 <http://127.0.0.1:5000/> 查看首页。在另一个终端检查健康接口：

```powershell
python -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:5000/health').read().decode())"
```

返回 JSON `{"status":"ok"}`（空白格式可能不同）。它表示 Web 进程可响应，不检查业务数据库或外部服务。用 `Ctrl+C` 停止。端口被占用时可运行 `python run.py --port 5001`，并将浏览器和检查命令中的端口同步改成 5001。服务仅监听本机回环地址，默认关闭调试，不用于远程部署。

从其他工作目录运行时，传入 `run.py` 的实际路径即可。例如从项目根目录用 PowerShell 取得路径后，切换到上一级再启动：

```powershell
$projectPath = (Get-Location).Path
Set-Location ..
python (Join-Path $projectPath 'run.py')
```

停止后可用 `Set-Location $projectPath` 返回。路径来自代码文件位置，与终端工作目录无关。

## 网页使用

首次克隆或取得包含迁移的代码后，先执行 `python -m flask --app app:create_app init-db`；已有版本 2 的库会先备份再应用 003 聊天迁移，不需重新填充 SP/CA。按后文配置本机密钥路径，然后 `python run.py` 打开 <http://127.0.0.1:5000/>。仅希望使用虚构演示时才显式执行 `seed-demo` 和 `md-refresh`。

- **财务档案**：编辑金额、风险偏好、目标和持仓后统一点击“保存财务档案”。删除子项会先确认，再从草稿移除，保存后才写库。空白表示未知，明确的零会保留。已确定的基础币种不可直接修改。版本冲突时保留输入，需手动重新读取并核对，不会自动重试；重新读取会在确认后丢弃未保存草稿。
- **对话记录**：新建/选择会话，发送问题并等待助手回答；角色和模型用量分开显示，刷新或重启后仍可查看。模型失败保留用户原文，原问题旁显示“重试”按钮，不写假助手回复。已保存问题确认后清空对应草稿；HTTP 状态不确定时先重新读取，保留未保存输入。同会话未完成问题需先处理，不能插入新问题；历史摘要中的“当时确认”不是当前 SP。
- **待确认变更**：查看修改内容、来源会话/原始用户消息、状态和基于的档案版本。确认前再次展示具体修改，成功后读取最新档案与候选；拒绝不改 SP。查看和切换页签不会自动处理候选。本轮不提供候选编辑器。
- **市场数据**：按当前提供器列目录、只读已有缓存，手动刷新才调用该提供器。显示精确值、单位、真实/模拟标记、AKShare 与对应上游、数据日期、获取/过期时间、缓存和刷新状态。无缓存显示“暂无数据”，过期旧值和刷新错误都会保留；日期不补造时刻，指数点位无币种。模式切换见下文。

所有写入均调用后端服务；详细契约见 [HTTP 接口说明](docs/API.md)。当前结构版本为 3，仅新增 `003_chat_requests.sql`，001/002 保持原样。

### 隔离的人工网页检查

本轮自动浏览器通道不可用，未完成浏览器视觉、窄屏、键盘与控制台验收。已执行隔离库 HTTP 串联、全部 unittest 和前端 JavaScript 语法检查；这些不能替代浏览器验收。人工操作请先建立项目内独立副本，避免改变当前开发数据（以下在项目根目录的 PowerShell 逐行执行）：

```powershell
$webCheckPath = Join-Path (Get-Location).Path 'runtime/tests/t04-manual'
if (Test-Path $webCheckPath) { throw '检查目录已存在，请换一个新目录名，勿覆盖已有数据' }
New-Item -ItemType Directory -Path $webCheckPath -Force | Out-Null
Copy-Item -Path app,run.py,requirements.txt -Destination $webCheckPath -Recurse
# 需要真实聊天时在副本中另建仅含外部密钥路径的 config.local.json，勿复制密钥文件。
Set-Location $webCheckPath
python -m flask --app app:create_app init-db
python -m flask --app app:create_app seed-demo
$env:MD_PROVIDER_ID = 'mock-fixed-v1'
python run.py --port 5001
```

打开 <http://127.0.0.1:5001/>，可依次检查：

1. 编辑金额、增加/编辑/删除目标和持仓，保存后重新载入；用两个标签页基于同一版本编辑，先保存其中一个，再保存另一个，确认出现冲突且保留输入。
2. 配置副本的本机密钥路径，新建两个会话并发送虚构问题，追问后刷新/重启，确认用户与助手消息均保留、归属正确。发送会产生费用。用无效配置模拟失败，应仅保留用户消息并显示明确重试入口；修复后重试不新增同一用户消息。连续点击、同请求 ID 重发不能重复成功回复。展开摘要/Overview，确认聊天未自动更新它们。
3. 在不同的新副本中分别确认和拒绝待确认候选，检查档案版本及预算；仅查看不能修改。处理后按钮应消失。
4. 初始 MD 应显示空缓存，手动刷新后显示模拟值；再次读取缓存不会获取。再停止副本服务，在同一终端设置 `$env:MD_PROVIDER_ID = 'akshare-real-daily-v1'` 后重新启动并重新加载页面，应出现三个真实资产、真实日线标记和独立缓存。低频逐项刷新，确认日期和来源；失败须显示错误并保留旧值。可在开发者工具 Network 中确认列目录/读缓存无 POST。停止后设回 mock 并重启，模拟缓存应保留。浏览器离线只验证 HTTP 失败；真实上游失败以返回的 refresh_status/error 为准。
5. 将视口缩窄，检查自然堆叠与无横向溢出；用 Tab、方向键和 Esc 操作页签/确认框，检查可见焦点，并查看开发者工具控制台是否有异常。

用 `Ctrl+C` 停止副本服务。测试副本的数据位于该副本的 `runtime/`，不会写入原开发库；不要复制真实密钥或个人资料进行检查。

## 功能检查

在项目根目录执行：

```powershell
$env:MD_PROVIDER_ID = 'mock-fixed-v1'
python -B -m unittest discover -s tests -v
```

如已安装 Node，可选执行 `node --check app/static/app.js` 检查前端语法；无需 npm 安装或构建，应用运行不需要 Node。

测试覆盖原有离线启动、SP/CA 和 MD 规则，以及 HTTP 档案/目标/持仓编辑、冲突回滚、固定用户角色、消息重连、候选确认/拒绝/重复处理、摘要只读、MD 状态透传、初始化提示、安全错误响应和迁移副本 API 读写。浏览器验收情况与人工步骤见上节。

所有测试使用 `runtime/tests/` 下独立临时目录和数据库，结束后仅清理该次临时文件，不访问实际开发数据库。测试需要允许本机回环连接，不需要外网或模型/行情服务。

## 初始化与虚构演示

在项目根目录显式执行（无需启动 Web 服务）：

```powershell
python -m flask --app app:create_app init-db
python -m flask --app app:create_app seed-demo
```

`init-db` 首次创建 `runtime/data/app.sqlite3` 和版本 0 的空 SP（金额未知）；当前数据库**结构版本为 3**。重复执行不清库或覆盖数据。已有库升级前在 `runtime/backups/` 自动备份，再顺序执行待应用迁移：002 建立 MD，003 新增聊天请求表，保留原 SP/CA/MD 数据；无需迁移时不备份。未知结构、较新版本或被改写的迁移会报错并保留数据，不要删库重建。详情见 [数据模型与迁移说明](docs/DATA_MODEL.md)。

`seed-demo` 只填充明确标注的**虚构数据**：投资预算 CNY 500.00 → 用户提出 800.00 → 创建候选 → 明确确认后 SP 为 800.00；保留原始消息、旧预算摘要、修改原因、Overview，另有 1000.00 的待确认候选。摘要由代码明确写入，不是模型生成。

重复执行演示命令不新增或覆盖数据；若数据库已有其他业务数据则拒绝填充。演示记录是本地开发数据，不会进入 Git，也不会在应用启动时自动产生。查看本地档案及候选状态：

```powershell
python -c "from pprint import pprint; from app.storage import Store; s=Store(); pprint(s.get_profile()); pprint(s.list_changes())"
```

业务入口为 `app.storage.Store`；在 Flask 相关代码中可用 `Store.from_app(app)` 读取应用配置。可选 `create_app(test_config)` 支持测试路径隔离；调用方应同时配置所需的 `DATABASE_PATH`、`RUNTIME_DIR`、`BACKUP_DIR` 等路径至项目内独立测试目录。服务规则见 [DATA_MODEL.md](docs/DATA_MODEL.md)，薄 HTTP 适配层见 [API.md](docs/API.md)。

## 模拟行情与已有库升级

首次使用可按上节初始化，按需生成 SP/CA 虚构演示，然后显式填充 MD。**已有 T02 开发库只需升级并刷新 MD，无需重新填充 SP/CA**：

```powershell
python -m flask --app app:create_app init-db
python -m flask --app app:create_app md-refresh
python -m flask --app app:create_app md-show
```

升级前停止其他数据库写入，保留命令输出中的备份路径；若升级失败，保留现场检查错误，不删库重建。`md-refresh` 强制获取当前配置目录，默认获取三项固定虚构样例，逐项原子写入；`md-show` 只读当前模式已有缓存，不触发获取。两条命令均输出完整资产 ID、指标、数值、币种/单位、来源、模拟标记、数据时间、获取/过期时间及状态。新库未刷新时显示 `missing`，不会编造数值。

本节命令默认 mock；若终端此前选择过真实模式，先设置 `$env:MD_PROVIDER_ID = 'mock-fixed-v1'`。当前 T05 需要将版本 2 升级至 3，无需重建或重新填充 SP/CA。

| 虚构样例 | 完整资产 ID | 指标 / 单位 | 固定数据时间 |
| --- | --- | --- | --- |
| 虚构演示股票 | `demo:stock:DEMO_EXCHANGE:DEMO001` | `price` / CNY 每股 | 2026-01-15 07:00 UTC |
| 虚构演示基金 | `demo:fund:DEMO_FUNDS:DEMO001` | `nav` / CNY 每份 | 2026-01-14，仅日期 |
| 虚构演示指数 | `demo:index:DEMO_EXCHANGE:DEMO001` | `index_level` / 点，无币种 | 2026-01-15 07:00 UTC |

仅支持上述完整标识与指标；相同短代码 `DEMO001` 不足以确定资产。提供器为 `mock-fixed-v1`，全部 `is_simulated=true`。刷新只改变获取/过期时间，不把固定样例时间改成当前时间。MD 数值保存八位小数，不影响 SP 的两位金额精度。

Python 入口为 `app.md.MDService`，可用 `MDService.from_app(app)`。`list_assets()` 列出目录；`read_cache(asset_id, metric)` 只读；`query(...)` 优先命中有效缓存，否则获取；`refresh(...)` 或 `query(..., force_refresh=True)` 强刷。可注入提供器和带时区的时钟。

默认有效期 `Config.MD_CACHE_TTL_SECONDS=300` 秒，可配置为 1–86400 秒整数，影响下次成功刷新写入的过期时间。`fetched_at <= 当前时间 < expires_at` 才算 `fresh`，到期时即为 `stale`。**缓存新鲜仅表示最近获取，不能证明数据时间新或行情实时。** 结果同时返回 `cache_status`、`refresh_status`、`error` 与完整数据时间/模拟标记。获取或校验失败保留旧记录，旧值可能为 `fresh` 或 `stale`，必须同时查看本次错误；没有缓存则 `data=None`。并发重叠刷新或更旧来源时间不会覆盖已保存的更新结果，详见数据模型说明。

## AKShare 真实日线：切换、刷新与查看

使用 VS Code 中同一个 PowerShell 终端设置环境变量，无需改源码。设置只作用于当前终端及新启动的子进程；另一个终端需重新设置。切换前用 Ctrl+C 停止旧 Web 进程，切换后重启并重新加载网页。

```powershell
$env:MD_PROVIDER_ID = 'akshare-real-daily-v1'
python -m flask --app app:create_app init-db
python -m flask --app app:create_app md-refresh
python -m flask --app app:create_app md-show
python run.py
```

打开市场数据页：目录、缓存 GET 和刷新 POST 均使用当前提供器。列目录、读取缓存、首页和健康检查不请求外部行情。`md-refresh` 刷新**当前模式**；`md-refresh-real` 无论当前模式如何都显式刷新真实目录，但**不切换** Web 模式或以后命令的配置。`md-show` 永远只读当前模式缓存，因此查看真实刷新结果也必须先设置上述环境变量。

| 完整资产 ID | 数据及单位 | AKShare 函数 / 上游 |
| --- | --- | --- |
| `cn:stock:SHA:600519` | 贵州茅台不复权日线收盘价，price，CNY/股 | stock_zh_a_hist / 东方财富 |
| `cn:index:SHS:000001` | 上证指数日线收盘点位，index_level，点，无币种 | stock_zh_index_daily / 新浪财经 |
| `cn:fund:OEF:001186` | 基金 001186 正式单位净值，nav，CNY/份 | fund_open_fund_info_em / 天天基金、东方财富 |

真实来源 ID 为 `akshare-real-daily-v1`，`is_simulated=false`；三类均保留中国市场的源日期。股票只请求截至已完成日期的近 45 个自然日窗口，兼顾跨年、周末和长假；超过窗口没有记录则明确失败，不扩大为多年历史。股票/指数在中国时间 15:00 前排除当日未完成 K 线。指数、基金的现有接口返回历史表（无日期范围参数），适配器只保留最新合格日期；不建立历史库。基金允许公布延迟，周末返回最近交易日正常，数据日期不要求等于今天。

数值经 `Decimal(str(value))` 和原有八位小数校验，不静默舍入；上游浮点值已经丢失的精度无法恢复。空表、缺列、非法日期、缺失/非有限或超精度值均刷新失败。失败不覆盖旧缓存、不延长 TTL、不自动回退模拟值。HTTP 200 也可能含 `refresh_status=failed`；CLI 部分失败会非零退出，其他已成功项保留。遇到限流请稍后再试，不连续点击刷新。

本项目核对了锁定版本的实际函数签名，未传不存在的 timeout 参数。AKShare 1.20.18 内部 HTTP 使用默认连接 10 秒、读取 60 秒超时；不修改全局网络库，也不额外重试。读取超时不是整个操作的总时长保证。接口语义可参照 [AKShare 官方源码及说明](https://github.com/akfamily/akshare)，实际以锁定版本为准。

切回 mock（也可 `Remove-Item Env:MD_PROVIDER_ID` 恢复默认）：

```powershell
$env:MD_PROVIDER_ID = 'mock-fixed-v1'
python -m flask --app app:create_app md-show
python run.py
```

只接受上述两个完整提供器 ID；拼写错误会明确报错，不会悄悄回退。配置入口集中在 `app/config.py`，不自动加载 `.env`。模拟与真实缓存独立，切换模式不删除任何数据。

## 百炼模型聊天与连通检查（T05）

网页聊天和连通检查共用 `app/llm.py` 的 OpenAI 官方 SDK 调用层。原 urllib 手写请求层已替换，未保留两套实现。通过 [官方 SDK 的超时与重试设置](https://developers.openai.com/api/reference/python) 显式配置 `max_retries=0`，HTTP 客户端禁止重定向并保留正常证书校验；`enable_thinking=false` 经 extra_body 进入百炼 HTTP 请求体顶层。仅向配置的兼容接口调用，不自动更换模型。

先用 VS Code 在项目外创建自己的私密 JSON 文件（可含中文/空格路径，支持 UTF-8 或 UTF-8 BOM）。以下为占位示例，地址和密钥必须替换；base_url 从百炼导出表的 openAiCompatible 字段复制完整 HTTPS 地址，不能带账号密码、查询参数或片段：

```json
{
  "base_url": "请粘贴 openAiCompatible 的完整 HTTPS 地址",
  "model": "qwen-plus",
  "api_key": "请填写自己的真实密钥"
}
```

推荐在项目根目录创建本机文件 `config.local.json`，只记录自己的外部 JSON 路径。例如（每位组员改成自己的位置，可使用正斜杠）：

```json
{
  "llm_config_file": "D:/PFC_secrets/bailian.json"
}
```

该本机文件被现有 `/config.local.*` 规则忽略，不进入 Git；已有其他字段可保留。不把外部密钥、模型地址等内容复制进来，也不新增独立 example 文件。在 VS Code 的 PowerShell 终端进入项目根目录即可运行：

```powershell
python -B -m app.llm_check
```

需要临时覆盖本机路径时，仍可设置当前终端环境变量：

```powershell
$env:PFC_LLM_CONFIG_FILE = 'C:\你的私密目录\bailian.json'
python -B -m app.llm_check
```

读取优先级为 PFC_LLM_CONFIG_FILE → 项目根目录 config.local.json 的 llm_config_file → 中文缺失提示。显式环境变量为空、无效，或本机文件内容/路径错误时明确报错，不静默切换来源。项目根目录由代码位置确定；两个来源中的相对路径均以项目根目录解析，与终端当前目录无关。

**显式执行这条命令会产生一次真实模型请求，可能计费。** 固定用户消息为“请只回复：连接成功。”，stream=false、enable_thinking=false（HTTP 请求体顶层）、max_tokens=64，超时 30 秒。不自动重试、更换模型或跟随重定向；使用正常 HTTPS 证书验证。保留配置的完整域名和路径，仅补齐 /chat/completions；已包含该后缀则不重复拼接。参数语义见 [百炼官方兼容接口说明](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/)。

成功显示回复、响应实际提供的模型名及 token 用量；缺少模型名/用量时明确说明，不使用配置值代替或估算。配置、认证、限流、超时、服务端和格式错误返回非零退出码，不输出密钥、配置对象、上游错误响应体或异常堆栈。超时也不要立即重试，服务端可能已经处理并计费。终端若显示乱码，可用 `python -B -X utf8 -m app.llm_check` 强制本次 Python 进程使用 UTF-8。

config.local.json 是本机路径设置，新克隆的仓库不包含它；换电脑或部署服务器时需创建或调整自己的路径。PFC_LLM_CONFIG_FILE 仅在当前终端会话及其子进程生效；执行 `Remove-Item Env:PFC_LLM_CONFIG_FILE` 取消覆盖后，会重新使用本机文件，不要用 setx 设置持久变量。外部文件属于 Python 后端私有配置，不发送到浏览器，不复制或提交到项目，也不自动搜索密钥位置。

导入模块、`python run.py`、首页、/health 和自动测试不会自动读取模型配置或调用模型。自动测试只用 runtime/tests 内临时假配置和模拟网络，不读取真实配置。离线检查：

```powershell
python -B -m unittest discover -s tests -p test_llm_check.py -v
python -B -m unittest discover -s tests -v
```

### 聊天上下文、失败与并发

网页发送最长 4000 字符的问题。后端先保存用户消息，再读取当前确认 SP、最新 Overview（可无）和当前会话最近最多 12 条 user/assistant 消息，最后追加本次问题一次。SP/Overview 以 user 数据消息中的 JSON 提供，不作为 system 指令；system 由后端固定维护，待确认意向与旧摘要不能代替当前 SP。默认总内容上限 16000 字符，结构化上下文约 6000 字符，目标/持仓各最多 10 项，概览各类最多 3 项并可截短；超限先减少子项或旧消息，不截断基础金额、不丢掉当前问题。不做精确 token 计数。不同会话原文不混入，跨会话背景仅通过 Overview。

正常聊天默认输出最多 1024 tokens、超时 60 秒；连通检查仍为 64 tokens、30 秒，且不读取业务数据。聊天参数集中在 Config 的 CHAT_RECENT_MESSAGES、CHAT_CONTEXT_CHARS、CHAT_MAX_TOKENS、CHAT_TIMEOUT_SECONDS，可由 create_app 测试/应用配置覆盖；边界见代码。所有模型调用只在发送、明确重试或显式检查时发生，可能计费；未提供用量时不估算。

请求 ID 绑定问题，成功后的重复请求直接返回已保存回复。失败不会写假助手消息；点击原问题的重试按钮会复用用户消息，但重新调用可能再次计费。模型已返回而保存失败时会明确说明。HTTP 结果未知请先读取会话，同一页面再次提交相同草稿沿用请求 ID。前端同一窗口一次发送；后端通过数据库约束保证同一会话只有一个未完成问题，不同会话可并行，旧的原文保存接口也不能插队。

若服务进程中断，running 状态不自动代表失败；默认 180 秒占用期后只读界面显示 interrupted，可明确重试。新尝试用不同 attempt_id，迟到旧回复不能覆盖新结果。没有自动网络重试、后台恢复或队列；尚无法确认外部是否已计费时请谨慎重试。调用模型期间不持有 SQLite 写锁，成功回复与请求成功状态原子保存。

2026-11-03 隔离真实验收：两次 qwen-plus 调用。首轮正确使用当前预算 800、收入 6000、负债 0，没有采用 Overview 的历史预算 500；次轮正确回忆前一问题、给出约 13.33% 并说明现金未知。服务端用量分别为 400+33=433、467+78=545 tokens。重复请求未增加调用，新应用实例读取到四条已保存消息；SP/Overview 未变。自动浏览器通道不可用，视觉、窄屏与控制台仍需人工验收，不能用这些 HTTP 结果代替。

## 文件与运行数据

```text
app/
  __init__.py        应用创建
  config.py          集中配置与路径
  routes.py          首页和健康检查
  api.py             JSON HTTP 接口与安全错误响应
  db.py              显式迁移、连接与事务
  migrations/        纳入 Git 的版本化 SQL
  storage.py         SP/CA 可复用服务
  md.py              MD 规范化、缓存和刷新服务
  md_provider.py     有限模拟/真实资产目录与 AKShare 适配器
  llm.py             后端模型配置加载与单次请求
  llm_check.py       显式模型连通检查入口
  chat.py            上下文、消息编排、重复请求与失败重试
  validation.py      有限修改结构与输入校验
  demo.py / cli.py   虚构演示逻辑与显式命令
  templates/         HTML 模板
  static/            原生 JavaScript 业务交互与页面样式
run.py               启动入口
tests/               功能与迁移检查
requirements.txt     直接运行依赖
constraints.txt      已验证平台的完整运行时间接依赖约束
library/             本地背景资料（Git 忽略）
runtime/             本地运行数据（Git 忽略）
```

`app/config.py` 使用 `pathlib` 从代码位置定位根目录，统一约定：

| 路径 | 用途 |
| --- | --- |
| `runtime/data/` | 本地数据库 `app.sqlite3` |
| `runtime/logs/` | 需要时产生的日志 |
| `runtime/imports/` | 未来的实际导入数据 |
| `runtime/backups/` | 未来的数据库备份 |
| `runtime/tests/` | 测试临时文件与临时数据库 |

目录按需创建；Web 启动不创建数据库，只有显式初始化命令创建业务库；测试只写独立测试目录。源码与持久化引用不得绑定用户名、盘符或本机绝对路径。

`library/` 是各成员的本地背景资料，不修改或提交；`runtime/` 是各成员自己的运行数据；公开源码、测试、文档及依赖清单由 Git 管理。新克隆的仓库不含背景资料、运行数据或真实配置。

普通 Web 启动不读取模型配置，也不自动加载 `.env`。发送聊天或显式连通检查时优先使用 PFC_LLM_CONFIG_FILE，未设置时读取项目根目录 config.local.json 中的路径，再读取项目外私密 JSON；真实密钥不进入项目。真实 `.env`、`config.local.*`、数据库及附属文件已忽略。无密钥的 `.env.example`、迁移 SQL、数据脚本和合成测试样例允许纳入 Git。

## 整体迁移与协作

停止服务与其他数据库写入，将整个项目文件夹复制到新位置，在新环境检查 Python 并按需安装 `requirements.txt`，再执行启动和测试命令，无需修改路径。若要保留 Git 历史，复制时包含 `.git`；携带本地数据时包含 `runtime/`。数据库没有指向原目录的绝对路径，可在副本继续使用；若采用新克隆，则按需初始化新库，不会获得原机器的本地数据。私有资料和密钥单独安全传递，不通过公开仓库同步；Python 环境不随文件夹迁移，使用新机器自己的解释器。

小组日常操作见 [小组 GitHub 协作指南](CONTRIBUTING.md)，以 VS Code 图形操作为主。
