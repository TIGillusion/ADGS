// ============================================================
// REST 客户端单元测试（fetch 打桩，不触网）
// ============================================================

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/lib/api";
import type { StateSnapshot } from "@/types";

function jsonResponse(body: unknown, init?: { status?: number }) {
  return new Response(JSON.stringify(body), {
    status: init?.status ?? 200,
    headers: { "Content-Type": "application/json" },
  });
}

function mockFetchOnce(impl: (input: RequestInfo | URL, init?: RequestInit) => Response | Promise<Response>) {
  return vi.stubGlobal("fetch", vi.fn(impl));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api 基本请求", () => {
  it("GET /api/state 解析 JSON", async () => {
    const snap: StateSnapshot = {
      ok: true,
      workspace: "/ws",
      population: { active: 3, capacity: 8 },
      models: { ceo: "deepseek", brain: "qwen", worker: "hermes" },
      nodes: [],
      events: [],
      busy: false,
      queue_len: 0,
      boot_ts: 1700000000,
    };
    mockFetchOnce(() => jsonResponse(snap));
    const res = await api.state();
    expect(res.boot_ts).toBe(1700000000);
  });

  it("POST /api/chat 携带 JSON body", async () => {
    let captured: { url?: string; body?: string; method?: string } = {};
    mockFetchOnce((input, init) => {
      captured = { url: String(input), body: String(init?.body), method: init?.method };
      return jsonResponse({ ok: true });
    });
    await api.chat("做个爬虫");
    expect(captured.url).toBe("/api/chat");
    expect(captured.method).toBe("POST");
    expect(JSON.parse(captured.body!)).toEqual({ text: "做个爬虫" });
  });

  it("POST /api/session/graph body 只含 graph", async () => {
    let captured = "";
    mockFetchOnce((_i, init) => {
      captured = String(init?.body);
      return jsonResponse({ ok: true });
    });
    await api.sessionSaveGraph([{ key: "ceo" }]);
    expect(JSON.parse(captured)).toEqual({ graph: [{ key: "ceo" }] });
  });

  it("节点路由 URL 编码", async () => {
    let url = "";
    mockFetchOnce((input) => {
      url = String(input);
      return jsonResponse({ ok: true, node: {}, skills: [] });
    });
    await api.nodeDetail("a b/1");
    expect(url).toBe("/api/node/a%20b%2F1");
  });

  it("GET /api/node/<id>/logs 带行数参数（磁盘历史日志回退）", async () => {
    let url = "";
    mockFetchOnce((input) => {
      url = String(input);
      return jsonResponse({ ok: true, node: "ags_9", hermes: null, agentlog: null });
    });
    const res = await api.nodeLogs("ags_9");
    expect(url).toBe("/api/node/ags_9/logs?lines=400");
    expect(res.ok).toBe(true);
    expect(res.hermes).toBeNull();
    // 显式行数参数 → 自定义 URL（同一 stub 复用，只验证请求路径）
    await api.nodeLogs("ags_9", 1000);
    expect(String(vi.mocked(fetch).mock.calls.at(-1)![0])).toBe(
      "/api/node/ags_9/logs?lines=1000",
    );
  });

  it("非 JSON 响应 → raw 包装", async () => {
    mockFetchOnce(() => new Response("oops html", { status: 500 }));
    await expect(api.state()).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      payload: { raw: "oops html" },
    });
  });

  it("POST /api/models 携带模型修改 body", async () => {
    let url = "";
    let body = "";
    let method: string | undefined;
    mockFetchOnce((input, init) => {
      url = String(input);
      body = String(init?.body);
      method = init?.method;
      return jsonResponse({ ok: true, msg: "ok" });
    });
    await api.updateModels({ worker: { model: "qwen4-32b" } });
    expect(url).toBe("/api/models");
    expect(method).toBe("POST");
    expect(JSON.parse(body)).toEqual({ worker: { model: "qwen4-32b" } });
  });

  it("POST /api/settings 携带运行/调优修改 body；GET 回读", async () => {
    let url = "";
    let body = "";
    let method: string | undefined;
    mockFetchOnce((input, init) => {
      url = String(input);
      body = String(init?.body);
      method = init?.method;
      return jsonResponse({ ok: true, msg: "ok" });
    });
    await api.updateSettings({ runtime: { max_workers: 4 }, advanced: { explore_beta: 0 } });
    expect(url).toBe("/api/settings");
    expect(method).toBe("POST");
    expect(JSON.parse(body)).toEqual({
      runtime: { max_workers: 4 },
      advanced: { explore_beta: 0 },
    });
    mockFetchOnce(() =>
      jsonResponse({
        ok: true,
        runtime: { max_workers: 4, max_delegation_depth: 5, worker_timeout: 3600, inactivity_timeout: 1800 },
        advanced: { tau: 8, sim_weight: 1.5, score_weight: 0.5, explore_beta: 0, top_k: 5, temperature: 0.3 },
      }),
    );
    const s = await api.settings();
    expect(s.runtime.max_workers).toBe(4);
    expect(s.advanced.explore_beta).toBe(0);
  });
});

describe("api 错误处理", () => {
  it("HTTP 错误优先取 body.error", async () => {
    mockFetchOnce(() => jsonResponse({ ok: false, error: "会话不存在" }, { status: 404 }));
    try {
      await api.sessionLoad("nope");
      throw new Error("不应到达");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(404);
      expect((e as ApiError).message).toBe("会话不存在");
    }
  });

  it("HTTP 错误无 error 字段 → HTTP 状态码", async () => {
    mockFetchOnce(() => jsonResponse({ ok: false }, { status: 500 }));
    await expect(api.state()).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      message: "HTTP 500",
    });
  });

  it("网络失败 → ApiError(0)", async () => {
    mockFetchOnce(() => Promise.reject(new TypeError("network down")));
    await expect(api.state()).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
      message: "network down",
    });
  });

  it("AbortError → 请求超时", async () => {
    mockFetchOnce(() =>
      Promise.reject(
        new DOMException("The operation was aborted.", "AbortError"),
      ),
    );
    await expect(api.state()).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
      message: "请求超时",
    });
  });
});

it("API 明确传递批次引导模式和会话归属", async () => {
  mockFetchOnce(() => jsonResponse({ok:true,queued:true}));
  await api.chat('改进方向',{mode:'interrupt',session:'running-chat'});
  const body=JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body));
  expect(body).toEqual({text:'改进方向',mode:'interrupt',session:'running-chat'});
});
