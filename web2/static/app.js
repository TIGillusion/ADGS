/* ============================================================
   ADGS Web2 重写版（2026-09-02）— 全新组织，无旧版补丁残留
   对接现有后端 API（/api/state /api/chat /api/events /api/session/* …）
   ============================================================ */
"use strict";

/* ================= 1. 工具 ================= */
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

/* ANSI 256 色板 → hex（旧版已验证：style.css 只定义 0-15，≥16 用标准色板） */
function ansi256ToHex(n) {
  n = Math.max(0, Math.min(255, n | 0));
  if (n < 16) return ["#4a4a55","#ff5555","#50fa7b","#f1fa8c","#5b8def","#bd93f9",
                      "#8be9fd","#f8f8f2","#6b7280","#ff6e6e","#69ff94","#ffffa5",
                      "#7aa2ff","#cfa9ff","#a4ffff","#ffffff"][n];
  if (n >= 232) { const g = 8 + (n - 232) * 10; const h = g.toString(16).padStart(2, "0"); return `#${h}${h}${h}`; }
  const i = n - 16;
  const lv = [0, 95, 135, 175, 215, 255];
  const r = lv[Math.floor(i / 36)], g = lv[Math.floor(i / 6) % 6], b = lv[i % 6];
  const hx = (v) => v.toString(16).padStart(2, "0");
  return `#${hx(r)}${hx(g)}${hx(b)}`;
}

/* ================= 2. 全局状态 ================= */
const nodes = new Map();        // key -> 节点
const nodeLogs = new Map();     // key -> 日志条目[]
const lastDispatch = new Map(); // node_id -> 最近实例 key
const taskNameToKey = new Map(); // 旧事件 task 名映射（兜底）
const colors = {};              // CSS 变量缓存

let view = "welcome";          // welcome | main | overview | nodes
let currentSession = null;     // 当前查看/续接的会话名（null=新对话）
let resumeSaving = false;      // 用户刚发消息（续接会话时允许保存）
let graphSaveSuppressed = true;
let layoutMode = "tree";       // tree | radial
let roundSeq = 0, currentRound = null, roundActive = false, currentCeo = null;
let selectedKey = null;
let busy = false;
let isReplaying = false;
let backendRestarted = false;
let overviewBackup = null;     // 总览前的图谱备份
let layoutCycle = null;        // 整理布局三态循环状态
let _graphSaveTimer = null;

const SNAP_KEY = "adgs2_graph_snap";
const VIEW_KEY = "adgs2_view";
const THEME_KEY = "adgs2_theme";
const BOOT_KEY = "adgs2_boot_ts";

/* ================= 3. API ================= */
async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  return r.json().catch(() => ({}));
}

/* ================= 4. 图谱数据 ================= */
function nodeColor(n) {
  const st = colors["st-" + n.status] || colors["st-idle"];
  return st;
}
function typeColor(n) {
  return colors["ty-" + n.type] || "#8b8b9c";
}
function typeName(n) {
  if (n.type === "ceo") return "CEO";
  if (n.type === "round") return "轮次";
  if (n.type === "task") return "序号";
  if (n.type === "clone") return "克隆";
  return "节点";
}
function upsertNode(key, opts) {
  let n = nodes.get(key);
  if (!n) {
    n = { key, id: opts.id, type: opts.type || "node", label: opts.label || key,
          parent: opts.parent || null, children: [],
          status: "idle", slot: 0, depth: 0,
          x: 0, y: 0, tx: 0, ty: 0, weight: 1, born: performance.now(),
          extra: opts.extra || {}, pinned: false };
    nodes.set(key, n);
  } else {
    if (opts.type) n.type = opts.type;
    if (opts.label) n.label = opts.label;
    if (opts.parent !== undefined) { n.parent = opts.parent; }
    if (opts.status) n.status = opts.status;
    if (opts.extra) n.extra = { ...n.extra, ...opts.extra };
    if (opts.id) n.id = opts.id;
  }
  if (n.parent && nodes.has(n.parent)) {
    const p = nodes.get(n.parent);
    if (!p.children.includes(key)) p.children.push(key);
  }
  return n;
}
function snapNode(n) {
  return { key: n.key, id: n.id, type: n.type, label: n.label,
           parent: n.parent, status: n.status, extra: n.extra || {},
           x: n.x, y: n.y, tx: n.tx, ty: n.ty, pinned: n.pinned || false };
}
function ensureCeo() {
  if (!nodes.size) {
    nodes.set("ceo", { key: "ceo", id: "ceo", type: "ceo", label: "CEO 决策中枢",
      parent: null, children: [], status: "idle", slot: 0, depth: 0,
      x: 0, y: 0, tx: 0, ty: 0, weight: 1, born: performance.now(),
      extra: {}, pinned: false });
  }
}
/* 重建图谱拓扑（快照/会话恢复共用） */
function applyGraph(snap, restoreMode) {
  if (!snap || !snap.nodes || !snap.nodes.length) return false;
  nodes.clear();
  if (restoreMode && snap.mode === "radial") layoutMode = "radial";
  for (const s of snap.nodes) {
    nodes.set(s.key, { key: s.key, id: s.id, type: s.type, label: s.label,
      parent: s.parent || null, children: [], status: s.status || "idle",
      slot: 0, depth: 0, x: s.x || 0, y: s.y || 0, tx: s.tx ?? s.x ?? 0,
      ty: s.ty ?? s.y ?? 0, weight: 1, born: performance.now(),
      extra: s.extra || {}, pinned: s.pinned || false });
  }
  for (const n of nodes.values()) {
    if (n.parent && nodes.has(n.parent)) nodes.get(n.parent).children.push(n.key);
  }
  let maxR = 0;
  for (const n of nodes.values()) {
    if (n.type === "round") {
      const m = /^round-(\d+)$/.exec(n.key || "");
      if (m) maxR = Math.max(maxR, parseInt(m[1], 10));
    }
  }
  if (maxR > 0) { roundSeq = maxR; currentRound = `round-${maxR}`; roundActive = false; }
  return true;
}
/* 快照（带会话名，刷新后据此恢复会话） */
function saveSnapshot() {
  if (view === "overview") return;  // 总览浏览模式不污染快照
  if (_graphSaveTimer) return;
  _graphSaveTimer = setTimeout(() => {
    _graphSaveTimer = null;
    try {
      localStorage.setItem(SNAP_KEY, JSON.stringify({
        t: Date.now(), mode: layoutMode,
        session: currentSession || null,
        nodes: [...nodes.values()].map(snapNode),
      }));
    } catch (e) { /* 存储禁用 */ }
  }, 2000);
}
function restoreSnapshot() {
  try {
    const raw = localStorage.getItem(SNAP_KEY);
    if (!raw) return false;
    const snap = JSON.parse(raw);
    if (snap.session) currentSession = snap.session;
    return applyGraph(snap, true);
  } catch (e) { return false; }
}

/* ================= 5. 布局 ================= */
function computeWeights(id) {
  const n = nodes.get(id); if (!n) return 1;
  if (!n.children.length) { n.weight = 1; return 1; }
  let s = 0;
  for (const c of n.children) s += computeWeights(c);
  n.weight = Math.max(1, s);
  return n.weight;
}
let slotCursor = 0;
const PAD = 60, ROW_H = 90;
/* 分配槽位：叶子递增 slot；父节点居中于其子树（返回自身 slot） */
function assign(id, depth) {
  const n = nodes.get(id); if (!n) return null;
  n.depth = depth;
  if (!n.children.length) { n.slot = slotCursor++; return n.slot; }
  let first = Infinity, last = -Infinity;
  for (const c of n.children) {
    const r = assign(c, depth + 1);
    if (r != null) { if (r < first) first = r; if (r > last) last = r; }
  }
  n.slot = (first + last) / 2;
  return n.slot;
}
/* 树状向下（round 根水平均布，子节点逐层向下，父居中于子树） */
function treeLayout() {
  if (!nodes.size) return;
  const cv = $("graph");
  if (!cv.width || !cv.clientWidth) return;  // canvas 不可见时不布局
  const roots = [];
  for (const n of nodes.values()) {
    if (!n.parent || !nodes.has(n.parent)) roots.push(n.key);
  }
  if (!roots.length) {
    if (nodes.has("ceo")) roots.push("ceo");
    else return;
  }
  roots.sort();
  for (const r of roots) computeWeights(r);
  slotCursor = 0;
  for (const r of roots) assign(r, 0);
  const total = Math.max(1, slotCursor);
  for (const n of nodes.values()) {
    if (n.pinned) continue;
    // 2026-09-02：不再对 depth=0 特判 0.5（多根时全部叠在中心）；
    // 统一按子树槽位分布——根节点 x 居中于自己的子树，多根自然水平散开
    n.tx = (n.slot + 0.5) / total * cv.width;
    n.ty = PAD + n.depth * ROW_H;
  }
}
/* 圆形放射（CEO/根为中心，子节点绕父排圆） */
function subtreeR(id, kidsOf, memo) {
  if (memo.has(id)) return memo.get(id);
  const n = nodes.get(id); if (!n) return 1;
  const kids = kidsOf(id);
  let r = 24;
  for (const k of kids) r += subtreeR(k, kidsOf, memo);
  memo.set(id, r);
  return Math.max(24, r);
}
function radialLayout() {
  if (!nodes.size) return;
  const cv = $("graph");
  if (!cv.width || !cv.clientWidth) return;  // canvas 不可见时不布局
  for (const n of nodes.values()) n.pinned = false;
  const orphans = [...nodes.values()]
    .filter(n => n.key !== "ceo" && (!n.parent || !nodes.has(n.parent)));
  const rootKey = nodes.has("ceo") ? "ceo" : (orphans.length ? orphans[0].key : null);
  if (!rootKey) return;
  const virtKids = [];
  for (const o of orphans) if (o.key !== rootKey) virtKids.push(o.key);
  const rn = nodes.get(rootKey);
  if (rn && rn.children) for (const c of rn.children) {
    if (c !== rootKey && !virtKids.includes(c)) virtKids.push(c);
  }
  const kidsOf = (id) => (id === rootKey ? virtKids : nodes.get(id).children);
  const memo = new Map();
  placeRadial(rootKey, cv.width / 2, cv.height / 2, kidsOf, memo);
}
function placeRadial(id, cx0, cy0, kidsOf, memo) {
  const n = nodes.get(id); if (!n) return;
  n.tx = cx0; n.ty = cy0;
  const kids = kidsOf(id);
  const k = kids.length;
  if (!k) return;
  const rs = kids.map(kk => subtreeR(kk, kidsOf, memo));
  const ring = Math.max(Math.max(...rs) + 30, 40);
  const th = [];
  for (let i = 0; i < k; i++) {
    const x = (rs[i] + rs[(i + 1) % k] + 10) / (2 * ring);
    th.push(2 * Math.asin(Math.min(1, x)));
  }
  const arc = [];
  let total = 0;
  for (let i = 0; i < k; i++) { arc.push(Math.max(th[(i - 1 + k) % k], th[i])); total += arc[i]; }
  const scale = Math.PI * 2 * 0.98 / Math.max(1e-9, total);
  let a = -Math.PI / 2;
  for (let i = 0; i < k; i++) {
    const ca = a + arc[i] * scale / 2;
    placeRadial(kids[i], cx0 + Math.cos(ca) * ring, cy0 + Math.sin(ca) * ring, kidsOf, memo);
    a += arc[i] * scale;
  }
}
function fitView() {
  if (!nodes.size) return;
  const cv = $("graph");
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes.values()) {
    if (n.tx < minX) minX = n.tx; if (n.tx > maxX) maxX = n.tx;
    if (n.ty < minY) minY = n.ty; if (n.ty > maxY) maxY = n.ty;
  }
  const m = 60;
  const bw = Math.max(1, maxX - minX + m * 2);
  const bh = Math.max(1, maxY - minY + m * 2);
  cam.zoom = Math.min(1.4, Math.max(0.01, Math.min(cv.width / bw, cv.height / bh)));
  cam.px = cv.width / 2 - (minX + maxX) / 2 * cam.zoom;
  cam.py = cv.height / 2 - (minY + maxY) / 2 * cam.zoom;
}
function relayout() {
  if (layoutMode === "radial") radialLayout();
  else treeLayout();
}
/* 整理布局三态循环：树状 → 圆形 → 恢复原版 → 循环 */
function cycleLayout() {
  if (view === "overview" || view === "nodes") { addSys("浏览模式下不可整理布局"); return; }
  if (!layoutCycle) {
    layoutCycle = {
      saved: [...nodes.values()].map(n => ({ key: n.key, x: n.x, y: n.y })),
      mode: layoutMode,
      cam: { px: cam.px, py: cam.py, zoom: cam.zoom },
      step: 0,
    };
  }
  layoutCycle.step = (layoutCycle.step + 1) % 3;
  if (layoutCycle.step === 1) {
    layoutMode = "tree"; relayout(); fitView();
    addSys("整理：树状向下分支");
    saveSnapshot();
  } else if (layoutCycle.step === 2) {
    layoutMode = "radial"; relayout(); fitView();
    addSys("整理：CEO 为中心圆形放射");
    saveSnapshot();
  } else {
    for (const n of nodes.values()) {
      const s = layoutCycle.saved.find(v => v.key === n.key);
      if (s) { n.x = s.x; n.y = s.y; n.tx = s.x; n.ty = s.y; }
    }
    layoutMode = layoutCycle.mode;
    cam.px = layoutCycle.cam.px; cam.py = layoutCycle.cam.py;
    cam.zoom = layoutCycle.cam.zoom;
    addSys("已恢复整理前的布局");
    saveSnapshot();
  }
}

/* ================= 6. Canvas ================= */
const cam = { px: 0, py: 0, zoom: 1 };
const NODE_W = { ceo: 150, round: 110, task: 46, node: 96, clone: 96 };
let cv, ctx;
function initCanvas() {
  cv = $("graph");
  ctx = cv.getContext("2d");
  resizeCanvas();
  // 从 CSS 读颜色
  const cs = getComputedStyle(document.body);
  for (const k of ["st-idle","st-running","st-waiting","st-done","st-failed",
                   "st-delegating","ty-ceo","ty-round","ty-task","ty-node","ty-clone"]) {
    colors[k] = cs.getPropertyValue("--" + k).trim() || "#888";
  }
}
function resizeCanvas() {
  if (!cv) return;
  cv.width = cv.clientWidth * devicePixelRatio;
  cv.height = cv.clientHeight * devicePixelRatio;
  ctx = cv.getContext("2d");
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
}
const sx = (x) => x * cam.zoom + cam.px;
const sy = (y) => y * cam.zoom + cam.py;
function draw() {
  if (!ctx || !cv) return;
  const W = cv.clientWidth, H = cv.clientHeight;
  ctx.clearRect(0, 0, W, H);
  // 网格
  ctx.strokeStyle = "rgba(120,120,140,.06)";
  ctx.lineWidth = 1;
  const gs = 40 * cam.zoom;
  if (gs > 8) {
    for (let gx = sx(0) % gs; gx < W; gx += gs) {
      ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, H); ctx.stroke();
    }
    for (let gy = sy(0) % gs; gy < H; gy += gs) {
      ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(W, gy); ctx.stroke();
    }
  }
  // 动画插值
  for (const n of nodes.values()) {
    n.x += (n.tx - n.x) * 0.16;
    n.y += (n.ty - n.y) * 0.16;
  }
  // 边
  for (const n of nodes.values()) {
    if (!n.parent || !nodes.has(n.parent)) continue;
    const p = nodes.get(n.parent);
    ctx.strokeStyle = "rgba(130,130,150,.35)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(sx(p.x), sy(p.y));
    const mx = (p.x + n.x) / 2;
    ctx.bezierCurveTo(mx, p.y, mx, n.y, n.x, n.y);
    ctx.stroke();
  }
  // 节点（按 depth 排序避免遮挡）
  const arr = [...nodes.values()].sort((a, b) => (a.depth || 0) - (b.depth || 0));
  for (const n of arr) drawNode(n);
}
function drawNode(n) {
  const w = (NODE_W[n.type] || 90) / 2;
  const x = sx(n.x), y = sy(n.y);
  const sc = Math.max(0.2, Math.min(1.6, cam.zoom));
  const rw = w * sc, rh = (n.type === "task" ? w : w * 0.62) * sc;
  const col = n.type === "ceo" ? colors["ty-ceo"] : nodeColor(n);
  const fill = n.type === "ceo" || n.type === "round" ? "rgba(18,18,26,.9)"
             : "rgba(12,12,18,.92)";
  // 选中高亮
  if (n.key === selectedKey) {
    ctx.beginPath();
    ctx.arc(x, y, Math.max(rw, rh) + 8 * sc, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(0,212,170,.18)";
    ctx.fill();
  }
  ctx.save();
  ctx.translate(x, y);
  // 呼吸环
  if (n.status === "running") {
    const t = performance.now() / 600;
    ctx.beginPath();
    ctx.arc(0, 0, Math.max(rw, rh) + 6 + Math.sin(t * Math.PI * 2) * 4, 0, Math.PI * 2);
    ctx.strokeStyle = col;
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  ctx.strokeStyle = col;
  ctx.lineWidth = n.key === selectedKey ? 2.5 : 1.5;
  ctx.fillStyle = fill;
  if (n.type === "round") {
    roundRect(ctx, -rw, -rh, rw * 2, rh * 2, 10 * sc);
  } else if (n.type === "task") {
    ctx.beginPath(); ctx.arc(0, 0, rw, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  } else {
    roundRect(ctx, -rw, -rh, rw * 2, rh * 2, 8 * sc);
  }
  // 标签
  const show = sc > 0.45;
  ctx.fillStyle = n.type === "task" ? "#0a0a0f" : typeColor(n);
  ctx.font = `${(n.type === "task" ? 10 : 11) * sc}px var(--font)`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const label = n.type === "task" ? n.label : (n.type === "ceo" ? "CEO" : n.label);
  ctx.fillText(String(label || "").slice(0, 12), 0, -rh - 8 * sc);
  // 状态点
  if (n.status && n.status !== "idle") {
    ctx.beginPath();
    ctx.arc(rw + 6 * sc, -rh - 4 * sc, 4 * sc, 0, Math.PI * 2);
    ctx.fillStyle = nodeColor(n);
    ctx.fill();
  }
  ctx.restore();
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}
function hitTest(px, py) {
  const arr = [...nodes.values()].sort((a, b) => (a.depth || 0) - (b.depth || 0));
  for (let i = arr.length - 1; i >= 0; i--) {
    const n = arr[i];
    const w = (NODE_W[n.type] || 90) / 2;
    const x = sx(n.x), y = sy(n.y);
    if (Math.abs(px - x) < w && Math.abs(py - y) < w) return n;
  }
  return null;
}
let drag = null;  // {mode:'node'|'pan', key?, moved}
function setupCanvasInteractions() {
  cv.addEventListener("mousedown", (e) => {
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const n = hitTest(mx, my);
    if (n) {
      drag = { mode: "node", key: n.key, moved: false };
      n.pinned = true;
    } else {
      drag = { mode: "pan", moved: false, x0: e.clientX, y0: e.clientY,
               px0: cam.px, py0: cam.py };
    }
  });
  window.addEventListener("mousemove", (e) => {
    if (!drag) return;
    const rect = cv.getBoundingClientRect();
    if (drag.mode === "pan") {
      cam.px = drag.px0 + (e.clientX - drag.x0);
      cam.py = drag.py0 + (e.clientY - drag.y0);
      drag.moved = true;
    } else {
      const n = nodes.get(drag.key);
      if (n) {
        n.x = n.tx = (e.clientX - rect.left - cam.px) / cam.zoom;
        n.y = n.ty = (e.clientY - rect.top - cam.py) / cam.zoom;
        drag.moved = true;
      }
    }
  });
  window.addEventListener("mouseup", (e) => {
    if (!drag) return;
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    if (drag.mode === "node" && !drag.moved) {
      selectNode(drag.key);
    } else if (drag.mode === "pan" && !drag.moved) {
      selectNode(null);
    }
    if (drag.mode === "node" && drag.moved) saveSnapshot();
    drag = null;
  });
  cv.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const nz = Math.max(0.02, Math.min(4, cam.zoom * f));
    cam.px = mx - (mx - cam.px) * (nz / cam.zoom);
    cam.py = my - (my - cam.py) * (nz / cam.zoom);
    cam.zoom = nz;
  }, { passive: false });
}

/* ================= 7. 保存守卫（统一派生） ================= */
/* 语义：
   - 总览/欢迎页 = 浏览（合并/无主图谱），禁止保存
   - 查看历史会话（currentSession 非空且未续接发消息）= 禁止保存（防污染）
   - 新对话（currentSession=null）/ 用户刚发消息续接 = 允许保存
   resumeSaving 在 sendText 置 true；chat.user 事件后复位 */
function updateSaveGuard() {
  if (view === "overview" || view === "welcome" || view === "nodes") {
    graphSaveSuppressed = true;
  } else if (currentSession && !resumeSaving) {
    graphSaveSuppressed = true;
  } else {
    graphSaveSuppressed = false;
  }
}
function scheduleGraphSave() {
  if (graphSaveSuppressed || view === "overview") return;
  if (_graphSaveTimer) return;
  _graphSaveTimer = setTimeout(() => {
    _graphSaveTimer = null;
    if (!currentSession || graphSaveSuppressed) return;
    api("/api/session/graph", { method: "POST",
      body: JSON.stringify({ graph: [...nodes.values()].map(snapNode) }) });
  }, 2000);
}

/* ================= 8. 事件处理（SSE） ================= */
/* 会话隔离：事件带 session（服务端当前任务会话）；查看历史会话
   （currentSession 非空）且不一致 → 忽略（system 保留提示） */
function sessionFiltered(ev) {
  return !!(ev.session && currentSession && ev.session !== currentSession
            && ev.type !== "system");
}
function handleEvent(ev) {
  const d = ev.data || {};
  if (sessionFiltered(ev)) {
    if (ev.type === "system") addSys(d.msg || "");
    return;
  }
  switch (ev.type) {
    case "chat.user": onChatUser(d); break;
    case "chat.ceo_ask":
      setBusy(false); roundActive = false;
      recordCeoFirst("❓ " + d.question);
      addChat("ceo", { text: "❓ CEO 提问：" + d.question });
      pushLog("ceo", "[提问] " + String(d.question).slice(0, 200));
      break;
    case "chat.ceo_tool":
      setBusy(true);
      addChat("ceo", { kind: "tool", name: d.name, args: d.args, result: d.result });
      pushLog("ceo", `[工具] ${d.name} ${JSON.stringify(d.args || {}).slice(0, 150)}`);
      break;
    case "chat.ceo_report":
      setBusy(true);
      recordCeoFirst("📊 " + d.report);
      addChat("ceo", { text: "📊 " + (d.report || "") });
      pushLog("ceo", "[汇报] " + String(d.report || "").slice(0, 200));
      break;
    case "chat.ceo_finish":
      setBusy(false); roundActive = false;
      recordCeoFirst("🏁 " + (d.summary || ""));
      addChat("ceo", { text: "🏁 " + (d.summary || "") });
      pushLog("ceo", "[完成] " + String(d.summary || "").slice(0, 300));
      scheduleGraphSave();
      break;
    case "chat.ceo_end":
      setBusy(false); roundActive = false;
      addSys(d.status === "exception" ? "CEO 出错结束：" + (d.error || "")
             : (d.status === "error" ? "CEO 决策结束" : "本轮结束"));
      scheduleGraphSave();
      break;
    case "graph.task": onGraphTask(d); break;
    case "graph.dispatch": onGraphDispatch(d); break;
    case "node.status": onNodeStatus(d); break;
    case "node.brain":
      pushLogNode(d.node, `[brain] ${d.action || ""} ${String(d.detail || "").slice(0, 120)}`);
      break;
    case "node.eval":
      pushLogNode(d.node, `[验收] ${d.passed ? "通过" : "未过"} score=${d.score}`);
      break;
    case "hermes.start":
      pushLogNode(d.node, `[hermes 启动] ${d.profile || d.node}`);
      break;
    case "hermes.line": onHermesLine(d); break;
    case "hermes.end":
      pushLogNode(d.node, `[hermes 结束] exit=${d.exit_code}`);
      break;
    case "evolve.round":
      addSys(`进化：繁衍 ${d.births?.length ?? 0} 淘汰 ${d.eliminations?.length ?? 0}，` +
             `种群 ${d.n_before}→${d.n_after}（压力 ${d.pressure}）`);
      refreshStats();
      break;
    case "system":
      addSys(d.msg || "");
      break;
  }
  if (["chat.user","graph.task","graph.dispatch","node.status","chat.ceo_ask",
       "chat.ceo_finish","chat.ceo_end"].includes(ev.type)) {
    scheduleGraphSave();
  }
}
function recordCeoFirst(text) {
  if (!currentRound || !nodes.has(currentRound)) return;
  const rn = nodes.get(currentRound);
  if (rn.extra && !rn.extra.ceo_first) {
    rn.extra = { ...rn.extra, ceo_first: String(text || "").slice(0, 1500) };
  }
}
function onChatUser(d) {
  // 总览是浏览模式：新用户消息到达 → 先退出总览回到原图谱
  if (view === "overview") restoreFromOverview();
  const txt = String(d.text || "").slice(0, 2000);
  addChat("user", txt);
  // 用户发消息 = 恢复保存（续接会话可落盘）
  resumeSaving = false;
  updateSaveGuard();
  // 轮次 +1
  ensureCeo();
  const ceoN = nodes.get("ceo");
  if (ceoN) { ceoN.extra = { ...ceoN.extra, task_title: txt.slice(0, 300) }; }
  roundActive = true;
  roundSeq++;
  currentRound = `round-${roundSeq}`;
  upsertNode(currentRound, { type: "round", id: currentRound, label: `轮 ${roundSeq}`,
    parent: "ceo", extra: { user_text: txt, ceo_first: "" } });
  layoutMode = "tree";
  relayout();
}
function onGraphTask(d) {
  if (view === "overview") return;
  const isCeoTask = !d.parent || d.parent === "ceo";
  if (isCeoTask && !roundActive && !currentRound) {
    roundActive = true; roundSeq++;
    currentRound = `round-${roundSeq}`;
    ensureCeo();
    upsertNode(currentRound, { type: "round", id: currentRound, label: `轮 ${roundSeq}`,
      parent: "ceo", extra: { user_text: "", ceo_first: "" } });
  }
  const seq = d.seq || String(d.id || "").replace(/^t/, "") || "?";
  const parentKey = isCeoTask ? (currentRound || "ceo")
    : (d.parent && nodes.has(d.parent)) ? d.parent
    : (d.parent ? (lastDispatch.get(String(d.parent).split("@")[0]) || d.parent)
                : (currentRound || "ceo"));
  const key = d.id || `t${seq}`;
  upsertNode(key, { id: key, type: "task", label: String(seq),
    parent: parentKey, extra: { goal: d.goal || "", overall: d.overall || "",
      count: d.count } });
  if (!nodes.has(parentKey)) return;
  if (d.count && d.count > 1 && d.overall) {
    upsertNode(parentKey + "", {});
    const t = nodes.get(parentKey);
    if (t && t.type === "task") t.extra = { ...t.extra, is_multi: true };
  }
  relayoutIfNeeded();
}
function onGraphDispatch(d) {
  if (view === "overview") return;
  const pid = d.node;
  const instKey = `${pid}@${d.task || "task"}`;
  const isClone = d.mode === "clone";
  let parentKey;
  if (d.task && nodes.has(d.task)) parentKey = d.task;
  else if (taskNameToKey.has(d.task)) parentKey = taskNameToKey.get(d.task);
  else parentKey = (d.parent && nodes.has(d.parent)) ? d.parent : (currentRound || "ceo");
  taskNameToKey.set(d.task, parentKey);
  lastDispatch.set(pid, instKey);
  upsertNode(instKey, { id: instKey, type: isClone ? "clone" : "node",
    label: pid, parent: parentKey,
    extra: { goal: d.goal || "", task: d.task || "" } });
  relayoutIfNeeded();
}
function onNodeStatus(d) {
  const key = (d.task && nodes.has(`${d.node}@${d.task}`))
    ? `${d.node}@${d.task}` : (lastDispatch.get(d.node) || d.node);
  const n = nodes.get(key);
  if (n) {
    const st = d.status === "running" ? "running"
      : d.status === "done" ? "done" : d.status === "waiting" ? "waiting"
      : d.status === "delegating" ? "delegating" : "failed";
    n.status = st;
    pushLogNode(d.node, `[状态] ${st}`);
    if (selectedKey === key || (n.id && selectedKey === n.id)) renderDetail();
  }
}
function onHermesLine(d) {
  if (!d.node) return;
  const logKey = `${d.node}@${d.task || "task"}`;
  const entry = { t: evTs(), txt: String(d.line || "").slice(0, 800) };
  const logs = nodeLogs.get(logKey) || [];
  logs.push(entry);
  if (logs.length > 300) logs.shift();
  nodeLogs.set(logKey, logs);
  const n = nodes.get(logKey) || nodes.get(lastDispatch.get(d.node));
  if (n && selectedKey === n.key) renderHermes(n);
}
let _evTs = 0;
function evTs() { return ++_evTs; }
function pushLogNode(nodeId, txt) {
  // 日志归属到该节点的实例 key（与 onHermesLine 一致）
  const key = lastDispatch.get(nodeId) || nodeId;
  const logs = nodeLogs.get(key) || [];
  logs.push(`[${fmtTime(Date.now() / 1000)}] ${txt}`);
  if (logs.length > 200) logs.shift();
  nodeLogs.set(key, logs);
  if (selectedKey === key || selectedKey === nodeId) {
    const n = nodes.get(key) || nodes.get(nodeId);
    if (n) renderHermes(n);
  }
}
function relayoutIfNeeded() {
  if (view !== "main") return;
  // 新结构到达：若当前没在手动拖拽布局就排一下
  if (!drag) { relayout(); }
}
/* 日志：renderHermes/renderDetail 在视图部分 */

/* 断线重连校准：增量补 node.status / 缺失结构 / 丢失的 CEO 消息 */
async function resyncAfterReconnect() {
  const prev = graphSaveSuppressed;
  graphSaveSuppressed = true;
  try {
    const st = await api("/api/state");
    if (!st.ok) return;
    updateStats(st);
    setBusy(st.busy);
    let patched = 0;
    const logText = () => $("chat-log").textContent || "";
    for (const ev of st.events || []) {
      if (ev.session && currentSession && ev.session !== currentSession) continue;
      const d = ev.data || {};
      if (ev.type === "node.status") {
        const key = (d.task && nodes.has(`${d.node}@${d.task}`))
          ? `${d.node}@${d.task}` : (lastDispatch.get(d.node) || d.node);
        const n = nodes.get(key);
        if (n) {
          n.status = d.status === "running" ? "running"
            : d.status === "done" ? "done" : d.status === "waiting" ? "waiting"
            : d.status === "delegating" ? "delegating" : "failed";
          patched++;
        }
      } else if (ev.type === "graph.dispatch" && d.node) {
        const ik = `${d.node}@${d.task || "task"}`;
        if (!nodes.has(ik)) { handleEvent(ev); patched++; }
      } else if (ev.type === "graph.task" && d.id && !nodes.has(d.id)) {
        handleEvent(ev); patched++;
      } else if ((ev.type === "chat.ceo_finish" || ev.type === "chat.ceo_ask")
                 && d && !logText().includes(String(d.summary || d.question || "").slice(0, 30))) {
        handleEvent(ev); patched++;
      }
    }
    if (patched) addSys(`连接恢复，已校准 ${patched} 处`);
    else addSys("连接已恢复");
  } catch (e) { /* 静默 */ }
  finally { graphSaveSuppressed = prev; }
}

/* ================= 9. 对话渲染 ================= */
function addChat(who, payload) {
  const log = $("chat-log");
  const m = el("div", `msg msg-${who}`);
  if (who !== "sys") {
    const whoEl = el("div", "who", who === "user" ? "你" : "CEO");
    m.appendChild(whoEl);
  }
  const bub = el("div", "bubble");
  if (typeof payload === "string") {
    bub.textContent = payload;
  } else if (payload.kind === "tool") {
    bub.appendChild(renderToolCall(payload));
  } else {
    bub.textContent = payload.text || "";
  }
  m.appendChild(bub);
  log.appendChild(m);
  log.scrollTop = log.scrollHeight;
  return m;
}
function addSys(msg) {
  const log = $("chat-log");
  if (!msg) return;
  const m = el("div", "msg msg-sys");
  m.appendChild(el("div", "bubble", `[${fmtTime(Date.now() / 1000)}] ${msg}`));
  log.appendChild(m);
  log.scrollTop = log.scrollHeight;
}
function renderToolCall(p) {
  const wrap = el("div");
  wrap.appendChild(el("div", null, `⚡ ${p.name || ""}(${Object.keys(p.args || {}).length} 参)`));
  if (p.result != null) {
    const res = el("div", null);
    res.textContent = String(p.result).slice(0, 600);
    wrap.appendChild(res);
  }
  return wrap;
}
function pushLog(who, txt) {
  if (who === "ceo") {
    const logs = nodeLogs.get("__ceo__") || [];
    logs.push(`[${fmtTime(Date.now() / 1000)}] ${txt}`);
    if (logs.length > 100) logs.shift();
    nodeLogs.set("__ceo__", logs);
  }
}
function renderSessionMessages(messages) {
  const log = $("chat-log");
  log.innerHTML = "";
  for (const m of messages || []) {
    if (m.kind === "user") addChat("user", String(m.text || "").slice(0, 2000));
    else if (m.kind === "tool") addChat("ceo", { kind: "tool", name: m.name, args: m.args, result: m.result });
    // 2026-09-03：CEO 话术 kind 与实时流同格式（❓提问 / 📊汇报 / 🏁结论）
    else if (m.kind === "ask") addChat("ceo", { text: "❓ CEO 提问：" + (m.text || "") });
    else if (m.kind === "report") addChat("ceo", { text: "📊 " + (m.text || "") + (m.reason ? "\n▶ 下一步：" + m.reason : "") });
    else if (m.kind === "finish") addChat("ceo", { text: "🏁 " + (m.text || "") });
    else addChat("ceo", String(m.text || "").slice(0, 2000));
  }
}

/* ================= 10. 视图与状态 ================= */
function showWelcome() {
  view = "welcome";
  try { localStorage.removeItem(VIEW_KEY); } catch (e) {}
  $("welcome-wrap").classList.remove("hidden");
  $("main-wrap").classList.add("hidden");
  $("nodes-wrap").classList.add("hidden");
  selectNode(null);
  updateSaveGuard();
  loadSessions();
  refreshStats();
}
function showMain() {
  view = "main";
  try { localStorage.setItem(VIEW_KEY, "main"); } catch (e) {}
  $("welcome-wrap").classList.add("hidden");
  $("nodes-wrap").classList.add("hidden");
  $("main-wrap").classList.remove("hidden");
  updateSaveGuard();
  resizeCanvas();
}
async function newSession() {
  const j = await api("/api/session/new", { method: "POST", body: "{}" });
  if (!j.ok) { addSys("新建对话失败：" + (j.error || "")); return; }
  restoreFromOverview();
  currentSession = null;
  resumeSaving = false;
  $("graph-title").textContent = "新任务图谱";
  $("chat-log").innerHTML = "";
  nodes.clear(); nodeLogs.clear(); lastDispatch.clear(); taskNameToKey.clear();
  roundSeq = 0; currentRound = null; roundActive = false; layoutMode = "tree";
  showMain();  // 先显示再布局（canvas 尺寸）
  ensureCeo();
  relayout();
  for (const it of $("hist-list").querySelectorAll(".hist-item")) {
    it.classList.remove("active");
  }
  addSys("已新建对话（CEO 上下文已重置，历史保留在左侧列表）");
}
async function loadSession(name, item) {
  const j = await api("/api/session/load", { method: "POST",
    body: JSON.stringify({ name }) });
  if (!j.ok) { addSys("切换会话失败：" + (j.error || "")); return; }
  currentSession = name;
  resumeSaving = false;
  $("graph-title").textContent = name;
  for (const it of $("hist-list").querySelectorAll(".hist-item")) {
    it.classList.toggle("active", it.dataset.name === name);
  }
  nodes.clear(); nodeLogs.clear(); lastDispatch.clear(); taskNameToKey.clear();
  roundSeq = 0; currentRound = null; roundActive = false; layoutMode = "tree";
  const demoted = !j.running
    ? (j.graph || []).filter(n => n.status === "running").length : 0;
  renderSessionMessages(j.messages || []);
  showMain();  // 先显示主视图（canvas 有尺寸）再布局——hidden 时 clientWidth=0，
  // relayout 会把所有节点算到 x=0 重叠（2026-09-02 图谱塌缩 bug 根因）
  if (j.graph && j.graph.length) {
    applyGraph({ nodes: j.graph }, false);
    if (!j.running) {
      for (const n of nodes.values()) if (n.status === "running") n.status = "waiting";
    }
    relayout();
    addSys(`已恢复会话 ${name} 的图谱（${j.graph.length} 节点）`);
  } else {
    ensureCeo();
    relayout();
    addSys(`会话 ${name} 暂无图谱（显示 CEO 占位）`);
  }
  updateSaveGuard();
  await syncSessionState(name);
}
/* 从后端事件流同步该会话节点状态/缺失结构（与旧版一致，防止刷新后状态滞后） */
async function syncSessionState(name) {
  const prev = graphSaveSuppressed;
  graphSaveSuppressed = true;
  try {
    const st = await api("/api/state");
    if (!st.ok) return;
    let patched = 0;
    for (const ev of st.events || []) {
      if (ev.session !== name) continue;
      const d = ev.data || {};
      if (ev.type === "node.status") {
        const key = (d.task && nodes.has(`${d.node}@${d.task}`))
          ? `${d.node}@${d.task}` : (lastDispatch.get(d.node) || d.node);
        const n = nodes.get(key);
        if (n) {
          n.status = d.status === "running" ? "running"
            : d.status === "done" ? "done" : d.status === "waiting" ? "waiting"
            : d.status === "delegating" ? "delegating" : "failed";
          patched++;
        }
      } else if (ev.type === "graph.dispatch" && d.node) {
        const ik = `${d.node}@${d.task || "task"}`;
        if (!nodes.has(ik)) { handleEvent(ev); patched++; }
      } else if (ev.type === "graph.task" && d.id && !nodes.has(d.id)) {
        handleEvent(ev); patched++;
      }
    }
    if (patched) addSys(`已同步后端状态（${patched} 处）`);
  } catch (e) { /* 静默 */ }
  finally { graphSaveSuppressed = prev; }
}

/* ================= 11. 详情面板 ================= */
function selectNode(key) {
  selectedKey = key;
  const detail = $("detail");
  if (!key) {
    detail.classList.add("hidden");
    return;
  }
  detail.classList.remove("hidden");
  renderDetail();
}
function renderDetail() {
  const n = nodes.get(selectedKey);
  if (!n) return;
  $("detail-title").textContent = `${typeName(n)} · ${n.key}`;
  const goal = $("detail-task-goal");
  goal.textContent = "";
  if (n.type === "round") {
    goal.textContent = `【第 ${n.key.replace("round-", "")} 轮 · 用户输入】\n`
      + (n.extra?.user_text || "(无)") + "\n\n【CEO 首次回复】\n"
      + (n.extra?.ceo_first || "（尚未回复）");
  } else if (n.extra?.goal) {
    goal.textContent = `【任务目标】\n${n.extra.goal}`;
  } else if (n.type === "ceo") {
    goal.textContent = `【当前任务标题】\n${n.extra?.task_title || "(暂无)"}`;
  } else {
    goal.textContent = "(无附加信息)";
  }
  const meta = $("detail-meta");
  meta.textContent = `key: ${n.key}\nid: ${n.id || "-"}\n类型: ${typeName(n)}\n` +
    `状态: ${n.status}\n父: ${n.parent || "-"}\nlabel: ${n.label || "-"}`;
  renderHermes(n);
}
function renderHermes(n) {
  const block = $("dl-hermes-block");
  const logKey = n.id && n.key.includes("@") ? n.key
    : (n.key.includes("@") ? n.key : `${n.key}`);
  const logs = nodeLogs.get(logKey) || nodeLogs.get("__live__") || [];
  const dom = $("detail-hermes");
  dom.innerHTML = "";
  if (!logs.length) { block.classList.add("hidden"); return; }
  block.classList.remove("hidden");
  for (const e of logs.slice(-80)) {
    const line = el("div");
    line.textContent = typeof e === "string" ? e : e.txt || e.text || "";
    dom.appendChild(line);
  }
  dom.scrollTop = dom.scrollHeight;
}

/* ================= 12. 历史列表 / 统计 ================= */
async function loadSessions() {
  try {
    const j = await api("/api/sessions");
    const list = $("hist-list");
    list.innerHTML = "";
    for (const s of j.sessions || []) {
      const it = el("div", "hist-item");
      it.dataset.name = s.name;
      if (s.name === currentSession) it.classList.add("active");
      it.appendChild(el("div", "hi-name", s.name));
      it.appendChild(el("div", "hi-preview", (s.preview || "").slice(0, 60)));
      it.addEventListener("click", () => loadSession(s.name, it));
      list.appendChild(it);
    }
  } catch (e) { /* 静默 */ }
}
async function refreshStats() {
  try {
    const j = await api("/api/state");
    if (j.ok) updateStats(j);
  } catch (e) { /* 静默 */ }
}
function updateStats(st) {
  const pop = st.population || {};
  const models = st.models || {};
  $("wl-stats").textContent =
    `种群 ${pop.active ?? "-"} · CEO ${models.ceo || "-"} · 节点 ${models.brain || "-"} · 执行 ${models.worker || "-"} · 队列 ${st.queue_len ?? 0}`;
}
let busyState = false;
function setBusy(b) {
  busyState = b;
  $("graph-status").className = b ? "busy" : "idle";
  $("graph-status").textContent = b ? "● 任务执行中" : "空闲";
}

/* ================= 13. 任务总览 ================= */
function restoreFromOverview() {
  if (!overviewBackup || view !== "overview") return;
  nodes.clear(); nodeLogs.clear(); lastDispatch.clear(); taskNameToKey.clear();
  applyGraph({ nodes: overviewBackup }, false);
  overviewBackup = null;
  view = "main";
  $("main-wrap").classList.remove("hidden");
  relayout();
  updateSaveGuard();
  addSys("已退出任务总览");
}
/* 总览：每个历史会话的图谱缩放到自己的"圆"里，网格排布互不重叠。
   浏览模式：全程抑制保存；点击圆内节点进入对应会话。 */
async function taskOverview() {
  if (overviewBackup == null) {
    overviewBackup = [...nodes.values()].map(snapNode);
  }
  const j = await api("/api/sessions/graphs");
  if (!j.ok) { overviewBackup = null; addSys("总览加载失败"); return; }
  const groups = [];
  for (const s of j.sessions || []) {
    const g = s.graph || [];
    if (g.length) groups.push({ name: s.name, preview: s.preview || s.name, graph: g });
  }
  if (!groups.length) { overviewBackup = null; addSys("没有可总览的历史图谱"); return; }
  view = "overview";
  updateSaveGuard();
  $("chat-log").innerHTML = "";   // 浏览模式不混入对话
  nodes.clear(); nodeLogs.clear(); lastDispatch.clear(); taskNameToKey.clear();
  const cv = $("graph");
  const cols = Math.ceil(Math.sqrt(groups.length));
  const rows = Math.ceil(groups.length / cols);
  const cellW = cv.width / cols, cellH = cv.height / rows;
  let gi = 0;
  for (const gr of groups) {
    const cx = (gi % cols) * cellW + cellW / 2;
    const cy = Math.floor(gi / cols) * cellH + cellH / 2;
    gi++;
    const g = gr.graph;
    // 用已有坐标算包围盒（图谱保存过布局）
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    let hasPos = false;
    for (const n of g) {
      if (n.x != null && n.y != null && (n.x || n.y)) {
        hasPos = true;
        if (n.x < minX) minX = n.x; if (n.x > maxX) maxX = n.x;
        if (n.y < minY) minY = n.y; if (n.y > maxY) maxY = n.y;
      }
    }
    const span = hasPos ? Math.max(1, maxX - minX, maxY - minY) : 1;
    const scale = hasPos
      ? Math.max(0.05, Math.min(1, (Math.min(cellW, cellH) - 40) / (span + 60)))
      : 1;
    for (const s of g) {
      const key = `${gr.name}::${s.key}`;
      const ox = hasPos ? (s.x || 0) : 0;
      const oy = hasPos ? (s.y || 0) : 0;
      nodes.set(key, {
        key, id: s.id, type: s.type || "node", label: s.label || s.key,
        parent: s.parent ? `${gr.name}::${s.parent}` : null, children: [],
        status: s.status || "idle", slot: 0, depth: 0,
        x: cx + (ox - (hasPos ? (minX + maxX) / 2 : 0)) * scale,
        y: cy + (oy - (hasPos ? (minY + maxY) / 2 : 0)) * scale,
        tx: 0, ty: 0, weight: 1, born: performance.now(),
        extra: s.extra || {}, pinned: false, _sess: gr.name,
      });
      const n = nodes.get(key);
      n.tx = n.x; n.ty = n.y;
    }
  }
  for (const n of nodes.values()) {
    if (n.parent && !nodes.has(n.parent)) n.parent = null;
  }
  for (const n of nodes.values()) {
    if (n.parent && nodes.has(n.parent)) nodes.get(n.parent).children.push(n.key);
  }
  addSys(`任务总览：${groups.length} 个历史任务图谱（点击任一节点进入对应会话）`);
  resizeCanvas();
}
/* 总览节点点击 → 进入该会话 */
function overviewClick(key) {
  const n = nodes.get(key);
  if (n && n._sess) {
    restoreFromOverview();
    loadSession(n._sess);
  } else if (n) {
    restoreFromOverview();
  }
}

/* ================= 14. 节点管理 ================= */
async function showNodesView() {
  view = "nodes";
  selectNode(null);
  $("welcome-wrap").classList.add("hidden");
  $("main-wrap").classList.add("hidden");
  $("nodes-wrap").classList.remove("hidden");
  updateSaveGuard();
  const list = $("nodes-list");
  list.innerHTML = "";
  $("nodes-count").textContent = "加载中…（读取节点技能需数秒）";
  list.appendChild(el("div", "nc-desc", "正在读取节点数据…"));
  const j = await api("/api/nodes");
  list.innerHTML = "";
  const nodesArr = j.nodes || [];
  $("nodes-count").textContent = `${nodesArr.length} 个活动节点`;
  window._allNodes = nodesArr;
  renderNodes(nodesArr);
}
function renderNodes(arr) {
  const q = ($("nodes-search").value || "").toLowerCase();
  const list = $("nodes-list");
  list.innerHTML = "";
  for (const n of arr) {
    if (q && !(n.id + " " + n.describe + " " + (n.skills || []).join(" "))
          .toLowerCase().includes(q)) continue;
    const card = el("div", "node-card");
    const head = el("div", "nc-head");
    head.appendChild(el("span", null, n.id));
    head.appendChild(el("span", "badge", `分 ${Math.round(n.score || 0)}`));
    head.appendChild(el("span", "badge", `成功率 ${Math.round((n.success_rate || 0) * 100)}%`));
    card.appendChild(head);
    card.appendChild(el("div", "nc-desc", n.describe || ""));
    card.addEventListener("click", () => {
      $("nodes-search").value = "";
      showMain();
      nodes.clear(); nodeLogs.clear();
      nodes.set("ceo", { key: "ceo", id: "ceo", type: "ceo", label: "CEO 决策中枢",
        parent: null, children: [], status: "idle", slot: 0, depth: 0,
        x: 0, y: 0, tx: 0, ty: 0, weight: 1, born: performance.now(), extra: {}, pinned: false });
      nodes.set(n.id, { key: n.id, id: n.id, type: "node", label: n.id,
        parent: "ceo", children: [], status: "idle", slot: 0, depth: 1,
        x: 0, y: 0, tx: 100, ty: 100, weight: 1, born: performance.now(),
        extra: { goal: n.describe || "" }, pinned: false });
      relayout();
      selectNode(n.id);
    });
    list.appendChild(card);
  }
}

/* ================= 15. 发送消息 ================= */
async function sendText(text) {
  resumeSaving = true;
  updateSaveGuard();
  if (text.startsWith("/")) {
    addChat("user", text);
    const j = await api("/api/command", { method: "POST",
      body: JSON.stringify({ text }) });
    if (!j.ok) addSys("指令失败：" + (j.error || ""));
    else if (j.msg) addSys(j.msg);
    if (text.startsWith("/workspace")) refreshStats();
    return;
  }
  addChat("user", text);
  if (busyState) addSys("任务执行中，消息将自动排队…");
  else { setBusy(true); addSys("任务已提交"); }
  const j = await api("/api/chat", { method: "POST",
    body: JSON.stringify({ text }) });
  if (!j.ok) {
    if (!busyState) setBusy(false);
    addSys("提交失败：" + (j.error || ""));
  } else if (j.queued) {
    addSys(`已加入队列（第 ${j.position} 位）`);
    refreshStats();
  }
}
function sendChatInput() {
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";
  sendText(text);
}

/* ================= 16. 主题 / 侧栏 ================= */
function applyTheme() {
  let t = "dark";
  try { t = localStorage.getItem(THEME_KEY) || "dark"; } catch (e) {}
  document.body.dataset.theme = t;
  $("btn-theme").textContent = t === "dark" ? "☀ 白天" : "🌙 夜晚";
  colors["st-idle"] = getComputedStyle(document.body).getPropertyValue("--st-idle").trim();
}
function toggleTheme() {
  const next = document.body.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
  applyTheme();
}
function toggleSidebar() {
  const sb = $("sidebar");
  sb.classList.toggle("collapsed");
  $("btn-collapse").textContent = sb.classList.contains("collapsed") ? "»" : "«";
}
/* 图谱/聊天分隔条拖拽 */
function makeResizable(handle, target) {
  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const startY = e.clientY;
    const baseH = target.offsetHeight;
    const wrap = target.parentElement;
    const mm = (ev) => {
      const delta = startY - ev.clientY;
      const total = wrap.offsetHeight;
      const h = Math.max(120, Math.min(total - 140, baseH + delta));
      $("graph-wrap").style.flex = `${h}px`;
    };
    const mu = () => {
      window.removeEventListener("mousemove", mm);
      window.removeEventListener("mouseup", mu);
      resizeCanvas();
    };
    window.addEventListener("mousemove", mm);
    window.addEventListener("mouseup", mu);
  });
}

/* ================= 17. 初始化 ================= */
async function init() {
  applyTheme();
  initCanvas();
  setupCanvasInteractions();
  makeResizable($("divider-gc"), $("graph-wrap"));
  requestAnimationFrame(function loop() { draw(); requestAnimationFrame(loop); });

  // 绑定按钮
  $("wl-send").addEventListener("click", wlSend);
  $("wl-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); wlSend(); }
  });
  $("chat-send").addEventListener("click", sendChatInput);
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatInput(); }
    e.target.style.height = "auto";
    e.target.style.height = Math.min(120, e.target.scrollHeight) + "px";
  });
  $("btn-collapse").addEventListener("click", toggleSidebar);
  $("btn-theme").addEventListener("click", toggleTheme);
  $("btn-layout").addEventListener("click", cycleLayout);
  $("btn-tasks").addEventListener("click", taskOverview);
  $("btn-newsess").addEventListener("click", newSession);
  $("btn-nodes").addEventListener("click", showNodesView);
  $("nodes-back").addEventListener("click", showMain);
  $("nodes-search").addEventListener("input", () =>
    renderNodes(window._allNodes || []));
  $("detail-close").addEventListener("click", () => selectNode(null));
  $("detail-open-edit").addEventListener("click", () => { selectNode(null); showNodesView(); });
  $("btn-clear").addEventListener("click", () => { $("chat-log").innerHTML = ""; });
  $("graph").addEventListener("click", (e) => {
    if (view === "overview") {
      const rect = $("graph").getBoundingClientRect();
      const n = hitTest(e.clientX - rect.left, e.clientY - rect.top);
      if (n) overviewClick(n.key);
    }
  });

  // 初始视图：快照存在且有 session → 主视图自动恢复会话
  let inMain = false;
  try { inMain = localStorage.getItem(VIEW_KEY) === "main"; } catch (e) {}
  let bootOk = false;
  try {
    const st = await api("/api/state");
    if (st.ok) {
      bootOk = true;
      const savedBoot = localStorage.getItem(BOOT_KEY);
      if (st.boot_ts && savedBoot && String(savedBoot) !== String(st.boot_ts)) {
        backendRestarted = true;
        try { localStorage.removeItem(SNAP_KEY); } catch (e) {}
        try { localStorage.removeItem(VIEW_KEY); } catch (e) {}
        currentSession = null;
        addSys("检测到后端重启，已重置为新会话");
        showWelcome();
      } else {
        if (st.boot_ts) localStorage.setItem(BOOT_KEY, String(st.boot_ts));
        restoreSnapshot();
        const snapSess = (() => {
          try { return JSON.parse(localStorage.getItem(SNAP_KEY) || "{}").session || null; }
          catch (e) { return null; }
        })();
        if (snapSess) {
          await loadSession(snapSess);
        } else if (inMain || nodes.size) {
          showMain();
          if (nodes.size) $("graph-title").textContent = "当前任务图谱（新对话）";
          else { ensureCeo(); relayout(); showMain(); }
        } else {
          showWelcome();
        }
      }
      updateStats(st);
      setBusy(st.busy);
      replayEvents(st.events || []);
      if (st.events?.length) setBusy(st.busy);
      loadSessions();
    }
  } catch (e) {
    if (!bootOk) { restoreSnapshot(); showWelcome(); addSys("后端连接失败：" + e.message); }
  }
  // SSE
  const es = new EventSource("/api/events");
  es.onmessage = (msg) => {
    try { handleEvent(JSON.parse(msg.data)); } catch (e) { /* 坏帧忽略 */ }
  };
  es.onopen = () => {
    $("graph-status").className = busyState ? "busy" : "idle";
    checkRestart();
    resyncAfterReconnect();
  };
  es.onerror = () => {
    setBusy(false);
    $("graph-status").textContent = "连接中断，重连中…";
  };
}
function replayEvents(events) {
  isReplaying = true;
  try {
    for (const ev of events) {
      const t = ev.type;
      if (["chat.user","graph.task","graph.dispatch","graph.clone","node.status",
           "node.brain","node.eval","chat.ceo_tool","chat.ceo_report",
           "chat.ceo_finish","chat.ceo_end","chat.ceo_ask"].includes(t)) continue;
      handleEvent({ type: ev.type, ts: ev.ts, data: ev.data });
    }
  } finally { isReplaying = false; }
}
async function checkRestart() {
  try {
    const st = await api("/api/state");
    if (!st.ok || !st.boot_ts) return;
    const savedBoot = localStorage.getItem(BOOT_KEY);
    if (savedBoot && String(savedBoot) !== String(st.boot_ts)) {
      backendRestarted = true;
      try { localStorage.removeItem(SNAP_KEY); } catch (e) {}
      try { localStorage.removeItem(VIEW_KEY); } catch (e) {}
      currentSession = null;
      nodes.clear();
      showWelcome();
      addSys("检测到后端重启，图谱已重置");
    }
  } catch (e) { /* 静默 */ }
}
async function wlSend() {
  const input = $("wl-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  // 欢迎页输入 = 新对话（清空后端 _current_sess + 前端状态，防发到旧会话）
  await newSession();
  showMain();
  await sendText(text);
}

/* 侧栏折叠按钮在 collapsed 时 fixed 到左缘（css 已处理） */
window.addEventListener("resize", resizeCanvas);

document.addEventListener("DOMContentLoaded", init);
