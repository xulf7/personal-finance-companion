# Personal Finance Companion

MIS2001 小组项目：结合个人财务档案、历史决策与市场数据的个人金融规划助手。采用 Flask、HTML/CSS/JavaScript 与本地 SQLite 的模块化单体结构，模型 API 在后续任务接入。

## 设计与当前能力

- **SP（Structured Profile）**：保存当前已确认的结构化财务档案，表示当前有效状态。
- **CA（Conversation Archive）**：保存原始聊天、会话摘要与历史重要决策概览 CA Overview，解释历史变化。
- **MD（Market Data）**：保存规范化、有明确来源和时间的市场数据。

后续每轮默认提供精简 SP、CA Overview 和近期消息；详细历史与行情由后端工具按需读取。当前方案不使用 RAG、Embedding 或向量数据库。

**T01/T02 已实现**：Flask 首页和 `GET /health`；SP 当前档案、目标、持仓的 SQLite 持久化；会话、原始消息、版本化摘要及 CA Overview；财务变更候选的创建、确认和拒绝；显式初始化、版本化 SQL 迁移和虚构演示数据。业务能力通过 Python 服务提供。

**尚未实现**：业务 HTTP 接口与交互页面、模型与行情 API、自动摘要与模型工具调用、MD、完整聊天界面和目标 Dashboard。不需要 API 密钥；导入应用、启动、访问首页或 `/health` 均不自动创建或初始化业务数据库。

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

## 功能检查

在项目根目录执行：

```powershell
python -B -m unittest discover -s tests -v
```

测试覆盖无密钥且禁止网络连接时的首页/健康接口、原有不同工作目录与迁移副本的真实 HTTP 启动，以及文件数据库重连、重复初始化、消息与摘要来源、精确金额、候选状态/并发版本冲突/失败回滚、升级备份与迁移回滚、虚构数据幂等和携带数据库的整体迁移。

所有测试使用 `runtime/tests/` 下独立临时目录和数据库，结束后仅清理该次临时文件，不访问实际开发数据库。测试需要允许本机回环连接，不需要外网或模型/行情服务。

## 初始化与虚构演示

在项目根目录显式执行（无需启动 Web 服务）：

```powershell
python -m flask --app app:create_app init-db
python -m flask --app app:create_app seed-demo
```

`init-db` 首次创建 `runtime/data/app.sqlite3` 和版本 0 的空 SP（金额未知）；数据库结构当前为版本 1。重复执行不清库或覆盖数据。以后有待执行的迁移时，会先将已初始化数据库备份到 `runtime/backups/`，再在事务内升级；无待执行迁移时不备份。高于代码版本、未知表结构或迁移文件被改写时会明确报错并保留数据。尚未初始化时，Python 业务服务会提示先运行 `init-db`。详情见 [数据模型与迁移说明](docs/DATA_MODEL.md)。

`seed-demo` 只填充明确标注的**虚构数据**：投资预算 CNY 500.00 → 用户提出 800.00 → 创建候选 → 明确确认后 SP 为 800.00；保留原始消息、旧预算摘要、修改原因、Overview，另有 1000.00 的待确认候选。摘要由代码明确写入，不是模型生成。

重复执行演示命令不新增或覆盖数据；若数据库已有其他业务数据则拒绝填充。演示记录是本地开发数据，不会进入 Git，也不会在应用启动时自动产生。查看本地档案及候选状态：

```powershell
python -c "from pprint import pprint; from app.storage import Store; s=Store(); pprint(s.get_profile()); pprint(s.list_changes())"
```

业务入口为 `app.storage.Store`；在 Flask 相关代码中可用 `Store.from_app(app)` 读取应用配置。可选 `create_app(test_config)` 支持测试路径隔离；调用方应同时配置所需的 `DATABASE_PATH`、`RUNTIME_DIR`、`BACKUP_DIR` 等路径至项目内独立测试目录。服务签名、修改操作与异常类型见 [DATA_MODEL.md](docs/DATA_MODEL.md)。本轮不新增业务 HTTP 路由。

## 文件与运行数据

```text
app/
  __init__.py        应用创建
  config.py          集中配置与路径
  routes.py          首页和健康检查
  db.py              显式迁移、连接与事务
  migrations/        纳入 Git 的版本化 SQL
  storage.py         SP/CA 可复用服务
  validation.py      有限修改结构与输入校验
  demo.py / cli.py   虚构演示逻辑与显式命令
  templates/         HTML 模板
  static/            页面样式
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
