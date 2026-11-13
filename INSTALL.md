# ADGS 源码安装与使用教程

本文对应仓库当前的 Web3 前端、Hermes CEO/Worker 架构和外部控制 API。安装前请准备模型服务和 Hermes Agent CLI。仓库只包含 ADGS 源码、技能模板、测试和文档，不包含 Python 环境、Node 依赖、模型权重、Hermes 安装、账号密钥或运行数据。

## 1. 系统要求

- Python 3.11–3.13，推荐 3.12
- Node.js 20.19+ 或 22.12+
- Git、npm 和 Python venv 支持
- 可访问的 OpenAI 兼容模型接口
- 可执行的 Hermes Agent CLI 及其源码环境

Windows、Linux 和 macOS 的依赖版本必须由目标设备实际支持。ADGS 在 Windows 开发环境完成回归验证，Linux/macOS 使用代码中的 POSIX 路径与进程实现，仍需在目标系统自行验证 Python、Hermes、PyTorch 和终端依赖。

## 2. 安装 Hermes

Hermes 不随本仓库分发。请按 Hermes Agent 对应平台的官方安装方式完成安装，并确认以下任一条件成立。

1. `hermes` 在 `PATH` 中，且同一安装目录能找到 Hermes 源码和 Python 环境。
2. 设置 `ADGS_HERMES_PYTHON`，指向 Hermes 虚拟环境中的 Python。

Windows PowerShell 示例。

```powershell
$env:ADGS_HERMES_PYTHON = 'C:\path\to\hermes-agent\venv\Scripts\python.exe'
hermes --version
```

Linux/macOS 示例。

```bash
export ADGS_HERMES_PYTHON=/absolute/path/to/hermes-agent/venv/bin/python
hermes --version
```

ADGS 的 `core/terminal_runtime.py` 和 `tools/run_web3.py` 会检查显式路径、项目内兼容路径和 `PATH` 中的 Hermes。Hermes Python 所在环境必须能导入 `hermes_cli.main`。如果只安装了一个无法定位源码的独立可执行文件，请改用源码安装或显式设置正确的 Python 路径。

## 3. 获取源码并安装 ADGS 后端

### Linux / macOS

```bash
git clone https://github.com/TIGillusion/ADGS.git ADGS
cd ADGS
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
git clone https://github.com/TIGillusion/ADGS.git ADGS
Set-Location ADGS
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 只描述 ADGS 后端依赖。Hermes 使用自己的安装环境，避免与 ADGS 的 MCP 和 Python 依赖混装。

## 4. 配置模型

复制脱敏示例。

```bash
cp config/AI_config.example.json config/AI_config.json
```

```powershell
Copy-Item config\AI_config.example.json config\AI_config.json
```

编辑新生成的 `config/AI_config.json`。

```json
{
  "ceo": {
    "url": "http://model-host:9000/v1",
    "model": "qwen3.8-27b",
    "key": "your-key"
  },
  "brain": {
    "url": "http://model-host:9000/v1",
    "model": "qwen3.8-27b",
    "key": "your-key"
  },
  "worker": {
    "url": "http://model-host:9000/v1",
    "model": "qwen3.8-27b",
    "key": "your-key",
    "context_length": 262144
  }
}
```

CEO 默认示例使用 Qwen。你可以把三组模型指向不同的 OpenAI 兼容服务。`config/AI_config.json` 已在 `.gitignore` 中，真实 key 不应进入 Git。

检查 `config/settings.yaml` 中的上下文和并发参数是否符合模型服务能力。当前 CEO 预算为 262144。模型服务返回的最大上下文、输出限制和并发上限可能不同，部署时应以实际服务为准。

## 5. 构建 Web3 前端

```bash
cd web3
npm ci
npm run typecheck
npm run build
cd ..
```

构建后应存在 `web3/dist/index.html`。源码仓库不提交 `node_modules` 和 `dist`。首次下载 embedding 模型时，代码可能需要访问模型仓库。

可以单独检查 embedding。

```bash
.venv/bin/python -c "from utils.embedding import get_embedding; print(get_embedding('安装检查').shape)"
```

Windows PowerShell 使用：

```powershell
.\.venv\Scripts\python.exe -c "from utils.embedding import get_embedding; print(get_embedding('安装检查').shape)"
```

## 6. 检查和启动

`tools/run_web3.py --check` 只检查文件和 Hermes 环境，不访问模型接口。

```bash
.venv/bin/python tools/run_web3.py --check
.venv/bin/python tools/run_web3.py
```

```powershell
.\.venv\Scripts\python.exe tools\run_web3.py --check
.\.venv\Scripts\python.exe tools\run_web3.py
```

打开 <http://127.0.0.1:8021/web3/>。ADGS Mesh 默认使用 8022。

Windows 可以双击 `start_web3.bat`。它会在缺少前端构建产物时执行 `npm run build`，复用或启动 8021 后端，并打开浏览器。该脚本适合本机使用，跨平台部署优先使用 `tools/run_web3.py`。

启动后发送一个简短测试任务，确认 CEO 能访问模型服务、Hermes 能启动、Worker 能写入测试工作区。安装检查通过并不代表模型 API 已连通。

## 7. 常用运行目录

首次运行会生成下列目录。它们是本机状态，不能复制进公共源码仓库。

| 目录 | 用途 |
| --- | --- |
| `.adgs_runtime/` | Flask 宿主状态、控制令牌、执行记录、终端记录和事件数据库 |
| `.hermes/` | 项目内 Hermes 数据根目录、profile、技能、记忆和会话 |
| `data/` | 节点注册表、项目会话、归档和工作区元数据 |
| `models/` | embedding 模型缓存 |
| `web3/dist/` | Web3 生产构建产物 |

任务工作区由项目设置决定，可以位于仓库外部。项目和会话之间共享群体、技能和长期记忆，会话历史、CEO 上下文、队列和执行状态独立保存。

## 8. 外部 AI 控制

控制 API 默认地址为 `http://127.0.0.1:8021/api/v1`。令牌首次启动时写入 `.adgs_runtime/control_token`，也可以通过 `ADGS_CONTROL_TOKEN` 设置。不要通过公共 URL 或日志暴露令牌。

```bash
.venv/bin/python tools/adgs_control.py status
.venv/bin/python tools/adgs_control.py projects
.venv/bin/python tools/adgs_control.py sessions
.venv/bin/python tools/adgs_control.py state SESSION_ID
.venv/bin/python tools/adgs_control.py usage SESSION_ID
```

外部 AI 应先读取 `/health` 和 `/projects`，再读取目标会话的 `/state`、`/executions`、`/events` 和 `/files`。发送消息和恢复执行使用幂等键。HTTP 202 只说明后台接收，不表示任务完成。

完整 API、恢复和幂等规则见 [docs/外部AI控制接口.md](docs/外部AI控制接口.md)。

## 9. 测试

仓库根目录包含 `pytest.ini`，将 Python 测试收集范围限定为 `tests/`，避免误收集运行数据、Hermes 源码或历史工作区中的同名测试。

```bash
.venv/bin/python -m pytest tests -q
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

前端测试和构建。

```bash
cd web3
npm run typecheck
npm test -- --run
npm run build
```

真实任务测试需要有效的模型配置、Hermes 和可写工作区。单元测试不能替代真实模型和端到端执行验证。

## 10. 备份、升级和回滚

升级前至少备份以下内容。

- `.adgs_runtime/`
- `.hermes/`
- `data/`
- `config/AI_config.json`
- `config/settings_overrides.json`
- 任务工作区和重要产出物

源码回滚使用 Git。运行数据回滚按照 [docs/备份与回滚.md](docs/备份与回滚.md) 处理。重启或恢复任务前确认端口、PID、会话和 execution 归属，不要按 `python`、`node` 或 `hermes` 映像名批量结束进程。

## 11. 常见问题

### `tools/run_web3.py --check` 提示 Hermes 环境不存在

确认 `hermes --version` 可执行，或设置 `ADGS_HERMES_PYTHON`。该 Python 必须位于包含 `hermes_cli/main.py` 的 Hermes 源码环境中。

### Web3 返回 404

进入 `web3/` 执行 `npm ci && npm run build`，确认 `web3/dist/index.html` 存在，然后重启 ADGS 后端。

### 模型返回 401、403 或超时

核对 `config/AI_config.json` 的 URL、模型名和 key，确认目标设备能访问模型服务。不要把 key 写入命令行历史或日志。

### 任务长时间没有变化

读取 `/api/v1/sessions/{session}/state` 和 `/api/v1/executions/{execution}`，区分 CEO 等待子节点、排队、Hermes 工作、等待输入和真正无活动。终端 attempt 的 SSE 输出用于判断实际工具进展。必要时从原 execution 恢复，不要用新 execution 代替原任务。

### 需要远程控制本机服务

默认只绑定本机回环地址。优先使用 SSH 隧道，再通过 `/api/v1` 控制。完整安全边界见外部控制接口文档。
