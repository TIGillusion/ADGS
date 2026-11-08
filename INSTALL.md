# ADGS 纯源码安装

本说明适用于本包的当前Web3前端与Hermes CEO/Worker架构。先按本文安装；仓库内旧的一键脚本、README部署片段和迁移手册是历史资料，可能假设已有Windows环境、模型缓存或凭据。

## 包的内容

- ADGS完整受版本管理的后端、前端源码、技能、测试、依赖声明与文档。
- `vendor/hermes-agent/`：实际使用的Hermes源码快照，含4个本地修改文件的当前内容、原始LICENSE、pyproject.toml和uv.lock。源码版本/修改文件见 `SOURCE_MANIFEST.json`。不要用安装最新版Hermes替换这份源码。
- `SOURCE_MANIFEST.json`：各文件SHA-256及明确排除的文件；ZIP旁的 `.sha256` 为整个压缩包校验值。

这是新设备源码部署包：不含Python、Node、虚拟环境、site-packages、node_modules、Web3 dist、冻结exe、模型权重、`.git`、账号密钥、登录Cookie、节点档案、对话记录、工作区成果和运行数据库。安装依赖、构建前端、首次下载embedding需要目标设备联网；纯源码包本身不能直接离线运行。依赖锁文件是文本声明，已经包含。

## 1. 准备系统依赖

Python **3.11–3.13**，推荐3.12；所附Hermes不支持3.14。Node.js满足Vite锁文件的要求：**20.19+（20系列）或22.12+**，推荐22系列。准备npm、Git和可用的本地编译工具；ARM或其他架构需确认PyTorch等依赖提供对应wheel。

Linux以Debian/Ubuntu为例（其他发行版使用自己的包管理器）：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git build-essential
python3 --version
node --version
npm --version
```

若系统Python版本不在范围内，先安装3.12/3.13，下面的 `python3` 换成对应解释器。上述apt命令不保证发行版自带合适的Node版本，需要另行安装满足要求的Node。

macOS安装Python、Node和Git，并运行 `xcode-select --install` 安装命令行工具。后续使用与Linux相同的bash/zsh命令。

Windows安装Python 3.12（含venv）、Node和Git；如pip需要编译扩展，再安装Visual C++ Build Tools。后续使用PowerShell命令。代码的非Windows路径有POSIX进程实现；本次打包在Windows核验，未将Linux/macOS安装称为已经实机验证。其他系统需具备兼容Python、进程与依赖支持。

## 2. 解压并创建两个独立环境

完整解压到可写目录，例如 `~/ADGS` 或 `D:\apps\ADGS`。所有命令均从解压后包含 `web_app.py`、`INSTALL.md` 的目录运行。两个环境分别安装，避免ADGS的MCP 1.26与所附Hermes的MCP 2.x依赖冲突。

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

python3 -m venv vendor/hermes-agent/venv
vendor/hermes-agent/venv/bin/python -m pip install --upgrade pip
vendor/hermes-agent/venv/bin/python -m pip install -e './vendor/hermes-agent[mcp]'
```

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

py -3.12 -m venv vendor\hermes-agent\venv
.\vendor\hermes-agent\venv\Scripts\python.exe -m pip install --upgrade pip
.\vendor\hermes-agent\venv\Scripts\python.exe -m pip install -e ".\vendor\hermes-agent[mcp]"
```

Hermes需要可编辑源码安装，并将它自己的venv放在该源码目录之内。ADGS通过这个目录关系定位源码。安装器会根据各自声明下载依赖，不需要复制原设备环境。`uv.lock`随源码保留；上述pip方式使用pyproject声明的固定直接依赖，不声称锁定全部传递依赖。

## 3. 配置模型

复制示例后编辑 **新生成的** `config/AI_config.json`：

```bash
# Linux/macOS
cp config/AI_config.example.json config/AI_config.json
```

```powershell
# Windows
Copy-Item config/AI_config.example.json config/AI_config.json
```

填写ceo、brain、worker各组的 `url`、`model`、`key`。示例地址和key均为占位，必须替换；无鉴权的自建服务也填写非空占位key，如 `unused`。CEO可使用付费模型，Worker/brain指向自己的模型服务。此包不包含推理服务或LLM权重。不要把源设备的私密配置作为分发内容。

检查 `config/settings.yaml`：`agent.max_workers` / `vllm_concurrency` 应符合服务容量；`compress` 的上下文长度和worker的 `context_length` 应符合实际模型限制。不要直接沿用示例里的大上下文数值而不核对提供方。初次启动会生成新种群、项目数据和各节点Hermes配置。

可选米家技能：先自行创建 `skills/mijia-home/mijia_config.json`，见同目录example；登录会话会在首次登录后生成。没有配置时不使用该技能。ADGS Mesh插件由代码自动生成，无需复制原设备 `.hermes/`。

## 4. 构建当前Web3前端

两种系统都可执行：

```text
cd web3
npm ci
npm run build
cd ..
```

会生成 `web3/dist/`。源码包未预装node_modules或dist，所以这一步不可跳过。构建后正常启动不需要保持Vite开发服务器运行。

## 5. 下载embedding模型

```bash
# Linux/macOS
.venv/bin/python -c "from utils.embedding import get_embedding; print(get_embedding('安装检查').shape)"
```

```powershell
# Windows
.\.venv\Scripts\python.exe -c "from utils.embedding import get_embedding; print(get_embedding('安装检查').shape)"
```

首次下载 `BAAI/bge-small-zh-v1.5` 到本项目 `models/`。CPU可以加载，生成512维向量。默认首次下载使用代码中的HF镜像；如需官方站，运行前设置 `HF_ENDPOINT=https://huggingface.co`。网络失败时先修复下载链路；尚无模型缓存时不要设置 `HF_HUB_OFFLINE=1`。纯源码包不带模型缓存。

## 6. 检查并启动

```bash
# Linux/macOS
.venv/bin/python tools/run_web3.py --check
.venv/bin/python tools/run_web3.py
```

```powershell
# Windows
.\.venv\Scripts\python.exe tools/run_web3.py --check
.\.venv\Scripts\python.exe tools/run_web3.py
```

启动器会为Hermes设置自己的Python与PATH，无需在当前终端同时激活两个venv。`--check`只核对安装文件，不代表模型API已连通；正常使用还需发送一个小任务，确认实际Worker执行和验收。

打开 **http://127.0.0.1:8021/web3/**，新建项目并选择目标设备上的真实工作目录。A2A Mesh使用8022端口。若端口占用，先检查具体所属进程，不要按python/node映像名称批量终止。

默认任务宿主独立运行，关闭网页或前台Web进程不等于停止任务；需要暂停时使用会话暂停接口。单进程调试可加 `--local`，此模式退出进程会中断任务。远端访问可用SSH隧道映射8021，例如 `ssh -N -L 8021:127.0.0.1:8021 user@host`，再在本地浏览器打开对应地址。

## 7. 给其他Agent调用

```bash
.venv/bin/python tools/adgs_control.py status
.venv/bin/python tools/adgs_control.py sessions
.venv/bin/python tools/adgs_control.py state SESSION_ID
.venv/bin/python tools/adgs_control.py usage SESSION_ID
```

Windows将解释器换为 `.\.venv\Scripts\python.exe`。控制入口为 `http://127.0.0.1:8021/api/v1`；首次运行生成本机 `.adgs_runtime/control_token`，客户端自动读取。完整说明见 [外部AI控制接口](docs/外部AI控制接口.md)。

## 8. 常见问题与验证

- `AI_config.json`不存在：完成第3步，JSON不能带注释。
- `Installed Hermes source/venv not found`：按第2步在 `vendor/hermes-agent/venv` 安装；使用本文启动器。手工启动时同时设置 `ADGS_HERMES_PYTHON` 为该venv的Python绝对路径，并把它的Scripts/bin目录加入PATH。
- `hermes`命令不存在或启动了其他版本：使用本文启动器；不要让系统另一套Hermes抢占PATH。
- MCP依赖冲突：ADGS和Hermes必须安装到各自环境，不能把两份依赖混装。
- Web3 404：完成 `npm ci && npm run build`，检查 `web3/dist/index.html`。
- 模型服务403/401/超时：核对目标设备访问地址、模型名和key；本包未携带原设备服务配置。
- PyTorch等依赖没有对应wheel：选择支持的Python版本和系统架构，或按对应库要求安装编译环境。未知系统不能仅凭解压认定可运行。

可选后端回归（在ADGS环境安装pytest后执行）：

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest tests/test_control_api.py tests/test_control_usage.py tests/test_state_fidelity.py -q
```

这些测试需要按第3步准备合法配置结构，但使用隔离的测试状态。前端验证可在 `web3/` 运行 `npm test`。包的完整性验证不需要安装依赖：`python tools/package_source.py --verify 下载的.zip`。
