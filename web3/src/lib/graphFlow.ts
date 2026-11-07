import type { BusEvent, GraphNode } from "@/types";
import type { GraphState } from "@/lib/graph";
import { ceoGraphStatus } from "@/lib/ceo";
import type { CeoActivity } from "@/types";

export interface FlowContract {
  id: string;
  nodeId: string;
  executionId?: string;
  instanceKey?: string;
  input: string;
  taskId?: string;
  historical?: boolean;
}
export interface FlowSnapshot {
  version: 1;
  nodes: GraphNode[];
  links: { from: string; to: string }[];
  current: Record<string, string>;
  taskBatch: Record<string, string>;
  taskInstance: Record<string, string>;
  delegationBatch: Record<string, string>;
  seen: Record<string, true>;
}
export const FLOW_EVENTS = new Set([
  "chat.user", "chat.ceo_tool", "chat.ceo_ask", "chat.ceo_report", "chat.ceo_finish", "chat.ceo_end", "chat.ceo_activity",
  "graph.task", "graph.dispatch", "graph.instruction", "node.rework", "node.brain", "node.eval",
  "node.status", "graph.node_status", "a2a.delegate", "a2a.verdict", "a2a.escalate",
]);
const str = (value: unknown) => value == null ? "" : String(value);
const text = (d: Record<string, unknown>) => str(d.instruction ?? d.task_md ?? d.goal ?? d.summary ?? d.text);
const identity = (e: BusEvent) => e.id ? `event:${e.id}` : `${e.type}:${e.ts}:${JSON.stringify(e.data)}`;

/** Migration retains known history and marks excerpt-only inputs instead of inventing text. */
export function flowFromLegacy(graph: GraphState): FlowSnapshot {
  const saved = graph.nodes.find(n => n.key === "ceo")?.extra.flow_snapshot;
  if (saved?.version === 1 && Array.isArray(saved.nodes)) return saved;
  const original = graph.nodes;
  const nodes = original.filter(n => n.type !== "task").map(n => ({ ...n, extra: { ...n.extra } }));
  const f: FlowSnapshot = { version: 1, nodes, links: [], current: {}, taskBatch: {}, taskInstance: {}, delegationBatch: {}, seen: {} };
  const byKey = new Map(nodes.map(n => [n.key,n]));
  for (const n of nodes) if (n.type === "round" && n.parent) f.current[n.parent] = n.key;
  const ensureRound = (owner: string) => {
    if (f.current[owner]) return f.current[owner];
    const key = owner === "ceo" ? "round-1" : `${owner}/round/legacy-initial`;
    const inst = byKey.get(owner);
    const r: GraphNode = {key,id:key,type:"round",label:"轮 1",parent:owner,status:"idle",
      extra:{user_text:inst?.extra.goal || "",instruction:inst?.extra.goal || "",source:"初始任务（历史记录可能仅有摘要）",operations:[]}};
    nodes.push(r);byKey.set(key,r);f.current[owner]=key;return key;
  };
  const batches = new Map<string,GraphNode>();
  const aliases = new Map<string,string>();
  const ensureBatch = (parent: string, token: string) => {
    const key = `${parent}/batch/${token}`;
    let b = batches.get(key);
    if (!b) {
      const seq=[...batches.values()].filter(b=>b.parent===parent).length+1;
      b={key,id:key,type:"task",parent,label:String(seq),status:"idle",extra:{seq,contracts:[],operations:[]}};
      batches.set(key,b);nodes.push(b);byKey.set(key,b);
    }
    return b;
  };
  for (const n of original.filter(n=>n.type==='task')) {
    const parent = byKey.get(n.parent || "");
    const roundKey=parent?.type==='round'?parent.key:ensureRound(parent?.type==='node'?parent.key:'ceo');
    // Modern task IDs append their index to a shared stage ID.
    const stage=/^(t\d+_\d+_[^_]+)_\d+$/.exec(n.key)?.[1] || n.key;
    const b=ensureBatch(roundKey,stage);aliases.set(n.key,b.key);f.taskBatch[n.extra.task_id || n.key]=b.key;
    b.extra.goal ||= n.extra.goal;
  }
  for (const n of nodes.filter(n=>n.type==='node')) {
    const oldParent=n.parent || '';
    n.parent=aliases.get(oldParent) || ensureBatch(ensureRound('ceo'),`recovered-${oldParent || n.key}`).key;
    const task=n.extra.task_id || n.key.split('@').slice(1).join('@');
    if(task) { f.taskInstance[task]=n.key;f.taskBatch[task]=n.parent; }
    const b=byKey.get(n.parent)!;
    b.extra.contracts=[...(b.extra.contracts||[]),{id:task||n.key,nodeId:n.id,executionId:n.extra.execution_id,instanceKey:n.key,taskId:task,input:n.extra.goal || '',historical:true}];
    b.extra.count=b.extra.contracts.length;
    ensureRound(n.key);
  }

  return f;
}

export function recordFlowEvent(previous: FlowSnapshot | undefined, event: BusEvent, legacyBefore: GraphState): FlowSnapshot | undefined {
  if (!FLOW_EVENTS.has(event.type)) return previous;
  const original = previous ?? flowFromLegacy(legacyBefore);
  if (["node.eval", "node.brain", "node.rework"].includes(event.type) && event.data?.execution_id &&
      !original.nodes.some(n => n.type === "node" && n.extra.execution_id === event.data.execution_id)) return previous;
  const eventId = identity(event);
  if (original.seen[eventId]) return original;
  const f: FlowSnapshot = { ...original, nodes: [], links: [...original.links], current: { ...original.current },
    taskBatch: { ...original.taskBatch }, taskInstance: { ...original.taskInstance }, delegationBatch: { ...original.delegationBatch }, seen: { ...original.seen, [eventId]: true } };
  const nodes = new Map(original.nodes.map(n => [n.key, { ...n, extra: { ...n.extra } }]));
  const d = event.data ?? {};
  const now = event.ts * 1000;
  const get = (key: string) => nodes.get(key);
  const put = (n: GraphNode) => { nodes.set(n.key, n); return n; };
  const append = (key: string | undefined) => {
    const n = key ? get(key) : undefined;
    if (n) n.extra.operations = [...(n.extra.operations ?? []), event];
  };
  const round = (owner: string, input = "", token?: string, fresh = false) => {
    if (!fresh && f.current[owner] && get(f.current[owner])) return get(f.current[owner])!;
    const seq = [...nodes.values()].filter(n => n.type === "round" && n.parent === owner).length + 1;
    const key = owner === "ceo" ? `round-${seq}` : `${owner}/round/${token || eventId}`;
    const n = get(key) ?? put({ key, id: key, type: "round", parent: owner, label: `轮 ${seq}`, status: "idle", bornTs: now,
      extra: { seq, user_text: input, instruction: input, source: owner === "ceo" ? "用户" : "上级指令", operations: [] } });
    f.current[owner] = key; return n;
  };
  const findInstance = (nodeId: string, executionId = "") => {
    if (executionId) return [...nodes.values()].find(n => n.type === "node" && n.extra.execution_id === executionId && (!nodeId || n.id === nodeId));
    const candidates = [...nodes.values()].filter(n => n.type === "node" && n.id === nodeId);
    return candidates[candidates.length - 1];
  };
  const owner = () => {
    const parent = str(d.parent);
    if (!parent || parent === "ceo") return "ceo";
    if (get(parent)?.type === "node") return parent;
    const execution = str(d.parent_execution_id);
    const n = findInstance(parent, execution);
    if (n) return n.key;
    const key = `${parent}@${execution || `historical-parent-${eventId}`}`;
    put({ key, id: parent, label: parent, type: "node", parent: round("ceo").key, status: "idle", bornTs: now,
      extra: { execution_id: execution || undefined, source: "历史来源，缺少派发记录" } });
    return key;
  };
  const batch = (ownerKey: string, token: string) => {
    const r = round(ownerKey);
    const key = `${r.key}/batch/${token}`;
    const existing = get(key); if (existing) return existing;
    const seq = [...nodes.values()].filter(n => n.type === "task" && n.parent === r.key).length + 1;
    return put({ key, id: key, type: "task", parent: r.key, label: String(seq), status: "idle", bornTs: now,
      extra: { seq, contracts: [], operations: [], source: "委派批次" } });
  };
  const contract = (b: GraphNode, item: FlowContract) => {
    const all = b.extra.contracts ?? [];
    const sameId = all.find(c => c.id === item.id);
    const old = sameId && (!sameId.executionId || !item.executionId || sameId.executionId === item.executionId)
      ? sameId : all.find(c => c.executionId && c.executionId === item.executionId && c.taskId === item.taskId);
    const id = old?.id ?? (sameId && item.executionId ? `${item.id}@${item.executionId}` : item.id);
    const next = { ...old, ...item, id, historical: false, input: item.input || old?.input || "" };
    b.extra.contracts = old ? all.map(c => c.id === old.id ? next : c) : [...all, next];

    b.extra.count = b.extra.contracts.length;
  };
  const attach = (b: GraphNode, nodeId: string, execution: string, task: string, input: string) => {
    const mapped = f.taskInstance[task] ? get(f.taskInstance[task]) : undefined;
    let n = execution ? findInstance(nodeId, execution) : mapped;
    // Legacy A2A events precede the child's authoritative execution ID. Rebind only the
    // placeholder for this exact task; never merge separate executions by population ID.
    if (!n && mapped && !mapped.extra.execution_id && mapped.id === nodeId) {
      n = mapped; n.extra.execution_id = execution || undefined;
    }
    if (!n) {
      const key = `${nodeId}@${execution || task || eventId}`;
      n = put({ key, id: nodeId, label: nodeId, type: "node", parent: b.key, status: "waiting", bornTs: now,
        extra: { execution_id: execution || undefined, task_id: task, goal: input, profile: nodeId } });
    }
    if (task) f.taskInstance[task] = n.key;
    if (n.parent !== b.key && !f.links.some(l => l.from === b.key && l.to === n!.key)) f.links.push({ from: b.key, to: n.key });
    return n;
  };
  const addInput = (n: GraphNode, input: string, token: string) => {
    const key = `${n.key}/round/${token}`;
    let r = get(key);
    if (!r && token.startsWith("initial-")) r = [...nodes.values()].find(r => r.type === "round" && r.parent === n.key && (r.key.includes("/round/initial-") || r.extra.source?.startsWith("初始任务")));
    if (!r) r = round(n.key, input, token, true);
    else { f.current[n.key] = r.key; if (input) { r.extra.user_text = input; r.extra.instruction = input; } }
    if (input) { r.extra.user_text = input; r.extra.instruction = input; }
    if (token.startsWith("initial-")) r.extra.source = "初始任务";
    f.current[n.key] = r.key;
    append(r.key); return r;
  };
  const opToOwner = (key: string) => append(round(key).key);
  const task = str(d.task_id ?? d.task ?? d.id);
  const execution = str(d.execution_id);
  const nodeId = str(d.node);
  if (event.type === "chat.ceo_activity") {
    const ceo = get("ceo");
    if (ceo) {
      ceo.extra.ceo_activity = d as unknown as CeoActivity;
      ceo.extra.execution_id = str(d.execution_id) || undefined;
      ceo.extra.attempt_id = str(d.attempt_id) || undefined;
      ceo.status = ceoGraphStatus(str(d.phase));
    }
  } else if (event.type === "chat.user") {
    const r = round("ceo", str(d.text), undefined, true); append(r.key);
  } else if (event.type.startsWith("chat.ceo_")) {
    opToOwner("ceo");
    // Old continue_task events contain the full instruction even before explicit input events existed.
    if (event.type === "chat.ceo_tool" && d.name === "continue_task") {
      const args = (d.args ?? {}) as Record<string, unknown>;
      const n = findInstance("", str(args.execution_id));
      if (n && !n.extra.last_input_event) {
        const b = batch("ceo", eventId); const instruction = str(args.instruction);
        contract(b, { id: eventId, nodeId: n.id, executionId: n.extra.execution_id, instanceKey: n.key, input: instruction });
        if (n.parent !== b.key) f.links.push({ from: b.key, to: n.key });
        addInput(n, instruction, eventId); append(b.key);
      }
    }
  } else if (event.type === "graph.task") {
    const ownerKey = owner();
    const token = str(d.stage_id ?? d.batch_id ?? (d.seq != null ? `seq-${d.seq}` : d.id));
    const b = get(f.taskBatch[task]) ?? batch(ownerKey, token || eventId);
    if (task) f.taskBatch[task] = b.key;
    contract(b, { id: task || eventId, nodeId: str(d.node), executionId: execution || undefined, taskId: task, input: text(d) });
    append(b.key); opToOwner(ownerKey);
  } else if (event.type === "graph.dispatch") {
    if (!nodeId) return previous;
    const ownerKey = owner();
    const b = get(f.taskBatch[task]) ?? batch(ownerKey, str(d.stage_id ?? d.batch_id ?? task) || eventId);
    f.taskBatch[task] = b.key;
    const n = attach(b, nodeId, execution, task, text(d));
    contract(b, { id: task || eventId, nodeId, executionId: execution || undefined, instanceKey: n.key, taskId: task, input: text(d) });
    addInput(n, text(d), `initial-${execution || task}`);
    append(b.key); opToOwner(ownerKey);
  } else if (event.type === "graph.instruction") {
    const continuation = d.context_mode === "continue";
    let n = findInstance(nodeId, execution);
    const ownerKey = owner();
    const b = continuation ? batch(ownerKey, str(d.batch_id ?? d.instruction_id) || eventId)
      : get(f.taskBatch[task]) ?? batch(ownerKey, str(d.batch_id ?? task) || eventId);
    n = n ?? attach(b, nodeId, execution, task, text(d));
    if (n.parent !== b.key && !f.links.some(l => l.from === b.key && l.to === n!.key)) f.links.push({from:b.key,to:n.key});
    contract(b, { id: continuation ? eventId : (b.extra.contracts?.find(c => c.instanceKey === n!.key)?.id ?? (task || eventId)), nodeId, executionId: execution, instanceKey: n.key, taskId: task, input: text(d) });
    const token = continuation ? str(d.instruction_id) || eventId : `initial-${execution || task}`;
    const r = addInput(n, text(d), token);
    if (d.origin) r.extra.source = str(d.origin);
    n.extra.last_input_event = eventId;
    append(b.key); opToOwner(ownerKey);
  } else if (event.type === "a2a.delegate") {
    const did = str(d.delegation_id); if (!did || !nodeId) return previous;
    let b = get(f.delegationBatch[did]);
    if (!b) {
      const ownerKey = owner(); b = batch(ownerKey, str(d.batch_id) || did);
      f.delegationBatch[did] = b.key; opToOwner(ownerKey);
    }
    if (d.status === "running" || !b.extra.contracts?.some(c => c.id === did)) {
      const childExecution = str(d.child_execution_id);
      const childTask = str(d.child_task_id) || `mesh_${did.slice(0,8)}`;
      const n = attach(b, nodeId, childExecution, childTask, text(d));
      f.taskBatch[childTask] = b.key;
      contract(b, { id: did, nodeId, executionId: childExecution || undefined, instanceKey: n.key, taskId: childTask, input: text(d) });
      addInput(n, text(d), `initial-${childExecution || childTask}`);
    }
    append(b.key);
    for (const c of b.extra.contracts ?? []) {
      if (c.id !== did || !c.instanceKey) continue;
      const n = get(c.instanceKey); if (n && d.status !== "running") { n.status = d.status === "passed" ? "done" : d.status === "blocked" ? "blocked" : "failed"; append(f.current[n.key]); }
    }
  } else if (event.type.startsWith("a2a.")) {
    const b = get(f.delegationBatch[str(d.delegation_id)]); if (b) append(b.key);
  } else {
    let n = findInstance(nodeId, execution);
    if (!n && task && f.taskInstance[task]) n = get(f.taskInstance[task]);
    if (!n && nodeId && (execution || task)) {
      const b = get(f.taskBatch[task]) ?? batch("ceo", `recovered-${task || execution}`);
      n = attach(b, nodeId, execution, task, "");
      addInput(n, "", `initial-${execution || task}`).extra.source = "历史输入未记录";
    }
    if (n) {
      if (execution && !n.extra.execution_id) n.extra.execution_id = execution;
      if (event.type.endsWith("status")) {
        if (d.status === "running" && n.status !== "running") n.extra.runTs = now;
        n.status = (d.status as GraphNode["status"]) || n.status;
        if (d.attempt_id) n.extra.attempt_id = str(d.attempt_id);
        if (d.score != null) n.extra.score = Number(d.score);
      }
      if (event.type === "node.eval") {
        n.extra.eval_detail = str(d.detail);
        if (typeof d.passed === "boolean") n.extra.passed = d.passed;
        n.status = d.unjudged || d.infra_failure ? "unjudged" : d.passed === true ? "done" : d.passed === false ? "failed" : n.status;
      }
      if (event.type === "node.eval" || event.type.endsWith("status")) {
        const latest = [...f.links].reverse().find(l => l.to === n!.key);
        append(latest?.from ?? n.parent ?? undefined);
      }
      if (event.type === "node.rework") addInput(n, str(d.instruction ?? d.detail), eventId).extra.source = "验收返工";
      else append(f.current[n.key]);
      if (d.score != null) n.extra.score = Number(d.score);
    }
  }
  f.nodes = [...nodes.values()]; return f;
}
const cache = new WeakMap<GraphState, GraphState>();
/** Shared projection for Canvas, details and save. No virtual peers or edge details. */
export function displayGraph(graph: GraphState): GraphState {
  const cached = cache.get(graph); if (cached) return cached;
  const flow = graph.flow ?? flowFromLegacy(graph);
  const projected = { ...graph, flow, nodes: flow.nodes, links: flow.links, a2aEdges: [] };
  cache.set(graph, projected); return projected;
}
