import { ArtifactList } from "@/components/ArtifactList";
// ============================================================
// 对话工作台 — CEO 聊天流 + 流式工具消息 + 斜杠命令
// ============================================================

import { useEffect, useMemo, useRef, useState } from "react";
import {
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  ChevronUp,
  Circle,
  Clock,
  FileOutput,
  HelpCircle,
  ListTree,
  Pencil,
  Send,
  ShieldAlert,
  Terminal,
  Trash2,
  Wrench,
  X,
  XCircle,
} from "lucide-react";
import { store, useAppState } from "@/lib/store";
import { fmtArgs, fmtTime, nodeIdLabel } from "@/lib/format";
import { groupChatMessages } from "@/lib/chat";
import { ceoGraphStatus, ceoPhaseLabel } from "@/lib/ceo";
import { GRAPH_STATUS } from "@/lib/executionState";
import { AutoScroll, Button, Chip, Code, Collapsible, Empty, Spinner } from "@/components/ui";
import { MarkdownBody } from "@/components/MarkdownBody";
import type { ChatMessage, QueueItem } from "@/types";

const SLASH_COMMANDS: { cmd: string; desc: string }[] = [
  { cmd: "/help", desc: "查看命令帮助" },
  { cmd: "/status", desc: "系统状态" },
  { cmd: "/workspace", desc: "当前工作区" },
  { cmd: "/evolve", desc: "手动触发一轮进化" },
  { cmd: "/queue", desc: "任务队列（/queue clear 清空）" },
  { cmd: "/save 名称", desc: "保存当前会话" },
  { cmd: "/load 名称", desc: "载入历史会话" },
  { cmd: "/sessions", desc: "会话列表" },
  { cmd: "/history", desc: "CEO 对话历史" },
  { cmd: "/debug", desc: "调试信息" },
];

// ---------------- 排队消息面板（查看 / 编辑 / 撤销，2026-10-22） ----------------

function QueueRow({ item, position }: { item: QueueItem; position: number }) {
  const workerHold = useAppState((s) => Boolean(s.snap?.worker_hold));
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.text);
  const [working, setWorking] = useState(false);

  useEffect(() => {
    if (!editing) setDraft(item.text);
  }, [item.text, editing]);

  const save = async () => {
    setWorking(true);
    try {
      await store.queueUpdate(item.id, draft);
      setEditing(false);
    } finally {
      setWorking(false);
    }
  };

  const remove = async () => {
    setWorking(true);
    try {
      await store.queueRemove(item.id);
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="rounded-lg border border-ink-600/20 bg-abyss-850/70 p-2.5">
      <div className="mb-1 flex items-center gap-2">
        <span className="num text-[10.5px] text-ink-600">#{position}</span>
        <Chip color={item.mode === "interrupt" ? "ceo" : "dim"}>{item.mode === "interrupt" ? "打断引导 · 本批后" : "排队 · 任务后"}</Chip>
        {item.session && <Chip color="dim">{item.session}</Chip>}
        {item.queued_at ? (
          <span className="num text-[10px] text-ink-600">{fmtTime(item.queued_at)}</span>
        ) : null}
        <span className="flex-1" />
        {!workerHold && !editing && item.mode !== "interrupt" && <button className="text-[11px] text-life-300" title="本批汇报后引导 CEO" onClick={()=>void store.guideQueued(item.id)}>引导</button>}
        {editing ? (
          <>
            <button
              className="cursor-pointer rounded p-1 text-life-300 transition-colors hover:bg-life-500/10 disabled:opacity-40"
              onClick={() => void save()}
              disabled={working || !draft.trim()}
              aria-label="保存修改"
              title="保存修改"
            >
              <CheckCircle2 size={14} />
            </button>
            <button
              className="cursor-pointer rounded p-1 text-ink-500 transition-colors hover:bg-ink-600/20 disabled:opacity-40"
              onClick={() => { setEditing(false); setDraft(item.text); }}
              disabled={working}
              aria-label="取消编辑"
              title="取消"
            >
              <X size={14} />
            </button>
          </>
        ) : (
          <>
            <button
              className="cursor-pointer rounded p-1 text-ink-400 transition-colors hover:bg-pulse-500/10 hover:text-pulse-300 disabled:opacity-40"
              onClick={() => setEditing(true)}
              disabled={working}
              aria-label="编辑这条排队消息"
              title="编辑"
            >
              <Pencil size={13} />
            </button>
            <button
              className="cursor-pointer rounded p-1 text-ink-400 transition-colors hover:bg-danger-500/10 hover:text-danger-300 disabled:opacity-40"
              onClick={() => void remove()}
              disabled={working}
              aria-label="撤销这条排队消息"
              title="撤销（不执行）"
            >
              <Trash2 size={13} />
            </button>
          </>
        )}
      </div>
      {editing ? (
        <textarea
          rows={3}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              void save();
            }
            if (e.key === "Escape") { setEditing(false); setDraft(item.text); }
          }}
          className="w-full resize-y rounded-md border border-life-500/30 bg-abyss-900 p-2 text-[12px] leading-5 text-ink-100 outline-none focus:border-life-500/60"
          aria-label="编辑排队消息内容"
          autoFocus
        />
      ) : (
        <div className="clamp-3 whitespace-pre-wrap text-[12px] leading-5 text-ink-200">
          {item.text}
        </div>
      )}
    </div>
  );
}

function QueuePanel({ onClose }: { onClose: () => void }) {
  const queue = useAppState((s) => s.queue);
  const busy = useAppState((s) => s.busy);
  // 打开面板时主动同步一次（防御 SSE 漏事件/断线期间的过期快照）
  useEffect(() => {
    void store.refreshState();
  }, []);
  return (
    <>
      <div className="fixed inset-0 z-20" onClick={onClose} aria-hidden />
      <div role="dialog" aria-label="任务队列" className="queue-panel surface pop-in absolute right-0 top-full z-30 mt-2 w-[420px] p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[11px] font-semibold tracking-[0.08em] text-ink-400">
            任务队列{queue.length ? `（${queue.length} 条待执行）` : ""}
          </span>
          <button
            className="cursor-pointer rounded p-1 text-ink-500 transition-colors hover:bg-ink-600/20"
            onClick={onClose}
            aria-label="关闭队列面板"
          >
            <X size={14} />
          </button>
        </div>
        {queue.length === 0 ? (
          <div className="py-3 text-center text-[11.5px] text-ink-600">
            队列为空——任务执行中发送的消息会排到这里，结束后自动执行
          </div>
        ) : (
          <div className="max-h-[320px] space-y-2 overflow-y-auto pr-0.5">
            {queue.map((item, i) => (
              <QueueRow key={item.id} item={item} position={i + 1} />
            ))}
          </div>
        )}
        <div className="mt-2 border-t border-ink-600/15 pt-1.5 text-[10.5px] text-ink-600">
          {busy
            ? "打断引导在本批汇报后交给 CEO；普通排队等待当前任务结束。均可编辑或撤销"
            : "空闲中——队列剩余消息将在下一个任务结束后继续"}
        </div>
      </div>
    </>
  );
}

// ---------------- 消息渲染 ----------------

function UserMsg({ m }: { m: ChatMessage }) {
  return (
    <div className="msg-in flex justify-end">
      <div className="max-w-[72%] rounded-2xl rounded-br-md border border-life-500/25 bg-life-500/10 px-4 py-2.5">
        <div className="whitespace-pre-wrap break-words text-[13.5px] leading-6 text-ink-100">
          {m.text}
        </div>
        <div className="mt-1 text-right text-[10px] text-ink-600">{m.ts > 0 ? fmtTime(m.ts) : "时间未记录"}</div>
      </div>
    </div>
  );
}

function SystemMsg({ m }: { m: ChatMessage }) {
  return (
    <div className="msg-in flex justify-center">
      <div className="rounded-full border border-ink-600/25 bg-abyss-850/80 px-3.5 py-1 text-[11px] text-ink-500">
        {m.text}
      </div>
    </div>
  );
}

function AskMsg({ m }: { m: ChatMessage }) {
  return (
    <div className="msg-in flex gap-2.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-ceo-500/40 bg-ceo-500/10 text-ceo-400">
        <HelpCircle size={14} />
      </div>
      <div className="surface min-w-0 max-w-[78%] flex-1 rounded-xl p-3.5">
        <div className="mb-1.5 text-[11px] font-medium tracking-wide text-ceo-400">
          CEO 提问
        </div>
        <MarkdownBody text={m.text}/>
        <div className="mt-2 text-[10.5px] text-ink-600">
          直接在下方输入框回复即可（CEO 会带着上下文继续）
        </div>
      </div>
    </div>
  );
}

function ToolMsg({ m }: { m: ChatMessage }) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(false);
  const [copied, setCopied] = useState(false);
  const hasResult = m.result && m.result.length > 0;
  const tone = m.name === "finish" || m.name === "report" ? "life" : "pulse";
  const argsStr = m.args ? fmtArgs(m.args) : "";
  const copyAll = () => {
    const text = [argsStr && `【参数】${argsStr}`, hasResult && `【结果】${m.result}`]
      .filter(Boolean)
      .join("\n\n");
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    });
  };
  return (
    <div className="msg-in flex gap-2.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-pulse-500/30 bg-pulse-500/10 text-pulse-400">
        <Wrench size={13} />
      </div>
      <div className="min-w-0 flex-1">
        <button
          className="flex cursor-pointer items-center gap-2 rounded-lg border border-ink-600/20 bg-abyss-850/60 px-3 py-1.5 text-left transition-colors hover:border-ink-600/40"
          onClick={() => setOpen(!open)}
        >
          <span className="text-ink-600">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }}>
              <path d="m9 18 6-6-6-6" />
            </svg>
          </span>
          <Chip color={tone} className="num">{m.name || "tool"}</Chip>
          <span className="num min-w-0 flex-1 truncate text-[11.5px] text-ink-500">
            {argsStr || (hasResult ? m.result?.slice(0, 60) : "…")}
          </span>
          {hasResult && (
            <span className="text-[10px] text-ink-600">{m.result?.length ? `结果 ${m.result.length}` : ""}</span>
          )}
        </button>
        {open && (
          <div className="pop-in mt-1.5 space-y-2 pl-1">
            {argsStr && (
              <div>
                <div className="section-title mb-1">参数</div>
                <Code>{argsStr}</Code>
              </div>
            )}
            {hasResult && (
              <div>
                <div className="section-title mb-1 flex items-center gap-2">
                  结果
                  <button
                    className="link-btn text-[10.5px] text-ink-500"
                    onClick={() => setFull(!full)}
                  >
                    {full ? "收起" : "展开全文"}
                  </button>
                  <button
                    className="link-btn text-[10.5px] text-ink-500"
                    onClick={copyAll}
                  >
                    {copied ? "已复制 ✓" : "复制全部"}
                  </button>
                </div>
                {/* 全量内容：默认限高滚动，可展开完整查看（2026-10-23 用户需求） */}
                <pre
                  className={`num whitespace-pre-wrap break-words rounded-lg border border-ink-600/20 bg-abyss-950/70 p-3 text-[11.5px] leading-[1.7] text-ink-200 ${
                    full ? "max-h-none" : "max-h-[220px] overflow-auto"
                  }`}
                >
                  {m.result ?? ""}
                </pre>
              </div>
            )}
            {!argsStr && !hasResult && (
              <div className="text-[11.5px] text-ink-600">（无返回内容）</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolGroup({ items }: { items: ChatMessage[] }) {
  const [open, setOpen] = useState(false);
  // 2026-09-03 用户反馈：工具一条条全量铺开太占高度，显示不下还挤出行内滚动
  // → 连续工具合并成一组：默认收缩为一行芯片（显示不下自动换行），点击展开
  // 逐条查看（每条仍可单独展开参数/结果）
  const names = items.map((m) => m.name || "tool");
  return (
    <div className="msg-in flex gap-2.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-pulse-500/30 bg-pulse-500/10 text-pulse-400">
        <Wrench size={13} />
      </div>
      <div className="min-w-0 flex-1">
        <button
          className="flex cursor-pointer items-center gap-2 rounded-lg border border-ink-600/20 bg-abyss-850/60 px-3 py-1.5 text-left transition-colors hover:border-ink-600/40"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
        >
          <span className="text-ink-600">
            {open ? <ChevronUp size={13} /> : <ChevronRight size={13} />}
          </span>
          <Chip color="pulse" className="num">工具 ×{items.length}</Chip>
          {!open && (
            <span className="num min-w-0 flex-1 truncate text-[11.5px] text-ink-500">
              {names.join(" · ")}
            </span>
          )}
        </button>
        {!open ? (
          /* 折叠态：芯片行，flex-wrap 显示不下自动换行（不产生横向滚动） */
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {items.map((m) => (
              <span key={m.id} title={fmtArgs(m.args) || undefined}>
                <Chip color={m.name === "finish" || m.name === "report" ? "life" : "pulse"} className="num">
                  {m.name || "tool"}
                </Chip>
              </span>
            ))}
          </div>
        ) : (
          <div className="space-y-1.5 pl-1 pt-1.5">
            {items.map((m) => (
              <ToolMsg key={m.id} m={m} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ReportMsg({ m }: { m: ChatMessage }) {
  return (
    <div className="msg-in flex gap-2.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-life-500/30 bg-life-500/10 text-life-400">
        <FileOutput size={13} />
      </div>
      <div className="surface-glass min-w-0 max-w-[78%] flex-1 rounded-xl p-3.5">
        <div className="mb-1.5 flex items-center gap-2 text-[11px] font-medium tracking-wide text-life-400">
          <Clock size={12} /> 进度汇报
        </div>
        <MarkdownBody text={m.text}/>
        {m.reason && (
          <div className="mt-2 border-t border-ink-600/20 pt-2 text-[12px] leading-5 text-ink-500">
            <span className="text-ink-600">下一步： </span>
            <MarkdownBody text={m.reason}/>
          </div>
        )}
      </div>
    </div>
  );
}

function FinishMsg({ m }: { m: ChatMessage }) {
  const blocked = m.status === "blocked";
  const failed = m.status === "failed";
  const unresolved = blocked || failed;
  const label = m.status === "answered" ? "CEO 答复" : blocked ? "CEO 汇报受阻，任务待恢复" : failed ? "CEO 汇报任务未通过" : "任务完成";
  const Icon = unresolved ? ShieldAlert : CheckCircle2;
  return (
    <div className="msg-in flex gap-2.5">
      <div className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border ${unresolved ? "border-warn-400/40 bg-warn-400/10 text-warn-400" : "border-ok-500/40 bg-ok-500/10 text-ok-400"}`}>
        <Icon size={15} />
      </div>
      <div className={`surface min-w-0 max-w-[82%] flex-1 rounded-xl p-4 ${unresolved ? "border-warn-400/25" : "border-ok-500/20"}`}>
        <div className={`mb-2 flex items-center gap-2 text-[11.5px] font-semibold tracking-wide ${unresolved ? "text-warn-400" : "text-ok-400"}`}>
          <Icon size={13} /> {label}
        </div>
        <MarkdownBody text={m.text}/>
        {m.workflow && (
          <Collapsible title={<span className="flex items-center gap-1.5"><ListTree size={11} /> 执行工作流</span>} defaultOpen={false}>
            <Code>{m.workflow}</Code>
          </Collapsible>
        )}
        {m.artifacts && m.artifacts.length > 0 && <ArtifactList paths={m.artifacts}/>}
      </div>
    </div>
  );
}

function EndMsg({ m }: { m: ChatMessage }) {
  const isError = m.status === "error";
  return (
    <div className="msg-in flex gap-2.5">
      <div
        className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border ${
          isError ? "border-danger-500/40 bg-danger-500/10 text-danger-400" : "border-warn-400/40 bg-warn-400/10 text-warn-400"
        }`}
      >
        {isError ? <XCircle size={14} /> : <ShieldAlert size={14} />}
      </div>
      <div
        className={`max-w-[78%] min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere] rounded-xl border px-3.5 py-2.5 text-[12.5px] leading-6 ${
          isError ? "border-danger-500/25 bg-danger-500/5 text-danger-300" : "border-warn-400/25 bg-warn-400/5 text-warn-400/90"
        }`}
      >
        {m.text}
      </div>
    </div>
  );
}

function TextMsg({ m }: { m: ChatMessage }) {
  return (
    <div className="msg-in flex gap-2.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-ink-600/30 bg-abyss-850 text-ink-500">
        <Terminal size={13} />
      </div>
      <div className="surface min-w-0 max-w-[80%] flex-1 rounded-xl px-3.5 py-2.5 text-ink-200">
        <MarkdownBody text={m.text}/>
      </div>
    </div>
  );
}

function BrainMsg({ m }: { m: ChatMessage }) {
  return (
    <div className="msg-in flex gap-2.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-gene-500/30 bg-gene-500/10 text-gene-400">
        <BrainCircuit size={13} />
      </div>
      <div className="min-w-0 max-w-[78%] rounded-xl border border-gene-500/15 bg-gene-500/5 px-3.5 py-2">
        <div className="flex items-center gap-2">
          <span className="num text-[11px] font-medium text-gene-300">{nodeIdLabel(m.node ?? "")}</span>
          <Chip color="gene" className="num">{m.action}</Chip>
        </div>
        {m.text && <div className="clamp-3 mt-1 text-[12px] leading-5 text-ink-300">{m.text}</div>}
      </div>
    </div>
  );
}

const COLLAB_LABEL: Record<string, string> = {
  delegate: "委派",
  verdict: "上级复核",
  escalate: "越级纠错",
  settle: "CEO 结算",
};

function CollabMsg({ m }: { m: ChatMessage }) {
  const label = COLLAB_LABEL[m.collabKind ?? ""] ?? "协作";
  const pair = [m.from && nodeIdLabel(m.from), m.to && nodeIdLabel(m.to)]
    .filter(Boolean)
    .join(" → ");
  return (
    <div className="msg-in flex gap-2.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-gene-500/30 bg-gene-500/10 text-gene-400">
        <Circle size={12} />
      </div>
      <div className="min-w-0 max-w-[78%] rounded-xl border border-gene-500/15 bg-gene-500/5 px-3.5 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-medium tracking-[0.08em] text-gene-300">{label}</span>
          {pair && <span className="num text-[11px] text-ink-400">{pair}</span>}
          {m.passed != null && (
            <Chip color={m.passed ? "ok" : "danger"}>{m.passed ? "通过" : "未通过"}</Chip>
          )}
          {m.delta != null && (
            <Chip color={m.delta >= 0 ? "ok" : "danger"} className="num">
              {m.delta >= 0 ? `+${m.delta.toFixed(1)}` : m.delta.toFixed(1)} 分
            </Chip>
          )}
        </div>
        {m.text && <div className="clamp-3 mt-1 text-[12px] leading-5 text-ink-300">{m.text}</div>}
      </div>
    </div>
  );
}

function EvalMsg({ m }: { m: ChatMessage }) {
  return (
    <div className="msg-in flex gap-2.5">
      <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-life-500/30 bg-life-500/10 text-life-400">
        <Circle size={12} />
      </div>
      <div className="min-w-0 max-w-[78%] rounded-xl border border-life-500/15 bg-life-500/5 px-3.5 py-2">
        <div className="flex items-center gap-2">
          <span className="num text-[11px] font-medium text-life-300">{nodeIdLabel(m.node ?? "")}</span>
          {m.passed != null && (
            <Chip color={m.passed ? "ok" : "danger"}>{m.passed ? "通过" : "未通过"}</Chip>
          )}
          {m.score != null && <Chip color="dim" className="num">{m.score.toFixed(1)} 分</Chip>}
        </div>
        {m.text && <div className="clamp-3 mt-1 text-[12px] leading-5 text-ink-300">{m.text}</div>}
      </div>
    </div>
  );
}

function Message({ m }: { m: ChatMessage }) {
  switch (m.kind) {
    case "user": return <UserMsg m={m} />;
    case "system": return <SystemMsg m={m} />;
    case "ask": return <AskMsg m={m} />;
    case "tool": return <ToolMsg m={m} />;
    case "report": return <ReportMsg m={m} />;
    case "finish": return <FinishMsg m={m} />;
    case "end": return <EndMsg m={m} />;
    case "brain": return <BrainMsg m={m} />;
    case "eval": return <EvalMsg m={m} />;
    case "collab": return <CollabMsg m={m} />;
    default: return <TextMsg m={m} />;
  }
}

// ---------------- 斜杠命令面板 ----------------

function CommandPanel({ query, onPick }: { query: string; onPick: (cmd: string) => void }) {
  const items = useMemo(() => {
    const q = query.trim().toLowerCase();
    return SLASH_COMMANDS.filter((c) => c.cmd.toLowerCase().startsWith(q) || c.desc.toLowerCase().includes(q)).slice(0, 8);
  }, [query]);
  if (!items.length) return null;
  return (
    <div className="surface pop-in absolute bottom-full left-0 z-20 mb-2 w-[380px] overflow-hidden p-1.5">
      <div className="px-2.5 py-1.5 text-[10.5px] tracking-[0.1em] text-ink-600">斜杠命令</div>
      {items.map((c) => (
        <button
          key={c.cmd}
          className="flex w-full cursor-pointer items-center gap-3 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-life-500/10"
          onClick={() => onPick(c.cmd)}
        >
          <span className="num w-[110px] shrink-0 text-[12px] font-medium text-life-300">{c.cmd}</span>
          <span className="text-[12px] text-ink-400">{c.desc}</span>
        </button>
      ))}
    </div>
  );
}

// ---------------- 视图 ----------------

export function ChatView() {
  const ceo = useAppState((s) => s.snap?.ceo);
  const ceoStatus = ceoGraphStatus(ceo?.phase);
  const ceoColor = `var(${GRAPH_STATUS[ceoStatus].color})`;
  const messages = useAppState((s) => s.messages);
  const busy = useAppState((s) => s.busy);
  const recoveryChat = useAppState((s) => Boolean(s.snap?.worker_hold));
  const scopedRecovery = useAppState((s) => Boolean(s.viewSession && s.snap?.runs?.[s.viewSession]?.recovery_execution));
  const queueLen = useAppState((s) => s.queueLen);
  const queue = useAppState((s) => s.queue);
  const projects = useAppState((s) => s.projects);
  const projectSupport = useAppState((s) => s.projectSupport);
  const viewSession = useAppState((s) => s.viewSession);
  const liveSession = useAppState((s) => s.liveSession);
  const project = projects.find(item => item.sessions.some(chat => chat.id === viewSession));
  const conversation = project?.sessions.find(chat => chat.id === viewSession);
  const archived = Boolean(project?.archived || conversation?.archived);
  const [input, setInput] = useState(() => store.draftFor(viewSession));
  const [showCmds, setShowCmds] = useState(false);
  const [showQueue, setShowQueue] = useState(false);
  const [sending, setSending] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => { setInput(store.draftFor(viewSession)); setShowQueue(false); setShowCmds(false); }, [viewSession]);

  useEffect(() => {
    const box = inputRef.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, 200)}px`;
  }, [input, viewSession]);

  useEffect(() => {
    const box = inputRef.current;
    if (!box) return;
    let width = box.clientWidth;
    const observer = new ResizeObserver(() => {
      if (box.clientWidth === width) return;
      width = box.clientWidth;
      box.style.height = "auto";
      box.style.height = `${Math.min(box.scrollHeight, 200)}px`;
    });
    observer.observe(box);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") { setShowQueue(false); setShowCmds(false); } };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);

  const send = async () => {
    const t = input.trim();
    if (!t || sending || archived || scopedRecovery) return;
    setShowCmds(false);
    setSending(true);
    try {
      if (t.startsWith("/")) {
        setInput("");
        store.saveDraft(viewSession, "");
        await store.sendCommand(t);
      } else {
        const accepted = await store.sendChat(t);
        if (accepted && store.draftFor(viewSession) === input) {
          store.saveDraft(viewSession, "");
          if (store.getState().viewSession === viewSession) setInput("");
        }
      }
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const onInput = (v: string) => {
    store.saveDraft(viewSession, v);
    setInput(v);
    setShowCmds(v.startsWith("/") && !v.includes(" "));
  };

  const lastAsk = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.kind === "ask") return m;
      if (m.kind === "user") return null;
    }
    return null;
  }, [messages]);

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      {/* 顶栏 */}
      <div className="chat-header flex h-[52px] shrink-0 items-center justify-between border-b border-ink-600/15 bg-abyss-900/40 px-5">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <h2 className="font-display text-[15px] font-semibold text-ink-100 truncate">{conversation?.title || "对话工作台"}</h2>
          {archived && <Chip color="dim">已归档</Chip>}
          {!projectSupport && (viewSession ? (
            <Chip color="pulse">历史视图 · {viewSession}</Chip>
          ) : (
            <Chip color="dim">{liveSession ?? "实时会话"}</Chip>
          ))}
        </div>
        <div className="relative flex items-center gap-2.5">
          <button className="hidden sm:block shrink-0 rounded-full border border-ceo-400/25 px-3 py-1 text-[11px] text-ceo-300 hover:bg-ceo-400/10" onClick={() => store.selectInst("ceo")} aria-label="查看 CEO 协调活动" title="固定协调者 · 查看 CEO 活动和 Hermes 决策记录">CEO · Hermes</button>
          <button
            className={`num cursor-pointer rounded-full border px-2.5 py-1 text-[11px] transition-colors ${
              queueLen > 0
                ? "border-warn-400/40 bg-warn-400/10 text-warn-400 hover:bg-warn-400/20"
                : "border-ink-600/25 text-ink-600 hover:border-ink-600/40 hover:text-ink-400"
            }`}
            onClick={() => setShowQueue((v) => !v)}
            aria-label={queueLen > 0 ? `查看任务队列（${queueLen} 条）` : "查看任务队列"}
            title="任务队列（查看 / 编辑 / 撤销排队消息）"
          >
            队列 {queueLen}
          </button>
          {showQueue && <QueuePanel onClose={() => setShowQueue(false)} />}
          <span data-testid="ceo-status" data-status={ceoStatus} className="flex items-center gap-2 rounded-full border px-3 py-1 text-[11.5px]"
            style={{color:ceoColor,borderColor:`color-mix(in srgb, ${ceoColor} 30%, transparent)`,background:`color-mix(in srgb, ${ceoColor} 8%, transparent)`}}>
            {busy && ceoStatus === "running" ? <Spinner size={11}/> : <span className="dot" style={{background:"currentColor",width:6,height:6}}/>}
            {ceoPhaseLabel(ceo,busy)}
          </span>
        </div>
      </div>

      {/* 消息流（2026-09-03：列宽收窄到 720px + 禁横向滚动，长内容靠换行/内部滚动）
          2026-09-04：AutoScroll 粘性跟随——新消息到达且用户在底部时自动滚到最底，
          用户手动上滚查看历史时释放跟随（不再抢滚动位置） */}
      <AutoScroll className="chat-scroll relative min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-5 py-5">
        {messages.length === 0 ? (
          <Empty
            icon={<Terminal size={36} strokeWidth={1.2} />}
            title="开始一个新任务"
            hint="直接向 CEO 描述你的需求（例如「帮我调研 X 技术并写一份报告」）。输入 / 查看斜杠命令。复杂任务会自动拆分子任务并派发给节点种群执行。"
          />
        ) : (
          <div className="mx-auto min-w-0 max-w-[720px] space-y-3">
            {groupChatMessages(messages).map((seg) =>
              seg.type === "tools" ? (
                <ToolGroup key={`g-${seg.items[0].id}`} items={seg.items} />
              ) : (
                <Message key={seg.m.id} m={seg.m} />
              ),
            )}
            {busy && (
              <div className="msg-in flex items-center gap-2.5 pl-9 text-[12px] text-ink-500">
                <Spinner size={12} />
                {ceoPhaseLabel(ceo, true)}{queueLen > 0 ? ` · ${queueLen} 条消息排队` : ""}…
              </div>
            )}
          </div>
        )}
      </AutoScroll>

      {/* CEO 追问横幅 */}
      {lastAsk && !busy && !archived && (
        <div className="mx-auto mb-2 w-full max-w-[720px] px-5">
          <div className="toast-in flex items-center gap-2 rounded-lg border border-ceo-500/30 bg-ceo-500/10 px-3 py-2 text-[12px] text-ceo-300">
            <HelpCircle size={13} className="shrink-0" />
            CEO 在等你回答（输入回答后回车即可）
          </div>
        </div>
      )}

      {/* 输入区 */}
      <div className="chat-input-area relative shrink-0 px-5 py-4">
        {showCmds && !archived && <CommandPanel query={input} onPick={(cmd) => { onInput(cmd + " "); setShowCmds(false); inputRef.current?.focus(); }} />}
        {!archived && queue.length > 0 && <div className="pending-messages mx-auto w-full max-w-[720px]" aria-label="待处理消息">
          {queue.slice(0,3).map(item=><div className="pending-message" key={item.id}>
            <Clock size={14}/><span title={item.text}>{item.text}</span>
            {recoveryChat ? <small>任务暂停</small> : item.mode === 'interrupt' ? <small>等待本批汇报</small> : <button onClick={()=>void store.guideQueued(item.id)} title="本批已派发任务汇报后引导 CEO">引导</button>}
            <button aria-label={`撤销待处理消息${item.text}`} title="撤销" onClick={()=>void store.queueRemove(item.id)}><Trash2 size={14}/></button>
          </div>)}
          {queue.length>3&&<button className="pending-more" onClick={()=>setShowQueue(true)}>查看其余 {queue.length-3} 条消息</button>}
        </div>}
        <div className="chat-composer surface mx-auto w-full max-w-[720px]">
          <div className="composer-text">
            <textarea
              ref={inputRef}
              rows={1}
              aria-label="消息内容"
              value={archived ? "" : input}
              disabled={archived}
              onChange={(e) => onInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                  e.preventDefault();
                  void send();
                }
                if (e.key === "Escape") setShowCmds(false);
              }}
              placeholder={
                archived ? "请在左侧「已归档」中恢复后继续对话"
                : scopedRecovery ? "受限恢复任务正在运行，结束后可继续对话…"
                : recoveryChat ? "询问 CEO 当前进展，或讨论下一步…"
                : busy
                  ? "继续输入，消息会先排队；发送后可点「引导」…"
                  : projectSupport ? "和 CEO 一起工作，或输入 / 使用命令…"
                  : viewSession
                    ? `历史会话已激活（${viewSession}）—— 新消息将带着该会话的 CEO 上下文继续，或输入 / 使用命令`
                    : "描述你的任务，或输入 / 使用命令…  Enter 发送 · Shift+Enter 换行"
              }
              className="max-h-[160px] w-full resize-none bg-transparent text-[13.5px] leading-6 text-ink-100 outline-none placeholder:text-ink-600"
            />
          </div>
          <div className="composer-bottom"><span>{scopedRecovery ? "事故恢复中 · 已核准的节点任务运行中" : recoveryChat ? "事故恢复中 · 可以对话，节点任务暂停" : busy ? "默认排队 · 点「引导」后在本批任务汇报时交给 CEO" : "CEO 规划与委派 · Worker 执行"}</span><Button
            variant="primary"
            className="composer-send"
            onClick={() => void send()}
            disabled={archived || scopedRecovery || !input.trim() || sending}
            aria-label="发送"
          >
            {sending ? <Spinner size={16} /> : <Send size={17} className="-ml-0.5 mt-0.5" />}
          </Button></div>
        </div>
        <div className="mx-auto mt-2 flex w-full max-w-[720px] items-center justify-between text-[10.5px] text-ink-600">
          <span>Enter 发送 · Shift + Enter 换行 · / 查看命令</span>
          <span className="num">{messages.length} 条消息</span>
        </div>
      </div>
    </div>
  );
}

// 供其他组件复用
export { SLASH_COMMANDS };
