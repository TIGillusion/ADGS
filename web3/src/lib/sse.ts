// ============================================================
// SSE 事件流客户端（/api/events）
// - 基于原生 EventSource（自动重连），外层再加看门狗：
//   EventSource 重连风暴或服务器半死时强制重建
// - 状态机：connecting → open → error（暴露给顶栏指示器）
// - 事件解析为 BusEvent 后交给回调（调用方负责会话过滤/分发）
// ============================================================

import type { BusEvent } from "@/types";

export type SseStatus = "connecting" | "open" | "reconnecting" | "error";

export interface SseHandlers {
  onEvent: (ev: BusEvent) => void;
  onStatus?: (status: SseStatus) => void;
}

const WATCHDOG_MS = 25000; // 25s without data/open/named heartbeat → reconnect
const MAX_RETRIES = 6;

export class SseClient {
  private es: EventSource | null = null;
  private watchdog: number | null = null;
  private lastEventAt = 0;
  private retries = 0;
  private destroyed = false;
  private retryTimer: number | null = null;
  private cursor = 0;
  private handlers: SseHandlers;
  url: string;

  constructor(url = "/api/events", handlers: SseHandlers, after = 0) {
    this.url = url;
    this.handlers = handlers;
    this.cursor = after;
  }

  start(): void {
    this.destroyed = false;
    this.open();
  }

  destroy(): void {
    this.destroyed = true;
    this.closeInner();
    if (this.watchdog != null) window.clearTimeout(this.watchdog);
    this.watchdog = null;
    if (this.retryTimer != null) window.clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }

  private closeInner(): void {
    try {
      this.es?.close();
    } catch {
      /* ignore */
    }
    this.es = null;
  }

  private open(): void {
    if (this.destroyed) return;
    this.closeInner();
    this.handlers.onStatus?.(this.retries > 0 ? "reconnecting" : "connecting");
    const url = this.cursor > 0 ? `${this.url}${this.url.includes("?") ? "&" : "?"}after=${this.cursor}` : this.url;
    const es = new EventSource(url);
    this.es = es;
    this.lastEventAt = Date.now();
    // A request stuck before response headers never emits open/error.
    this.armWatchdog();

    // Named heartbeat frames are transport health, not workflow events/cursors.
    es.addEventListener("heartbeat", () => {
      if (!this.destroyed && this.es === es) this.lastEventAt = Date.now();
    });

    es.onopen = () => {
      this.retries = 0;
      this.lastEventAt = Date.now();
      this.handlers.onStatus?.("open");
      this.armWatchdog();
    };

    es.onmessage = (msg: MessageEvent) => {
      this.lastEventAt = Date.now();
      let ev: BusEvent;
      try {
        ev = JSON.parse(String(msg.data)) as BusEvent;
      } catch {
        return; // 非 JSON 心跳行，忽略
      }
      if (!ev || typeof ev !== "object" || !("type" in ev)) return;
      const id = Number(ev.id ?? msg.lastEventId ?? 0);
      if (id > 0) {
        if (id <= this.cursor) return;
        ev.id = id;
        this.cursor = id;
      }
      try {
        this.handlers.onEvent(ev);
      } catch (err) {
        // 回调异常不能打断流
        console.error("[sse] onEvent handler error:", err);
      }
    };

    es.onerror = () => {
      // EventSource 会自动重连；这里记账 + 兜底重建
      if (this.destroyed) return;
      this.retries += 1;
      if (this.retries > MAX_RETRIES) {
        this.closeInner();
        this.handlers.onStatus?.("error");
        // 10s 后再整体重建（避免疯狂重连占满后端连接）
        if (this.watchdog != null) window.clearTimeout(this.watchdog);
        this.retryTimer = window.setTimeout(() => {
          if (!this.destroyed) {
            this.retries = 0;
            this.open();
          }
        }, 10000);
        return;
      }
      this.handlers.onStatus?.("reconnecting");
      this.armWatchdog();
    };
  }

  /** OPEN alone cannot prove health; data/open/named heartbeats renew the lease. */
  private armWatchdog(): void {
    if (this.watchdog != null) window.clearTimeout(this.watchdog);
    const delay = Math.max(1, WATCHDOG_MS - (Date.now() - this.lastEventAt));
    this.watchdog = window.setTimeout(() => {
      if (this.destroyed) return;
      if (Date.now() - this.lastEventAt >= WATCHDOG_MS) {
        // This also recovers an OPEN socket whose server stopped making progress.
        this.closeInner();
        this.handlers.onStatus?.("reconnecting");
        this.retries = 0;
        this.open();
      } else {
        this.armWatchdog(); // 还有活动，继续观察
      }
    }, delay);
  }
}
