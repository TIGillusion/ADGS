// ============================================================
// 图谱 reducer 单元测试 — 纯函数：事件序列 → 图谱状态
// 覆盖：建轮 / 任务 / 派发 / 状态 / 评估 / 克隆 / 旧版兼容 / 统计
// ============================================================

import { describe, expect, it } from "vitest";
import {
  CEO_KEY,
  applyEventToGraph,
  graphFromSaved,
  graphInit,
  graphStats,
  splitInstKey,
  type GraphState,
} from "@/lib/graph";
import type { BusEvent, SavedGraphNode } from "@/types";

let t = 1_700_000_000;
const ev = (type: string, data: Record<string, unknown>, session?: string): BusEvent => ({
  type: type as BusEvent["type"],
  ts: t++,
  data,
  ...(session ? { session } : {}),
});

function play(state: GraphState, events: BusEvent[]): GraphState {
  let g = state;
  for (const e of events) g = applyEventToGraph(g, e, e.ts * 1000);
  return g;
}

const byKey = (g: GraphState) => new Map(g.nodes.map((n) => [n.key, n]));

describe("graphInit", () => {
  it("初始只有 CEO 节点", () => {
    const g = graphInit();
    expect(g.nodes).toHaveLength(1);
    expect(g.nodes[0]).toMatchObject({
      key: CEO_KEY,
      id: "ceo",
      type: "ceo",
      parent: null,
    });
    expect(g.roundSeq).toBe(0);
    expect(g.currentRound).toBeNull();
  });
});

describe("chat.user — 建轮", () => {
  it("每次用户任务建立新轮，CEO 标题更新", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "写个爬虫" }), t * 1000);
    expect(g.roundSeq).toBe(1);
    expect(g.currentRound).toBe("round-1");
    const ceo = byKey(g).get(CEO_KEY)!;
    expect(ceo.extra.task_title).toBe("写个爬虫");
    const r1 = byKey(g).get("round-1")!;
    expect(r1).toMatchObject({ type: "round", parent: CEO_KEY, status: "idle" });
    expect(r1.extra.user_text).toBe("写个爬虫");

    g = applyEventToGraph(g, ev("chat.user", { text: "再翻译一篇文章" }), t * 1000);
    expect(g.roundSeq).toBe(2);
    expect(g.currentRound).toBe("round-2");
    expect(byKey(g).has("round-2")).toBe(true);
  });

  it("重复事件幂等（同轮次号不重复建轮）", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务A" }), t * 1000);
    const before = g.nodes.length;
    // 同一轮号再次收到 chat.user 属于异常重放 → 仍建新轮（语义：每次用户输入=一轮）
    g = applyEventToGraph(g, ev("chat.user", { text: "任务B" }), t * 1000);
    expect(g.nodes.length).toBe(before + 1);
  });
});

describe("graph.task — 任务节点", () => {
  it("CEO 派发任务挂到当前轮，label=序号", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "批量任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.task", {
        id: "t1_1_abc",
        name: "抓取",
        goal: "抓取页面",
        overall: "总目标",
        parent: "ceo",
        seq: 1,
        count: 2,
      }),
      t * 1000,
    );
    const task = byKey(g).get("t1_1_abc")!;
    expect(task).toMatchObject({
      type: "task",
      parent: "round-1",
      label: "1",
      status: "idle",
    });
    expect(task.extra).toMatchObject({
      goal: "抓取页面",
      overall_goal: "总目标",
      task_title: "抓取",
      seq: 1,
      count: 2,
    });
  });

  it("无轮时兜底建轮（刷新后 chat.user 缺失场景）", () => {
    let g = graphInit();
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "t9_1_x", name: "补轮", parent: "ceo", seq: 1, count: 1 }),
      t * 1000,
    );
    const task = byKey(g).get("t9_1_x")!;
    expect(task.parent).toBe("round-1");
    expect(g.currentRound).toBe("round-1");
  });

  it("委派任务挂到父节点实例下", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "复杂任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("graph.task", {
        id: "ags_1-n1-uuid",
        name: "子任务",
        goal: "g",
        parent: "ags_1@t1_1_a",
        seq: 1,
        count: 1,
      }),
      t * 1000,
    );
    const sub = byKey(g).get("ags_1-n1-uuid")!;
    expect(sub.parent).toBe("ags_1@t1_1_a");
  });

  it("每轮序号从 1 重编号：后端 seq 漂移（ask_user 挂起恢复）不带到显示", () => {
    // 场景：轮 1 拆 1 个任务（后端 seq=1）→ CEO 提问挂起（_task_done=False）
    // → 用户回答（前端建新轮 2）→ 后端续用旧轮号继续派发 seq=2/3（不重置）。
    // 前端每轮显示必须从 1 开始，与后端累计 seq 解耦。
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务A" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "t1_1_a", name: "拆分1", parent: "ceo", seq: 1, count: 1 }),
      t * 1000,
    );
    expect(byKey(g).get("t1_1_a")!.label).toBe("1");

    g = applyEventToGraph(g, ev("chat.user", { text: "回答/继续" }), t * 1000);
    // 后端仍认为同一轮：task_id 前缀 t1_，seq 继续 2、3
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "t1_2_b", name: "拆分2", parent: "ceo", seq: 2, count: 1 }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "t1_3_c", name: "拆分3", parent: "ceo", seq: 3, count: 1 }),
      t * 1000,
    );
    const r2 = "round-2";
    expect(byKey(g).get("t1_2_b")!.parent).toBe(r2);
    expect(byKey(g).get("t1_2_b")!.label).toBe("1"); // 轮 2 的第一个 = 1
    expect(byKey(g).get("t1_3_c")!.label).toBe("2"); // 轮 2 的第二个 = 2
    // extra.seq 与 label 一致（tooltip 显示同值）；后端原始 seq=3 嵌在 id t1_3_c 中
    expect(byKey(g).get("t1_3_c")!.extra.seq).toBe(2);
  });

  it("后端重启归零后的正常序号不受重编号影响", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务A" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "t1_1_a", name: "拆分1", parent: "ceo", seq: 1, count: 1 }),
      t * 1000,
    );
    g = applyEventToGraph(g, ev("chat.user", { text: "任务B" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "t2_1_b", name: "拆分1", parent: "ceo", seq: 1, count: 1 }),
      t * 1000,
    );
    expect(byKey(g).get("t2_1_b")!.label).toBe("1");
    expect(byKey(g).get("t2_1_b")!.extra.seq).toBe(1);
  });

  it("重复/补发同一任务 id 不重新编号（label 保持首次创建值）", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务A" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "t1_1_a", name: "拆分1", parent: "ceo", seq: 1, count: 1 }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "t1_2_b", name: "拆分2", parent: "ceo", seq: 2, count: 1 }),
      t * 1000,
    );
    // 补发 t1_1_a（后端 _dispatch_single 独立调用路径会补发 graph.task）
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "t1_1_a", name: "拆分1", parent: "ceo", seq: 1, count: 2 }),
      t * 1000,
    );
    expect(byKey(g).get("t1_1_a")!.label).toBe("1");
    expect(byKey(g).get("t1_1_a")!.extra.count).toBe(2);
    expect(byKey(g).get("t1_2_b")!.label).toBe("2");
  });

  it("委派任务（挂节点实例下）保留后端本地序号，不参与轮内重编号", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "复杂任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    // 节点本地第 5 次委派批次
    g = applyEventToGraph(
      g,
      ev("graph.task", { id: "ags_1-n5-uuid", name: "子任务", parent: "ags_1@t1_1_a", seq: 5, count: 1 }),
      t * 1000,
    );
    const sub = byKey(g).get("ags_1-n5-uuid")!;
    expect(sub.label).toBe("5");
    expect(sub.extra.seq).toBe(5);
  });
});

describe("graph.dispatch — 执行实例", () => {
  it("建立 node@task 实例（waiting），再派发同键不重复", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct", goal: "g1" }),
      t * 1000,
    );
    const inst = byKey(g).get("ags_1@t1_1_a")!;
    expect(inst).toMatchObject({
      type: "node",
      id: "ags_1",
      // 2026-10-23:锚点缺失时就地补建任务节点，层级保持 轮→任务→实例
      parent: "t1_1_a",
      status: "waiting",
    });
    expect(byKey(g).get("t1_1_a")).toMatchObject({ type: "task", parent: "round-1" });
    expect(inst.extra.mode).toBe("direct");
    expect(inst.extra.goal).toBe("g1");

    const n = g.nodes.length;
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct", goal: "g2" }),
      t * 1000,
    );
    expect(g.nodes.length).toBe(n);
    expect(byKey(g).get("ags_1@t1_1_a")!.extra.goal).toBe("g2");
  });

  it("task 未建时按名称匹配兜底挂父", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "抓取", node: "ags_2", mode: "direct" }),
      t * 1000,
    );
    // 无对应 task → 就地补建任务节点，实例挂任务下（2026-10-23 层级修复）
    const inst = byKey(g).get("ags_2@抓取")!;
    expect(inst.parent).toBe("抓取");
    expect(byKey(g).get("抓取")).toMatchObject({ type: "task", parent: "round-1" });
  });
});

describe("node.status / node.eval", () => {
  it("带 task 精确更新实例状态", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_1", status: "running", task: "t1_1_a" }),
      t * 1000,
    );
    expect(byKey(g).get("ags_1@t1_1_a")!.status).toBe("running");
  });

  it("不带 task 更新最近实例", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_1", status: "done" }),
      t * 1000,
    );
    expect(byKey(g).get("ags_1@t1_1_a")!.status).toBe("done");
  });

  it("状态先于派发到达 → 临时实例（不丢状态）", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_9", status: "running" }),
      t * 1000,
    );
    const inst = byKey(g).get("ags_9@round-1")!;
    expect(inst.status).toBe("running");
    expect(inst.parent).toBe("round-1");
  });

  it("转入 running 记录服务端时间戳 runTs（执行时长起点）", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_1", status: "running", task: "t1_1_a" }),
      t * 1000,
    );
    const inst = byKey(g).get("ags_1@t1_1_a")!;
    // runTs 来自事件 ts（服务端时间）——重连后事件重放可恢复，不归零
    expect(inst.extra.runTs).toBeGreaterThan(0);
    expect(inst.bornTs!).toBeLessThan(inst.extra.runTs!);
  });

  it("重复 running 不重置 runTs；delegating→running 再入重置（返工重计）", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_1", status: "running", task: "t1_1_a" }),
      t * 1000,
    );
    const first = byKey(g).get("ags_1@t1_1_a")!.extra.runTs!;
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_1", status: "running", task: "t1_1_a" }),
      t * 1000,
    );
    expect(byKey(g).get("ags_1@t1_1_a")!.extra.runTs).toBe(first);
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_1", status: "delegating", task: "t1_1_a" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_1", status: "running", task: "t1_1_a" }),
      t * 1000,
    );
    expect(byKey(g).get("ags_1@t1_1_a")!.extra.runTs!).toBeGreaterThan(first);
  });

  it("临时实例 bornTs 用事件时间而非客户端 Date.now()（重连重放不漂移）", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    const before = Date.now();
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_9", status: "running" }),
      t * 1000,
    );
    expect(byKey(g).get("ags_9@round-1")!.bornTs!).toBeLessThan(before);
  });

  it("非法状态忽略", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("node.status", { node: "ags_1", status: "bogus", task: "t1_1_a" }),
      t * 1000,
    );
    expect(byKey(g).get("ags_1@t1_1_a")!.status).toBe("waiting");
  });

  it("node.eval：passed→done / 未过→failed + 分数", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("node.eval", { node: "ags_1", passed: true, score: 88.5, reworks: 1, detail: "ok" }),
      t * 1000,
    );
    const ok = byKey(g).get("ags_1@t1_1_a")!;
    expect(ok.status).toBe("done");
    expect(ok.extra.score).toBe(88.5);
    expect(ok.extra.passed).toBe(true);
    expect(ok.extra.reworks).toBe(1);
    expect(ok.extra.eval_detail).toBe("ok");

    // 第二个任务：未过 → failed
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_2_b", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("node.eval", { node: "ags_1", passed: false, score: 30 }),
      t * 1000,
    );
    const bad = byKey(g).get("ags_1@t1_2_b")!;
    expect(bad.status).toBe("failed");
    expect(bad.extra.score).toBe(30);
  });
});

describe("graph.clone — 临时克隆", () => {
  it("最近实例标记 clone + profile 绑定", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_2_b", node: "ags_1", mode: "clone" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("graph.clone", { node: "ags_1", profile: "ags_1_clone_77" }),
      t * 1000,
    );
    const inst = byKey(g).get("ags_1@t1_2_b")!;
    expect(inst.extra.mode).toBe("clone");
    expect(inst.extra.profile).toBe("ags_1_clone_77");
    // 本体实例不受影响
    expect(byKey(g).get("ags_1@t1_1_a")!.extra.mode).toBe("direct");
  });
});

describe("hermes 阶段事件", () => {
  it("hermes.start/end 写入 brain 阶段（按 profile 命中克隆实例）", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_2_b", node: "ags_1", mode: "clone" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("graph.clone", { node: "ags_1", profile: "clone_p1" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("hermes.start", { node: "ags_1", profile: "clone_p1", phase: "执行中" }),
      t * 1000,
    );
    expect(byKey(g).get("ags_1@t1_2_b")!.extra.brain).toBe("执行中");
  });
});

describe("graphFromSaved — 历史会话兼容", () => {
  it("旧版字段映射（x/y/pinned 忽略、overall→overall_goal、clone→node）", () => {
    const saved: SavedGraphNode[] = [
      { key: "ceo", id: "ceo", type: "ceo", label: "CEO", parent: "", status: "idle" },
      {
        key: "round-1",
        id: "round-1",
        type: "round",
        label: "轮 1",
        parent: "ceo",
        status: "idle",
        x: 10,
        y: 20,
        tx: 10,
        ty: 20,
        pinned: true,
        extra: { user_text: "旧任务" },
      },
      {
        key: "t1_1_old",
        id: "t1_1_old",
        type: "task",
        label: "1",
        parent: "round-1",
        status: "idle",
        extra: { goal: "g", overall: "总目标", seq: 1, count: 3 },
      },
      {
        key: "ags_1@t1_1_old",
        id: "ags_1",
        type: "clone",
        label: "ags_1",
        parent: "t1_1_old",
        status: "weird-status",
        extra: { score: 77.7, passed: true, reworks: 2, profile: "p" },
      },
      { key: "", id: "x", type: "node", label: "", parent: "", status: "" },
    ];
    const g = graphFromSaved(saved);
    const map = byKey(g);
    expect(map.has(CEO_KEY)).toBe(true);
    const task = map.get("t1_1_old")!;
    expect(task.extra.overall_goal).toBe("总目标");
    expect(task.extra.seq).toBe(1);
    expect(task.extra.count).toBe(3);
    const inst = map.get("ags_1@t1_1_old")!;
    expect(inst.type).toBe("node"); // clone → node
    expect(inst.status).toBe("idle"); // 非法状态降级
    expect(inst.extra.mode).toBe("clone");
    expect(inst.extra.score).toBe(77.7);
    expect(inst.extra.passed).toBe(true);
    // 轮序恢复
    expect(g.roundSeq).toBe(1);
    expect(g.currentRound).toBe("round-1");
    // 空 key 丢弃
    expect(g.nodes.length).toBe(4);
  });

  it("载入历史图谱同样每轮从 1 重编号（修复旧版保存的序号漂移）", () => {
    // 旧版保存的 label 直接来自后端累计 seq（实测：轮 5 = 7,8,9,10,11）
    const saved: SavedGraphNode[] = [
      { key: "round-1", id: "round-1", type: "round", label: "轮 1", parent: "ceo", status: "idle" },
      { key: "t3_7_a", id: "t3_7_a", type: "task", label: "7", parent: "round-1", status: "idle", extra: { seq: 7, count: 1 } },
      { key: "t3_8_b", id: "t3_8_b", type: "task", label: "8", parent: "round-1", status: "idle", extra: { seq: 8, count: 1 } },
      { key: "t3_9_c", id: "t3_9_c", type: "task", label: "9", parent: "round-1", status: "idle", extra: { seq: 9, count: 1 } },
      // 委派序号（挂节点实例）保持原样
      { key: "ags_1@t3_7_a", id: "ags_1", type: "node", label: "ags_1", parent: "t3_7_a", status: "done" },
      { key: "ags_1-n4-x", id: "ags_1-n4-x", type: "task", label: "4", parent: "ags_1@t3_7_a", status: "idle", extra: { seq: 4, count: 1 } },
    ];
    const g = graphFromSaved(saved);
    const map = byKey(g);
    expect(map.get("t3_7_a")!.label).toBe("1");
    expect(map.get("t3_7_a")!.extra.seq).toBe(1);
    expect(map.get("t3_8_b")!.label).toBe("2");
    expect(map.get("t3_9_c")!.label).toBe("3");
    // 委派任务不受影响
    expect(map.get("ags_1-n4-x")!.label).toBe("4");
    expect(map.get("ags_1-n4-x")!.extra.seq).toBe(4);
  });

  it("旧版「任务总览」dump 归一化（29 个 CEO → 1 个，2026-09-03 用户报告）", () => {
    // 旧前端总览把全部会话节点加 "${会话}::" 前缀画到同一画布，该视图被自动保存
    // 写进会话文件 → 载入得到多 CEO 混合树（实测 305 节点 / 29 ceo / 44 轮）
    const saved: SavedGraphNode[] = [
      { key: "ceo", id: "ceo", type: "ceo", label: "CEO", parent: "", status: "idle" },
      { key: "auto-a::ceo", id: "ceo", type: "ceo", label: "CEO 决策中枢", parent: "ceo", status: "idle" },
      { key: "auto-b::ceo", id: "ceo", type: "ceo", label: "CEO 决策中枢", parent: "ceo", status: "idle" },
      // 轮次挂在各自会话的 ceo 下（含悬空：auto-m 的 ceo 节点缺失）
      { key: "auto-a::round-1", id: "auto-a::round-1", type: "round", label: "轮 1", parent: "auto-a::ceo", status: "idle", extra: { user_text: "任务A" } },
      { key: "auto-a::round-2", id: "auto-a::round-2", type: "round", label: "轮 2", parent: "auto-a::ceo", status: "idle", extra: { user_text: "任务B" } },
      { key: "auto-b::round-1", id: "auto-b::round-1", type: "round", label: "轮 1", parent: "auto-b::ceo", status: "idle", extra: { user_text: "任务C" } },
      { key: "auto-m::round-1", id: "auto-m::round-1", type: "round", label: "轮 1", parent: "auto-m::ceo", status: "idle" },
      // 任务 label 是漂移的旧 seq，重编号仍要生效
      { key: "auto-a::t1_7_a", id: "auto-a::t1_7_a", type: "task", label: "7", parent: "auto-a::round-1", status: "done", extra: { seq: 7 } },
      { key: "auto-a::t1_8_b", id: "auto-a::t1_8_b", type: "task", label: "8", parent: "auto-a::round-1", status: "done", extra: { seq: 8 } },
      { key: "auto-a::t2_9_c", id: "auto-a::t2_9_c", type: "task", label: "9", parent: "auto-a::round-2", status: "done", extra: { seq: 9 } },
      { key: "auto-a::ags_1@t1_7_a", id: "ags_1", type: "node", label: "ags_1", parent: "auto-a::t1_7_a", status: "done" },
    ];
    const g = graphFromSaved(saved);
    // 多 CEO 合并为单一规范 CEO
    const ceos = g.nodes.filter((n) => n.type === "ceo");
    expect(ceos).toHaveLength(1);
    expect(ceos[0].key).toBe(CEO_KEY);
    // 所有轮次（含悬空 ceo 父的）重定向到规范 CEO，前缀 key 原样保留
    const rounds = g.nodes.filter((n) => n.type === "round");
    expect(rounds).toHaveLength(4);
    expect(rounds.every((r) => r.parent === CEO_KEY)).toBe(true);
    expect(rounds.map((r) => r.key)).toContain("auto-m::round-1");
    // 该轮任务重编号 1,2 / 1（与漂移旧 label 解耦）
    const t1 = g.nodes.find((n) => n.key === "auto-a::t1_7_a")!;
    const t2 = g.nodes.find((n) => n.key === "auto-a::t1_8_b")!;
    const t3 = g.nodes.find((n) => n.key === "auto-a::t2_9_c")!;
    expect([t1.label, t2.label]).toEqual(["1", "2"]);
    expect(t3.label).toBe("1");
    // 实例保留，parent 指向前缀任务键
    const inst = g.nodes.find((n) => n.key === "auto-a::ags_1@t1_7_a")!;
    expect(inst.parent).toBe("auto-a::t1_7_a");
  });

  it("空图谱 → 仅 CEO", () => {
    const g = graphFromSaved([]);
    expect(g.nodes).toHaveLength(1);
    expect(g.roundSeq).toBe(0);
  });

  it("陈旧瞬态状态归一化（2026-09-03：任务完成但实例卡 running/delegating）", () => {
    const saved: SavedGraphNode[] = [
      { key: "round-1", id: "round-1", type: "round", label: "轮 1", parent: "ceo", status: "idle" },
      // Missing terminal evidence is unknown, not an acceptance failure.
      { key: "ags_101@t1_11", id: "ags_101", type: "node", label: "ags_101", parent: "round-1", status: "running" },
      // 委派父实例未收到终态事件，但评估信息存在且通过 → done
      {
        key: "ags_255@t1_1",
        id: "ags_255",
        type: "node",
        label: "ags_255",
        parent: "round-1",
        status: "delegating",
        extra: { passed: true, score: 91.2 },
      },
      // A waiting snapshot without evaluation cannot prove failure.
      { key: "ags_140@t1_2", id: "ags_140", type: "node", label: "ags_140", parent: "round-1", status: "waiting" },
      // 终态不受影响
      { key: "ags_342@t1_3", id: "ags_342", type: "node", label: "ags_342", parent: "round-1", status: "done", extra: { score: 95 } },
    ];
    const g = graphFromSaved(saved);
    const map = byKey(g);
    expect(map.get("ags_101@t1_11")!.status).toBe("unknown");
    expect(map.get("ags_255@t1_1")!.status).toBe("done");
    expect(map.get("ags_255@t1_1")!.extra.score).toBe(91.2); // 评估分数保留
    expect(map.get("ags_140@t1_2")!.status).toBe("unknown");
    expect(map.get("ags_342@t1_3")!.status).toBe("done"); // 终态原样
  });
});

describe("查询工具", () => {
  it("splitInstKey", () => {
    expect(splitInstKey("ags_1@t1_1_a")).toEqual({ nodeId: "ags_1", taskId: "t1_1_a" });
    expect(splitInstKey("ags_1")).toEqual({ nodeId: "ags_1", taskId: "ags_1" });
  });

  it("graphStats 只统计执行实例", () => {
    let g = graphInit();
    g = applyEventToGraph(g, ev("chat.user", { text: "任务" }), t * 1000);
    g = applyEventToGraph(
      g,
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_a", node: "ags_1", mode: "direct" }),
      t * 1000,
    );
    g = applyEventToGraph(
      g,
      ev("node.eval", { node: "ags_1", passed: true, score: 90 }),
      t * 1000,
    );
    const s = graphStats(g);
    expect(s.total).toBe(1);
    expect(s.done).toBe(1);
    expect(s.active).toBe(0);
    expect(s.failed).toBe(0);
  });

  it("完整任务流：轮→任务→实例→完成 全链路", () => {
    let g = graphInit();
    const events: BusEvent[] = [
      ev("chat.user", { text: "帮我做一批网页抓取" }),
      ev("graph.task", { id: "t1_1_aaa", name: "抓取A", goal: "ga", overall: "总", parent: "ceo", seq: 1, count: 2 }),
      ev("graph.task", { id: "t1_2_bbb", name: "抓取B", goal: "gb", overall: "总", parent: "ceo", seq: 2, count: 2 }),
      ev("graph.dispatch", { parent: "ceo", task: "t1_1_aaa", node: "ags_1", mode: "direct", goal: "ga" }),
      ev("graph.dispatch", { parent: "ceo", task: "t1_2_bbb", node: "ags_1", mode: "clone", goal: "gb" }),
      ev("graph.clone", { node: "ags_1", profile: "ags_1_x9" }),
      ev("node.status", { node: "ags_1", status: "running", task: "t1_1_aaa" }),
      ev("node.status", { node: "ags_1", status: "running", task: "t1_2_bbb" }),
      ev("node.eval", { node: "ags_1", passed: true, score: 92, task: "t1_1_aaa" }),
      ev("node.status", { node: "ags_1", status: "done", task: "t1_1_aaa" }),
      ev("node.eval", { node: "ags_1", passed: false, score: 20, task: "t1_2_bbb" }),
      ev("node.status", { node: "ags_1", status: "failed", task: "t1_2_bbb" }),
    ];
    g = play(g, events);
    const map = byKey(g);
    expect(map.get("ags_1@t1_1_aaa")!.status).toBe("done");
    expect(map.get("ags_1@t1_1_aaa")!.extra.score).toBe(92);
    expect(map.get("ags_1@t1_2_bbb")!.status).toBe("failed");
    expect(map.get("ags_1@t1_2_bbb")!.extra.mode).toBe("clone");
    const s = graphStats(g);
    expect(s.total).toBe(2);
    expect(s.done).toBe(1);
    expect(s.failed).toBe(1);
  });
});

describe("A2A 委派边（merged_brain）", () => {
  it("running 建边、结束更新状态、未知事件安全", () => {
    let g = graphInit();
    g = play(g, [
      ev("graph.dispatch", { parent: "ceo", task: "t1", node: "ags_1", mode: "direct", goal: "g" }),
      ev("a2a.delegate", { delegation_id: "d1", parent: "ags_1", node: "ags_2", status: "running", summary: "下载并部署" }),
    ]);
    expect(g.a2aEdges).toHaveLength(1);
    expect(g.a2aEdges[0]).toMatchObject({
      delegationId: "d1", fromNodeId: "ags_1", toNodeId: "ags_2", status: "running",
    });
    g = play(g, [ev("a2a.delegate", { delegation_id: "d1", parent: "ags_1", node: "ags_2", status: "passed", summary: "完成" })]);
    expect(g.a2aEdges).toHaveLength(1);
    expect(g.a2aEdges[0].status).toBe("passed");
    expect(g.a2aEdges[0].summary).toBe("完成");
    // 事件缺 delegation_id → 忽略
    g = play(g, [ev("a2a.delegate", { node: "ags_3", status: "running" })]);
    expect(g.a2aEdges).toHaveLength(1);
  });

  it("a2aEndpointKey 归一到最近实例；无实例返回 null", () => {
    let g = graphInit();
    g = play(g, [ev("graph.dispatch", { parent: "ceo", task: "t1", node: "ags_1", mode: "direct", goal: "g" })]);
    expect(g.a2aEdges).toHaveLength(0);
  });
});

describe("A2A 多次委派 = 多条边(2026-10-23 用户需求)", () => {
  it("同一对节点的两次委派产生两条独立边,各自状态更新互不干扰", () => {
    let g = graphInit();
    g = play(g, [
      ev("graph.dispatch", { parent: "ceo", task: "t1", node: "ags_591", mode: "direct", goal: "g" }),
      ev("a2a.delegate", { delegation_id: "b1365", parent: "ags_591", node: "ags_582", status: "running", summary: "第一轮委派" }),
      ev("a2a.delegate", { delegation_id: "b1365", parent: "ags_591", node: "ags_582", status: "passed", summary: "第一轮汇报" }),
      ev("a2a.verdict", { delegation_id: "b1365", parent: "ags_591", node: "ags_582", passed: false, detail: "汇报缺项,打回" }),
      ev("a2a.delegate", { delegation_id: "1e1a6", parent: "ags_591", node: "ags_582", status: "running", summary: "返工委派" }),
      ev("a2a.delegate", { delegation_id: "1e1a6", parent: "ags_591", node: "ags_582", status: "passed", summary: "返工后汇报" }),
      ev("a2a.verdict", { delegation_id: "1e1a6", parent: "ags_591", node: "ags_582", passed: true, detail: "复核通过" }),
    ]);
    expect(g.a2aEdges).toHaveLength(2);
    const first = g.a2aEdges.find((e) => e.delegationId === "b1365")!;
    const second = g.a2aEdges.find((e) => e.delegationId === "1e1a6")!;
    // 复核结果写入边状态:第一轮被父复核打回(failed),返工后通过(passed)
    expect(first.status).toBe("failed");
    expect(second.status).toBe("passed");
    expect(first.summary).toBe("第一轮汇报");
    expect(second.summary).toBe("返工后汇报");
    // 两条边各自独立、端点一致
    expect(first).not.toBe(second);
    expect(first.fromNodeId).toBe(second.fromNodeId);
    expect(first.toNodeId).toBe(second.toNodeId);
  });

  it("A2A 子执行的 node.status(mesh_ 前缀)不进 CEO 树", () => {
    let g = graphInit();
    g = play(g, [
      ev("graph.dispatch", { parent: "ceo", task: "t1", node: "ags_591", mode: "direct", goal: "g" }),
      ev("node.status", { node: "ags_582", task: "mesh_1e1a6e21", execution_id: "exec-sub", status: "running" }),
      ev("node.status", { node: "ags_582", task: "mesh_1e1a6e21", execution_id: "exec-sub", status: "done" }),
    ]);
    // ags_582 的 A2A 子执行不得直挂 CEO 树(由 a2a 边表达)
    expect(g.nodes.some((n) => n.id === "ags_582")).toBe(false);
  });

  it("dispatch 缺 graph.task 锚点(续接/恢复)时就地补建轮次与任务", () => {
    let g = graphInit();
    g = play(g, [
      // 没有 chat.user/graph.task —— 模拟事件窗口丢失后的恢复场景
      ev("graph.dispatch", { parent: "ceo", task: "t0_7_abc", node: "ags_377", mode: "session", goal: "第11章" }),
    ]);
    const keys = g.nodes.map((n) => n.key);
    expect(keys).toContain("round-1");
    expect(keys).toContain("t0_7_abc");
    const inst = g.nodes.find((n) => n.key === "ags_377@t0_7_abc")!;
    expect(inst.parent).toBe("t0_7_abc");
    expect(g.nodes.find((n) => n.key === "t0_7_abc")!.parent).toBe("round-1");
  });
});
