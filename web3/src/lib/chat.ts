// ============================================================
// 事件 → 聊天消息（纯函数，可单测）
// ============================================================

import type { BusEvent, ChatMessage, SessionMessage } from "@/types";

let seq = 0;
export function nextMsgId(): string {
  seq += 1;
  return `m${Date.now().toString(36)}-${seq}`;
}

/** 历史会话消息（后端 _session_messages）→ 聊天渲染消息 */
export function mapSessionMessage(m: SessionMessage, id?: string): ChatMessage {
  const ts = typeof m.ts === "number" && Number.isFinite(m.ts) && m.ts > 0 ? m.ts : 0;
  const base = { id: id ?? nextMsgId(), ts };
  switch (m.kind) {
    case "user":
      return { ...base, kind: "user", text: m.text };
    case "tool":
      return { ...base, kind: "tool", name: m.name, args: m.args, result: m.result };
    case "ask":
      return { ...base, kind: "ask", text: m.text };
    case "report":
      return { ...base, kind: "report", text: m.text, reason: m.reason };
    case "finish":
      return {
        ...base,
        kind: "finish",
        text: m.text,
        status: m.status,
        workflow: m.workflow,
        artifacts: m.artifacts,
      };
    default:
      return { ...base, kind: "text", text: m.text };
  }
}

/** Restore recorded input times; missing or ambiguous history stays unknown. */
export function mapSessionMessages(messages: SessionMessage[], events: BusEvent[]): ChatMessage[] {
  const counts = new Map<string, number>();
  const times = new Map<string, number[]>();
  for (const m of messages) if (m.kind === "user") counts.set(m.text || "", (counts.get(m.text || "") || 0) + 1);
  for (const e of events) {
    if (!["chat.user", "chat.recovery_user"].includes(e.type) || !Number.isFinite(e.ts) || e.ts <= 0) continue;
    const text = String(e.data.text ?? "");
    const values = times.get(text);
    if (values) values.push(e.ts); else times.set(text, [e.ts]);
  }
  // A partial legacy transcript cannot identify which repeated input was sent.
  for (const [text, values] of times) if (values.length !== counts.get(text)) times.delete(text);
  return messages.map(m => {
    const mapped = mapSessionMessage(m);
    const recorded = m.kind === "user" ? times.get(m.text || "")?.shift() : undefined;
    return !mapped.ts && recorded ? { ...mapped, ts: recorded } : mapped;
  });
}

/** 一条 SSE 事件是否应显示在对话流中 */
export function eventToMessage(ev: BusEvent): ChatMessage | null {
  const d = ev.data ?? {};
  const base = { id: nextMsgId(), ts: ev.ts };
  switch (ev.type) {
    case "chat.user":
    case "chat.recovery_user":
      return { ...base, kind: "user", text: String(d.text ?? "") };
    case "chat.recovery_reply":
      return { ...base, kind: "text", text: String(d.text ?? "") };
    case "system":
      if (d.msg === "当前运行已结束") return null;
      return { ...base, kind: "system", text: String(d.msg ?? "") };
    case "chat.ceo_ask":
      return { ...base, kind: "ask", text: String(d.question ?? "") };
    case "chat.ceo_tool":
      return {
        ...base,
        kind: "tool",
        name: String(d.name ?? ""),
        args: d.args ?? null,
        // 全量保留（后端已不截断）；折叠展示由 ChatView 的 UI 层负责
        result: String(d.result ?? ""),
      };
    case "chat.ceo_report":
      return {
        ...base,
        kind: "report",
        text: String(d.summary ?? ""),
        reason: String(d.next_plan ?? ""),
      };
    case "chat.ceo_finish":
      return {
        ...base,
        kind: "finish",
        text: String(d.summary ?? ""),
        status: String(d.status ?? "completed"),
        workflow: String(d.workflow ?? ""),
        artifacts: Array.isArray(d.artifacts)
          ? (d.artifacts as unknown[]).map(String)
          : [],
      };
    case "chat.ceo_end": {
      const status = String(d.status ?? "");
      const reason = String(d.reason ?? d.error ?? "");
      const text =
        status === "error"
          ? `出错：${reason}`
          : status === "limit"
            ? `本轮决策达到上限${reason ? `（${reason}）` : ""}，任务可能未完全结束，可继续追问`
            : status === "blocked" || status === "exception" || status === "paused"
              ? `本轮执行暂停，任务尚未完成${reason ? `：${reason}` : "；成果与上下文保留，可继续处理"}`
            : `任务结束（${status}）`;
      return { ...base, kind: "end", status, reason, text: String(d.text || text) };
    }
    case "node.brain":
      return {
        ...base,
        kind: "brain",
        node: String(d.node ?? ""),
        action: String(d.action ?? ""),
        text: String(d.detail ?? ""),
      };
    case "node.eval":
      return {
        ...base,
        kind: "eval",
        node: String(d.node ?? ""),
        passed: typeof d.passed === "boolean" ? d.passed : undefined,
        score: d.score != null ? Number(d.score) : undefined,
        text: String(d.detail ?? ""),
      };
    case "a2a.delegate":
      return {
        ...base,
        kind: "collab",
        collabKind: "delegate",
        from: String(d.parent ?? ""),
        to: String(d.node ?? ""),
        text: String(d.summary ?? d.detail ?? ""),
      };
    case "a2a.verdict":
      return {
        ...base,
        kind: "collab",
        collabKind: "verdict",
        from: String(d.parent ?? ""),
        to: String(d.node ?? ""),
        passed: typeof d.passed === "boolean" ? d.passed : undefined,
        delta: d.score != null ? Number(d.score) : undefined,
        text: String(d.detail ?? ""),
      };
    case "a2a.escalate":
      return {
        ...base,
        kind: "collab",
        collabKind: "escalate",
        from: String(d.by ?? ""),
        to: String(d.node ?? ""),
        passed: typeof d.passed === "boolean" ? d.passed : undefined,
        delta: d.score != null ? Number(d.score) : undefined,
        text: String(d.detail ?? ""),
      };
    case "ceo.settle":
      return {
        ...base,
        kind: "collab",
        collabKind: "settle",
        to: String(d.node ?? ""),
        passed: String(d.outcome ?? "") === "passed",
        delta: d.delta != null ? Number(d.delta) : undefined,
        text: `CEO 直系结算（${String(d.outcome ?? "")}）`,
      };
    default:
      return null;
  }
}

// ---------------- 连续工具消息分组（渲染层用） ----------------

/** 聊天流渲染单元：普通消息原样，连续 tool 消息合并为一个可折叠组 */
export type ChatSegment =
  | { type: "msg"; m: ChatMessage }
  | { type: "tools"; items: ChatMessage[] };

/**
 * 把连续的工具消息合并成分组（2026-09-03 用户反馈：工具一条条铺太占高度，
 * 显示不下还产生横向滚动 → 折叠成一行芯片，点击展开）。
 * 单条工具也成组（组头统一显示「工具 ×1」，与多条同构）。
 */
export function groupChatMessages(messages: ChatMessage[]): ChatSegment[] {
  const out: ChatSegment[] = [];
  for (const m of messages) {
    if (m.kind === "tool") {
      const last = out[out.length - 1];
      if (last && last.type === "tools") {
        last.items.push(m);
      } else {
        out.push({ type: "tools", items: [m] });
      }
    } else {
      out.push({ type: "msg", m });
    }
  }
  return out;
}
