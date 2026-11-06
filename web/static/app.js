/* ADGS Web 前端逻辑 — Canvas 图谱 + SSE 事件流 + 对话 + 节点详情 */
"use strict";

/* ================= 工具 ================= */
const $ = (id) => document.getElementById(id);
/* ANSI 256 色板 → hex（标准 xterm 调色板：16-231 为 6×6×6 立方体，
   232-255 为 24 级灰阶）。style.css 只定义 --ansi-0..15，256 色直接用 hex。 */
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
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

/* ================= ANSI 颜色解析（hermes 终端输出 → 浏览器颜色） ================= */
// 支持: \x1b[38;2;R;G;Bm 真彩色 / \x1b[31m 16色 / 粗体 1 / 重置 0 / 38;5;Nm 256色
function ansiToHtml(text) {
  if (!text || !text.includes("\x1b")) return null;
  const SGR_RE = /\x1b\[([0-9;]*)m/g;
  const stack = [];  // 当前生效的样式（css 片段）
  let out = "";
  let last = 0, m;
  let openCount = 0;
  while ((m = SGR_RE.exec(text)) !== null) {
    out += esc(text.slice(last, m.index));
    const codes = m[1] ? m[1].split(";").map(Number) : [0];
    applySGR(codes, stack);
    // 闭合之前的 span，按新 stack 重开
    out += "</span>".repeat(openCount);
    openCount = 0;
    if (stack.length) {
      out += `<span style="${stack.join(";")}">`;
      openCount = 1;
    }
    last = m.index + m[0].length;
  }
  out += esc(text.slice(last));
  out += "</span>".repeat(openCount);
  return out;
}
function applySGR(codes, stack) {
  for (let i = 0; i < codes.length; i++) {
    const c = codes[i];
    if (c === 0) { stack.length = 0; continue; }
    if (c === 1) { pushStyle(stack, "font-weight", "bold"); continue; }
    if (c === 3) { pushStyle(stack, "font-style", "italic"); continue; }
    if (c === 4) { pushStyle(stack, "text-decoration", "underline"); continue; }
    if (c === 38) {  // 前景色
      if (codes[i+1] === 2) {  // 真彩色 38;2;R;G;B
        const rgb = `${codes[i+2]},${codes[i+3]},${codes[i+4]}`;
        pushStyle(stack, "color", `rgb(${rgb})`);
        i += 4;
      } else if (codes[i+1] === 5) {  // 256 色 38;5;N
        const n = codes[i+2];
        // 2026-08-30 P2-18：≥16 用标准 256 色板生成 hex（style.css 只定义了
        // --ansi-0..15，引用 var(--ansi-16..255) 无效 → 之前 256 色不显色）
        pushStyle(stack, "color", n < 16 ? `var(--ansi-${n})` : ansi256ToHex(n));
        i += 2;
      }
      continue;
    }
    if (c === 48) {  // 背景色
      if (codes[i+1] === 2) {
        const rgb = `${codes[i+2]},${codes[i+3]},${codes[i+4]}`;
        pushStyle(stack, "background-color", `rgb(${rgb})`);
        i += 4;
      } else if (codes[i+1] === 5) {
        const n = codes[i+2];
        pushStyle(stack, "background-color",
                  n < 16 ? `var(--ansi-bg-${n})` : ansi256ToHex(n));
        i += 2;
      }
      continue;
    }
    // 16 色（30-37 前景，40-47 背景）
    if (c >= 30 && c <= 37) {
      pushStyle(stack, "color", `var(--ansi-${c - 30})`); continue;
    }
    if (c >= 40 && c <= 47) {
      pushStyle(stack, "background-color", `var(--ansi-bg-${c - 40})`); continue;
    }
    if (c === 39) removeStyle(stack, "color");
    if (c === 49) removeStyle(stack, "background-color");
    if (c === 22) removeStyle(stack, "font-weight");
    if (c === 23) removeStyle(stack, "font-style");
  }
}
function pushStyle(stack, prop, val) {
  // 替换同属性（后写覆盖先写）
  const idx = stack.findIndex(s => s.startsWith(prop + ":"));
  if (idx >= 0) stack.splice(idx, 1);
  stack.push(`${prop}:${val}`);
}
function removeStyle(stack, prop) {
  const idx = stack.findIndex(s => s.startsWith(prop + ":"));
  if (idx >= 0) stack.splice(idx, 1);
}

/* ================= 全局状态 ================= */
// 画布调色板：随主题切换（applyTheme 时从 CSS 变量重读）
let COLORS = {
  ceo: "#e8b339", task: "#5b8def", node: "#00d4aa",
  running: "#00d4aa", done: "#1f8a5f", failed: "#e5484d",
  waiting: "#e8b339", delegating: "#a78bfa", idle: "#5a6472", edge: "#2a3344",
  gold: "#e8b339",
  grid: "#101018", nodeBg: "#0a1f1a", nodeFg: "#c8d3e0", dim: "#6b7280",
  ceoBg: "#1c170d", ceoFg: "#f0d9a8",
  taskBg: "#0e1526", taskNum: "#9db8f5", sel: "#ffffff",
};
const THEME_KEY = "adgs_theme";
function readCanvasColors() {
  // 从 CSS 变量读取当前主题的画布配色（Canvas 无法直接用 CSS 变量）
  const v = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();
  return {
    ceo: v("--gold"), task: v("--blue"), node: v("--accent"),
    running: v("--accent"), done: v("--done"), failed: v("--red"),
    waiting: v("--gold"), delegating: v("--purple"), idle: v("--idle"), edge: v("--edge"),
    gold: v("--gold"),
    grid: v("--grid"), nodeBg: v("--node-bg"), nodeFg: v("--node-fg"), dim: v("--node-fg-dim"),
    ceoBg: v("--ceo-bg"), ceoFg: v("--ceo-fg"),
    taskBg: v("--task-bg"), taskNum: v("--task-num"), sel: v("--sel"),
  };
}
function applyTheme() {
  let theme = "dark";
  try { theme = localStorage.getItem(THEME_KEY) || "dark"; } catch (e) { /* 存储禁用 */ }
  document.body.dataset.theme = theme;
  COLORS = readCanvasColors();
  const btn = $("btn-theme");
  if (btn) btn.textContent = theme === "dark" ? "☀ 白天" : "🌙 夜晚";
}
const NODE_W = { ceo: 130, round: 100, task: 44, node: 116, clone: 116 };
const NODE_H = { ceo: 48, round: 54, task: 44, node: 42, clone: 42 };
const ROW_H = 110, PAD = 70;

const nodes = new Map();          // key -> {id,key,type,label,parent,children,status,slot,depth,x,y,tx,ty,weight,born,extra}
const nodeLogs = new Map();       // nodeId -> {brain:[], hermes:[]}
const cloneProfiles = new Map();  // profile -> nodeId
const lastDispatch = new Map();   // nodeId -> instanceKey（最近派发实例）
const taskNameToKey = new Map();  // 任务名 -> 任务节点 key（兼容旧事件 task=名字）
let selectedKey = null;
let evSeq = 0;
let roundSeq = 0;         // 任务轮次计数（CEO 拆分任务时 +1）
let currentRound = null;  // 当前轮次根节点 key（round-N）
let currentCeo = null;    // 保留（兼容）
let roundActive = false;  // 当前轮次是否活跃（graph.task 开始，finish/end 结束）

const cam = { px: 0, py: 0, zoom: 1 };  // 平移（屏幕像素）+ 缩放
const cv = $("graph"), ctx = cv.getContext("2d");

function nodeId(nodeId) { return `ags_${nodeId.replace(/^ags_/, "")}`; }

/* ================= 图谱快照（localStorage 持久化，刷新恢复拓扑） ================= */
const SNAP_KEY = "adgs_graph_snap_v2";  // v2: 轮次树结构（round-N 为根）；v1 旧结构自动作废
let _snapTimer = null;
function snapNode(n) {
  return {
    key: n.key, id: n.id, type: n.type, label: n.label,
    parent: n.parent, status: n.status,
    x: n.x, y: n.y, tx: n.tx, ty: n.ty, pinned: n.pinned || false,
    extra: {
      goal: (n.extra && n.extra.goal || "").slice(0, 2000),
      text: (n.extra && n.extra.text || "").slice(0, 200),
      seq: n.extra && n.extra.seq,
      user_text: (n.extra && n.extra.user_text || "").slice(0, 2000),
      ceo_first: (n.extra && n.extra.ceo_first || "").slice(0, 1500),
      overall_goal: (n.extra && n.extra.overall_goal || "").slice(0, 2000),
      task_title: (n.extra && n.extra.task_title || "").slice(0, 300),
      count: n.extra && n.extra.count,
    },
  };
}
function saveSnapshot() {
  // 节流：节点变化频繁，每 2s 最多写一次
  if (_snapTimer) return;
  _snapTimer = setTimeout(() => {
    _snapTimer = null;
    try {
      const snap = [];
      for (const n of nodes.values()) snap.push(snapNode(n));
      // 2026-08-30：快照带当前会话名——刷新后据此恢复"刚才在看的会话"
      // （图谱+聊天+标题），否则刷新后只剩无主图谱、用户不知道是哪个会话
      localStorage.setItem(SNAP_KEY, JSON.stringify({
        t: Date.now(), nodes: snap, mode: layoutMode,
        session: currentSession || null }));
    } catch (e) { /* 存储满/禁用时静默 */ }
  }, 2000);
}
let backendRestarted = false;  // 服务端重启标志：仅重启后才降级图谱 running 节点
function applyGraphSnapshot(snap, opts) {
  /* 重建图谱拓扑（restoreSnapshot 与历史会话 loadSession 共用）
     opts.restoreMode: 是否恢复布局模式（localStorage 恢复用） */
  if (!snap || !snap.nodes || !snap.nodes.length) return false;
  if (opts && opts.restoreMode) {
    if (snap.mode === "radial") layoutMode = "radial";
    if (snap.mode === "tasks") layoutMode = "tasks";
  }
  const hasRound = snap.nodes.some(s => s.type === "round");
  if (hasRound) {
    // 合法集 = CEO 根 + round 树（round 及其所有子孙）
    const legal = new Set(["ceo"]);
    const isLegal = (s) => s.type === "round" || (legal.has(s.parent) && s.parent !== null)
      || (s.key === "ceo");
    let changed = true;
    while (changed) {
      changed = false;
      for (const s of snap.nodes) {
        if (isLegal(s) && !legal.has(s.key)) { legal.add(s.key); changed = true; }
      }
    }
    for (const s of snap.nodes) {
      if (legal.has(s.key)) {
        upsertNode(s.key, {
          id: s.id, type: s.type, label: s.label, parent: s.parent,
          // 2026-08-29：恢复正常状态恢复——仅服务端重启（boot_ts 变化）时
          // 由调用方降级 running；正常切会话/刷新时任务还活着，保留 running
          status: s.status,
          extra: s.extra || {},
          x: s.x, y: s.y, tx: s.tx, ty: s.ty, pinned: s.pinned,
        });
      }
    }
  } else {
    for (const s of snap.nodes) {
      upsertNode(s.key, {
        id: s.id, type: s.type, label: s.label, parent: s.parent,
        status: s.status,
        extra: s.extra || {},
        x: s.x, y: s.y, tx: s.tx, ty: s.ty, pinned: s.pinned,
      });
    }
  }
  // 推导 roundSeq / currentRound：取快照中最大 round-N
  let maxR = 0;
  for (const s of snap.nodes) {
    if (s.type === "round") {
      const m = /^round-(\d+)$/.exec(s.key || "");
      if (m) maxR = Math.max(maxR, parseInt(m[1], 10));
    }
  }
  if (maxR > 0) {
    roundSeq = maxR;
    currentRound = `round-${maxR}`;
    currentCeo = null;
    roundActive = false;  // 视为无活跃轮次
  }
  // 恢复后 CEO 无标题 → 用第一个轮次的用户输入补（旧快照缺标题，
  // 2026-08-29：此前 CEO 点开一直"（暂无任务）"）
  const ceoN = nodes.get("ceo");
  if (ceoN && !(ceoN.extra && ceoN.extra.task_title)) {
    const firstRound = [...nodes.values()].find(n => n.type === "round");
    if (firstRound && firstRound.extra && firstRound.extra.user_text) {
      ceoN.extra = ceoN.extra || {};
      ceoN.extra.task_title = String(firstRound.extra.user_text).slice(0, 300);
    }
  }
  return true;
}
function restoreSnapshot() {
  try {
    const raw = localStorage.getItem(SNAP_KEY);
    if (!raw) return false;
    const snap = JSON.parse(raw);
    // 2026-08-30：恢复快照同时恢复它所属的会话（刷新后前端会话隔离
    // 过滤、图谱保存语义都以此为准）
    if (snap.session) currentSession = snap.session;
    return applyGraphSnapshot(snap, { restoreMode: true });
  } catch (e) {
    return false;
  }
}
function upsertNode(key, opts) {
  let n = nodes.get(key);
  if (!n) {
    n = { key, id: opts.id, type: opts.type || "node", label: opts.label || key,
          parent: opts.parent || null, children: [],
          status: "idle", slot: 0, depth: 0,
          x: 0, y: 0, tx: 0, ty: 0, weight: 1, born: performance.now(),
          extra: opts.extra || {} };
    if (n.parent && nodes.has(n.parent)) nodes.get(n.parent).children.push(key);
    nodes.set(key, n);
    evSeq++;
  }
  if (opts.label) n.label = opts.label;
  if (opts.status) n.status = opts.status;
  if (opts.type && n.type === "node" && opts.type === "clone") n.type = "clone";
  relayout();
  // 恢复拖拽位置（快照恢复时传入 x/y/tx/ty）——放 relayout 后覆盖默认布局
  if (typeof opts.x === "number" && !isNaN(opts.x)) {
    n.x = n.tx = opts.x;
    n.y = n.ty = opts.y;
    if (opts.pinned) n.pinned = true;
  }
  saveSnapshot();
  return n;
}

/* ================= 图谱布局（紧凑树 / CEO 中心放射树） ================= */
let layoutMode = "tree";  // tree=紧凑树（默认）| radial=CEO 中心放射（一键整理）
let layoutCycle = null;   // 整理布局三态循环状态（2026-08-30）：
// {saved:[{key,x,y}], mode, cam:{px,py,zoom}, step} —— 第一次点击保存"原版"，
// 之后 tree→radial→原版 循环；恢复时坐标/视角/模式原样返回
function computeWeights(id) {
  const n = nodes.get(id); if (!n) return 1;
  if (!n.children.length) { n.weight = 1; return 1; }
  let w = 0;
  for (const c of n.children) w += computeWeights(c);
  n.weight = Math.max(1, w);
  return n.weight;
}
let slotCursor = 0;
function assign(id, depth) {
  const n = nodes.get(id);
  n.depth = depth;
  if (!n.children.length) {
    n.slot = slotCursor++;
  } else {
    assign(n.children[0], depth + 1);
    for (let i = 1; i < n.children.length; i++) assign(n.children[i], depth + 1);
    n.slot = (nodes.get(n.children[0]).slot + nodes.get(n.children[n.children.length - 1]).slot) / 2;
  }
}
/* ---- 气球图核心（radialLayout 全局 / taskOverviewLayout 按任务复用） ---- */
function subtreeR(id, kidsOf, rMemo) {
  if (rMemo.has(id)) return rMemo.get(id);
  const n = nodes.get(id);
  if (!n) return 0;
  const kids = kidsOf(id);
  let r;
  if (!kids.length) {
    r = (NODE_W[n.type] || 90) / 2 + 12;  // 叶子：自身尺寸 + 边距
  } else {
    let maxChildR = 0;
    for (const k of kids) maxChildR = Math.max(maxChildR, subtreeR(k, kidsOf, rMemo));
    r = ringRadius(id, kidsOf, rMemo) + maxChildR;
  }
  rMemo.set(id, r);
  return r;
}
function ringRadius(id, kidsOf, rMemo) {
  const n = nodes.get(id);
  const kids = kidsOf(id);
  const k = kids.length;
  if (!k) return 0;
  const rs = kids.map(kk => subtreeR(kk, kidsOf, rMemo));
  let maxChildHalf = 0;
  for (const kk of kids) maxChildHalf = Math.max(maxChildHalf, (NODE_W[nodes.get(kk).type] || 90) / 2);
  let ring = Math.max(Math.max(...rs) + 30, (NODE_W[n.type] || 90) / 2 + maxChildHalf + 12);
  for (let iter = 0; iter < 40; iter++) {
    const th = [];
    for (let i = 0; i < k; i++) {
      const x = (rs[i] + rs[(i + 1) % k] + 10) / (2 * ring);
      th.push(2 * Math.asin(Math.min(1, x)));
    }
    let totalMax = 0;
    for (let i = 0; i < k; i++) totalMax += Math.max(th[i], th[(i + 1) % k]);
    if (totalMax <= Math.PI * 2 * 0.98) break;
    ring *= 1.12;
  }
  return ring;
}
function placeRadial(id, cx0, cy0, kidsOf, rMemo) {
  const n = nodes.get(id); if (!n) return;
  n.tx = cx0; n.ty = cy0;
  const kids = kidsOf(id);
  const k = kids.length;
  if (!k) return;
  const rs = kids.map(kk => subtreeR(kk, kidsOf, rMemo));
  const ring = ringRadius(id, kidsOf, rMemo);
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
    placeRadial(kids[i], cx0 + Math.cos(ca) * ring, cy0 + Math.sin(ca) * ring, kidsOf, rMemo);
    a += arc[i] * scale;
  }
}
function radialLayout() {
  /* 一键整理：气球图（balloon layout）—— 每个父节点都是局部圆心，
     子节点围绕各自的父节点排圆；子树有包围圆约束，整体互不遮挡。 */
  if (!nodes.size) return;
  // 一键整理 = 全部重新摆放：解除所有手动定位
  for (const n of nodes.values()) n.pinned = false;
  // 根：ceo 置圆心；无 ceo 时以第一个根节点作圆心
  const orphans = [...nodes.values()]
    .filter(n => n.key !== "ceo" && (!n.parent || !nodes.has(n.parent)));
  const rootKey = nodes.has("ceo") ? "ceo" : (orphans.length ? orphans[0].key : null);
  if (!rootKey) return;
  // 圆心节点的"虚拟子" = 无父的根节点 + 圆心节点自身 children（防老结构漏挂）
  const virtKids = [];
  for (const o of orphans) if (o.key !== rootKey) virtKids.push(o.key);
  const rootNode = nodes.get(rootKey);
  if (rootNode && rootNode.children) {
    for (const c of rootNode.children) {
      if (c !== rootKey && !virtKids.includes(c)) virtKids.push(c);
    }
  }
  const kidsOf = (id) => (id === rootKey ? virtKids : nodes.get(id).children);
  const rMemo = new Map();
  placeRadial(rootKey, cv.width / 2, cv.height / 2, kidsOf, rMemo);
}
let overviewBackup = null;  // 任务总览前的图谱备份（退出总览时恢复）
function restoreFromOverview() {
  /* 总览是浏览模式：发新消息/新对话时自动退出，恢复进入前的图谱，
     避免在合并图谱上叠加新节点导致错乱 */
  if (!overviewBackup || layoutMode !== "tasks") return;
  nodes.clear();
  nodeLogs.clear();
  lastDispatch.clear();
  taskNameToKey.clear();
  applyGraphSnapshot({ nodes: overviewBackup }, { restoreMode: false });
  overviewBackup = null;
  layoutMode = "tree";
  relayout();
  // 2026-08-30：退出总览后恢复保存抑制状态（新对话=恢复保存；
  // 查看历史会话=继续抑制）——此前总览期间抑制缺失，合并图谱被
  // POST 回 _current_sess 会话（用户实测 001846 被混入测试会话节点）
  graphSaveSuppressed = currentSession ? true : false;
  addSys("已退出任务总览，回到原会话视图");
}
function taskOverviewAll() {
  // 2026-08-30：总览 = 合并图谱浏览模式，全程禁止自动保存
  //（合并图谱含所有会话节点，POST 回任何会话都会污染它）
  graphSaveSuppressed = true;
  /* 任务总览（2026-08-28 用户规则 v2）：
     - 每个历史任务的图谱先【自己整理】：以该任务自己的 CEO 节点为圆心做
       气球图（复用 placeRadial），整理后的形态就是任务图谱的样子
     - 每个任务的"圆" = 以 CEO 为圆心、离 CEO 最远的节点到 CEO 的距离为半径
     - 圆与圆相切不重叠（物理松弛 + 模拟退火），大圆居中、小圆向外
     - 圆不重合 → 任务图谱不堆叠 */
  selectNode(null);  // 2026-08-30：进总览前关闭详情面板（防遮挡顶栏）
  return fetch("/api/sessions/graphs")
    .then(r => r.json())
    .then(j => {
      if (!j.ok || !j.sessions || !j.sessions.length) {
        addSys("没有可总览的历史任务图谱（任务结束后自动保存）");
        return;
      }
      const rawPut = (key, opts) => {
        const n = { key, id: opts.id, type: opts.type || "node",
          label: opts.label || key, parent: opts.parent || null,
          children: [], status: opts.status || "idle",
          slot: 0, depth: 0, x: 0, y: 0, tx: 0, ty: 0, weight: 1,
          born: performance.now(), extra: opts.extra || {} };
        if (n.parent && nodes.has(n.parent)) nodes.get(n.parent).children.push(key);
        nodes.set(key, n);
        return n;
      };
      // 备份进入总览前的图谱（发消息/新对话时恢复，防合并图谱被新节点污染）
      overviewBackup = [...nodes.values()].map(snapNode);
      nodes.clear();
      nodeLogs.clear();
      lastDispatch.clear();
      taskNameToKey.clear();
      // 1) 临时挂载所有会话图谱（key 加会话前缀；快照顺序父先于子）。
      //    空图谱任务：CEO 占位节点（2026-08-29 用户规则），点开显示任务名
      const groups = [];
      for (const s of j.sessions) {
        let g = s.graph || [];
        if (!g.length) {
          g = [{ key: "ceo", id: "ceo", type: "ceo", label: "CEO 决策中枢",
                 parent: null, status: "idle", x: 0, y: 0, tx: 0, ty: 0,
                 extra: { task_title: s.preview || s.name } }];
        }
        const P = s.name + "::";
        for (const n of g) {
          rawPut(P + (n.key || n.id), {
            id: n.id, type: n.type, label: n.label,
            parent: n.parent ? P + n.parent : null,
            status: n.status, extra: n.extra || {},
          });
        }
        // 任务根 = 该任务的 CEO 节点（快照里 key="ceo"），无则第一个无父节点
        const ceoKey = P + "ceo";
        const rootKey = nodes.has(ceoKey) ? ceoKey
          : (g.find(nn => !nn.parent) ? P + g.find(nn => !nn.parent).key : null);
        if (rootKey) groups.push({ name: s.name, preview: s.preview || s.name,
                                   prefix: P, rootKey, members: [] });
      }
      if (!groups.length) { addSys("没有可总览的历史任务图谱"); return; }
      // 2) 每个任务自己整理：以 CEO 为圆心做气球图；半径 = CEO 到最远节点
      const kidsOf = (id) => nodes.get(id).children;
      for (const gr of groups) {
        const rMemo = new Map();
        placeRadial(gr.rootKey, 0, 0, kidsOf, rMemo);
        const cm = (id) => { gr.members.push(id); for (const c of nodes.get(id).children) cm(c); };
        cm(gr.rootKey);
        let maxR = 0;
        for (const m of gr.members) {
          const nn = nodes.get(m);
          maxR = Math.max(maxR, Math.hypot(nn.tx, nn.ty));  // CEO 在 (0,0)
        }
        gr.radius = Math.max(maxR, Math.sqrt(gr.members.length) * 70) + 60;
        gr.cx = 0; gr.cy = 0;
      }
      // 3) 初始排布：同一圆周、角度均匀，圆周半径按「相邻相切」反解
      groups.sort((a, b) => b.radius - a.radius);
      const R0 = groups[0].radius;
      const nG = groups.length;
      let maxArc = 0;
      for (let i = 0; i < nG; i++) {
        const kk = (i + 1) % nG;
        maxArc = Math.max(maxArc, groups[i].radius + groups[kk].radius + 24);
      }
      const Rinit = (nG > 1) ? (maxArc * nG) / (2 * Math.PI) : R0 + 40;
      for (let i = 0; i < nG; i++) {
        const ang = (i / nG) * Math.PI * 2;
        groups[i].cx = Math.cos(ang) * Rinit;
        groups[i].cy = Math.sin(ang) * Rinit;
      }
      // 4) 物理松弛：重叠推离 + 中心引力（大圆居中），模拟退火收敛相切
      for (let iter = 0; iter < 500; iter++) {
        const cool = 1 - iter / 500;
        const pushK = 0.4 + 0.6 * cool;
        for (let i = 0; i < nG; i++) {
          for (let kk = i + 1; kk < nG; kk++) {
            const a = groups[i], b = groups[kk];
            const dx = b.cx - a.cx, dy = b.cy - a.cy;
            const d = Math.hypot(dx, dy) || 1;
            const minD = a.radius + b.radius + 24;
            if (d < minD) {
              const push = (minD - d) / 2 * 0.7 * pushK;
              const ux = dx / d, uy = dy / d;
              a.cx -= ux * push; a.cy -= uy * push;
              b.cx += ux * push; b.cy += uy * push;
            }
          }
        }
        for (const m of groups) {
          const gF = 0.025 * (m.radius / R0) * cool;
          m.cx -= m.cx * gF; m.cy -= m.cy * gF;
        }
      }
      // 5) 整体包围盒 → 平移到画布中心；节点坐标 = 整理后局部坐标 + 圆心偏移
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const m of groups) {
        minX = Math.min(minX, m.cx - m.radius); maxX = Math.max(maxX, m.cx + m.radius);
        minY = Math.min(minY, m.cy - m.radius); maxY = Math.max(maxY, m.cy + m.radius);
      }
      const offX = (cv.width / 2) - (minX + maxX) / 2;
      const offY = (cv.height / 2) - (minY + maxY) / 2;
      for (const m of groups) {
        for (const mk of m.members) {
          const nn = nodes.get(mk);
          nn.tx += m.cx + offX; nn.ty += m.cy + offY;
          nn.x = nn.tx; nn.y = nn.ty;
        }
      }
      layoutMode = "tasks";
      fitRadialView();
      // 不调 saveSnapshot：总览是临时浏览视图，不污染本地持久化快照
      addSys(`任务总览：${groups.length} 个历史任务（每个任务先整理为 CEO 中心气球图，` +
             `圆 = CEO 到最远节点距离，圆相切不重叠，大圆居中）`);
    })
    .catch(() => addSys("任务总览加载失败（后端版本过旧？）"));
}
function fitRadialView() {
  /* 整理后自动缩放/平移，让整个放射图完整可见（只由「整理布局」按钮触发，
     不干预用户手动缩放平移） */
  if (!nodes.size) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of nodes.values()) {
    if (n.tx < minX) minX = n.tx;
    if (n.tx > maxX) maxX = n.tx;
    if (n.ty < minY) minY = n.ty;
    if (n.ty > maxY) maxY = n.ty;
  }
  const margin = 40;
  const bw = Math.max(1, maxX - minX + margin * 2);
  const bh = Math.max(1, maxY - minY + margin * 2);
  const nz = Math.min(1.3, Math.max(0.005, Math.min(cv.width / bw, cv.height / bh)));
  cam.zoom = nz;
  cam.px = cv.width / 2 - (minX + maxX) / 2 * nz;
  cam.py = cv.height / 2 - (minY + maxY) / 2 * nz;
}
function relayout() {
  if (!nodes.size) return;
  if (layoutMode === "radial") { radialLayout(); return; }
  if (layoutMode === "tasks") return;  // 任务总览：坐标已由 taskOverviewAll 定死，不重排
  // 多根布局：每轮对话一棵子树（round-N 为根），根节点水平均布
  // 2026-08-30 修复：parent 指向不存在节点的"孤儿"也算根——历史会话
  // 图谱里 round 节点的 parent 字段可能残留（指向已清空的 key），原逻辑
  // 只认 !n.parent，孤儿被忽略 → 整图无根 → relayout 直接 return 不布局
  const roots = [];
  for (const n of nodes.values()) {
    if (!n.parent || !nodes.has(n.parent)) roots.push(n.key);
  }
  if (!roots.length) {
    // 无显式根时回退：以 ceo 为根（旧结构兼容）
    if (nodes.has("ceo")) roots.push("ceo");
    else return;
  }
  roots.sort();
  // 每根单独计算权重与布局
  for (const r of roots) computeWeights(r);
  slotCursor = 0;
  for (const r of roots) assign(r, 0);
  const total = Math.max(1, slotCursor);
  for (const n of nodes.values()) {
    if (n.pinned) continue;  // 手动拖拽定位的节点保持位置（子树跟随已整体移动）
    n.tx = (n.depth === 0 ? 0.5 : (n.slot + 0.5) / total) * cv.width;
    n.ty = PAD + n.depth * ROW_H;
  }
}
function ensureCeo() {
  // 空态占位：仅当图谱完全为空时创建全局 CEO（否则多根布局会被它干扰）
  if (!nodes.size && !nodes.has("ceo")) {
    nodes.set("ceo", { key: "ceo", id: "ceo", type: "ceo", label: "CEO 决策中枢",
      parent: null, children: [], status: "idle", slot: 0, depth: 0,
      x: 0, y: 0, tx: 0, ty: 0, weight: 1, born: performance.now(), extra: {} });
    relayout();
  }
}

/* ================= 图谱绘制 ================= */
function sx(x) { return x * cam.zoom + cam.px; }
function sy(y) { return y * cam.zoom + cam.py; }

function draw() {
  ctx.clearRect(0, 0, cv.width, cv.height);
  // 网格背景
  ctx.strokeStyle = COLORS.grid;
  ctx.lineWidth = 1;
  for (let gx = 0; gx < cv.width; gx += 40) {
    ctx.beginPath(); ctx.moveTo(gx + 0.5, 0); ctx.lineTo(gx + 0.5, cv.height); ctx.stroke();
  }
  for (let gy = 0; gy < cv.height; gy += 40) {
    ctx.beginPath(); ctx.moveTo(0, gy + 0.5); ctx.lineTo(cv.width, gy + 0.5); ctx.stroke();
  }
  const t = performance.now();
  const radial = layoutMode === "radial" || layoutMode === "tasks";
  // 连线
  for (const n of nodes.values()) {
    if (!n.parent || !nodes.has(n.parent)) continue;
    const p = nodes.get(n.parent);
    const x1 = sx(p.x), y1 = sy(p.y) + NODE_H[p.type] / 2 * cam.zoom;
    const x2 = sx(n.x), y2 = sy(n.y) - NODE_H[n.type] / 2 * cam.zoom;
    const born = Math.min(1, Math.max(0, (t - n.born) / 500));
    const ex = x1 + (x2 - x1) * born, ey = y1 + (y2 - y1) * born;
    const color = n.status === "running" ? COLORS.running
      : n.status === "failed" ? COLORS.failed
      : n.status === "done" ? COLORS.done
      : n.status === "waiting" ? COLORS.waiting
      : n.status === "delegating" ? COLORS.delegating : COLORS.edge;
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.45;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (radial) {
      // 放射布局：父中心 → 子中心直线（向四周发散）
      ctx.moveTo(sx(p.x), sy(p.y));
      ctx.lineTo(ex, ey);
    } else {
      ctx.moveTo(x1, y1);
      ctx.quadraticCurveTo(x1, (y1 + ey) / 2, ex, ey);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  // 节点
  for (const n of nodes.values()) {
    const born = Math.min(1, Math.max(0, (t - n.born) / 450));
    const scale = 0.6 + 0.4 * born;
    drawNode(n, t, scale);
  }
}
function drawNode(n, t, scale) {
  // 位置插值
  n.x += (n.tx - n.x) * 0.16;
  n.y += (n.ty - n.y) * 0.16;
  const w = NODE_W[n.type] * cam.zoom, h = NODE_H[n.type] * cam.zoom;
  const cx = sx(n.x), cy = sy(n.y);
  const x = cx - w / 2, y = cy - h / 2;
  const r = 8 * cam.zoom;
  // 呼吸光环（执行中）
  if (n.status === "running") {
    const a = 0.25 + 0.25 * Math.sin(t / 260);
    ctx.strokeStyle = COLORS.running;
    ctx.globalAlpha = a;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(x - 6 * cam.zoom, y - 6 * cam.zoom, w + 12 * cam.zoom, h + 12 * cam.zoom, r + 4);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  const sel = n.key === selectedKey;
  // round = 对话轮次根节点：金色圆角方块，显示轮次号 + 对话摘要
  if (n.type === "round") {
    const w2 = NODE_W.round * cam.zoom * scale, h2 = NODE_H.round * cam.zoom * scale;
    const x2 = cx - w2 / 2, y2 = cy - h2 / 2;
    const r2 = 10 * cam.zoom;
    // 呼吸光晕（本轮有任务执行中）
    if (n.status === "running") {
      const a = 0.25 + 0.25 * Math.sin(t / 260);
      ctx.strokeStyle = COLORS.running;
      ctx.globalAlpha = a;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.roundRect(x2 - 6 * cam.zoom, y2 - 6 * cam.zoom, w2 + 12 * cam.zoom, h2 + 12 * cam.zoom, r2 + 4);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    ctx.fillStyle = COLORS.ceoBg;
    ctx.strokeStyle = COLORS.gold;
    ctx.lineWidth = 1.8 * cam.zoom;
    ctx.beginPath();
    ctx.roundRect(x2, y2, w2, h2, r2);
    ctx.fill();
    ctx.stroke();
    // 轮次号（"轮 N"）
    ctx.fillStyle = COLORS.ceoFg;
    ctx.font = `bold ${Math.round(15 * cam.zoom * scale)}px "Cascadia Code", Consolas, monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText((n.label || "").slice(0, 6), cx, cy - 4 * cam.zoom);
    // 对话摘要（小字）
    const brief = (n.extra && n.extra.text ? n.extra.text : n.typeLabel || "").slice(0, 12);
    ctx.font = `${Math.round(8.5 * cam.zoom * scale)}px "Microsoft YaHei", sans-serif`;
    ctx.fillStyle = COLORS.dim;
    ctx.fillText(brief + (brief.length >= 12 ? "…" : ""), cx, cy + 12 * cam.zoom);
    if (sel) {
      ctx.strokeStyle = COLORS.sel;
      ctx.globalAlpha = 0.7;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.roundRect(x2 - 3 * cam.zoom, y2 - 3 * cam.zoom, w2 + 6 * cam.zoom, h2 + 6 * cam.zoom, r2 + 3);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    return;
  }
  // task = 序号节点：画金色圆形（区别于方形节点）
  if (n.type === "task") {
    const r = 20 * cam.zoom * scale;
    const cx0 = cx, cy0 = cy;
    // 呼吸光晕（执行中）
    if (n.status === "running") {
      const a = 0.3 + 0.3 * Math.sin(t / 240);
      ctx.strokeStyle = COLORS.running;
      ctx.globalAlpha = a;
      ctx.lineWidth = 2.5 * cam.zoom;
      ctx.beginPath();
      ctx.arc(cx0, cy0, r + 7 * cam.zoom, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    ctx.fillStyle = COLORS.taskBg;
    ctx.strokeStyle = n.status === "running" ? COLORS.running
      : n.status === "failed" ? COLORS.failed
      : n.status === "done" ? COLORS.done
      : COLORS.task;  // 序号节点：蓝色（区别于轮次金色）
    ctx.lineWidth = 2 * cam.zoom;
    ctx.beginPath();
    ctx.arc(cx0, cy0, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    // 序号数字
    ctx.fillStyle = COLORS.taskNum;
    ctx.font = `bold ${Math.round(15 * cam.zoom * scale)}px "Cascadia Code", Consolas, monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText((n.label || "").slice(0, 3), cx0, cy0);
    // 下方小字：第N次派发
    ctx.font = `${Math.round(9 * cam.zoom * scale)}px "Microsoft YaHei", sans-serif`;
    ctx.fillStyle = COLORS.dim;
    ctx.fillText(n.typeLabel || "", cx0, cy0 + r + 12 * cam.zoom);
    if (sel) {
      ctx.strokeStyle = COLORS.sel;
      ctx.globalAlpha = 0.7;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(cx0, cy0, r + 3 * cam.zoom, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    return;
  }
  // 主体
  const fill = n.type === "ceo" ? COLORS.ceoBg : COLORS.nodeBg;
  const stroke = n.type === "ceo" ? COLORS.ceo
    : n.type === "task" ? COLORS.task
    : n.status === "running" ? COLORS.running
    : n.status === "failed" ? COLORS.failed
    : n.status === "done" ? COLORS.done
    : n.status === "waiting" ? COLORS.waiting
    : n.status === "delegating" ? COLORS.delegating : COLORS.edge;
  ctx.save();
  ctx.globalAlpha = 1;
  ctx.translate(cx, cy);
  ctx.scale(scale, scale);
  ctx.translate(-cx, -cy);
  ctx.fillStyle = fill;
  ctx.strokeStyle = stroke;
  ctx.lineWidth = (n.type === "clone" ? 1.6 : 1.6) * cam.zoom;
  if (n.type === "clone") ctx.setLineDash([5 * cam.zoom, 4 * cam.zoom]);
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
  ctx.fill();
  ctx.stroke();
  ctx.setLineDash([]);
  if (sel) {
    ctx.strokeStyle = COLORS.sel;
    ctx.globalAlpha = 0.7;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(x - 3 * cam.zoom, y - 3 * cam.zoom, w + 6 * cam.zoom, h + 6 * cam.zoom, r + 3);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  // 状态点
  const dotColor = n.status === "running" ? COLORS.running
    : n.status === "failed" ? COLORS.failed
    : n.status === "done" ? COLORS.done
    : n.status === "waiting" ? COLORS.waiting
    : n.status === "delegating" ? COLORS.delegating
    : n.type === "ceo" ? COLORS.ceo : n.type === "task" ? COLORS.task : COLORS.edge;
  ctx.fillStyle = dotColor;
  ctx.beginPath();
  ctx.arc(x + 10 * cam.zoom, cy, 3.5 * cam.zoom, 0, Math.PI * 2);
  ctx.fill();
  // 文本
  ctx.fillStyle = n.type === "ceo" ? COLORS.ceoFg : COLORS.nodeFg;
  ctx.font = `${Math.round(12 * cam.zoom * scale)}px "Cascadia Code", Consolas, monospace`;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  const label = (n.label || "").slice(0, 16);
  ctx.fillText(label, x + 18 * cam.zoom, cy);
  ctx.font = `${Math.round(9.5 * cam.zoom * scale)}px "Microsoft YaHei", sans-serif`;
  ctx.fillStyle = COLORS.dim;
  ctx.textAlign = "center";
  ctx.fillText(n.typeLabel || "", cx, y + h - 7 * cam.zoom);
  ctx.restore();
}

/* ================= 图谱交互 ================= */
let drag = null;
// 节点拖拽：拖动上层节点时，整个子树保持相对位置跟随（2026-08-24）
function hitTestNode(mx, my) {
  // mx/my 为画布像素坐标（未缩放）
  for (const n of nodes.values()) {
    const w = NODE_W[n.type] || 100, h = NODE_H[n.type] || 40;
    // 圆形序号节点用半径判定，其他用矩形
    if (n.type === "task") {
      const r = 20;
      if (Math.hypot(mx - n.x, my - n.y) <= r + 6) return n;
    } else {
      if (Math.abs(mx - n.x) <= w / 2 + 6 && Math.abs(my - n.y) <= h / 2 + 6) return n;
    }
  }
  return null;
}
function collectSubtree(key, out) {
  // 收集节点及其全部子孙（相对偏移记录）
  const n = nodes.get(key);
  if (!n) return;
  out.push(n);
  for (const c of n.children) collectSubtree(c, out);
}
cv.addEventListener("mousedown", (e) => {
  const rect = cv.getBoundingClientRect();
  const mx = (e.clientX - rect.left - cam.px) / cam.zoom;
  const my = (e.clientY - rect.top - cam.py) / cam.zoom;
  const hit = hitTestNode(mx, my);
  if (hit) {
    // 命中节点 → 拖动节点子树（保持子节点相对位置）
    const sub = [];
    collectSubtree(hit.key, sub);
    const offsets = sub.map(n => ({ n, dx: n.x - hit.x, dy: n.y - hit.y }));
    drag = { mode: "node", key: hit.key, x: e.clientX, y: e.clientY,
             baseX: hit.x, baseY: hit.y, sub, offsets, moved: false };
  } else {
    // 空白处 → 拖动画布
    drag = { mode: "cam", x: e.clientX, y: e.clientY,
             px: cam.px, py: cam.py, moved: false };
  }
});
window.addEventListener("mousemove", (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  if (!drag.moved) return;
  if (drag.mode === "cam") {
    cam.px = drag.px + dx; cam.py = drag.py + dy;
  } else {
    // 节点拖动：节点 + 整个子树按同一偏移移动（保持相对位置）
    const nx = drag.baseX + dx / cam.zoom, ny = drag.baseY + dy / cam.zoom;
    for (const { n, dx: odx, dy: ody } of drag.offsets) {
      n.tx = nx + odx;
      n.ty = ny + ody;
      n.x = n.tx;
      n.y = n.ty;
      n.pinned = true;  // 手动定位：relayout 不再重置该子树位置
    }
  }
});
window.addEventListener("mouseup", (e) => {
  if (drag && !drag.moved) {
    // 点击命中检测（未拖动 = 点击）
    const rect = cv.getBoundingClientRect();
    const mx = (e.clientX - rect.left - cam.px) / cam.zoom;
    const my = (e.clientY - rect.top - cam.py) / cam.zoom;
    const hit = hitTestNode(mx, my);
    selectNode(hit ? hit.key : null);
  }
  if (drag && drag.mode === "node") saveSnapshot();  // 拖完保存新位置
  drag = null;
});
cv.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = cv.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const f = e.deltaY < 0 ? 1.1 : 0.9;
  const nz = Math.min(2.5, Math.max(0.005, cam.zoom * f));
  // 以鼠标为中心缩放
  cam.px = mx - (mx - cam.px) * (nz / cam.zoom);
  cam.py = my - (my - cam.py) * (nz / cam.zoom);
  cam.zoom = nz;
}, { passive: false });

function resizeCanvas() {
  const wrap = $("graph-wrap");
  cv.width = wrap.clientWidth;
  cv.height = wrap.clientHeight;
  relayout();
}
window.addEventListener("resize", resizeCanvas);

/* ================= 节点详情 ================= */
function profileNode(profile) {
  if (cloneProfiles.has(profile)) return cloneProfiles.get(profile);
  const m = /^(ags_\d+)/.exec(profile || "");
  return m ? m[1] : profile;
}
function buildLogLine(entry) {
  const line = el("div", `dl-line ${entry.cls || ""}`);
  line.append(el("span", "ts", fmtTime(entry.ts)));
  const html = ansiToHtml(entry.text);
  if (html !== null) {
    // ANSI 颜色 → 渲染成带色 span（内嵌 HTML，必须用 innerHTML）
    const body = el("span", "log-body");
    body.innerHTML = html;
    line.append(body);
  } else {
    line.append(document.createTextNode(entry.text));
  }
  return line;
}
function pushLog(nodeId, kind, entry) {
  if (!nodeLogs.has(nodeId)) nodeLogs.set(nodeId, { brain: [], hermes: [] });
  nodeLogs.get(nodeId)[kind].push(entry);
  // 选中该节点时增量追加一行（不整面板重绘、不重复 fetch，保证高频 hermes 输出实时流畅）
  if (selectedKey) {
    const inst = nodes.get(selectedKey);
    if (inst && inst.id === nodeId) {
      const dom = kind === "brain" ? $("detail-brain") : $("detail-hermes");
      const empty = dom.querySelector(".dl-empty");
      if (empty) empty.remove();
      dom.append(buildLogLine(entry));
      dom.scrollTop = dom.scrollHeight;
    }
  }
}
function selectNode(key) {
  selectedKey = key;
  const d = $("detail");
  if (!key) {
    d.classList.add("hidden");
    return;
  }
  const n = nodes.get(key);
  d.classList.remove("hidden");
  $("detail-title").textContent = n.type === "ceo" ? "CEO" : n.label;
  renderDetail();
}
let metaCache = null;  // 节点元数据 fetch 缓存（切换节点才重新拉取）
function renderDetail() {
  const n = nodes.get(selectedKey);
  if (!n) return;
  const nodeId = n.id;
  const meta = $("detail-meta");
  const taskPanel = $("detail-task");
  const logCols = $("detail-cols");
  meta.innerHTML = "";
  const statusTxt = n.status === "running" ? "执行中" : n.status === "done" ? "完成" : n.status === "failed" ? "失败" : n.status === "waiting" ? "等待并发槽" : n.status === "delegating" ? "委派子任务中" : "待命";
  const stColor = n.status === "running" ? "var(--accent)" : n.status === "failed" ? "var(--red)" : n.status === "waiting" ? "var(--gold)" : n.status === "delegating" ? "var(--purple)" : "var(--fg)";
  // 用 innerHTML 渲染（含 <b> 标签），不用 el()（textContent 会把标签当字面文本）
  meta.innerHTML =
    `<span>状态 <b style="color:${stColor}">${statusTxt}</b></span>` +
    `<span>类型 <b>${n.type === "ceo" ? "CEO" : n.type === "round" ? "对话轮次" : n.type === "task" ? "任务" : n.type === "clone" ? "临时克隆" : "节点"}</b></span>`;
  // "节点管理"跳转按钮：仅真实节点/克隆显示（CEO/任务序号没有 registry 实体）
  const openBtn = $("detail-open-edit");
  if (n.type === "node" || n.type === "clone") {
    openBtn.classList.remove("hidden");
    openBtn.onclick = () => {
      showNodesView();
      selectNodeEdit(nodeId);
    };
  } else {
    openBtn.classList.add("hidden");
    openBtn.onclick = null;
  }

  // ---- 轮次节点：用户输入 + CEO 首次回复（2026-08-28 规则）----
  if (n.type === "round") {
    taskPanel.classList.remove("hidden");
    taskPanel.classList.add("fill");
    $("divider-dt").classList.add("hidden");
    logCols.classList.add("hidden");
    $("detail-task-title").textContent = "📋 对话内容";
    const u = (n.extra && n.extra.user_text) || "（无对话内容）";
    const c = (n.extra && n.extra.ceo_first) || "（CEO 尚未回复）";
    $("detail-task-goal").textContent =
      `【${n.label || "?"} · 用户输入】\n${u}\n\n【CEO 首次回复】\n${c}`;
    return;
  }

  // ---- 序号/任务节点：并发（count>1）→ 显示总体任务；单发（count=1）→
  // 显示该次委派自己的任务目标（2026-08-30 修复：此前用 overall!==goal
  // 判定并发，单发时两者文本必不同 → 非并发序号也误显示"并发总体任务"）----
  if (n.type === "task") {
    taskPanel.classList.remove("hidden");
    taskPanel.classList.add("fill");
    $("divider-dt").classList.add("hidden");
    logCols.classList.add("hidden");
    $("detail-task-title").textContent = "📋 任务目标";
    const ov = (n.extra && n.extra.overall_goal) || "";
    const g = (n.extra && n.extra.goal) || "";
    const cnt = (n.extra && n.extra.count) || 1;
    $("detail-task-goal").textContent =
      (cnt > 1)
        ? `【并发总体任务】\n${ov}`
        : `【任务目标】\n${g || "（无任务目标信息）"}`;
    return;
  }
  // 只有 ags 节点（node/clone）显示 任务书 + Brain/hermes 输出（2026-08-28 规则：
  // 其他节点（CEO/轮次/序号）用各自专属模板，不显示实时输出面板）
  if (n.type === "node" || n.type === "clone") {
    taskPanel.classList.remove("hidden");
    taskPanel.classList.remove("fill");
    $("divider-dt").classList.remove("hidden");
    $("detail-task-title").textContent = "📋 任务目标";
    $("detail-task-goal").textContent = (n.extra && n.extra.goal)
      ? `【任务目标】\n${n.extra.goal}` : "（无任务目标信息）";
    logCols.classList.remove("hidden");
  } else if (n.type === "ceo") {
    // CEO 专属模板：只显示当前任务标题
    taskPanel.classList.remove("hidden");
    taskPanel.classList.add("fill");
    $("divider-dt").classList.add("hidden");
    $("detail-task-title").textContent = "📋 当前任务";
    $("detail-task-goal").textContent = (n.extra && n.extra.task_title)
      ? `【当前任务标题】\n${n.extra.task_title}` : "（暂无任务）";
    logCols.classList.add("hidden");
  } else {
    // 其它类型（不应出现）：隐藏任务书与输出区
    taskPanel.classList.add("hidden");
    $("divider-dt").classList.add("hidden");
    logCols.classList.add("hidden");
  }

  // 节点/克隆：元数据 + 日志（原有逻辑）
  if ((n.type === "node" || n.type === "clone")
      && (!metaCache || metaCache.key !== n.key)) {
    metaCache = { key: n.key, nodeId };
    fetch(`/api/node/${encodeURIComponent(nodeId)}`)
      .then((r) => r.json()).then((j) => {
        if (!j.ok || selectedKey !== n.key) return;
        const nd = j.node;
        meta.innerHTML = "";
        meta.innerHTML =
          `<span>状态 <b style="color:var(--accent)">${statusTxt}</b></span>` +
          `<span>分数 <b>${nd.score}</b></span>` +
          `<span>成功率 <b>${(nd.success_rate * 100).toFixed(1)}%</b></span>` +
          `<span>任务数 <b>${nd.tasks_done}</b></span>` +
          `<span>代数 <b>${nd.gen}</b></span>` +
          `<span>基因 <b>${j.skills.length}</b></span>`;
      }).catch(() => {});
  }
  const logs = nodeLogs.get(nodeId) || { brain: [], hermes: [] };
  renderCol($("detail-brain"), logs.brain, "暂无输出");
  renderCol($("detail-hermes"), logs.hermes, "暂无输出（hermes 执行时实时显示）");
  // 2026-09-03 用户反馈：实时缓冲只有"本页打开期间"的事件——刷新后或点开
  // 刷新前已完成的旧节点，两列全是"暂无输出" → 回退拉磁盘持久化日志尾部
  if (n.type === "node" || n.type === "clone") loadHistLogs(nodeId, n.type);
}
function renderCol(dom, entries, emptyText) {
  dom.innerHTML = "";
  if (!entries.length) {
    dom.append(el("div", "dl-empty", emptyText));
    return;
  }
  const frag = document.createDocumentFragment();
  for (const e of entries) frag.append(buildLogLine(e));
  dom.append(frag);
  dom.scrollTop = dom.scrollHeight;
}
/* ---- 磁盘日志回退（2026-09-03 用户反馈：点开节点没有 brain/worker 内容）----
   实时缓冲 nodeLogs 只有"本页打开期间"到达的事件：刷新后、或点开刷新前
   已完成任务的旧节点，Brain / hermes 两列全是"暂无输出"。
   回退：/api/node/<id>/logs 返回磁盘持久化日志尾部——
     hermes 列 ← 工作区 .adgs_logs/{id}_live.log（最近一次任务完整 stdout，
                 每个任务截断重写，克隆 profile 删除后此文件仍保留）
     brain 列  ← profiles/{id}/logs/agent.log（node.brain 事件不落盘，
                 agent 日志是唯一落盘的决策侧记录；无则显示说明）
   缓存按 nodeId 去重；请求期间实时行到达时 pushLog 已追加，
   renderHistCol 检测到列已有内容就不再覆盖。 */
const histLogCache = new Map(); // queryId -> Promise<result|null>
function loadHistLogs(nodeId, instType) {
  // 克隆实例优先查克隆 profile 自己的日志：profile 目录并行结束后被删，
  // 但 live.log 留在工作区 .adgs_logs/；本页期间没见过程序名（如刷新后）
  // 则回退查本体 id（端点返回双 null 时 UI 显示说明）
  let queryId = nodeId;
  if (instType === "clone") {
    let lastProfile = null;
    for (const [profile, base] of cloneProfiles) if (base === nodeId) lastProfile = profile;
    if (lastProfile) queryId = lastProfile;
  }
  let p = histLogCache.get(queryId);
  if (!p) {
    p = fetch(`/api/node/${encodeURIComponent(queryId)}/logs?lines=400`)
      .then((r) => r.json())
      .then((j) => (j && j.ok) ? j : null)
      .catch(() => null)
      .then((j) => {  // 失败不缓存，下次选中重试
        if (j) histLogCache.set(queryId, j);
        else histLogCache.delete(queryId);
        return j;
      });
    histLogCache.set(queryId, p);
  }
  p.then((j) => {
    // 仍在看这个节点才渲染（切走则下次选中时缓存直接命中）
    const inst = selectedKey ? nodes.get(selectedKey) : null;
    if (!inst || inst.id !== nodeId) return;
    const logs = nodeLogs.get(nodeId) || { brain: [], hermes: [] };
    if (logs.brain.length === 0)
      renderHistCol($("detail-brain"), j && j.agentlog,
        "无历史记录：大脑决策（node.brain）事件不落盘；最近任务的 worker 输出见右侧 hermes 列",
        j && j.node);
    if (logs.hermes.length === 0)
      renderHistCol($("detail-hermes"), j && j.hermes,
        "磁盘无该节点 hermes 执行日志（从未执行或日志已清理）",
        j && j.node);
  });
}
function renderHistCol(dom, file, emptyText, tag) {
  // 列已有实时内容（请求期间 pushLog 追加过）→ 不覆盖
  if (dom.querySelector(":scope > :not(.dl-empty)")) return;
  dom.innerHTML = "";
  if (!file || !file.lines.length) {
    dom.append(el("div", "dl-empty", emptyText));
    return;
  }
  const head = el("div", "hist-head");
  head.textContent = `历史日志 · 磁盘尾部${tag ? "（" + tag + "）" : ""} · 最近一次任务${file.truncated ? "（截断）" : ""} · 更新于 ${fmtTime(file.mtime)}`;
  dom.append(head);
  const frag = document.createDocumentFragment();
  for (const ln of file.lines) {
    const line = el("div", "hist-line");
    const html = ansiToHtml(ln);
    if (html !== null) {
      const body = el("span", "log-body");
      body.innerHTML = html;
      line.append(body);
    } else {
      line.append(document.createTextNode(ln));
    }
    frag.append(line);
  }
  dom.append(frag);
  dom.scrollTop = dom.scrollHeight;
}

/* ================= 对话区 ================= */
function addChat(kind, payload) {
  const log = $("chat-log");
  const msg = el("div", `msg msg-${kind}`);
  const head = el("div", "msg-head", kind === "user" ? "你" : "CEO");
  msg.append(head);
  const bubble = el("div", "bubble");
  bubble.dataset.ts = Date.now();  // 去重时间窗用（2026-08-29）
  if (typeof payload === "string") {
    bubble.textContent = payload;
  } else if (payload.kind === "tool") {
    bubble.className += " tool";
    const tname = el("span", "tname", "⚡ " + payload.name);
    bubble.append(tname);
    const args = el("div", "targs");
    args.textContent = JSON.stringify(payload.args || {}, null, 1).slice(0, 400);
    bubble.append(args);
    if (payload.result) {
      const tres = el("div", "tres", "↳ " + String(payload.result).slice(0, 400));
      bubble.append(tres);
    }
  } else {
    bubble.className += ` ${payload.kind || ""}`;
    bubble.textContent = payload.text || "";
  }
  msg.append(bubble);
  log.append(msg);
  log.scrollTop = log.scrollHeight;
}
function addSys(text) {
  const log = $("chat-log");
  log.append(el("div", "msg-sys", text));
  log.scrollTop = log.scrollHeight;
  const sysLog = $("sys-log");
  const line = el("div", "sys-line");
  line.append(el("b", null, fmtTime(Date.now() / 1000)), document.createTextNode(" " + text));
  sysLog.prepend(line);
  while (sysLog.children.length > 60) sysLog.lastChild.remove();
}

/* ================= 图谱重置（每个新任务开始时清空，只保留 CEO 根） ================= */
function resetGraph() {
  nodes.clear();
  nodeLogs.clear();
  cloneProfiles.clear();
  lastDispatch.clear();
  taskNameToKey.clear();
  selectedKey = null;
  metaCache = null;
  roundSeq = 0;
  currentRound = null;
  currentCeo = null;
  roundActive = false;
  try { localStorage.removeItem(SNAP_KEY); } catch (e) {}
  $("detail").classList.add("hidden");
  ensureCeo();
  relayout();
}

/* ================= 历史会话（GPT 式侧栏列表） ================= */
let currentSession = null;  // 当前加载的历史会话名（null = 未加载）
let sessionsCache = [];     // 最新会话列表缓存
async function loadSessions() {
  try {
    const j = await (await fetch("/api/sessions")).json();
    if (!j.ok) return;
    sessionsCache = j.sessions || [];
    const list = $("hist-list");
    list.innerHTML = "";
    if (!sessionsCache.length) {
      list.append(el("div", "hist-empty", "暂无历史任务（任务结束后自动保存）"));
      return;
    }
    for (const s of sessionsCache) {
      const item = el("div", "hist-item" + (currentSession === s.name ? " active" : ""));
      item.dataset.name = s.name;
      item.append(el("div", "hi-preview", s.preview || "(无对话)"));
      const meta = `${s.saved_at || "?"} · ${s.msg_count} 条`;
      item.append(el("div", "hi-meta", meta));
      item.addEventListener("click", () => loadSession(s.name, item));
      list.append(item);
    }
  } catch (e) { /* 后端未就绪时静默 */ }
}
async function saveGraphToSession() {
  /* 把当前图谱拓扑存入当前任务会话文件（历史会话恢复图谱用）。
     后端旧版本无此 API 时静默失败，不影响主功能。 */
  try {
    const snap = [];
    for (const n of nodes.values()) snap.push(snapNode(n));
    await fetch("/api/session/graph", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ graph: snap }),
    });
  } catch (e) { /* 静默 */ }
}
/* 图谱随每次 CEO 回复保存（节流 1.5s）：中断后点进会话恢复的图谱就是
   最新轮次结构，CEO 继续分配任务时轮次接续（2026-08-29 用户规则）。
   graphSaveSuppressed：loadSession 后抑制——防止运行中任务的回复事件
   把"用户刚点开查看的其它会话图谱"写进任务自己的会话。 */
let _graphSaveTimer = null;
let graphSaveSuppressed = false;
function scheduleGraphSave() {
  if (graphSaveSuppressed || _graphSaveTimer) return;
  _graphSaveTimer = setTimeout(() => {
    _graphSaveTimer = null;
    saveGraphToSession();
  }, 1500);
}
function renderSessionMessages(messages) {
  const log = $("chat-log");
  log.innerHTML = "";
  for (const m of messages) {
    if (m.kind === "user") addChat("user", String(m.text).slice(0, 2000));
    else if (m.kind === "tool") {
      addChat("ceo", { kind: "tool", name: m.name, args: m.args, result: m.result });
    }
    // 2026-09-03：后端把 CEO 话术工具还原为 ask/report/finish kind，
    // 历史恢复与实时流同格式渲染（此前 else 兜底只出纯文本）
    else if (m.kind === "ask") addChat("ceo", { kind: "ask", text: "❓ CEO 提问：" + (m.text || "") });
    else if (m.kind === "report") addChat("ceo", { kind: "report", text: "📊 " + (m.text || "") + (m.reason ? "\n▶ 下一步：" + m.reason : "") });
    else if (m.kind === "finish") addChat("ceo", { kind: "finish", text: "🏁 " + (m.text || "") });
    else addChat("ceo", String(m.text || "").slice(0, 2000));
  }
}
async function loadSession(name, item) {
  try {
    const j = await (await fetch("/api/session/load", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    })).json();
    if (!j.ok) { addSys("切换会话失败：" + (j.error || "")); return; }
    currentSession = name;
    // 2026-08-30：图谱标题显示当前会话名（刷新后用户能立刻知道在看哪个会话）
    $("graph-title").textContent = name;
    graphSaveSuppressed = true;  // 查看历史会话期间不自动存图谱（防串写）
    for (const it of $("hist-list").querySelectorAll(".hist-item")) {
      it.classList.toggle("active", it.dataset.name === name);
    }
    // 恢复点语义：正在执行的节点不在保存点内，恢复后挂起（执行进程已不在）。
    // 2026-08-29 修正：仅当该会话任务【未在执行】时降级——后端返回 running
    // （talk 锁定该会话）说明节点真实执行中，降级会误显示"等待并发槽"（用户实测）
    const demoted = !j.running
      ? (j.graph || []).filter(n => n.status === "running").length : 0;
    renderSessionMessages(j.messages || []);
    // 恢复该任务的历史图谱（会话文件里存的拓扑）——之后点「任务总览」
    // 展示的就是这个任务的图谱（2026-08-28 用户需求）
    if (j.graph && j.graph.length) {
      overviewBackup = null;  // 已切到具体会话视图，总览备份作废
      nodes.clear();
      nodeLogs.clear();
      lastDispatch.clear();
      taskNameToKey.clear();
      applyGraphSnapshot({ nodes: j.graph }, { restoreMode: false });
      if (!j.running) {
        // 该会话无任务在执行：恢复的 running 是历史遗留 → 降级
        for (const n of nodes.values()) if (n.status === "running") n.status = "waiting";
      }
    } else {
      // 空图谱任务：图谱只显示 CEO 占位节点（2026-08-29 用户规则）
      overviewBackup = null;
      nodes.clear();
      nodeLogs.clear();
      lastDispatch.clear();
      taskNameToKey.clear();
      nodes.set("ceo", {
        key: "ceo", id: "ceo", type: "ceo", label: "CEO 决策中枢",
        parent: null, children: [], status: "idle", slot: 0, depth: 0,
        x: 0, y: 0, tx: 0, ty: 0, weight: 1, born: performance.now(), extra: {},
      });
      layoutMode = "tree";
      relayout();
      addSys("该任务暂无图谱，显示 CEO 占位");
    }
    // CEO 节点标题 = 当前打开的这个历史任务的标题（第一条用户消息，
    // 与左侧列表 preview 同源；无条件覆盖保存时的旧值，2026-08-28）
    const ceoN = nodes.get("ceo");
    const firstUser = (j.messages || []).find(m => m.kind === "user");
    if (ceoN && firstUser) {
      ceoN.extra = ceoN.extra || {};
      ceoN.extra.task_title = String(firstUser.text || "").slice(0, 300);
    }
    if (j.graph && j.graph.length) {
      relayout();
      addSys(`已恢复该任务的历史图谱（${j.graph.length} 个节点，可点任务总览查看）`);
      showMainView();  // 进入该历史任务的对话视图
    } else {
      showMainView();
    }
    // 2026-08-30：从后端事件流同步该会话的最新节点状态/缺失节点——
    // 文件图谱是"保存点"快照，任务进行中/刚结束时节点状态滞后
    //（running→waiting 降级、新节点缺失），用户刷新后看到的状态不同步
    await syncSessionState(name);
  } catch (e) { addSys("切换会话失败：" + e.message); }
}
/* 从后端事件流同步指定会话的节点状态（2026-08-30）：
   - node.status：直接更新节点状态（幂等，不触发保存副作用）
   - graph.task / graph.dispatch：图谱缺失的节点补挂（事件流比保存点新）
   全程抑制 scheduleGraphSave，防止同步结果写回会话文件 */
async function syncSessionState(name) {
  const prevSuppress = graphSaveSuppressed;
  graphSaveSuppressed = true;
  try {
    const st = await (await fetch("/api/state")).json();
    if (!st.ok || !st.events) return;
    let patched = 0;
    for (const ev of st.events) {
      if (ev.session !== name) continue;
      const d = ev.data || {};
      if (ev.type === "node.status") {
        const key = (d.task && nodes.has(`${d.node}@${d.task}`))
          ? `${d.node}@${d.task}` : (lastDispatch.get(d.node) || d.node);
        const n = nodes.get(key);
        if (n) {
          n.status = d.status === "running" ? "running"
            : d.status === "done" ? "done"
            : d.status === "waiting" ? "waiting"
            : d.status === "delegating" ? "delegating" : "failed";
          patched++;
        }
      } else if (ev.type === "graph.dispatch" && d.node) {
        // 实例节点 key = node@task（与 handleEvent 1509 行同构造）；
        // 事件本身无 id 字段，直接用 key 判断缺失
        const instKey = `${d.node}@${d.task || "task"}`;
        if (!nodes.has(instKey)) {
          handleEvent(ev);  // 事件流中比保存点新的实例节点：补挂
          patched++;
        }
      } else if ((ev.type === "graph.task")
                 && d && d.id && !nodes.has(d.id)) {
        handleEvent(ev);  // 序号节点缺失：补挂
        patched++;
      }
    }
    if (patched) addSys(`已同步后端状态（${patched} 处节点更新）`);
  } catch (e) { /* 后端暂不可达时静默 */ }
  finally { graphSaveSuppressed = prevSuppress; }
}
async function newSession() {
  try {
    const j = await (await fetch("/api/session/new", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: "{}",
    })).json();
    if (!j.ok) { addSys("新建对话失败：" + (j.error || "")); return; }
    restoreFromOverview();  // 新对话：退出任务总览
    currentSession = null;
    $("graph-title").textContent = "新任务图谱";  // 2026-08-30：标题复位
    $("chat-log").innerHTML = "";
    for (const it of $("hist-list").querySelectorAll(".hist-item")) it.classList.remove("active");
  } catch (e) { addSys("新建对话失败：" + e.message); }
}

/* ================= SSE 事件分发 ================= */
let isReplaying = false;  // 历史重放标志：重放只补气泡/日志，不创建轮次结构（快照已有）
// 轮次节点记录 CEO 首次回复（ask/report/finish 中第一个到达的）
function recordCeoFirstReply(text) {
  if (!currentRound || !roundActive) return;
  const rn = nodes.get(currentRound);
  if (rn && rn.extra && !rn.extra.ceo_first) {
    rn.extra.ceo_first = String(text || "").slice(0, 1500);
  }
}
function handleEvent(ev) {
  const d = ev.data || {};
  // 2026-08-30 会话隔离（用户实测）：事件带 session（服务端当前任务会话）。
  // 前端正在查看历史会话（currentSession 非空）且与事件会话不一致时忽略——
  // 否则其它任务的消息/图谱节点会串台到当前查看的会话（"为什么我在这个
  // 对话，却显示/恢复别的任务"）。system 消息保留（信息性提示）。
  if (ev.session && currentSession && ev.session !== currentSession) {
    if (ev.type === "system") addSys(d.msg || "");
    return;
  }
  switch (ev.type) {
    case "chat.user": {
      // 任务总览是浏览模式：新消息到达 → 先退出总览恢复原图谱，再正常处理
      restoreFromOverview();
      // 图谱保存恢复条件（2026-08-30 修复）：仅在【当前会话=null（新对话/
      // 欢迎页）】时恢复——正在查看历史会话（currentSession 非空）时保持抑制，
      // 否则该页面的图谱会被写回被查看的会话（多标签/测试页污染，用户实测
      // 132 节点会话被覆盖成 1 节点）。恢复保存后用户发消息/新对话正常流转。
      if (!currentSession) graphSaveSuppressed = false;
      // 用户对话：气泡 + 【用户每输入一次 = 轮次 +1】（2026-08-28 规则：
      // 不再等 CEO 分配任务；轮次节点记录这次用户说了什么 + CEO 首次回复）
      //
      // 2026-08-29 修复：去重【只作用于气泡显示】——sendText 发送时前端已
      // 本地加气泡，chat.user 事件到达时内容相同且刚加过 → 不重复加气泡；
      // 但【轮次逻辑不受影响】——此前去重直接 break 导致每次输入轮次都不增。
      // 后端每个输入只 emit 一次 chat.user，replayEvents 也跳过它。
      const txt = String(d.text).slice(0, 2000);
      const log = $("chat-log");
      const users = log.querySelectorAll(".msg-user");
      const last = users.length ? users[users.length - 1].querySelector(".bubble") : null;
      if (!(last && last.dataset.ts
            && (Date.now() - parseInt(last.dataset.ts, 10)) < 3000
            && last.textContent.slice(0, 2000) === txt)) {
        addChat("user", txt);
      }
      // CEO 节点记录当前任务标题（点开显示）——
      // 2026-08-29 修复顺序：必须先 ensureCeo() 再设置标题（此前在
      // ensureCeo 之前取 nodes.get("ceo")，首次输入图谱为空 → null →
      // 标题永远设置不上 → CEO 点开一直"（暂无任务）"）
      ensureCeo();
      const ceoN = nodes.get("ceo");
      if (ceoN) {
        ceoN.extra = ceoN.extra || {};
        ceoN.extra.task_title = String(d.text || "").slice(0, 300);
      }
      roundActive = true;
      roundSeq++;
      currentRound = `round-${roundSeq}`;
      upsertNode(currentRound, {
        type: "round", id: currentRound, label: `轮 ${roundSeq}`,
        parent: "ceo", typeLabel: `第${roundSeq}轮`,
        extra: { user_text: String(d.text || "").slice(0, 2000), ceo_first: "" },
      });
      break;
    }
    case "chat.ceo_ask":
      setBusy(false);  // 提问后任务暂停，等待用户回答
      scheduleGraphSave();
      recordCeoFirstReply("❓ " + d.question);
      addChat("ceo", { kind: "ask", text: "❓ CEO 提问：" + d.question });
      pushLog("ceo", "brain", { ts: ev.ts, cls: "action",
        text: "[提问] " + String(d.question).slice(0, 200) });
      break;
    case "chat.ceo_tool":
      scheduleGraphSave();  // 每次 CEO 决策后图谱随 history 一起落盘
      addChat("ceo", { kind: "tool", name: d.name, args: d.args, result: d.result });
      pushLog("ceo", "brain", { ts: ev.ts, cls: "action",
        text: `[工具] ${d.name} ${JSON.stringify(d.args || {}).slice(0, 160)}` });
      break;
    case "chat.ceo_report":
      scheduleGraphSave();
      recordCeoFirstReply("📊 " + (d.summary || ""));
      addChat("ceo", { kind: "report", text: "📊 " + (d.summary || "") + (d.next_plan ? "\n▶ 下一步：" + d.next_plan : "") });
      pushLog("ceo", "brain", { ts: ev.ts, cls: "action",
        text: "[汇报] " + String(d.summary || "").slice(0, 300) });
      break;
    case "chat.ceo_finish":
      setBusy(false);  // 任务完成，解除执行中状态
      roundActive = false;  // 本轮结束：下次用户输入创建新轮次
      scheduleGraphSave();
      recordCeoFirstReply("🏁 " + (d.summary || ""));
      addChat("ceo", { kind: "finish", text: "🏁 " + (d.summary || "") });
      pushLog("ceo", "brain", { ts: ev.ts, cls: "eval-pass",
        text: "[完成] " + String(d.summary || "").slice(0, 300) });
      break;
    case "chat.ceo_end":
      setBusy(false);  // 决策循环结束（上限/出错/异常）
      roundActive = false;
      scheduleGraphSave();
      addSys(d.status === "error" ? "CEO 出错结束：" + (d.error || "") : "CEO 本轮决策结束");
      break;
    case "system":
      // 后端任务线程结束的兜底信号
      if (d.msg && String(d.msg).includes("任务结束")) {
        setBusy(false);
        refreshStats();  // 队列可能已自动续跑，刷新状态栏
        loadSessions();  // 任务已自动存档，刷新历史会话列表
        saveGraphToSession();  // 把当前图谱拓扑存入该任务会话（历史恢复用）
      }
      addSys(d.msg || "");
      break;
    case "graph.task": {
      // 总览浏览模式：忽略结构事件（chat.user 到达时已先退出总览）
      if (overviewBackup && layoutMode === "tasks") break;
      // 轮次已由 chat.user 创建（用户每输入一次 = 新轮次，2026-08-28 规则）；
      // 此处仅兜底：快照恢复等场景 currentRound 为空时创建
      // 节点委派（parent=节点）不触发新轮次——它是当前轮次的子任务树
      const isCeoTask = !d.parent || d.parent === "ceo";
      if (isCeoTask && !roundActive && !currentRound) {
        roundActive = true;
        roundSeq++;
        currentRound = `round-${roundSeq}`;
        ensureCeo();
        upsertNode(currentRound, {
          type: "round", id: currentRound, label: `轮 ${roundSeq}`,
          parent: "ceo", typeLabel: `第${roundSeq}轮`,
          extra: { user_text: "", ceo_first: "" },
        });
      }
      // 序号节点：label = 该轮第几次委派（后端 seq，每轮从 1 开始）
      // 同一 stage 多个子任务共享序号 → 从序号分裂多个节点
      const seq = d.seq || String(d.id || "").replace(/^t/, "") || "?";
      // 挂载点：CEO 拆分 → 当前轮次 round-N；节点委派 → 委派节点的"实例 key"
      // （后端 parent = 节点id@上层task_id，与实例 key 一致）→ 树枝 节点→数字→子节点。
      // 旧格式事件 parent 是裸节点 id（图中无此 key，会悬空无树枝）→
      // 用 lastDispatch 找该节点最新实例兜底，找不到再原样挂
      const parentKey = isCeoTask
        ? (currentRound || "ceo")
        : (d.parent && nodes.has(d.parent)) ? d.parent
        : (d.parent
            ? (lastDispatch.get(String(d.parent).split("@")[0]) || d.parent)
            : (currentRound || "ceo"));
      upsertNode(d.id, { type: "task", id: d.id, label: String(seq),
        parent: parentKey, typeLabel: `第${seq}次委派`,
        extra: { overall_goal: d.overall || "", goal: d.goal || "",
                 seq, count: d.count || 1 } });
      taskNameToKey.set(d.name, d.id);
      break;
    }
    case "graph.dispatch": {
      if (overviewBackup && layoutMode === "tasks") break;  // 总览浏览模式
      const pid = d.node; // 本体节点 id
      const instKey = `${pid}@${d.task || "task"}`;
      const isClone = d.mode === "clone";
      // 父节点解析：
      // 1. d.task 是已存在的序号节点（tN/nN，graph.task 先发）→ 挂序号下
      // 2. 否则 d.task 是子任务名 → taskNameToKey 映射兜底（旧事件）
      // 3. 都没有 → 挂 d.parent（委派节点/CEO）
      // 递归委派（parent=节点）也必须挂序号节点下，形成 CEO→序号→节点→序号→节点 树
      let parentKey;
      if (d.task && nodes.has(d.task)) {
        parentKey = d.task;
      } else if (d.task && taskNameToKey.get(d.task)) {
        parentKey = taskNameToKey.get(d.task);
      } else {
        parentKey = d.parent || "ceo";
      }
      // node 节点目标：优先用 dispatch 事件自带的 goal（并发时各自子任务目标，
      // 2026-08-28：此前共享 task_id 导致所有 ags 点开都是最后一个子任务目标）；
      // 事件没带（旧版/单发独立调用）→ 从对应序号节点继承
      let tGoal = d.goal || "", tOverall = "";
      if (!tGoal) {
        const tn = (d.task && nodes.get(d.task))
          || (d.task && taskNameToKey.get(d.task) && nodes.get(taskNameToKey.get(d.task)));
        if (tn && tn.extra) { tGoal = tn.extra.goal || ""; tOverall = tn.extra.overall_goal || ""; }
      } else {
        const tn = (d.task && nodes.get(d.task))
          || (d.task && taskNameToKey.get(d.task) && nodes.get(taskNameToKey.get(d.task)));
        if (tn && tn.extra) tOverall = tn.extra.overall_goal || "";
      }
      upsertNode(instKey, {
        id: pid, type: isClone ? "clone" : "node",
        label: isClone ? pid + "·克隆" : pid,
        parent: parentKey,
        status: "idle",
        typeLabel: isClone ? "临时克隆" : "节点",
        extra: { goal: tGoal, overall_goal: tOverall },
      });
      lastDispatch.set(pid, instKey);
      addSys(`派发 ${pid}${isClone ? "（克隆）" : ""} → ${d.task || "任务"}`);
      break;
    }
    case "graph.clone":
      cloneProfiles.set(d.profile, d.node);
      break;
    case "node.status": {
      if (overviewBackup && layoutMode === "tasks") break;  // 总览浏览模式
      scheduleGraphSave();  // 节点状态变化 → 图谱落盘
      // 优先按 task_id 精确映射到实例：同节点多实例并发（本体+克隆）时，
      // lastDispatch 只指向最后派发的实例，其它实例的状态事件会互相覆盖
      // （一个实例反复 running/done 闪烁 = 用户看到的"很多 worker 在闪"）。
      // 新事件带 task=tN 可直接定位；旧事件/委派场景无 task 时回退 lastDispatch。
      const key = (d.task && nodes.has(`${d.node}@${d.task}`))
        ? `${d.node}@${d.task}`
        : (lastDispatch.get(d.node) || d.node);
      const n = nodes.get(key);
      if (n) {
        n.status = d.status === "running" ? "running"
          : d.status === "done" ? "done"
          : d.status === "waiting" ? "waiting"
          : d.status === "delegating" ? "delegating"
          : "failed";
        if (d.task && !n.label.startsWith(d.node)) n.label = d.node;
      }
      if (d.status === "running") {
        setBusy(true);
        addSys(`${d.node} 开始执行`);
      } else if (d.status === "waiting") {
        addSys(`${d.node} 等待并发槽（vLLM 并发已满）`);
      } else if (d.status === "delegating") {
        addSys(`${d.node} 拆分任务，委派子节点执行`);
      } else if (d.status === "failed") {
        addSys(`${d.node} 失败（score ${d.score ?? "-"}${d.reworks ? "，返工 " + d.reworks + " 次" : ""}）`);
      } else if (d.status === "done") {
        addSys(`${d.node} 完成（score ${d.score ?? "-"}${d.reworks ? "，返工 " + d.reworks + " 次" : ""}）`);
      }
      // 其他状态（idle 等）不显示系统消息，避免"派发即失败"假象
      break;
    }
    case "node.brain":
      pushLog(d.node, "brain", { ts: ev.ts, cls: "action",
        text: `[决策] ${d.action}${d.detail ? "：" + d.detail : ""}` });
      break;
    case "node.eval":
      pushLog(d.node, "brain", { ts: ev.ts, cls: d.passed ? "eval-pass" : "eval-fail",
        text: `[验收] ${d.passed ? "通过" : "未通过"} · score ${d.score}${d.reworks ? " · 返工" + d.reworks : ""}${d.detail ? " · " + d.detail : ""}` });
      break;
    case "hermes.start":
      pushLog(profileNode(d.profile), "hermes", { ts: ev.ts, cls: "hermes-mark",
        text: `▶ hermes ${d.phase || "execute"} 开始${d.brief ? "：" + d.brief : ""}` });
      break;
    case "hermes.end":
      pushLog(profileNode(d.profile), "hermes", { ts: ev.ts, cls: d.exit_code === 0 ? "hermes-mark" : "herr-err",
        text: `■ hermes ${d.phase || "execute"} 结束 exit=${d.exit_code}${d.error ? " · " + d.error : ""}` });
      // 移除该节点的等待心跳行（如有）
      {
        const nd = profileNode(d.profile);
        if (nodeLogs.has(nd)) {
          const logs = nodeLogs.get(nd);
          const hbIdx = logs.hermes.findIndex(e => e.cls === "hermes-hb");
          if (hbIdx >= 0) {
            logs.hermes.splice(hbIdx, 1);
            if (selectedKey) {
              const inst = nodes.get(selectedKey);
              if (inst && inst.id === nd) {
                const old = $("detail-hermes").querySelector(".herr-hb-line");
                if (old) old.remove();
              }
            }
          }
        }
      }
      break;
    case "hermes.line": {
      const nd = profileNode(d.profile);
      const text = String(d.line).replace(/\n+$/, "");
      if (!text) break;
      // 有新输出 → 移除该节点的等待心跳行（静默结束）
      if (nodeLogs.has(nd)) {
        const logs = nodeLogs.get(nd);
        const hbIdx = logs.hermes.findIndex(e => e.cls === "hermes-hb");
        if (hbIdx >= 0) {
          logs.hermes.splice(hbIdx, 1);
          if (selectedKey) {
            const inst = nodes.get(selectedKey);
            if (inst && inst.id === nd) {
              const old = $("detail-hermes").querySelector(".herr-hb-line");
              if (old) old.remove();
            }
          }
        }
      }
      pushLog(nd, "hermes", { ts: ev.ts, cls: d.err ? "herr-err" : "herr", text });
      break;
    }
    case "hermes.agentlog": {
      // hermes-agent 完整日志（API call / 工具 / 压缩 / 错误），实时 tail agent.log
      const nd = profileNode(d.profile);
      const line = String(d.line || "");
      if (!line) break;
      // 去掉日志前缀时间戳（buildLogLine 自己加），分类着色
      const m = line.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(\w+)\s+(.*)$/);
      const level = m ? m[2] : "INFO";
      const text = m ? m[3] : line;
      const cls = level === "ERROR" || level === "CRITICAL" ? "herr-err"
        : level === "WARNING" ? "herr-warn" : "herr-agent";
      pushLog(nd, "hermes", { ts: ev.ts, cls, text });
      break;
    }
    case "hermes.heartbeat": {
      // 静默期心跳：LLM 调用中无输出时定期推送，显示"等待响应"状态行
      // （不刷屏：同一节点的心跳行原地更新）
      const nd = profileNode(d.profile);
      if (!nodeLogs.has(nd)) nodeLogs.set(nd, { brain: [], hermes: [] });
      const logs = nodeLogs.get(nd);
      // 找是否已有心跳行（cls=hermes-hb），有则更新，无则追加
      const hbIdx = logs.hermes.findIndex(e => e.cls === "hermes-hb");
      const entry = { ts: ev.ts, cls: "hermes-hb",
        text: `⏳ 等待 LLM 响应（已 ${d.idle || 0}s，本轮 ${d.elapsed || 0}s）…` };
      if (hbIdx >= 0) logs.hermes[hbIdx] = entry;
      else logs.hermes.push(entry);
      // 选中该节点时同步更新 DOM
      if (selectedKey) {
        const inst = nodes.get(selectedKey);
        if (inst && inst.id === nd) {
          const dom = $("detail-hermes");
          const old = dom.querySelector(".herr-hb-line");
          if (old) old.remove();
          const empty = dom.querySelector(".dl-empty");
          if (empty) empty.remove();
          const line = buildLogLine(entry);
          line.classList.add("herr-hb-line");
          dom.append(line);
          dom.scrollTop = dom.scrollHeight;
        }
      }
      break;
    }
    case "evolve.round": {
      // 淘汰质量指标（方案 B）：淘汰均分 < 种群均分 = 弱者优先淘汰生效
      let gap = "";
      if (d.elim_avg_score != null && d.pop_avg_score != null) {
        const ok = d.score_gap < 0 ? "弱者优先 ✓" : "质量持平";
        gap = ` | 淘汰均分 ${d.elim_avg_score} / 种群均分 ${d.pop_avg_score}（${ok}）`;
      }
      // 力量四：动态承载量（种群变强 → K 扩张）
      const k = d.effective_capacity != null ? `，承载量 ${d.effective_capacity}` : "";
      // 新节点初始分（60 百分位动态）
      const nbScores = (d.births || []).filter(b => b.score != null).map(b => b.score);
      const nb = nbScores.length ? `，新节点初始分 ${nbScores[0]}` : "";
      addSys(`进化：繁衍 ${d.births?.length ?? 0} 淘汰 ${d.eliminations?.length ?? 0}，种群 ${d.n_before}→${d.n_after}${k}${nb}（压力 ${d.pressure}）${gap}`);
      refreshStats();  // 进化后重新拉取状态（避免用空数据覆盖）
      break;
    }
    default:
      break;
  }
}

/* ================= 面板拖拽调整大小 ================= */
const PANEL_KEY = "adgs_panel_sizes";
function makeResizable(handle, target, axis, onResize, flip) {
  // axis: 'h' = 水平拖（改宽度）| 'v' = 垂直拖（改高度）
  // flip: true = 拖动方向反转（固定右/下缘的面板，如右侧详情）
  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    handle.classList.add("dragging");
    const startPos = axis === "h" ? e.clientX : e.clientY;
    const baseSize = axis === "h" ? target.offsetWidth : target.offsetHeight;
    const parentEl = target.parentElement;
    const parentSize = axis === "h" ? parentEl.clientWidth : parentEl.clientHeight;
    let rafId = null;
    const mm = (ev) => {
      let delta = (axis === "h" ? ev.clientX : ev.clientY) - startPos;
      if (flip) delta = -delta;  // 固定右缘：分隔条左移 = 变宽
      const size = Math.max(140, Math.min(parentSize - 140, baseSize + delta));
      if (rafId) return;
      rafId = requestAnimationFrame(() => {
        rafId = null;
        if (axis === "h") target.style.width = size + "px";
        else target.style.height = size + "px";
        if (onResize) onResize(size);
        savePanelSizes();
      });
    };
    const mu = () => {
      handle.classList.remove("dragging");
      window.removeEventListener("mousemove", mm);
      window.removeEventListener("mouseup", mu);
    };
    window.addEventListener("mousemove", mm);
    window.addEventListener("mouseup", mu);
  });
}
function savePanelSizes() {
  try {
    localStorage.setItem(PANEL_KEY, JSON.stringify({
      sidebar: $("sidebar").style.width || null,
      graph: $("graph-wrap").style.height || null,
      detail: $("detail").style.width || null,
      sidebarSec: $("side-sec-fixed").style.height || null,
      sysState: $("sidebar").querySelectorAll(".side-sec")[0].style.height || null,
      legend: $("sidebar").querySelectorAll(".side-sec")[1].style.height || null,
      detailCols: $("detail-cols").children[0].style.width || null,
      nodeEdit: $("node-edit").style.width || null,
      nodeDesc: $("node-edit").querySelector(".edit-sec:not(.grow)").style.height || null,
      taskH: $("detail-task").style.height || null,
    }));
  } catch (e) { /* 存储禁用 */ }
}
function loadPanelSizes() {
  try {
    const s = JSON.parse(localStorage.getItem(PANEL_KEY) || "{}");
    if (s.sidebar) $("sidebar").style.width = s.sidebar;
    if (s.graph) $("graph-wrap").style.height = s.graph;
    if (s.detail) $("detail").style.width = s.detail;
    if (s.sidebarSec) $("side-sec-fixed").style.height = s.sidebarSec;
    if (s.sysState) $("sidebar").querySelectorAll(".side-sec")[0].style.height = s.sysState;
    if (s.legend) $("sidebar").querySelectorAll(".side-sec")[1].style.height = s.legend;
    if (s.detailCols) $("detail-cols").children[0].style.width = s.detailCols;
    if (s.nodeEdit) $("node-edit").style.width = s.nodeEdit;
    if (s.nodeDesc) $("node-edit").querySelector(".edit-sec:not(.grow)").style.height = s.nodeDesc;
    if (s.taskH) $("detail-task").style.height = s.taskH;
  } catch (e) { /* 存储禁用 */ }
}
function initResizers() {
  makeResizable($("divider-side"), $("sidebar"), "h", () => resizeCanvas());
  makeResizable($("divider-gc"), $("graph-wrap"), "v", () => resizeCanvas());
  makeResizable($("divider-detail"), $("detail"), "h", null, true);
  makeResizable($("divider-side-sec"), $("side-sec-fixed"), "v", null, true);
  makeResizable($("divider-ss"), $("sidebar").querySelectorAll(".side-sec")[0], "v");
  makeResizable($("divider-lg"), $("sidebar").querySelectorAll(".side-sec")[1], "v");
  makeResizable($("divider-cols"), $("detail-cols").children[0], "h");
  makeResizable($("divider-dt"), $("detail-task"), "v");
  makeResizable($("divider-ne"), $("node-edit"), "h");
  makeResizable($("divider-ne2"), $("node-edit").querySelector(".edit-sec:not(.grow)"), "v");
}

/* ================= 状态栏 / 忙碌 ================= */
function updateStats(st) {
  const s = st || {};
  if (s.population) {
    $("stat-pop").innerHTML = `种群 <b class="acc">${s.population.active ?? "-"}</b>/<b>${s.population.capacity ?? "-"}</b>`;
  }
  if (s.models) {
    $("stat-ceo").textContent = s.models.ceo ?? "-";
    $("stat-brain").textContent = s.models.brain ?? "-";
    $("stat-worker").textContent = s.models.worker ?? "-";
  }
  if (s.workspace) {
    $("stat-ws").textContent = s.workspace;
    $("stat-ws").title = s.workspace;
  }
  if (typeof s.queue_len === "number") {
    $("stat-queue").innerHTML = `队列 <b${s.queue_len > 0 ? ' style="color:var(--gold)"' : ""}>${s.queue_len}</b>`;
  }
}
async function refreshStats() {
  try {
    const st = await (await fetch("/api/state")).json();
    if (st.ok) updateStats(st);
  } catch (e) { /* 后端暂不可达时静默 */ }
}
/* 聊天区是否已包含指定文本（重连补放去重用，2026-08-30） */
function chatLogHas(text) {
  if (!text) return true;
  const t = String(text).trim();
  if (!t) return true;
  const log = document.querySelector("#chat-log");
  return log ? log.textContent.includes(t.slice(0, 40)) : false;
}
let busy = false;
function setBusy(b) {
  busy = b;
  $("graph-status").className = b ? "busy" : "idle";
  $("graph-status").textContent = b ? "● 任务执行中" : "空闲";
}

/* ================= 初始化 ================= */
function replayEvents(events) {
  // 历史重放：快照已恢复拓扑，这里只补气泡/日志/系统消息。
  // 结构事件（graph.task/graph.dispatch/node.status 等）若重放会与快照
  // 重复挂载/覆盖，导致轮次树叠加错乱（2026-08-24 实测：round-3 后叠 CEO）。
  // 实时事件（SSE）不受影响，照常走 handleEvent 完整逻辑。
  isReplaying = true;
  try {
    for (const ev of events) {
      const t = ev.type;
      if (t === "chat.user" || t === "graph.task" || t === "graph.dispatch"
          || t === "graph.clone" || t === "node.status" || t === "node.brain"
          || t === "node.eval" || t === "chat.ceo_tool" || t === "chat.ceo_report"
          || t === "chat.ceo_finish" || t === "chat.ceo_end" || t === "chat.ceo_ask") {
        // 结构/决策类：跳过（快照已有拓扑；nodeLogs 由 live 事件重建）
        continue;
      }
      handleEvent({ type: ev.type, ts: ev.ts, data: ev.data });
    }
  } finally {
    isReplaying = false;
  }
}
async function init() {
  applyTheme();  // 应用保存的主题（暗色/亮色），并让 Canvas 调色板随之生效
  // GPT 式首页：没有活跃会话（首次进入/未进入过对话）→ 欢迎界面；
  // 之前进入过会话（刷新保持主视图）→ 直接主视图
  let inMain = false;
  try { inMain = localStorage.getItem(VIEW_KEY) === "main"; } catch (e) {}
  if (inMain) showMainView(); else showWelcome();
  loadSessions();  // 加载历史会话列表
  loadPanelSizes();  // 恢复面板拖拽尺寸
  initResizers();  // 面板拖拽调整大小
  ensureCeo();
  resizeCanvas();
  requestAnimationFrame(function loop(t) { draw(); requestAnimationFrame(loop); });
  // 初始状态：先拉 /api/state（拿 boot_ts 判断后端是否重启），再恢复快照。
  // 后端重启 = CEO 上下文/事件历史清空 → 前端图谱同步清空（保持一致性）
  let bootOk = false;
  try {
    const st = await (await fetch("/api/state")).json();
    if (st.ok) {
      bootOk = true;
      const savedBoot = localStorage.getItem("adgs_boot_ts");
      if (st.boot_ts && savedBoot && String(savedBoot) !== String(st.boot_ts)) {
        // 后端重启过：清空旧图谱快照，图谱与上下文同步从空开始；
        // 内存图谱中执行中的节点降级（进程已随重启消失，2026-08-29）
        backendRestarted = true;
        try { localStorage.removeItem(SNAP_KEY); } catch (e) {}
        try { localStorage.removeItem(VIEW_KEY); } catch (e) {}  // 重启 = 新会话 → 欢迎页
        let demoted = 0;
        for (const n of nodes.values()) if (n.status === "running") { n.status = "waiting"; demoted++; }
        addSys(`检测到后端重启，图谱已清空（与新会话同步）· ${demoted} 个执行中的节点已挂起`);
        showWelcome();  // 2026-08-29：刚启动服务端进入网页应为欢迎页，不是残留图谱
      }
      if (st.boot_ts) localStorage.setItem("adgs_boot_ts", String(st.boot_ts));
      restoreSnapshot();
      // 2026-08-30：刷新后自动恢复"刚才在看的会话"——快照已带 session，
      // 直接 loadSession 把聊天记录/标题/图谱文件版一起恢复，不再只剩
      // 一个不知道属于谁的无主图谱；新对话（session=null）保留快照
      const snapSess = (() => {
        try {
          const s = JSON.parse(localStorage.getItem(SNAP_KEY) || "{}");
          return s.session || null;
        } catch (e) { return null; }
      })();
      if (snapSess) {
        await loadSession(snapSess, null);
        addSys(`已恢复会话 ${snapSess} 的图谱与对话`);
      } else if (nodes.size) {
        $("graph-title").textContent = "当前任务图谱（新对话）";
      }
      updateStats(st);
      setBusy(st.busy);
      replayEvents(st.events || []);
      if (st.events?.length) setBusy(st.busy);
    }
  } catch (e) {
    if (!bootOk) {
      // 后端暂不可达：仍恢复快照（离线展示上次图谱）
      restoreSnapshot();
      addSys("后端连接失败：" + e.message);
    }
  }
  // SSE
  const es = new EventSource("/api/events");
  es.onmessage = (msg) => {
    try {
      const ev = JSON.parse(msg.data);
      handleEvent(ev);
    } catch (e) { /* 忽略坏帧 */ }
  };
  es.onopen = () => {
    $("graph-status").textContent = "连接正常";
    checkBackendRestart();  // SSE 重连/页面开着重启服务端时检测（2026-08-29）
    resyncAfterReconnect();  // P1-10（2026-08-30）：重连后无条件校准
  };
  es.onerror = () => { setBusy(false); $("graph-status").textContent = "连接中断，重连中…"; };
}
/* 重连校准（2026-08-30 P1-10 修复）：SSE 断线期间后端事件丢失，图谱/对话
   与后端失步（僵尸"执行中"节点、缺轮次/节点）。重连成功后增量补回：
   - node.status 直接改节点状态（幂等，不触发 handleEvent 的保存副作用）
   - graph.task / graph.dispatch 若图谱缺失则补挂（handleEvent，保存已抑制）
   全程抑制 scheduleGraphSave，防止校准图谱写回被查看的会话（多页面污染）。 */
async function resyncAfterReconnect() {
  const prevSuppress = graphSaveSuppressed;
  graphSaveSuppressed = true;
  try {
    const st = await (await fetch("/api/state")).json();
    if (!st.ok) return;
    updateStats(st);
    setBusy(st.busy);
    let patched = 0;
    for (const ev of st.events || []) {
      // 会话隔离：校准只处理当前查看会话的事件（2026-08-30）
      if (ev.session && currentSession && ev.session !== currentSession) continue;
      const d = ev.data || {};
      if (ev.type === "node.status") {
        const key = (d.task && nodes.has(`${d.node}@${d.task}`))
          ? `${d.node}@${d.task}` : (lastDispatch.get(d.node) || d.node);
        const n = nodes.get(key);
        if (n) {
          n.status = d.status === "running" ? "running"
            : d.status === "done" ? "done"
            : d.status === "waiting" ? "waiting"
            : d.status === "delegating" ? "delegating" : "failed";
          patched++;
        }
      } else if (ev.type === "graph.dispatch" && d.node) {
        const instKey = `${d.node}@${d.task || "task"}`;
        if (!nodes.has(instKey)) {  // 断线期间新实例节点：补挂
          handleEvent(ev);
          patched++;
        }
      } else if (ev.type === "graph.task" && d && d.id && !nodes.has(d.id)) {
        handleEvent(ev);  // 断线期间新序号节点：补挂（保存已抑制）
        patched++;
      } else if ((ev.type === "chat.ceo_finish" || ev.type === "chat.ceo_ask")
                 && d && !chatLogHas(d.summary || d.question || "")) {
        // 2026-08-30：断线期间丢失的 CEO 回复/提问补放（去重：聊天区已有
        // 相同内容则不重复；resync 全程抑制保存，不会污染会话）
        handleEvent(ev);
        patched++;
      }
    }
    if (patched) addSys(`连接已恢复，校准 ${patched} 处图谱状态`);
    else addSys("连接已恢复");
  } catch (e) { /* 后端暂不可达时静默 */ }
  finally { graphSaveSuppressed = prevSuppress; }
}
/* 服务端重启检测（SSE 重连时调用）：boot_ts 变化 → 清快照 + 降级执行中节点 */
async function checkBackendRestart() {
  try {
    const st = await (await fetch("/api/state")).json();
    if (!st.ok || !st.boot_ts) return;
    const savedBoot = localStorage.getItem("adgs_boot_ts");
    if (savedBoot && String(savedBoot) !== String(st.boot_ts)) {
      backendRestarted = true;
      try { localStorage.removeItem(SNAP_KEY); } catch (e) {}
      try { localStorage.removeItem(VIEW_KEY); } catch (e) {}
      let demoted = 0;
      for (const n of nodes.values()) if (n.status === "running") { n.status = "waiting"; demoted++; }
      if (st.boot_ts) localStorage.setItem("adgs_boot_ts", String(st.boot_ts));
      updateStats(st);
      setBusy(st.busy);
      replayEvents(st.events || []);
      showWelcome();  // 服务端重启 = 新会话 → 欢迎页（2026-08-29）
      addSys(`检测到服务端重启：${demoted} 个执行中的节点已挂起（已回到欢迎页）`);
    }
  } catch (e) { /* 后端暂不可达，忽略 */ }
}

/* ================= 事件绑定 ================= */
$("chat-send").addEventListener("click", sendChat);
$("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
async function sendChat() {
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  await sendText(text);
}
async function sendText(text) {
  // 2026-08-30：发消息 = 用户主动续接/新对话，恢复图谱自动保存
  // （此前查看历史会话时抑制保持，续接发消息后图谱节点不再落盘，
  // 再次刷新图谱退回旧版）
  graphSaveSuppressed = false;
  // 系统指令（/workspace /evolve /help ...）：本地执行，不走 CEO
  if (text.startsWith("/")) {
    addChat("user", text);
    const resp = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const j = await resp.json().catch(() => ({}));
    if (!j.ok) {
      addSys("指令失败：" + (j.error || "未知错误"));
    } else if (j.msg) {
      addSys(j.msg);
    }
    if (text.startsWith("/workspace")) refreshStats();  // 工作区变化刷新侧栏
    return;
  }

  addChat("user", text);
  if (busy) {
    addSys("任务执行中，消息将自动排队…");
  } else {
    setBusy(true);
    addSys("任务已提交");
  }
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const j = await resp.json().catch(() => ({}));
  if (!j.ok) {
    if (!busy) setBusy(false);
    addSys("提交失败：" + (j.error || "未知错误"));
  } else if (j.queued) {
    addSys(`已加入队列（第 ${j.position} 位）`);
    refreshStats();  // 队列长度变化刷新状态栏
  }
}
$("chat-clear").addEventListener("click", () => { $("chat-log").innerHTML = ""; });
// 欢迎界面：输入任务 → 开新任务对话并进入主视图（GPT 式逻辑）
async function wlSend() {
  const input = $("wl-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  // 2026-08-30 修复（用户实测）：欢迎页输入 = 开启新对话——必须先 newSession()
  // （清空 currentSession + 后端 _current_sess 置 None + 重置 CEO 上下文），
  // 否则任务会发到上次查看的历史会话里、CEO 复用旧上下文
  await newSession();
  showMainView();
  await sendText(text);
}
$("wl-send").addEventListener("click", wlSend);
$("wl-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); wlSend(); }
});
$("detail-close").addEventListener("click", () => selectNode(null));
$("btn-collapse").addEventListener("click", () => {
  const sb = $("sidebar");
  sb.classList.toggle("collapsed");
  // 折叠后按钮变 »（可展开提示），展开后变 «（2026-08-30）
  $("btn-collapse").textContent = sb.classList.contains("collapsed") ? "»" : "«";
});

/* ================= 节点管理视图 ================= */
const BIRTH_LABEL = { initial: "初始", recombine: "重组", whiteboard: "白板" };
let nodesView = { list: [], arch: [], current: null };

function showNodesView() {
  selectNode(null);  // 2026-08-30：切换视图前关闭详情面板，否则 fixed 面板
  // 会遮挡 nodes-head 的返回按钮（用户实测点不到）
  $("welcome-wrap").classList.add("hidden");
  $("graph-wrap").classList.add("hidden");
  $("chat-wrap").classList.add("hidden");
  $("divider-gc").classList.add("hidden");
  $("nodes-wrap").classList.remove("hidden");
  loadNodes();
}
function showGraphView() {
  selectNode(null);  // 2026-08-30：回图谱前关闭详情面板（避免残留遮挡）
  $("welcome-wrap").classList.add("hidden");
  $("nodes-wrap").classList.add("hidden");
  $("graph-wrap").classList.remove("hidden");
  $("chat-wrap").classList.remove("hidden");
  $("divider-gc").classList.remove("hidden");
  resizeCanvas();
}
/* GPT 式首页：欢迎界面 ↔ 主视图（图谱+对话）。
   只有【没有进入活跃会话】时才显示欢迎页；进入过会话（主视图）后
   刷新保持主视图（GPT 逻辑：刷新回到上次的对话，不退回欢迎页） */
const VIEW_KEY = "adgs_view_state";
function showWelcome() {
  try { localStorage.removeItem(VIEW_KEY); } catch (e) {}
  $("welcome-wrap").classList.remove("hidden");
  $("graph-wrap").classList.add("hidden");
  $("chat-wrap").classList.add("hidden");
  $("divider-gc").classList.add("hidden");
  $("nodes-wrap").classList.add("hidden");
}
function showMainView() {
  try { localStorage.setItem(VIEW_KEY, "main"); } catch (e) {}
  $("welcome-wrap").classList.add("hidden");
  $("graph-wrap").classList.remove("hidden");
  $("chat-wrap").classList.remove("hidden");
  $("divider-gc").classList.remove("hidden");
  resizeCanvas();
}
function nodeMsg(text) {
  const old = $("node-edit").querySelector(".edit-msg");
  if (old) old.remove();
  const m = el("div", "edit-msg", text);
  $("node-edit").append(m);
  setTimeout(() => m.remove(), 4000);
}

let nodesSearch = "";  // 节点管理搜索词（id/描述/技能 模糊匹配）
async function loadNodes() {
  try {
    const j = await (await fetch("/api/nodes")).json();
    if (!j.ok) throw new Error(j.error || "未知错误");
    nodesView.list = j.nodes || [];
    nodesView.arch = j.archived || [];
    renderNodes();
  } catch (e) {
    const list = $("nodes-list");
    list.innerHTML = "";
    list.append(el("div", "dl-empty", "加载失败：" + e.message));
  }
}
function renderNodes() {
  const kw = nodesSearch.trim().toLowerCase();
  const hit = (n) => !kw
    || (n.id || "").toLowerCase().includes(kw)
    || (n.describe || "").toLowerCase().includes(kw)
    || (n.skills || []).some(s => s.toLowerCase().includes(kw));
  const active = nodesView.list.filter(hit);
  const arch = (nodesView.arch || []).filter(hit);
  $("nodes-count").textContent = `${active.length} 活动 / ${arch.length} 已淘汰`;
  const list = $("nodes-list");
  list.innerHTML = "";
  if (!active.length && !arch.length) {
    list.append(el("div", "dl-empty", kw ? "没有匹配的节点" : "暂无节点"));
    return;
  }
  for (const n of active) list.append(buildNodeCard(n));
  // 已淘汰折叠区
  if (arch.length) {
    const sec = el("div", "arch-section");
    const tog = el("div", "arch-toggle");
    tog.append(el("span", "tri", "▸"),
               el("span", null, `已淘汰节点 ${arch.length}`),
               el("span", null, "点击展开查看"));
    const grid = el("div", "arch-grid hidden");
    for (const n of arch) grid.append(buildNodeCard(n, true));
    sec.append(tog, grid);
    tog.addEventListener("click", () => {
      const open = sec.classList.toggle("open");
      grid.classList.toggle("hidden", !open);
    });
    list.append(sec);
  }
}

function buildNodeCard(n, isArch = false) {
  const card = el("div", "node-card" + (isArch ? " arch" : "")
                  + (nodesView.current === n.id ? " sel" : ""));
  card.dataset.id = n.id;
  const head = el("div", "nc-head");
  const idBox = el("span", "nc-id");
  idBox.append(el("span", "nc-dot " + (isArch ? "idle" : (n.status || "idle"))),
               document.createTextNode(n.id));
  head.append(idBox,
    isArch
      ? el("span", "nc-badge b-arch", "已淘汰")
      : el("span", "nc-badge b-" + (n.birth_type || "initial"),
           BIRTH_LABEL[n.birth_type] || n.birth_type || "初始"));
  card.append(head);
  card.append(el("div", "nc-desc", n.describe || "（无描述）"));
  // 分数条
  const scoreRow = el("div", "nc-score-row");
  const bar = el("div", "nc-score-bar");
  const fill = el("div", "nc-score-fill");
  fill.style.width = (Math.max(0, Math.min(100, Number(n.score) || 0))) + "%";
  bar.append(fill);
  scoreRow.append(bar, el("span", "nc-score-txt",
                          (Number(n.score) || 0).toFixed(1)));
  card.append(scoreRow);
  // meta 行
  const meta = el("div", "nc-meta");
  meta.append(
    el("span", n.success_rate >= 0.5 ? "up" : "down",
       `${(n.success_rate * 100).toFixed(0)}% 成功`),
    el("span", null, `任务 ${n.tasks_done}`),
    el("span", null, `代 ${n.gen}`)
  );
  card.append(meta);
  if (isArch) {
    const note = el("div", "nc-arch-note");
    note.append(el("span", null, `已淘汰 · 存活至第 ${n.gen} 代`));
    const revive = el("span", "nc-revive", "↺ 复活");
    revive.title = "复活该节点（profile 与技能基因完整保留，回到活动种群）";
    revive.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`确认复活节点 ${n.id}？\n描述：${(n.describe || "").slice(0, 60)}`)) return;
      try {
        const r = await (await fetch(`/api/node/${encodeURIComponent(n.id)}/revive`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
        })).json();
        if (!r.ok) { nodeMsg("复活失败：" + (r.error || "")); return; }
        await loadNodes();
      } catch (err) { nodeMsg("复活失败：" + err.message); }
    });
    note.append(revive);
    card.append(note);
  } else {
    // 技能 chips（最多 4 个）
    const sk = (n.skills || []).slice(0, 4);
    if (sk.length) {
      const skBox = el("div", "nc-skills");
      for (const s of sk) {
        skBox.append(el("span", "nc-skill-chip",
                        s.length > 16 ? s.slice(0, 16) + "…" : s));
      }
      if ((n.skills || []).length > 4) {
        skBox.append(el("span", "nc-skill-chip", `+${n.skills.length - 4}`));
      }
      card.append(skBox);
    }
  }
  card.addEventListener("click", () => selectNodeEdit(n.id));
  return card;
}

let editCache = null;  // {id, node, skills}
async function selectNodeEdit(id) {
  nodesView.current = id;
  for (const c of $("nodes-list").children) c.classList.toggle("sel", c.dataset.id === id);
  const ed = $("node-edit");
  ed.classList.remove("hidden");
  $("divider-ne").classList.remove("hidden");
  $("node-edit-title").textContent = id;
  $("node-edit-meta").innerHTML = "";
  $("node-edit-describe").value = "加载中…";
  $("node-edit-skills").innerHTML = "";
  try {
    const j = await (await fetch(`/api/node/${encodeURIComponent(id)}`)).json();
    if (!j.ok) throw new Error(j.error || "未知错误");
    editCache = { id, node: j.node, skills: j.skills || [] };
    renderNodeEdit();
  } catch (e) {
    $("node-edit-describe").value = "加载失败：" + e.message;
  }
}

function renderNodeEdit() {
  const { node: n, skills } = editCache;
  const meta = $("node-edit-meta");
  meta.innerHTML = "";
  meta.append(
    el("span", null, `分数 <b>${n.score}</b>`),
    el("span", null, `成功率 <b>${(n.success_rate * 100).toFixed(1)}%</b>`),
    el("span", null, `任务数 <b>${n.tasks_done}</b>`),
    el("span", null, `代数 <b>${n.gen}</b>`),
    el("span", null, `出生 <b>${BIRTH_LABEL[n.birth_type] || n.birth_type}</b>`),
    n.parents && n.parents.length
      ? el("span", null, `亲代 <b>${n.parents.join(",")}</b>`) : null
  );
  $("node-edit-describe").value = n.describe || "";
  // 技能基因
  const skBox = $("node-edit-skills");
  skBox.innerHTML = "";
  if (!skills.length) skBox.append(el("div", "dl-empty", "（无自定义技能）"));
  for (const sk of skills) {
    const item = el("div", "skill-item");
    const head = el("div", "sk-head");
    head.append(el("span", "sk-name", sk.name));
    const del = el("span", "sk-del", "✕ 删除");
    del.title = `删除技能 ${sk.name}`;
    del.addEventListener("click", () => removeSkill(sk.name));
    head.append(del);
    item.append(head);
    if (sk.desc) item.append(el("div", "sk-desc", sk.desc));
    if (sk.content) {
      const det = el("details");
      det.append(el("summary", null, "查看 SKILL.md 内容"));
      det.append(el("pre", null, sk.content));
      item.append(det);
    }
    skBox.append(item);
  }
  // 安装技能：其它节点的技能（技能名 @ 来源节点）
  const sel = $("node-edit-skill-src");
  sel.innerHTML = '<option value="">选择其它节点技能…</option>';
  const opts = [];
  for (const n2 of nodesView.list) {
    if (n2.id === editCache.id) continue;
    for (const s of n2.skills || []) opts.push({ name: s, src: n2.id });
  }
  opts.sort((a, b) => a.name.localeCompare(b.name));
  for (const o of opts) {
    const opt = el("option", null, `${o.name} @ ${o.src}`);
    opt.value = `${o.src}::${o.name}`;
    sel.append(opt);
  }
}

async function saveDescribe() {
  const text = $("node-edit-describe").value.trim();
  if (!editCache || !text) return;
  const r = await fetch(`/api/node/${encodeURIComponent(editCache.id)}/describe`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ describe: text }),
  });
  const j = await r.json().catch(() => ({}));
  nodeMsg(j.ok ? "描述已保存" : "保存失败：" + (j.error || ""));
  if (j.ok) { loadNodes(); editCache.node.describe = text; }
}
async function removeSkill(name) {
  if (!confirm(`确认删除节点 ${editCache.id} 的技能「${name}」？`)) return;
  const r = await fetch(`/api/node/${encodeURIComponent(editCache.id)}/skill/remove`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const j = await r.json().catch(() => ({}));
  if (j.ok) { nodeMsg(`技能「${name}」已删除`); selectNodeEdit(editCache.id); loadNodes(); }
  else nodeMsg("删除失败：" + (j.error || ""));
}
async function installSkill() {
  const v = $("node-edit-skill-src").value;
  if (!v) { nodeMsg("请先选择要安装的技能"); return; }
  const [from_node, name] = v.split("::");
  const r = await fetch(`/api/node/${encodeURIComponent(editCache.id)}/skill/install`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ from_node, name }),
  });
  const j = await r.json().catch(() => ({}));
  if (j.ok) { nodeMsg(`技能「${name}」已安装`); selectNodeEdit(editCache.id); loadNodes(); }
  else nodeMsg("安装失败：" + (j.error || ""));
}
async function removeNode() {
  if (!editCache) return;
  if (!confirm(`确认移除节点 ${editCache.id}？\n将删除 hermes profile 并从种群移除，此操作不可恢复！`)) return;
  const r = await fetch(`/api/node/${encodeURIComponent(editCache.id)}/remove`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  const j = await r.json().catch(() => ({}));
  if (j.ok) {
    $("node-edit").classList.add("hidden");
    nodesView.current = null;
    editCache = null;
    nodeMsg(`节点已移除`);
    loadNodes();
  } else nodeMsg("移除失败：" + (j.error || ""));
}

$("hist-new").addEventListener("click", newSession);
$("btn-theme").addEventListener("click", () => {
  // 切换 白天/夜晚 模式（CSS 变量 + Canvas 调色板 + 持久化）
  const next = document.body.dataset.theme === "light" ? "dark" : "light";
  try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* 存储禁用 */ }
  applyTheme();
});
$("btn-layout").addEventListener("click", () => {
  // 2026-08-30 用户需求：整理布局三态循环 ——
  //   第1下：树状向下分支（每轮对话一棵子树，最早默认样式）
  //   第2下：圆形放射向四周（CEO 为中心，当前样式）
  //   第3下：恢复整理前的原版样子（坐标+视角+模式）
  //   第4下起循环。第一次点击前保存"原版"快照。
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
    layoutMode = "tree";
    relayout();        // 多根树：round-N 为根水平均布，子节点向下展开
    fitRadialView();   // 缩放适配整图可见
    saveSnapshot();
    addSys("已整理布局：树状向下分支（每轮对话一棵子树）");
  } else if (layoutCycle.step === 2) {
    layoutMode = "radial";
    radialLayout();
    fitRadialView();
    saveSnapshot();
    addSys("已整理布局：以 CEO 为中心向四周发散，节点围着各自上层节点");
  } else {
    // 恢复原版：坐标（当前+目标）与视角（zoom/平移）原样返回
    const saved = layoutCycle.saved;
    for (const n of nodes.values()) {
      const s = saved.find(v => v.key === n.key);
      if (s) { n.x = s.x; n.y = s.y; n.tx = s.x; n.ty = s.y; }
    }
    layoutMode = layoutCycle.mode;
    cam.px = layoutCycle.cam.px; cam.py = layoutCycle.cam.py;
    cam.zoom = layoutCycle.cam.zoom;
    saveSnapshot();
    addSys("已恢复整理前的布局");
  }
});
$("btn-tasks").addEventListener("click", () => {
  addSys("正在加载所有历史任务的图谱…");
  taskOverviewAll();
});
$("nodes-search").addEventListener("input", (e) => {
  nodesSearch = e.target.value;
  renderNodes();
});
$("btn-nodes").addEventListener("click", showNodesView);
$("nodes-back").addEventListener("click", showGraphView);
$("nodes-refresh").addEventListener("click", loadNodes);
$("node-edit-close").addEventListener("click", () => {
  $("node-edit").classList.add("hidden");
  $("divider-ne").classList.add("hidden");
});
$("node-edit-save").addEventListener("click", saveDescribe);
$("node-edit-describe").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); saveDescribe(); }
});
$("node-edit-skill-add").addEventListener("click", installSkill);
$("node-edit-remove").addEventListener("click", removeNode);
$("btn-evolve").addEventListener("click", async () => {
  addSys("触发进化…");
  try {
    const j = await (await fetch("/api/evolve", { method: "POST" })).json();
    addSys(j.ok ? j.msg : "进化失败：" + j.error);
  } catch (e) { addSys("进化请求失败"); }
});

init();
