import "@testing-library/jest-dom/vitest";

// jsdom 无 matchMedia
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// jsdom 无 ResizeObserver
if (typeof globalThis !== "undefined" && !globalThis.ResizeObserver) {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = RO;
}

// jsdom 无 EventSource（SSE）——测试用可控桩
// 暴露全局控制器：window.__fakeSse = { instances, emit(ev) }
if (typeof globalThis !== "undefined" && !(globalThis as { EventSource?: unknown }).EventSource) {
  class FakeEventSource {
    // 与原生 EventSource 一致的静态常量（SseClient 看门狗会用 EventSource.OPEN 判定存活）
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 2;
    url: string;
    readyState = 0;
    onopen: ((e: unknown) => void) | null = null;
    onmessage: ((e: { data: string }) => void) | null = null;
    onerror: ((e: unknown) => void) | null = null;
    closed = false;
    private listeners = new Map<string, Set<(event: { data: string }) => void>>();
    constructor(url: string) {
      this.url = url;
      const w = window as unknown as { __fakeSse?: { instances: FakeEventSource[] } };
      (w.__fakeSse ??= { instances: [] as FakeEventSource[] }).instances.push(this);
    }
    close() {
      this.closed = true;
    }
    addEventListener(type: string, listener: (event: { data: string }) => void) {
      const listeners = this.listeners.get(type) ?? new Set();
      listeners.add(listener);
      this.listeners.set(type, listeners);
    }
    emitNamed(type: string, data = "{}") {
      if (this.closed) return;
      for (const listener of this.listeners.get(type) ?? []) listener({ data });
    }
  }
  (globalThis as { EventSource: unknown }).EventSource = FakeEventSource;
  if (typeof window !== "undefined") {
    (
      window as unknown as {
        __fakeSse?: { instances: FakeEventSource[]; emit: (ev: unknown) => void };
      }
    ).__fakeSse = {
      instances: [],
      emit: (ev: unknown) => {
        const w = window as unknown as { __fakeSse?: { instances: FakeEventSource[] } };
        for (const inst of w.__fakeSse?.instances ?? []) {
          if (!inst.closed) inst.onmessage?.({ data: JSON.stringify(ev) });
        }
      },
    };
  }
}
