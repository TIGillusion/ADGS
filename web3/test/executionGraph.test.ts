import { describe, expect, it } from "vitest";
import { applyEventToGraph, graphInit, graphFromSaved } from "@/lib/graph";
import { GraphLayoutCache } from "@/lib/graphCanvas";
import type { BusEvent } from "@/types";

const event = (type: string, data: Record<string, unknown>): BusEvent => ({ type, ts: 100, data });
describe("explicit execution identity", () => {
  it("keeps two executions sharing node, task and profile independent", () => {
    let graph = graphInit();
    for (const id of ["e1", "e2"]) graph = applyEventToGraph(graph, event("graph.dispatch", { node: "ags_1", task: "t1", execution_id: id, profile: "ags_1" }), 100000);
    graph = applyEventToGraph(graph, event("node.eval", { node: "ags_1", execution_id: "e1", passed: true, score: 90 }), 100000);
    expect(graph.nodes.find((n) => n.key === "ags_1@e1")?.status).toBe("done");
    expect(graph.nodes.find((n) => n.key === "ags_1@e2")?.status).toBe("waiting");
    const previous = graph;
    graph = applyEventToGraph(graph, event("node.eval", { node: "ags_1", execution_id: "missing", passed: false }), 100000);
    expect(graph).toBe(previous);
  });

  it("does not label infrastructure/protocol evaluation failures as task failures", () => {
    let graph = applyEventToGraph(graphInit(), event("graph.dispatch", { node: "ags_1", task: "t1", execution_id: "e1" }), 100000);
    graph = applyEventToGraph(graph, event("node.eval", { node: "ags_1", execution_id: "e1", passed: false, unjudged: true }), 100000);
    expect(graph.nodes.find((n) => n.extra.execution_id === "e1")?.status).toBe("unjudged");
    graph = applyEventToGraph(graph, event("node.status", { node: "ags_1", execution_id: "e1", status: "interrupted", infra: true, passed: false }), 100000);
    expect(graph.nodes.find((n) => n.extra.execution_id === "e1")?.extra.passed).toBeUndefined();
    const restored = graphFromSaved([{ key: "ags_1@e1", id: "ags_1", type: "node", label: "ags_1", parent: "ceo", status: "running", extra: { execution_id: "e1" } }]);
    expect(restored.nodes[1].status).toBe("unknown");
    expect(restored.nodes[1].extra.execution_id).toBe("e1");
  });
});

describe("layout geometry cache", () => {
  it("does not recompute geometry for status or evidence changes", () => {
    const cache = new GraphLayoutCache();
    let graph = graphInit();
    cache.get(graph, "radial");
    for (let i = 0; i < 50; i++) {
      graph = { ...graph, nodes: graph.nodes.map((n) => ({ ...n, status: "running", extra: { score: i } })) };
      const layout = cache.get(graph, "radial");
      expect(layout.nodes[0].n.extra.score).toBe(i);
    }
    expect(cache.computations).toBe(1);
    graph = applyEventToGraph(graph, event("graph.dispatch", { node: "ags_1", task: "t1", execution_id: "e1" }), 100000);
    cache.get(graph, "radial"); expect(cache.computations).toBe(2);
    cache.get(graph, "tree"); expect(cache.computations).toBe(3);
  });
});
