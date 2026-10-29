# Personal Finance Companion

MIS2001 小组项目：结合个人财务档案、历史决策与市场数据的个人金融规划助手。采用 Flask、HTML/CSS/JavaScript 与本地 SQLite 的模块化单体结构，模型 API 在后续任务接入。

## 设计与当前能力

- **SP（Structured Profile）**：保存当前已确认的结构化财务档案，表示当前有效状态。
- **CA（Conversation Archive）**：保存原始聊天、会话摘要与历史重要决策概览 CA Overview，解释历史变化。
- **MD（Market Data）**：保存规范化、有明确来源和时间的市场数据。

后续每轮默认提供精简 SP、CA Overview 和近期消息；详细历史与行情由后端工具按需读取。当前方案不使用 RAG、Embedding 或向量数据库。

**T01–T04 已实现**：四个中文业务页签与 `/api` JSON 接口，可查看/编辑财务档案、目标和持仓，创建会话并保存用户消息，读取原文/摘要/CA Overview，确认或拒绝已有财务候选，查看和手动刷新模拟行情。沿用已有 Python 服务、SQLite 显式初始化/备份/版本迁移和 CLI。

**尚未实现**：模型回答、自然语言候选提取、自动摘要、模型工具调用、真实行情、后台调度、登录/多用户、交易和完整 Dashboard。发送消息仅保存用户原文，不会产生助手回复、摘要或候选。目前仅有固定虚构行情，不是实时数据，无需密钥或外网。`create_app()`、首页 HTML 和 `/health` 不访问业务数据库或提供器；浏览器加载后通过 API 读取业务数据，未初始化时显示指引，不自动建库或填充。

## 安装与运行

本轮在 Windows、Python **3.11.5**、SQLite **3.42.0**、Flask **3.1.1** 上验证。建议先使用 Python 3.11；其他 Python 版本尚未验证。`sqlite3` 随 Python 提供。

在 VS Code 打开项目文件夹，选择自己已有的 Python 解释器，在项目根目录新建终端。环境名称不限，不强制创建 Conda 或虚拟环境；用下面命令确认终端实际使用的解释器：

```powershell
python -c "import sys, sqlite3; print(sys.executable); print(sys.version); print(sqlite3.sqlite_version)"
python -c "from importlib.metadata import version; print(version('Flask'))"
```

若缺少依赖或需要对齐版本，再执行安装；已有兼容版本时不要批量升级环境：

```powershell
python -m pip install -r requirements.txt
```

唯一第三方直接运行依赖为 `Flask==3.1.1`；测试使用标准库 unittest。仅修改文档无需安装运行依赖。

启动本地开发服务：

```powershell
python run.py
```

打开 <http://127.0.0.1:5000/> 查看首页。在另一个终端检查健康接口：

```powershell
python -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:5000/health').read().decode())"
```

返回 JSON `{"status":"ok"}`（空白格式可能不同）。它表示 Web 进程可响应，不检查尚未接入的数据库或外部服务。用 `Ctrl+C` 停止。端口被占用时可运行 `python run.py --port 5001`，并将浏览器和检查命令中的端口同步改成 5001。服务仅监听本机回环地址，默认关闭调试，不用于远程部署。

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
- **市场数据**：自动读取已有缓存，手动刷新才调用模拟提供器。显示精确值、单位、模拟标记、来源、数据时间、获取/过期时间、缓存和刷新状态。无缓存显示“暂无数据”，过期旧值和刷新错误都会保留；基金日期不补造时刻，指数点位无币种。

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
python run.py --port 5001
```

打开 <http://127.0.0.1:5001/>，可依次检查：

1. 编辑金额、增加/编辑/删除目标和持仓，保存后重新载入；用两个标签页基于同一版本编辑，先保存其中一个，再保存另一个，确认出现冲突且保留输入。
2. 新建两个会话、快速切换并保存文本，刷新页面确认消息归属正确；展开摘要/Overview。重复主动发送相同文字应允许，连续点击一次保存不能产生重复请求。
3. 在不同的新副本中分别确认和拒绝待确认候选，检查档案版本及预算；仅查看不能修改。处理后按钮应消失。
4. 初始 MD 应显示空缓存，手动刷新后显示模拟值；再次读取缓存不会获取。可在浏览器开发者工具中临时模拟离线，检查错误提示与已有显示的保留，然后恢复联网。
5. 将视口缩窄，检查自然堆叠与无横向溢出；用 Tab、方向键和 Esc 操作页签/确认框，检查可见焦点，并查看开发者工具控制台是否有异常。

用 `Ctrl+C` 停止副本服务。测试副本的数据位于该副本的 `runtime/`，不会写入原开发库；不要复制真实密钥或个人资料进行检查。

## 功能检查

在项目根目录执行：

```powershell
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

升级前停止其他数据库写入，保留命令输出中的备份路径；若升级失败，保留现场检查错误，不删库重建。`md-refresh` 强制获取三项固定虚构样例并逐项原子写入；`md-show` 只读已有缓存，不触发获取。两条命令均输出完整资产 ID、指标、数值、币种/单位、来源、模拟标记、数据时间、获取/过期时间及状态。新库未刷新时显示 `missing`，不会编造数值。

| 虚构样例 | 完整资产 ID | 指标 / 单位 | 固定数据时间 |
| --- | --- | --- | --- |
| 虚构演示股票 | `demo:stock:DEMO_EXCHANGE:DEMO001` | `price` / CNY 每股 | 2026-01-15 07:00 UTC |
| 虚构演示基金 | `demo:fund:DEMO_FUNDS:DEMO001` | `nav` / CNY 每份 | 2026-01-14，仅日期 |
| 虚构演示指数 | `demo:index:DEMO_EXCHANGE:DEMO001` | `index_level` / 点，无币种 | 2026-01-15 07:00 UTC |

仅支持上述完整标识与指标；相同短代码 `DEMO001` 不足以确定资产。提供器为 `mock-fixed-v1`，全部 `is_simulated=true`。刷新只改变获取/过期时间，不把固定样例时间改成当前时间。MD 数值保存八位小数，不影响 SP 的两位金额精度。

Python 入口为 `app.md.MDService`，可用 `MDService.from_app(app)`。`list_assets()` 列出目录；`read_cache(asset_id, metric)` 只读；`query(...)` 优先命中有效缓存，否则获取；`refresh(...)` 或 `query(..., force_refresh=True)` 强刷。可注入提供器和带时区的时钟。

默认有效期 `Config.MD_CACHE_TTL_SECONDS=300` 秒，可配置为 1–86400 秒整数，影响下次成功刷新写入的过期时间。`fetched_at <= 当前时间 < expires_at` 才算 `fresh`，到期时即为 `stale`。**缓存新鲜仅表示最近获取，不能证明数据时间新或行情实时。** 结果同时返回 `cache_status`、`refresh_status`、`error` 与完整数据时间/模拟标记。获取或校验失败保留旧记录，旧值可能为 `fresh` 或 `stale`，必须同时查看本次错误；没有缓存则 `data=None`。并发重叠刷新或更旧来源时间不会覆盖已保存的更新结果，详见数据模型说明。

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
  md_provider.py     有限虚构资产目录与提供器协议
  validation.py      有限修改结构与输入校验
  demo.py / cli.py   虚构演示逻辑与显式命令
  templates/         HTML 模板
  static/            原生 JavaScript 业务交互与页面样式
run.py               启动入口
tests/               功能与迁移检查
requirements.txt     直接运行依赖
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
