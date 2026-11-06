import type { TerminalRecord } from "@/types";

/** A read-only, replayable sink. Slow viewers never pause the child process. */
export class TerminalStream {
  private source: EventSource | null = null;
  private queue: TerminalRecord[] = [];
  private queuedBytes = 0;
  private cursor = 0;
  private received = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private busy = false;
  private stopped = false;
  private reconnect = false;

  constructor(
    readonly attemptId: string,
    private write: (records: TerminalRecord[]) => Promise<void>,
    private status: (status: string) => void,
  ) {}

  start(): void { this.open(); }

  destroy(): void {
    this.stopped = true;
    this.source?.close();
    if (this.timer) clearTimeout(this.timer);
    this.queue = [];
  }

  private open(): void {
    if (this.stopped) return;
    this.reconnect = false;
    this.received = this.cursor;
    this.status(this.cursor ? "补收输出" : "连接终端");
    const es = new EventSource(`/api/attempts/${encodeURIComponent(this.attemptId)}/terminal?after=${this.cursor}`);
    this.source = es;
    es.onopen = () => { if (!this.stopped) this.status("实时连接"); };
    es.onmessage = (event) => {
      if (this.stopped || this.source !== es) return;
      let record: TerminalRecord;
      try { record = JSON.parse(String(event.data)) as TerminalRecord; } catch { return; }
      if (record.attempt_id !== this.attemptId || !Number.isSafeInteger(record.seq)) return;
      if (!["data", "resize", "end"].includes(record.type)) return;
      if (record.seq <= this.received) return;
      if (record.seq !== this.received + 1) {
        this.status("补收缺失输出");
        this.restart();
        return;
      }
      this.received = record.seq;
      this.queue.push(record);
      this.queuedBytes += record.data?.length ?? 0;
      if (this.queuedBytes > 512 * 1024) this.restart();
      this.schedule();
    };
    es.onerror = () => {
      if (this.stopped || this.source !== es) return;
      this.status("重新连接");
      this.restart();
    };
  }

  private restart(): void {
    this.source?.close();
    this.source = null;
    this.reconnect = true;
    this.schedule();
  }

  private schedule(): void {
    if (this.timer || this.busy || this.stopped) return;
    this.timer = setTimeout(() => { this.timer = null; void this.drain(); }, 50);
  }

  private async drain(): Promise<void> {
    if (this.stopped || this.busy) return;
    if (!this.queue.length) {
      if (this.reconnect) this.timer = setTimeout(() => { this.timer = null; this.open(); }, 1000);
      return;
    }
    const batch: TerminalRecord[] = [];
    let size = 0;
    while (this.queue.length && size < 64 * 1024) {
      const record = this.queue.shift()!;
      batch.push(record);
      const n = record.data?.length ?? 0;
      size += n;
      this.queuedBytes -= n;
      if (record.type !== "data") break;
    }
    this.busy = true;
    try {
      await this.write(batch);
      if (this.stopped) return;
      this.cursor = batch[batch.length - 1].seq;
      if (batch[batch.length - 1].type === "end") {
        this.status("执行已结束");
        this.destroy();
      }
    } catch {
      this.status("终端显示失败");
      this.destroy();
    } finally {
      this.busy = false;
      if (this.queue.length || this.reconnect) this.schedule();
    }
  }
}
