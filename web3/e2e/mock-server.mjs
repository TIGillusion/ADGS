// ============================================================
// ADGS web3 E2E — 本地 mock 后端（零依赖 Node http）
// 完整实现 /api/* 契约 + /api/events SSE，并内置任务生命周期
// 事件脚本；/e2e/* 仅供测试断言（introspection/reset）
// ============================================================

import http from "node:http";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.resolve(__dirname, "../dist");
const PORT = Number(process.env.E2E_PORT ?? 8099);

// ---------------- 内存状态 ----------------

const bootTs = Math.floor(Date.now() / 1000);

// 模型配置（/api/models，2026-09-27 web 配置入口）：POST 可改，GET 回读
const mockModels = {
  ceo: { model: "deepseek-chat", url: "https://api.deepseek.com/v1" },
  brain: { model: "qwen3.8-27b", url: "http://8.138.131.119:9000/v1" },
  worker: { model: "qwen3.8-27b", url: "http://8.138.131.119:9000/v1" },
};

// 运行/调优参数（/api/settings，2026-09-28 系统设置）：POST 可改，GET 回读
const mockSettings = {
  runtime: { max_workers: 2, max_delegation_depth: 5, worker_timeout: 3600, inactivity_timeout: 1800 },
  advanced: { tau: 8, sim_weight: 1.5, score_weight: 0.5, explore_beta: 0.15, top_k: 5, temperature: 0.3 },
};

const activeNodes = [
  {
    id: "ags_1",
    describe: "擅长网页抓取与结构化抽取，对 JS 渲染页面有稳定处理路径",
    score: 88.5,
    success_rate: 0.83,
    tasks_done: 12,
    gen: 2,
    birth_type: "recombine",
    parents: ["ags_init_1"],
    status: "idle",
    created_at: bootTs - 86400 * 3,
    updated_at: bootTs - 3600,
    skills: ["web_scrape", "json_parse"],
    model: "qwen3.6-27b",
  },
  {
    id: "ags_2",
    describe: "长文写作与排版，擅长按大纲分节生成并保持风格一致",
    score: 72.0,
    success_rate: 0.67,
    tasks_done: 9,
    gen: 1,
    birth_type: "initial",
    parents: [],
    status: "idle",
    created_at: bootTs - 86400 * 7,
    updated_at: bootTs - 7200,
    skills: ["long_form_writing"],
    model: "qwen3.6-27b",
  },
  {
    id: "ags_3",
    describe: "数据分析与图表生成，熟悉 pandas 与常见可视化库",
    score: 55.5,
    success_rate: 0.5,
    tasks_done: 4,
    gen: 3,
    birth_type: "whiteboard",
    parents: ["ags_1", "ags_2"],
    status: "idle",
    created_at: bootTs - 86400,
    updated_at: bootTs - 600,
    skills: ["pandas"],
    model: "qwen3.8-27b",
  },
];

const archivedNodes = [
  {
    id: "ags_0",
    describe: "早期通用节点，适应度偏低被自然淘汰",
    score: 31.0,
    success_rate: 0.2,
    tasks_done: 3,
    gen: 1,
    birth_type: "initial",
    parents: [],
    status: "archived",
    archived_at: bootTs - 86400 * 2,
    skills: [],
  },
];

const nodeSkills = {
  ags_1: [
    { name: "web_scrape", desc: "网页抓取技能：请求 → 选择器抽取 → 清洗", content: "# web_scrape\n\n步骤：\n1. 发起请求\n2. 选择器抽取\n3. 清洗输出 JSON" },
    { name: "json_parse", desc: "稳健 JSON 解析与字段归一", content: "# json_parse\n\n容错解析，字段名归一为 snake_case" },
  ],
  ags_2: [
    { name: "long_form_writing", desc: "长文写作：大纲 → 分节 → 润色", content: "# long_form_writing\n\n保持风格一致" },
  ],
  ags_3: [{ name: "pandas", desc: "pandas 数据处理套路", content: "# pandas\n\nread_csv → groupby → 可视化" }],
  ags_0: [],
};

// 预置历史会话（旧版图谱字段：x/y/tx/ty/pinned/type=clone/extra.overall）
const demoSession = {
  name: "demo-session",
  saved_at: "2026-08-20T10:00:00",  history: [
    { role: "user", content: "把这篇英文摘要翻译成中文并润色" },
    { role: "assistant", content: "好的，我来处理这段摘要。" },
  ],
  graph: [
    {
      key: "round-1",
      id: "round-1",
      type: "round",
      label: "轮 1",
      parent: "ceo",
      status: "idle",
      x: 40, y: 200, tx: 40, ty: 200, pinned: false,
      extra: { user_text: "把这篇英文摘要翻译成中文并润色" },
    },
    {
      key: "t1_1_old",
      id: "t1_1_old",
      type: "task",
      label: "1",
      parent: "round-1",
      status: "idle",
      x: 160, y: 180, tx: 160, ty: 180, pinned: true,
      extra: { goal: "翻译摘要", overall: "翻译并润色", seq: 1, count: 1 },
    },
    {
      key: "ags_2@t1_1_old",
      id: "ags_2",
      type: "clone",
      label: "ags_2",
      parent: "t1_1_old",
      status: "done",
      x: 300, y: 180, tx: 300, ty: 180, pinned: false,
      extra: { score: 81.0, passed: true, reworks: 0, profile: "ags_2", mode: "clone" },
    },
  ],
};

// 2026-09-04：多轮会话（5 轮）——专测"载入 >3 轮会话默认聚焦最近轮 + 轮次下拉"
const multiSession = {
  name: "multi-round",
  saved_at: "2026-08-21T09:00:00",
  history: [
    { role: "user", content: "多轮长任务" },
    { role: "assistant", content: "好的，分五轮执行。" },
  ],
  graph: [
    ...[1, 2, 3, 4, 5].map((i) => ({
      key: `round-${i}`,
      id: `round-${i}`,
      type: "round",
      label: `轮 ${i}`,
      parent: "ceo",
      status: "idle",
      extra: { user_text: `第 ${i} 轮` },
    })),
    {
      key: "t5_1_multi",
      id: "t5_1_multi",
      type: "task",
      label: "1",
      parent: "round-5",
      status: "idle",
      extra: { goal: "第五轮任务", seq: 1, count: 1 },
    },
    {
      key: "ags_3@t5_1_multi",
      id: "ags_3",
      type: "node",
      label: "ags_3",
      parent: "t5_1_multi",
      status: "done",
      extra: { score: 77.7, passed: true, reworks: 0, profile: "ags_3", mode: "direct" },
    },
  ],
};

const state = {
  busy: false,
  queueLen: 0,
  queue: [],
  savedGraphs: [], // {name, at, graph}
  currentSess: null, // 服务端单指针（模拟 _current_sess）
  chats: [],
  commands: [],
  events: [], // 最近事件（/api/state.events 重放）
};
let eventSeq = 0;
const executions = new Map();
const terminalRecords = new Map();
const terminalClients = new Map();

// ---------------- 工具 ----------------

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-cache",
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve) => {
    let buf = "";
    req.on("data", (c) => (buf += c));
    req.on("end", () => {
      try {
        resolve(buf ? JSON.parse(buf) : {});
      } catch {
        resolve({});
      }
    });
  });
}

function pushEvent(type, data, session) {
  const ev = { id: ++eventSeq, type, ts: Date.now() / 1000, data };
  if (session) ev.session = session;
  state.events.push(ev);
  if (state.events.length > 1500) state.events.shift();
  broadcast(ev);
  return ev;
}

// ---------------- SSE ----------------

const sseClients = new Set();

function broadcast(ev) {
  const frame = `id: ${ev.id}\ndata: ${JSON.stringify(ev)}\n\n`;
  for (const res of sseClients) {
    try {
      res.write(frame);
    } catch {
      sseClients.delete(res);
    }
  }
}

// ---------------- 任务生命周期脚本 ----------------

// 跨提交的累计 seq 游标：模拟真实后端的漂移语义——ask_user 挂起恢复 /
// 异常恢复后 _task_seq 不重置，新一次提交的任务接着上轮 seq 继续（3, 4…）。
// 前端必须按轮重编号显示 1..N（2026-09-03 用户实测"轮 4 从 2 开始"回归）。
let taskSeqCursor = 0;

function taskSeqSteps(text, session, idA, idB, seqA, seqB) {
  const s = { text, session };
  return [
    { ms: 60, run: (s) => pushEvent("chat.user", { text: s.text }, s.session) },
    { ms: 240, run: (s) => pushEvent("chat.ceo_report", { summary: "已拆解为两个子任务", next_plan: "并行派发" }, s.session) },
    { ms: 420, run: (s) => pushEvent("graph.task", { id: idA, name: "页面抓取A", goal: "抓取页面A", overall: s.text, parent: "ceo", seq: seqA, count: 2 }, s.session) },
    { ms: 480, run: (s) => pushEvent("graph.task", { id: idB, name: "页面抓取B", goal: "抓取页面B", overall: s.text, parent: "ceo", seq: seqB, count: 2 }, s.session) },
    { ms: 640, run: (s) => pushEvent("graph.dispatch", { parent: "ceo", task: idA, node: "ags_1", mode: "direct", goal: "抓取页面A" }, s.session) },
    { ms: 700, run: (s) => pushEvent("graph.dispatch", { parent: "ceo", task: idB, node: "ags_1", mode: "clone", goal: "抓取页面B" }, s.session) },
    { ms: 760, run: (s) => pushEvent("graph.clone", { node: "ags_1", profile: "ags_1_clone_e2e" }, s.session) },
    { ms: 950, run: (s) => pushEvent("node.status", { node: "ags_1", status: "running", task: idA }, s.session) },
    { ms: 1000, run: (s) => pushEvent("node.status", { node: "ags_1", status: "running", task: idB, profile: "ags_1_clone_e2e" }, s.session) },
    { ms: 1150, run: (s) => pushEvent("hermes.start", { node: "ags_1", profile: "ags_1_clone_e2e", phase: "执行中", brief: "抓取B" }, s.session) },
    { ms: 1300, run: (s) => pushEvent("hermes.line", { profile: "ags_1_clone_e2e", line: "GET https://example.com … 200" }, s.session) },
    { ms: 1450, run: (s) => pushEvent("hermes.line", { profile: "ags_1_clone_e2e", line: "抽取 42 个字段" }, s.session) },
    { ms: 1700, run: (s) => pushEvent("hermes.end", { node: "ags_1", profile: "ags_1_clone_e2e", phase: "执行结束" }, s.session) },
    { ms: 1900, run: (s) => pushEvent("node.eval", { node: "ags_1", passed: true, score: 92.0, reworks: 0, detail: "字段完整，无缺失" }, s.session) },
    { ms: 2000, run: (s) => pushEvent("node.status", { node: "ags_1", status: "done", task: idA, passed: true, score: 92.0, reworks: 0 }, s.session) },
    { ms: 2300, run: (s) => pushEvent("node.eval", { node: "ags_1", passed: true, score: 78.5, reworks: 1, detail: "基本完成，一处格式偏差已返工" }, s.session) },
    { ms: 2400, run: (s) => pushEvent("node.status", { node: "ags_1", status: "done", task: idB, profile: "ags_1_clone_e2e", passed: true, score: 78.5, reworks: 1 }, s.session) },
    { ms: 2700, run: (s) => pushEvent("chat.ceo_finish", { summary: "两个页面抓取完成，结果已落盘", workflow: "拆解 → 并行派发 ags_1（本体+克隆） → 汇总", artifacts: ["out/a.json", "out/b.json"] }, s.session) },
    { ms: 2850, run: (s) => { pushEvent("system", { msg: "任务完成" }); state.busy = false; state.queueLen = state.queue.length; } },
  ];
}

function scheduleTask(text) {
  const session = `auto-e2e-${Date.now()}`;
  // 第一次提交 seq=1,2（后端正常归零）；第二次起 seq=3,4…（漂移场景）
  const seqA = taskSeqCursor + 1;
  const seqB = taskSeqCursor + 2;
  taskSeqCursor += 2;
  const idA = `t2_${seqA}_e2e`;
  const idB = `t2_${seqB}_e2e`;
  for (const step of taskSeqSteps(text, session, idA, idB, seqA, seqB)) {
    setTimeout(step.run.bind(null, { text, session }), step.ms);
  }
  return session;
}

function runEvolveRound() {
  const report = {
    n_before: 3,
    n_after: 4,
    pressure: 0.35,
    effective_capacity: 8,
    elimination_target: 1,
    births: [{ id: "ags_4", type: "recombine", score: 50.0, parents: ["ags_1", "ags_3"], skills: ["web_scrape"] }],
    eliminations: [{ id: "ags_3", describe: "数据分析与图表生成，熟悉 pandas 与常见可视化库" }],
    pop_avg_score: 72.0,
    elim_avg_score: 55.5,
    score_gap: -16.5,
  };
  pushEvent("evolve.round", report);
  return report;
}

// ---------------- 静态资源 ----------------

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".json": "application/json",
  ".map": "application/json",
  ".woff2": "font/woff2",
};

function serveStatic(req, res, urlPath) {
  let p = urlPath === "/" ? "/index.html" : urlPath;
  const file = path.join(DIST, path.normalize(p));
  if (!file.startsWith(DIST) || !existsSync(file)) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("not found");
    return;
  }
  const ext = path.extname(file);
  res.writeHead(200, { "Content-Type": MIME[ext] ?? "application/octet-stream" });
  res.end(readFileSync(file));
}

// ---------------- 路由 ----------------

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const p = u.pathname;
  const m = req.method;

  // 测试内省
  if (p === "/e2e/state" && m === "GET") return sendJson(res, 200, state);
  if (p === "/e2e/terminal" && m === "POST") {
    const executionId = "exec-terminal-demo";
    const attemptId = "attempt-terminal-demo";
    state.currentSess = "terminal-demo";
    pushEvent("chat.user", { text: "验证子节点终端与实时观测" }, state.currentSess);
    pushEvent("graph.task", { id: "terminal-task", name: "执行观测", goal: "运行测试并检查证据", parent: "ceo", seq: 1 }, state.currentSess);
    pushEvent("graph.dispatch", { node: "ags_1", task: "terminal-task", execution_id: executionId, profile: "ags_1" }, state.currentSess);
    pushEvent("node.status", { node: "ags_1", execution_id: executionId, status: "running", phase: "executing" }, state.currentSess);
    const events = [pushEvent("node.brain", { node: "ags_1", execution_id: executionId, action: "plan_hermes", detail: "检查构建、生成报告并提交验收" }, state.currentSess)];
    executions.set(executionId, { execution_id: executionId, node_id: "ags_1", profile: "ags_1", task_id: "terminal-task", run_id: "terminal-demo", state: "running", phase: "执行与验证", started_at: Date.now() / 1000 - 8, last_activity_at: Date.now() / 1000, attempts: [{ attempt_id: attemptId, cols: 120, rows: 36, state: "running", last_seq: 4 }], events });
    terminalRecords.set(attemptId, [
      { seq: 1, attempt_id: attemptId, type: "resize", cols: 120, rows: 36 },
      { seq: 2, attempt_id: attemptId, type: "data", data: "\x1b[38;5;115mHERMES · AGS 1\x1b[0m\r\n\r\nTask: 验证执行证据与报告格式\r\nProfile: ags_1  |  Session: session-demo\r\n\r\n" },
      { seq: 3, attempt_id: attemptId, type: "data", data: "\x1b[36m[tool] terminal\x1b[0m  npm run build\r\n\x1b[32m✓\x1b[0m TypeScript checks passed\r\n\x1b[32m✓\x1b[0m 84 modules transformed\r\n\r\n" },
      { seq: 4, attempt_id: attemptId, type: "data", data: "\x1b[33m[verification]\x1b[0m collecting acceptance evidence...\r\n\r\n通过：构建检查、测试结果、交付文件\r\n下一步：补充格式化验收报告\r\n" },
    ]);
    return sendJson(res, 200, { ok: true });
  }
  if (p === "/e2e/terminal/push" && m === "POST") {
    const { data = "", evaluate = false } = await readBody(req);
    const attemptId = "attempt-terminal-demo";
    const records = terminalRecords.get(attemptId) ?? [];
    const record = { seq: records.length + 1, attempt_id: attemptId, type: "data", data };
    records.push(record);
    for (const client of terminalClients.get(attemptId) ?? []) client.write(`id: ${record.seq}\ndata: ${JSON.stringify(record)}\n\n`);
    if (evaluate) {
      const execution = executions.get("exec-terminal-demo");
      const ev = pushEvent("node.eval", { node: "ags_1", execution_id: "exec-terminal-demo", passed: true, score: 91.5, passed_items: ["构建检查", "测试结果", "交付文件"], failed_items: [], evidence: ["build.log", "report.md"], detail: "验收证据齐全，报告格式已补充" }, state.currentSess);
      execution.events.push(ev);
    }
    return sendJson(res, 200, { ok: true, seq: record.seq });
  }
  if (p === "/e2e/reset" && m === "POST") {
    state.busy = false;
    state.queueLen = 0;
    state.queue = [];
    state.savedGraphs = [];
    state.currentSess = null;
    state.chats = [];
    state.commands = [];
    state.events = [];
    executions.clear();
    terminalRecords.clear();
    return sendJson(res, 200, { ok: true });
  }

  if (p.startsWith("/api/")) {
    try {
      // ---- 状态 ----
      if (p === "/api/state" && m === "GET") {
        return sendJson(res, 200, {
          ok: true,
          workspace: "D:\\e2e\\workspace",
          population: { active: activeNodes.length, capacity: 8 },
          models: { ceo: mockModels.ceo.model, brain: mockModels.brain.model, worker: mockModels.worker.model },
          runtime: { max_workers: mockSettings.runtime.max_workers },
          advanced: { tau: mockSettings.advanced.tau, explore_beta: mockSettings.advanced.explore_beta },
          nodes: activeNodes,
          events: state.events,
          busy: state.busy,
          queue_len: state.queueLen,
          boot_ts: bootTs,
        });
      }

      // ---- 模型配置（2026-09-27 web 配置入口）----
      if (p === "/api/models" && m === "GET") {
        return sendJson(res, 200, { ok: true, models: mockModels });
      }
      if (p === "/api/models" && m === "POST") {
        const body = await readBody(req);
        const parts = [];
        for (const g of ["ceo", "brain", "worker"]) {
          const ch = body?.[g];
          if (!ch || typeof ch !== "object") continue;
          for (const f of ["model", "url", "key"]) {
            if (typeof ch[f] === "string" && ch[f].trim()) mockModels[g][f] = ch[f].trim();
          }
          if (typeof ch.model === "string" && ch.model.trim()) {
            parts.push(`${g} → ${ch.model.trim()}${g === "worker" ? "（仅之后新建的节点使用）" : ""}`);
          }
        }
        if (!parts.length) return sendJson(res, 400, { ok: false, error: "没有可应用的修改" });
        const msg = `模型配置已更新：${parts.join("；")}`;
        pushEvent("system", { msg });
        return sendJson(res, 200, { ok: true, msg, models: mockModels });
      }

      // ---- 运行/调优参数（2026-09-28 系统设置）----
      if (p === "/api/settings" && m === "GET") {
        return sendJson(res, 200, { ok: true, ...mockSettings });
      }
      if (p === "/api/settings" && m === "POST") {
        const body = await readBody(req);
        const parts = [];
        for (const [group, spec] of [
          ["runtime", { max_workers: [1, 32, "int"], max_delegation_depth: [1, 10, "int"], worker_timeout: [60, 86400, "int"], inactivity_timeout: [60, 14400, "int"] }],
          ["advanced", { tau: [0, 20, "num"], sim_weight: [0, 10, "num"], score_weight: [0, 10, "num"], explore_beta: [0, 3, "num"], top_k: [1, 20, "int"], temperature: [0, 2, "num"] }],
        ]) {
          const ch = body?.[group];
          if (!ch || typeof ch !== "object") continue;
          for (const [k, [lo, hi, typ]] of Object.entries(spec)) {
            if (ch[k] === undefined || ch[k] === null || ch[k] === "") continue;
            const v = typ === "int" ? parseInt(ch[k], 10) : Number(ch[k]);
            if (!Number.isFinite(v)) return sendJson(res, 400, { ok: false, error: `${k} 需为数字` });
            if (v < lo || v > hi) return sendJson(res, 400, { ok: false, error: `${k} 需在 ${lo}~${hi} 内` });
            mockSettings[group][k] = v;
            parts.push(`${k} → ${v}`);
          }
        }
        if (!parts.length) return sendJson(res, 400, { ok: false, error: "没有可应用的修改" });
        const msg = `参数已更新：${parts.join("；")}`;
        pushEvent("system", { msg });
        return sendJson(res, 200, { ok: true, msg, ...mockSettings });
      }

      // ---- SSE ----
      if (p === "/api/events" && m === "GET") {
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        });
        res.write(": connected\n\n");
        const after = Number(req.headers["last-event-id"] ?? u.searchParams.get("after") ?? 0);
        for (const ev of state.events) if (ev.id > after) res.write(`id: ${ev.id}\ndata: ${JSON.stringify(ev)}\n\n`);
        sseClients.add(res);
        const keepalive = setInterval(() => {
          try {
            res.write("event: heartbeat\ndata: {}\n\n");
          } catch {
            clearInterval(keepalive);
          }
        }, 15000);
        req.on("close", () => {
          clearInterval(keepalive);
          sseClients.delete(res);
        });
        return;
      }

      const executionMatch = /^\/api\/executions\/([^/]+)$/.exec(p);
      if (executionMatch) return sendJson(res, executions.has(executionMatch[1]) ? 200 : 404, { ok: executions.has(executionMatch[1]), execution: executions.get(executionMatch[1]) });
      const terminalMatch = /^\/api\/attempts\/([^/]+)\/terminal$/.exec(p);
      if (terminalMatch) {
        res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
        const after = Number(u.searchParams.get("after") ?? 0);
        const clients = terminalClients.get(terminalMatch[1]) ?? new Set();
        clients.add(res); terminalClients.set(terminalMatch[1], clients);
        for (const record of terminalRecords.get(terminalMatch[1]) ?? []) if (record.seq > after) res.write(`id: ${record.seq}\ndata: ${JSON.stringify(record)}\n\n`);
        const timer = setInterval(() => res.write(": keepalive\n\n"), 15000);
        req.on("close", () => { clearInterval(timer); clients.delete(res); });
        return;
      }
      const downloadMatch = /^\/api\/attempts\/([^/]+)\/download$/.exec(p);
      if (downloadMatch) {
        res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8", "Content-Disposition": "attachment; filename=terminal-demo.txt" });
        return res.end((terminalRecords.get(downloadMatch[1]) ?? []).map((r) => r.data ?? "").join(""));
      }

      // ---- 对话 ----
      if (p === "/api/chat" && m === "POST") {
        const { text } = await readBody(req);
        if (!text || typeof text !== "string") return sendJson(res, 400, { ok: false, error: "缺少 text" });
        if (text.startsWith("/")) return sendJson(res, 400, { ok: false, error: "指令请走 /api/command" });
        state.chats.push({ text, at: Date.now() });
        if (state.busy) {
          state.queue.push(text);
          state.queueLen = state.queue.length;
          return sendJson(res, 200, { ok: true, queued: true, position: state.queueLen, busy: true });
        }
        state.busy = true;
        scheduleTask(text);
        return sendJson(res, 200, { ok: true, queued: false, busy: true, message: "任务已启动" });
      }

      // ---- 指令 ----
      if (p === "/api/command" && m === "POST") {
        const { text } = await readBody(req);
        state.commands.push(text);
        const msg =
          text === "/status"
            ? "状态：idle · 种群 3/8 · 队列 0"
            : text === "/help"
              ? "可用指令：/workspace /evolve /queue /status /save /load /sessions /history /help"
              : text.startsWith("/queue")
                ? `队列：${state.queueLen}`
                : text.startsWith("/save")
                  ? "已保存当前会话"
                  : text === "/sessions"
                    ? `会话：${demoSession.name}`
                    : "指令已执行";
        if (text === "/evolve") setTimeout(runEvolveRound, 300);
        return sendJson(res, 200, { ok: true, message: msg });
      }

      // ---- 会话 ----
      if (p === "/api/session/new" && m === "POST") {
        state.currentSess = null;
        return sendJson(res, 200, { ok: true, message: "已新建会话" });
      }

      if (p === "/api/sessions" && m === "GET") {
        return sendJson(res, 200, {
          ok: true,
          sessions: [
            { name: demoSession.name, saved_at: demoSession.saved_at, msg_count: demoSession.history.length, preview: demoSession.history[0].content },
            { name: multiSession.name, saved_at: multiSession.saved_at, msg_count: multiSession.history.length, preview: multiSession.history[0].content },
          ],
        });
      }

      if (p === "/api/sessions/graphs" && m === "GET") {
        return sendJson(res, 200, {
          ok: true,
          sessions: [
            { name: demoSession.name, saved_at: demoSession.saved_at, preview: demoSession.history[0].content, graph: demoSession.graph },
            { name: multiSession.name, saved_at: multiSession.saved_at, preview: multiSession.history[0].content, graph: multiSession.graph },
          ],
        });
      }

      if (p === "/api/session/load" && m === "POST") {
        const { name } = await readBody(req);
        const sess = name === multiSession.name ? multiSession : name === demoSession.name ? demoSession : null;
        if (!sess) return sendJson(res, 404, { ok: false, error: "会话不存在" });
        state.currentSess = name;
        // 2026-09-03：与后端 _session_messages 同构——CEO 话术（ask/report/finish）
        // 按话术 kind 还原，历史恢复能看到 CEO 提问卡片（不再只有工具条目）
        return sendJson(res, 200, {
          ok: true,
          msg: `已载入 ${name}`,
          busy: state.busy,
          running: state.busy,
          messages:
            sess === multiSession
              ? [
                  { kind: "user", text: multiSession.history[0].content },
                  { kind: "text", text: multiSession.history[1].content },
                ]
              : [
                  { kind: "user", text: demoSession.history[0].content },
                  { kind: "ask", text: "翻译需要保留原文术语吗？" },
                  { kind: "tool", name: "split_tasks", args: { count: 1 }, result: "已拆 1 个子任务" },
                  { kind: "tool", name: "single_task", args: { node: "ags_1" }, result: "done" },
                  { kind: "report", text: "翻译完成", reason: "润色" },
                  { kind: "text", text: demoSession.history[1].content },
                ],
          graph: sess.graph,
        });
      }

      if (p === "/api/session/graph" && m === "POST") {
        const { graph, name } = await readBody(req);
        state.savedGraphs.push({ name: name ?? state.currentSess ?? "live", at: Date.now(), graph });
        return sendJson(res, 200, { ok: true });
      }

      // ---- 进化 ----
      if (p === "/api/evolve" && m === "POST") {
        setTimeout(() => {
          // /api/evolve 同步返回 report，同时广播事件
        }, 0);
        const report = runEvolveRound();
        return sendJson(res, 200, { ok: true, report });
      }

      // ---- 节点 ----
      if (p === "/api/nodes" && m === "GET") {
        return sendJson(res, 200, { ok: true, nodes: activeNodes, archived: archivedNodes });
      }

      const nodeMatch = /^\/api\/node\/([^/]+)(\/.*)?$/.exec(p);
      if (nodeMatch) {
        const id = decodeURIComponent(nodeMatch[1]);
        const sub = nodeMatch[2] ?? "/";
        const node = activeNodes.find((n) => n.id === id) ?? archivedNodes.find((n) => n.id === id);
        if (!node) return sendJson(res, 404, { ok: false, error: "节点不存在" });
        if (sub === "/") {
          return sendJson(res, 200, { ok: true, node: { ...node, archived_at: node.archived_at ?? null }, skills: nodeSkills[id] ?? [] });
        }
        if (sub === "/describe" && m === "POST") {
          const { describe } = await readBody(req);
          node.describe = String(describe ?? node.describe);
          return sendJson(res, 200, { ok: true, message: "描述已更新" });
        }
        if (sub === "/revive" && m === "POST") {
          const i = archivedNodes.indexOf(node);
          if (i >= 0) archivedNodes.splice(i, 1);
          activeNodes.push({ ...node, status: "idle", archived_at: null });
          return sendJson(res, 200, { ok: true, message: "已复活" });
        }
        if (sub === "/remove" && m === "POST") {
          if (state.busy) return sendJson(res, 409, { ok: false, error: "任务执行中，无法移除节点" });
          const i = activeNodes.indexOf(node);
          if (i >= 0) activeNodes.splice(i, 1);
          return sendJson(res, 200, { ok: true, message: "已移除" });
        }
        if (sub === "/skill/remove" && m === "POST") {
          const { name } = await readBody(req);
          const arr = nodeSkills[id] ?? [];
          nodeSkills[id] = arr.filter((s) => s.name !== name);
          if (node.skills) node.skills = node.skills.filter((s) => s !== name);
          return sendJson(res, 200, { ok: true, message: `技能 ${name} 已移除` });
        }
        if (sub === "/skill/install" && m === "POST") {
          const { from_node, name } = await readBody(req);
          const src = (nodeSkills[from_node] ?? []).find((s) => s.name === name);
          if (!src) return sendJson(res, 404, { ok: false, error: "源技能不存在" });
          (nodeSkills[id] ??= []).push(src);
          node.skills = [...new Set([...(node.skills ?? []), name])];
          return sendJson(res, 200, { ok: true, message: `技能 ${name} 已移植` });
        }
        if (sub === "/logs" && m === "GET") {
          // 磁盘历史日志回退（2026-09-03）：确定性样本，供抽屉历史标签 E2E
          return sendJson(res, 200, {
            ok: true,
            node: id,
            hermes: {
              lines: [`${id} 磁盘历史行A`, `${id} 磁盘历史行B`],
              total_lines: 2, mtime: 1750000000, size: 100, truncated: false,
            },
            agentlog: {
              lines: [`${id} agent 历史行`],
              total_lines: 1, mtime: 1750000000, size: 50, truncated: false,
            },
          });
        }
        return sendJson(res, 404, { ok: false, error: "未知路由" });
      }

      return sendJson(res, 404, { ok: false, error: `未知 API ${p}` });
    } catch (e) {
      return sendJson(res, 500, { ok: false, error: String(e) });
    }
  }

  // SPA / 静态
  if (m === "GET") return serveStatic(req, res, p);
  res.writeHead(405);
  res.end();
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[mock] ADGS web3 E2E backend on http://127.0.0.1:${PORT} (dist: ${DIST})`);
});
