import { executionGraphStatus, GRAPH_STATUS } from "@/lib/executionState";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { ArrowDown, CheckCircle2, Copy, Download, Maximize2, Minus, Plus, Search, Terminal as TerminalIcon, X } from "lucide-react";
import type { Terminal } from "@xterm/xterm";
import type { SearchAddon } from "@xterm/addon-search";
import { api } from "@/lib/api";
import { fmtAgo, fmtDuration, fmtTime, nodeIdLabel } from "@/lib/format";
import { store } from "@/lib/store";
import { TerminalStream } from "@/lib/terminalStream";
import { CEO_PHASES, ceoPhaseLabel, ceoGraphStatus } from "@/lib/ceo";
import type { CeoActivity } from "@/types";
import type { BusEvent, ExecutionAttempt, ExecutionDetail, TerminalRecord } from "@/types";

const EVENT_LABELS: Record<string, string> = {
  "node.brain": "节点决策", "node.status": "执行状态", "node.eval": "验收结论", "node.rework": "补充与返工原因",
  "node.delegate": "委派子任务", "hermes.start": "Hermes 启动", "hermes.end": "Hermes 退出",
  "hermes.heartbeat": "进程心跳", "tool.start": "工具开始", "tool.end": "工具结束",
  "execution.status": "执行状态", "execution.activity": "工具活动",
  "terminal.started": "终端启动", "terminal.ended": "终端结束", "terminal.activity": "执行活动",
  "terminal.stage": "终端阶段", "terminal.diagnostic": "终端诊断", "hermes.message": "Hermes 会话消息",
  "chat.ceo_activity": "CEO 协调阶段",
};
const STATE_LABELS: Record<string, string> = {
  waiting: "等待执行", queued: "排队中", running: "执行中", evaluating: "验收中",
  done: "已完成", succeeded: "已完成", failed: "未通过", interrupted: "执行中断",
  unknown: "待核实", unjudged: "暂无法验收", blocked: "受阻", delegating: "委派中",
  passed: "验收通过", completed: "执行结束", starting: "启动中", stopping: "停止中",
  waiting_user: "等待澄清", protocol_error: "汇报待修复", evaluation_unavailable: "验收暂不可用",
  context_overflow: "上下文待恢复",
};
const PHASE_LABELS: Record<string, string> = { queued:"等待执行名额", coordinating: "协调下属", decision: "节点决策", delegation: "委派执行", acceptance: "验收", execute: "执行", executing: "执行", evaluating: "验收", evaluation: "验收", rework: "补充与返工", resume: "继续会话", finished: "已结束" };
const FREQUENT_EVENTS = new Set(["hermes.heartbeat", "terminal.activity", "terminal.output", "hermes.line"]);
const isKeyEvent = (event: BusEvent) => !FREQUENT_EVENTS.has(event.type) && event.type !== "hermes.message";
const TERMINAL_STAGES: Record<string, string> = { process_exited: "进程已退出", tree_reaped: "子进程已回收", output_drained: "输出已读取", terminal_closed: "终端已关闭" };
function eventPreview(event: BusEvent): string {
  const raw = String(event.data.phase ?? event.data.action ?? event.data.status ?? event.data.tool ?? event.data.detail ?? "");
  const labels = event.type === "chat.ceo_activity" ? CEO_PHASES : event.type === "terminal.stage" ? TERMINAL_STAGES : {...PHASE_LABELS,...STATE_LABELS};
  return (labels[raw] ?? raw).slice(0,140);
}

function useExecutionEvents(id: string): BusEvent[] {
  return useSyncExternalStore(
    (fn) => store.subscribeExecution(id, fn),
    () => store.getExecutionEvents(id),
  );
}

function useExecution(id: string) {
  const [execution, setExecution] = useState<ExecutionDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const result = await api.execution(id, controller.signal);
        if (controller.signal.aborted) return;
        if (!result.ok || !result.execution) throw new Error(result.error || "执行记录暂不可用");
        setExecution(result.execution);
        setError("");
        if (!result.execution.ended_at) timer = setTimeout(() => void load(), 5000);
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = setTimeout(() => void load(), 5000);
      }
    };
    void load();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [id]);
  return { execution, error };
}

export function TerminalView({ attempt }: { attempt: ExecutionAttempt }) {
  const host = useRef<HTMLDivElement>(null);
  const terminal = useRef<Terminal | null>(null);
  const search = useRef<SearchAddon | null>(null);
  const following = useRef(true);
  const [follow, setFollow] = useState(true);
  const [status, setStatus] = useState("加载终端");
  const [query, setQuery] = useState("");
  const [fontSize, setFontSize] = useState(12);
  const [copied, setCopied] = useState(false);
  const [matches, setMatches] = useState<{ resultIndex: number; resultCount: number } | null>(null);

  useEffect(() => {
    let disposed = false;
    let stream: TerminalStream | undefined;
    const disposables: { dispose(): void }[] = [];
    void Promise.all([import("@xterm/xterm"), import("@xterm/addon-search"), import("@xterm/xterm/css/xterm.css")])
      .then(([{ Terminal }, { SearchAddon }]) => {
        if (disposed || !host.current) return;
        const term = new Terminal({
          cols: attempt.cols || 120, rows: attempt.rows || 36,
          disableStdin: true, cursorBlink: false, scrollback: 10000,
          // The official SearchAddon uses decoration APIs for match highlights.
          allowProposedApi: true,
          fontFamily: '"Cascadia Code", "Consolas", "Noto Sans Mono", monospace',
          fontSize: 12, lineHeight: 1.25, minimumContrastRatio: 4.5,
          theme: { background: "#080f18", foreground: "#d2dee9", cursor: "#7dd3bd", selectionBackground: "#28434f", black: "#101924", red: "#fb7185", green: "#75d4ae", yellow: "#f5ca79", blue: "#82b8f4", magenta: "#c2a1f1", cyan: "#81d4df", white: "#dbe7f0" },
        });
        const addon = new SearchAddon();
        term.loadAddon(addon);
        term.open(host.current);
        terminal.current = term;
        search.current = addon;
        disposables.push(term.onScroll(() => {
          const active = term.buffer.active;
          const atBottom = active.viewportY >= active.baseY;
          following.current = atBottom;
          setFollow(atBottom);
        }));
        disposables.push(addon.onDidChangeResults((result) => setMatches(result)));
        const write = async (records: TerminalRecord[]) => {
          let pending = "";
          const flush = async () => {
            if (!pending || disposed) return;
            const data = pending; pending = "";
            await new Promise<void>((resolve) => term.write(data, resolve));
          };
          for (const record of records) {
            if (record.type === "data") pending += record.data ?? "";
            else {
              await flush();
              if (disposed) return;
              if (record.type === "resize" && record.cols && record.rows) term.resize(record.cols, record.rows);
            }
          }
          await flush();
          if (!disposed && following.current) term.scrollToBottom();
        };
        stream = new TerminalStream(attempt.attempt_id, write, (text) => { if (!disposed) setStatus(text); });
        stream.start();
      }).catch((e) => { if (!disposed) setStatus(`终端加载失败：${String(e)}`); });
    return () => {
      disposed = true;
      stream?.destroy();
      disposables.forEach((item) => item.dispose());
      terminal.current?.dispose();
      terminal.current = null;
      search.current = null;
    };
  }, [attempt.attempt_id, attempt.cols, attempt.rows]);

  useEffect(() => { if (terminal.current) terminal.current.options.fontSize = fontSize; }, [fontSize]);
  useEffect(() => {
    if (query) search.current?.findNext(query, { incremental: true, decorations: { matchBackground: "#374759", activeMatchBackground: "#886b30", matchOverviewRuler: "#90a3b9", activeMatchColorOverviewRuler: "#e3bc68" } });
    else { search.current?.clearDecorations(); setMatches(null); }
  }, [query]);

  const copy = async () => {
    const term = terminal.current;
    if (!term) return;
    const buffer = term.buffer.active;
    const text = term.getSelection() || Array.from({ length: buffer.length }, (_, i) => buffer.getLine(i)?.translateToString(true) ?? "").join("\n");
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { store.toast("error", "复制失败，请选中文本后使用系统复制"); }
  };

  return <div className="terminal-shell" data-testid="terminal-panel">
    <div className="terminal-toolbar">
      <div className="terminal-search"><Search size={13} /><input aria-label="搜索终端" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索当前终端缓冲…" onKeyDown={(e) => { if (e.key === "Enter") e.shiftKey ? search.current?.findPrevious(query) : search.current?.findNext(query); }} />
        {matches && query && <span className="num">{matches.resultCount ? matches.resultIndex + 1 : 0}/{matches.resultCount}</span>}
      </div>
      <button title="复制选中文本或缓冲" aria-label="复制终端" onClick={() => void copy()}>{copied ? <CheckCircle2 size={14} /> : <Copy size={14} />}</button>
      <button title="缩小字号" aria-label="缩小字号" onClick={() => setFontSize((n) => Math.max(9, n - 1))}><Minus size={14} /></button>
      <span className="num text-[10px]">{fontSize}</span>
      <button title="增大字号" aria-label="增大字号" onClick={() => setFontSize((n) => Math.min(18, n + 1))}><Plus size={14} /></button>
      <a title="下载完整文本日志" aria-label="下载终端日志" href={`/api/attempts/${encodeURIComponent(attempt.attempt_id)}/download?format=text`}><Download size={14} /></a>
    </div>
    <div className="terminal-viewport"><div ref={host} /></div>
    <div className="terminal-footer"><span><i className={status === "实时连接" ? "terminal-live-dot" : "terminal-muted-dot"} />{status}</span><span>只读 · {attempt.cols || 120} × {attempt.rows || 36}</span>
      <button className={follow ? "is-following" : ""} onClick={() => { following.current = true; setFollow(true); terminal.current?.scrollToBottom(); }}><ArrowDown size={12} />{follow ? "跟随输出" : "回到底部"}</button>
    </div>
  </div>;
}

function EventTimeline({ events }: { events: BusEvent[] }) {
  const [filter, setFilter] = useState("");
  const [showMessages, setShowMessages] = useState(false);
  const shown = events.filter((ev) => !FREQUENT_EVENTS.has(ev.type)
    && (showMessages || ev.type !== "hermes.message")
    && (!filter || `${EVENT_LABELS[ev.type] ?? ""} ${eventPreview(ev)} ${ev.type} ${JSON.stringify(ev.data)}`.toLowerCase().includes(filter.toLowerCase())));
  return <div className="execution-events"><input className="input mb-4" aria-label="搜索执行事件" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="搜索工具、阶段、决策或错误…" />
    <div className="execution-events-summary" role="status">{filter ? `找到 ${shown.length} 条匹配事件` : `当前显示 ${shown.length} 条${showMessages ? "事件" : "关键事件"}`} · 默认隐藏会话消息与高频状态</div>
    <label className="mb-3 flex items-center gap-2 text-xs"><input type="checkbox" checked={showMessages} onChange={(e) => setShowMessages(e.target.checked)} />显示 Hermes 会话消息</label>
    {!shown.length && <div className="execution-empty">{filter ? "未找到匹配事件，请调整搜索内容" : "当前记录中暂无可展示的执行事件"}</div>}
    {shown.map((ev, i) => <details className={`execution-event ${ev.type === "node.eval" ? "is-evaluation" : ""}`} key={ev.id ?? `${ev.ts}-${i}`}>
      <summary><span className="num event-time">{fmtTime(ev.ts)}</span><span>{EVENT_LABELS[ev.type] ?? ev.type}</span><span className="event-preview">{eventPreview(ev)}</span></summary>
      <pre>{JSON.stringify(ev.data, null, 2)}</pre>
    </details>)}
  </div>;
}

function Acceptance({ events }: { events: BusEvent[] }) {
  const evaluated = events.filter((ev) => ev.type === "node.eval" || ev.type === "node.rework");
  if (!evaluated.length) return <div className="execution-empty">尚无验收结论，执行状态将由后续事件更新。</div>;
  return <div className="acceptance-list">{evaluated.slice().reverse().map((ev, i) => {
    let report = (ev.data.evaluation ?? ev.data.acceptance ?? ev.data) as Record<string, unknown>;
    if (typeof ev.data.detail === "string") {
      try { report = { ...report, ...JSON.parse(ev.data.detail) }; } catch { /* Human report remains visible. */ }
    }
    const unknown = report.unjudged || report.infra_failure || typeof report.passed !== "boolean";
    const passed = !unknown && report.passed === true;
    return <article className={`acceptance-card ${unknown ? "is-unknown" : passed ? "is-passed" : "is-failed"}`} key={ev.id ?? i}>
      <div className="flex items-center justify-between"><strong>{unknown ? "暂无法判断" : passed ? "验收通过" : "需要补充或修复"}</strong><span className="num text-[11px] text-ink-500">{fmtTime(ev.ts)}</span></div>
      {ev.type === "node.rework" && <div className="mt-2 text-[12px] text-ink-400">第 {String(ev.data.reworks ?? 1)} 次补充 · 保留已完成产物并继续原会话</div>}
      {ev.type !== "node.rework" && typeof report.score === "number" && <div className="mt-2 text-[12px] text-ink-400">评分 {report.score.toFixed(2)}</div>}
      <p>{String(report.reason ?? report.summary ?? report.detail ?? "")}</p>
      {["passed_items", "failed_items", "evidence"].map((key) => Array.isArray(report[key]) && (report[key] as unknown[]).length > 0 ? <div key={key} className="mt-4"><div className="section-title mb-2">{{ passed_items: "已通过", failed_items: "待处理", evidence: "证据" }[key]}</div><ul>{(report[key] as unknown[]).map((item, n) => <li key={n}>{typeof item === "string" ? item : JSON.stringify(item)}</li>)}</ul></div> : null)}
    </article>;
  })}</div>;
}

export function ExecutionMonitor({ executionId, nodeId, profile, coordinator }: { executionId: string; nodeId: string; profile: ReactNode; coordinator?: CeoActivity }) {
  const { execution, error } = useExecution(executionId);
  const liveEvents = useExecutionEvents(executionId);
  const [tab, setTab] = useState("terminal");
  const [expanded, setExpanded] = useState(false);
  const [pickedAttempt, setPickedAttempt] = useState<string | null>(null);
  const events = useMemo(() => {
    const merged = [...(execution?.events ?? []), ...liveEvents];
    const seen = new Set<string>();
    return merged.filter((ev) => {
      const key = ev.id != null ? `id:${ev.id}` : `${ev.type}:${ev.ts}:${JSON.stringify(ev.data)}`;
      if (seen.has(key)) return false; seen.add(key); return true;
    }).sort((a, b) => a.ts - b.ts);
  }, [execution?.events, liveEvents]);
  const keyEventCount = events.filter(isKeyEvent).length;
  const attempts = execution?.attempts ?? [];
  const attempt = attempts.find((a) => a.attempt_id === pickedAttempt) ?? attempts[attempts.length - 1];
  const state = coordinator ? ceoGraphStatus(coordinator.phase) : execution ? executionGraphStatus(execution) : "unknown";
  const activity = [...events].reverse().find((ev) => ev.type === "terminal.activity" && ev.data.attempt_id === attempt?.attempt_id);
  const description = String(activity?.data.activity_description ?? "");
  const activityLabel = description.startsWith("receiving stream") ? "模型正在生成响应"
    : description.startsWith("waiting for ") ? "等待模型响应"
    : description.startsWith("executing ") || description.startsWith("sequential tool running") ? "工具正在执行"
    : description;
  return <div className={`execution-monitor ${expanded ? "is-expanded" : ""}`}>
    <header className="execution-header"><div className="min-w-0"><div className="flex items-center gap-2"><TerminalIcon size={16} className="text-life-400" /><strong>{nodeIdLabel(nodeId)}</strong><span className={`execution-state state-${state}`}>{coordinator ? ceoPhaseLabel(coordinator) : GRAPH_STATUS[state].label}</span></div><div className="num execution-id" title={executionId}>{executionId}</div></div>
      <div className="flex gap-1"><button aria-label={expanded ? "收起执行面板" : "展开执行面板"} onClick={() => setExpanded((v) => !v)}><Maximize2 size={16} /></button><button aria-label="关闭" onClick={() => store.selectInst(null)}><X size={17} /></button></div>
    </header>
    {coordinator && <div className="execution-meta"><span>固定 CEO · Hermes · 只负责协调</span><b>{ceoPhaseLabel(coordinator)}</b></div>}
    <div className="execution-meta"><span>阶段 <b>{coordinator ? ceoPhaseLabel(coordinator) : PHASE_LABELS[execution?.phase ?? ""] ?? execution?.phase ?? "等待记录"}</b></span><span>已用时 <b className="num">{execution?.started_at ? fmtDuration(Math.max(0, (execution.ended_at || Date.now() / 1000) - execution.started_at)) : "—"}</b></span><span>最近活动 <b>{execution?.last_activity_at ? fmtAgo(execution.last_activity_at) : "—"}</b></span><span>Profile <b className="num">{execution?.profile || nodeId}</b></span></div>
    {activityLabel && state === "running" && attempt?.state === "running" && activity && Date.now() / 1000 - activity.ts < 90 && <div className="execution-meta" title={description}><span>当前活动 <b>{activityLabel}</b></span><span>无进展 <b className="num">{fmtDuration(Number(activity.data.idle_seconds ?? 0))}</b></span></div>}
    <nav className="execution-tabs">{(coordinator ? [["terminal", "Hermes 决策记录"], ["events", "执行事件"], ["profile", "CEO 职责与技能"]] : [["terminal", "实时终端"], ["events", "执行事件"], ["acceptance", "验收"], ["profile", "节点档案"]]).map(([key, label]) => <button key={key} className={tab === key ? "is-active" : ""} onClick={() => setTab(key)}>{label}{key === "events" && keyEventCount > 0 && <small>{keyEventCount}</small>}</button>)}</nav>
    <div className="execution-body">
      {error && <div className="execution-error">{error}</div>}
      {tab === "terminal" && <>
        {attempts.length > 0 && <div className="attempt-select"><label htmlFor="attempt-choice">执行尝试</label><select id="attempt-choice" value={attempt?.attempt_id ?? ""} onChange={(e) => setPickedAttempt(e.target.value)}>{attempts.map((a, i) => <option key={a.attempt_id} value={a.attempt_id}>第 {i + 1} 次 · {STATE_LABELS[a.state ?? ""] ?? a.phase ?? a.state ?? a.attempt_id}</option>)}</select></div>}
        {attempt ? <TerminalView key={attempt.attempt_id} attempt={attempt} /> : <div className="execution-empty">{execution ? "Hermes 尚未启动，等待调度或节点决策…" : "正在读取执行记录…"}</div>}
      </>}
      {tab === "events" && <EventTimeline events={events} />}
      {tab === "acceptance" && <Acceptance events={events} />}
      {tab === "profile" && profile}
    </div>
  </div>;
}
