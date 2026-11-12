import { reconcileGraphSnapshot } from "@/lib/graphSnapshot";
// ============================================================
// 全局 store — 单一数据源
// - useSyncExternalStore 订阅（无额外依赖）
// - SSE 事件路由：会话隔离过滤 → 聊天消息 + 图谱 + 日志缓冲
// - 轮询 /api/state：busy/队列/种群/后端重启检测
// - 图谱防抖落盘（POST /api/session/save-graph）
// ============================================================

import { useSyncExternalStore } from "react";
import { api, ApiError } from "@/lib/api";
import { groupLegacySessions } from "@/lib/legacyProjects";
import { eventToMessage, mapSessionMessages, nextMsgId } from "@/lib/chat";
import {
  applyEventToGraph,
  CEO_KEY,
  graphFromSaved,
  graphInit,
  type GraphState,
  type A2aEdge,
} from "@/lib/graph";
import { FLOW_EVENTS, displayGraph, flowFromLegacy, recordFlowEvent } from "@/lib/graphFlow";
import { nodeHistInvalidate } from "@/lib/nodeHistCache";
import { SseClient, type SseStatus } from "@/lib/sse";
import type {
  BusEvent,
  ChatMessage,
  EvolveRound,
  ModelUpdatePayload,
  QueueItem,
  SessionLoadResult,
  SettingsUpdatePayload,
  StateSnapshot,
  ViewId,
} from "@/types";

// ---------------- 类型 ----------------

export interface Toast {
  id: number;
  kind: "info" | "ok" | "warn" | "error";
  text: string;
}

export interface AppState {
  projects: import("@/types").ProjectItem[];
  projectSupport: boolean;
  boot: { status: "loading" | "ok" | "error"; error?: string };
  snap: StateSnapshot | null;
  stateReceivedAt?: number;
  view: ViewId;
  /** 当前查看的会话名；null = 实时视图 */
  viewSession: string | null;
  /** 实时任务当前所属会话（从事件 session 字段观测） */
  liveSession: string | null;
  messages: ChatMessage[];
  graph: GraphState;
  /**
   * 图谱按轮次聚焦（2026-09-04：历史会话跑了多轮，tidy tree 纵向堆叠很高，
   * fit 全图缩到 ~0.2 倍节点不可读 → 用户误以为"丢节点"）。null = 全图；
   * "round-N" = 只 fit 到该轮子树（工具栏下拉切换 / 载入多轮会话默认聚焦最近轮）。
   */
  graphFocusRound: string | null;
  /** 图谱布局模式：radial=行星（子绕父 360° 发散，默认）/ tree=树状（向下分支） */
  graphLayoutMode: "radial" | "tree";
  /**
   * 一键整理三态循环（原始→树状→行星）：进入循环时保存"原始"快照
   * （当时的布局模式 + 手动拖拽偏移），供循环第三态恢复。null=未进入循环。
   */
  layoutCycle: {
    savedMode: "radial" | "tree";
    savedManual: Map<string, { dx: number; dy: number }>;
  } | null;
  /** 节点手动拖拽偏移（2026-09-05）：根节点 key → 整棵子树平移量（跨视图保留，不落盘） */
  graphManual: Map<string, { dx: number; dy: number }>;
  sessionMeta: { name: string; saved_at: string } | null;
  selectedInst: string | null;
  busy: boolean;
  queueLen: number;
  /** A2A 委派交互事件归档版本（delegationEvents 变化时自增，驱动边详情浮层刷新） */
  delegationVersion: number;
  /** 排队中的消息（任务执行中提交；可编辑/撤销） */
  queue: QueueItem[];
  sse: SseStatus;
  evolveLast: EvolveRound | null;
  evolveBanner: EvolveRound | null;
  toasts: Toast[];
}

// ---------------- 节点日志缓冲（高频，独立于 React 状态） ----------------

export interface NodeLogEntry {
  instKey: string;
  nodeId: string;
  phase: string;
  hermes: string[];
  agentlog: string[];
  lastHeartbeat: number;
  lastActive: number;
}

const LOG_LINE_CAP = 1500;

export class LogBuffer {
  private entries = new Map<string, NodeLogEntry>();
  /** profile → 实例 key（克隆 profile 与本体实例的精确映射） */
  private profileMap = new Map<string, string>();
  private listeners = new Set<() => void>();
  private version = 0;
  private notifyTimer: ReturnType<typeof setTimeout> | null = null;

  bindProfile(profile: string, instKey: string): void {
    if (profile) this.profileMap.set(profile, instKey);
    if (this.profileMap.size > 500) this.profileMap.delete(this.profileMap.keys().next().value!);
  }

  /**
   * 日志行归属实例的键（2026-09-03 修复 keying）：
   * 1) 克隆 profile 已绑定 → 精确实例 key（node@task）
   * 2) 事件带 node → 节点 id（direct 执行 profile==node id）
   * 3) 仅带 profile（agentlog/line/heartbeat）→ profile（direct 时即节点 id；
   *    克隆 profile 未绑定时落回节点级，抽屉按 nodeId 兜底展示）
   *
   * 此前把"当前选中实例"当作兜底键：用户开着 A 实例抽屉时，B 节点的日志
   * 全被写进 A 的条目（串台），未选中时写进空键（抽屉永远空白）。
   */
  private resolveInst(nodeId: string, profile: string): string {
    if (profile) {
      const bound = this.profileMap.get(profile);
      if (bound) return bound;
    }
    if (nodeId) return nodeId;
    if (profile) return profile;
    return "";
  }

  append(
    nodeId: string,
    profile: string,
    channel: "hermes" | "agentlog",
    line: string,
    executionId?: string,
  ): void {
    const key = executionId ? `${nodeId}@${executionId}` : this.resolveInst(nodeId, profile);
    if (!key) return;
    let e = this.entries.get(key);
    if (!e) {
      e = {
        instKey: key,
        nodeId,
        phase: "",
        hermes: [],
        agentlog: [],
        lastHeartbeat: 0,
        lastActive: 0,
      };
      this.entries.set(key, e);
      this.trimEntries();
    }
    const arr = e[channel];
    arr.push(line);
    if (arr.length > LOG_LINE_CAP) arr.splice(0, arr.length - LOG_LINE_CAP);
    e.lastActive = Date.now();
    this.version += 1;
    this.emit();
  }

  setPhase(nodeId: string, profile: string, phase: string): void {
    const key = this.resolveInst(nodeId, profile);
    if (!key) return;
    let e = this.entries.get(key);
    if (!e) {
      e = {
        instKey: key,
        nodeId,
        phase,
        hermes: [],
        agentlog: [],
        lastHeartbeat: 0,
        lastActive: 0,
      };
      this.entries.set(key, e);
      this.trimEntries();
    }
    e.phase = phase;
    this.version += 1;
    this.emit();
  }

  heartbeat(nodeId: string, profile: string): void {
    const key = this.resolveInst(nodeId, profile);
    const e = this.entries.get(key);
    if (e) e.lastHeartbeat = Date.now();
    this.version += 1;
    this.emit();
  }

  entry(key: string): NodeLogEntry | undefined {
    return this.entries.get(key);
  }

  /** 供 React：version 作为 snapshot（组件按需读 entry） */
  getVersion(): number {
    return this.version;
  }

  /**
   * 订阅日志变化。必须保持实例绑定（箭头属性）：
   * useSyncExternalStore 会以裸函数方式调用 subscribe，
   * 若是普通类方法则 this 为 undefined，React 树整体崩溃。
   */
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private emit(): void {
    if (this.notifyTimer != null) return;
    this.notifyTimer = setTimeout(() => {
      this.notifyTimer = null;
      for (const fn of this.listeners) fn();
    }, 50);
  }

  private trimEntries(): void {
    if (this.entries.size > 200) this.entries.delete(this.entries.keys().next().value!);
  }

  clear(): void {
    this.entries.clear();
    this.profileMap.clear();
    this.version += 1;
    this.emit();
  }
}

// ---------------- Store ----------------

const MSG_CAP = 500;
const STATE_POLL_MS = 8000;
const GRAPH_SAVE_DEBOUNCE = 1500;
const BANNER_TTL = 9000;
const GRAPH_RELEVANT = new Set([...FLOW_EVENTS,
  "chat.user",
  "graph.task",
  "graph.dispatch",
  "graph.clone",
  "node.status",
  "graph.node_status",
  "node.eval",
  "a2a.delegate",
  "a2a.verdict",
  "a2a.escalate",
]);
const EMPTY_EXECUTION_EVENTS: BusEvent[] = [];

class Store {
  private drafts = new Map<string, string>();
  draftFor(session: string | null): string {
    const key=session ?? 'legacy';
    if (!this.drafts.has(key)) {
      try { this.drafts.set(key,localStorage.getItem(`adgs_draft:${key}`) ?? ''); } catch { /* Storage can be unavailable. */ }
    }
    return this.drafts.get(key) ?? '';
  }
  saveDraft(session: string | null, text: string): void {
    const key=session ?? 'legacy';this.drafts.set(key,text);
    try {
      if(text)localStorage.setItem(`adgs_draft:${key}`,text);
      else localStorage.removeItem(`adgs_draft:${key}`);
    } catch { /* Keep the in-memory draft usable if browser storage is full. */ }
  }
  private loadVersion = 0;
  private stateRequest = 0;
  private lastRunEventAt = 0;
  private lastQueueEventAt = 0;
  private layouts = new Map<string, Pick<AppState, "graphManual" | "graphLayoutMode" | "graphFocusRound">>();
  private lastEventId = 0;
  private executionEvents = new Map<string, BusEvent[]>();
  private executionListeners = new Map<string, Set<() => void>>();
  /** A2A 委派的完整信息交互过程（委派/汇报/复核，按 delegation_id 归档；2026-10-23） */
  private delegationEvents = new Map<string, BusEvent[]>();
  getExecutionEvents(id: string): BusEvent[] { return this.executionEvents.get(id) ?? EMPTY_EXECUTION_EVENTS; }
  /** 某次 A2A 委派的完整交互事件（时间序），供图谱边点击详情 */
  getDelegationEvents(id: string): BusEvent[] {
    return this.delegationEvents.get(id) ?? EMPTY_EXECUTION_EVENTS;
  }
  subscribeExecution(id: string, fn: () => void): () => void {
    const listeners = this.executionListeners.get(id) ?? new Set();
    listeners.add(fn);
    this.executionListeners.set(id, listeners);
    return () => { listeners.delete(fn); if (!listeners.size) this.executionListeners.delete(id); };
  }
  private state: AppState = {
    projects: [], projectSupport: false,
    boot: { status: "loading" },
    snap: null,
    view: "chat",
    viewSession: null,
    liveSession: null,
    messages: [],
    graph: graphInit(),
    graphFocusRound: null,
    graphLayoutMode: "radial",
    layoutCycle: null,
    graphManual: new Map(),
    sessionMeta: null,
    selectedInst: null,
    busy: false,
    queueLen: 0,
    delegationVersion: 0,
    queue: [],
    sse: "connecting",
    evolveLast: null,
    evolveBanner: null,
    toasts: [],
  };
  private listeners = new Set<() => void>();
  private sse: SseClient | null = null;
  private pollTimer: number | null = null;
  private saveTimer: number | null = null;
  private bannerTimer: number | null = null;
  private lastBootTs: number | null = null;
  private toastSeq = 0;
  /** 最近一次本地发送的完整任务文本（chat.user 事件截断时的回填源，2026-09-29） */
  private lastSentUserText: string | null = null;

  readonly logs = new LogBuffer();

  // ---------- 订阅 ----------

  getState(): AppState {
    return this.state;
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private set(partial: Partial<AppState>): void {
    this.state = { ...this.state, ...partial };
    for (const fn of this.listeners) fn();
  }

  // ---------- Toast ----------

  toast(kind: Toast["kind"], text: string): void {
    this.toastSeq += 1;
    const t: Toast = { id: this.toastSeq, kind, text };
    this.set({ toasts: [...this.state.toasts.slice(-3), t] });
    window.setTimeout(() => this.dismissToast(t.id), 4200);
  }

  dismissToast(id: number): void {
    const toasts = this.state.toasts.filter((t) => t.id !== id);
    if (toasts.length !== this.state.toasts.length) this.set({ toasts });
  }

  // ---------- 启动 ----------

  async init(): Promise<void> {
    try {
      const snap = await api.state();
      this.lastBootTs = snap.boot_ts;
      this.set({ snap, stateReceivedAt: Date.now(), boot: { status: "ok" }, busy: snap.busy, queueLen: snap.queue_len, queue: snap.queue ?? [] });
      // 启动时重放最近事件（后端重启前遗留，含当前任务的中间状态）
      const pending = snap.events ?? [];
      if (pending.length) {
        const graph = this.foldGraph(pending);
        const messages = this.foldMessages(pending);
        this.set({ graph, messages });
        for (const ev of pending) {
          if (ev.id) this.lastEventId = Math.max(this.lastEventId, ev.id);
          this.recordExecutionEvent(ev);
        }
      }
    } catch (e) {
      this.set({
        boot: { status: "error", error: e instanceof Error ? e.message : String(e) },
      });
      return;
    }
    this.startSse();
    this.startPolling();
    void this.refreshProjects(true);
  }

  async refreshProjects(restore = false): Promise<void> {
    try {
      const response = await api.projects();
      if (!response.ok) return;
      this.set({projects: response.projects, projectSupport: true});
      if (restore && !this.state.viewSession && this.loadVersion === 0) {
        let saved: string | null = null;
        try { saved = localStorage.getItem("adgs_active_chat"); } catch { /* optional */ }
        const chats = response.projects.filter(p => !p.archived).flatMap(p => p.sessions.filter(c => !c.archived));
        const target = chats.find(c => c.id === saved) ?? chats[0];
        if (target) await this.loadSession(target.id);
      }
    } catch (error) {
      if (this.state.projectSupport || !(error instanceof ApiError) || error.status !== 404) return;
      try {
        const [chats, index] = await Promise.allSettled([api.sessions(), api.legacyWorkspaces()]);
        if (chats.status !== 'fulfilled' || !chats.value.ok) return;
        const projects=groupLegacySessions(chats.value.sessions, index.status === 'fulfilled' ? index.value.workspaces : {});
        this.set({projects});
        if(restore && !this.state.viewSession && this.loadVersion===0) {
          let saved:string|null=null;
          try {saved=localStorage.getItem('adgs_active_chat');} catch { /* Optional. */ }
          if(saved && projects.some(p=>p.sessions.some(c=>c.id===saved)))await this.loadSession(saved);
        }
      } catch { /* Keep the current conversation usable when history is unavailable. */ }
    }
  }

  async createProject(name: string, workspace: string): Promise<void> {
    const result = await api.projectCreate(name, workspace);
    if (!result.ok) throw new Error("项目创建失败");
    await this.refreshProjects();
    await this.createProjectSession(result.project.id);
  }
  async createProjectSession(project: string): Promise<void> {
    const result = await api.projectNewSession(project);
    if (!result.ok) throw new Error("会话创建失败");
    await this.refreshProjects();
    await this.loadSession(result.session.id);
  }
  async renameProject(id: string, name: string): Promise<void> {
    await api.projectRename(id,name); await this.refreshProjects();
  }
  async renameSession(id: string, title: string): Promise<void> {
    await api.sessionRename(id,title); await this.refreshProjects();
  }
  async setProjectArchived(id: string, archived: boolean): Promise<void> {
    await api.projectArchive(id, archived);
    await this.refreshProjects();
    this.toast("ok", archived ? "项目已归档" : "项目已恢复");
  }
  async setSessionArchived(id: string, archived: boolean): Promise<void> {
    await api.sessionArchive(id, archived);
    await this.refreshProjects();
    this.toast("ok", archived ? "会话已归档" : "会话已恢复");
  }
  async guideQueued(id: string): Promise<void> {
    try {
      const result = await api.queueGuide(id);
      if (!result.ok) throw new Error("引导未被接受");
      this.toast("ok", result.started ? "已交给 CEO 继续处理" : "已转为引导：等待本批汇报后交给 CEO");
    } catch(e) { this.toast("error", e instanceof ApiError && e.status === 404 ? "当前后端尚未启用发送后引导，原消息仍保留在队列" : `无法转为引导：${e instanceof Error ? e.message : String(e)}`); }
    void this.refreshState();
  }

  /** 用事件序列重建图谱（纯函数折叠，可测试） */
  foldGraph(events: BusEvent[]): GraphState {
    let g = graphInit();
    for (const ev of events) g = applyEventToGraph(g, ev, ev.ts * 1000);
    return g;
  }

  /** 用事件序列重建聊天消息 */
  foldMessages(events: BusEvent[]): ChatMessage[] {
    const out: ChatMessage[] = [];
    for (const ev of events) {
      const m = eventToMessage(ev);
      if (m) out.push(m);
    }
    return out.slice(-MSG_CAP);
  }

  // ---------- SSE ----------

  private startSse(): void {
    if (this.sse) return;
    this.sse = new SseClient("/api/events", {
      onStatus: (s) => this.set({ sse: s }),
      onEvent: (ev) => this.handleEvent(ev),
    }, this.lastEventId);
    this.sse.start();
  }

  /** SSE 断线后由 UI 触发重建（或看门狗自动） */
  reattachSse(): void {
    this.sse?.destroy();
    this.sse = null;
    this.startSse();
    this.refreshState();
  }

  // ---------- 事件路由 ----------

  handleEvent(ev: BusEvent): void {
    if (ev.id) {
      if (ev.id <= this.lastEventId) return;
      this.lastEventId = ev.id;
    }
    this.recordExecutionEvent(ev);
    const s = this.state;
    const runStarted = ev.type === "chat.user" || ev.type === "chat.recovery_user"
      || (ev.type === "system" && ev.data?.msg === "任务已提交，CEO 开始处理…");
    const runEnded = ev.type === "system" && ev.data?.msg === "当前运行已结束";
    if (s.projectSupport && ev.session && (runStarted || runEnded)) {
      this.set({ projects: s.projects.map(p => ({...p,sessions:p.sessions.map(c => c.id === ev.session ? {...c,busy:runStarted} : c)})) });
      if (runEnded) void this.refreshProjects();
    }
    if (s.projectSupport && !ev.session && FLOW_EVENTS.has(ev.type)) return;
    // 1) 会话观测 + 隔离过滤
    if (ev.session) {
      if (this.state.liveSession !== ev.session) this.set({ liveSession: ev.session });
      if (s.viewSession && ev.session !== s.viewSession) return;
    }
    if ((runStarted || runEnded) && (!s.projectSupport || Boolean(ev.session && ev.session===s.viewSession))) {
      this.lastRunEventAt=ev.ts;
      this.set({busy:runStarted});
    }

    // 2) 聊天消息
    if (ev.type === "chat.ceo_activity") {
      const ceo = ev.data as unknown as import("@/types").CeoActivity;
      const snap = this.state.snap;
      if(ceo.updated_at && snap?.ceo && snap.ceo.execution_id===ceo.execution_id && ceo.updated_at<(snap.ceo.updated_at||0)) return;
      if (snap) this.set({ snap: { ...snap, ceo, ceo_execution_id: ceo.execution_id } });
    }
    let msg = eventToMessage(ev);
    // 长消息截断兜底（2026-09-29）：旧后端的 chat.user 事件只带前 500 字，
    // 用本地已发送的全文回填，气泡显示完整内容（CEO 始终收到全文，仅显示问题）
    if (msg && ev.type === "chat.user" && this.lastSentUserText) {
      const full = this.lastSentUserText;
      const shown = msg.text ?? "";
      if (shown.length < full.length && full.startsWith(shown)) {
        msg = { ...msg, text: full };
      }
    }

    // 3) 图谱
    const d = ev.data ?? {};
    // 队列实时同步（2026-10-22）：入队/出队/编辑/撤销由后端推送完整快照；
    // 运行结束信号立即刷新 busy（不再等 8s 轮询纠正「思考中」残留）
    if (ev.type === "queue.update" && Array.isArray(d.queue)) {
      this.lastQueueEventAt=ev.ts;
      const items = d.queue as QueueItem[];
      const visible = this.state.projectSupport && this.state.viewSession ? items.filter(item => item.session === this.state.viewSession) : items;
      this.set({ queue: visible, queueLen: visible.length });
    }
    if (ev.type === "system" && typeof d.msg === "string" && d.msg.includes("当前运行已结束")) {
      void this.refreshState();
    }

    // 2.5) A2A 委派汇报流（merged_brain）：节点间委派/汇报/复核 → 聊天流可见
    if (ev.type === "a2a.delegate") {
      const parent = String(d.parent ?? "?");
      const child = String(d.node ?? "?");
      const status = String(d.status ?? "running");
      const id8 = String(d.delegation_id ?? "").slice(0, 8);
      const text = status === "running"
        ? `【A2A 委派】${parent} → ${child}（${id8}）：${String(d.summary ?? "").slice(0, 120)}`
        : `【A2A 汇报】${child} → ${parent}（${id8}）：状态 ${status} —— ${String(d.summary ?? "").slice(0, 160)}`;
      msg = { id: nextMsgId(), kind: "system", ts: ev.ts, text };
    } else if (ev.type === "a2a.verdict") {
      const passed = d.passed === true;
      msg = {
        id: nextMsgId(),
        kind: "system",
        ts: ev.ts,
        text: `【A2A 复核】${String(d.parent ?? "?")} 判定 ${String(d.node ?? "?")} 的委派结果：${passed ? "✅ 通过" : "❌ 未通过"}${d.detail ? `（${String(d.detail).slice(0, 120)}）` : ""}`,
      };
    }
    if (msg) this.set({ messages: [...s.messages, msg].slice(-MSG_CAP) });
    const nodeId = d.node != null ? String(d.node) : "";
    const profile = d.profile != null ? String(d.profile) : "";
    if (GRAPH_RELEVANT.has(ev.type)) {
      const graph = applyEventToGraph(s.graph, ev, ev.ts * 1000);
      this.set({ graph });
      this.scheduleGraphSave();
      // 节点有新活动（派发/状态流转/评估）→ 磁盘历史日志的旧结论
      // （含"磁盘无日志"哨兵）过期，抽屉回退展示需重查（2026-09-27 修复：
      // null 哨兵永不失效，节点后来产生了日志仍显示"磁盘无日志"）
      if (nodeId) nodeHistInvalidate(nodeId);
    }

    // 4) 进化轮
    if (ev.type === "evolve.round") {
      const round = ev.data as unknown as EvolveRound;
      this.set({ evolveLast: round, evolveBanner: round });
      if (this.bannerTimer != null) window.clearTimeout(this.bannerTimer);
      this.bannerTimer = window.setTimeout(
        () => this.set({ evolveBanner: null }),
        BANNER_TTL,
      );
      this.refreshState(); // 种群数已变
    }

      // 5) 节点日志缓冲（高频，不进 React 状态；d/nodeId/profile 已在上方定义）
      switch (ev.type) {
      case "node.brain":
        // 大脑决策（节点 decide 的 action+理由）→ 大脑决策通道（web2 同口径：
        // 以 [brain] 前缀行并入节点日志；此前只进聊天流，抽屉"大脑决策"标签
        // 永远只有 agentlog，历史/刷新后全空）
        this.logs.append(
          nodeId,
          "",
          "agentlog",
          `[brain] ${String(d.action ?? "")} ${String(d.detail ?? "")}`.trim(),
          typeof d.execution_id === "string" ? d.execution_id : undefined,
        );
        break;
      case "hermes.line":
        // New executions have a durable attempt stream; do not duplicate every
        // VT chunk into the legacy React log cache for all running workers.
        if (d.execution_id) break;
        this.logs.append(
          nodeId,
          profile,
          "hermes",
          String(d.line ?? ""),
          typeof d.execution_id === "string" ? d.execution_id : undefined,
        );
        break;
      case "hermes.agentlog":
        if (d.execution_id) break;
        this.logs.append(
          nodeId,
          profile,
          "agentlog",
          String(d.line ?? d.text ?? ""),
          typeof d.execution_id === "string" ? d.execution_id : undefined,
        );
        break;
      case "hermes.heartbeat":
        this.logs.heartbeat(nodeId, profile);
        break;
      case "hermes.start":
        this.logs.setPhase(
          nodeId,
          profile,
          String(d.phase ?? "hermes 开始执行"),
        );
        break;
      case "hermes.end":
        this.logs.setPhase(
          nodeId,
          profile,
          String(d.phase ?? "hermes 执行结束"),
        );
        break;
      case "graph.dispatch":
        // 克隆实例的 profile 绑定（graph.clone 随后到达时再精确化）
        if (d.mode === "clone" && d.node) {
          const task = String(d.task ?? "task");
          this.logs.bindProfile(String(d.node), `${String(d.node)}@${task}`);
        }
        break;
      case "graph.clone":
        if (d.node && d.profile) {
          // 克隆 profile → 该节点最近实例
          let instKey = "";
          for (const n of s.graph.nodes) {
            if (n.id === String(d.node) && n.type === "node") instKey = n.key;
          }
          this.logs.bindProfile(String(d.profile), instKey || String(d.node));
        }
        break;
    }
  }

  private recordExecutionEvent(ev: BusEvent): void {
    // A2A 委派交互事件按 delegation_id 归档（图谱边点击详情的数据源）
    if (ev.type === "a2a.delegate" || ev.type === "a2a.verdict") {
      const did = String(ev.data?.delegation_id ?? "");
      if (did) {
        const events = this.delegationEvents.get(did) ?? [];
        if (!(ev.id && events.some((item) => item.id === ev.id))) {
          this.delegationEvents.set(did, [...events, ev]);
          this.set({ delegationVersion: this.state.delegationVersion + 1 });
        }
      }
      return;
    }
    const id = String(ev.data?.execution_id ?? "");
    if (!id || ev.type === "terminal.output" || ev.type === "hermes.line" || ev.type === "hermes.agentlog") return;
    const events = this.executionEvents.get(id) ?? [];
    if (ev.id && events.some((item) => item.id === ev.id)) return;
    const activityTypes = ["terminal.activity", "hermes.heartbeat"];
    const retained = activityTypes.includes(ev.type)
      ? events.filter(item => item.type !== ev.type || item.data?.attempt_id !== ev.data?.attempt_id)
      : events;
    const combined = [...retained, ev];
    const messages = combined.filter(item => item.type === "hermes.message").slice(-100);
    const important = combined.filter(item => item.type !== "hermes.message").slice(-500);
    const keep = new Set([...messages, ...important]);
    this.executionEvents.set(id, combined.filter(item => keep.has(item)));
    for (const fn of this.executionListeners.get(id) ?? []) fn();
    if (this.executionEvents.size > 200) this.executionEvents.delete(this.executionEvents.keys().next().value!);
  }

  // ---------- 轮询 ----------

  private startPolling(): void {
    if (this.pollTimer != null) return;
    const tick = () => {
      void this.refreshState();
      this.pollTimer = window.setTimeout(tick, STATE_POLL_MS);
    };
    this.pollTimer = window.setTimeout(tick, STATE_POLL_MS);
  }

  async refreshState(): Promise<void> {
    try {
      const request = ++this.stateRequest;
      const requestedSession = this.state.viewSession;
      const selected = this.state.projectSupport ? requestedSession : null;
      const snap = selected ? await api.state(selected) : await api.state();
      if (request !== this.stateRequest || requestedSession !== this.state.viewSession) return;
      const cur = this.state;
      if(snap.snapshot_at && snap.snapshot_at < this.lastRunEventAt) snap.busy=cur.busy;
      if(snap.snapshot_at && snap.snapshot_at < this.lastQueueEventAt) { snap.queue=cur.queue; snap.queue_len=cur.queueLen; }
      if (!cur.projectSupport && snap.capabilities?.projects) void this.refreshProjects();
      // 后端重启检测：CEO 上下文与事件历史已清空 → 本地同步重置
      if (this.lastBootTs != null && snap.boot_ts !== this.lastBootTs) {
        this.lastBootTs = snap.boot_ts;
        if (cur.projectSupport && selected) {
          this.lastEventId = snap.event_id ?? this.lastEventId;
          this.sse?.destroy(); this.sse = null; this.startSse();
          void this.loadSession(selected); void this.refreshProjects();
          return;
        }
        this.set({
          graph: graphInit(),
          messages: [
            {
              id: nextMsgId(),
              kind: "system",
              ts: Date.now() / 1000,
              text: "检测到后端重启：CEO 上下文与事件历史已清空，图谱已同步重置",
            },
          ],
          viewSession: null,
          sessionMeta: null,
          selectedInst: null,
          evolveLast: null,
          evolveBanner: null,
          graphFocusRound: null,
          graphLayoutMode: "radial",
          layoutCycle: null,
          graphManual: new Map(),
        });
        this.logs.clear();
        this.lastEventId = 0;
        this.executionEvents.clear();
        this.reattachSse();
      }
      if (snap.ceo && (snap.ceo.updated_at || 0) < (this.state.snap?.ceo?.updated_at || 0)) {
        snap.ceo = this.state.snap?.ceo;
        snap.ceo_execution_id = snap.ceo?.execution_id;
      }
      const graph = reconcileGraphSnapshot(this.state.graph, snap);
      this.set({
        snap,
        stateReceivedAt: Date.now(),
        graph,
        busy: snap.busy,
        queueLen: snap.queue_len,
        queue: snap.queue ?? [],
        boot: { ...cur.boot, status: "ok" },
      });
      if (snap.runs) this.set({projects:this.state.projects.map(p=>({...p,sessions:p.sessions.map(c=>({...c,busy:!!snap.runs?.[c.id]?.busy}))}))});
    } catch {
      // 轮询失败不打扰用户（SSE 断线指示器会显示）
    }
  }

  // ---------- 图谱落盘 ----------

  private scheduleGraphSave(): void {
    if (this.saveTimer != null) window.clearTimeout(this.saveTimer);
    this.saveTimer = window.setTimeout(() => void this.saveGraphNow(), GRAPH_SAVE_DEBOUNCE);
  }

  /** 将当前图谱保存到服务端当前会话（/api/session/graph 无目标参数）
   *  规则：
   *  - 历史视图（查看已载会话）：仅当任务事件确认 liveSession == 该会话时才保存
   *    （即「继续已载会话」场景，服务端当前会话正是它）
   *  - 实时视图：需已观测到 liveSession（会话文件已创建）
   *  - 其余情况不保存，避免把重建/空图谱写错会话
   */
  async saveGraphNow(): Promise<void> {
    const s = this.state;
    if (s.viewSession && s.liveSession !== s.viewSession) return;
    const name = s.viewSession ?? s.liveSession;
    if (!name) return;
    if (s.graph.nodes.length <= 1) return; // 只有 CEO 节点，无需保存
    try {
      const saved = displayGraph(s.graph).nodes.map((n) => ({
        key: n.key,
        id: n.id,
        type: n.type,
        label: n.label,
        parent: n.parent,
        status: n.status,
        extra: n.key === CEO_KEY ? { ...n.extra, flow_snapshot: displayGraph(s.graph).flow } : n.extra,
      }));
      await api.sessionSaveGraph(saved, name);
    } catch {
      /* 落盘失败静默（后端有倒退防护，可再试） */
    }
  }

  // ---------- 动作 ----------

  setView(view: ViewId): void {
    if (view !== this.state.view) this.set({ view });
  }

  selectInst(key: string | null): void {
    this.set({ selectedInst: key });
  }

  /** 图谱轮次聚焦：null = 全图；"round-N" = 只 fit 该轮子树 */
  setGraphFocusRound(key: string | null): void {
    if (this.state.graphFocusRound !== key) this.set({ graphFocusRound: key });
  }

  /** 图谱布局模式：radial=行星 / tree=树状 */
  setGraphLayoutMode(mode: "radial" | "tree"): void {
    if (this.state.graphLayoutMode !== mode) this.set({ graphLayoutMode: mode });
  }

  /**
   * 一键整理三态循环（v1 btn-layout 同款语义）：原始 → 树状 → 行星 → 原始 …
   *  - 第一次点击：保存当前(模式+手动偏移)为"原始"快照，切到树状
   *  - 第二次：切到行星（清除手动拖拽，纯自动布局）
   *  - 第三次：恢复"原始"快照（模式 + 手动偏移）
   *  - 之后循环。返回本次落到的状态（供 UI 提示）。
   */
  cycleLayout(): "tree" | "radial" | "original" {
    const s = this.state;
    if (!s.layoutCycle) {
      this.set({
        layoutCycle: {
          savedMode: s.graphLayoutMode,
          savedManual: new Map(s.graphManual),
        },
        graphLayoutMode: "tree",
        graphManual: new Map(),
      });
      return "tree";
    }
    if (s.graphLayoutMode === "tree") {
      this.set({ graphLayoutMode: "radial", graphManual: new Map() });
      return "radial";
    }
    // 当前 radial → 恢复原始
    const saved = s.layoutCycle;
    this.set({
      graphLayoutMode: saved.savedMode,
      graphManual: new Map(saved.savedManual),
      layoutCycle: null,
    });
    return "original";
  }

  /** 历史委派边注入（GraphView 挂载时从 /api/mesh/delegations 恢复）：
   *  按 delegationId 去重合并，running 状态优先保留现有边 */
  injectA2aEdges(edges: A2aEdge[]): void {
    if (!edges.length) return;
    const g = this.state.graph;
    const byId = new Map(g.a2aEdges.map((e) => [e.delegationId, e]));
    for (const e of edges) {
      const existing = byId.get(e.delegationId);
      byId.set(e.delegationId, existing ? { ...existing, ...e } : { ...e });
    }
    const merged = [...byId.values()].sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0));
    this.set({ graph: { ...g, a2aEdges: merged } });
  }

  /** 节点手动拖拽偏移：整体替换（拖动中每帧只改 canvas 内部，松手才落 store） */
  setGraphManual(map: Map<string, { dx: number; dy: number }>): void {
    this.set({ graphManual: new Map(map) });
  }

  /** 清除所有手动拖拽（"整理布局"按钮） */
  clearGraphManual(): void {
    if (this.state.graphManual.size) this.set({ graphManual: new Map() });
  }

  /** 会话内的轮次节点 key 列表（按 roundSeq 升序），供工具栏下拉 */
  roundKeys(): string[] {
    const keys = displayGraph(this.state.graph).nodes
      .filter((n) => n.type === "round" && n.parent === CEO_KEY)
      .map((n) => n.key)
      .sort((a, b) => {
        const sa = Number(/round-(\d+)$/.exec(a)?.[1] ?? 0);
        const sb = Number(/round-(\d+)$/.exec(b)?.[1] ?? 0);
        return sa - sb;
      });
    return keys;
  }

  async sendChat(text: string, mode: "queue" | "interrupt" = "queue"): Promise<boolean> {
    const t = text.trim();
    if (!t) return false;
    this.lastSentUserText = t;
    try {
      if (this.state.projectSupport && !this.state.viewSession) {
        const project = this.state.projects.find(p => !p.archived);
        if (!project) throw new Error("请先创建项目");
        await this.createProjectSession(project.id);
      }
      const session = this.state.viewSession ?? this.state.liveSession ?? undefined;
      const res = mode === "interrupt" ? await api.chat(t, { mode, session }) : this.state.projectSupport ? await api.chat(t, {session}) : await api.chat(t);
      if (!res.ok) throw new Error(res.error ?? "消息未被接受");
      if (res.queued) {
        if (mode === "interrupt" && res.mode !== "interrupt") {
          this.toast("warn", "当前后端尚未支持打断引导，此消息已按普通排队保存；后端更新重启后可使用引导模式");
          void this.refreshState();
          return true;
        }
        this.toast("warn", mode === "interrupt" ? "引导已入队：当前已派发任务汇报后交给 CEO，在途工作继续执行" : "任务已排队：当前任务结束后执行，可在队列编辑或撤销");
      }
    } catch (e) {
      this.toast("error", `提交失败：${e instanceof Error ? e.message : String(e)}`);
      return false;
    }
    void this.refreshState();
    void this.refreshProjects();
    return true;
  }

  /** 编辑一条排队消息的文本（仅排队中可改） */
  async queueUpdate(id: string, text: string): Promise<void> {
    const t = text.trim();
    if (!t) {
      this.toast("warn", "消息内容不能为空");
      return;
    }
    try {
      const res = await api.queueUpdate(id, t);
      if (!res.ok) throw new Error(res.error ?? "修改失败");
      this.toast("ok", "排队消息已修改");
    } catch (e) {
      this.toast("error", `修改失败：${e instanceof Error ? e.message : String(e)}`);
    }
    void this.refreshState();
  }

  /** 撤销一条排队消息（不执行） */
  async queueRemove(id: string): Promise<void> {
    try {
      const res = await api.queueRemove(id);
      if (!res.ok) throw new Error(res.error ?? "撤销失败");
      this.toast("ok", "已撤销排队消息");
    } catch (e) {
      this.toast("error", `撤销失败：${e instanceof Error ? e.message : String(e)}`);
    }
    void this.refreshState();
  }

  async sendCommand(text: string): Promise<void> {
    try {
      const res = this.state.projectSupport && this.state.viewSession ? await api.command(text, this.state.viewSession) : await api.command(text);
      this.pushLocal("system", res.message ?? "指令已执行");
      void this.refreshState();
    } catch (e) {
      this.toast("error", `指令执行失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  /** 后端指令回执以 system 事件推过来；这里在失败/无事件时本地补一条 */
  pushLocal(kind: ChatMessage["kind"], text: string): void {
    const m: ChatMessage = {
      id: nextMsgId(),
      kind,
      ts: Date.now() / 1000,
      text,
    };
    this.set({ messages: [...this.state.messages, m].slice(-MSG_CAP) });
  }

  async newSession(): Promise<void> {
    try {
      await api.sessionNew();
      this.set({
        graph: graphInit(),
        messages: [
          {
            id: nextMsgId(),
            kind: "system",
            ts: Date.now() / 1000,
            text: "已新建对话（CEO 上下文已重置）",
          },
        ],
        viewSession: null,
        sessionMeta: null,
        selectedInst: null,
        liveSession: null,
        graphFocusRound: null,
        graphLayoutMode: "radial",
        layoutCycle: null,
        graphManual: new Map(),
      });
    } catch (e) {
      this.toast("error", `新建对话失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async loadSession(name: string): Promise<void> {
    const version = ++this.loadVersion;
    this.lastRunEventAt=0; this.lastQueueEventAt=0;
    const openedSession = this.state.projects.flatMap(project => project.sessions).find(session => session.id === name);
    if (this.state.projectSupport) {
      const current = this.state.viewSession;
      if (current && this.state.sessionMeta?.name === current) this.layouts.set(current, {graphManual:this.state.graphManual,graphLayoutMode:this.state.graphLayoutMode,graphFocusRound:this.state.graphFocusRound});
      this.set({viewSession:name,messages:[],graph:graphInit(),selectedInst:null,queue:[],queueLen:0,busy:!!this.state.projects.flatMap(p=>p.sessions).find(c=>c.id===name)?.busy});
    }
    try {
      const res: SessionLoadResult = await api.sessionLoad(name);
      if (version !== this.loadVersion) return;
      // 2026-09-03：后端把 CEO 话术（ask/report/finish）还原为对应 kind，
      // 历史恢复能看到 CEO 提问/进度汇报/最终结论，不再只有工具
      const ownEvents = (res.events ?? []).filter(e => !e.session || e.session === name);
      const messages = mapSessionMessages(res.messages ?? [], ownEvents);
      const lastTurnEvent = [...(res.events ?? [])].reverse().find(e =>
        (!e.session || e.session === name) && ['chat.user','chat.recovery_user','chat.ceo_finish','chat.ceo_end'].includes(e.type));
      if (lastTurnEvent?.type === 'chat.ceo_end') {
        const ending = eventToMessage(lastTurnEvent);
        if (ending && messages[messages.length - 1]?.text !== ending.text) messages.push(ending);
      }
      let graph = graphFromSaved(res.graph ?? []);
      // A saved browser projection can contain a bug from an older open tab.
      // Rebuild it from the complete durable transcript rather than trusting its
      // seen-event cache, which would otherwise retain corrupted task inputs.
      const firstInputOrDispatch = ownEvents.find(e => ["chat.user", "graph.task", "graph.dispatch", "graph.instruction", "a2a.delegate"].includes(e.type));
      if (firstInputOrDispatch?.type === "chat.user" || (!graph.flow && ownEvents.some(e => e.type === "chat.user"))) {
        graph = { ...graph, flow: flowFromLegacy(graphInit()) };
      }
      // 磁盘图谱可能落后于正在执行的会话。这里只补结构与状态，
      // 不重新插入聊天/日志，也不推进全局 SSE 游标（此数组不是完整事件流）。
      for (const ev of res.events ?? []) {
        if (ev.session && ev.session !== name) continue;
        // a2a.* 一并重放:载入历史会话时重建 A2A 委派边(2026-10-23)
        if (!FLOW_EVENTS.has(ev.type)) continue;
        if (ev.type === "chat.user") graph = { ...graph, flow: recordFlowEvent(graph.flow, ev, graph) };
        else graph = applyEventToGraph(graph, ev, ev.ts * 1000);
        if (ev.type.startsWith("a2a.")) this.recordExecutionEvent(ev);
      }
      if (!res.running && graph.flow) graph = { ...graph, flow: { ...graph.flow, nodes: graph.flow.nodes.map(n =>
        ["waiting", "running", "delegating"].includes(n.status) ? { ...n, status: "unknown" as const } : n) } };
      // 2026-09-04：历史会话跑了很多轮 → tidy tree 纵向很高，fit 全图缩到
      // ~0.2 倍节点不可读（用户误以为"丢节点"）。轮次 >3 时默认聚焦最近一轮，
      // 让载入即可读；用户可在图谱工具栏切"全部"或指定轮次。
      const roundKeys = displayGraph(graph).nodes
        .filter((n) => n.type === "round" && n.parent === CEO_KEY)
        .map((n) => n.key)
        .map((k) => ({ k, seq: Number(/round-(\d+)$/.exec(k)?.[1] ?? 0) }))
        .sort((a, b) => a.seq - b.seq);
      const graphFocusRound =
        roundKeys.length > 3 ? roundKeys[roundKeys.length - 1].k : null;
      this.set({
        graph,
        messages,
        viewSession: name,
        snap: this.state.snap ? { ...this.state.snap, ceo: undefined, ceo_execution_id: null } : null,
        sessionMeta: { name, saved_at: "" },
        selectedInst: null,
        view: "chat",
        graphFocusRound,
        graphLayoutMode: "radial",
        layoutCycle: null,
        graphManual: new Map(),
      });
      if (this.state.projectSupport && openedSession?.completed) {
        void api.sessionMarkViewed(name).then(() => {
          this.set({projects: this.state.projects.map(project => ({
            ...project,
            sessions: project.sessions.map(session => session.id === name
              ? {...session, completion_seen: true}
              : session),
          }))});
        }).catch(() => { /* Viewing remains usable when the metadata endpoint is unavailable. */ });
      }
      if (this.state.projectSupport) {
        const layout = this.layouts.get(name);
        if (layout) this.set(layout);
      }
      try { localStorage.setItem("adgs_active_chat", name); } catch { /* optional */ }
      this.toast("ok", this.state.projectSupport ? "已切换会话" : `已载入会话 ${name}`);
      void this.refreshState();
    } catch (e) {
      if (version !== this.loadVersion) return;
      this.toast("error", `载入失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async runEvolve(): Promise<void> {
    this.toast("info", "进化已触发：繁衍 + 淘汰进行中…");
    try {
      const res = await api.evolve();
      if (!res.ok) {
        this.toast("error", res.error ?? "进化失败");
      } else {
        this.toast("ok", "进化完成");
      }
    } catch (e) {
      this.toast("error", `进化失败：${e instanceof Error ? e.message : String(e)}`);
    }
    void this.refreshState();
  }

  /** 保存模型配置（CEO/决策大脑立即热生效；hermes 仅对之后新建的节点生效） */
  async saveModels(payload: ModelUpdatePayload): Promise<void> {
    try {
      const res = await api.updateModels(payload);
      if (!res.ok) throw new Error(res.error ?? "保存失败");
      this.toast("ok", res.msg ?? "模型配置已保存");
    } catch (e) {
      this.toast("error", `模型配置保存失败：${e instanceof Error ? e.message : String(e)}`);
      throw e;
    }
    void this.refreshState();
  }

  /** 保存运行/调优参数（并行上限等立即生效；进化参数下一轮生效） */
  async saveSettings(payload: SettingsUpdatePayload): Promise<void> {
    try {
      const res = await api.updateSettings(payload);
      if (!res.ok) throw new Error(res.error ?? "保存失败");
      this.toast("ok", res.msg ?? "参数已保存");
    } catch (e) {
      this.toast("error", `参数保存失败：${e instanceof Error ? e.message : String(e)}`);
      throw e;
    }
    void this.refreshState();
  }

  // ---------- 节点管理 ----------

  async nodeAction(id: string, action: string, detail?: string): Promise<void> {
    try {
      switch (action) {
        case "describe": {
          const res = await api.nodeDescribe(id, detail ?? "");
          if (!res.ok) throw new Error(res.error ?? res.message ?? "操作失败");
          this.toast("ok", `${id} 生态位描述已更新`);
          break;
        }
        case "skill-remove": {
          const res = await api.nodeSkillRemove(id, detail ?? "");
          if (!res.ok) throw new Error(res.error ?? res.message ?? "操作失败");
          this.toast("ok", `${id} 技能 ${detail} 已移除`);
          break;
        }
        case "skill-install": {
          // detail 格式：fromNode::skillName
          const [from, name] = (detail ?? "").split("::");
          const res = await api.nodeSkillInstall(id, from ?? "", name ?? "");
          if (!res.ok) throw new Error(res.error ?? res.message ?? "操作失败");
          this.toast("ok", `${id} 已移植技能 ${name}（来自 ${from}）`);
          break;
        }
        case "revive": {
          const res = await api.nodeRevive(id);
          if (!res.ok) throw new Error(res.error ?? res.message ?? "操作失败");
          this.toast("ok", `${id} 已复活回活动池`);
          break;
        }
        case "remove": {
          const res = await api.nodeRemove(id);
          if (!res.ok) throw new Error(res.error ?? "操作失败");
          this.toast("ok", `${id} 已移除`);
          break;
        }
        default:
          throw new Error(`未知操作 ${action}`);
      }
      void this.refreshState();
    } catch (e) {
      this.toast("error", `${id} 操作失败：${e instanceof Error ? e.message : String(e)}`);
      throw e;
    }
  }
}

export const store = new Store();

// ---------------- React Hook ----------------

export function useApp<T>(selector: (s: AppState) => T): T {
  return useSyncExternalStore(
    store.subscribe,
    () => selector(store.getState()),
    () => selector(store.getState()),
  );
}

/** 组件级：选择器式订阅（默认整个 state；选择器必须返回稳定引用） */
export function useAppState<T = AppState>(selector?: (s: AppState) => T): T {
  const get = () =>
    (selector ? selector(store.getState()) : store.getState()) as T;
  return useSyncExternalStore(store.subscribe, get, get);
}

// ---------------- 卸载清理（图谱落盘） ----------------

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", () => {
    void store.saveGraphNow();
  });
}

// re-export 便于引用
export { CEO_KEY };
