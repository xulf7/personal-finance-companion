# Personal Finance Companion

MIS2001 小组项目：结合个人财务档案、历史决策与市场数据的个人金融规划助手。采用 Flask、HTML/CSS/JavaScript 与本地 SQLite 的模块化单体结构，模型 API 在后续任务接入。

## 设计与当前能力

- **SP（Structured Profile）**：保存当前已确认的结构化财务档案，表示当前有效状态。
- **CA（Conversation Archive）**：保存原始聊天、会话摘要与历史重要决策概览 CA Overview，解释历史变化。
- **MD（Market Data）**：保存规范化、有明确来源和时间的市场数据。

后续每轮默认提供精简 SP、CA Overview 和近期消息；详细历史与行情由后端工具按需读取。当前方案不使用 RAG、Embedding 或向量数据库。

**T01 已实现**：可启动的 Flask 应用、介绍页、`GET /health`、集中路径配置，以及基础功能和迁移测试。首页展示模块职责与实际开发阶段。

**尚未实现**：SP/CA/MD 业务表与数据库读写、模型与行情 API、摘要与工具调用、完整聊天界面、目标 Dashboard。当前不创建业务数据库，也不需要 API 密钥。

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

测试覆盖无密钥且禁止网络连接时的首页/健康接口、静态样式、集中路径，以及根目录、不同工作目录、迁移副本的真实本机 HTTP 启动。测试会在 `runtime/tests/` 下创建独立临时目录，复制最少的应用文件，检查副本使用自己的根目录与 runtime，结束后只清理该次临时目录。测试需要允许本机回环连接，不需要外网或模型/行情服务。

## 文件与运行数据

```text
app/
  __init__.py        应用创建
  config.py          集中配置与路径
  routes.py          首页和健康检查
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
| `runtime/data/` | 未来本地数据库，暂定 `app.sqlite3` |
| `runtime/logs/` | 需要时产生的日志 |
| `runtime/imports/` | 未来的实际导入数据 |
| `runtime/backups/` | 未来的数据库备份 |
| `runtime/tests/` | 测试临时文件与临时数据库 |

目录按需创建；当前应用启动不创建上述目录或数据库，测试只创建所需测试目录。源码与持久化引用不得绑定用户名、盘符或本机绝对路径。

`library/` 是各成员的本地背景资料，不修改或提交；`runtime/` 是各成员自己的运行数据；公开源码、测试、文档及依赖清单由 Git 管理。新克隆的仓库不含背景资料、运行数据或真实配置。

当前无需配置文件，不自动读取 `.env`，因此不提供无用途的 `.env.example`。后续引入密钥时仅放在后端私有配置中；真实 `.env`、`config.local.*`、数据库及附属文件已忽略。无密钥的 `.env.example`、迁移 SQL、数据脚本和合成测试样例允许纳入 Git。

## 整体迁移与协作

停止服务，将整个项目文件夹复制到新位置，在新环境检查 Python 并按需安装 `requirements.txt`，再执行启动和测试命令，无需修改路径。若要保留 Git 历史，复制时包含 `.git`；若未来需携带本地数据，应在服务停止后自行保管并复制 `runtime/`。私有资料和密钥单独安全传递，不通过公开仓库同步；Python 环境不随文件夹迁移，使用新机器自己的解释器。

小组日常操作见 [小组 GitHub 协作指南](CONTRIBUTING.md)，以 VS Code 图形操作为主。
