// ============================================================
// 图谱模型 — SSE reducer + graphFlow 统一轮次/批次投影
// nodes 保留旧状态归因接口；web3 渲染和详情使用 displayGraph(state)。
//
// 结构（与后端事件语义 / 历史会话存储格式对齐）：
//   ceo
//    └─ round-N            （用户每提交一次任务 = 一轮）
//        └─ t{...}          （任务序号节点，label=序号）
//            └─ ags_x@t..   （执行节点实例，key=节点@任务，可克隆/可嵌套）
//                └─ ags_x-n1-...   （委派产生的下级任务序号 → 再挂执行实例）
//
// 纯函数设计：同一事件序列 → 同一图谱状态（可单元测试、可回放）
// ============================================================

import { recordFlowEvent, type FlowSnapshot } from "@/lib/graphFlow";

import type {
  BusEvent,
  GraphNode,
  GraphNodeStatus,
  SavedGraphNode,
} from "@/types";

export interface GraphState {
  flow?: FlowSnapshot;
  links?: { from: string; to: string }[];
  nodes: GraphNode[];
  roundSeq: number;
  currentRound: string | null;
  /** A2A 节点间委派边（merged_brain：worker 直接互相委派，非树结构，叠加渲染） */
  a2aEdges: A2aEdge[];
}

/** A2A 委派边：parent_node → child_node（存节点 id，渲染时归到最近实例） */
export interface A2aEdge {
  delegationId: string;
  fromNodeId: string;
  toNodeId: string;
  status: "running" | "passed" | "blocked" | "failed" | "interrupted";
  summary: string;
  /** 直系上级复核（report_delegation_verdict）的粒度分与理由 */
  score?: number;
  note?: string;
  /** 资源互斥声明（shared_resources） */
  resources?: string[];
  /** 越级纠错（可多次；保留最近一次） */
  escalatedBy?: string;
  escalateScore?: number;
  escalateNote?: string;
  createdAt?: number;
}

export const CEO_KEY = "ceo";

const ACTIVE_STATUSES: ReadonlySet<GraphNodeStatus> = new Set([
  "waiting",
  "running",
  "delegating",
]);

export function graphInit(): GraphState {
  return {
    nodes: [
      {
        key: CEO_KEY,
        id: "ceo",
        type: "ceo",
        label: "CEO",
        parent: null,
        status: "idle",
        extra: { task_title: "" },
      },
    ],
    roundSeq: 0,
    currentRound: null,
    a2aEdges: [],
  };
}

// ---------------- 内部工具 ----------------

function byKey(nodes: GraphNode[]): Map<string, GraphNode> {
  const m = new Map<string, GraphNode>();
  for (const n of nodes) m.set(n.key, n);
  return m;
}

/** upsert：替换或追加节点（保留数组顺序 = 插入顺序，用于"最近实例"推断） */
function upsert(nodes: GraphNode[], node: GraphNode): GraphNode[] {
  const idx = nodes.findIndex((n) => n.key === node.key);
  if (idx === -1) return [...nodes, node];
  const next = nodes.slice();
  next[idx] = node;
  return next;
}

/** 某节点 id 的最近一个执行实例（数组后序 = 最近创建） */
export function lastInstance(state: GraphState, nodeId: string): GraphNode | null {
  let found: GraphNode | null = null;
  for (const n of state.nodes) {
    if (n.id === nodeId && (n.type === "node" || n.type === "task")) {
      if (n.type === "node") found = n;
    }
  }
  return found;
}

/** 某节点 id 最近一个仍在执行中的实例（node.eval 不带 task 时的归因：
 *  后端先发 eval 后发 node.status(done, 带 task)，eval 到达时目标实例
 *  通常还是 running/delegating —— 并行双实例场景下后续带 task 的
 *  node.status 会把精确分数/状态写回正确实例，形成自修正） */
function lastActiveInstance(state: GraphState, nodeId: string): GraphNode | null {
  let found: GraphNode | null = null;
  for (const n of state.nodes) {
    if (n.id === nodeId && n.type === "node" && ACTIVE_STATUSES.has(n.status)) {
      found = n;
    }
  }
  return found;
}

/** 按任务标题找任务节点（dispatch 事件 task=名称 时的回退匹配） */
function taskByTitle(nodes: GraphNode[], title: string): GraphNode | null {
  for (const n of nodes) {
    if (n.type === "task" && n.extra.task_title === title) return n;
  }
  return null;
}

function cloneExtra(n: GraphNode): GraphNode["extra"] {
  return { ...n.extra };
}

function isStatus(s: unknown): s is GraphNodeStatus {
  return (
    s === "idle" ||
    s === "waiting" ||
    s === "running" ||
    s === "delegating" ||
    s === "done" ||
    s === "failed" || s === "interrupted" || s === "unknown" ||
    s === "unjudged" || s === "blocked"
  );
}

function executionExtra(d: Record<string, unknown>): GraphNode["extra"] {
  const e: GraphNode["extra"] = {};
  for (const field of ["execution_id", "attempt_id", "run_id", "task_id"] as const) {
    if (typeof d[field] === "string") e[field] = d[field];
  }
  return e;
}

function explicitInstance(state: GraphState, d: Record<string, unknown>): GraphNode | null {
  const execution = String(d.execution_id ?? "");
  if (execution) return state.nodes.find((n) => n.type === "node" &&
    (n.extra.execution_id === execution || n.key === `${String(d.node ?? "")}@${execution}`)) ?? null;
  const task = String(d.task_id ?? d.task ?? "");
  return task ? state.nodes.find((n) => n.key === `${String(d.node ?? "")}@${task}`) ?? null : null;
}

// ---------------- 主 reducer ----------------

export function applyEventToGraph(state: GraphState, ev: BusEvent, now: number): GraphState {
  const next = applyLegacyEventToGraph(state, ev, now);
  const flow = recordFlowEvent(state.flow, ev, state);
  return flow === state.flow ? next : { ...next, flow };
}

function applyLegacyEventToGraph(
  state: GraphState,
  ev: BusEvent,
  now: number,
): GraphState {
  const d = ev.data ?? {};
  switch (ev.type) {
    case "chat.user":
      return onChatUser(state, d, now);
    case "chat.ceo_report":
    case "chat.ceo_finish":
      return recordCeoFirst(state, String(d.text ?? d.summary ?? ""));
    case "graph.task":
      return onGraphTask(state, d, now);
    case "graph.dispatch":
      return onGraphDispatch(state, d, now);
    case "graph.clone":
      return onGraphClone(state, d);
    case "graph.child":
      // 旧版事件（等价于 dispatch）
      return onGraphDispatch(
        state,
        {
          parent: d.parent,
          task: d.task ?? d.id,
          node: d.node,
          mode: d.mode,
          goal: d.goal,
        },
        now,
      );
    case "node.status":
    case "graph.node_status":
      return onNodeStatus(state, d, now);
    case "node.eval":
      return onNodeEval(state, d);
    case "hermes.start":
      return onHermesPhase(state, d, "hermes 开始执行");
    case "hermes.end":
      return onHermesPhase(state, d, "hermes 执行结束");
    case "a2a.delegate":
      return onA2aDelegate(state, d);
    case "a2a.verdict":
      return onA2aVerdict(state, d);
    case "a2a.escalate":
      return onA2aEscalate(state, d);
    default:
      return state;
  }
}

// ---------------- 各事件处理 ----------------

function onChatUser(state: GraphState, d: Record<string, unknown>, now: number): GraphState {
  const roundSeq = state.roundSeq + 1;
  const key = `round-${roundSeq}`;
  const text = String(d.text ?? "");
  let nodes = state.nodes;
  // 刷新 CEO 任务标题
  const ceo = nodes.find((n) => n.key === CEO_KEY);
  if (ceo) {
    nodes = upsert(nodes, {
      ...ceo,
      extra: { ...cloneExtra(ceo), task_title: text.slice(0, 300) },
    });
  }
  nodes = upsert(nodes, {
    key,
    id: key,
    type: "round",
    label: `轮 ${roundSeq}`,
    parent: CEO_KEY,
    status: "idle",
    extra: { user_text: text, ceo_first: "" },
    bornTs: now,
  });
  return { ...state, nodes, roundSeq, currentRound: key };
}

function recordCeoFirst(state: GraphState, text: string): GraphState {
  if (!state.currentRound) return state;
  const round = state.nodes.find((n) => n.key === state.currentRound);
  if (!round || round.extra.ceo_first) return state;
  return {
    ...state,
    nodes: upsert(state.nodes, {
      ...round,
      extra: { ...cloneExtra(round), ceo_first: text.slice(0, 1500) },
    }),
  };
}

function onGraphTask(
  state: GraphState,
  d: Record<string, unknown>,
  now: number,
): GraphState {
  const id = String(d.id ?? "");
  if (!id) return state;
  const name = String(d.name ?? "");
  const goal = String(d.goal ?? "");
  const overall = String(d.overall ?? "");
  const seq = d.seq != null ? Number(d.seq) : null;
  const count = d.count != null ? Number(d.count) : undefined;
  const parentRaw = d.parent != null ? String(d.parent) : "";
  const isCeoTask = !parentRaw || parentRaw === CEO_KEY;

  let { nodes, roundSeq, currentRound } = state;
  const map = byKey(nodes);
  const existingTask = map.get(id);

  // CEO 直接派发的任务必须挂到某轮下（刷新后 chat.user 缺失时兜底建轮）
  if (isCeoTask && (!currentRound || !map.has(currentRound))) {
    roundSeq += 1;
    currentRound = `round-${roundSeq}`;
    nodes = upsert(nodes, {
      key: currentRound,
      id: currentRound,
      type: "round",
      label: `轮 ${roundSeq}`,
      parent: CEO_KEY,
      status: "idle",
      extra: { user_text: "", ceo_first: "" },
      bornTs: now,
    });
  }

  let parentKey: string;
  if (isCeoTask) {
    // 结构事件回放没有 chat.user 轮次边界：旧任务保持已保存的轮次归属。
    parentKey = existingTask?.parent ?? currentRound ?? CEO_KEY;
  } else if (map.has(parentRaw)) {
    parentKey = parentRaw; // 委派：父是节点实例 key
  } else {
    // 父实例尚未到达（事件乱序兜底）→ 回退最近实例 / 当前轮
    const inst = lastInstance(state, parentRaw);
    parentKey = inst ? inst.key : currentRound ?? CEO_KEY;
  }

  const label = seq != null ? String(seq) : id;
  // 每轮从 1 重编号（CEO 级任务）：后端 seq 是跨 ask_user 挂起/重启的累计
  // 计数器（提问恢复不重置、重启归零），直接展示会出现"新一轮从 2/7 开始"
  // 的错位。前端按轮重编号 = 该轮第 k 个拆分显示 k；count（批次节点数）
  // 保留原值，原始 seq 不再展示（仍嵌在任务 id t{round}_{seq}_{uid} 中）。
  const displaySeq =
    isCeoTask && !existingTask
      ? nodes.reduce((c, n) => c + (n.type === "task" && n.parent === parentKey ? 1 : 0), 0) + 1
      : null;
  nodes = upsert(nodes, {
    key: id,
    id,
    type: "task",
    label: existingTask?.label ?? (displaySeq != null ? String(displaySeq) : label),
    parent: parentKey,
    status: "idle",
    extra: {
      goal: goal.slice(0, 1000),
      overall_goal: overall.slice(0, 1000),
      task_title: name,
      seq: existingTask?.extra.seq ?? (displaySeq != null ? displaySeq : (seq ?? undefined)),
      count,
    },
    bornTs: existingTask?.bornTs ?? now,
  });
  return { ...state, nodes, roundSeq, currentRound };
}

/** CEO 级事件的兜底建轮（恢复/续接回放缺 chat.user 锚点时保持层级）
 *  2026-10-23 用户报告「节点直连 CEO」：续接派发不发 graph.task、事件窗口
 *  又丢失轮次锚点时，实例曾直挂 CEO——现在就地补建轮次与任务节点。 */
function ensureRound(
  state: GraphState,
  now: number,
): { nodes: GraphNode[]; roundSeq: number; currentRound: string } {
  if (state.currentRound && state.nodes.some((n) => n.key === state.currentRound)) {
    return { nodes: state.nodes, roundSeq: state.roundSeq, currentRound: state.currentRound };
  }
  const roundSeq = state.roundSeq + 1;
  const currentRound = `round-${roundSeq}`;
  const nodes = upsert(state.nodes, {
    key: currentRound,
    id: currentRound,
    type: "round",
    label: `轮 ${roundSeq}`,
    parent: CEO_KEY,
    status: "idle",
    extra: { user_text: "", ceo_first: "" },
    bornTs: now,
  });
  return { nodes, roundSeq, currentRound };
}

/** A2A 子执行的 task_id 前缀（core/web_app._mesh_run_child 约定）：
 *  它们不属于 CEO 树（无轮次/任务锚点），关系由 a2a 委派边表达。 */
function isA2aSubtask(task: string): boolean {
  return task.startsWith("mesh_");
}

function onGraphDispatch(
  state: GraphState,
  d: Record<string, unknown>,
  now: number,
): GraphState {
  const node = String(d.node ?? "");
  if (!node) return state;
  const task = String(d.task_id ?? d.task ?? "task");
  if (isA2aSubtask(task)) return state; // A2A 子执行：不进 CEO 树
  const instKey = `${node}@${String(d.execution_id ?? task)}`;
  const mode: "direct" | "clone" = d.mode === "clone" ? "clone" : "direct";
  const goal = String(d.goal ?? "").slice(0, 1000);
  const parentRaw = d.parent != null ? String(d.parent) : "";

  let nodes = state.nodes;
  let { roundSeq, currentRound } = state;
  const map = byKey(nodes);

  // 父任务定位：task id → 名称匹配 → parent 兜底 → 补建任务（不再直挂 CEO）
  // parent="ceo" 是后端对顶层派发的字面标记，不作为真实父节点
  // （否则实例会绕过轮节点直接挂到 CEO 下，轮变成空容器）
  let parentKey: string;
  if (map.has(task)) parentKey = task;
  else {
    const byTitle = taskByTitle(nodes, task);
    if (byTitle) parentKey = byTitle.key;
    else if (parentRaw && parentRaw !== CEO_KEY && map.has(parentRaw)) parentKey = parentRaw;
    else {
      const ensured = ensureRound({ nodes, roundSeq, currentRound, a2aEdges: state.a2aEdges }, now);
      nodes = ensured.nodes;
      roundSeq = ensured.roundSeq;
      currentRound = ensured.currentRound;
      nodes = upsert(nodes, {
        key: task,
        id: task,
        type: "task",
        label: "1",
        parent: currentRound,
        status: "running",
        extra: { goal: goal.slice(0, 1000), task_title: task.slice(0, 30) },
        bornTs: now,
      });
      parentKey = task;
    }
  }

  const existing = byKey(nodes).get(instKey);
  const next: GraphNode = existing
    ? {
        ...existing,
        parent: parentKey,
        extra: {
          ...cloneExtra(existing),
          goal: goal || existing.extra.goal,
          mode,
          profile: existing.extra.profile ?? node,
          ...executionExtra(d),
        },
      }
    : {
        key: instKey,
        id: node,
        type: "node" as const,
        label: node,
        parent: parentKey,
        status: "waiting" as const,
        extra: { goal, task_title: task, mode, profile: node, ...executionExtra(d) },
        bornTs: now,
      };
  return { ...state, nodes: upsert(nodes, next), roundSeq, currentRound };
}

function onGraphClone(state: GraphState, d: Record<string, unknown>): GraphState {
  const node = String(d.node ?? "");
  const profile = String(d.profile ?? "");
  if (!node) return state;
  // 克隆实例 = 该节点最近一个 mode=clone 的实例（否则最近实例）
  let target: GraphNode | null = null;
  for (const n of state.nodes) {
    if (n.id === node && n.type === "node") {
      if (n.extra.mode === "clone") target = n;
    }
  }
  if (!target) target = lastInstance(state, node);
  if (!target) return state;
  return {
    ...state,
    nodes: upsert(state.nodes, {
      ...target,
      extra: { ...cloneExtra(target), profile: profile || target.extra.profile, mode: "clone" },
    }),
  };
}

function onNodeStatus(
  state: GraphState,
  d: Record<string, unknown>,
  now: number,
): GraphState {
  const node = String(d.node ?? "");
  if (!node || !isStatus(d.status)) return state;
  const status = d.status;
  const task = String(d.task_id ?? d.task ?? "");
  const profile = d.profile != null ? String(d.profile) : "";

  const instKey = d.execution_id ? `${node}@${String(d.execution_id)}` :
    task ? `${node}@${task}` : lastInstance(state, node)?.key ?? "";
  const existing = instKey ? state.nodes.find((n) => n.key === instKey) : undefined;
  // A2A 子执行（委派给其他节点的 mesh_ 任务）不进 CEO 树：
  // 状态/评分由 a2a 委派边与节点种群页表达，直挂 CEO 会破坏层级（2026-10-23）
  if (!existing && isA2aSubtask(task)) return state;

  const extra: GraphNode["extra"] = existing
    ? cloneExtra(existing)
    : { goal: "", task_title: task || node, profile: profile || node, mode: "direct" };
  if (profile) extra.profile = profile;
  Object.assign(extra, executionExtra(d));
  if (d.score != null && !Number.isNaN(Number(d.score))) extra.score = Number(d.score);
  if (d.infra || d.infra_failure || ["unjudged", "unknown", "interrupted", "blocked"].includes(status)) delete extra.passed;
  else if (typeof d.passed === "boolean") extra.passed = d.passed;
  if (d.reworks != null && !Number.isNaN(Number(d.reworks))) {
    extra.reworks = Number(d.reworks);
  }

  if (existing) {
    // 转入 running 的瞬间记录服务端时间戳（ms）——画布"执行中 mm:ss"
    // 的计时起点（重连后由事件重放恢复，不随页面刷新归零）
    if (status === "running" && existing.status !== "running") {
      extra.runTs = now;
    }
    return {
      ...state,
      nodes: upsert(state.nodes, { ...existing, status, extra }),
    };
  }

  // 兜底：状态事件先于 dispatch 到达 → 建临时实例挂当前轮（避免状态丢失；
  // 轮次锚点缺失时兜底建轮，不直挂 CEO——2026-10-23 用户报告）
  if (d.execution_id || (!task && (status === "waiting" || status === "running" || status === "delegating"))) {
    const ensured = ensureRound(state, now);
    const key = instKey || `${node}@${ensured.currentRound}`;
    if (ensured.nodes.some((n) => n.key === key)) return state;
    return {
      ...state,
      nodes: upsert(ensured.nodes, {
        key,
        id: node,
        type: "node",
        label: node,
        parent: ensured.currentRound,
        status,
        extra,
        bornTs: now,
      }),
    };
  }
  return state;
}

function onNodeEval(state: GraphState, d: Record<string, unknown>): GraphState {
  const node = String(d.node ?? "");
  if (!node) return state;
  // 归因优先级：显式 task（当前后端不发，留作前向兼容）→
  // 最近执行中实例（eval 早于 node.status 到达时目标仍 running）→ 最近实例
  let inst: GraphNode | null = explicitInstance(state, d);
  if (d.execution_id && !inst) return state; // Never guess a different execution.
  if (!inst) inst = lastActiveInstance(state, node);
  if (!inst) inst = lastInstance(state, node);
  if (!inst) return state;
  const extra = cloneExtra(inst);
  Object.assign(extra, executionExtra(d));
  if (d.score != null && !Number.isNaN(Number(d.score))) extra.score = Number(d.score);
  if (d.unjudged || d.infra_failure) delete extra.passed;
  else if (typeof d.passed === "boolean") extra.passed = d.passed;
  if (d.reworks != null && !Number.isNaN(Number(d.reworks))) {
    extra.reworks = Number(d.reworks);
  }
  const detail = String(d.detail ?? "");
  if (detail) extra.eval_detail = detail.slice(0, 500);
  // Infrastructure/protocol errors do not establish an acceptance failure.
  const status: GraphNodeStatus =
    d.unjudged || d.infra_failure
      ? "unjudged"
      : inst.status === "done" || inst.status === "failed"
      ? inst.status
      : d.passed === true
        ? "done"
        : d.passed === false && !d.unjudged && !d.infra_failure
          ? "failed" : "unjudged";
  return {
    ...state,
    nodes: upsert(state.nodes, { ...inst, status, extra }),
  };
}

function onHermesPhase(state: GraphState, d: Record<string, unknown>, phase: string): GraphState {
  const node = d.node != null ? String(d.node) : "";
  const profile = d.profile != null ? String(d.profile) : "";
  // 优先按 profile 精确命中克隆实例
  let inst: GraphNode | null = explicitInstance(state, d);
  if (d.execution_id && !inst) return state;
  for (const n of state.nodes) {
    if (!d.execution_id && n.type === "node" && n.extra.profile === profile && profile) {
      inst = n; // 后序覆盖 = 最近
    }
  }
  if (!inst && node) inst = lastInstance(state, node);
  if (!inst) return state;
  const extra = cloneExtra(inst);
  if (profile && !extra.profile) extra.profile = profile;
  // 后端 hermes.start/end 携带语义 phase（如 "rework" 返工），优先于默认文案
  const phaseText = d.phase != null && String(d.phase) ? String(d.phase) : phase;
  return {
    ...state,
    nodes: upsert(state.nodes, {
      ...inst,
      extra: { ...extra, brain: phaseText },
    }),
  };
}

// ---------------- 历史会话兼容 ----------------

/** 将后端保存的图谱（SavedGraphNode[]，含旧版字段）转换为前端 GraphState */
export function graphFromSaved(saved: SavedGraphNode[]): GraphState {
  let roundSeq = 0;
  for (const s of saved) {
    const m = /^round-(\d+)$/.exec(s.key ?? "");
    if (m) roundSeq = Math.max(roundSeq, Number(m[1]));
  }
  let currentRound: string | null = null;
  if (roundSeq > 0) {
    // 恢复后新事件继续用同一轮；若该轮存在则指向它
    currentRound = `round-${roundSeq}`;
    if (!saved.some((s) => s.key === currentRound)) currentRound = null;
  }
  const nodes: GraphNode[] = [
    {
      key: CEO_KEY,
      id: "ceo",
      type: "ceo",
      label: "CEO",
      parent: null,
      status: "idle",
      extra: {},
    },
  ];
  // 旧前端"任务总览"视图污染归一化（2026-09-03 用户报告：一个历史会话打开后
  // 出现 29 个 CEO 节点）。总览模式把全部会话的节点 key 加 "${会话名}::" 前缀
  // 画到同一画布（web2/static/app.js 的 overview），该视图被旧前端自动保存写进
  // 某会话文件后，载入会得到"多 CEO + 跨会话轮次"的混合树。归一化规则：
  // ① 丢弃所有 ceo 型节点（规范 CEO 已在上方创建，多 CEO 无独立信息）；
  // ② 父引用落在任意 ceo 键（"ceo" / "xxx::ceo"，该 ceo 节点可能已缺失）
  //    → 重定向到规范 CEO。带前缀的 round/task/实例 key 原样保留（跨会话唯一，
  //    剥前缀会造成 round-1 等键冲突合并）。
  const ceoKeys = new Set<string>([CEO_KEY]);
  for (const s of saved) {
    if (s && s.type === "ceo" && typeof s.key === "string" && s.key) ceoKeys.add(s.key);
  }
  const toParentKey = (raw: unknown): string => {
    const p = typeof raw === "string" && raw ? raw : CEO_KEY;
    return ceoKeys.has(p) || /::ceo$/.test(p) ? CEO_KEY : p;
  };
  for (const s of saved) {
    if (!s || typeof s.key !== "string" || !s.key) continue;
    if (s.key === CEO_KEY) continue;
    if (s.type === "ceo") continue; // 总览 dump 的多 CEO 节点
    const type =
      s.type === "round" || s.type === "task" || s.type === "ceo"
        ? s.type
        : "node"; // 旧版 clone/其它 → 统一按执行节点
    // Saved transient states are unknown unless an actual acceptance result exists.
    let status: GraphNodeStatus = isStatus(s.status) ? s.status : "idle";
    if (status === "waiting" || status === "running" || status === "delegating") {
      const passed = s.extra ? (s.extra as Record<string, unknown>).passed : undefined;
      status = passed === true ? "done" : passed === false ? "failed" : "unknown";
    }
    const extra: GraphNode["extra"] = {};
    if (s.extra) {
      const e = s.extra as Record<string, unknown>;
      Object.assign(extra, executionExtra(e));
      if (e.runTs != null && Number.isFinite(Number(e.runTs))) extra.runTs = Number(e.runTs);
      if (typeof e.goal === "string") extra.goal = e.goal;
      if (typeof e.overall === "string") extra.overall_goal = e.overall;
      if (typeof e.user_text === "string") extra.user_text = e.user_text;
      if (typeof e.ceo_first === "string") extra.ceo_first = e.ceo_first;
      if (typeof e.overall_goal === "string") extra.overall_goal = e.overall_goal;
      if (e.seq != null && !Number.isNaN(Number(e.seq))) extra.seq = Number(e.seq);
      if (e.count != null && !Number.isNaN(Number(e.count))) extra.count = Number(e.count);
      if (e.score != null && !Number.isNaN(Number(e.score))) extra.score = Number(e.score);
      if (e.reworks != null && !Number.isNaN(Number(e.reworks))) {
        extra.reworks = Number(e.reworks);
      }
      if (typeof e.passed === "boolean") extra.passed = e.passed;
      if (typeof e.profile === "string") extra.profile = e.profile;
      if (typeof e.eval_detail === "string") extra.eval_detail = e.eval_detail;
      if (typeof e.task_title === "string") extra.task_title = e.task_title;
      if (e.flow_snapshot && typeof e.flow_snapshot === "object" && (e.flow_snapshot as FlowSnapshot).version === 1) extra.flow_snapshot = e.flow_snapshot as FlowSnapshot;
      if (e.mode === "clone" || e.mode === "direct") extra.mode = e.mode;
      if (s.type === "clone") extra.mode = "clone";
    }
    nodes.push({
      key: s.key,
      id: typeof s.id === "string" && s.id ? s.id : s.key,
      type,
      label: typeof s.label === "string" && s.label ? s.label : s.key,
      parent: toParentKey(s.parent),
      status,
      extra,
    });
  }
  // 载入历史图谱与实时视图同一编号规则：CEO 级任务（挂轮/挂 CEO 下）
  // 按创建顺序每轮从 1 重编号——旧版保存的 label 直接来自后端累计 seq，
  // 存在"轮 5 从 7 开始"的漂移；委派序号（挂节点实例下）是节点本地编号，保持原样。
  const roundKeys = new Set(nodes.filter((n) => n.type === "round").map((n) => n.key));
  const perParentSeq = new Map<string, number>();
  for (const n of nodes) {
    if (n.type !== "task" || n.parent == null) continue;
    if (n.parent !== CEO_KEY && !roundKeys.has(n.parent)) continue;
    const seq = (perParentSeq.get(n.parent) ?? 0) + 1;
    perParentSeq.set(n.parent, seq);
    n.label = String(seq);
    n.extra.seq = seq;
  }
  const flow = saved.find(n => n.key === CEO_KEY)?.extra?.flow_snapshot as FlowSnapshot | undefined;
  return { nodes, roundSeq, currentRound, a2aEdges: [], ...(flow ? { flow } : {}) };
}

// ---------------- 查询工具 ----------------

/** 实例 key 分解：node@task → {nodeId, taskId} */
export function splitInstKey(key: string): { nodeId: string; taskId: string } {
  const at = key.indexOf("@");
  if (at === -1) return { nodeId: key, taskId: key };
  return { nodeId: key.slice(0, at), taskId: key.slice(at + 1) };
}

/** 统计：活跃/完成/失败节点实例数 */
export function graphStats(state: GraphState): {
  total: number;
  active: number;
  done: number;
  failed: number;
} {
  let total = 0;
  let active = 0;
  let done = 0;
  let failed = 0;
  for (const n of state.nodes) {
    if (n.type !== "node") continue;
    total += 1;
    if (ACTIVE_STATUSES.has(n.status)) active += 1;
    else if (n.status === "done") done += 1;
    else if (n.status === "failed") failed += 1;
  }
  return { total, active, done, failed };
}

/** A2A 委派边：running 建边，结束/复核更新状态（纯函数可回放） */
function onA2aDelegate(state: GraphState, d: Record<string, unknown>): GraphState {
  const delegationId = String(d.delegation_id ?? "");
  const fromNodeId = String(d.parent ?? "");
  const toNodeId = String(d.node ?? "");
  if (!delegationId || !toNodeId) return state;
  const status = (["running", "passed", "blocked", "failed", "interrupted"] as const)
    .includes(d.status as never) ? (d.status as A2aEdge["status"]) : "running";
  const summary = String(d.summary ?? "").slice(0, 200);
  const existing = state.a2aEdges.find((e) => e.delegationId === delegationId);
  const edge: A2aEdge = existing
    ? { ...existing, status, summary: summary || existing.summary }
    : { delegationId, fromNodeId: fromNodeId || "?", toNodeId, status, summary };
  const a2aEdges = existing
    ? state.a2aEdges.map((e) => (e.delegationId === delegationId ? edge : e))
    : [...state.a2aEdges, edge];
  // 委派进行中：父实例标记 delegating（图谱状态语义与 CEO 派发一致）
  const nodes = state.nodes.map((n) => {
    if (status === "running" && n.type === "node" && n.id === edge.fromNodeId &&
        (n.status === "running" || n.status === "waiting")) {
      return { ...n, status: "delegating" as GraphNodeStatus };
    }
    return n;
  });
  return { ...state, nodes, a2aEdges };
}

/** 节点 id → 最近实例 key（A2A 边端点渲染归一；找不到返回 null） */
/** 直系上级复核：更新边状态 + 粒度分 + 复核理由 */
function onA2aVerdict(state: GraphState, d: Record<string, unknown>): GraphState {
  const delegationId = String(d.delegation_id ?? "");
  if (!delegationId) return state;
  const passed = d.passed === true;
  const score = d.score != null ? Number(d.score) : undefined;
  return {
    ...state,
    a2aEdges: state.a2aEdges.map((e) =>
      e.delegationId === delegationId
        ? {
            ...e,
            status: passed ? "passed" : "failed",
            score: score ?? e.score,
            note: String(d.detail ?? e.note ?? ""),
          }
        : e,
    ),
  };
}

/** 越级纠错（例外权）：标注到边，不覆盖直系上级的判定分 */
function onA2aEscalate(state: GraphState, d: Record<string, unknown>): GraphState {
  const delegationId = String(d.delegation_id ?? "");
  if (!delegationId) return state;
  const by = String(d.by ?? "");
  const score = d.score != null ? Number(d.score) : undefined;
  const note = String(d.detail ?? "");
  return {
    ...state,
    a2aEdges: state.a2aEdges.map((e) =>
      e.delegationId === delegationId
        ? { ...e, escalatedBy: by, escalateScore: score, escalateNote: note }
        : e,
    ),
  };
}

export function a2aEndpointKey(state: GraphState, nodeId: string): string | null {
  let found: string | null = null;
  for (const n of state.nodes) {
    if (n.id === nodeId && n.type === "node") found = n.key;
  }
  return found;
}
