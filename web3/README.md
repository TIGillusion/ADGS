# ADGS · 智能体达尔文进化观测台（web3）

> ADGS 智能体达尔文进化系统的**全新 Web 前端**。从零重写，不复用、不借鉴旧版 `web/` 与 `web2/` 的任何代码；自建设计系统、自建 Canvas 实时图谱引擎。功能与旧版完全对齐，交互说明中列出的能力全部实现，生产级质量。

## 技术栈

- **React 19** + **TypeScript 5.8（strict）** + **Vite 7**
- **Tailwind CSS v4**（自定义设计 token）+ **lucide-react** 图标
- **Vitest 3** + **jsdom** + **Testing Library**（单元/组件测试）
- **Playwright 1.52**（E2E，零依赖 Node mock 后端）

构建产物为纯静态 SPA（无 Node 运行时依赖），由 Flask 挂载在 `/web3/`。

## 设计系统：深渊生物发光（Abyss Bioluminescence）

深邃空间暗色底（`#060a13`）+ 生物荧光强调色，克制、高对比、信息密度高：

| 语义 | 色 | 用途 |
| --- | --- | --- |
| `life`（#2fe0bd） | 青碧 | 主操作、活跃/成功、连接 |
| `ok`（#3ecf7f） | 翠绿 | 高分/通过 |
| `warn`（#fbbf24） | 琥珀 | 中分段/连接中/重连 |
| `danger`（#f4535f） | 绯红 | 低分/错误/危险操作 |
| `gene` / `pulse` | 紫 / 蓝 | 谱系/重组类型 |

字体：Space Grotesk（展示）+ Noto Sans SC（正文）+ JetBrains Mono（数字/代码）。首屏内联铺底色防闪。

## 功能清单（与旧版完全对齐）

- **对话工作台**：CEO 流式对话、busy/排队状态、`/` 斜杠命令自动补全（`/workspace /evolve /queue /status /debug /save /load /sessions /history /help`）、执行中再发任务自动排队。
- **演化图谱**：Canvas 实时图谱（CEO → 任务 → 节点实例），支持平移/缩放/点选/悬浮提示/图例；统计条「X 轮 · Y 任务 · Z 实例」；临时克隆（clone）实例以独立样式呈现。
- **节点种群**：活动节点列表 + 生态位/搜索过滤 + 成功率分数条；归档区懒加载（复活 / 永久删除，危险操作二次确认）；点击节点打开抽屉。
- **节点抽屉**：双栏详情——生态位描述（可编辑）、综合评分/成功率/谱系、技能基因（查看/移除/从其他节点移植）、大脑决策 + Hermes 执行日志实时滚动、危险操作（复活/淘汰/删除，均带确认）。
- **历史会话**：会话列表、载入历史（图谱 + 转录回放）、新建对话（确认 + CEO 上下文重置）、历史视图隔离占位。
- **系统概览**：种群状态/承载量/平均适应度、累计任务、模型信息、当前优胜榜、最近一次进化、工作区、最近事件流。
- **进化控制**：手动触发进化（确认）、进化横幅（`N → M`）、种群刷新。
- **实时连接**：SSE 事件流，顶栏指示器（连接中/实时连接/重连中/断开）+ 看门狗。
- **错误态**：后端不可达 → 重连入口。

## 目录结构

```
web3/
├─ src/
│  ├─ lib/
│  │  ├─ api.ts        # REST 客户端（ApiError、超时、同域 JSON）
│  │  ├─ sse.ts        # SSE 客户端（EventSource + 看门狗，状态机）
│  │  ├─ store.ts      # 全局 store（状态/轮询/SSE 分发/图谱落盘/节点操作）+ LogBuffer
│  │  ├─ graph.ts      # 图谱 reducer（事件 → 轮次/任务/实例，语义不变量）
│  │  ├─ graphCanvas.ts# Canvas 图谱渲染引擎（布局/绘制/交互）
│  │  ├─ chat.ts       # 对话消息/流式/斜杠补全逻辑
│  │  └─ format.ts     # 分数色调/时间/百分比/JSON 格式化
│  ├─ components/      # ChatView / GraphView / NodesView / NodeDrawer / SessionsView / OverviewView / ui
│  ├─ App.tsx          # 布局 + 导航 + 视图路由 + 全局快捷键
│  └─ types.ts         # 类型定义
├─ test/              # Vitest 单元/组件测试（86 用例）
├─ e2e/
│  ├─ app.spec.ts      # Playwright E2E（11 场景，mock 后端）
│  ├─ mock-server.mjs  # 零依赖 Node mock 后端（8099，SSE + 全 API + 脚本化任务生命周期）
│  └─ live-smoke.mjs   # 真实后端冒烟（可选，见下）
├─ playwright.config.ts
├─ vite.config.ts      # base: "./"（相对资源，兼容 /web3/ 与 / 两种挂载）
└─ public/favicon.svg
```

## 运行

```bash
# 依赖（已安装则跳过）
npm install --ignore-scripts

# 开发（Vite dev server 5199，/api 代理到 8021）
npm run dev

# 类型检查 / 单元测试 / 生产构建
npm run typecheck
npm run test
npm run build          # 产物 dist/

# E2E（自动起 mock 后端 8099）
npm run e2e
npm run e2e:report     # HTML 报告
```

## 后端集成（Flask，端口 8021）

构建产物由 `web_app.py` 挂载在 `/web3/`（与旧版 `/`、`/web2/` 三版并存）：

```python
WEB3_DIR = ROOT / "web3" / "dist"

@app.route("/web3/")
@app.route("/web3/<path:filename>")
def web3_index(filename="index.html"):
    p = (WEB3_DIR / filename).resolve()
    if not str(p).startswith(str(WEB3_DIR.resolve())) or not p.exists():
        return "not found", 404
    return send_from_directory(WEB3_DIR, filename)
```

前端兼容既有 Flask API（同域 JSON + SSE）：`/api/state`、`/api/chat`、`/api/command`、`/api/session/*`、`/api/evolve`、`/api/nodes`、`/api/node/<id>` 及其子操作、`/api/events`（SSE）。兼容旧版保存图谱格式（`graphFromSaved` 映射 legacy 字段）。

**真实后端冒烟**（可选，需 8021 在跑）：

```bash
node e2e/live-smoke.mjs
```

断言：导航渲染真实徽标、5 视图实质内容、节点视图渲染真实节点、SSE 60s 稳定性（不出现 ~50s 误重连）、无 JS 崩溃、无失败请求。

## 测试

| 层 | 工具 | 覆盖 | 结果 |
| --- | --- | --- | --- |
| 单元/组件 | Vitest + jsdom | graph reducer、chat、format、api、store、NodeDrawer 渲染回归、SSE 看门狗回归、总览污染归一化回归 | **92/92** |
| E2E | Playwright + mock 后端 | 启动、对话全生命周期+图谱落盘、图谱、节点卡片/搜索/归档/抽屉、复活+移除、会话载入、斜杠命令、进化、新建对话、排队、错误态、跨轮 seq 漂移重编号 | **12/12** |
| 真实后端 | live-smoke.mjs | /web3/ 路由、真实数据渲染、SSE 稳定性、无崩溃 | 通过 |

关键回归防线（曾出过 bug，均有测试锁定）：
- **NodeDrawer 打开即崩溃**：`LogBuffer.subscribe` 曾被 `useSyncExternalStore` 以裸函数调用（`this=undefined`）→ 整树卸载。已改箭头属性 + 4 个组件测试。
- **SSE 空闲误重连**：后端 keepalive 是 SSE 注释行（不触发 `onmessage`），仅以 data 事件计时会每 ~50s 误判半死。看门狗现以浏览器 `readyState===OPEN` 判定存活 + 2 个测试。
- **图谱 reducer 语义**：`parent="ceo"` 字面标记过滤、并行 eval 归因、hermes 语义 phase、实例键 `node@task`、`node.eval` 先于带 task 的 `node.status` 到达等，均有测试。
- **任务序号每轮从 1 开始**（2026-09-03 修复，用户实测"轮 4 从 2 开始"）：后端 `_task_seq` 是累计计数器——ask_user 提问挂起恢复、任务异常恢复时**故意不重置**（同一轮继续），重启才归零，跨轮直接展示会出现"新一轮从 2/7 开始"的错位。前端图谱改为**按轮重编号**：CEO 级任务 label 一律显示"该轮第 k 个拆分 = k"，与后端累计 seq 解耦；委派序号（挂节点实例下）是节点本地编号，保持原样。载入历史图谱（`graphFromSaved`）同样重编号，旧会话的漂移 label 载入后即纠正。后端异常路径（`web_app.py` 任务 runner）同步修复：`reset_session` 后恢复 `_task_done=True`，使下一条用户消息作为新任务开新轮、轮号 +1，且队列不再被误判为"提问挂起"而卡住。三层防线：`web3/test/graph.test.ts`（5 个重编号用例）、`ADGS/tests/test_task_seq_reset.py`（后端状态迁移 4 用例 + 漂移复现对照）、E2E 双任务漂移回归（mock 端 seq 跨提交累计，断言每轮 label = 1,2）。
- **旧前端「任务总览」污染防护**（2026-09-03 用户报告：`auto-20260828-133107` 载入后出现 29 个 CEO 节点、305 节点）：旧 web2 总览模式把**全部会话**节点 key 加 `${会话名}::` 前缀画到同一画布（`web2/static/app.js:1077`），该视图被旧前端自动保存 POST 到 `/api/session/graph`，后端原样落盘 → 29 个会话的完整图谱（含 29 个 `xxx::ceo` 节点）混进单个会话文件。旧"防倒退"守卫只拦节点数锐减、不拦膨胀，故放行且未再覆盖（一次性事故，全库 29 文件仅 1 个受污，外部数据在各会话文件均有完整副本）。三层防线：**① 后端拒写**（`web_app.py` `/api/session/graph`）——写入图谱只要含任一"其它会话名::"前缀的 key 即整图拒绝（同时挡住原始 dump 与浏览器刷新后的再导出；本会话名::前缀的自身残留放行）；**② 前端载入归一化**（`graphFromSaved`）——任何被污染文件载入时丢弃所有 ceo 型节点（规范 CEO 唯一）、父引用落在 `ceo`/`xxx::ceo`（含缺失）一律重定向到规范 CEO、带前缀 key 原样保留（剥前缀会造成 `round-1` 键冲突）；**③ 数据清理脚本**（`e2e/repair-session.mjs`）——备份原文件后仅保留本会话自身节点（无 `::` 前缀 + 本会话名::非 ceo 剥前缀），父引用闭合校验不通过则中止。回归：`web3/test/graph.test.ts`（多 CEO 合并 + 悬空父 + 前缀保留）、`ADGS/tests/test_api_guardrails.py::test_graph_overview_pollution_guard`（外部:: 拒 / 本会话:: 允许）。诊断工具：`e2e/graph-inspect.mjs`（单文件解剖）、`e2e/scan-sessions.mjs`（全库扫描）、`e2e/repair-analyze.mjs`（修复前冗余/悬空校验）。

## 已知行为说明（非 bug）

- **SSE 首帧延迟**：后端 `/api/events` 无事件时最长 15s 才发首个 keepalive，故空闲时指示器约 15s 才从「连接中」变「实时连接」。系统忙碌（有事件）时秒级 open。此为后端既有设计（`web2` 同行为），前端看门狗已适配不误重连。
- **图谱为空**：图谱展示**当前会话的实时任务演化**，从初始化时 `/api/state.events` 尾部折叠 + 后续 SSE 构建。若近期只有进化/系统事件、无任务事件，则显示「0 轮 · 0 任务 · 0 实例」属正常；执行任务后即实时填充。
- **种群徽标**：活动列表来自 `/api/state`（8s 轮询），节点操作后徽标在下一次轮询刷新。
