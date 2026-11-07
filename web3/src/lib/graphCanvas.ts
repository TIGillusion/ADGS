import { edgeSignal } from "@/lib/graphActivity";
import { GRAPH_STATUS, graphStatus } from "@/lib/executionState";
import { ceoPhaseLabel } from "@/lib/ceo";

// ============================================================

// 图谱 Canvas 引擎 — 布局 + 渲染 + 交互（纯 TS，无 React 依赖）

//

// 视觉：深空底色，生物荧光。CEO 金色六边形 → 轮次胶囊 → 任务序号环

//      → 执行节点实例（状态色 + 运行脉冲 + 边上粒子流）。

// 布局：行星模型（楔形星系密排，见 solveGalaxy）——全树以 CEO 为极点做

//     极坐标布局，每个节点占一个从极点张开的楔形扇区（极角区间），兄弟

//     子树楔形互不重叠 ⇒ 行星与连线结构性零交叠；子节点以各自最小切距

//     贴着父圆（小子树不再被推到大子树轨道），楔宽由子树内容在真实半径

//     处的角需求决定（越外越密），顶层放不下才整体外推（二分）。

// 动效：节点弹性生长（easeOutBack 过冲）+ 角度平滑跟随；连线端点按各形状

//     （旋转矩形/胶囊/圆/六边形）的支撑距离精确贴合节点表面（乘动画缩放，

//     永不悬空），新边自父端向子端生长出现，hover/选中高亮相连边。

// 交互：滚轮缩放（指向光标）、拖拽平移、双击适应、hover 命中、点击选中。

// ============================================================



import type { GraphNode } from "@/types";

import { splitInstKey, CEO_KEY, type GraphState, type A2aEdge } from "@/lib/graph";

import { fmtDuration } from "@/lib/format";



// ---------------- 视觉常量 ----------------



const C = {

  ceo: "#f5c96b",

  ceoGlow: "rgba(245, 201, 107, 0.55)",

  round: "#8be9ff",

  task: "#a5edff",

  waiting: "#fbbf24",

  running: "#2fe0bd",

  delegating: "#9a72f5",

  done: "#3ecf7f",

  failed: "#f4535f",

  edgeActive: "rgba(95, 242, 214, 0.85)",

  sel: "#5ff2d6",

} as const;



// 主题相关画布色：从 CSS 变量读取，随 :root[data-theme] 切换（浅色主题下

// 深空底色节点/文字/边同步变浅，功能色不变）。按当前主题缓存，避免每帧

// getComputedStyle。

interface ThemeColors {
  ceo: string;
  waiting: string;
  running: string;
  delegating: string;
  done: string;
  failed: string;
  selection: string;
  evaluating: string;

  nodeBg: string;

  nodeBgActive: string;

  edge: string;

  edgeFaint: string;

  idle: string;

  text: string;

  textDim: string;

  /** 轮次/任务标签文字色(浅色主题下用深色变体,避免亮青配白底看不清) */

  round: string;

  task: string;

}

let themeCacheKey = "";

let themeCache: ThemeColors | null = null;

function cssVar(name: string, fallback: string): string {

  try {

    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();

    return v || fallback;

  } catch {

    return fallback;

  }

}

function themeColors(): ThemeColors {

  const key =

    typeof document !== "undefined" ? document.documentElement.dataset.theme ?? "" : "";

  if (themeCache && themeCacheKey === key) return themeCache;

  themeCacheKey = key;

  themeCache = {
    evaluating: cssVar("--canvas-evaluating", "#8be9ff"),
    ceo: cssVar("--canvas-ceo", C.ceo),
    waiting: cssVar("--canvas-waiting", C.waiting),
    running: cssVar("--canvas-running", C.running),
    delegating: cssVar("--canvas-delegating", C.delegating),
    done: cssVar("--canvas-done", C.done),
    failed: cssVar("--canvas-failed", C.failed),
    selection: cssVar("--canvas-selection", C.sel),

    nodeBg: cssVar("--canvas-node-bg", "rgba(10, 18, 32, 0.92)"),

    nodeBgActive: cssVar("--canvas-node-bg-active", "rgba(13, 26, 40, 0.95)"),

    edge: cssVar("--canvas-edge", "rgba(120, 155, 210, 0.30)"),

    edgeFaint: cssVar("--canvas-edge-faint", "rgba(120, 155, 210, 0.06)"),

    idle: cssVar("--canvas-idle", "#6d7f9f"),

    text: cssVar("--canvas-text", "#eaf1fd"),

    textDim: cssVar("--canvas-text-dim", "#93a4c3"),

    round: cssVar("--canvas-round", C.round),

    task: cssVar("--canvas-task", C.task),

  };

  return themeCache;

}



const SIZE = {

  ceo: { w: 108, h: 64 },

  round: { w: 64, h: 38 },

  task: { w: 40, h: 40 },

  node: { w: 150, h: 46 },

} as const;



const PAD = 70;



// 最小缩放：原 0.25 → 1/20 = 0.0125（可把整张超大图谱缩得很小再看）

const MIN_ZOOM = 0.0125;



// ---------------- 布局 ----------------



interface LaidNode {

  n: GraphNode;

  x: number; // 目标中心

  y: number;

  w: number;

  h: number;

  /** 节点随枝向的旋转角（CEO = 0；树状布局 = 0） */

  rot: number;

  depth: number;

  ghost: boolean; // 待删除（淡出中）

}



interface LayoutResult {

  nodes: LaidNode[];

  width: number;

  height: number;

  maxDepth: number;

}



/** Geometry is independent of status/log activity; retain current node metadata. */

export class GraphLayoutCache {

  private graph: GraphState | null = null;

  private signature = "";

  private mode: GraphLayoutMode | null = null;

  private result: LayoutResult | null = null;

  computations = 0;

  get(graph: GraphState, mode: GraphLayoutMode): LayoutResult {

    if (this.graph === graph && this.mode === mode && this.result) return this.result;

    const signature = JSON.stringify(graph.nodes.map((n) => [n.key, n.parent, n.type]));

    if (!this.result || signature !== this.signature || mode !== this.mode) {

      this.result = layoutTree(graph, mode);

      this.computations++;

    } else {

      const current = new Map(graph.nodes.map((n) => [n.key, n]));

      this.result = { ...this.result, nodes: this.result.nodes.map((l) => ({ ...l, n: current.get(l.n.key)! })) };

    }

    this.graph = graph;

    this.signature = signature;

    this.mode = mode;

    return this.result;

  }

}



function nodeSize(n: GraphNode): { w: number; h: number } {

  if (n.type === "ceo") return SIZE.ceo;

  if (n.type === "round") return SIZE.round;

  if (n.type === "task") return SIZE.task;

  return SIZE.node;

}



/** 旋转矩形的轴对齐包围盒半宽/半高（bbox 与 fit 用） */

function aabbHalf(w: number, h: number, rot: number): { ex: number; ey: number } {

  const c = Math.abs(Math.cos(rot));

  const s = Math.abs(Math.sin(rot));

  return { ex: (c * w + s * h) / 2, ey: (s * w + c * h) / 2 };

}



// ---------------- 行星布局：楔形星系密排（最小范围 + 层级 + 零交叠） ----------------

//

// 旧模型（等距同心圆 + 圆盘包裹）的两处空间浪费：① 所有子树被推到同一半径

// D（按最大子树算），小行星被迫飘到大行星轨道；② 细长子树（如"任务→实例"链）

// 被包成大圆盘参与祖父级排布，一圈只能放 3~4 个枝。楔形模型：

//  · 全树以 CEO 为极点做极坐标布局，每个节点占一个从极点张开的【楔形扇区】

//    （极角区间 [a0,a1]），其子树全部节点都落在这条楔形内；

//  · 兄弟子树的楔形互不重叠（中间至少隔一段弧距）：楔形是从极点出发的凸区域

//    且两两相离 ⇒ 任意两个互不为祖先的节点必不重叠、父子连线段不穿越别枝

//    （结构性零交叠，不依赖数值精度）；

//  · 节点矩形沿枝向【旋转对齐】（文字左半圆翻转保持可读）：角阴影与角度无关，

//    = 2·atan2(h/2+pad, r−w/2−pad)——150×46 的实例在 r=332 处只占 ~10°，

//    而旧的外接圆模型占 29.5°；

//  · 父节点的子分两种排布：

//      - 全叶子子（含单子链）→【径向堆叠】成一列（沿枝向首尾相接，楔宽 =

//        最宽一节的阴影），4 个实例从占 4×29° 变成占 1×10°；

//      - 含子树的子 → 楔形内扇形展开，每个子以最小步长挂在父圆上

//        （step = circ(父) + gap0 + 沿枝前伸(子)，小子树贴着父节点）；

//  · 顶层 Σ楔宽 > 2π 时整体外推（二分最小拉伸系数 s ≥ 1），而不是硬撑角度；

//    部分占用时子块以楔形中线为中心居中（树冠朝正下方，阅读顺序不变）。



/** 行星布局常量（导出供测试/调试复算约束） */

export const RADIAL_CONSTS = {

  /** 节点矩形几何外扩裕量（px）：间距与阴影均按外扩后的形状计算 */

  shapePad: 6,

  /** 相邻形状之间保持的最小净距（px） */

  gap0: 8,

  /** 相邻兄弟子树楔形之间的弧距（px，在子极半径处度量） */

  gapPad: 14,

} as const;



/** 星系布局调试信息（测试/目视验证用） */

export interface RadialDebug {

  laid: { key: string; type: string; parent: string | null; x: number; y: number; w: number; h: number; rot: number }[];

  /** 每个节点的楔形扇区：极角区间 [a0,a1] 与极半径 r（极点 = CEO） */

  wedges: { key: string; a0: number; a1: number; r: number; x: number; y: number }[];

}



/**

 * 星系布局核心（纯函数，layoutTree 与 radialDebug 共用）：

 * 自顶向下给每个节点分配楔形扇区并落位。可达性剪枝防环（回边忽略），

 * 孤岛组件平铺在主树下方兜底。

 */

function solveGalaxy(

  byKey: Map<string, GraphNode>,

  kids: Map<string, GraphNode[]>,

  ceo: GraphNode,

): {

  pos: Map<string, { x: number; y: number }>;

  wedges: Map<string, { a0: number; a1: number; r: number }>;

  rots: Map<string, number>;

} {

  const K = RADIAL_CONSTS;



  // 从 CEO 出发的可达树（环数据兜底：回边直接忽略，剩余孤岛另行平铺）

  const kidsSafe = new Map<string, GraphNode[]>();

  {

    const seen = new Set<string>([ceo.key]);

    const stack = [ceo.key];

    while (stack.length) {

      const k = stack.pop()!;

      for (const c of kids.get(k) ?? []) {

        if (seen.has(c.key)) continue;

        seen.add(c.key);

        const arr = kidsSafe.get(k) ?? [];

        arr.push(c);

        kidsSafe.set(k, arr);

        stack.push(c.key);

      }

    }

  }

  const kidsOf = (id: string): GraphNode[] => kidsSafe.get(id) ?? [];



  /** 节点沿枝方向的前伸半长（矩形旋转对齐后长边贴枝）+ 裕量 */

  const alongOf = (id: string): number => nodeSize(byKey.get(id)!).w / 2 + K.shapePad;

  /** 节点垂直于枝方向的半宽 + 裕量 */

  const perpOf = (id: string): number => nodeSize(byKey.get(id)!).h / 2 + K.shapePad;

  /** 节点外接圆半径（与方向无关的最大支撑半径，用于扇形父子间距的保守估计） */

  const circOf = (id: string): number => {

    const s = nodeSize(byKey.get(id)!);

    return Math.hypot(s.w, s.h) / 2 + K.shapePad;

  };

  /**

   * 旋转对齐节点在极半径 r 处的角阴影（全角）：

   * 近端角偏离枝轴 = atan2(perp, r − along) ⇒ 阴影 = 2·atan2。

   * 与枝的朝向无关（旋转把几何锁进了本地坐标系），是纯函数。

   */

  const bodyShadow = (id: string, r: number): number =>

    2 * Math.atan2(perpOf(id), Math.max(1, r - alongOf(id)));

  /** 统一步长：父侧任意方向保守支撑（外接圆），子侧沿枝前伸（含裕量）。

   *  单子链与叶子堆叠列共用于同一公式；方向对齐 ⇒ 子侧支撑 = along 恰当，

   *  父侧（尤其 rot=0 的 CEO）用外接圆保证任意子方向都安全。 */

  const stepOf = (pid: string, cid: string): number => circOf(pid) + K.gap0 + alongOf(cid);

  /** 相邻兄弟子树楔形之间的弧距换算成角（在子极半径处度量，半径保底 60px） */

  const padAngle = (r: number): number => K.gapPad / Math.max(60, r);



  /** 子树 c 根在极半径 r 处所需的楔形角宽 = max(自身阴影, 子排布需求) */

  function needW(id: string, r: number): number {

    const cs = kidsOf(id);

    const own = bodyShadow(id, r);

    if (!cs.length) return own;

    // 单子链 / 全叶子兄弟列 → 径向堆叠成一列：楔宽 = max(自身, 最宽一节)

    if (cs.length === 1 || cs.every((c) => kidsOf(c.key).length === 0)) {

      let cursor = r + circOf(id) + K.gap0;

      let need = own;

      for (const c of cs) {

        cursor += alongOf(c.key);

        need = Math.max(need, needW(c.key, cursor));

        cursor += alongOf(c.key) + K.gap0;

      }

      return need;

    }

    // 含 ≥2 个带子树的子 → 扇形展开：Σ子楔宽 + 弧距

    let sum = 0;

    let prevR = 0;

    for (let i = 0; i < cs.length; i++) {

      const rc = r + stepOf(id, cs[i].key);

      if (i > 0) sum += padAngle(Math.min(prevR, rc));

      sum += needW(cs[i].key, rc);

      prevR = rc;

    }

    return Math.max(own, sum);

  }



  const pos = new Map<string, { x: number; y: number }>();

  const wedges = new Map<string, { a0: number; a1: number; r: number }>();

  const rots = new Map<string, number>();



  function place(id: string, r: number, a0: number, a1: number): void {

    const mid = (a0 + a1) / 2;

    pos.set(id, { x: Math.cos(mid) * r, y: Math.sin(mid) * r });

    wedges.set(id, { a0, a1, r });

    rots.set(id, mid);

    const cs = kidsOf(id);

    if (!cs.length) return;

    // 单子链 / 全叶子兄弟列 → 径向堆叠：全部放在楔形中线方向上（首尾相接）

    if (cs.length === 1 || cs.every((c) => kidsOf(c.key).length === 0)) {

      let cursor = r + circOf(id) + K.gap0;

      for (const c of cs) {

        cursor += alongOf(c.key);

        const w = needW(c.key, cursor);

        place(c.key, cursor, mid - w / 2, mid + w / 2);

        cursor += alongOf(c.key) + K.gap0;

      }

      return;

    }

    const avail = a1 - a0;

    /** 拉伸系数 s 下全部子的楔宽 + 弧距之和（s 越大子极半径越远，需求和单调减） */

    const totalAt = (s: number): number => {

      let t = 0;

      let prevR = 0;

      for (let i = 0; i < cs.length; i++) {

        const rc = r + s * stepOf(id, cs[i].key);

        if (i > 0) t += padAngle(Math.min(prevR, rc));

        t += needW(cs[i].key, rc);

        prevR = rc;

      }

      return t;

    };

    // 正常情况下 avail = needW(本节点) ≥ s=1 的需求（楔宽就是这么算出来的），

    // 二分只在顶层（2π 预算）触发；比较加 1e-9 容差，避免边界上 1 ULP 的

    // 浮点抖动触发无意义的拉伸（实测会让单链每级多漂 ~0.1px）

    let s = 1;

    if (totalAt(1) > avail + 1e-9) {

      let lo = 1;

      let hi = 1.5;

      while (totalAt(hi) > avail && hi < 64) hi *= 1.5;

      for (let it = 0; it < 40 && hi - lo > 1e-6; it++) {

        const m = (lo + hi) / 2;

        if (totalAt(m) <= avail) hi = m;

        else lo = m;

      }

      s = hi;

    }

    // 子块在楔形内居中：部分占用时树冠朝楔形中线收拢，不留单侧大空角

    const used = totalAt(s);

    let cursor = mid - used / 2;

    for (let i = 0; i < cs.length; i++) {

      const rc = r + s * stepOf(id, cs[i].key);

      const w = needW(cs[i].key, rc);

      place(cs[i].key, rc, cursor, cursor + w);

      cursor += w + padAngle(rc);

    }

  }



  // 根：整圆 2π 可用，子块以正下方（canvas y 向下 → 角 π/2）为中心

  place(ceo.key, 0, Math.PI / 2 - Math.PI, Math.PI / 2 + Math.PI);

  rots.set(ceo.key, 0); // CEO 水平放置，不随枝向旋转



  // 孤岛兜底（环/断链损坏数据）：主树下方一行平铺，间距保证矩形不重叠

  let maxY = 0;

  for (const p of pos.values()) if (p.y > maxY) maxY = p.y;

  let ix = 0;

  for (const n of byKey.values()) {

    if (pos.has(n.key)) continue;

    pos.set(n.key, { x: ix * 180, y: maxY + 160 });

    wedges.set(n.key, { a0: 0, a1: 0, r: maxY + 160 });

    rots.set(n.key, 0);

    ix += 1;

  }



  return { pos, wedges, rots };

}



/** 调试/验证：星系布局全量信息（位置 + 各节点楔形扇区） */

export function radialDebug(state: GraphState): RadialDebug {

  const byKey = new Map(state.nodes.map((n) => [n.key, n]));

  const kids = new Map<string, GraphNode[]>();

  for (const n of state.nodes) {

    if (n.parent === null) continue;

    const p = byKey.has(n.parent) ? n.parent : CEO_KEY;

    const arr = kids.get(p) ?? [];

    arr.push(n);

    kids.set(p, arr);

  }

  const ceo = state.nodes.find((n) => n.key === CEO_KEY);

  if (!ceo) return { laid: [], wedges: [] };

  const solved = solveGalaxy(byKey, kids, ceo);

  const laid = state.nodes.map((n) => {

    const p = solved.pos.get(n.key) ?? { x: 0, y: 0 };

    const s = nodeSize(n);

    return { key: n.key, type: n.type, parent: n.parent, x: p.x, y: p.y, w: s.w, h: s.h, rot: solved.rots.get(n.key) ?? 0 };

  });

  const wedges = state.nodes

    .filter((n) => solved.wedges.has(n.key))

    .map((n) => {

      const w = solved.wedges.get(n.key)!;

      const p = solved.pos.get(n.key) ?? { x: 0, y: 0 };

      return { key: n.key, a0: w.a0, a1: w.a1, r: w.r, x: p.x, y: p.y };

    });

  return { laid, wedges };

}

export type GraphLayoutMode = "radial" | "tree";



/**

 * 树状布局（v1 relayout tree 同款语义）：根（CEO）顶部居中，多轮水平均布，

 * 每棵子树向下逐层展开（depth 决定 y，叶子按 slot 均布水平宽度）。

 * 用于「一键整理」三态循环中的"树状"。

 */

function layoutTreeTidy(state: GraphState): LayoutResult {

  const byKey = new Map(state.nodes.map((n) => [n.key, n]));

  const kids = new Map<string, GraphNode[]>();

  for (const n of state.nodes) {

    if (n.parent === null) continue;

    const p = byKey.has(n.parent) ? n.parent : "ceo";

    const arr = kids.get(p) ?? [];

    arr.push(n);

    kids.set(p, arr);

  }

  const ceo = state.nodes.find((n) => n.key === "ceo");

  if (!ceo) return { nodes: [], width: 0, height: 0, maxDepth: 0 };



  // 深度

  const depth = new Map<string, number>();

  {

    const stack: { k: string; d: number }[] = [{ k: ceo.key, d: 0 }];

    const seen = new Set<string>();

    while (stack.length) {

      const { k, d } = stack.pop()!;

      if (seen.has(k)) continue;

      seen.add(k);

      depth.set(k, d);

      for (const c of kids.get(k) ?? []) stack.push({ k: c.key, d: d + 1 });

    }

    let extraD = 1;

    for (const n of state.nodes) if (!depth.has(n.key)) depth.set(n.key, extraD++);

  }

  const maxDepth = Math.max(...state.nodes.map((n) => depth.get(n.key) ?? 1));



  // 叶子 slot：中序遍历给每片叶子编号，父 slot = 子叶 slot 均值

  const slot = new Map<string, number>();

  let cursor = 0;

  function assign(k: string): void {

    const cs = kids.get(k) ?? [];

    if (!cs.length) {

      slot.set(k, cursor++);

      return;

    }

    assign(cs[0].key);

    const first = slot.get(cs[0].key)!;

    let last = first;

    for (let i = 1; i < cs.length; i++) {

      assign(cs[i].key);

      last = slot.get(cs[i].key)!;

    }

    slot.set(k, (first + last) / 2);

  }

  assign(ceo.key);

  const total = Math.max(1, cursor);



  const ROW_H = 92; // 每层行高

  const COL_W = 150; // 每 slot 列宽

  const pos = new Map<string, { x: number; y: number }>();

  for (const n of state.nodes) {

    const d = depth.get(n.key) ?? 0;

    const s = slot.get(n.key) ?? 0;

    pos.set(n.key, {

      x: (s + 0.5) * COL_W - (total * COL_W) / 2,

      y: 50 + d * ROW_H,

    });

  }



  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

  const nodes: LaidNode[] = state.nodes.map((n) => {

    const p = pos.get(n.key) ?? { x: 0, y: 0 };

    const sz = nodeSize(n);

    const { ex, ey } = aabbHalf(sz.w, sz.h, 0);

    if (p.x - ex < minX) minX = p.x - ex;

    if (p.x + ex > maxX) maxX = p.x + ex;

    if (p.y - ey < minY) minY = p.y - ey;

    if (p.y + ey > maxY) maxY = p.y + ey;

    return {

      n,

      x: p.x,

      y: p.y,

      w: sz.w,

      h: sz.h,

      rot: 0,

      depth: depth.get(n.key) ?? 0,

      ghost: false,

    };

  });

  return {

    nodes,

    width: Math.max(1, maxX - minX) + PAD * 2,

    height: Math.max(1, maxY - minY) + PAD * 2,

    maxDepth,

  };

}



export function layoutTree(state: GraphState, mode: GraphLayoutMode = "radial"): LayoutResult {

  if (mode === "tree") return layoutTreeTidy(state);

  const byKey = new Map(state.nodes.map((n) => [n.key, n]));

  // 孤儿回挂 CEO（数据损坏兜底）

  const kids = new Map<string, GraphNode[]>();

  for (const n of state.nodes) {

    if (n.parent === null) continue;

    const p = byKey.has(n.parent) ? n.parent : "ceo";

    const arr = kids.get(p) ?? [];

    arr.push(n);

    kids.set(p, arr);

  }

  const ceo = state.nodes.find((n) => n.key === "ceo");

  if (!ceo) return { nodes: [], width: 0, height: 0, maxDepth: 0 };



  // 深度（BFS，用于 maxDepth 与视觉衰减）

  const depth = new Map<string, number>();

  {

    const stack: { k: string; d: number }[] = [{ k: ceo.key, d: 0 }];

    const seen = new Set<string>();

    while (stack.length) {

      const { k, d } = stack.pop()!;

      if (seen.has(k)) continue;

      seen.add(k);

      depth.set(k, d);

      for (const c of kids.get(k) ?? []) stack.push({ k: c.key, d: d + 1 });

    }

    let extraD = 1;

    for (const n of state.nodes) {

      if (!depth.has(n.key)) depth.set(n.key, extraD++);

    }

  }

  const maxDepth = Math.max(...state.nodes.map((n) => depth.get(n.key) ?? 1));



  const solved = solveGalaxy(byKey, kids, ceo);

  const pos = solved.pos;



  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

  const nodes: LaidNode[] = state.nodes.map((n) => {

    const p = pos.get(n.key) ?? { x: 0, y: 0 };

    const s = nodeSize(n);

    const rot = solved.rots.get(n.key) ?? 0;

    const { ex, ey } = aabbHalf(s.w, s.h, rot);

    if (p.x - ex < minX) minX = p.x - ex;

    if (p.x + ex > maxX) maxX = p.x + ex;

    if (p.y - ey < minY) minY = p.y - ey;

    if (p.y + ey > maxY) maxY = p.y + ey;

    return {

      n,

      x: p.x,

      y: p.y,

      w: s.w,

      h: s.h,

      rot,

      depth: depth.get(n.key) ?? 0,

      ghost: false,

    };

  });

  const width = Math.max(1, maxX - minX) + PAD * 2;

  const height = Math.max(1, maxY - minY) + PAD * 2;

  return { nodes, width, height, maxDepth };

}



// ---------------- 动画状态 ----------------



interface AnimNode {

  x: number;

  y: number;

  scale: number;

  opacity: number;

  /** 当前显示的旋转角（向布局目标 rot 平滑跟随） */

  rot: number;

  /** 出生时刻（performance.now），用于弹性生长曲线 */

  t0: number;

}



/** easeOutBack：从 0 到 1，末端过冲到 ~1.1 再回落（弹性生长感） */

function easeOutBack(t: number): number {

  const c1 = 1.70158;

  const c3 = c1 + 1;

  const u = t - 1;

  return 1 + c3 * u * u * u + c1 * u * u;

}



/** easeOutCubic：快速出发缓速到达（连线生长） */

function easeOutCubic(t: number): number {

  const u = 1 - t;

  return 1 - u * u * u;

}



/** 最短角差：把 to−from 归一化到 (−π, π]，用于角度平滑插值 */

function shortAngle(from: number, to: number): number {

  let d = (to - from) % (2 * Math.PI);

  if (d > Math.PI) d -= 2 * Math.PI;

  if (d < -Math.PI) d += 2 * Math.PI;

  return d;

}



// ---------------- 引擎 ----------------



export interface GraphCanvasHandlers {

  onSelect?: (key: string | null) => void;

  onHover?: (key: string | null, cx: number, cy: number) => void;

  /** 点击 A2A 委派边（点到线段命中）；null = 点了空白处取消选中 */

  onA2aEdgeSelect?: (delegationId: string | null, cx: number, cy: number) => void;

}



export interface GraphCanvasOptions {

  getGraph: () => GraphState;

  selected: () => string | null;

  /** Fresh live session only; historical/disconnected views never imply message traffic. */
  getLive?: () => boolean;

  handlers: GraphCanvasHandlers;

  /** 按轮次聚焦：返回 "round-N" 只 fit 该轮子树；null/undefined = 全图 */

  getFocusRound?: () => string | null;

  /** 当前布局模式（radial=行星 / tree=树状） */

  getLayoutMode?: () => GraphLayoutMode;

  /** 手动拖拽偏移：根节点 key → 整棵子树平移量（undefined = 无手动布局） */

  getManual?: () => Map<string, { dx: number; dy: number }>;

  /** 拖动结束回调（子树整体偏移，需写入持久层/store） */

  onManualChange?: (map: Map<string, { dx: number; dy: number }>) => void;

  /** A2A 节点间委派边（merged_brain；叠加在树连线之上，虚线渲染） */

  getA2aEdges?: () => A2aEdge[];

}



/** 手动偏移叠加：把 layoutTree 结果中每个手动根及其全部后代整体平移 */

export function applyManualOffsets(

  laid: LaidNode[],

  manual: Map<string, { dx: number; dy: number }>,

): LaidNode[] {

  if (!manual.size) return laid;

  const byKey = new Map(laid.map((l) => [l.n.key, l]));

  const kids = new Map<string, string[]>();

  for (const l of laid) {

    if (!l.n.parent) continue;

    const p = byKey.has(l.n.parent) ? (l.n.parent as string) : "ceo";

    const arr = kids.get(p) ?? [];

    arr.push(l.n.key);

    kids.set(p, arr);

  }

  // 手动根按深度从深到浅？任意顺序均可——每个根的偏移独立作用于它自己的子树，

  // 若 A 是 B 的祖先且都在 manual，B 子树会叠加两段（拖父再拖子=相对叠加，符合直觉）。

  const out = laid.map((l) => ({ ...l, x: l.x, y: l.y }));

  const outByKey = new Map(out.map((l) => [l.n.key, l]));

  const add = (k: string, dx: number, dy: number, seen: Set<string>) => {

    const l = outByKey.get(k);

    if (!l || seen.has(k)) return;

    seen.add(k);

    l.x += dx;

    l.y += dy;

    for (const c of kids.get(k) ?? []) add(c, dx, dy, seen);

  };

  for (const [root, off] of manual) {

    add(root, off.dx, off.dy, new Set());

  }

  return out;

}



export class GraphCanvas {

  private canvas: HTMLCanvasElement;

  private ctx: CanvasRenderingContext2D;

  private opts: GraphCanvasOptions;

  private view = { x: 60, y: 40, k: 1 };

  private anim = new Map<string, AnimNode>();

  private ghosts = new Map<string, { x: number; y: number; t0: number; w: number; h: number; n: GraphNode }>();

  private raf = 0;

  private t0 = performance.now();

  private hoverKey: string | null = null;

  private dragging = false;

  /** 节点拖动状态：dragNodeKey=被拖子树根，dragNode={dx,dy}=相对拖起点的世界增量 */

  private dragNodeKey: string | null = null;

  private dragNode: { dx: number; dy: number } | null = null;

  private dragStart = { x: 0, y: 0, vx: 0, vy: 0 };

  private userTouched = false;

  private fittedOnce = false;

  private destroyed = false;

  private dpr = 1;

  private lastNodeCount = 0;

  private resizeObs: ResizeObserver | null = null;

  private laid: LaidNode[] = [];

  private layoutCache = new GraphLayoutCache();

  private lastFrameAt = 0;

  private onVisibility = (): void => {

    if (document.hidden) { cancelAnimationFrame(this.raf); this.raf = 0; }

    else if (!this.destroyed && !this.raf) this.raf = requestAnimationFrame(this.tick);

  };

  private edges: { from: LaidNode; to: LaidNode }[] = [];

  /** 边首次出现时刻（key = from\0to），用于连线生长动画 */

  private edgeBirth = new Map<string, number>();



  constructor(canvas: HTMLCanvasElement, opts: GraphCanvasOptions) {

    this.canvas = canvas;

    const ctx = canvas.getContext("2d");

    if (!ctx) throw new Error("canvas 2d context unavailable");

    this.ctx = ctx;

    this.opts = opts;

    this.dpr = Math.min(window.devicePixelRatio || 1, 2);

    this.resizeObs = new ResizeObserver(() => this.resize());

    this.resizeObs.observe(canvas);

    this.resize();

    canvas.addEventListener("wheel", this.onWheel, { passive: false });

    canvas.addEventListener("pointerdown", this.onDown);

    canvas.addEventListener("pointermove", this.onMove);

    canvas.addEventListener("pointerup", this.onUp);

    canvas.addEventListener("pointerleave", this.onLeave);

    canvas.addEventListener("dblclick", this.onDblClick);

  }



  start(): void {

    document.addEventListener("visibilitychange", this.onVisibility);

    if (!document.hidden) this.raf = requestAnimationFrame(this.tick);

  }



  destroy(): void {

    this.destroyed = true;

    cancelAnimationFrame(this.raf);

    document.removeEventListener("visibilitychange", this.onVisibility);

    this.resizeObs?.disconnect();

    this.canvas.removeEventListener("wheel", this.onWheel);

    this.canvas.removeEventListener("pointerdown", this.onDown);

    this.canvas.removeEventListener("pointermove", this.onMove);

    this.canvas.removeEventListener("pointerup", this.onUp);

    this.canvas.removeEventListener("pointerleave", this.onLeave);

    this.canvas.removeEventListener("dblclick", this.onDblClick);

  }



  private resize(): void {

    const w = this.canvas.clientWidth || 800;

    const h = this.canvas.clientHeight || 500;

    this.canvas.width = Math.round(w * this.dpr);

    this.canvas.height = Math.round(h * this.dpr);

  }



  // ---------------- 视图控制 ----------------



  /** 手动偏移（拖动中叠加本地增量 + store 存量） */

  private manualBase(): Map<string, { dx: number; dy: number }> {

    const m = this.opts.getManual?.() ?? new Map<string, { dx: number; dy: number }>();

    return m;

  }



  /** 布局 = 自动布局（行星/树状）+ 手动拖拽偏移（统一供 fit/渲染/hitTest 使用） */

  private computeLayout(): LayoutResult {

    const g = this.opts.getGraph();

    const mode = this.opts.getLayoutMode?.() ?? "radial";

    const laid = this.layoutCache.get(g, mode);

    const final = applyManualOffsets(laid.nodes, this.manualBase());

    // 测试/调试钩子：最近的布局结果挂在 canvas 上（e2e 读取节点坐标用）

    try {

      (this.canvas as HTMLCanvasElement & { __adgsLayout?: unknown }).__adgsLayout = final.map(

        (l) => ({ key: l.n.key, x: l.x, y: l.y, w: l.w, h: l.h, rot: l.rot }),

      );

      (this.canvas as HTMLCanvasElement & { __adgsView?: unknown }).__adgsView = { ...this.view };

    } catch {

      /* ignore */

    }

    return { ...laid, nodes: final };

  }



  fit(): void {

    const g = this.opts.getGraph();

    if (g.nodes.length <= 1) return;

    const laid = this.computeLayout();

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

    for (const l of laid.nodes) {

      const { ex, ey } = aabbHalf(l.w, l.h, l.rot);

      minX = Math.min(minX, l.x - ex);

      maxX = Math.max(maxX, l.x + ex);

      minY = Math.min(minY, l.y - ey);

      maxY = Math.max(maxY, l.y + ey);

    }

    for (const p of this.virtualAnchors(laid.nodes).values()) {
      minX = Math.min(minX, p.x - 50); maxX = Math.max(maxX, p.x + 50);
      minY = Math.min(minY, p.y - 12); maxY = Math.max(maxY, p.y + 30);
    }
    this.fitToBBox(minX, minY, maxX, maxY);

  }



  /**

   * 聚焦到某一轮的子树（2026-09-04：历史会话多轮树太高，全图 fit 缩到不可读）。

   * 只框住 rootKey 子树 + CEO 根，缩放到可读尺度；其它轮的节点仍画但可能在视口外。

   */

  fitSubtree(rootKey: string): void {

    const g = this.opts.getGraph();

    if (g.nodes.length <= 1) return;

    const keySet = new Set(g.nodes.map((n) => n.key));

    if (!keySet.has(rootKey)) return;

    // 收集 rootKey 子树（孤儿回挂 ceo，与 layoutTree 的 kids 逻辑一致）

    const kids = new Map<string, string[]>();

    for (const n of g.nodes) {

      if (n.parent === null) continue;

      const p = keySet.has(n.parent) ? n.parent : "ceo";

      const arr = kids.get(p) ?? [];

      arr.push(n.key);

      kids.set(p, arr);

    }

    for (const link of g.links ?? []) {
      const children = kids.get(link.from) ?? [];
      if (!children.includes(link.to)) children.push(link.to);
      kids.set(link.from, children);
    }

    const inSub = new Set<string>([rootKey, CEO_KEY]);

    const stack = [rootKey];

    while (stack.length) {

      const k = stack.pop()!;

      for (const c of kids.get(k) ?? []) {

        if (!inSub.has(c)) {

          inSub.add(c);

          stack.push(c);

        }

      }

    }

    const laid = this.computeLayout();

    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

    let found = false;

    for (const l of laid.nodes) {

      if (!inSub.has(l.n.key)) continue;

      found = true;

      const { ex, ey } = aabbHalf(l.w, l.h, l.rot);

      minX = Math.min(minX, l.x - ex);

      maxX = Math.max(maxX, l.x + ex);

      minY = Math.min(minY, l.y - ey);

      maxY = Math.max(maxY, l.y + ey);

    }

    if (!found) {

      this.fit();

      return;

    }

    this.fitToBBox(minX, minY, maxX, maxY);

  }



  private fitToBBox(minX: number, minY: number, maxX: number, maxY: number): void {

    const pad = 70;

    const bw = maxX - minX + pad * 2;

    const bh = maxY - minY + pad * 2;

    const cw = this.canvas.clientWidth || 800;

    const ch = this.canvas.clientHeight || 500;

    const k = Math.min(1.6, Math.max(0.1, Math.min(cw / bw, ch / bh)));

    this.view.k = k;

    this.view.x = cw / 2 - ((minX + maxX) / 2) * k;

    this.view.y = ch / 2 - ((minY + maxY) / 2) * k;

    this.fittedOnce = true;

  }



  resetView(): void {

    this.view = { x: 60, y: 40, k: 1 };

    this.userTouched = false;

  }



  /** 以画布中心缩放（工具栏按钮） */

  zoom(factor: number): void {

    this.userTouched = true;

    const cx = (this.canvas.clientWidth || 800) / 2;

    const cy = (this.canvas.clientHeight || 500) / 2;

    const k = Math.min(3, Math.max(MIN_ZOOM, this.view.k * factor));

    const wx = (cx - this.view.x) / this.view.k;

    const wy = (cy - this.view.y) / this.view.k;

    this.view.k = k;

    this.view.x = cx - wx * k;

    this.view.y = cy - wy * k;

  }



  get viewK(): number {

    return this.view.k;

  }



  // ---------------- 交互 ----------------



  private toWorld(px: number, py: number): { x: number; y: number } {

    return { x: (px - this.view.x) / this.view.k, y: (py - this.view.y) / this.view.k };

  }



  private onWheel = (e: WheelEvent): void => {

    e.preventDefault();

    this.userTouched = true;

    const rect = this.canvas.getBoundingClientRect();

    const px = e.clientX - rect.left;

    const py = e.clientY - rect.top;

    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;

    const k = Math.min(3, Math.max(MIN_ZOOM, this.view.k * factor));

    const wx = (px - this.view.x) / this.view.k;

    const wy = (py - this.view.y) / this.view.k;

    this.view.k = k;

    this.view.x = px - wx * k;

    this.view.y = py - wy * k;

  };



  private onDown = (e: PointerEvent): void => {

    this.dragging = true;

    this.userTouched = true;

    const rect = this.canvas.getBoundingClientRect();

    const px = e.clientX - rect.left;

    const py = e.clientY - rect.top;

    // 命中节点 → 拖该节点整棵子树；命中 A2A 锚点 → 拖锚点；空白 → 拖画布

    const hit = this.hitTest(px, py);

    this.dragNodeKey = hit && hit !== CEO_KEY ? hit : null;

    this.dragNode = null;

    this.dragVirtualId = this.dragNodeKey ? null : this.hitA2aAnchor(px, py);
    this.dragVirtualStart = this.dragVirtualId ? this.a2aVirtual.get(this.dragVirtualId) ?? null : null;

    this.dragStart = { x: e.clientX, y: e.clientY, vx: this.view.x, vy: this.view.y };

    this.canvas.setPointerCapture(e.pointerId);

  };



  private onMove = (e: PointerEvent): void => {

    if (this.dragging) {

      const dxPx = e.clientX - this.dragStart.x;

      const dyPx = e.clientY - this.dragStart.y;

      this.opts.handlers.onHover?.(null, 0, 0);

      this.hoverKey = null;

      if (this.dragNodeKey) {

        // 节点拖动：整棵子树跟随（render 里按 dragNode 增量叠加）

        this.dragNode = { dx: dxPx / this.view.k, dy: dyPx / this.view.k };

        return;

      }

      if (this.dragVirtualId) {

        // A2A 锚点拖动：指针世界坐标即锚点新位置



        this.dragNode = { dx: dxPx / this.view.k, dy: dyPx / this.view.k };
        if (this.dragVirtualStart) this.a2aVirtual.set(this.dragVirtualId, {
          x: this.dragVirtualStart.x + this.dragNode.dx,
          y: this.dragVirtualStart.y + this.dragNode.dy,
        });

        return;

      }

      this.view.x = this.dragStart.vx + dxPx;

      this.view.y = this.dragStart.vy + dyPx;

      return;

    }

    const rect = this.canvas.getBoundingClientRect();

    const key = this.hitTest(e.clientX - rect.left, e.clientY - rect.top);
    this.canvas.style.cursor = key || this.hitA2aAnchor(e.clientX - rect.left, e.clientY - rect.top) ? "grab"
      : this.hitA2aEdge(e.clientX - rect.left, e.clientY - rect.top) ? "pointer" : "";

    if (key !== this.hoverKey) {

      this.hoverKey = key;

      const rect2 = this.canvas.getBoundingClientRect();

      this.opts.handlers.onHover?.(key, e.clientX - rect2.left, e.clientY - rect2.top);

    }

  };



  private onUp = (e: PointerEvent): void => {

    const moved =

      Math.abs(e.clientX - this.dragStart.x) + Math.abs(e.clientY - this.dragStart.y) > 5;

    this.dragging = false;

    if (this.dragVirtualId && this.dragNode) {
      const base = this.manualBase();
      const key = `a2a:${this.dragVirtualId}`;
      const prev = base.get(key) ?? { dx: 0, dy: 0 };
      const next = new Map(base);
      next.set(key, { dx: prev.dx + this.dragNode.dx, dy: prev.dy + this.dragNode.dy });
      this.opts.onManualChange?.(next);
    }
    if (this.dragNodeKey && this.dragNode) {

      // 拖动结束：把增量并入 store 的手动偏移（整棵子树）

      const base = this.manualBase();

      const prev = base.get(this.dragNodeKey) ?? { dx: 0, dy: 0 };

      const next = new Map(base);

      next.set(this.dragNodeKey, {

        dx: prev.dx + this.dragNode.dx,

        dy: prev.dy + this.dragNode.dy,

      });

      this.opts.onManualChange?.(next);

    }

    this.dragNodeKey = null;

    this.dragNode = null;

    this.dragVirtualId = null;
    this.dragVirtualStart = null;

    if (!moved) {

      const rect = this.canvas.getBoundingClientRect();

      const px = e.clientX - rect.left;

      const py = e.clientY - rect.top;

      const key = this.hitTest(px, py);

      if (key) {

        this.opts.handlers.onSelect?.(key);

        this.opts.handlers.onA2aEdgeSelect?.(null, px, py);

        return;

      }

      const edgeId = this.hitA2aEdge(px, py);

      this.opts.handlers.onA2aEdgeSelect?.(edgeId, px, py);

      if (edgeId == null) this.opts.handlers.onSelect?.(null);

    }

  };



  /** A2A 委派边命中：点到线段距离 ≤ 7px（世界系判定，与 hitTest 同一 toWorld 变换） */

  private hitA2aEdge(px: number, py: number): string | null {

    const w = this.toWorld(px, py);

    // 与绘制同一几何：沿贝塞尔曲线采样 8 点判近（多边弧线各自可命中）

    const threshold = 8 / this.view.k;

    let nearest: string | null = null;
    let distance = threshold;
    for (const geom of this.a2aEdgeGeom) {
      const length = Math.hypot(geom.cp.x - geom.p0.x, geom.cp.y - geom.p0.y) + Math.hypot(geom.p1.x - geom.cp.x, geom.p1.y - geom.cp.y);
      const steps = Math.max(16, Math.min(512, Math.ceil(length * this.view.k / 8)));
      let a = geom.p0;
      for (let i = 1; i <= steps; i++) {
        const b = GraphCanvas.qpoint(geom.p0, geom.cp, geom.p1, i / steps);
        const dx = b.x - a.x, dy = b.y - a.y;
        const u = Math.max(0, Math.min(1, ((w.x - a.x) * dx + (w.y - a.y) * dy) / (dx * dx + dy * dy || 1)));
        const d = Math.hypot(w.x - a.x - u * dx, w.y - a.y - u * dy);
        if (d < distance) { distance = d; nearest = geom.delegationId; }
        a = b;
      }
    }
    return nearest;

  }





  private onLeave = (): void => {

    this.hoverKey = null;

    this.opts.handlers.onHover?.(null, 0, 0);

  };



  private onDblClick = (): void => {

    this.fit();

  };



  private hitTest(px: number, py: number): string | null {

    const w = this.toWorld(px, py);

    for (let i = this.laid.length - 1; i >= 0; i--) {

      const l = this.laid[i];

      const a = this.anim.get(l.n.key);

      if (!a || a.opacity < 0.5) continue;

      // 命中所见即所得：用动画位置/角度（探测点逆旋转到节点本地系再判 AABB）

      const dx = w.x - a.x;

      const dy = w.y - a.y;

      const cos = Math.cos(-a.rot);

      const sin = Math.sin(-a.rot);

      const lx = dx * cos - dy * sin;

      const ly = dx * sin + dy * cos;

      if (Math.abs(lx) <= l.w / 2 && Math.abs(ly) <= l.h / 2) return l.n.key;

    }

    return null;

  }



  // ---------------- 帧循环 ----------------



  private tick = (): void => {

    this.raf = 0;

    if (this.destroyed || document.hidden) return;

    const now = performance.now();

    if (now - this.lastFrameAt >= 1000 / 30) {

      this.lastFrameAt = now;

      this.render(now);

    }

    this.raf = requestAnimationFrame(this.tick);

  };



  private render(now: number): void {

    const t = (now - this.t0) / 1000;

    const g = this.opts.getGraph();

    // 布局 = 自动布局（行星/树状）+ 手动偏移（拖动中叠加本帧增量）

    const mode = this.opts.getLayoutMode?.() ?? "radial";

    let laidNodes = this.layoutCache.get(g, mode).nodes;

    if (this.dragNode && this.dragNodeKey) {

      const base = this.manualBase();

      const draft = new Map(base);

      const key = this.dragNodeKey;

      // 拖动中的实时偏移 = 原累计偏移 + 本次拖动增量（相对拖前该根自动位置）

      const rootAuto = laidNodes.find((l) => l.n.key === key);

      if (rootAuto) {

        const prev = base.get(key) ?? { dx: 0, dy: 0 };

        draft.set(key, {

          dx: prev.dx + this.dragNode.dx,

          dy: prev.dy + this.dragNode.dy,

        });

      }

      laidNodes = applyManualOffsets(laidNodes, draft);

    } else {

      laidNodes = applyManualOffsets(laidNodes, this.manualBase());

    }

    const laid = { nodes: laidNodes, width: 0, height: 0, maxDepth: 0 };

    this.laid = laid.nodes;

    // 测试/调试钩子：最近的布局结果挂在 canvas 上（e2e 读取节点坐标用）

    try {

      (this.canvas as HTMLCanvasElement & { __adgsLayout?: unknown }).__adgsLayout = laid.nodes.map((l) => ({

        key: l.n.key,

        x: l.x,

        y: l.y,

        w: l.w,

        h: l.h,

        rot: l.rot,

      }));

      (this.canvas as HTMLCanvasElement & { __adgsView?: unknown }).__adgsView = { ...this.view };

    } catch {

      /* ignore */

    }



    // 幽灵（删除节点淡出）

    const liveKeys = new Set(g.nodes.map((n) => n.key));

    for (const [k, a] of this.anim) {

      if (!liveKeys.has(k)) {

        const l = laid.nodes.find((x) => x.n.key === k);

        this.ghosts.set(k, {

          x: a.x, y: a.y, t0: now,

          w: l?.w ?? 120, h: l?.h ?? 40,

          n: { key: k, id: k, type: "node", label: k, parent: null, status: "idle", extra: {} },

        });

        this.anim.delete(k);

      }

    }

    for (const [k, gh] of this.ghosts) {

      if (now - gh.t0 > 450) this.ghosts.delete(k);

    }



    // 动画位置推进（拖动中：被拖子树硬跟随 laid 目标，其余正常 lerp）

    const dragSub = this.dragNodeKey ? new Set<string>() : null;

    if (dragSub && this.dragNodeKey) {

      const stack: string[] = [this.dragNodeKey];

      while (stack.length) {

        const k = stack.pop()!;

        if (dragSub.has(k)) continue;

        dragSub.add(k);

        for (const c of laid.nodes) {

          if (c.n.parent === k) stack.push(c.n.key);

        }

      }

    }

    for (const l of laid.nodes) {

      let a = this.anim.get(l.n.key);

      if (!a) {

        // 新节点：从父节点位置弹性生长

        const parent = laid.nodes.find((p) => p.n.key === l.n.parent);

        const px = parent ? this.anim.get(parent.n.key)?.x ?? parent.x : l.x;

        const py = parent ? this.anim.get(parent.n.key)?.y ?? parent.y : l.y;

        a = { x: px, y: py, scale: 0, opacity: 0, rot: l.rot, t0: now };

        this.anim.set(l.n.key, a);

      }

      if (dragSub && dragSub.has(l.n.key)) {

        a.x = l.x;

        a.y = l.y; // 拖动：整棵子树硬跟随，无 lerp 滞后

      } else {

        a.x += (l.x - a.x) * 0.16;

        a.y += (l.y - a.y) * 0.16;

      }

      a.scale = easeOutBack(Math.min(1, (now - a.t0) / 450));

      a.rot += shortAngle(a.rot, l.rot) * 0.16;

      a.opacity += (1 - a.opacity) * 0.22;

    }





    // 首次出现内容 → 自动适应（用户手动操作过视图后不再抢占）

    const cnt = g.nodes.length;

    if (!this.fittedOnce && !this.userTouched && cnt > this.lastNodeCount) {

      const fr = this.opts.getFocusRound?.();

      if (fr) this.fitSubtree(fr);

      else this.fit();

    }

    if (cnt <= 1) {

      this.fittedOnce = false;

      this.userTouched = false;

    }

    this.lastNodeCount = cnt;



    // 边

    const byKey = new Map(laid.nodes.map((l) => [l.n.key, l]));

    this.edges = [];

    for (const l of laid.nodes) {

      if (!l.n.parent) continue;

      const p = byKey.get(l.n.parent);

      if (p) this.edges.push({ from: p, to: l });

    }



    // Repeated dispatches reference the same context instance without reparenting it.
    for (const link of g.links ?? []) {
      const from = byKey.get(link.from), to = byKey.get(link.to);
      if (from && to && !this.edges.some(e => e.from.n.key === from.n.key && e.to.n.key === to.n.key)) this.edges.push({ from, to });
    }

    const ctx = this.ctx;

    const { width: cw, height: ch } = { width: this.canvas.width / this.dpr, height: this.canvas.height / this.dpr };

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    ctx.clearRect(0, 0, cw, ch);

    ctx.save();

    ctx.translate(this.view.x, this.view.y);

    ctx.scale(this.view.k, this.view.k);



    // 调试/测试锚点：尽量早写（后续任何绘制异常都不影响外部观测布局与视图）

    (this.canvas as HTMLCanvasElement & { __adgsDrawnLayout?: unknown }).__adgsDrawnLayout = laid.nodes.map((l) => {

      const a = this.anim.get(l.n.key)!;

      return { key: l.n.key, x: a.x, y: a.y, scale: a.scale };

    });

    (this.canvas as HTMLCanvasElement & { __adgsView?: unknown }).__adgsView = {

      x: this.view.x, y: this.view.y, k: this.view.k,

    };

    Object.assign(this.canvas,{__adgsNodeStates:laid.nodes.map(l=>({key:l.n.key,status:l.n.status,color:this.nodeColor(l.n)}))});

    this.drawEdges(ctx, t, now);

    this.drawA2aEdges(ctx, t);

    // 节点填充与文字遮住途经的连线，避免线条压住标签。
    for (const l of laid.nodes) this.drawNode(ctx, l, t);

    // 幽灵淡出

    for (const gh of this.ghosts.values()) {

      const p = 1 - (now - gh.t0) / 450;

      ctx.globalAlpha = Math.max(0, p) * 0.6;

      this.roundRect(ctx, gh.x - gh.w / 2, gh.y - gh.h / 2, gh.w, gh.h, 10);

      ctx.strokeStyle = "rgba(120,155,210,0.4)";

      ctx.stroke();

    }

    ctx.globalAlpha = 1;

    ctx.restore();

  };



  // ---------------- 绘制 ----------------



  private nodeColor(n: GraphNode): string {
    const theme=themeColors();
    return n.type === "ceo" ? theme.ceo : n.type === "round" ? theme.round : n.type === "task" ? theme.task : this.statusColor(n);
  }

  private statusColor(n: GraphNode): string {

    switch (n.status) {

      case "waiting": return themeColors().waiting;

      case "running": return themeColors().running;

      case "delegating": return themeColors().delegating;

      case "evaluating": return themeColors().evaluating;

      case "waiting_user": return themeColors().waiting;

      case "done": return themeColors().done;

      case "failed": return themeColors().failed;

      case "blocked":

      case "unjudged":

      case "interrupted": return themeColors().waiting;

      default: return themeColors().idle;

    }

  }



  private roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {

    ctx.beginPath();

    ctx.moveTo(x + r, y);

    ctx.arcTo(x + w, y, x + w, y + h, r);

    ctx.arcTo(x + w, y + h, x, y + h, r);

    ctx.arcTo(x, y + h, x, y, r);

    ctx.arcTo(x, y, x + w, y, r);

    ctx.closePath();

  }



  /**

   * 节点形状沿世界方向 (ux,uy) 的支撑距离（中心 → 表面）。

   * 按实际绘制形状精确计算（六边形/胶囊/圆/旋转矩形），乘动画缩放并压入

   * 1px——连线端点永远钉在形状表面上、藏进填充里，绝不悬空。

   */

  private shapeSupport(l: LaidNode, scale: number, rot: number, ux: number, uy: number): number {

    const c = Math.cos(-rot), s = Math.sin(-rot);

    const lx = Math.abs(ux * c - uy * s);

    const ly = Math.abs(ux * s + uy * c);

    const hw = l.w / 2, hh = l.h / 2;
    let d: number;
    if (l.n.type === "task") d = hw;
    else if (l.n.type === "ceo") {
      // 射线与仿射六边形边界相交；投影支撑距离不是射线交点。
      d = Math.min(lx > 1e-9 ? Math.sqrt(3) / 2 * hw / lx : Infinity,
        1 / (lx / (Math.sqrt(3) * hw) + ly / hh));
    } else {
      const r = l.n.type === "round" ? hh : 11;
      let lo = 0, hi = Math.hypot(hw, hh);
      for (let i = 0; i < 32; i++) {
        const mid = (lo + hi) / 2;
        const dx = Math.max(mid * lx - (hw - r), 0);
        const dy = Math.max(mid * ly - (hh - r), 0);
        if (dx * dx + dy * dy <= r * r) lo = mid; else hi = mid;
      }
      d = lo;
    }
    return Math.max(0, (d - 1) * scale);

  }



  /** A2A 节点间委派边：端点归一到双方最近实例，虚线 + 状态色（叠加在树连线上） */

  /** A2A 委派边几何缓存（绘制时生成，hitA2aEdge 复用同一曲线采样） */

  private a2aEdgeGeom: { delegationId: string; p0: { x: number; y: number }; cp: { x: number; y: number }; p1: { x: number; y: number } }[] = [];



  private static qpoint(p0: { x: number; y: number }, cp: { x: number; y: number }, p1: { x: number; y: number }, t: number): { x: number; y: number } {

    const u = 1 - t;

    return {

      x: u * u * p0.x + 2 * u * t * cp.x + t * t * p1.x,

      y: u * u * p0.y + 2 * u * t * cp.y + t * t * p1.y,

    };

  }



  /** 本帧真正画出的锚点，命中与绘制共用。手动偏移与树节点共存 store。 */
  private a2aVirtual = new Map<string, { x: number; y: number }>();
  private dragVirtualId: string | null = null;
  private dragVirtualStart: { x: number; y: number } | null = null;

  resetA2aAnchors(): void {
    this.a2aVirtual.clear();
  }

  private virtualAnchors(nodes: LaidNode[]): Map<string, { x: number; y: number }> {
    const edges = this.opts.getA2aEdges?.() ?? [];
    const present = new Set(nodes.filter(l => l.n.type === "node").map(l => l.n.id));
    const ids = [...new Set(edges.flatMap(e => [e.fromNodeId, e.toNodeId]))]
      .filter(id => !present.has(id)).sort();
    if (!ids.length) return new Map();
    const cx = nodes.length ? (Math.min(...nodes.map(l => l.x)) + Math.max(...nodes.map(l => l.x))) / 2 : 0;
    const cy = nodes.length ? (Math.min(...nodes.map(l => l.y)) + Math.max(...nodes.map(l => l.y))) / 2 : 0;
    // 全树包围圆之外留出标签距离；无视口、缩放或边顺序参与计算。
    const radius = Math.max(160, ...nodes.map(l => Math.hypot(l.x - cx, l.y - cy) + Math.hypot(l.w, l.h) / 2 + 100), ids.length * 120 / (2 * Math.PI));
    const manual = this.manualBase();
    // 保持 ID 的圆周顺序，整体旋转到最接近关联实例的一侧，减少横穿树干。
    let alignX = 0, alignY = 0;
    const positions = new Map(nodes.filter(l => l.n.type === "node").map(l => [l.n.id, l]));
    ids.forEach((id, i) => {
      const theta = i * 2 * Math.PI / ids.length;
      for (const edge of edges) {
        const peer = edge.fromNodeId === id ? edge.toNodeId : edge.toNodeId === id ? edge.fromNodeId : null;
        const p = peer ? positions.get(peer) : undefined;
        if (!p) continue;
        alignX += (p.x - cx) * Math.cos(theta) + (p.y - cy) * Math.sin(theta);
        alignY += -(p.x - cx) * Math.sin(theta) + (p.y - cy) * Math.cos(theta);
      }
    });
    const rotation = Math.hypot(alignX, alignY) > 1e-6 ? Math.atan2(alignY, alignX) : -Math.PI / 2;
    return new Map(ids.map((id, i) => {
      const angle = rotation + i * 2 * Math.PI / ids.length;
      const off = manual.get(`a2a:${id}`);
      return [id, { x: cx + Math.cos(angle) * radius + (off?.dx ?? 0), y: cy + Math.sin(angle) * radius + (off?.dy ?? 0) }];
    }));
  }

  private hitA2aAnchor(px: number, py: number): string | null {
    const w = this.toWorld(px, py);
    for (const [id, pos] of this.a2aVirtual) {
      if (Math.hypot(w.x - pos.x, w.y - pos.y) <= 12 / this.view.k) return id;
    }
    return null;
  }

  private drawA2aEdges(ctx: CanvasRenderingContext2D, t: number): void {

    const edges = this.opts.getA2aEdges?.() ?? [];

    if (!edges.length) {

      this.a2aEdgeGeom = [];
      this.a2aVirtual.clear();
      Object.assign(this.canvas, { __adgsA2aAnchors: [], __adgsA2aEdges: [] });
      return;

    }

    const sel = this.opts.selected();

    // 同一对端点的多次委派 = 多条边：按对计数做中心对称弧线偏移，

    // 否则全部直线重叠成一条，看不出委派了几次（2026-10-23 用户需求）

    const pairSeq = new Map<string, number>();

    const pairTotal = new Map<string, number>();

    const resolved: { edge: A2aEdge; a: { x: number; y: number }; b: { x: number; y: number }; pk: string; selHit: boolean }[] = [];

    const virtualAnchors: { x: number; y: number; id: string }[] = [];

    const animated = this.laid.map(l => {
      const a = this.anim.get(l.n.key);
      return a ? { ...l, x: a.x, y: a.y } : l;
    });
    this.a2aVirtual = this.virtualAnchors(animated);
    if (this.dragVirtualId && this.dragVirtualStart && this.dragNode) {
      this.a2aVirtual.set(this.dragVirtualId, {
        x: this.dragVirtualStart.x + this.dragNode.dx,
        y: this.dragVirtualStart.y + this.dragNode.dy,
      });
    }
    const endpointKey = (id: string): string | null => {
      const candidates = this.laid.filter(l => l.n.type === "node" && l.n.id === id);
      return candidates.length ? candidates[candidates.length - 1].n.key : null;
    };
    const anchorOf = (nodeId: string): { x: number; y: number } => {
      const key = endpointKey(nodeId);
      const l = key ? animated.find(l => l.n.key === key) : undefined;
      return l ?? this.a2aVirtual.get(nodeId)!;
    };

    for (const edge of edges) {

      const fromKey = endpointKey(edge.fromNodeId);

      const toKey = endpointKey(edge.toNodeId);

      const fromPos = fromKey ? this.anim.get(fromKey) : undefined;

      const toPos = toKey ? this.anim.get(toKey) : undefined;

      // 先解析实例端,虚拟端锚定到同边实例端(near)——2026-10-23 修正:
      // 此前 from 端固定 null 基准,hash 随机角度导致整理后锚点仍乱

      let a: { x: number; y: number };

      let b: { x: number; y: number };

      if (fromPos) {

        a = fromPos;

        b = anchorOf(edge.toNodeId);

      } else if (toPos) {

        b = toPos;

        a = anchorOf(edge.fromNodeId);

      } else {

        b = anchorOf(edge.toNodeId);

        a = anchorOf(edge.fromNodeId);

      }

      if (this.a2aVirtual.has(edge.fromNodeId)) virtualAnchors.push({ x: a.x, y: a.y, id: edge.fromNodeId });

      if (this.a2aVirtual.has(edge.toNodeId)) virtualAnchors.push({ x: b.x, y: b.y, id: edge.toNodeId });

      if (Math.hypot(b.x - a.x, b.y - a.y) < 8) continue;

      resolved.push({ edge, a, b, pk: [edge.fromNodeId, edge.toNodeId].sort().join("\0"), selHit: sel != null && (fromKey === sel || toKey === sel) });

      pairTotal.set(resolved[resolved.length - 1].pk, (pairTotal.get(resolved[resolved.length - 1].pk) ?? 0) + 1);

    }

    this.a2aEdgeGeom = [];

    for (const { edge, a, b, pk, selHit } of resolved) {

      const idx = pairSeq.get(pk) ?? 0;

      pairSeq.set(pk, idx + 1);

      const total = pairTotal.get(pk) ?? 1;

      // 反向边也用同一个法向，否则 A→B 与 B→A 恰好落在同一弧线上。
      const direction = edge.fromNodeId <= edge.toNodeId ? 1 : -1;
      const lane = idx - (total - 1) / 2;
      const offset = (lane * 48 + (lane >= 0 ? 1 : -1) * 80) / Math.min(1, this.view.k) * direction;

      const x1 = a.x, y1 = a.y, x2 = b.x, y2 = b.y;

      const len = Math.hypot(x2 - x1, y2 - y1);

      if (len < 8) continue;

      const nx = -(y2 - y1) / len, ny = (x2 - x1) / len;

      let p0 = { x: x1, y: y1 };

      const cp = { x: (x1 + x2) / 2 + nx * offset, y: (y1 + y2) / 2 + ny * offset };

      let p1 = { x: x2, y: y2 };
      const clip = (id: string, pos: { x: number; y: number }) => {
        const dx = cp.x - pos.x, dy = cp.y - pos.y;
        const d = Math.hypot(dx, dy) || 1;
        const key = endpointKey(id);
        const l = this.laid.find(n => n.n.key === key);
        const anim = key ? this.anim.get(key) : undefined;
        const r = l && anim ? this.shapeSupport(l, anim.scale, anim.rot, dx / d, dy / d) : 9;
        return { x: pos.x + dx / d * r, y: pos.y + dy / d * r };
      };
      p0 = clip(edge.fromNodeId, p0);
      p1 = clip(edge.toNodeId, p1);

      this.a2aEdgeGeom.push({ delegationId: edge.delegationId, p0, cp, p1 });

      const escalated = edge.escalatedBy != null;

      const color = escalated ? "rgba(244,158,83,0.8)"

        : edge.status === "running" ? "rgba(178,132,255,0.75)"

        : edge.status === "passed" ? "rgba(62,207,127,0.6)"

        : "rgba(244,158,83,0.65)";

      const active = edge.status === "running";

      const incident = sel != null && selHit;

      ctx.save();

      ctx.beginPath();

      ctx.setLineDash([7, 6]);

      ctx.lineDashOffset = active ? -t * 22 : 0;

      ctx.moveTo(p0.x, p0.y);

      ctx.quadraticCurveTo(cp.x, cp.y, p1.x, p1.y);

      ctx.strokeStyle = color;

      ctx.globalAlpha = incident ? 1 : 0.75;

      ctx.lineWidth = (active ? 2.2 : 1.7) * (incident ? 1.4 : 1);

      ctx.stroke();

      // 委派方向箭头（指向子节点一端）

      const tx = p1.x - cp.x, ty = p1.y - cp.y;

      const tlen = Math.hypot(tx, ty) || 1;

      const ux = tx / tlen, uy = ty / tlen;

      const hx = p1.x - ux * 14, hy = p1.y - uy * 14;

      ctx.setLineDash([]);

      ctx.beginPath();

      ctx.moveTo(p1.x, p1.y);

      ctx.lineTo(hx - uy * 5, hy + ux * 5);

      ctx.lineTo(hx + uy * 5, hy - ux * 5);

      ctx.closePath();

      ctx.fillStyle = color;

      ctx.fill();

      // 中点标签：直系复核分 / 越级警示

      const mid = GraphCanvas.qpoint(p0, cp, p1, 0.5);

      const mx = mid.x, my = mid.y;

      const tags: string[] = [];

      if (edge.score != null) tags.push(`${edge.score >= 0 ? "+" : ""}${Math.round(edge.score)}`);

      if (escalated) tags.push("⚠越级");

      if (tags.length) {

        const label = tags.join(" ");

        ctx.font = "600 10.5px ui-monospace, monospace";

        const w = ctx.measureText(label).width + 10;

        ctx.fillStyle = "rgba(10,14,22,0.82)";

        ctx.beginPath();

        // roundRect 在部分 Chromium 内核缺失（IAB 实测抛 TypeError 中断整帧绘制）

        if (typeof ctx.roundRect === "function") {

          ctx.roundRect(mx - w / 2, my - 9, w, 18, 5);

        } else {

          ctx.rect(mx - w / 2, my - 9, w, 18);

        }

        ctx.fill();

        ctx.strokeStyle = escalated ? "rgba(244,158,83,0.5)" : "rgba(62,207,127,0.4)";

        ctx.lineWidth = 1;

        ctx.stroke();

        ctx.fillStyle = escalated ? "rgba(244,158,83,0.95)" : "rgba(62,207,127,0.9)";

        ctx.textAlign = "center";

        ctx.textBaseline = "middle";

        ctx.fillText(label, mx, my);

      }

      ctx.restore();

    }

    Object.assign(this.canvas, {
      __adgsA2aAnchors: [...this.a2aVirtual].map(([id, p]) => ({ id, ...p })),
      __adgsA2aEdges: this.a2aEdgeGeom,
    });
    // 虚拟对端锚点：不在当前任务树的委派参与方(小圆点 + 节点 id)

    const seen = new Set<string>();

    for (const v of virtualAnchors) {

      if (seen.has(v.id)) continue;

      seen.add(v.id);

      ctx.save();

      ctx.beginPath();

      ctx.arc(v.x, v.y, 7, 0, Math.PI * 2);

      ctx.fillStyle = "rgba(24,30,42,0.95)";

      ctx.fill();

      ctx.strokeStyle = "rgba(139,233,255,0.7)";

      ctx.lineWidth = 1.5;

      ctx.stroke();

      ctx.font = "600 10px ui-monospace, monospace";

      ctx.fillStyle = "rgba(139,233,255,0.85)";

      ctx.textAlign = "center";

      ctx.textBaseline = "top";

      ctx.fillText(v.id, v.x, v.y + 11);

      ctx.restore();

    }

  }



  private drawEdges(ctx: CanvasRenderingContext2D, t: number, now: number): void {

    const sel = this.opts.selected();

    const hov = this.hoverKey;

    const live = new Set<string>();
    const traffic: {from:string;to:string;kind:string|null}[] = [];

    for (const e of this.edges) {

      const a = this.anim.get(e.from.n.key);

      const b = this.anim.get(e.to.n.key);

      if (!a || !b) continue;

      // 父中心 → 子中心（行星式径向连线）

      const x1 = a.x;

      const y1 = a.y;

      const x2 = b.x;

      const y2 = b.y;

      const dx = x2 - x1;

      const dy = y2 - y1;

      const len = Math.hypot(dx, dy);

      if (len < 1e-6) continue;

      const ux = dx / len;

      const uy = dy / len;

      // 端点 = 各自形状沿连线方向的支撑表面（随生长动画缩放贴合）

      const rf = this.shapeSupport(e.from, a.scale, a.rot, ux, uy);

      const rt = this.shapeSupport(e.to, b.scale, b.rot, -ux, -uy);

      const segLen = len - rf - rt;

      if (segLen <= 0.5) continue; // 形状近乎贴上（动画初期）→ 无需画线

      // 新边生长动画：从父端向子端延伸

      const ek = `${e.from.n.key}\u0000${e.to.n.key}`;

      live.add(ek);

      let t0 = this.edgeBirth.get(ek);

      if (t0 == null) {

        t0 = now;

        this.edgeBirth.set(ek, t0);

      }

      const u = easeOutCubic(Math.min(1, (now - t0) / 420));

      const growLen = segLen * u;

      const sx = x1 + ux * rf;

      const sy = y1 + uy * rf;

      const ex = sx + ux * growLen;

      const ey = sy + uy * growLen;

      const flow = edgeSignal(this.opts.getGraph(), e.from.n.key, e.to.n.key, Date.now(), this.opts.getLive?.() === true);
      const active = flow !== null;
      traffic.push({from:e.from.n.key,to:e.to.n.key,kind:flow?.kind ?? null});

      const failed = e.to.n.status === "failed";

      const incident =

        (sel != null && (e.from.n.key === sel || e.to.n.key === sel)) ||

        (hov != null && (e.from.n.key === hov || e.to.n.key === hov));



      const grad = ctx.createLinearGradient(sx, sy, ex, ey);

      if (active) {

        grad.addColorStop(0, themeColors().edgeFaint);

        grad.addColorStop(1, flow.kind === "report" ? themeColors().round : themeColors().selection);

      } else if (failed) {

        grad.addColorStop(0, "rgba(244, 83, 95, 0.08)");

        grad.addColorStop(1, themeColors().failed);

      } else {

        const tc = themeColors();

        grad.addColorStop(0, tc.edgeFaint);

        grad.addColorStop(1, tc.edge);

      }

      ctx.globalAlpha = Math.min(1, 0.2 + 0.8 * u);

      ctx.beginPath();

      ctx.moveTo(sx, sy);

      ctx.lineTo(ex, ey);

      ctx.strokeStyle = grad;

      ctx.lineWidth = (active ? 3.2 : 2.4) * (incident ? 1.3 : 1);

      ctx.stroke();

      // hover/选中：相连边叠加一圈亮色描边（连接关系一目了然）

      if (incident) {

        ctx.strokeStyle = themeColors().selection;

        ctx.globalAlpha = 0.4;

        ctx.lineWidth = (active ? 3.2 : 2.4) + 1.6;

        ctx.stroke();

      }

      ctx.globalAlpha = 1;



      // 活跃边粒子流（沿直线；边长全出后再流动）

      if (active && u >= 1) {

        const p0 = e.to.n.key.length % 7;

        for (let i = 0; i < 2; i++) {

          const progress = (t * 0.45 + i * 0.5 + p0 * 0.13) % 1;
          const p = flow?.kind === "report" ? 1 - progress : progress;

          const px = sx + (ex - sx) * p;

          const py = sy + (ey - sy) * p;

          ctx.beginPath();

          ctx.arc(px, py, 2.2, 0, Math.PI * 2);

          ctx.fillStyle = flow.kind === "report" ? themeColors().round : themeColors().selection;

          ctx.shadowColor = ctx.fillStyle;

          ctx.shadowBlur = 6;

          ctx.fill();

          ctx.shadowBlur = 0;

        }

      }

    }

    // 清掉已消失的边的出生记录（防止 key 泄漏）

    Object.assign(this.canvas, {__adgsTraffic:traffic});

    for (const k of this.edgeBirth.keys()) if (!live.has(k)) this.edgeBirth.delete(k);

  }



  private drawNode(ctx: CanvasRenderingContext2D, l: LaidNode, t: number): void {

    const a = this.anim.get(l.n.key);

    if (!a) return;

    const { n } = l;

    const s = a.scale;

    const w = l.w * s, h = l.h * s;

    ctx.globalAlpha = a.opacity;



    const selected = this.opts.selected() === n.key;

    const hovered = this.hoverKey === n.key;

    const color =

      this.nodeColor(n);

    const active = n.status === "running" && n.type === "node" && this.opts.getLive?.() === true;

    const pulse = 0.5 + 0.5 * Math.sin(t * 3.2);



    // 本地坐标系：原点 = 节点中心，x 轴沿枝向（朝外）。

    // 注意：框架【不】随左右半圆翻转 π——否则状态色条/呼吸环会在跨过 90° 后

    // 左右互换（连线会接到"薄"的一端）。卡片本地 +x 恒为离心方向 ⇒

    // 状态色条(-w/2)恒在父端 = 连线进入侧，呼吸环(+w/2)恒在外侧端；

    // 需要摆正的只有文字，见各分支内的局部反旋转。

    ctx.save();

    ctx.translate(a.x, a.y);

    ctx.rotate(a.rot);

    const flipped = Math.cos(a.rot) < 0;



    // 光晕

    if (n.type === "ceo") {

      ctx.shadowColor = C.ceoGlow;

      ctx.shadowBlur = this.opts.getLive?.() === true && n.status === "running" ? 24 + pulse * 8 : 10;

    } else if (active) {

      ctx.shadowColor = color;

      ctx.shadowBlur = 14 + pulse * 10;

    } else if (n.status === "failed") {

      ctx.shadowColor = "rgba(244, 83, 95, 0.5)";

      ctx.shadowBlur = 10;

    }



    // 形状

    const tc = themeColors();

    const fillBg = tc.nodeBg;

    if (n.type === "ceo") {

      this.hexagon(ctx, 0, 0, w / 2, h / 2);

      ctx.fillStyle = fillBg;

      ctx.fill();

      ctx.strokeStyle = color;

      ctx.lineWidth = selected || hovered ? 2.4 : 1.6;

      ctx.stroke();

      ctx.shadowBlur = 0;

      ctx.fillStyle = color;

      ctx.font = `600 ${13 * s}px "Space Grotesk", sans-serif`;

      ctx.textAlign = "center";

      ctx.textBaseline = "middle";

      ctx.fillText("CEO", 0, -7 * s);

      ctx.fillStyle = tc.textDim;

      ctx.font = `${9.5 * s}px "Noto Sans SC", sans-serif`;

      // 副标题按六边形宽度截断(9 字 + …),防止长任务名压出光晕

      const rawTitle = n.extra.ceo_activity ? ceoPhaseLabel(n.extra.ceo_activity) : n.extra.task_title || "";

      const title = rawTitle.length > 9 ? rawTitle.slice(0, 9) + "…" : rawTitle || "唯一入口";

      ctx.fillText(title, 0, 10 * s);

    } else if (n.type === "round") {

      this.roundRect(ctx, -w / 2, -h / 2, w, h, 19);

      ctx.fillStyle = fillBg;

      ctx.fill();

      ctx.strokeStyle = color;

      ctx.lineWidth = selected || hovered ? 2 : 1.3;

      ctx.stroke();

      ctx.shadowBlur = 0;

      // 轮次文字保持水平：反旋转回世界系（居中锚点，任意枝向都永不倒置）；

      // 可用宽度 = 旋转后胶囊的世界水平跨度，超长自动缩字（"轮 12" 在近竖直方向也不出界）

      ctx.save();

      ctx.rotate(-a.rot);

      ctx.fillStyle = tc.round;

      ctx.font = `600 ${11.5 * s}px "Space Grotesk", sans-serif`;

      const roomW = (Math.abs(Math.cos(a.rot)) * w + Math.abs(Math.sin(a.rot)) * h) - 10 * s;

      const tw = ctx.measureText(n.label).width;

      if (tw > roomW) {

        ctx.font = `600 ${Math.max(8, (11.5 * roomW) / tw) * s}px "Space Grotesk", sans-serif`;

      }

      ctx.textAlign = "center";

      ctx.textBaseline = "middle";

      ctx.fillText(n.label, 0, 0);

      ctx.restore();

    } else if (n.type === "task") {

      ctx.beginPath();

      ctx.arc(0, 0, w / 2, 0, Math.PI * 2);

      ctx.fillStyle = fillBg;

      ctx.fill();

      ctx.strokeStyle = color;

      ctx.lineWidth = selected || hovered ? 2.2 : 1.4;

      ctx.stroke();

      ctx.shadowBlur = 0;

      // 序号数字保持水平：反旋转回世界系（圆旋转对称，任意枝向永不倒置）

      ctx.save();

      ctx.rotate(-a.rot);

      ctx.fillStyle = tc.task;

      ctx.font = `600 ${12.5 * s}px "JetBrains Mono", monospace`;

      ctx.textAlign = "center";

      ctx.textBaseline = "middle";

      ctx.fillText(n.label, 0, 0);

      ctx.restore();

    } else {

      // 执行节点实例

      this.roundRect(ctx, -w / 2, -h / 2, w, h, 11);

      ctx.fillStyle = active ? tc.nodeBgActive : fillBg;

      ctx.fill();

      ctx.strokeStyle = color;

      ctx.lineWidth = selected || hovered ? 2.2 : 1.3;

      ctx.stroke();

      ctx.shadowBlur = 0;



      // 状态色条（恒在父端 = 连线进入侧，即"厚"的一端；本地 -x 恒指向父节点）

      ctx.beginPath();

      ctx.moveTo(-w / 2 + 1, -h / 2 + 1);

      ctx.lineTo(-w / 2 + 4.5, -h / 2 + 1);

      ctx.lineTo(-w / 2 + 4.5, h / 2 - 1);

      ctx.lineTo(-w / 2 + 1, h / 2 - 1);

      ctx.closePath();

      ctx.fillStyle = color;

      ctx.fill();



      // 文字：在"摆正"的局部系里绘制（左半圆补转 π），任意枝向永不倒置。

      // 右半圆左对齐、左半圆右对齐——都从状态条这一端（父端）起排，读向卡内。

      const padX = 12 * s;

      ctx.save();

      if (flipped) ctx.rotate(Math.PI);

      ctx.textAlign = flipped ? "right" : "left";

      ctx.textBaseline = "middle";

      const tx = flipped ? w / 2 - padX : -w / 2 + padX;

      // 名字

      ctx.fillStyle = tc.text;

      ctx.font = `600 ${12 * s}px "JetBrains Mono", monospace`;

      ctx.fillText(n.label, tx, -9 * s);

      // 状态行

      ctx.font = `${10 * s}px "Noto Sans SC", sans-serif`;

      ctx.fillStyle = color;

      let sub = statusText(n.status);

      if (n.extra.mode === "clone") sub = `克隆 · ${sub}`;

      if (n.extra.score != null) sub += ` · ${n.extra.score.toFixed(1)}分`;

      if (n.status === "running") {

        // 真实执行时长：起点 = 转入 running 的服务端时间戳（事件 ts，重连后

        // 随事件重放恢复，不归零），回退实例创建时间。此前用动画时钟

        // t*2%60 —— 每 30 秒循环归零、刷新即归零的假秒表（2026-09-27 用户报告）

        const start = n.extra.runTs ?? n.bornTs;

        if (active && start) sub += ` · ${fmtDuration((Date.now() - start) / 1000)}`;

      }

      ctx.fillText(sub.slice(0, 22), tx, 9 * s);

      ctx.restore();



      // 运行中：右侧呼吸环

      if (n.status === "running") {

        const rr = 5.5 + pulse * 2.5;

        ctx.beginPath();

        ctx.arc(w / 2 - 11 * s, 0, rr, 0, Math.PI * 2);

        ctx.strokeStyle = color;

        ctx.lineWidth = 1.6;

        ctx.stroke();

        ctx.beginPath();

        ctx.arc(w / 2 - 11 * s, 0, 2, 0, Math.PI * 2);

        ctx.fillStyle = color;

        ctx.fill();

      }

    }



    // 选中描边

    if (selected) {

      this.roundRect(ctx, -w / 2 - 4, -h / 2 - 4, w + 8, h + 8, 13);

      ctx.strokeStyle = themeColors().selection;

      ctx.lineWidth = 1.4;

      ctx.setLineDash([5, 4]);

      ctx.stroke();

      ctx.setLineDash([]);

    }

    ctx.restore();

    ctx.globalAlpha = 1;

  }



  private hexagon(ctx: CanvasRenderingContext2D, cx: number, cy: number, rx: number, ry: number): void {

    ctx.beginPath();

    for (let i = 0; i < 6; i++) {

      const ang = (Math.PI / 3) * i - Math.PI / 2;

      const px = cx + Math.cos(ang) * rx;

      const py = cy + Math.sin(ang) * ry;

      if (i === 0) ctx.moveTo(px, py);

      else ctx.lineTo(px, py);

    }

    ctx.closePath();

  }

}



export function statusText(status: string): string { return GRAPH_STATUS[graphStatus(status)].label; }
export { splitInstKey };
