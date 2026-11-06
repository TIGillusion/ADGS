# ADGS 项目介绍（智能体达尔文进化系统）

> 本文档由对 D:\user_illusion\test\ADGS 的只读代码探查生成：所有论断都标注了真实文件路径与类/函数名，供非作者读者对照源码核实（行号为探查时快照，代码演进后可能偏移）。

---

## 一、项目定位与目标

ADGS（Adaptive Darwinian Group System，v3）是一个由多个 AI 智能体节点组成的「种群」系统：节点像生物个体一样经历**繁衍、基因重组、生态位竞争与淘汰**，在真实任务中优胜劣汰；一个 CEO 智能体作为唯一入口与用户对话，负责澄清意图、拆解任务、路由派发与结果汇报。（`README.md` 开篇；设计原始思路见 `设计.txt` 1-7 行）

一句话概括运行方式：**用户只和 CEO 说话；CEO 把任务拆成 markdown「任务书」派给节点；节点里的「智能体」负责决策与验收、节点里的「hermes 执行器」真正干活；干得好的节点得分并繁衍后代，干得差的被淘汰存档。**

代码入口（项目根）：
- CLI 交互入口 `main.py`：首次运行初始化 10 个初始节点，此后每轮新对话先触发一轮进化再进入 CEO 对话循环（`main.py:208` `main()`；`main.py:214-222` 初始池/进化触发；初始统一描述 `main.py:35` `INIT_DESCRIBE`）。
- Web 入口 `web_app.py`：Flask，端口 8021（`web_app.py:31` `PORT`），提供对话、SSE 实时事件流、节点详情、进化按钮与三套前端。
- 一键启动/安装：`start.bat` / `start_web.bat` / `start_web2.bat` / `start_web3.bat` / `setup_new_pc.bat|.ps1`；部署迁移见 `迁移手册.md`。

依赖极简（`requirements.txt`）：openai(<3)、pyyaml、numpy、sentence-transformers、flask。embedding 模型本地缓存于 `models/`（bge-small-zh-v1.5，约 92MB），LLM 推理全部走远程 API（CEO 走 DeepSeek 官方，节点侧走自建 vLLM）。

---

## 二、三层架构：CEO / 节点智能体 / hermes 执行器

系统是清晰的三层结构（架构图与术语表见 `README.md` 第二节；代码分别在 `core/ceo.py`、`core/node.py`、`core/agent.py`、`core/hermes.py`）：

```
用户 ──对话──> CEO（core/ceo.py，唯一入口，deepseek-v4-flash）
                │ split_tasks / single_task 产出 markdown 任务书
                ▼
        任务路由（core/router.py：向量相似度 × 成功率 → top-k → 温度采样）
                ▼
节点 NODE（core/node.py）= 节点智能体 AGENT（core/agent.py）
                        + hermes 执行器 HermesInstance（core/hermes.py，独立 profile）
```

### 2.1 CEO（唯一用户入口）—— core/ceo.py

- CEO 只决策不干活：系统提示词明确「你绝对不能自己写代码、自己生成产物……一律交给节点」（`config/prompts.py:7` `CEO_SYSTEM_PROMPT`）。
- CEO 的答题卡工具共 7 个：`ask_user` / `split_tasks` / `single_task` / `read_file` / `list_files` / `report` / `finish`（`config/prompts.py:206` `CEO_TOOLS`）。
- 主循环 `CEO.talk()` → `_talk_body()`（`core/ceo.py:269`）：用户消息入史后循环「强制工具调用 → 执行对应工具 → 结果回填历史」，直到 `finish` 工具（`core/ceo.py:430-440`，唯一终态）。
- 派发：`_dispatch_step()`（`core/ceo.py:667`）→ `_dispatch_parallel()`（`core/ceo.py:724`）按依赖 stage 并行派发；同节点第二个任务起走临时克隆（`core/ceo.py:794-799`）。
- 防失控设计：动态循环上限 `_dynamic_limit()`（`core/ceo.py:467`，100/80/60/40 随压缩次数与循环状态收紧）；回复循环检测 `_detect_loop()`（`core/ceo.py:482`，工具指纹归一化）。
- 探索纪律：`read_file` 只读 ≤3000 字符小文件（>1MB 拒读）、`list_files` 禁递归，累计超 5 次后拒绝执行，强制把消耗大的探索委派给节点（`core/ceo.py:369-384`；`CEO_SYSTEM_PROMPT`「探索纪律」「验收外包」两节）。
- 多会话上下文：`_histories`/`_logs` 按会话名隔离，`talk()` 期间锁定会话（`core/ceo.py:58-67`、`core/ceo.py:262`）；保存/恢复走 `data/sessions/<name>.json`（`save_conversation`/`load_conversation`，`core/ceo.py:134/165`，原子写 `core/ceo.py:28`）。

### 2.2 节点 NODE（智能体 + hermes 组合体）—— core/node.py

- `NODE` 类（`core/node.py:55`）持有 `self.agent = AGENT(...)` 与 `self.hermes = HermesInstance(...)`（`core/node.py:65-67`）。
- 任务闭环 `NODE.work()` → `_work_inner()`（`core/node.py:96` / `core/node.py:123`）：接任务书 → 智能体决策 → hermes 执行 → 验收打分 → EMA 更新与描述分化 → 上报；闭环内任何异常都补发终态事件，防止前端实例卡在瞬态（`core/node.py:109-121`）。
- kill+resume 修正：hermes 执行失败时在同一 session 上 `--resume` 继续修正而非重开会话，上限 `correction_limit=3`（`core/node.py:210-235`）。
- 验收返工：不通过时 `_rework_instruction()` 生成补充要求、同 session resume 保留上下文继续，上限 `rework_limit=3`；返工 ≥2 次时指令强制「方向纠偏」（重新列出根因候选方向、验证后可推翻重来，`core/node.py:406-417`）。
- 递归委派 `_delegate()`（`core/node.py:615`）：节点智能体 `split_tasks` 后把子任务经 `route()` 路由给下层节点执行（`core/node.py:654`）；依赖按 Kahn 拓扑分组（`resolve_stages()`，`core/router.py:91`），组内并行、组间串行，前驱结果注入依赖方（`_depends_context`，`core/node.py:515`）。
- 深度到顶（`max_delegation_depth=5`，`config/settings.yaml:63`）时合并子任务强制自己执行（`_merge_subtasks`，`core/node.py:479`）。
- 自我委派与并行：路由选中自己、或同节点被分配多任务时，用 `NODE.spawn_parallel()`（`core/node.py:788`）创建临时克隆 profile（`HermesInstance.create_temp_clone`，`core/hermes.py:763`）执行——上下文全新、分数归本体且无双计（`record_score` 语义，`core/node.py:104-107`、`core/node.py:796-800`）。
- 委派节点同样进化：`_evaluate_delegation_and_update()`（`core/node.py:599`）按子任务表现给委派方打分并分化描述。
- 子节点提问的澄清链路：`_resolve_child_clarify()`（`core/node.py:548`）——子节点 `ask_upper` 提问 → 委派方用 brain 回答（`_answer_child`，`core/node.py:530`）→ 补充说明拼进任务书重派同一子节点（最多 2 次）。
- 全局并发信号量 `_EXEC_SEM`（`core/node.py:37`）把同时运行的 hermes 进程限制在 `max_workers_effective`（=min(max_workers, vllm_concurrency)，`config/config.py:133-134`），跨所有线程池/嵌套委派层共享。

### 2.3 hermes 执行器 —— core/hermes.py

- `HermesInstance`（`core/hermes.py:215`）绑定一个 hermes profile（=节点 id，如 ags_N）。核心方法：
  - `execute()`（`core/hermes.py:352`）：子进程跑 `hermes --yolo --profile ags_N chat -Q -q <任务书>`；二进制 `read1` 流式读输出（防流式无换行导致误判无活动）；live.log 双信号活动检测；双超时（总墙钟 + 无活动）。
  - `resume()`（`core/hermes.py:566`）：`--resume <session_id>` 在同 session 继续（kill+resume 的底层）。
  - `list_skills()` / `get_skill()` / `install_skill()`（`core/hermes.py:256` / `core/hermes.py:326` / `core/hermes.py:303`）：读写 profile `skills/` 下的 SKILL.md —— 这是「技能基因」的物理载体。
  - `create()` / `setup_newborn()`（`core/hermes.py:680` / `core/hermes.py:693`）：创建新 profile、配置模型、禁用 clarify（无人值守）、显式声明上下文长度。
- `HERMES_HOME` 重定向到项目内 `.hermes/`（`core/hermes.py:82-87`）：所有节点 profile、技能、记忆、会话都在项目内，随项目一起迁移（`README.md` 第十节）。
- profile 自愈：`ensure_profile()`（`core/hermes.py:798`）在节点执行前（`NODE._ensure_live_log`，`core/node.py:325-333`）幂等补建缺失 profile，并对齐模型配置（`HermesInstance.ensure_model`，`core/hermes.py:739`）。

---

## 三、核心机制

### 3.1 答题卡模式（强制工具调用）

所有 LLM 决策都强制 `tool_choice="required"`，模型必须返回结构化工具参数，不解析自然语言（`README.md` 第三节 1；实现 `utils/chat.py:78` `CHAT.call_tools`）。

- 防抖链：required → 出错降级 auto → 仍失败省略 tool_choice；required 模式未返回工具调用时追加提示重试（`utils/chat.py:88-157`）。
- CEO 侧 7 个工具 schema：`config/prompts.py`（`CEO_TOOL_*`）；节点智能体侧 3 个工具 schema：`core/agent.py:21-94`（`TOOL_ASK_UPPER` / `TOOL_PLAN_HERMES` / `TOOL_SPLIT_TASKS`，汇总 `core/agent.py:96` `ALL_TOOLS`）。
- 验收也走答题卡：`AGENT.evaluate()` 强制 `accept_report` 工具（`core/agent.py:328-396`）；LLM 故障时返回旧分而非 0 分（`core/agent.py:379-382` 注释：防把几十轮积累的分数清零导致必被淘汰）。
- 模型侧 thinking 必须禁用（DeepSeek `thinking.type=disabled`、qwen 系 `enable_thinking=False`），否则拒绝 tool_choice 或 content=None（`utils/chat.py:65-74`；`README.md` 九.1-2）。

### 3.2 任务路由：向量相似度 × 成功率 → top-k → 温度采样

代码在 `core/router.py`（与 README 第二节一致）：

```
score_i = cosine(task_vec, describe_vec_i) × success_rate_i    # score_nodes(), core/router.py:16
top-k（默认 5）按 score 排序                                  # top_k_nodes(), core/router.py:37
softmax(score/T) 温度采样选 1 个节点（数值稳定版）            # temperature_select(), core/router.py:50
```

- 向量来自 `utils/embedding.py`（本地 bge-small-zh-v1.5）；任务向量带检索前缀（`utils/embedding.py:24` `QUERY_PREFIX`）。
- 成功率无历史时取中性 0.5（`core/router.py:32`）。
- 路由入口 `route()`（`core/router.py:75`）打印 top-k 明细；在 CEO 派发（`core/ceo.py:760`）与节点递归委派（`core/node.py:654`）两处被调用。
- 依赖子任务分组：`resolve_stages()`（`core/router.py:91`，Kahn 拓扑排序；越界/成环依赖被过滤到末 stage）。

### 3.3 动态逐步拆解

- CEO 与节点智能体的系统提示词都强调「只规划【下一步】，不是一次全部拆完」（`config/prompts.py:24`；`core/agent.py:169`）。
- CEO 拆解后要等节点结果回填历史再决定下一步（`CEO_SYSTEM_PROMPT`「工作循环」节；`core/ceo.py:297` while 循环）。
- 节点智能体 `AGENT.decide()`（`core/agent.py:284`）一次传入 3 个工具让模型自主选择：信息不足 → `ask_upper`；可一次完成 → `plan_hermes`；过于复杂 → `split_tasks`（`core/agent.py:292-324`）。
- 拆分标准（`core/agent.py:174-183` 决策规则节）：≥3 个独立模块/文件/函数、需并行探索多方向、单次会话预计 >15 工具调用、验收标准 ≥5 条且独立可并行；「并行优先」——能并行一次拆出，仅存在先后依赖才用 `depends_on`（0 基索引）。

### 3.4 任务书传递与权限告知

任务书是层与层之间唯一的交接物：markdown 格式、详细无歧义、含权限告知（`设计.txt:17`）。渲染与组装位置：

1. CEO 派发的任务书：`render_task_md()`（`config/prompts.py:216`）——固定小节：总体任务 / 背景 / 当前任务 / 工作区 / 需求明细 / 验收标准 / 权限告知 / 交付物 / 输出要求 / 技能固化。权限默认「只允许修改工作区内文件，禁止修改其他目录」（`config/prompts.py:236`）。
2. 报告路径按子任务名隔离（`report_<任务名>.md`）：CEO 派发时在 `core/ceo.py:750` 生成 `report_{_safe_name(...)}.md` 传入任务书渲染；节点递归委派时同理（`core/node.py:504`），避免多节点并行写同一 report.md 互相覆盖（`README.md` 九.5）。
3. 节点智能体给 hermes 的任务书：`AGENT._render_task_md()`（`core/agent.py:251-280`）——总体目标 / 执行步骤 / 验收标准 / 权限告知 / 交付物 / 技能固化，字段即 `TOOL_PLAN_HERMES` schema（goal/steps/acceptance/permissions/deliverables/skill_note，`core/agent.py:38-64`）。
4. 代码层强命令前置：`NODE._work_inner` 把「技能固化强命令」拼在任务书最开头，确保 hermes 第一眼看到明确命令（`core/node.py:191-196`；`README.md` 九.4）。
5. 下层没理解任务可以提问：`ask_upper` 要求「标注自己的身份和收到的任务要点」（`core/agent.py:25` 工具描述；`设计.txt:19`）；上层回答后补充说明追加进任务书再重派（`core/ceo.py:862`；`core/node.py:562`）。

**上下文全新但记忆/技能保留**：节点接任务时 brain 对话史清空（`AGENT.reset_history`，`core/agent.py:195`，在 `core/node.py:143` 每次 work 前调用）；跨任务不保留（`core/agent.py:151-153` 注释）。但长期记忆和技能存在 hermes profile 里，不随任务结束丢失（`设计.txt:18`；profile 目录结构见 `README.md` 第四节 `.hermes/profiles/ags_N/`）。

### 3.5 报告与验收闭环

- hermes 完成后，节点智能体阅读执行日志（工具调用 + 文本输出，不含工具返回结果）做验收：`AGENT.evaluate()`（`core/agent.py:328`）强制 `accept_report` 工具，输出 passed / score(-10~+10) / passed_items / failed_items。
- 打分回流：`score = old + clamp(打分, ±10) × delta_coef(0.2)`，单轮波动 ±2、下限 0 无上限（`core/agent.py:390-394`；`config/settings.yaml:79-83`）。
- 验收不通过 → 返工循环（`core/node.py:251-283`）；执行失败 → kill+resume 修正（`core/node.py:210-235`）。
- 成绩更新：`NODE._update_node()`（`core/node.py:746`）成功率 EMA + 累计分 + 任务数，写回注册表（`Registry.update`，`core/registry.py:193`，每次落盘）。
- 汇报链：子节点结果经 `NODE.work` 返回值逐层上抛（`upper` 参数记录上报对象）；CEO 收齐结果后 `report`（进度，不结束）或 `finish`（终态，`core/ceo.py:430-440`）向用户汇报。hermes 侧同时把执行报告写入工作区 `report_<task>.md`（`config/prompts.py:260-262`「输出要求」节）。

### 3.6 成功率 EMA 与温度采样

- 成功率 EMA：`success_rate = (1-α)×旧 + α×(本次得分/100)`，α=`ema_alpha=0.3`（`core/node.py:752-754`；`config/settings.yaml:77`；`config/config.py:145` 加载）。
- 路由温度采样：T=`temperature=0.3`（`config/settings.yaml:50`；`core/router.py:54`）。T 低=偏向最匹配节点，T 高=探索。
- 进化侧的承载量 EMA：`effective_capacity()` 用 0.3×新值+0.7×历史 平滑动态承载量（`core/evolution.py:118-122`）。

---

## 四、进化性专章：达尔文进化引擎如何工作

进化引擎全部在 `core/evolution.py`（模块 docstring 即设计原文摘要，`core/evolution.py:1-14`）。入口 `evolve()`（`core/evolution.py:307`），时序：**先繁衍、后淘汰**，淘汰数按繁衍前种群规模 n 计算（`core/evolution.py:402` 注释）。触发时机：CLI 每轮新对话开始（`main.py:219`）；Web 启动后台触发（`web_app.py` `_startup_evolve`）+ 手动 `/api/evolve` 端点；空池自愈（`core/evolution.py:319-329`）。

### 4.1 力量一：数量压力（S 型生存曲线）

`base_pressure()`（`core/evolution.py:26-30`）：

```
P_base(n) = 1 / (1 + e^((K - n) / τ))     K=50 承载量, τ=8
```

- K=`capacity`、τ=`tau`（`config/settings.yaml:7/14`；`config/config.py:95/101` 加载）。
- n 越接近/超过承载量，压力越接近 1，每个节点的基础淘汰概率越大（`设计.txt:15` 称之为「对整体节点数量的限制的力量」）。
- 落到淘汰数量：`elimination_count()`（`core/evolution.py:33-43`）：`E = round(R + (n - K) × coef)`，R=`reproduction_rate=5`、coef=`recovery_coef=0.5`，clamp 到 [0, n]。

### 4.2 力量二：种类竞争（生态位拥挤度）

`crowding_density()`（`utils/embedding.py:89-114`）+ `crowding_scores()`（`core/evolution.py:67-91`）：

```
density_i = Σ_j exp(-(1 - cos(v_i, v_j)) / σ²)     仅计入相似度 > 阈值的邻居
```

- σ=`crowding.sigma=0.15`、阈值 `sim_threshold=0.6`（`config/settings.yaml:17-19`）。技能生态位重叠越多 → 密度越高 → 淘汰概率越大（生态位压制）。
- 空节点保护：描述仍是初始统一文本的节点（`_is_blank_node()`，`core/evolution.py:48-64`）不参与拥挤竞争，压力强制取全体均值——防止「描述撞车」的无经验节点被误淘汰（`README.md` 九.6 同）。
- 拥挤度计算失败时回退 0（`core/evolution.py:89-91`），embedding 不可用不阻塞进化。

### 4.3 淘汰概率合成（含力量三、力量四）

个体淘汰概率 `elimination_probabilities()`（`core/evolution.py:139-174`）：

```
weight_i = (P_base + penalty × density_i) × 5^(−w × S_i / 10)
S_i      = score_i × (1 − penalty × density_i)        # 综合得分 _fitness_scores(), core/evolution.py:179
```

- 力量一 + 力量二相加（penalty=`crowding.penalty=0.5`），再乘「力量三」分数衰减：综合得分每高 10 分，淘汰概率 ÷5（`core/evolution.py:149-154` 注释；权重 `elimination.score_weight=1.0`，`config/settings.yaml:25-32`）。
- 「力量四」动态承载量：`effective_capacity()`（`core/evolution.py:94-122`）：`K_eff = K × (1 + slope × ln(avg_score / K))`，EMA 平滑、clamp [50, 200]（`config/settings.yaml:40-45`，`enabled=true`）——种群平均分随训练上升，承载量对数扩张、渐近饱和。
- 新节点动态初始分：`percentile_score()`（`core/evolution.py:125-136`）取种群 score 第 60 百分位（`newborn_score_percentile=0.60`），不拖累平均分也不会被秒淘汰。
- 亲代选择权重 = 5^(S/20)：综合得分每高 20 分繁衍概率 ×5，比淘汰侧温和，避免纯精英化垄断多样性（`_pick_parents()`，`core/evolution.py:192-212`）。
- 权重归一化为概率；全部权重 ≈0 时退回均匀分布（`core/evolution.py:170-174`）。

### 4.4 繁衍：基因重组 70% + 白板 30%

每轮固定繁衍 R=5 个。`plan_newborns()`（`core/evolution.py:242-271`）逐个决定：以 `whiteboard_ratio=0.30` 概率为白板，否则基因重组（`config/settings.yaml:9`）。

- **基因重组（70%）**：`_pick_parents()` 按 5^(S/20) 加权随机选 2 个亲代（允许同一节点但概率降 10 倍，`core/evolution.py:207-211`）→ `_skill_union()`（`core/evolution.py:215-239`）读两个亲代 profile 的技能并集（含 SKILL.md 全文，`HermesInstance.get_skill`，`core/hermes.py:326`）→ 抽取数量 = 两亲代技能数平均值（`core/evolution.py:262` `avg = max(1, int(round((len(skills1)+len(skills2))/2)))`）→ `rng.sample` 随机抽取（`core/evolution.py:264`）。
- **白板（30%）**：无技能、无记忆的全新节点（`core/evolution.py:254-256`）。
- 出生装配（`evolve()` 繁衍段，`core/evolution.py:346-400`）：创建 hermes profile（`HermesInstance.create`）→ `setup_newborn` 配模型 → 装默认技能 anysearch（含完整 API 文档，`core/evolution.py:381-384`）→ 安装继承的基因技能（跳过 anysearch 防简版覆盖完整版，`core/evolution.py:387-393`）。基因继承是**完整 SKILL.md 全文**，读不到全文才退化为简版模板（`_skill_union` 注释，`core/evolution.py:215-222`）。
- 基因=skill、无长期记忆：与设计一致（`设计.txt:23`「基因就是 skill，但是没有长期记忆」）——技能随亲代迁移，hermes profile 的 memories/ 不随繁衍迁移。
- 子代描述 = 亲代描述 +「继承了技能基因：…」摘要（`_recombine_describe()`，`core/evolution.py:441-458`，技能名去重保序、总长截断到 500 字）；白板子代回到初始统一文本（`_default_describe()`，`core/evolution.py:435-438`）。
- 代数记录：recombine 子代 gen = 亲代 gen + 1（`core/evolution.py:359`）；Node 的 gen/birth_type/parents 字段见 `core/registry.py:27-38`。

### 4.5 淘汰与存档（存活者保留、淘汰者存档不删）

- `select_eliminations()`（`core/evolution.py:276-302`）：按个体淘汰概率随机抽 E 个，去重、不足再按权重补抽。
- 淘汰**只存档不物理删除**：`Registry.archive_node()`（`core/registry.py:205-220`）把节点完整快照写入 `data/archive/<node_id>_archived_<时间戳>.json`、状态置 archived、移出活动池；hermes profile 与技能基因完整保留。
- 可复活：`Registry.revive_node()`（`core/registry.py:222-238`）archived → active，profile 未删故无需重建（Web 端点 `/api/node/<id>/revive`，`web_app.py` `api_node_revive`）。
- 本轮新生儿不参与当轮淘汰：防止 recombine 子代（描述继承亲代、拥挤度高）基因未表达即被清除（`core/evolution.py:402-407`）。
- 存档的二次用途：registry.json 损坏时从存档恢复（`_restore_from_archive`，`core/registry.py:113-143`）；工作区实际存在大量 `data/archive/*.json` 存档文件，是该机制运行的直接证据。
- 淘汰质量可见性：evolve 报告附 pop_avg_score / elim_avg_score / score_gap（弱者优先淘汰时为负 → 存活者平均分上升，`core/evolution.py:414-420`），并经 `evolve.round` 事件推给前端。

### 4.6 描述分化（个体描述如何随代际/任务差异）

- 时机：每个任务完成后（`NODE.evolve_describe()`，`core/node.py:763-782`，委派节点也走同一入口，`core/node.py:599-613`）。
- 方式：节点智能体（LLM）参考旧描述，吸收本次任务经验与当前技能库，生成 150~500 字新描述（`AGENT.update_describe()`，`core/agent.py:401-446`；输出净化 `_clean_describe()`，`core/agent.py:100-134`，失败保留旧描述）。
- 用途：向量路由（3.2）+ 拥挤度计算（4.2）——描述越分化，路由越精准、生态位越独特、被淘汰概率越小。
- 差异来源：初始全员同一描述（`main.py:35`）→ 任务后各自分化（`update_describe`）→ recombine 子代携带「亲代描述+技能摘要」（`_recombine_describe`）→ 白板回到统一文本。这是「描述随代际差异」的代码路径。

### 4.7 技能基因遗传

- 物理形态：hermes profile 下 `skills/<name>/SKILL.md`（`core/hermes.py:255-334` 一组读写方法）。
- `list_skills()` 排除内置 bundle（`.bundled_manifest` 名单），名取 frontmatter `name` 字段（`core/hermes.py:256-300`）；`install_skill()` 写 SKILL.md（`core/hermes.py:303-323`）。
- 繁衍时 `_skill_union()` 读亲代技能并集（含全文），`evolve()` 安装到子代（`core/evolution.py:387-393`）。默认技能 anysearch 所有节点出生自带（`skills/anysearch/SKILL.md` + `core/evolution.py:381-384` + `ensure_profile`，`core/hermes.py:823`）。
- 技能增长闭环：hermes 在任务中按任务书「技能固化」要求用 `skill_manage` 创建新技能（`core/node.py:191-196` 强命令）→ 新技能进入该节点技能库 → 下次繁衍即可遗传给子代。这是「节点可自我进化」的核心回路。
- Web 端可人工干预技能：`/api/node/<id>/skill/install`、`/api/node/<id>/skill/remove`（`web_app.py` `api_node_skill_install` / `api_node_skill_remove`）。

### 4.8 EMA 与温度采样（进化视角汇总）

- 成功率 EMA（路由得分因子）：`core/node.py:752-754`，α=0.3。
- 承载量 EMA（进化）：`core/evolution.py:118-122`，0.3/0.7 平滑。
- 温度采样（路由探索/收敛）：`core/router.py:50-72`，T=0.3。
- 打分累加（种群平均分上升的引擎）：-10~10 × 0.2（`core/agent.py:390-394`）→ 平均分上升 → 力量四承载量对数扩张 → 种群可随能力增长而扩大。

### 4.9 与 README/任务书描述的出入（以代码为准）

1. **力量数量**：README/任务书只提「两大力量」，代码实际是**四大力量**（基础压力/拥挤度/综合得分指数弱度/动态承载量）+ 百分位初始分（`core/evolution.py:141-156` 注释逐条标注「力量三」「力量四」）。以代码为准。
2. **节点侧模型名**：README 第二节写 qwen3.6-27b（`README.md:56`），而 `config/AI_config.json` 实配 **qwen3.8-27b**（brain/worker）。以配置文件为准；模型升级历史见 `core/hermes.py:744-747` 注释。
3. **节点工具清单**：README 第二节图列出 `declare_done` / `report_up`（`README.md:68`），但节点智能体当前决策工具只有 3 个（ask_upper / plan_hermes / split_tasks，`core/agent.py:96`）；`accept_report` 是验收时单独强制调用的工具（`core/agent.py:339-359`），并非常驻决策工具；`declare_done` / `report_up` 在现行代码中未实现。以代码为准。
4. **超时参数**：README 第八节参数表 worker_timeout=1800 / inactivity=600，而 `config/settings.yaml:71-72` 实为 3600 / 1800（注释：排队+生成需宽松超时）。以 `config/settings.yaml` 为准。
5. **进化触发时机**：README 第七节说「每次启动会先触发一轮进化」；CLI 侧实为「每轮新对话开始时」（`main.py:214-222`），Web 侧才是启动触发 + 手动端点。语义相近，表述以代码为准。
6. **初始节点数**：README 第七节「首次运行初始化 10 个节点」与代码一致（`initial_size=10`，`config/settings.yaml:6`；`registry.ensure_initial`，`core/registry.py:242`）。

---

## 五、主要特点与亮点

1. **动态逐步拆解**：CEO 与节点智能体都「只规划下一步」，拿到前一步结果再规划下一步（`config/prompts.py:24`；`core/agent.py:169`），比一次性拆解灵活。
2. **上下文全新，但长期记忆与技能保留**：每次接任务 brain 对话史清零（`core/node.py:143`），hermes profile 的技能/记忆持久存在（`core/hermes.py` profile 目录）——「同一个个体换一套全新记忆开工，但手艺还在」（`设计.txt:18`）。
3. **节点可互相/给自己传任务**：递归委派（`core/node.py:615`）+ 临时克隆（`core/node.py:788`，`core/hermes.py:763`）；路由选中自己即「自己给自己传任务，上下文全新」（`设计.txt:18`）。
4. **离线优先 embedding**：本地有 `models/` 缓存就 `HF_HUB_OFFLINE=1` 离线加载，无缓存走 hf-mirror 镜像；带 LRU 缓存（2048 条）与线程安全（`utils/embedding.py:26-48`）——离线可用、不联网 HEAD 卡死（`README.md` 九.7）。
5. **完整达尔文闭环且可追溯**：描述分化 → 向量拥挤度 → 淘汰概率 → 繁衍（重组 70%/白板 30%）→ 技能全文遗传；淘汰只存档不删除、支持复活（`core/registry.py:205/222`），`data/archive/` 下数百份存档是进化史的直接证据。
6. **答题卡模式贯穿全链**：CEO 决策、节点决策、节点验收全部强制工具调用，参数即结构（3.1 节）。
7. **kill+resume 修正与方向纠偏返工**：失败不重开会话，同 session resume 保留上下文（`core/node.py:210-235`）；返工 ≥2 次强制重新评估根因方向、允许推翻重来（`core/node.py:406-417`）。
8. **多会话隔离 + SSE 实时可视化**：CEO 多会话上下文按会话名隔离（`core/ceo.py:58-67`）；`web/event_bus.py` 纯标准库事件总线（无订阅者时 CLI 零开销，`web/event_bus.py:3-8`）+ `web_app.py` 的 `/api/events` SSE 流，推送 chat.* / graph.* / node.* / hermes.* / evolve.round 等事件（`web/event_bus.py:10-15` 事件类型清单）；三套前端并存（`web/`、`web2/`、`web3/`，`web_app.py:40-56`），前端含 Canvas 实时图谱（CEO→任务序号→节点→克隆动态生长，`README.md` 第七节）。
9. **并发与容错的工程细节**：全局信号量对齐 vLLM 并发（`core/node.py:37`）；注册表原子写（`core/registry.py:145-158`，临时文件+replace）；会话文件原子写+模块锁（`core/ceo.py:28-38`）；profile 自愈幂等（`ensure_profile`，`core/hermes.py:798`）；HERMES_HOME 重定向实现随项目迁移（`core/hermes.py:82-87`）；长任务关键词自动放宽超时（`NODE._compute_timeouts`，`core/node.py:310-323`）。
10. **CEO 探索纪律与验收外包**：CEO 探索工具限次限量（累计 5 次、单文件 3000 字符、禁递归），验收必须外包给节点执行（`core/ceo.py:369-384`；`CEO_SYSTEM_PROMPT`「验收外包」节）——入口模型只负责调度，不消耗在体力活上。

---

## 六、源码依据清单（文件路径 + 关键类/函数 对照表）

| # | 主题 | 文件路径 | 关键类/函数 | 行号（约） |
|---|------|----------|------------|-----------|
| 1 | CLI 入口 | `main.py` | `main()`、`INIT_DESCRIBE`、`handle_command`（/save、/backup 等指令） | 208、35、128 |
| 2 | Web 入口 | `web_app.py` | `PORT=8021`；端点 `/api/state`、`/api/chat`、`/api/command`、`/api/evolve`、`/api/events`(SSE)、`/api/nodes`、`/api/node/<id>`、`/api/node/<id>/describe`、`/api/node/<id>/logs`、`/api/node/<id>/skill/install|remove`、`/api/node/<id>/revive|remove`、`/api/session/*` | 31、40-56 及 `api_*` 函数 |
| 3 | CEO | `core/ceo.py` | `CEO.talk`、`_talk_body`、`_dispatch_step`、`_dispatch_parallel`、`_dispatch_single`、`_dispatch_clone`、`_answer_clarify`、`_dynamic_limit`、`_detect_loop`、`save_conversation`、`load_conversation`、`atomic_write_session` | 256、269、667、724、820、874、913、467、482、134、165、28 |
| 4 | CEO 提示词与任务书 | `config/prompts.py` | `CEO_SYSTEM_PROMPT`、`CEO_TOOLS`（7 个工具 schema）、`render_task_md` | 7、206、216 |
| 5 | 节点闭环 | `core/node.py` | `NODE`、`work`、`_work_inner`、`_delegate`、`_run_child`、`spawn_parallel`、`_update_node`、`evolve_describe`、`_rework_instruction`、`_correction_instruction`、`_evaluate_delegation`、`_EXEC_SEM`、`_compute_timeouts` | 55、96、123、615、456、788、746、763、383、373、572、37、310 |
| 6 | 节点智能体 | `core/agent.py` | `AGENT`、`decide`、`evaluate`（accept_report）、`update_describe`、`_render_task_md`、`_clean_describe`、`reset_history`、`compress_history_if_needed`、`TOOL_ASK_UPPER`/`TOOL_PLAN_HERMES`/`TOOL_SPLIT_TASKS`、`ALL_TOOLS` | 141、284、328、401、251、100、195、204、21-94、96 |
| 7 | hermes 执行器封装 | `core/hermes.py` | `HermesInstance`：`execute`、`resume`、`list_skills`、`get_skill`、`install_skill`、`delete_skill`、`create`、`setup_newborn`、`ensure_model`、`create_temp_clone`、`delete_temp`；模块级 `ensure_profile`、`_extract_session_id` | 215、352、566、256、326、303、336、680、693、739、763、771、798、165 |
| 8 | 答题卡封装 | `utils/chat.py` | `CHAT`、`call_tools`（防抖链）、`get_full_response`、`set_debug_level` | 58、78、164、43 |
| 9 | 向量化/拥挤度 | `utils/embedding.py` | `get_embedding`（LRU+线程安全）、`cosine_sim`、`crowding_density`、离线优先策略（HF_HUB_OFFLINE/HF_ENDPOINT）、`QUERY_PREFIX` | 61、84、89、39-48、24 |
| 10 | 任务路由 | `core/router.py` | `score_nodes`、`top_k_nodes`、`temperature_select`、`route`、`resolve_stages`（Kahn 拓扑） | 16、37、50、75、91 |
| 11 | 进化引擎 | `core/evolution.py` | `base_pressure`、`elimination_count`、`crowding_scores`、`_is_blank_node`、`effective_capacity`、`percentile_score`、`elimination_probabilities`、`_fitness_scores`、`_pick_parents`、`_skill_union`、`plan_newborns`、`select_eliminations`、`evolve`、`_default_describe`、`_recombine_describe` | 26、33、67、48、94、125、139、179、192、215、242、276、307、435、441 |
| 12 | 注册表 | `core/registry.py` | `Node`、`Registry`、`load`（损坏恢复）、`save`（原子写）、`archive_node`、`revive_node`、`ensure_initial`、`_restore_from_archive` | 26、77、89、145、205、222、242、113 |
| 13 | 上下文压缩 | `core/compress.py` | `should_compress`、`compress`（system+尾部精华+中部摘要）、`_brief`、`sanitize_tool_messages` | 21、27、72、81 |
| 14 | 全局配置 | `config/config.py` | `CONFIG.load`（模型分组 + population/pressure/crowding/elimination/capacity_growth/router/agent/hermes/scoring/compress 参数加载）、`workspace` property | 65、57 |
| 15 | 种群/进化参数 | `config/settings.yaml` | population（10/50/5/0.30/0.5）、pressure.tau=8、crowding（0.15/0.6/0.5）、elimination.score_weight=1.0、capacity_growth（enabled/slope/max/min/0.60）、router（5/0.3）、agent（3/3/2/3/5）、hermes（3600/1800/7200）、scoring（0.3/500/0.2）、compress（32768/0.67/0.25 等） | 全文 |
| 16 | 模型配置 | `config/AI_config.json` | ceo=deepseek-v4-flash（DeepSeek API）；brain/worker=qwen3.8-27b（自建 vLLM） | 全文 |
| 17 | 事件总线 | `web/event_bus.py` | `EventBus`、`emit`、`subscribe`、`history`、`set_task_session`；事件类型清单（chat.*/graph.*/node.*/hermes.*/evolve.round） | 45、76、53、93、33、10-15 |
| 18 | 遗留封装（未使用） | `utils/hermes.py` | 遗留简版 HermesInstance，无调用方（现行实现为 `core/hermes.py`，文件头自述） | 1-5 |
| 19 | 默认技能 | `skills/anysearch/`（`SKILL.md` + `anysearch.py`） | 所有节点出生自带（`core/evolution.py:381`、`core/hermes.py:823`） | — |
| 20 | 运行时数据 | `data/registry.json`、`data/archive/`、`data/sessions/`、`data/tasks/`、`data/workspace.json` | 种群状态 / 淘汰存档 / 会话存档 / 任务目录 / 工作区路径 | — |
| 21 | embedding 模型缓存 | `models/`（`models--BAAI--bge-small-zh-v1.5/`） | bge-small-zh-v1.5（约 92MB，离线加载） | — |
| 22 | 测试/运维脚本 | `tests/`（约 90 个 .py + 运行产物截图）、`run_verify_task.py`、`tools/watch_task.py`、`_ws_spa_test/` | `tests/ensure_node_profiles.py`（补建 profile）、`tests/install_anysearch.py`（批量装技能）、`tests/test_niche_competition.py`、`tests/test_evolution_sim.py`、`tests/train_system*.py`（训练脚本）、`tests/ui_*.py`（UI 回归）、`run_verify_task.py`（全链路验证） | — |
| 23 | 项目文档 | `README.md`、`设计.txt`、`迁移手册.md`、`docs/设计_交互增强.md` | — | — |
| 24 | 辅助脚本 | `backup_training.py`（`do_backup`/`do_restore`，/backup、/restore 指令）、`csv_cleaner.py`、`test.py`（未完成的注释代码） | — | — |
| 25 | 前端 | `web/static/`（index.html + app.js + style.css）、`web2/`、`web3/`（React+Vite 构建产物） | 对话 + Canvas 图谱 + 节点详情（`README.md` 第七节） | — |

---

*说明：本文档基于 core/、utils/、config/、web/、main.py、web_app.py 及关键辅助脚本的完整只读阅读撰写，未修改任何现有文件；4.9 节按任务要求以代码为准如实记录了与 README/任务书描述的出入。*
