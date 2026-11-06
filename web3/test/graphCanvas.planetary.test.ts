// 临时验证：行星（楔形星系密排 + 节点随枝向旋转）布局的几何性质
//  - 无节点矩形重叠（SAT，旋转感知）
//  - 全叶子子沿枝向径向堆叠（radii 递增）
//  - 深树（多轮 + 多任务 + 实例）运行稳定
import { describe, it, expect } from "vitest";
import { layoutTree, applyManualOffsets } from "@/lib/graphCanvas";
import { CEO_KEY, type GraphState } from "@/lib/graph";
import type { GraphNode } from "@/types";

function makeGraph(nodes: GraphNode[]): GraphState {
  return { nodes, roundSeq: 0, currentRound: null, a2aEdges: [] };
}

function leaf(type: GraphNode["type"], key: string, parent: string, label = ""): GraphNode {
  return { key, id: key, type, label: label || key, parent, status: "done", extra: {} };
}

interface RectLike {
  x: number;
  y: number;
  w: number;
  h: number;
  rot: number;
}

/** 两个旋转矩形是否重叠（分离轴定理，容差 1.5px 视为接触） */
function rectsOverlap(a: RectLike, b: RectLike, tol = 1.5): boolean {
  const cornersOf = (r: RectLike): [number, number][] => {
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

describe("planetary layoutTree", () => {
  it("单轮小图不重叠、全叶子子径向堆叠", () => {
    const nodes: GraphNode[] = [
      { key: CEO_KEY, id: "ceo", type: "ceo", label: "CEO", parent: null, status: "idle", extra: {} },
      leaf("round", "round-1", CEO_KEY),
      leaf("task", "t1", "round-1"),
      leaf("node", "ags_1@t1", "t1"),
      leaf("node", "ags_2@t1", "t1"),
      leaf("node", "ags_3@t1", "t1"),
    ];
    const laid = layoutTree(makeGraph(nodes)).nodes;
    expect(laid.length).toBe(nodes.length);
    // 无重叠
    for (let i = 0; i < laid.length; i++)
      for (let j = i + 1; j < laid.length; j++)
        expect(rectsOverlap(laid[i], laid[j]), `overlap ${laid[i].n.key} ↔ ${laid[j].n.key}`).toBe(false);
    // 三个实例为 t1 的全叶子子 → 径向堆叠：同一方向、极半径严格递增
    const byKey = new Map(laid.map((l) => [l.n.key, l]));
    const t1 = byKey.get("t1")!;
    const insts = ["ags_1@t1", "ags_2@t1", "ags_3@t1"].map((k) => byKey.get(k)!);
    const angs = insts.map((l) => Math.atan2(l.y - t1.y, l.x - t1.x));
    for (const a of angs) expect(Math.abs(a - angs[0])).toBeLessThan(1e-6);
    const dists = insts.map((l) => Math.hypot(l.x - t1.x, l.y - t1.y));
    expect(dists[1]).toBeGreaterThan(dists[0]);
    expect(dists[2]).toBeGreaterThan(dists[1]);
    const ceo = byKey.get(CEO_KEY)!;
    expect(Math.hypot(t1.x - ceo.x, t1.y - ceo.y)).toBeGreaterThan(50); // round 链与 CEO 分离
  });

  it("多轮多任务深树不重叠", () => {
    const nodes: GraphNode[] = [
      { key: CEO_KEY, id: "ceo", type: "ceo", label: "CEO", parent: null, status: "idle", extra: {} },
    ];
    // 4 轮，每轮 1-3 task，每个 task 1-4 实例，有的实例带下级 task+node
    for (let r = 1; r <= 4; r++) {
      const rk = `round-${r}`;
      nodes.push(leaf("round", rk, CEO_KEY));
      const nTasks = 1 + (r % 3);
      for (let t = 1; t <= nTasks; t++) {
        const tk = `t${r}_${t}`;
        nodes.push(leaf("task", tk, rk));
        const nInst = 1 + ((r + t) % 4);
        for (let k = 1; k <= nInst; k++) {
          const ik = `ags_${k}@${tk}`;
          nodes.push(leaf("node", ik, tk));
          // 约一半实例带下级委派（再深一层）
          if ((r + t + k) % 2 === 0) {
            const sub = `sub-${r}-${t}-${k}`;
            nodes.push(leaf("task", sub, ik));
            nodes.push(leaf("node", `deep@${sub}`, sub));
          }
        }
      }
    }
    const laid = layoutTree(makeGraph(nodes)).nodes;
    expect(laid.length).toBe(nodes.length);
    let overlaps = 0;
    let worst: string[] = [];
    for (let i = 0; i < laid.length; i++)
      for (let j = i + 1; j < laid.length; j++) {
        if (rectsOverlap(laid[i], laid[j])) {
          overlaps++;
          if (worst.length < 4) worst.push(`${laid[i].n.key} ↔ ${laid[j].n.key}`);
        }
      }
    expect(overlaps, `overlapping pairs: ${worst.join("; ")}`).toBe(0);
  });

  it("任意父节点的子节点不互相重叠（最小不重叠角语义）", () => {
    const nodes: GraphNode[] = [
      { key: CEO_KEY, id: "ceo", type: "ceo", label: "CEO", parent: null, status: "idle", extra: {} },
    ];
    for (let r = 1; r <= 5; r++) {
      const rk = `round-${r}`;
      nodes.push(leaf("round", rk, CEO_KEY));
      for (let t = 1; t <= 3; t++) {
        const tk = `${rk}-t${t}`;
        nodes.push(leaf("task", tk, rk));
        for (let k = 1; k <= 2; k++) nodes.push(leaf("node", `n${k}@${tk}`, tk));
      }
    }
    const laid = layoutTree(makeGraph(nodes)).nodes;
    const byKey = new Map(laid.map((l) => [l.n.key, l]));
    const kids = new Map<string, typeof laid>();
    for (const l of laid) {
      if (!l.n.parent) continue;
      const p = byKey.get(l.n.parent!) || byKey.get(CEO_KEY)!;
      const arr = kids.get(p.n.key) ?? [];
      arr.push(l);
      kids.set(p.n.key, arr);
    }
    // 同一父的任意两子不重叠（行星环最小不重叠角 → 矩形不相交）
    for (const [pk, children] of kids) {
      if (children.length < 2) continue;
      for (let i = 0; i < children.length; i++)
        for (let j = i + 1; j < children.length; j++)
          expect(
            rectsOverlap(children[i], children[j]),
            `parent ${pk} 的子节点 ${children[i].n.key} ↔ ${children[j].n.key} 不应重叠`,
          ).toBe(false);
    }
  });

  it("手动拖拽：拖父节点 → 整棵子树跟随；拖子节点 → 只该子树动", () => {
    const nodes: GraphNode[] = [
      { key: CEO_KEY, id: "ceo", type: "ceo", label: "CEO", parent: null, status: "idle", extra: {} },
      leaf("round", "round-1", CEO_KEY),
      leaf("task", "t1", "round-1"),
      leaf("node", "n1@t1", "t1"),
      leaf("task", "t2", "round-1"),
      leaf("node", "n2@t2", "t2"),
    ];
    const g = makeGraph(nodes);
    const base = layoutTree(g).nodes;
    const posOf = (key: string, laid: { n: GraphNode; x: number; y: number }[]) =>
      laid.find((l) => l.n.key === key)!;

    // 拖 round-1 整体 +300,+200 → round-1/t1/n1@t1/t2/n2@t2 全跟随
    const manual1 = new Map<string, { dx: number; dy: number }>([["round-1", { dx: 300, dy: 200 }]]);
    const laid1 = applyManualOffsets(base, manual1);
    for (const k of ["round-1", "t1", "n1@t1", "t2", "n2@t2"]) {
      const b = posOf(k, base), m = posOf(k, laid1);
      expect(m.x - b.x, `${k} x 应随 round-1 拖动 +300`).toBeCloseTo(300, 6);
      expect(m.y - b.y, `${k} y 应随 round-1 拖动 +200`).toBeCloseTo(200, 6);
    }
    expect(posOf("ceo", laid1).x - posOf("ceo", base).x).toBeCloseTo(0, 6); // CEO 不动

    // 拖 t1 子树（相对其已随 round-1 移动的位置）→ 只 t1/n1@t1 额外动
    const manual2 = new Map<string, { dx: number; dy: number }>([
      ["round-1", { dx: 300, dy: 200 }],
      ["t1", { dx: -80, dy: 60 }],
    ]);
    const laid2 = applyManualOffsets(base, manual2);
    const dt1x = posOf("t1", laid2).x - posOf("t1", base).x;
    expect(dt1x).toBeCloseTo(220, 6); // 300-80
    const dn1x = posOf("n1@t1", laid2).x - posOf("n1@t1", base).x;
    expect(dn1x).toBeCloseTo(220, 6); // 孙跟随 t1 的叠加偏移
    const dt2x = posOf("t2", laid2).x - posOf("t2", base).x;
    expect(dt2x).toBeCloseTo(300, 6); // t2 只在 round-1 偏移下
  });
});
