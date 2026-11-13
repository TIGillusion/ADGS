// ============================================================
// ADGS web3 — 后端契约类型定义
// 与 web_app.py / web/event_bus.py / core/*.py 对齐
// ============================================================

// ---------------- 事件 ----------------

export type EventType =
  | "chat.user"
  | "chat.ceo_ask"
  | "chat.ceo_tool"
  | "chat.ceo_report"
  | "chat.ceo_finish"
  | "chat.ceo_end"
  | "graph.task"
  | "graph.dispatch"
  | "graph.node_status"
  | "graph.child"
  | "graph.clone"
  | "node.status"
  | "node.brain"
  | "node.eval"
  | "node.delegate"
  | "hermes.start"
  | "hermes.line"
  | "hermes.end"
  | "hermes.heartbeat"
  | "hermes.agentlog"
  | "evolve.round"
  | "a2a.delegate"
  | "a2a.verdict"
  | "a2a.escalate"
  | "ceo.settle"
  | "system";

export interface BusEvent {
  /** Durable global event cursor. Legacy records may not have one. */
  id?: number;
  type: EventType | string;
  ts: number;
  data: Record<string, unknown>;
  /** 任务执行期间由后端附加：该事件属于哪个会话（用于会话隔离过滤） */
  session?: string;
}

export interface ExecutionAttempt {
  attempt_id: string;
  state?: string;
  phase?: string;
  cols: number;
  rows: number;
  last_seq?: number;
  started_at?: number;
  ended_at?: number | null;
  exit_code?: number | null;
  continuation?: "start" | "continue" | "resume";
}

export interface ExecutionDetail {
  execution_id: string;
  node_id: string;
  profile: string;
  task_id: string;
  run_id: string;
  state: string;
  phase?: string;
  waiting_for_children?: boolean;
  waiting_for_slot?: boolean;
  runtime_active?: boolean;
  hermes_process_alive?: boolean;
  hermes_process_state?: "working" | "waiting_input" | "waiting_dispatch" | "exited";
  started_at?: number;
  ended_at?: number | null;
  last_activity_at?: number;
  session_id?: string;
  attempts: ExecutionAttempt[];
  events?: BusEvent[];
}

export interface TerminalRecord {
  type: "data" | "resize" | "lifecycle" | "end";
  seq: number;
  attempt_id: string;
  data?: string;
  cols?: number;
  rows?: number;
  exit_code?: number;
}

// ---------------- 节点 ----------------

export type NodeBirthType = "initial" | "recombine" | "whiteboard";

export interface NodeSummary {
  id: string;
  describe: string;
  score: number;
  success_rate: number;
  tasks_done: number;
  gen: number;
  birth_type: NodeBirthType;
  /** /api/nodes 附加字段 */
  parents?: string[];
  status?: string;
  created_at?: number;
  updated_at?: number;
  skills?: string[];
  /** 该节点 hermes 钉住的执行模型（出生时快照；旧节点保持旧模型） */
  model?: string;
}

export interface NodeDetail extends NodeSummary {
  archived_at?: number | null;
}

/** 磁盘历史日志尾部（GET /api/node/<id>/logs）：文件不存在时该通道为 null */
export interface NodeLogFile {
  lines: string[];
  total_lines: number;
  mtime: number;
  size: number;
  truncated: boolean;
}

export interface NodeLogsResult {
  ok: boolean;
  node?: string;
  hermes?: NodeLogFile | null;
  agentlog?: NodeLogFile | null;
}

export interface SkillInfo {
  name: string;
  desc: string;
  content: string;
}

// ---------------- /api/state ----------------

/** 排队中的用户消息（任务执行中提交；可编辑/撤销，2026-10-22） */
export type QueueMode = "queue" | "interrupt";

export interface QueueItem {
  mode?: QueueMode;
  id: string;
  text: string;
  session: string | null;
  queued_at?: number;
}

export interface ProjectSession {
  id: string; project_id: string; title: string; created_at: number; updated_at: number;
  archived?: boolean | number; busy?: boolean; completed?: boolean; completion_seen?: boolean | number;
  terminal_status?: "completed" | "blocked" | "failed" | "interrupted" | "exception" | "limit" | "paused" | null;
}
export interface ProjectItem {
  id: string; name: string; workspace: string; sessions: ProjectSession[]; created_at?: number;
  archived?: boolean | number;
}

export interface CeoActivity {
  engine: string; role: string; phase: string; model?: string;
  execution_id?: string | null; attempt_id?: string; hermes_session_id?: string | null;
  detail?: string;
  updated_at?: number;
}

export interface StateSnapshot {
  snapshot_at?: number;
  executions?: ExecutionStateSnapshot[];
  ceo?: CeoActivity;
  event_id?: number;
  ceo_execution_id?: string | null;
  worker_hold?: boolean;
  capabilities?: { projects?: boolean; concurrent_sessions?: boolean; queue_guidance?: boolean };
  runs?: Record<string, { busy: boolean; workspace?: string; started_at?: number; recovery_execution?: string }>;
  ok: boolean;
  workspace: string;
  population: { active: number; capacity: number };
  models: { ceo: string; brain: string; worker: string };
  /** 全局并行上限（2026-09-28 系统设置；概览卡片展示） */
  runtime?: { max_workers: number };
  /** 调优摘选（概览卡片展示）：生存压力 / 探索系数 */
  advanced?: { tau: number; explore_beta: number };
  nodes: NodeSummary[];
  events: BusEvent[];
  busy: boolean;
  queue_len: number;
  queue?: QueueItem[];
  boot_ts: number;
}

export interface ExecutionStateSnapshot {
  execution_id: string;
  node_id: string;
  state: string;
  phase?: string;
  runtime_active?: boolean;
  waiting_for_children?: boolean;
  waiting_for_slot?: boolean;
  updated_at?: number;
}

// ---------------- 模型配置（2026-09-27 web 配置入口） ----------------

/** 单组模型配置（GET /api/models；不含 key，密钥不回传前端） */
export interface ModelGroupInfo {
  model: string;
  url: string;
}

export type ModelGroupId = "ceo" | "brain" | "worker";

/** POST /api/models 请求体：每组字段均可省略，省略 = 保持不变；key 留空 = 不修改 */
export interface ModelUpdatePayload {
  ceo?: Partial<{ model: string; url: string; key: string }>;
  brain?: Partial<{ model: string; url: string; key: string }>;
  worker?: Partial<{ model: string; url: string; key: string }>;
}

// ---------------- 运行/调优参数（2026-09-28 web 系统设置） ----------------

/** 运行参数：并行上限 / 委派深度 / 超时（GET /api/settings.runtime） */
export interface RuntimeInfo {
  max_workers: number;
  max_delegation_depth: number;
  worker_timeout: number;
  inactivity_timeout: number;
}

/** 进化/路由调优：生存压力、分配权重、探索系数、采样（GET /api/settings.advanced） */
export interface AdvancedInfo {
  tau: number;
  sim_weight: number;
  score_weight: number;
  explore_beta: number;
  top_k: number;
  temperature: number;
}

export interface SettingsInfo {
  runtime: RuntimeInfo;
  advanced: AdvancedInfo;
}

/** POST /api/settings 请求体：字段省略 = 保持不变 */
export interface SettingsUpdatePayload {
  runtime?: Partial<RuntimeInfo>;
  advanced?: Partial<AdvancedInfo>;
}

// ---------------- 会话 ----------------

export interface SessionListItem {
  workspace?: string;
  name: string;
  saved_at: string;
  msg_count: number;
  preview: string;
  completed?: boolean;
  completion_seen?: boolean | number;
  terminal_status?: ProjectSession["terminal_status"];
}

export interface SessionGraphItem {
  name: string;
  saved_at: string;
  preview: string;
  graph: SavedGraphNode[];
}

export interface SessionMessage {
  /** Original send time when recorded; absent legacy times remain unknown. */
  ts?: number;
  /** 2026-09-03：后端把 CEO 话术工具（ask_user/report/finish）还原为
   *  对应话术 kind，历史恢复不再退化成工具条目；旧会话文件仍只有
   *  user/text/tool 三种 */
  kind: "user" | "text" | "tool" | "ask" | "report" | "finish";
  text?: string;
  status?: string;
  name?: string;
  args?: unknown;
  result?: string;
  /** report: 下一步计划 */
  reason?: string;
  /** finish: 工作流描述 */
  workflow?: string;
  /** finish: 交付物 */
  artifacts?: string[];
}

export interface SessionLoadResult {
  ok: boolean;
  msg?: string;
  error?: string;
  busy: boolean;
  running: boolean;
  messages: SessionMessage[];
  graph: SavedGraphNode[];
  events?: BusEvent[];
}

// ---------------- 图谱（前端自持 + 历史会话共用） ----------------

export type GraphNodeType = "ceo" | "round" | "task" | "node";

/** 节点执行状态 */
export type GraphNodeStatus =
  | "idle"
  | "waiting"
  | "running"
  | "delegating"
  | "evaluating"
  | "waiting_user"
  | "done"
  | "failed"
  | "interrupted"
  | "unknown"
  | "unjudged"
  | "blocked";

export interface GraphNode {
  /** 实例 key：CEO/任务用自身 id；执行节点 = `${node_id}@${task_id}` */
  key: string;
  id: string;
  type: GraphNodeType;
  label: string;
  parent: string | null;
  status: GraphNodeStatus;
  extra: {
    operations?: BusEvent[];
    contracts?: import("@/lib/graphFlow").FlowContract[];
    flow_snapshot?: import("@/lib/graphFlow").FlowSnapshot;
    instruction?: string;
    source?: string;
    last_input_event?: string;
      active_batch?: string;
      statusTs?: number;
    execution_id?: string;
    attempt_id?: string;
    run_id?: string;
    task_id?: string;
    goal?: string;
    overall_goal?: string;
    user_text?: string;
    seq?: number;
    count?: number;
    score?: number | null;
    reworks?: number;
    passed?: boolean;
    profile?: string;
    brain?: string;
    brain_detail?: string;
    eval_detail?: string;
    task_title?: string;
    mode?: "direct" | "clone";
    ceo_first?: string;
    ceo_activity?: CeoActivity;
    /** 最近一次转入 running 的服务端时间戳（ms，事件 ts）——执行时长计时起点 */
    runTs?: number;
  };
  /** 首次出现时间（用于动画与排序） */
  bornTs?: number;
}

/** 历史会话存储的图谱节点（兼容 web/web2 格式：含 x/y/tx/ty/pinned） */
export interface SavedGraphNode {
  key: string;
  id: string;
  type: string;
  label: string;
  parent: string;
  status: string;
  x?: number;
  y?: number;
  tx?: number;
  ty?: number;
  pinned?: boolean;
  extra?: Record<string, unknown>;
}

// ---------------- 进化 ----------------

export interface EvolveBirth {
  id: string;
  type: string;
  score?: number;
  parents?: string[];
  skills?: string[];
}

export interface EvolveElimination {
  id: string;
  describe?: string;
}

export interface EvolveRound {
  n_before: number;
  n_after: number;
  pressure: number;
  effective_capacity?: number;
  elimination_target?: number;
  births: EvolveBirth[];
  eliminations: EvolveElimination[];
  pop_avg_score?: number;
  elim_avg_score?: number;
  score_gap?: number;
}

// ---------------- 聊天消息（前端渲染单元） ----------------

export type ChatMsgKind =
  | "user"
  | "text"
  | "system"
  | "ask"
  | "tool"
  | "report"
  | "finish"
  | "end"
  | "brain"
  | "eval"
  | "collab";

export interface ChatMessage {
  id: string;
  kind: ChatMsgKind;
  ts: number;
  /** 通用文本 */
  text?: string;
  /** tool: 工具名 */
  name?: string;
  /** tool: 参数（JSON 对象） */
  args?: unknown;
  /** tool: 结果摘要 */
  result?: string;
  /** finish: 工作流描述 */
  workflow?: string;
  /** finish: 交付物 */
  artifacts?: string[];
  /** end: 状态 */
  status?: string;
  /** end: 原因 */
  reason?: string;
  /** 关联节点（brain/eval） */
  node?: string;
  /** brain: 决策动作 */
  action?: string;
  /** eval: 是否通过 */
  passed?: boolean;
  /** eval: 分数 */
  score?: number;
  /** collab: 协作双方（委派方/被委派方/打分方…语义随子类型） */
  from?: string;
  to?: string;
  /** collab: 分数修正量（verdict/escalate/settle） */
  delta?: number;
  /** collab: 子类型（delegate/verdict/escalate/settle） */
  collabKind?: string;
}

// ---------------- A2A 协作档案 ----------------

export interface MeshDelegationVerdict {
  passed: boolean;
  note?: string;
  score?: number | null;
  at?: number;
}

export interface MeshDelegationEscalation {
  by: string;
  passed: boolean;
  score: number;
  note?: string;
  at?: number;
}

export interface MeshDelegation {
  delegation_id: string;
  parent_execution_id?: string;
  parent_node_id?: string;
  child_node_id?: string;
  task_md?: string;
  status: string;
  shared_resources?: string[];
  created_at?: number;
  ended_at?: number;
  summary?: string;
  deliverables?: string[];
  verdict?: MeshDelegationVerdict | null;
  escalations?: MeshDelegationEscalation[];
}

// ---------------- 侧栏/导航 ----------------

export type ViewId =
  | "overview"
  | "chat"
  | "graph"
  | "nodes"
  | "sessions";
