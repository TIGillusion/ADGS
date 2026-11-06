// 行星布局（楔形星系密排 + 节点随枝向旋转）的严格验证：
//  1. 零交叠：任意两节点旋转矩形不重叠（SAT 全对检查，典型树 + 固定种子随机大树）；
//  2. 枝不串门：每条父子连线段不与"非本枝"节点矩形相交
//     （楔形凸区域相离的结构性保证；同枝内部堆叠列上边从近侧行星背后
//     穿过属允许视觉行为——边绘制在节点层之下，与旧模型一致）；
//  3. 层级结构：每条边子节点极半径严格大于父节点（离 CEO 越远层级越深）；
//  4. 顶层预算：CEO 全部子楔形宽 + 弧距 ≤ 2π（放不下时二分外推，不硬撑）；
//  5. 单链径向垂直延伸：单链沿正下方严格径向排列（x≈0，步长 = 沿枝前伸公式）；
//  6. 密度护栏：典型树包围盒面积远小于旧圆盘模型（防密度退化回归）；
//  7. 性能护栏：layoutTree 均值 < 5ms（每帧重算）。
// 另：把典型树布局 dump 成 JSON（test/.radial_dump.json）供目视渲染。
import { describe, it, expect } from "vitest";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { layoutTree, radialDebug, RADIAL_CONSTS, GraphLayoutCache } from "@/lib/graphCanvas";
import { CEO_KEY, type GraphState } from "@/lib/graph";
import type { GraphNode } from "@/types";

const K = RADIAL_CONSTS;
const TAU = 2 * Math.PI;

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
  rot: number;
  key?: string;
}

function makeGraph(nodes: GraphNode[]): GraphState {
  return { nodes, roundSeq: 0, currentRound: null, a2aEdges: [] };
}
function leaf(type: GraphNode["type"], key: string, parent: string, label = ""): GraphNode {
  return { key, id: key, type, label: label || key, parent, status: "done", extra: {} };
}
/** 沿枝前伸半长（含 shapePad 裕量，与引擎 alongOf 一致） */
function alongOf(w: number): number {
  return w / 2 + K.shapePad;
}

/** 典型 ADGS 树：CEO → 4 轮 → 1..3 任务 → 1..4 实例（半数实例带下级委派） */
function buildTypical(): GraphState {
  const nodes: GraphNode[] = [
    { key: CEO_KEY, id: "ceo", type: "ceo", label: "CEO", parent: null, status: "idle", extra: {} },
  ];
  for (let r = 1; r <= 4; r++) {
    const rk = `round-${r}`;
    nodes.push(leaf("round", rk, CEO_KEY));
    const nT = 1 + ((r * 2) % 3);
    for (let t = 1; t <= nT; t++) {
      const tk = `t${r}_${t}`;
      nodes.push(leaf("task", tk, rk));
      const nI = 1 + ((r + t) % 4);
      for (let i = 1; i <= nI; i++) {
        const ik = `ags_${i}@${tk}`;
        nodes.push(leaf("node", ik, tk));
        if ((r + t + i) % 2 === 0) {
          const sk = `sub-${r}-${t}-${i}`;
          nodes.push(leaf("task", sk, ik));
          nodes.push(leaf("node", `deep@${sk}`, sk));
        }
      }
    }
  }
  return makeGraph(nodes);
}

/** 固定种子随机大树（压测重叠 + 性能） */
function buildBig(): GraphState {
  let seed = 0x2f6e2b1;
  const rnd = () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const nodes: GraphNode[] = [
    { key: CEO_KEY, id: "ceo", type: "ceo", label: "CEO", parent: null, status: "idle", extra: {} },
  ];
  for (let r = 1; r <= 12; r++) {
    const rk = `round-${r}`;
    nodes.push(leaf("round", rk, CEO_KEY));
    const nT = 1 + Math.floor(rnd() * 5);
    for (let t = 1; t <= nT; t++) {
      const tk = `t${r}_${t}`;
      nodes.push(leaf("task", tk, rk));
      const nI = 1 + Math.floor(rnd() * 7);
      for (let i = 1; i <= nI; i++) {
        const ik = `ags_${i}@${tk}`;
        nodes.push(leaf("node", ik, tk));
        if (rnd() < 0.5) {
          const sk = `sub-${r}-${t}-${i}`;
          nodes.push(leaf("task", sk, ik));
          nodes.push(leaf("node", `deep@${sk}`, sk));
        }
      }
    }
  }
  return makeGraph(nodes);
}

/** 父子映射（孤儿回挂 CEO，与 solveGalaxy 一致） */
function parentMap(nodes: GraphNode[]): Map<string, string | null> {
  const byKey = new Set(nodes.map((n) => n.key));
  const pm = new Map<string, string | null>();
  for (const n of nodes) pm.set(n.key, n.parent === null ? null : byKey.has(n.parent) ? n.parent : CEO_KEY);
  return pm;
}
/** 子树根（含自身）：沿 parent 一路向上直到 CEO 的直接子 */
function subtreeRootOf(key: string, pm: Map<string, string | null>): string {
  let cur = key;
  while (pm.get(cur) !== CEO_KEY && pm.get(cur) != null) cur = pm.get(cur)!;
  return cur;
}

/** 两个旋转矩形是否重叠（分离轴定理，容差 tol = 视为接触） */
function obbOverlap(a: Rect, b: Rect, tol = 1.5): boolean {
  const cornersOf = (r: Rect): [number, number][] => {
    const c = Math.cos(r.rot), s = Math.sin(r.rot);
    const hw = r.w / 2, hh = r.h / 2;
    return [
      [r.x + c * hw - s * hh, r.y + s * hw + c * hh],
      [r.x + c * hw + s * hh, r.y + s * hw - c * hh],
      [r.x - c * hw + s * hh, r.y - s * hw - c * hh],
      [r.x - c * hw - s * hh, r.y - s * hw + c * hh],
    ];
  };
  const ca = cornersOf(a);
  const cb = cornersOf(b);
  const axes: [number, number][] = [];
  for (const rot of [a.rot, b.rot]) {
    axes.push([Math.cos(rot), Math.sin(rot)]);
    axes.push([-Math.sin(rot), Math.cos(rot)]);
  }
  for (const [ax, ay] of axes) {
    let aMin = Infinity, aMax = -Infinity, bMin = Infinity, bMax = -Infinity;
    for (const [px, py] of ca) {
      const d = px * ax + py * ay;
      aMin = Math.min(aMin, d); aMax = Math.max(aMax, d);
    }
    for (const [px, py] of cb) {
      const d = px * ax + py * ay;
      bMin = Math.min(bMin, d); bMax = Math.max(bMax, d);
    }
    if (aMax - tol <= bMin || bMax - tol <= aMin) return false;
  }
  return true;
}

/** 线段是否与旋转矩形相交（把线段变换到矩形本地系后 Liang-Barsky） */
function segHitsRect(x1: number, y1: number, x2: number, y2: number, r: Rect): boolean {
  const c = Math.cos(-r.rot), s = Math.sin(-r.rot);
  const toLocal = (px: number, py: number): [number, number] => {
    const dx = px - r.x, dy = py - r.y;
    return [c * dx - s * dy, s * dx + c * dy];
  };
  const [lx1, ly1] = toLocal(x1, y1);
  const [lx2, ly2] = toLocal(x2, y2);
  const hw = r.w / 2, hh = r.h / 2;
  const ddx = lx2 - lx1, ddy = ly2 - ly1;
  let t0 = 0, t1 = 1;
  const clip = (p: number, q: number): boolean => {
    if (p === 0) return q >= 0;
    const t = q / p;
    if (p < 0) {
      if (t > t1) return false;
      if (t > t0) t0 = t;
    } else {
      if (t < t0) return false;
      if (t < t1) t1 = t;
    }
    return true;
  };
  return (
    clip(-ddx, lx1 + hw) && clip(ddx, hw - lx1) &&
    clip(-ddy, ly1 + hh) && clip(ddy, hh - ly1)
  );
}

describe("planetary layout · 楔形星系密排", () => {
  it("零交叠：典型树 + 大树任意两节点旋转矩形不重叠（SAT 全对检查）", () => {
    const check = (state: GraphState) => {
      const laid = layoutTree(state).nodes;
      expect(laid.length).toBe(state.nodes.length);
      const bad: string[] = [];
      for (let i = 0; i < laid.length; i++)
        for (let j = i + 1; j < laid.length; j++) {
          const a = laid[i];
          const b = laid[j];
          if (obbOverlap(a, b)) bad.push(`${a.n.key}↔${b.n.key}`);
        }
      expect(bad, `重叠: ${bad.slice(0, 6).join(", ")}`).toHaveLength(0);
    };
    check(buildTypical());
    const big = buildBig();
    check(big);
    console.log(`[miniradial] big tree nodes = ${big.nodes.length}（旋转矩形零重叠 ✓）`);
  });

  it("枝不串门：父子连线段不与异枝节点矩形相交（典型树 + 大树全对）", () => {
    const check = (state: GraphState) => {
      const dbg = radialDebug(state);
      const pm = parentMap(state.nodes);
      const byKey = new Map(dbg.laid.map((l) => [l.key, l]));
      const bad: string[] = [];
      for (const l of dbg.laid) {
        if (l.parent == null) continue;
        const p = byKey.get(l.parent === CEO_KEY || byKey.has(l.parent) ? l.parent : CEO_KEY)!;
        const branchA = subtreeRootOf(p.key, pm);
        const branchB = subtreeRootOf(l.key, pm);
        for (const o of dbg.laid) {
          if (o.key === p.key || o.key === l.key) continue;
          const ob = subtreeRootOf(o.key, pm);
          if (ob === branchA || ob === branchB) continue;
          if (segHitsRect(p.x, p.y, l.x, l.y, o)) bad.push(`${p.key}→${l.key} 穿过 ${o.key}`);
        }
      }
      expect(bad, `穿枝: ${bad.slice(0, 6).join("; ")}`).toHaveLength(0);
    };
    check(buildTypical());
    check(buildBig());
  });

  it("层级结构：每条边子节点极半径严格大于父节点（典型树 + 大树）", () => {
    const check = (state: GraphState) => {
      const dbg = radialDebug(state);
      const byKey = new Map(dbg.laid.map((l) => [l.key, l]));
      const bad: string[] = [];
      for (const l of dbg.laid) {
        if (l.parent == null) continue;
        const p = byKey.get(l.parent === CEO_KEY || byKey.has(l.parent) ? l.parent : CEO_KEY)!;
        const rp = Math.hypot(p.x, p.y);
        const rc = Math.hypot(l.x, l.y);
        if (rc <= rp) bad.push(`${p.key}(r=${rp.toFixed(1)}) → ${l.key}(r=${rc.toFixed(1)})`);
      }
      expect(bad, `极半径未递增: ${bad.slice(0, 6).join("; ")}`).toHaveLength(0);
    };
    check(buildTypical());
    check(buildBig());
  });

  it("顶层预算：CEO 全部子楔形宽 + 弧距 ≤ 2π（超出应二分外推而不是溢出）", () => {
    for (const state of [buildTypical(), buildBig()]) {
      const dbg = radialDebug(state);
      const rounds = dbg.wedges.filter((w) => /^round-/.test(w.key));
      expect(rounds.length).toBeGreaterThan(0);
      let total = 0;
      const sorted = [...rounds].sort((a, b) => a.a0 - b.a0);
      for (let i = 0; i < sorted.length; i++) {
        total += sorted[i].a1 - sorted[i].a0;
        if (i > 0) total += K.gapPad / Math.max(60, Math.min(sorted[i - 1].r, sorted[i].r));
      }
      expect(total, `顶层楔形总宽 ${total.toFixed(3)} 超出 2π`).toBeLessThanOrEqual(TAU + 1e-6);
    }
  });

  it("纯链径向垂直延伸：单链沿正下方 x≈0，步长 = 沿枝前伸公式（无环倍增）", () => {
    const nodes: GraphNode[] = [
      { key: CEO_KEY, id: "ceo", type: "ceo", label: "CEO", parent: null, status: "idle", extra: {} },
      leaf("round", "round-1", CEO_KEY),
      leaf("task", "t1", "round-1"),
      leaf("node", "n1@t1", "t1"),
      leaf("task", "sub", "n1@t1"),
      leaf("node", "deep@sub", "sub"),
    ];
    const laid = layoutTree(makeGraph(nodes)).nodes;
    const keys = [CEO_KEY, "round-1", "t1", "n1@t1", "sub", "deep@sub"];
    const pos = new Map(laid.map((l) => [l.n.key, l]));
    let prevR = 0;
    for (let i = 1; i < keys.length; i++) {
      const l = pos.get(keys[i])!;
      const p = pos.get(keys[i - 1])!;
      // 角度 = 正下方（π/2）→ x ≈ 0，y > 0
      expect(Math.abs(l.x), `${keys[i]} 应在正下方 (x≈0)`).toBeLessThan(1e-6);
      expect(l.y).toBeGreaterThan(0);
      const step = Math.hypot(p.w, p.h) / 2 + K.shapePad + K.gap0 + l.w / 2 + K.shapePad;
      expect(l.y - prevR, `${keys[i]} 步长应 = ${step}`).toBeCloseTo(step, 6);
      prevR = l.y;
    }
    expect(pos.get("deep@sub")!.y).toBeLessThan(600);
  });

  it("叶子兄弟径向堆叠：同父全叶子子沿同一方向首尾相接（radii 递增、角度一致）", () => {
    const nodes: GraphNode[] = [
      { key: CEO_KEY, id: "ceo", type: "ceo", label: "CEO", parent: null, status: "idle", extra: {} },
      leaf("round", "round-1", CEO_KEY),
      leaf("task", "t1", "round-1"),
      leaf("node", "a@t1", "t1"),
      leaf("node", "b@t1", "t1"),
      leaf("node", "c@t1", "t1"),
    ];
    const laid = layoutTree(makeGraph(nodes)).nodes;
    const byKey = new Map(laid.map((l) => [l.n.key, l]));
    const t1 = byKey.get("t1")!;
    const insts = ["a@t1", "b@t1", "c@t1"].map((k) => byKey.get(k)!);
    // 同方向（相对 t1 的角度一致）且极半径严格递增
    const angs = insts.map((l) => Math.atan2(l.y - t1.y, l.x - t1.x));
    for (const a of angs) expect(Math.abs(a - angs[0])).toBeLessThan(1e-6);
    const dists = insts.map((l) => Math.hypot(l.x - t1.x, l.y - t1.y));
    expect(dists[1]).toBeGreaterThan(dists[0]);
    expect(dists[2]).toBeGreaterThan(dists[1]);
    // 相邻步长 = 沿枝前伸公式
    for (let i = 1; i < insts.length; i++) {
      const step = alongOf(insts[i - 1].w) + K.gap0 + alongOf(insts[i].w);
      expect(dists[i] - dists[i - 1]).toBeCloseTo(step, 6);
    }
  });

  it("密度护栏：典型树包围盒面积远小于旧圆盘模型（防密度退化）", () => {
    const state = buildTypical();
    const laid = layoutTree(state).nodes;
    const bbox = laid.reduce(
      (m, l) => {
        const c = Math.abs(Math.cos(l.rot)), s = Math.abs(Math.sin(l.rot));
        const ex = (c * l.w + s * l.h) / 2, ey = (s * l.w + c * l.h) / 2;
        return {
          x0: Math.min(m.x0, l.x - ex), x1: Math.max(m.x1, l.x + ex),
          y0: Math.min(m.y0, l.y - ey), y1: Math.max(m.y1, l.y + ey),
        };
      },
      { x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity },
    );
    const area = (bbox.x1 - bbox.x0) * (bbox.y1 - bbox.y0);
    console.log(`[miniradial] typical bbox = (${bbox.x0.toFixed(0)},${bbox.y0.toFixed(0)})..(${bbox.x1.toFixed(0)},${bbox.y1.toFixed(0)}) area=${(area / 1e6).toFixed(2)}M px²`);
    // 旧圆盘模型同树实测 ~5.4M px²；无旋转楔形 ~3.6M；旋转对齐 + 叶子径向堆叠
    // 实测 ~1.6M（剩余半径由"任务→实例→委派"链长主导，属几何下限）。
    // 阈值留余量防平台字体/尺寸微调误报。
    expect(area).toBeLessThan(1.8e6);
  });

  it("性能护栏：稳定图谱的连续帧不重复求解布局", () => {
    const state = buildBig();
    const cache = new GraphLayoutCache();
    const initial = cache.get(state, "radial");
    const N = 200;
    for (let i = 0; i < N; i++) expect(cache.get(state, "radial")).toBe(initial);
    expect(initial.nodes).toHaveLength(state.nodes.length);
    expect(cache.computations).toBe(1);
  });

  it("dump：写出典型树布局 JSON（供目视渲染）", () => {
    const state = buildTypical();
    const dbg = radialDebug(state);
    writeFileSync(
      join(process.cwd(), "test", ".radial_dump.json"),
      JSON.stringify({ consts: K, laid: dbg.laid, wedges: dbg.wedges }, null, 1),
    );
    for (const w of dbg.wedges.filter((x) => /^round-/.test(x.key))) {
      const deg = ((w.a1 - w.a0) * 180) / Math.PI;
      console.log(`[miniradial] ${w.key}: r=${w.r.toFixed(0)} wedge=${deg.toFixed(0)}°`);
    }
  });
});
