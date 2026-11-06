import { describe, it, expect, vi } from "vitest";
import { GraphCanvas, layoutTree } from "@/lib/graphCanvas";
import { graphInit, type A2aEdge } from "@/lib/graph";

function fixture() {
  const graph = graphInit();
  graph.nodes.push({ key: "n@t", id: "n", parent: "ceo", type: "node", label: "n", status: "done", extra: {} });
  graph.a2aEdges = [
    { delegationId: "1", fromNodeId: "n", toNodeId: "v", status: "passed", summary: "" },
    { delegationId: "2", fromNodeId: "v", toNodeId: "n", status: "passed", summary: "" },
    { delegationId: "3", fromNodeId: "n", toNodeId: "w", status: "passed", summary: "" },
  ] as A2aEdge[];
  const ctx = new Proxy({ measureText: () => ({ width: 20 }) }, { get: (obj, k) => k in obj ? obj[k as keyof typeof obj] : () => {} });
  const canvas = document.createElement("canvas");
  canvas.getContext = vi.fn(() => ctx) as any;
  canvas.setPointerCapture = vi.fn();
  let manual = new Map();
  const engine = new GraphCanvas(canvas, { getGraph: () => graph, getA2aEdges: () => graph.a2aEdges,
    selected: () => null, handlers: {}, getManual: () => manual, onManualChange: m => { manual = m; } });
  // Exercise real engine geometry/handlers without RAF timing.
  const e = engine as any;
  e.laid = layoutTree(graph).nodes;
  e.anim = new Map(e.laid.map((l: any) => [l.n.key, { ...l, scale: 1, opacity: 1 }]));
  e.view = { x: 0, y: 0, k: 1 };
  const draw = () => e.drawA2aEdges(ctx, 0);
  const event = (x: number, y: number) => ({ clientX: x, clientY: y, pointerId: 1 });
  return { e, draw, graph, event, engine, canvas, manual: () => manual };
}

describe("A2A shared world geometry", () => {
  it("layout transition first frame may precede animation entries", () => {
    const f = fixture(); f.e.anim.clear();
    expect(() => f.draw()).not.toThrow();
    expect(f.e.a2aEdgeGeom).toHaveLength(3);
    f.engine.destroy();
  });
  it("pan/zoom cannot change world coordinates; shared peers have one anchor", () => {
    const f = fixture(); f.draw();
    const before = [...f.e.a2aVirtual];
    expect(before).toHaveLength(2);
    f.e.view = { x: 230, y: -160, k: .3 }; f.draw();
    expect([...f.e.a2aVirtual]).toEqual(before);
    f.engine.destroy();
  });
  it("all automatic anchors are draggable; offsets survive a canvas remount and tidy clears them", () => {
    const f = fixture(); f.draw();
    const p = f.e.a2aVirtual.get("v");
    expect(f.e.hitA2aAnchor(p.x, p.y)).toBe("v");
    f.e.onDown(f.event(p.x, p.y)); f.e.onMove(f.event(p.x + 90, p.y + 40)); f.draw();
    f.e.onUp(f.event(p.x + 90, p.y + 40)); f.draw();
    expect(f.manual().get("a2a:v")).toEqual({ dx: 90, dy: 40 });
    expect(f.e.a2aVirtual.get("v")).toEqual({ x: p.x + 90, y: p.y + 40 });
    f.e.a2aVirtual.clear(); f.draw();
    expect(f.e.a2aVirtual.get("v").x).toBeCloseTo(p.x + 90);
    f.manual().clear(); f.engine.resetA2aAnchors(); f.draw();
    expect(f.e.a2aVirtual.get("v")).toEqual(p);
    f.engine.destroy();
  });
  it("opposite directions remain distinct and every curve is clickable between former 1/8 samples", () => {
    const f = fixture(); f.draw();
    const [a,b] = f.e.a2aEdgeGeom;
    expect(a.cp).not.toEqual(b.cp);
    for (const geom of f.e.a2aEdgeGeom) {
      const t = .437, u = 1-t;
      const x = u*u*geom.p0.x + 2*u*t*geom.cp.x + t*t*geom.p1.x;
      const y = u*u*geom.p0.y + 2*u*t*geom.cp.y + t*t*geom.p1.y;
      expect(f.e.hitA2aEdge(x, y)).toBe(geom.delegationId);
    }
    f.engine.destroy();
  });
  it("dense peers are outside every tree shape, ordered and separated", () => {
    const f = fixture();
    f.graph.a2aEdges = Array.from({length: 24}, (_,i) => ({delegationId: String(i),fromNodeId: "n",toNodeId: `v${i}`,status: "running",summary: ""}));
    f.draw(); const points = [...f.e.a2aVirtual.values()] as {x:number;y:number}[];
    for (let i=0;i<points.length;i++) {
      for (let j=i+1;j<points.length;j++) expect(Math.hypot(points[i].x-points[j].x,points[i].y-points[j].y)).toBeGreaterThan(110);
      for (const l of f.e.laid) expect(Math.hypot(points[i].x-l.x,points[i].y-l.y)).toBeGreaterThan(Math.hypot(l.w,l.h)/2+90);
    }
    f.engine.destroy();
  });
  it("diagonal tree edges intersect a rotated rectangle instead of its projection", () => {
    const f = fixture();
    const l = {...f.e.laid.find((l:any)=>l.n.type==='node'),rot:Math.PI/2};
    const u = Math.SQRT1_2;
    const d = f.e.shapeSupport(l,1,l.rot,u,u);
    expect(d).toBeLessThan(34); // short side 23 / sin(45°), old projection was 69px
    expect(d).toBeGreaterThan(30);
    f.engine.destroy();
  });
});
