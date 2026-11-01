# Personal Finance Companion

MIS2001 小组项目：结合个人财务档案、历史决策与市场数据的个人金融规划助手。采用 Flask、HTML/CSS/JavaScript 与本地 SQLite 的模块化单体结构，模型 API 在后续任务接入。

## 设计与当前能力

- **SP（Structured Profile）**：保存当前已确认的结构化财务档案，表示当前有效状态。
- **CA（Conversation Archive）**：保存原始聊天、会话摘要与历史重要决策概览 CA Overview，解释历史变化。
- **MD（Market Data）**：保存规范化、有明确来源和时间的市场数据。

后续每轮默认提供精简 SP、CA Overview 和近期消息；详细历史与行情由后端工具按需读取。当前方案不使用 RAG、Embedding 或向量数据库。

**T01–T04 已实现**：四个中文业务页签与 `/api` JSON 接口，可查看/编辑财务档案、目标和持仓，创建会话并保存用户消息，读取原文/摘要/CA Overview，确认或拒绝已有财务候选，查看和手动刷新模拟行情。沿用已有 Python 服务、SQLite 显式初始化/备份/版本迁移和 CLI。

**T06 已接入**：保留 AKShare 方案，支持贵州茅台不复权日线收盘价、上证指数日线收盘点位、基金 001186 正式单位净值。默认仍为固定虚构行情；真实模式须显式选择，刷新需要外网，无需密钥。两种模式均非实时行情。

**尚未实现**：T05 模型回答、自然语言候选提取、自动摘要、模型工具调用、后台调度、登录/多用户、交易和完整 Dashboard。发送消息仅保存用户原文，不会产生助手回复、摘要或候选。`create_app()`、首页 HTML 和 `/health` 不访问业务数据库或外部行情；浏览器加载后通过 API 读取业务数据，未初始化时显示指引，不自动建库或填充。

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

直接运行依赖为 `Flask==3.1.1` 与 `akshare==1.20.18`。AKShare 延迟导入：仅使用 mock 和离线测试时只需 Flask；启用真实刷新才需要 AKShare 及其传递依赖。测试使用标准库 unittest，替身不需要 AKShare/pandas。已有 Flask 时只补 `python -m pip install akshare==1.20.18 -c constraints.txt`；无需升级其余环境。仅修改文档无需安装运行依赖。

`requirements.txt` 列两个直接依赖；[constraints.txt](constraints.txt) 记录当前 **Windows / Python 3.11.5** 下全部 **32 个运行时间接依赖**的已安装版本，包括 Flask、AKShare 的逐层依赖及共用包。清单依据包元数据和当前平台条件核对，不使用整个环境的 pip freeze，不记录无关包、可选 extras 或构建工具。仅使用 mock 时，constraints 不会额外要求安装 AKShare 的依赖；它只约束实际需要安装的包版本，详见 [pip 官方说明](https://pip.pypa.io/en/stable/user_guide/#constraints-files)。

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

首次克隆或取得包含迁移的代码后，先按下方“初始化与虚构演示”执行 `init-db`；只有希望使用演示数据时才显式执行 `seed-demo` 和 `md-refresh`。已有 T03 数据库无需新迁移或重新填充，直接 `python run.py` 并打开 <http://127.0.0.1:5000/> 即可。

- **财务档案**：编辑金额、风险偏好、目标和持仓后统一点击“保存财务档案”。删除子项会先确认，再从草稿移除，保存后才写库。空白表示未知，明确的零会保留。已确定的基础币种不可直接修改。版本冲突时保留输入，需手动重新读取并核对，不会自动重试；重新读取会在确认后丢弃未保存草稿。
- **对话记录**：新建/切换会话、保存用户文本、展开历史摘要和 CA Overview。保存成功才清空对应会话的文本，失败保留；切换时保留该会话草稿，晚到的旧请求不会覆盖当前会话。模型未接入，不生成占位回答。历史摘要中的“当时确认”不是当前档案。
- **待确认变更**：查看修改内容、来源会话/原始用户消息、状态和基于的档案版本。确认前再次展示具体修改，成功后读取最新档案与候选；拒绝不改 SP。查看和切换页签不会自动处理候选。本轮不提供候选编辑器。
- **市场数据**：按当前提供器列目录、只读已有缓存，手动刷新才调用该提供器。显示精确值、单位、真实/模拟标记、AKShare 与对应上游、数据日期、获取/过期时间、缓存和刷新状态。无缓存显示“暂无数据”，过期旧值和刷新错误都会保留；日期不补造时刻，指数点位无币种。模式切换见下文。

所有写入均调用现有服务；详细请求、状态码和 MD 结果语义见 [HTTP 接口说明](docs/API.md)。本轮数据库结构仍为版本 2，未增加或改写 SQL 迁移。

### 隔离的人工网页检查

本轮自动浏览器通道不可用，未完成浏览器视觉、窄屏、键盘与控制台验收。已执行隔离库 HTTP 串联、全部 unittest 和前端 JavaScript 语法检查；这些不能替代浏览器验收。人工操作请先建立项目内独立副本，避免改变当前开发数据（以下在项目根目录的 PowerShell 逐行执行）：

```powershell
$webCheckPath = Join-Path (Get-Location).Path 'runtime/tests/t04-manual'
if (Test-Path $webCheckPath) { throw '检查目录已存在，请换一个新目录名，勿覆盖已有数据' }
New-Item -ItemType Directory -Path $webCheckPath -Force | Out-Null
Copy-Item -Path app,run.py,requirements.txt -Destination $webCheckPath -Recurse
Set-Location $webCheckPath
python -m flask --app app:create_app init-db
python -m flask --app app:create_app seed-demo
$env:MD_PROVIDER_ID = 'mock-fixed-v1'
python run.py --port 5001
```

打开 <http://127.0.0.1:5001/>，可依次检查：

1. 编辑金额、增加/编辑/删除目标和持仓，保存后重新载入；用两个标签页基于同一版本编辑，先保存其中一个，再保存另一个，确认出现冲突且保留输入。
2. 新建两个会话、快速切换并保存文本，刷新页面确认消息归属正确；展开摘要/Overview。重复主动发送相同文字应允许，连续点击一次保存不能产生重复请求。
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

`init-db` 首次创建 `runtime/data/app.sqlite3` 和版本 0 的空 SP（金额未知）；当前数据库**结构版本为 2**。重复执行不清库或覆盖数据。升级已有版本 1 时，先在 `runtime/backups/` 生成有效备份，再执行 `002_market_data.sql`，只新增 MD 表，保留已有 SP/CA；无待执行迁移时不备份。高于代码版本、未知结构或已应用的迁移被改写时会报错并保留数据。尚未初始化或需要升级时，业务服务提示先执行 `init-db`。详情见 [数据模型与迁移说明](docs/DATA_MODEL.md)。

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

本节命令默认 mock；若终端此前选择过真实模式，先设置 `$env:MD_PROVIDER_ID = 'mock-fixed-v1'`。已有版本 2 的库在 T06 无需迁移、重建或重新填充 SP/CA。

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

当前无需配置文件，不自动读取 `.env`，因此不提供无用途的 `.env.example`。后续引入密钥时仅放在后端私有配置中；真实 `.env`、`config.local.*`、数据库及附属文件已忽略。无密钥的 `.env.example`、迁移 SQL、数据脚本和合成测试样例允许纳入 Git。

## 整体迁移与协作

停止服务与其他数据库写入，将整个项目文件夹复制到新位置，在新环境检查 Python 并按需安装 `requirements.txt`，再执行启动和测试命令，无需修改路径。若要保留 Git 历史，复制时包含 `.git`；携带本地数据时包含 `runtime/`。数据库没有指向原目录的绝对路径，可在副本继续使用；若采用新克隆，则按需初始化新库，不会获得原机器的本地数据。私有资料和密钥单独安全传递，不通过公开仓库同步；Python 环境不随文件夹迁移，使用新机器自己的解释器。

小组日常操作见 [小组 GitHub 协作指南](CONTRIBUTING.md)，以 VS Code 图形操作为主。
