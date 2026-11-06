// ============================================================
// SseClient 看门狗回归 — 关键覆盖「空闲但健康连接被误重建」：
// 后端每 15s 发命名 heartbeat；正常空闲不重连，停止心跳时 OPEN 也应恢复。
// ============================================================

import { afterEach, describe, expect, it, vi } from "vitest";
import { SseClient } from "@/lib/sse";
import type { SseStatus } from "@/lib/sse";

function fakeSse() {
  const w = window as unknown as {
    __fakeSse: { instances: { readyState: number; closed: boolean; onopen: (() => void) | null; emitNamed: (type: string, data?: string) => void }[] };
  };
  return w.__fakeSse;
}

afterEach(() => {
  vi.useRealTimers();
  const inst = fakeSse().instances;
  for (const i of inst) i.onopen = null;
  inst.length = 0;
});

describe("SseClient 看门狗", () => {
  it("uses the snapshot cursor, deduplicates replay, and resumes after consumed events", () => {
    vi.useFakeTimers();
    const events: number[] = [];
    const client = new SseClient("/api/events", { onEvent: (ev) => events.push(ev.id!) }, 8);
    client.start();
    const source = fakeSse().instances[0] as unknown as { url: string; readyState: number; onmessage: (ev: { data: string }) => void };
    expect(source.url).toBe("/api/events?after=8");
    const frame = (id: number) => source.onmessage({ data: JSON.stringify({ id, type: "node.status", ts: 100, data: {} }) });
    frame(8); frame(9); frame(9); frame(10);
    expect(events).toEqual([9, 10]);
    source.readyState = 0;
    vi.advanceTimersByTime(60000);
    const reopened = fakeSse().instances[1] as unknown as { url: string };
    expect(reopened.url).toBe("/api/events?after=10");
    client.destroy();
  });
  it("named heartbeats keep an idle connection healthy without creating workflow events", () => {
    vi.useFakeTimers();
    const statuses: SseStatus[] = [];
    const onEvent = vi.fn();
    const client = new SseClient("/api/events", { onEvent, onStatus: (s) => statuses.push(s) });
    client.start();
    const es = fakeSse().instances[0];
    // Transport heartbeats do not enter the chat/graph event reducer.
    es.readyState = 1; // OPEN
    es.onopen?.();

    const heartbeat = setInterval(() => es.emitNamed("heartbeat"), 15000);
    vi.advanceTimersByTime(120_000);

    expect(fakeSse().instances.length).toBe(1); // 无重建
    expect(statuses.at(-1)).toBe("open");
    expect(onEvent).not.toHaveBeenCalled();
    clearInterval(heartbeat);
    client.destroy();
  });

  it("recovers an OPEN connection when named heartbeats stop", () => {
    vi.useFakeTimers();
    const client = new SseClient("/api/events", { onEvent: () => {} }, 17);
    client.start();
    const es = fakeSse().instances[0];
    es.readyState = 1; es.onopen?.();
    vi.advanceTimersByTime(15000);
    es.emitNamed("heartbeat");
    vi.advanceTimersByTime(35000);
    expect(es.closed).toBe(true);
    expect(fakeSse().instances.length).toBe(2);
    expect((fakeSse().instances[1] as unknown as { url: string }).url).toBe("/api/events?after=17");
    client.destroy();
  });

  it("浏览器已不视连接 OPEN 且无数据流时，看门狗仍会重建（半死兜底保留）", () => {
    vi.useFakeTimers();
    const statuses: SseStatus[] = [];
    const client = new SseClient("/api/events", { onEvent: () => {}, onStatus: (s) => statuses.push(s) });
    client.start();
    const es1 = fakeSse().instances[0];
    es1.readyState = 1;
    es1.onopen?.(); // open，lastEventAt 定格

    // 模拟连接半死：浏览器不再保持 OPEN，但也没触发 onerror、无 data
    es1.readyState = 0;
    vi.advanceTimersByTime(120_000);

    expect(fakeSse().instances.length).toBeGreaterThanOrEqual(2); // Rebuild also covers a retry stuck before open.
    expect(es1.closed).toBe(true); // 旧连接已关闭
    expect(statuses.at(-1)).toBe("connecting");
    client.destroy();
  });
});
