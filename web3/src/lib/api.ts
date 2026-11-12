// ============================================================
// REST API 客户端 — 与 web_app.py 路由一一对应
// 全部走同源相对路径：生产由 Flask 挂载 web3/dist，
// 开发由 Vite 代理 /api → 127.0.0.1:8021
// ============================================================

import type {
  EvolveRound,
  ExecutionDetail,
  MeshDelegation,
  ModelGroupInfo,
  ModelUpdatePayload,
  NodeDetail,
  NodeLogsResult,
  NodeSummary,
  QueueItem,
  SessionGraphItem,
  SessionListItem,
  SessionLoadResult,
  SettingsInfo,
  SettingsUpdatePayload,
  SkillInfo,
  StateSnapshot,
} from "@/types";

export class ApiError extends Error {
  status: number;
  payload: unknown;
  constructor(status: number, message: string, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

async function req<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const { timeoutMs = 30000, ...rest } = init ?? {};
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const abort = () => ctrl.abort();
  if (rest.signal?.aborted) ctrl.abort();
  rest.signal?.addEventListener("abort", abort, { once: true });
  try {
    const res = await fetch(path, {
      ...rest,
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json", ...(rest.headers ?? {}) },
    });
    const text = await res.text();
    let data: unknown = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }
    if (!res.ok) {
      const msg =
        (data as { error?: string } | null)?.error ??
        `HTTP ${res.status}`;
      throw new ApiError(res.status, msg, data);
    }
    return data as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(0, "请求超时", null);
    }
    throw new ApiError(0, e instanceof Error ? e.message : String(e), null);
  } finally {
    clearTimeout(timer);
    rest.signal?.removeEventListener("abort", abort);
  }
}

const j = (body: unknown) => JSON.stringify(body);

export const api = {
  // ---- 状态 ----
  state: (session?: string) => req<StateSnapshot>(`/api/state${session ? `?session=${encodeURIComponent(session)}` : ""}`, { timeoutMs: 15000 }),

  // ---- 对话 ----
  chat: (text: string, options?: { mode?: "queue" | "interrupt"; session?: string }) =>
    req<{ ok: boolean; error?: string; busy?: boolean; queued?: boolean; running?: boolean; message?: string; mode?: "queue" | "interrupt" }>(
      "/api/chat",
      { method: "POST", body: j({ text, ...options }), timeoutMs: 20000 },
    ),
  command: (text: string, session?: string) =>
    req<{ ok: boolean; message?: string }>(
      "/api/command",
      { method: "POST", body: j({ text, ...(session ? {session} : {}) }), timeoutMs: 15000 },
    ),

  projects: () => req<{ok: boolean; projects: import("@/types").ProjectItem[]}>("/api/projects?include_archived=1"),
  projectCreate: (name: string, workspace: string) => req<{ok: boolean; project: import("@/types").ProjectItem}>("/api/projects", {method:"POST",body:j({name,workspace})}),
  projectRename: (id: string, name: string) => req<{ok: boolean}>(`/api/projects/${encodeURIComponent(id)}`, {method:"PATCH",body:j({name})}),
  projectArchive: (id: string, archived: boolean) => req<{ok: boolean}>(`/api/projects/${encodeURIComponent(id)}`, {method:"PATCH",body:j({archived})}),
  projectNewSession: (id: string) => req<{ok: boolean; session: import("@/types").ProjectSession}>(`/api/projects/${encodeURIComponent(id)}/sessions`,{method:"POST",body:j({})}),
  sessionRename: (id: string, title: string) => req<{ok: boolean}>(`/api/sessions/${encodeURIComponent(id)}`,{method:"PATCH",body:j({title})}),
  sessionArchive: (id: string, archived: boolean) => req<{ok: boolean}>(`/api/sessions/${encodeURIComponent(id)}`,{method:"PATCH",body:j({archived})}),
  sessionMarkViewed: (id: string) => req<{ok: boolean}>(`/api/sessions/${encodeURIComponent(id)}`,{method:"PATCH",body:j({completion_seen:true})}),
  queueGuide: (id: string) => req<{ok: boolean; mode?: string; started?: boolean}>(`/api/queue/${encodeURIComponent(id)}/guide`,{method:"POST",body:j({})}),

  // ---- 任务队列（排队中的消息：查看 / 编辑 / 撤销，2026-10-22） ----
  queue: () =>
    req<{ ok: boolean; queue: QueueItem[] }>("/api/queue", { timeoutMs: 15000 }),
  queueUpdate: (id: string, text: string) =>
    req<{ ok: boolean; error?: string }>(`/api/queue/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: j({ text }),
      timeoutMs: 15000,
    }),
  queueRemove: (id: string) =>
    req<{ ok: boolean; error?: string }>(`/api/queue/${encodeURIComponent(id)}`, {
      method: "DELETE",
      timeoutMs: 15000,
    }),
  sessionNew: () =>
    req<{ ok: boolean; message?: string }>("/api/session/new", {
      method: "POST",
      timeoutMs: 15000,
    }),

  // ---- 进化 ----
  evolve: () =>
    req<{ ok: boolean; report?: EvolveRound; error?: string }>(
      "/api/evolve",
      { method: "POST", timeoutMs: 120000 },
    ),

  // ---- 模型配置 ----
  models: () =>
    req<{ ok: boolean; models: Record<"ceo" | "brain" | "worker", ModelGroupInfo> }>(
      "/api/models",
      { timeoutMs: 15000 },
    ),
  updateModels: (payload: ModelUpdatePayload) =>
    req<{ ok: boolean; msg?: string; error?: string }>(
      "/api/models",
      { method: "POST", body: j(payload), timeoutMs: 20000 },
    ),

  // ---- 运行/调优参数 ----
  settings: () =>
    req<SettingsInfo & { ok: boolean }>("/api/settings", { timeoutMs: 15000 }),
  updateSettings: (payload: SettingsUpdatePayload) =>
    req<{ ok: boolean; msg?: string; error?: string }>(
      "/api/settings",
      { method: "POST", body: j(payload), timeoutMs: 20000 },
    ),

  // ---- 会话 ----
  sessions: () => req<{ ok: boolean; sessions: SessionListItem[] }>("/api/sessions"),
  legacyWorkspaces: () => req<{workspaces: Record<string,string>}>(`${import.meta.env.BASE_URL}session-workspaces.json`,{timeoutMs:5000}),
  sessionGraphs: () =>
    req<{ ok: boolean; sessions: SessionGraphItem[] }>("/api/sessions/graphs", {
      timeoutMs: 30000,
    }),
  sessionLoad: (name: string) =>
    req<SessionLoadResult>("/api/session/load", {
      method: "POST",
      body: j({ name }),
      timeoutMs: 20000,
    }),
  sessionSaveGraph: (graph: unknown, name?: string) =>
    req<{ ok: boolean; error?: string }>(
      "/api/session/graph",
      { method: "POST", body: j({ graph, name }), timeoutMs: 20000 },
    ),

  // ---- 节点 ----
  execution: (id: string, signal?: AbortSignal) =>
    req<{ ok: boolean; execution?: ExecutionDetail; error?: string }>(
      `/api/executions/${encodeURIComponent(id)}`, { signal },
    ),
  nodes: () =>
    req<{ ok: boolean; nodes: NodeSummary[]; archived: NodeSummary[] }>("/api/nodes", {
      timeoutMs: 60000,
    }),
  meshDelegations: (node?: string) =>
    req<{ ok: boolean; delegations: MeshDelegation[]; total: number }>(
      `/api/mesh/delegations${node ? `?node=${encodeURIComponent(node)}` : ""}`,
      { timeoutMs: 15000 },
    ),
  nodeDetail: (id: string) =>
    req<{ ok: boolean; error?: string; node?: NodeDetail; skills?: SkillInfo[] }>(
      `/api/node/${encodeURIComponent(id)}`,
    ),
  /** 节点磁盘历史日志回退（实时缓冲为空时）：
   *  hermes = workspace/.adgs_logs/{id}_live.log 尾部
   *  agentlog = .hermes/profiles/{id}/logs/agent.log 尾部 */
  nodeLogs: (id: string, lines = 400) =>
    req<NodeLogsResult>(`/api/node/${encodeURIComponent(id)}/logs?lines=${lines}`),
  nodeDescribe: (id: string, text: string) =>
    req<{ ok: boolean; message?: string; error?: string }>(
      `/api/node/${encodeURIComponent(id)}/describe`,
      { method: "POST", body: j({ describe: text }), timeoutMs: 20000 },
    ),
  nodeSkillRemove: (id: string, name: string) =>
    req<{ ok: boolean; message?: string; error?: string }>(
      `/api/node/${encodeURIComponent(id)}/skill/remove`,
      { method: "POST", body: j({ name }), timeoutMs: 30000 },
    ),
  nodeSkillInstall: (id: string, fromNode: string, name: string) =>
    req<{ ok: boolean; message?: string; error?: string }>(
      `/api/node/${encodeURIComponent(id)}/skill/install`,
      {
        method: "POST",
        body: j({ from_node: fromNode, name }),
        timeoutMs: 60000,
      },
    ),
  nodeRevive: (id: string) =>
    req<{ ok: boolean; message?: string; error?: string }>(
      `/api/node/${encodeURIComponent(id)}/revive`,
      { method: "POST", timeoutMs: 30000 },
    ),
  nodeRemove: (id: string) =>
    req<{ ok: boolean; error?: string }>(
      `/api/node/${encodeURIComponent(id)}/remove`,
      { method: "POST", timeoutMs: 30000 },
    ),
};
