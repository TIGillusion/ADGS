# ADGS

## Adaptive Darwinian Group System

ADGS 是一个面向真实工作流的多智能体协作系统。用户只和 CEO 对话，CEO 负责理解目标、查看必要的项目资料、拆分任务、并发委派、收集汇报和给出最终结论。Worker 节点通过 Hermes 在项目工作区执行任务，节点之间可以继续委派子任务。系统把项目、会话、执行记录、A2A 图谱、终端输出和恢复上下文持久化下来。

当前主入口是 **Web3 观测台**。它提供项目和会话管理、实时对话、任务队列、引导消息、A2A 图谱、节点详情、终端输出、产出物查看和深色/浅色主题。

> 这是源码仓库。模型服务、Hermes 安装、Python 虚拟环境、Node 依赖、embedding 权重、密钥和运行数据都由部署者准备。私密配置不会提交到仓库。

## 功能概览

- **CEO 协调**：CEO 使用 Hermes 决策，只负责规划、拆分、委派、复核和汇报。
- **Hermes 执行**：每个节点拥有独立 profile、会话、技能和记忆，执行过程通过终端记录持久化。
- **递归委派**：复杂任务可以继续拆分给子节点。等待子节点汇报的协调节点不会因为等待而计入无活动超时。
- **并发调度**：全局并发上限可配置。队列消息默认排队，“引导”消息在当前派发批次汇报后交给 CEO。
- **断点恢复**：会话、execution、attempt、任务事实和工具记录落盘。Hermes 进程退出后可以沿原执行继续。
- **A2A 图谱**：显示 CEO、轮次、序号、节点、子节点、委派关系和状态颜色。节点和边的详情与后端执行记录对应。
- **项目工作台**：一个项目可以有多个独立会话。项目共享节点群体、技能和长期记忆，会话拥有独立 CEO 上下文和队列。
- **外部控制 API**：外部 AI 可以查询状态、发送消息、恢复执行、读取事件、查看终端和管理项目。
- **群体进化**：节点会根据任务表现、生态位描述、技能和成功率参与路由、繁衍、重组和淘汰。

## 架构

```text
浏览器
  │ Web3 React/Vite 工作台
  ▼
ADGS Flask 宿主（8021） ───── A2A Mesh（8022）
  │
  ├─ 项目 / 会话 / 队列 / 事件 / 图谱 / 外部控制 API
  ├─ CEO Hermes（Qwen，256K 会话预算）
  ├─ 调度器与执行账本
  └─ Worker 节点 Hermes（独立 profile）
       ├─ 节点技能和长期记忆
       ├─ 终端 attempt 与结构化事件
       └─ 子任务委派和汇报
```

### 模型角色

模型通过 `config/AI_config.json` 配置。仓库只提供示例文件。

| 角色 | 职责 | 默认示例 |
| --- | --- | --- |
| `ceo` | 规划、拆分、委派、复核、汇报 | `qwen3.8-27b`，上下文预算见 `config/settings.yaml` |
| `brain` | 节点决策和任务验收 | `qwen3.8-27b` |
| `worker` | Hermes 执行器 | `qwen3.8-27b` |

CEO 当前预算配置为 256K。模型实际可用上下文、输出上限和服务端限制必须由部署者核对。

## 快速开始

### 依赖

- Python 3.11–3.13，推荐 3.12
- Node.js 20.19+ 或 22.12+
- Git、npm 和可创建虚拟环境的 Python
- 可用的 OpenAI 兼容模型接口
- 已安装的 Hermes Agent CLI 和其源码环境

Hermes 不随本仓库分发。ADGS 支持通过 `ADGS_HERMES_PYTHON` 指向 Hermes 虚拟环境，也支持从 `PATH` 中的 `hermes` 可执行文件推导安装位置。Windows 设备可以使用现有 Hermes 安装器，Linux/macOS 使用对应平台的 Hermes 安装方式。

### Linux / macOS

```bash
git clone https://github.com/TIGillusion/ADGS.git ADGS
cd ADGS

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

cp config/AI_config.example.json config/AI_config.json
# 编辑 config/AI_config.json，填入模型地址、模型名和 key

cd web3
npm ci
npm run build
cd ..

.venv/bin/python tools/run_web3.py --check
.venv/bin/python tools/run_web3.py
```

如果 Hermes 不在 PATH，先设置它的 Python 路径。

```bash
export ADGS_HERMES_PYTHON=/absolute/path/to/hermes-agent/venv/bin/python
.venv/bin/python tools/run_web3.py --check
.venv/bin/python tools/run_web3.py
```

打开 <http://127.0.0.1:8021/web3/>。

### Windows PowerShell

```powershell
git clone https://github.com/TIGillusion/ADGS.git ADGS
Set-Location ADGS

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item config\AI_config.example.json config\AI_config.json
# 编辑 config\AI_config.json

Set-Location web3
npm ci
npm run build
Set-Location ..

.\.venv\Scripts\python.exe tools\run_web3.py --check
.\.venv\Scripts\python.exe tools\run_web3.py
```

如果 Hermes 不在 PATH，可以在启动前设置。

```powershell
$env:ADGS_HERMES_PYTHON = 'C:\path\to\hermes-agent\venv\Scripts\python.exe'
.\.venv\Scripts\python.exe tools\run_web3.py --check
.\.venv\Scripts\python.exe tools\run_web3.py
```

Windows 也可以使用 `start_web3.bat`。它会构建缺失的 Web3 静态文件、复用或启动 8021 后端、打开浏览器并附着到日志查看器。生产或跨平台部署优先使用 `tools/run_web3.py`。

## 配置模型

复制示例文件后编辑真实配置。

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

`config/AI_config.json` 已被 `.gitignore` 排除。不要把 key 写入 README、日志、Issue 或提交记录。运行参数位于 `config/settings.yaml`，可通过 Web3 设置面板和控制 API 更新的白名单覆盖项会写入 `config/settings_overrides.json`。

常用运行参数包括：

- `agent.max_workers` 或对应的运行设置控制并发上限
- `hermes.worker_timeout` 控制单次模型决策预算
- `hermes.inactivity_timeout` 检测没有有效任务进展的执行
- `compress.ceo_ctx_window` 控制 CEO 会话预算，当前为 262144
- `scoring` 和高级调优项控制相关性、成功率、探索和温度

首次运行会生成 `.adgs_runtime/`、`.hermes/`、`data/`、`models/` 等本机目录。这些目录不属于源码提交内容。

## 使用 Web3 工作台

1. 在左侧创建项目，选择实际工作目录。
2. 在项目下创建一个或多个会话。
3. 输入目标，默认消息进入队列。
4. 需要尽快引导 CEO 时点击“引导”。它会等待当前派发批次的汇报边界，不终止正在执行的子任务。
5. 在图谱中查看轮次、序号、节点、子节点和状态。节点详情可以拖动，面板宽度可以调整。
6. 点击节点、执行记录或终端 attempt 查看具体任务、工具调用、任务书、报告和产出物。
7. 会话完成后，项目列表显示“成”。阻塞、失败、中断、异常和达到上限也会显示对应状态字样。点击进入会话后状态标记会记为已读。
8. 页面关闭不会自动删除会话。恢复会话后可以继续上下文，必要时通过外部 API 显式恢复原 execution。

CEO 的职责是规划和委派。复杂工作由 Worker 或子节点继续拆分，CEO 和协调节点通过汇报推进，不直接代替 Worker 执行代码。

## 外部 AI 控制 API

控制 API 默认地址为 `http://127.0.0.1:8021/api/v1`，需要控制令牌。首次启动会在 `.adgs_runtime/control_token` 生成本机令牌，也可以通过 `ADGS_CONTROL_TOKEN` 提供至少 32 个字符的令牌。

```bash
.venv/bin/python tools/adgs_control.py status
.venv/bin/python tools/adgs_control.py projects
.venv/bin/python tools/adgs_control.py sessions
.venv/bin/python tools/adgs_control.py state SESSION_ID
.venv/bin/python tools/adgs_control.py usage SESSION_ID
```

外部 AI 的推荐流程是先读取 `/health`、`/projects` 或 `/sessions`，再读取目标会话的 `/state`、`/executions` 和 `/events`。发送新消息和恢复执行必须使用幂等键，HTTP 202 只表示请求已接收，不表示任务已经完成。

常用接口如下。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 服务、运行会话、事件游标、Mesh 和事故保护状态 |
| `GET` | `/projects` | 项目树、会话、忙碌状态和终态徽标 |
| `POST` | `/sessions/{id}/messages` | 发送队列消息或引导消息 |
| `POST` | `/sessions/{id}/resume` | 从指定 execution 恢复 |
| `GET` | `/sessions/{id}/state` | CEO 阶段、活跃执行、等待子节点和并发状态 |
| `GET` | `/events` | 全局或按会话读取结构化事件 |
| `GET` | `/executions/{id}` | execution、attempt、终端和结构化事件 |
| `GET` | `/attempts/{id}/terminal` | SSE 终端输出回放 |
| `GET` | `/sessions/{id}/files` | 工作区内产出物读取和下载 |

完整字段、幂等语义、恢复规则、队列接口和 Python 示例见 [外部 AI 控制接口](docs/外部AI控制接口.md)。

## 测试与质量检查

### Python 后端

```bash
.venv/bin/python -m pytest tests -q
```

Windows PowerShell 使用：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

### Web3 前端

```bash
cd web3
npm run typecheck
npm test -- --run
npm run build
```

发布前建议至少验证项目会话、队列、状态一致性、终端流、A2A 图谱、Markdown 渲染和控制 API 测试。真实模型任务需要有效的模型配置和 Hermes 安装，不能用纯单元测试代替。

## 运行数据、备份和回滚

源码和运行数据分开管理。以下内容默认被 Git 忽略：

- `.adgs_runtime/`、`.hermes/`、`data/`、`models/`、`runtime/`
- `.venv/`、`node_modules/`、`web3/dist/`
- `config/AI_config.json`、运行覆盖配置、技能登录会话和密钥文件

升级前请备份运行数据和真实配置。源码回滚使用 Git，运行数据回滚按照 [备份与回滚](docs/备份与回滚.md) 处理。不要使用按映像名批量终止 Python、Node 或 Hermes 进程，先确认端口、PID 和执行归属。

## 安全边界

- 控制令牌拥有项目、会话、模型和节点管理权限，只提供给可信控制端。
- 控制 API 默认绑定本机回环地址。远程使用 SSH 隧道或受保护的反向代理。
- 不要把控制令牌、模型 key、Hermes profile、登录 Cookie、对话记录和工作区产物提交到公共仓库。
- 工作区路径和产出物读取由后端做路径约束。外部 AI 仍应按任务权限和执行记录判断结果。
- 真实模型和 Hermes 的执行能力由部署环境提供。仓库本身不包含模型权重和推理服务。

## 目录结构

```text
ADGS/
├── core/                  CEO、节点、调度、Hermes、恢复和项目目录
├── config/                配置代码、提示词、默认设置和脱敏示例
├── web/                   Flask API、兼容前端和事件服务
├── web3/                  当前 React/Vite/TypeScript Web3 前端
├── skills/                节点技能模板
├── tools/                 启动器、外部控制客户端和源码打包工具
├── tests/                 后端、前端和运行时回归测试
├── docs/                  API、设计、验证、备份和迁移文档
├── requirements.txt       ADGS Python 依赖
├── INSTALL.md             跨平台源码安装教程
└── web_app.py             Flask 宿主入口
```

## 当前发布边界

本仓库在 Windows 开发环境完成后端和 Web3 前端回归验证。Linux/macOS 路径按源码和 Python 进程实现提供，目标设备仍需按实际系统安装 Hermes、Python、Node 和模型依赖。没有模型服务、Hermes 或 embedding 模型时，安装检查可以通过，但真实任务不能执行。

## 许可证

本项目采用 GNU Affero General Public License v3.0 or later。完整条款见 [LICENSE](LICENSE)。Web3 前端包的许可证元数据也声明为 `AGPL-3.0-or-later`。

## 文档索引

- [跨平台源码安装](INSTALL.md)
- [外部 AI 控制接口](docs/外部AI控制接口.md)
- [备份与回滚](docs/备份与回滚.md)
- [Hermes 调度与协调节点](docs/Hermes调度与协调节点.md)
- [项目介绍](docs/PROJECT_INTRO.md)
- [Web3 前端说明](web3/README.md)
