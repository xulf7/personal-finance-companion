# 小组 GitHub 协作指南

适用于 xulf7、JunjieLiuCUHKSZ 和 Lawrenceluo827。xulf7 负责仓库维护与合并协调。仓库：<https://github.com/xulf7/personal-finance-companion>。

## 开始前与分支约定

先在群里确认任务、负责成员和准备修改的文件，避免重复修改同一部分。日常工作在自己的任务分支进行，不直接修改 `main`。`main` 是全组共享的已合并版本。

分支名说明用途即可，例如：

| 分支 | 例子 |
| --- | --- |
| `feature/...` | `feature/profile-view`：新增档案页面 |
| `fix/...` | `fix/health-response`：修复健康接口 |
| `docs/...` | `docs/setup-guide`：更新运行说明 |

这些只是命名例子，不表示相应业务功能已经完成。每个新任务从更新后的 `main` 建立新分支，不继续堆到已经合并的旧分支。

## VS Code 日常流程

先安装 Git 和 VS Code，确保自己的 GitHub 账号有仓库访问权限。首次获取项目可按 `Ctrl+Shift+P`，运行 **Git: Clone**，粘贴仓库地址、选择本地父目录并打开项目；按提示登录 GitHub。已有本地仓库直接“文件 → 打开文件夹”，无需重新初始化仓库。

### 1. 更新 main，再建立自己的分支

1. 打开左侧“源代码管理”（`Ctrl+Shift+G`），确认没有尚未处理的修改；点击左下角分支名，选择 `main`（或首次选择 `origin/main`）。若有未提交修改，保留现场，与组员确认如何保存后再切换，不点击丢弃修改。
2. 在源代码管理的 `…` 菜单选择 **Pull（拉取）**，把远端 main 更新到本地。若菜单不易找到，按 `Ctrl+Shift+P` 搜索 **Git: Pull**。若发生冲突或出现本地 main 超前/分叉的提示，停止操作并联系 xulf7。
3. 更新成功后，按 `Ctrl+Shift+P`，运行 **Git: Create Branch...（创建分支）**，输入任务名，例如 `docs/setup-guide`。VS Code 会创建并切换到该分支；检查左下角名字后再改文件。

### 2. 修改、检查、暂存和提交

1. 编辑并保存文件。使用编程智能体后，也要由成员逐个检查差异，确认任务范围、数据和密钥均正确。
2. 在源代码管理的 **Changes（更改）** 中点击文件，查看差异。新文件检查全部内容；已有文件检查新增和删除内容。修改代码后按下节运行必要测试；只改文档检查文字、链接与命令即可。
3. 对准备提交的文件点击旁边的 **+（Stage Changes，暂存更改）**，再次查看 **Staged Changes（暂存的更改）**，不要把无关文件一起提交。暂存后又修改文件，需要重新检查并暂存新版本。
4. 输入简短提交说明，例如 `docs: 补充启动步骤`，点击 **Commit（提交）**。这一步只保存到本机历史，不会上传。

### 3. 发布或推送自己的分支

首次上传任务分支时点击 **Publish Branch（发布分支）**；后续在同一分支提交后，使用源代码管理 `…` → **Push（推送）**。确认目标是自己的任务分支和本仓库 `origin`。不要另建一个 GitHub 仓库。

**Sync Changes（同步更改）** 会先拉取再推送；新手可以按这里的分步操作，以便知道正在做什么。如果被拒绝推送或出现冲突，保留现场与组员沟通，不强推，不反复点击同步。

菜单名称会随 VS Code 语言略有不同，可参照 [VS Code 官方提交说明](https://code.visualstudio.com/docs/sourcecontrol/quickstart) 和 [分支说明](https://code.visualstudio.com/docs/sourcecontrol/branches-worktrees)。

## PR 检查与统一合并

四个常见操作：**Commit** 把选定修改记入本机历史；**Push** 将本机提交上传到远端分支；**PR（Pull Request）** 提出合并请求、集中讨论差异；**Merge** 才是将审核后的改动合入目标分支。

1. 推送后打开 GitHub 仓库，点击 **Compare & pull request**，或进入 **Pull requests → New pull request**。
2. 检查 **base = main**，**compare = 自己的任务分支**。写清改了什么、为何修改、如何验证；未验证的内容如实注明。
3. 邀请其他协作者检查 PR（有 Reviewers 选项时指定成员，也可在群里发送 PR 链接）。不要自行合并到 main，由 **xulf7 确认后统一合并**。
4. 有修改意见时，继续在原分支修改、检查、Commit 和 Push，原 PR 会自动更新，无需再开同一任务的 PR。
5. 合并后回到 VS Code，确认当前修改已妥善处理，再切换 `main` 并 Pull。下一个任务重新从更新后的 main 建立新分支。

不强推、不改写共享历史，不用重置或丢弃修改来绕过问题。遇到冲突先告知组员，确认保留哪些内容，再共同处理。编程智能体不自动提交、推送或合并，以上操作由成员明确决定。

## Python、运行检查与本地资料

组员可使用自己的 Python 环境，不要求相同环境名称。本轮验证了 Python 3.11.5、SQLite 3.42.0 和 Flask 3.1.1；建议先使用 Python 3.11，其他版本尚未验证。在 VS Code 中选择解释器后，也确认终端中的 `python` 指向预期环境：

```powershell
python -c "import sys; print(sys.executable); print(sys.version)"
```

运行或修改代码时，在项目根目录检查并按需安装直接依赖（Flask、AKShare、OpenAI SDK），然后启动：

```powershell
python -m pip install -r requirements.txt -c constraints.txt
python run.py
```

已有兼容依赖时可跳过安装，不批量升级环境。打开 <http://127.0.0.1:5000/>；另开终端检查并测试：

```powershell
python -c "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:5000/health').read().decode())"
python -B -m unittest discover -s tests -v
```

首次克隆或拉取包含数据库迁移的代码后，在使用业务服务前先执行 `python -m flask --app app:create_app init-db`；已有库会在需要升级时自动备份，不要删库重建。SP/CA 演示和模拟行情均须显式填充，Web 启动不会自动写入。首次上手、已有库升级、`md-refresh`/`md-show` 的完整步骤见 [README](README.md#模拟行情与已有库升级)，只改文档无需初始化数据库。

行情默认 mock 可离线运行。模型调用使用官方 OpenAI SDK 2.8.1，按 requirements 与 constraints 安装，完整离线测试也需 SDK，但不需要真实密钥或外网。真实行情仍使用 AKShare 1.20.18，模式及缓存步骤见 [README](README.md#akshare-真实日线切换刷新与查看)，勿批量升级环境。T07 的 004 迁移将已有 T05 版本 3 升级至 4，执行 init-db 会先备份并保留聊天及业务数据；不重填 SP/CA、不删库。本轮没有新增依赖。

“对话记录”完成轮次后可点击“生成归档摘要”，显式调用模型并更新历史记忆，可能计费；普通发送仍不归档。有原文依据时生成待确认候选，在原页面核对来源并确认/拒绝；确认前 SP 不变。失败用原归档重试按钮，勿反复点击。分段范围、手动验收及当前限制见 [README 归档说明](README.md#对话归档与跨会话记忆t07)。请用隔离副本和虚构数据验证800→计划900→明确提出900→确认→新会话的流程，保护开发库。

聊天现可按需查历史摘要、查当前目录行情及计算固定月存入目标；无需新增依赖或T08迁移，结构仍为v4。默认mock，真实行情仍须显式选AKShare；多轮模型请求可能增加费用和等待。失败用原问题重试，成功重放不再调用工具。计算不计收益、不换汇，也不把情景写入SP。请按[README工具演示](README.md#聊天工具与-mvp-演示t08)在隔离副本验收；用量、日期、模拟/过期标记及失败信息都需核对。

同一归档生成多项候选时，先逐条拒绝不想应用的项，再“合并确认剩余N项”，核对每项原文后一次保存。版本或字段冲突先重新读取并处理，不自动换版本。此次修复保持结构v4，无新迁移或依赖；旧memory_limit失败可按原范围重试。桌面/390px人工步骤见README，使用隔离副本，不修改开发数据。

归档候选现在由模型理解意图及换算基本单位，后端只校验结构/业务契约和来源，不通过正则重新解读原文。来源真实不保证理解正确，确认前务必对照字段、新值、币种和原文，尤其核对万元等倍率。候选操作开始时清理旧提示，取消不写入；出现版本冲突应显示本次具体版本，勿依据上一轮成功提示判断。本轮模型替身与Node DOM替身测试不等于真实模型或浏览器验收，结构仍为v4。

首页和健康检查不需要 API 密钥或外网。用 `Ctrl+C` 停止；端口冲突时用 `python run.py --port 5001`，访问和检查时同样换端口。只改文档无需安装全部运行依赖；检查 Markdown、链接以及与实际代码一致的命令即可。更多路径与迁移说明见 [README](README.md)。

首页现已提供四个业务页签。聊天发送会保存用户原文并请求模型、保存助手回复；失败保留原问题并提供明确重试，不会自动更新档案、摘要或候选。验证聊天或档案修改时请使用虚构数据和独立副本，保护开发数据。步骤见 [README 网页使用](README.md#网页使用)，HTTP 契约见 [docs/API.md](docs/API.md)。

模型配置按 [README 模型聊天与连通检查](README.md#百炼模型聊天与连通检查t05) 创建项目外私密 JSON，再在项目根目录 config.local.json 中填写自己的 llm_config_file 路径；本机文件已忽略，不含密钥或模型地址。启动 `python run.py` 后到“对话记录”发送；独立检查仍为 `python -B -m app.llm_check`。PFC_LLM_CONFIG_FILE 可临时覆盖路径，显式错误不回退。发送、归档、明确重试和连通检查会产生费用；普通启动、GET 和自动测试不会调用。重试失败问题用原消息旁的按钮，结果未知先读取会话，勿新发相同问题。换电脑时调整路径，不使用 setx、不共享真实配置；并发范围及验收结果以 README 为准。

## 换行与模型调试

仓库文本以 LF 保存，允许 Windows 工作目录使用 CRLF。修改现有文件保留原换行，新建文本用 LF，同一文件内不混用；不为统一换行批量重写文件，不擅自修改组员的全局 Git 或编辑器设置。可按需查看模型输入、上下文和回复进行调试；使用虚构数据和忽略的 runtime 记录，真实 API Key 仅由程序内部读取和认证使用，不主动查看或输出。

不要提交真实密钥、`.env`、`config.local.*`、`library/` 背景资料或 `runtime/` 运行数据和数据库。新克隆的仓库不包含各成员自己的本地数据。需要共享测试样例时使用明确标注的合成数据，必要的迁移 SQL 和数据生成脚本可正常提交。

## 可选终端操作

以下只是图形操作的等价补充，不必两套都执行。在项目根目录逐行运行；**先确认没有未提交修改**，再从最新 main 创建分支（示例任务名请按实际任务替换）：

```powershell
git status --short
git switch main
git pull --ff-only origin main
git switch -c docs/setup-guide
```

修改并验证后，只选择实际要提交的文件，例如本次只改协作指南时：

```powershell
git diff
git add CONTRIBUTING.md
git diff --cached
git commit -m "docs: 更新协作指南"
git push -u origin docs/setup-guide
```

后续继续修改该分支时，检查并提交后用 `git push`。在 GitHub 创建和审核 PR，等待 xulf7 合并；合并后、工作区无未提交修改时：

```powershell
git switch main
git pull --ff-only origin main
git switch -c feature/next-task
```

任一步失败时先停下检查，不跳过错误继续后续命令，也不要使用 `--force`。
