import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TerminalStream } from "@/lib/terminalStream";
import type { TerminalRecord } from "@/types";

type Source = { url: string; closed: boolean; onmessage: ((ev: { data: string }) => void) | null; onerror: ((ev: unknown) => void) | null };
const sources = () => (window as unknown as { __fakeSse: { instances: Source[] } }).__fakeSse.instances;
const send = (source: Source, seq: number, data = `chunk-${seq}`, type: TerminalRecord["type"] = "data") => source.onmessage?.({ data: JSON.stringify({ attempt_id: "a1", seq, type, data }) });

beforeEach(() => { sources().length = 0; vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe("durable read-only terminal feed", () => {
  it("preserves control sequences and batches output without repeated records", async () => {
    const write = vi.fn(async (_records: TerminalRecord[]) => {});
    const stream = new TerminalStream("a1", write, () => {});
    stream.start();
    const source = sources()[0];
    expect(source.url).toContain("after=0");
    send(source, 1, "\x1b[31m中文\x1b[0m\rprogress");
    send(source, 1, "duplicate");
    send(source, 2, "\x1b[2Kdone\r\n");
    await vi.advanceTimersByTimeAsync(50);
    expect(write).toHaveBeenCalledTimes(1);
    expect(write.mock.calls[0][0].map((r) => r.data)).toEqual(["\x1b[31m中文\x1b[0m\rprogress", "\x1b[2Kdone\r\n"]);
    stream.destroy();
  });

  it("fills a gap from the last processed cursor", async () => {
    const records: number[] = [];
    const stream = new TerminalStream("a1", async (batch) => { records.push(...batch.map((r) => r.seq)); }, () => {});
    stream.start();
    send(sources()[0], 1);
    await vi.advanceTimersByTimeAsync(50);
    send(sources()[0], 3);
    expect(sources()[0].closed).toBe(true);
    await vi.advanceTimersByTimeAsync(1050);
    expect(sources()[1].url).toContain("after=1");
    send(sources()[1], 2); send(sources()[1], 3);
    await vi.advanceTimersByTimeAsync(50);
    expect(records).toEqual([1, 2, 3]);
    stream.destroy();
  });

  it("commits only after asynchronous terminal parsing completes", async () => {
    let finish!: () => void;
    const stream = new TerminalStream("a1", () => new Promise<void>((resolve) => { finish = resolve; }), () => {});
    stream.start(); send(sources()[0], 1);
    await vi.advanceTimersByTimeAsync(50);
    sources()[0].onerror?.({});
    await vi.advanceTimersByTimeAsync(1200);
    expect(sources()).toHaveLength(1);
    finish(); await Promise.resolve();
    await vi.advanceTimersByTimeAsync(1050);
    expect(sources()[1].url).toContain("after=1");
    stream.destroy();
  });

  it("bounds queued output and resumes from disk instead of blocking a child", async () => {
    const statuses: string[] = [];
    const stream = new TerminalStream("a1", async () => {}, (s) => statuses.push(s));
    stream.start();
    send(sources()[0], 1, "x".repeat(520 * 1024));
    expect(sources()[0].closed).toBe(true);
    await vi.advanceTimersByTimeAsync(1200);
    expect(sources()[1].url).toContain("after=1");
    stream.destroy();
  });

  it("does not reconnect after an end record or disposal", async () => {
    const stream = new TerminalStream("a1", async () => {}, () => {});
    stream.start(); send(sources()[0], 1, "", "end");
    await vi.advanceTimersByTimeAsync(5000);
    expect(sources()[0].closed).toBe(true);
    expect(sources()).toHaveLength(1);
    stream.destroy();
  });
});
