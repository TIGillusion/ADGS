# ADGS — 智能体达尔文进化系统

新设备安装或使用纯源码包，请先阅读 [跨平台安装说明](INSTALL.md)。当前入口是Web3，CEO和Worker均由Hermes驱动；下文部分架构、模型和一键安装说明为历史记录，以INSTALL及当前源码为准。源码包包含所用Hermes源码快照，不包含运行环境、模型或私密配置。

> Adaptive Darwinian Group System · v3

一个由多个 AI 智能体节点组成的「种群」系统：节点像生物个体一样经历**繁衍、基因重组、生态位竞争与淘汰**，在真实任务中优胜劣汰、自动进化。CEO 作为唯一入口与用户对话，负责任务拆解、路由派发与结果汇报。

---

## 一、核心概念

| 术语 | 含义 |
|---|---|
| **CEO** | 唯一用户入口（DeepSeek 强推理模型），澄清意图 → 拆解任务 → 路由派发 → 验收汇报 |
| **节点（Node）** | 一个「智能体(Leader) + hermes 执行器」的组合体，对应一个独立的 hermes profile |
| **节点智能体（AGENT）** | 节点的"大脑"，决策下一步动作（提问/执行/拆分），验收打分，维护自我描述 |
| **hermes 执行器** | 真正干活的 agent，绑定独立 profile，在指定工作区内完成任务 |
| **技能基因** | 节点通过 hermes profile 持有的 skill，繁衍时遗传给子代 |
| **生态位（描述）** | 节点的能力画像（≤500 字），用于任务路由向量匹配和拥挤度竞争 |
| **繁衍 / 淘汰** | 每轮对话开始时，繁殖固定数量新节点、淘汰一部分劣质节点 |

---

## 二、系统架构

```
用户
 │  对话
 ▼
┌─────────────────────────────────────────────┐
│ CEO（deepseek-v4-flash）                     │
│  · ask_user 澄清 / read_file / list_files    │
│  · split_tasks / single_task 动态拆解        │
│  · report 汇报                               │
└──────────────┬──────────────────────────────┘
               │ 任务书 markdown（路由派发）
               ▼
┌─────────────────────────────────────────────┐
│ 任务路由 router                              │
│  score = 向量相似度(task, describe) × 成功率 │
│  top-k → softmax(score/T) 温度采样           │
└──────────────┬──────────────────────────────┘
               │ 选中节点
               ▼
┌─────────────────────────────────────────────┐
│ 节点 NODE（ags_N）                           │
│  ┌─ 智能体 AGENT（qwen3.6-27b）              │
│  │    ask_upper / plan_hermes / split_tasks  │
│  │    accept_report 验收打分                 │
│  └─ hermes 执行器（qwen3.6-27b，独立 profile）│
│       在工作区内执行任务、产出交付物         │
└─────────────────────────────────────────────┘
```

三层模型：
- **CEO**：`deepseek-v4-flash` @ DeepSeek 官方 API（强推理，唯一入口）
- **节点智能体 + hermes 执行器**：`qwen3.6-27b` @ 自建 vLLM（`8.138.131.119:9000`）

---

## 三、核心机制详解

### 1. 答题卡模式（强制工具调用）

所有 LLM 决策都走 `tool_choice="required"` 强制工具调用——模型必须返回结构化的工具参数，而不是自由文本。参数天然是结构，无需解析自然语言。

- 工具协议固定；参数或汇报格式错误先修正协议，有限重试共享 deadline。无法判断不会被当成成果失败。
- CEO 工具：`ask_user` / `split_tasks` / `single_task` / `read_file` / `list_files` / `report`
- 节点智能体工具：`ask_upper` / `plan_hermes` / `split_tasks` / `declare_done` / `report_up` / `accept_report`（验收）

### 2. 任务路由（向量 × 成功率）

```
score_i = cosine(task_vec, describe_vec_i) × success_rate_i
```

- 用 `bge-small-zh-v1.5` 本地 embedding（缓存于 `./models/`，离线优先）
- 取 top-k（默认 5），按 `softmax(score/T)` 温度采样选节点（T 低=偏向最优，T 高=探索）
- 成功率跟随节点走（EMA 平滑）

### 3. 达尔文进化引擎（两大力量）

**力量一：数量压力（S 型生存曲线）**
```
P_base(n) = 1 / (1 + e^((K - n) / τ))     K=50 承载量, τ=8
本轮淘汰数 E = round(R + (n - K) × coef)   R=5 繁衍数, coef=0.5
```
节点越多压力越大，种群自动收敛到承载量附近。

**力量二：种类竞争（生态位拥挤度）**
```
density_i = Σ_j exp(-(1 - cos(v_i, v_j)) / σ²)    仅计入相似度 > 0.6 的邻居
```
技能生态位重叠越多的节点，淘汰概率越高（生态位压制）。空节点（描述未分化）不参与拥挤竞争，压力取均值。

**繁衍（每轮固定 R 个）**
- 亲代选择：按"生存压力小"加权随机（拥挤度低 → 更容易繁衍）
- 基因重组（70%）：2 亲代 → 技能并集随机抽（数量=两亲代平均值）→ 无记忆
- 白板（30%）：无技能无记忆的全新节点
- 子代出生时自动创建 hermes profile + 装默认 `anysearch` 技能 + 继承基因技能

**淘汰**
- 按拥挤度归一化概率淘汰，被淘汰节点**存档**（`data/archive/`），不物理删除

### 4. 描述分化（生态位画像）

每个任务完成后，节点智能体用 LLM 微调自我描述（≤500 字），吸收本次任务经验与新技能。描述用于：
- 向量路由（任务匹配）
- 拥挤度计算（生态位竞争）

### 5. 技能基因（skill 遗传）

节点的"技能"是 hermes profile 下的 skill 文件。繁衍时通过 `HermesInstance.list_skills()` 读取亲代技能，子代继承并集。默认技能 `anysearch`（联网搜索）所有节点出生自带。

### 6. kill+resume 修正

节点执行失败时，在**同一 session** 上 `--resume` 继续修正（而非重开新会话），修正上限 3 次。

### 7. 上下文压缩

对话 token 超过 `ctx_window × trigger_ratio`（32768 × 67%）时触发压缩，保留 system + 尾部精华 + 中部粗略摘要，压缩到 25% 预算。

### 8. 自愈机制

节点执行 hermes 前自动调用 `ensure_profile()`：profile 缺失（首次初始化/迁移后）时自动 `create + 配置模型 + 装 anysearch`，幂等、无需人工干预。

---

## 四、目录结构

```
ADGS/
├── main.py                  # CLI 入口（交互对话 + 快捷指令）
├── requirements.txt         # Python 依赖
├── start.bat                # 一键启动（双击即跑，无环境自动先装）
├── setup_new_pc.bat/.ps1    # 一键安装（Python + hermes + 依赖 + 模型）
├── 迁移手册.md              # 部署/迁移文档
├── config/
│   ├── config.py            # 全局配置单例（读 AI_config.json + settings.yaml）
│   ├── prompts.py           # CEO 系统提示词 + 工具 schema
│   ├── settings.yaml        # 种群与进化参数
│   └── AI_config.json       # 模型配置（ceo / brain / worker 的 url/model/key）
├── core/
│   ├── ceo.py               # CEO 智能体（对话循环 + 任务分发 + read_file/list_files）
│   ├── node.py              # NODE 闭环（决策 → 执行 → 验收 → 状态更新）
│   ├── agent.py             # 节点智能体 Leader（答题卡决策 + 验收打分 + 描述微调）
│   ├── hermes.py            # hermes 实例封装（profile 生命周期 + execute/resume + ensure_profile）
│   ├── evolution.py         # 达尔文进化引擎（压力/拥挤度/繁衍/淘汰）
│   ├── registry.py          # 节点注册表（活动池 + 存档池，JSON 持久化）
│   ├── router.py            # 任务路由（向量 × 成功率 → top-k → 温度采样）
│   └── compress.py          # 上下文压缩
├── utils/
│   ├── chat.py              # OpenAI SDK 封装（答题卡模式 call_tools）
│   ├── embedding.py         # 向量化（bge-small-zh-v1.5，离线优先）
│   └── hermes.py            # ⚠️ 遗留旧版封装（当前代码使用 core/hermes.py，此文件未使用）
├── skills/
│   └── anysearch/           # 联网搜索技能（节点出生自带）
├── tests/                   # 测试与运维脚本
│   ├── ensure_node_profiles.py   # 批量补建缺失的 hermes profile
│   ├── install_anysearch.py      # 批量安装 anysearch 技能
│   └── ...（进化模拟 / 生态位竞争等自测）
├── data/                    # 运行时数据（自动生成）
│   ├── registry.json        # 节点种群状态
│   ├── archive/             # 淘汰节点存档
│   ├── sessions/            # 对话存档
│   └── workspace.json       # 工作区路径
├── .hermes/                 # hermes 数据根目录（HERMES_HOME 已重定向到项目内）
│   └── profiles/ags_N/      #   每节点 skills(技能基因) + memories(记忆) + sessions
└── models/                  # embedding 模型缓存（bge-small-zh-v1.5，~92MB）
```

---

## 五、环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（hermes 数据经 `HERMES_HOME` 重定向到项目内 `.hermes/`，代码仍装在 `AppData\Local\hermes`） |
| Python | 3.11 ~ 3.13（推荐 3.13） |
| **Hermes Agent CLI** | 必须（节点执行器）；官方一键安装 `irm https://res1.hermesagent.org.cn/install.ps1 \| iex` |
| 网络 | 外网（DeepSeek API / anysearch）+ 可达 vLLM 服务器 `8.138.131.119:9000` |
| GPU | 不需要（embedding 用 CPU 版 torch 即可，LLM 推理全在远程 API） |
| 磁盘 | 项目 ~100MB + venv(torch CPU) ~1.5GB + hermes 本体 ~1GB |

Python 依赖（`requirements.txt`）：
- `openai>=1.0,<3`（注意 `<3`：v3 大版本 tool_call 行为与已验证的 2.x 有差异）
- `pyyaml>=6.0`
- `numpy>=1.24`
- `sentence-transformers>=2.2`（自动拉 torch）

---

## 六、安装部署

### 方式 A：一键安装（推荐）

双击 `setup_new_pc.bat`（或 PowerShell 运行 `setup_new_pc.ps1`），自动完成 6 步：
1. 检查/自动安装 Python 3.13
2. 安装 Hermes Agent CLI
3. python 加入用户 PATH（hermes 节点跑 anysearch 需要）
4. 创建 `.venv` 并安装依赖（默认 CPU 版 torch，省 ~2GB）
5. 检查 embedding 模型缓存（离线验证；缺失则首次运行自动下载）
6. 校验 hermes 命令 + 三个模型 API 端点连通性

### 方式 B：手动安装

```bash
# 1. 安装 hermes（PowerShell）
irm https://res1.hermesagent.org.cn/install.ps1 | iex

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖（无 NVIDIA 显卡先装 CPU 版 torch）
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

### 配置模型

编辑 `config/AI_config.json`，填入真实模型配置：
```json
{
  "ceo":    {"url": "https://api.deepseek.com/v1", "model": "deepseek-v4-flash", "key": "<DeepSeek Key>"},
  "brain":  {"url": "http://8.138.131.119:9000/v1", "model": "qwen3.6-27b", "key": "<vLLM Key>"},
  "worker": {"url": "http://8.138.131.119:9000/v1", "model": "qwen3.6-27b", "key": "<vLLM Key>"}
}
```

也可以在 web3 前端「系统概览 → 模型卡片右上角」打开系统设置实时修改（2026-09-27 模型 /
2026-09-28 运行与调优），三个标签页：

- **模型**：CEO 立即热生效；决策大脑对下一任务生效；hermes 执行模型只对之后新建的
  节点生效——已存在的节点钉住出生时的模型继续使用（节点抽屉可见各自执行模型）。
- **运行**：全局并行上限（信号量热调整，立即生效、不中断执行中任务）、递归委派
  深度、任务/无活动超时。
- **调优**：生存压力 τ（每轮淘汰力度）、任务分配权重（专精 sim_weight vs 得分
  score_weight，谁大听谁的）、探索系数 explore_beta（0=高分节点垄断任务，调高=
  给冷启动节点更多机会）、路由候选 top_k、采样温度。

模型改动写回 `config/AI_config.json`，运行/调优改动写回 `config/settings_overrides.json`
（白名单键覆盖 settings.yaml，两文件均已 gitignore，重启保留）。

---

## 七、快速开始

双击 `start.bat`，或：
```bash
.venv\Scripts\python main.py
```

### Web 前端（对话 + 实时图谱 + 节点详情）

双击 `start_web.bat`（或 `.venv\Scripts\python web_app.py`），浏览器打开 **http://127.0.0.1:8021**：

- **对话**：底部输入任务 → CEO 决策过程实时流式显示（工具调用 / 汇报 / 最终完成）
- **图谱**：Canvas 动画展示 CEO → 任务 → 节点 → 子节点（委派/临时克隆）动态生长，节点颜色标识执行状态
- **节点详情**：点击图谱节点，右侧面板双栏实时显示「智能体 Brain 决策/验收」与「hermes 执行器输出」
- **进化**：启动时自动触发一轮进化；也可点侧栏「进化一轮」按钮手动触发

技术栈：Flask + SSE（零构建链），事件埋点在 `core/`（ceo/node/hermes/evolution）经 `web/event_bus.py` 推送；CLI 模式零影响。

首次运行会初始化 10 个节点（自动创建对应 hermes profile + 装 anysearch）。之后每次启动会先触发一轮进化（繁衍 5 个 / 淘汰若干），然后进入对话：

```
> 帮我写一个 Flask 后端接口
[CEO 处理中...]
...
```

### 快捷指令

| 指令 | 说明 |
|---|---|
| `/workspace <path>` | 切换工作区（节点在此目录内工作） |
| `/debug <0-3>` | 调试级别（0=关闭 1=工具概要 2=+耗时 3=+完整模型回复+节点 hermes 执行输出实时显示） |
| `/save [name]` | 保存当前对话 |
| `/load <name>` | 恢复对话 |
| `/sessions` | 列出已保存会话 |
| `/history` | 查看当前对话历史 |
| `/help` | 帮助 |
| `/quit` | 退出 |

> 支持多行粘贴：直接粘贴一段多行任务描述，会作为整体发给 CEO，不会被拆成多行命令。

---

## 八、配置说明

### `config/settings.yaml` — 种群与进化参数

| 分组 | 参数 | 默认 | 说明 |
|---|---|---|---|
| population | initial_size | 10 | 初始节点数 |
| | capacity | 50 | 承载量 K |
| | reproduction_rate | 5 | 每轮繁衍数 R |
| | whiteboard_ratio | 0.30 | 白板占繁衍比例 |
| | recovery_coef | 0.5 | 淘汰回复系数 |
| pressure | tau | 8 | S 型曲线陡峭度 |
| crowding | sigma | 0.15 | 拥挤度高斯核带宽 |
| | sim_threshold | 0.6 | 只计相似度 > 阈值的邻居 |
| router | top_k | 5 | 综合得分前 k |
| | temperature | 0.3 | softmax 温度 |
| agent | correction_limit | 3 | 任务修正上限 |
| | rework_limit | 3 | 验收返工上限（验收不通过时同 session 继续修正） |
| | max_workers | 3 | 并行派发线程数 |
| hermes | worker_timeout | 1800 | 总墙钟超时(秒) |
| | inactivity_timeout | 600 | 无活动超时(秒) |
| | long_task_timeout | 7200 | 长任务超时(秒) |
| scoring | ema_alpha | 0.3 | success_rate 平滑系数 |
| | describe_max_len | 500 | 节点描述最大字符数 |
| compress | ctx_window | 32768 | 上下文窗口 |
| | trigger_ratio | 0.67 | 触发压缩阈值 |
| | budget_ratio | 0.25 | 压缩后保留预算 |

### `config/AI_config.json` — 模型配置

- `ceo`：CEO 模型（DeepSeek，强推理）
- `brain`：节点智能体 Leader 模型（vLLM）
- `worker`：hermes 执行器模型（vLLM）

---

## 九、关键设计要点与踩坑

1. **CEO 用 DeepSeek 必须禁用 thinking**：`extra_body={"thinking":{"type":"disabled"}}`。否则 Thinking 模式会拒绝 `tool_choice`，只回纯文本。
2. **qwen 系 vLLM 需 `enable_thinking=False`**（`chat_template_kwargs`），否则 `content=None`。
3. **答题卡模式**：所有决策强制 `tool_choice="required"`，参数即结构，避免解析自然语言。
4. **技能固化前置强命令**：节点任务书开头强制要求 hermes 完成后用 `skill_manage` 固化复杂经验。
5. **并行任务 report 隔离**：report 按任务名/时间戳写独立文件，避免多节点并行写同一 report.md 互相覆盖。
6. **空节点拥挤度 = 均值**：描述未分化的空节点不参与生态位竞争，防止"描述撞车"被误淘汰。
7. **embedding 离线优先**：本地有模型缓存就 `HF_HUB_OFFLINE=1` 离线加载，避免联网 HEAD 检查被墙卡死；无缓存走 hf-mirror 镜像下载。
8. **改代码后清 `__pycache__`**：防旧 `.pyc` 缓存导致改动不生效。
9. **Windows subprocess 调 hermes**：`shell=False` + 列表参数（cmd.exe 不认单引号）。
10. **profile 自愈**：`ensure_profile()` 保证节点执行前 profile 必存在（幂等）。

---

## 十、迁移部署（打包到其他电脑）

完整便携包见 `迁移手册.md`。要点：

- **必须**：安装 Python + hermes CLI + 依赖（`setup_new_pc.bat` 一键完成）
- **模型**：`models/`（92MB）随包分发，解压即离线可用，无需联网下载
- **key 脱敏**：分发前 `config/AI_config.json` 里的 API key 换成占位符，接收方运行前填回
- **不带**：`.venv/`（新机重建）、`data/`（新机从 10 个初始节点重新进化）
- **hermes profiles**：节点 profile 在项目内 `.hermes/profiles/ags_N`（代码通过 `HERMES_HOME` 环境变量把 hermes 数据根目录重定向到项目内，所有 profile/技能/记忆/会话都随项目一起迁移）。新机首次初始化会自动创建（新版已修复），也可跑 `tests/ensure_node_profiles.py` 批量补建；从旧版迁移可跑 `tests/migrate_hermes_home.py` 一次性搬移旧 profile

---

## 十一、测试

```bash
# 模块自测
.venv\Scripts\python core/evolution.py     # 繁衍/压力/淘汰/拥挤度
.venv\Scripts\python core/router.py        # 路由偏向
.venv\Scripts\python utils/embedding.py    # 向量相似度

# 补建缺失 profile
.venv\Scripts\python tests/ensure_node_profiles.py

# 批量安装 anysearch 技能
.venv\Scripts\python tests/install_anysearch.py
```

---

## 十二、常见问题

1. **报 `Profile 'ags_N' does not exist`** → 新版代码节点执行时会自动补建；或跑 `tests/ensure_node_profiles.py`。
2. **向量化失败 / 拥挤度回退 0** → 模型缓存缺失且联网失败；从源电脑拷 `models/` 目录，或确认能连 hf-mirror。
3. **CEO 报 `单轮决策上限`** → 单轮内部决策达到 16 次上限（读文件 + 拆多批任务），换个说法继续即可。
4. **CEO 读不了文件** → 新版 CEO 有 `read_file`（读文件）和 `list_files`（列目录）工具；旧进程需重启加载。
5. **hermes 执行超时** → 大任务放宽 `hermes.long_task_timeout`，或检查 vLLM 端点连通。

## 十三、持久执行与 Web3 实时终端

Windows 执行器使用 ConPTY，调用真实安装的 Hermes CLI，并显式启用 `--yolo`。
一个 AGS 对应一个持久 profile；不同 AGS 的技能、记忆和历史独立。同一个 profile
可以并行运行不同 session，恢复同一个 session 时只允许一个执行者。

Web 入口只负责静态页面与 API 代理；CEO、节点调度和终端进程属于独立本机 runtime。
关闭或重启 Web 入口不会终止已运行的任务。Runtime 状态、执行检查点、事件游标和
原始终端内容保存在 `.adgs_runtime/`，不写入其他 AGS 的 Hermes 配置。
`ADGS_STATE_DIR` 可指定独立的运行状态目录。

web3 推荐使用 `start_web3.bat`：复用已运行的后端，并在启动窗口持续显示委派、汇报和验收摘要。
打开时回放最近 20 条记录；没有新汇报时，每 30 秒显示活动执行状态。
后台已运行、只想打开日志窗口时，双击 `start_adgs_logs.bat`，或运行
`.venv\Scripts\python.exe tools/adgs_console.py`。日志查看器只读共享记录，关闭它会保留后台任务。

双击 `start_web.bat` 后访问 `http://127.0.0.1:8021/web3/`。新执行实例的详情包括
实时终端、执行事件、逐项验收和节点档案；终端支持搜索、复制、字号、跟随及下载。
旧历史记录缺少验收证据时显示待核实，不猜测为失败。执行 ID 与每次尝试的 attempt ID
独立，浏览器断线后按持久游标补收内容。

任务按依赖就绪调度。声明 `write_paths` 的不相交修改和只读任务可并行；相交写路径
等待正在修改的任务结束。无法明确写路径的修改保守串行，不按 AGS profile 串行。
汇报格式问题先补合法结论；环境中断保留成果；续接优先恢复原执行及未完成子任务。
评分按执行去重，续接通过可修订原评价，任务次数不重复增加。

已有旧版 Web 进程需在空闲时重启一次以加载新架构；此后只重启 Web 即可重新连接
后台。终端输出用于观察，完成状态仍由原始任务合同的验收决定。

隔离验证命令（不调用业务模型）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_execution_reliability.py tests/test_ceo_runtime_reliability.py tests/test_terminal_runtime.py tests/test_runtime_host.py -q
cd web3
npm.cmd test -- --run
npm.cmd run build
npm.cmd exec playwright test -- --config=playwright.execution.config.ts --reporter=list
```

终端和后台生存测试使用临时 profile、fake Hermes 或隔离 Flask fixture；真实安装
Hermes 的启动参数通过隔离 `--help` 检查。验证不代表已执行一次真实模型工程任务。
