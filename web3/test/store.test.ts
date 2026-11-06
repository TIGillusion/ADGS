// ============================================================
// 全局 store 单元测试（mock api 层；fake timers 控制防抖/横幅）
// 覆盖：事件路由/会话隔离、启动折叠、后端重启检测、落盘守卫、
//       会话载入映射、动作回执、setView
// ============================================================

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BusEvent, StateSnapshot } from "@/types";

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    payload: unknown;
    constructor(s: number, m: string, p: unknown) {
      super(m);
      this.status = s;
      this.payload = p;
    }
  }
  return {
    ApiError,
    api: {
      state: vi.fn(),
      projects: vi.fn(),
      legacyWorkspaces: vi.fn(),
      queueGuide: vi.fn(),
      chat: vi.fn(),
      command: vi.fn(),
      sessionNew: vi.fn(),
      evolve: vi.fn(),
      sessions: vi.fn(),
      sessionGraphs: vi.fn(),
      sessionLoad: vi.fn(),
      sessionSaveGraph: vi.fn(),
      nodes: vi.fn(),
      nodeDetail: vi.fn(),
      nodeDescribe: vi.fn(),
      nodeSkillRemove: vi.fn(),
      nodeSkillInstall: vi.fn(),
      nodeRevive: vi.fn(),
      nodeRemove: vi.fn(),
    },
  };
});

function snap(bootTs = 1000): StateSnapshot {
  return {
    ok: true,
    workspace: "/ws",
    population: { active: 1, capacity: 8 },
    models: { ceo: "deepseek", brain: "qwen", worker: "hermes" },
    nodes: [],
    events: [],
    busy: false,
    queue_len: 0,
    boot_ts: bootTs,
  };
}

let ts = 2_000_000_000;
const ev = (type: string, data: Record<string, unknown>, session?: string): BusEvent => ({
  type: type as BusEvent["type"],
  ts: ts++,
  data,
  ...(session ? { session } : {}),
});

let store: Awaited<typeof import("@/lib/store")>["store"];
let api: Record<string, ReturnType<typeof vi.fn>>;

beforeEach(async () => {
  vi.resetModules();
  vi.useFakeTimers();
  const m = await import("@/lib/store");
  const a = await import("@/lib/api");
  store = m.store;
  api = a.api as unknown as Record<string, ReturnType<typeof vi.fn>>;
  // mock 工厂跨用例共享：清空调用记录与实现，避免跨用例串扰
  for (const fn of Object.values(api)) fn.mockReset();
  api.state.mockResolvedValue(snap());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("折叠（纯函数）", () => {
  it("foldGraph 从事件序列重建图谱", () => {
    const events: BusEvent[] = [
      ev("chat.user", { text: "任务" }),
      ev("graph.dispatch", { parent: "ceo", task: "t1", node: "ags_1", mode: "direct" }),
    ];
    const g = store.foldGraph(events);
    // 2026-10-23 层级修复：dispatch 缺 graph.task 锚点时就地补建任务节点
    expect(g.nodes.map((n) => n.key)).toEqual(["ceo", "round-1", "t1", "ags_1@t1"]);
  });

  it("foldMessages 只保留聊天事件", () => {
    const msgs = store.foldMessages([
      ev("chat.user", { text: "hi" }),
      ev("graph.task", { id: "t1" }),
      ev("system", { msg: "x" }),
    ]);
    expect(msgs.map((m) => m.kind)).toEqual(["user", "system"]);
  });
});

describe("init / 启动折叠", () => {
  it("成功：boot=ok + 启动事件重放", async () => {
    api.state.mockResolvedValueOnce({
      ...snap(),
      events: [ev("chat.user", { text: "遗留任务" })],
    });
    await store.init();
    expect(store.getState().boot.status).toBe("ok");
    expect(store.getState().messages[0].text).toBe("遗留任务");
  });

  it("失败：boot=error", async () => {
    api.state.mockRejectedValueOnce(new Error("down"));
    await store.init();
    expect(store.getState().boot).toMatchObject({ status: "error", error: "down" });
  });
});

describe("会话隔离", () => {
  it("事件带 session 时观测 liveSession", () => {
    store.handleEvent(ev("chat.user", { text: "x" }, "auto-1"));
    expect(store.getState().liveSession).toBe("auto-1");
  });

  it("历史视图过滤非本会话事件", async () => {
    api.sessionLoad.mockResolvedValue({
      ok: true,
      busy: false,
      running: false,
      messages: [{ kind: "user", text: "旧" }],
      graph: [],
    });
    await store.loadSession("hist");
    expect(store.getState().viewSession).toBe("hist");

    // 其它会话事件被过滤
    store.handleEvent(ev("chat.user", { text: "别家" }, "auto-9"));
    expect(store.getState().messages).toHaveLength(1);
    // 本会话事件放行
    store.handleEvent(ev("chat.user", { text: "本家" }, "hist"));
    expect(store.getState().messages).toHaveLength(2);
  });
});

describe("后端重启检测", () => {
  it("boot_ts 变化 → 全量重置 + 系统提示", async () => {
    api.state.mockResolvedValueOnce(snap(1000));
    await store.init();
    // 制造一些状态
    store.handleEvent(ev("chat.user", { text: "任务" }));
    expect(store.getState().graph.nodes.length).toBeGreaterThan(1);

    api.state.mockResolvedValueOnce(snap(2000));
    await store.refreshState();
    const s = store.getState();
    expect(s.graph.nodes).toHaveLength(1);
    expect(s.messages[0].kind).toBe("system");
    expect(s.messages[0].text).toContain("重启");
  });

  it("boot_ts 不变 → 状态保留", async () => {
    api.state.mockResolvedValue(snap(1000));
    await store.init();
    store.handleEvent(ev("chat.user", { text: "任务" }));
    await store.refreshState();
    expect(store.getState().graph.nodes.length).toBeGreaterThan(1);
    expect(store.getState().busy).toBe(false);
  });
});

describe("图谱落盘守卫", () => {
  it("viewSession 与 liveSession 不一致 → 不保存", async () => {
    store.handleEvent(ev("chat.user", { text: "任务" }, "live-a"));
    api.sessionLoad.mockResolvedValue({
      ok: true,
      busy: false,
      running: false,
      messages: [],
      graph: [],
    });
    await store.loadSession("view-b");
    await store.saveGraphNow();
    expect(api.sessionSaveGraph).not.toHaveBeenCalled();
  });

  it("无会话（viewSession/liveSession 皆空）→ 不保存", async () => {
    store.handleEvent(ev("chat.user", { text: "任务" }));
    await store.saveGraphNow();
    expect(api.sessionSaveGraph).not.toHaveBeenCalled();
  });

  it("实时视图 + 已观测 liveSession → 防抖后保存", async () => {
    store.handleEvent(ev("chat.user", { text: "任务" }, "auto-1"));
    store.handleEvent(
      ev("graph.dispatch", { parent: "ceo", task: "t1", node: "ags_1", mode: "direct" }, "auto-1"),
    );
    vi.advanceTimersByTime(1600); // 防抖 1500ms
    expect(api.sessionSaveGraph).toHaveBeenCalledTimes(1);
    const payload = api.sessionSaveGraph.mock.calls[0][0];
    expect(Array.isArray(payload)).toBe(true);
    expect(payload.map((n: { key: string }) => n.key)).toContain("ags_1@t1");
  });

  it("仅 CEO 节点 → 跳过保存", async () => {
    // 带 session 的事件观测 liveSession，但不新增图谱节点
    store.handleEvent(ev("node.status", { node: "ags_9", status: "bogus" }, "auto-1"));
    expect(store.getState().liveSession).toBe("auto-1");
    expect(store.getState().graph.nodes).toHaveLength(1);
    await store.saveGraphNow();
    expect(api.sessionSaveGraph).not.toHaveBeenCalled();
  });
});

describe("会话载入", () => {
  it("消息 kind 映射 + 图谱重建 + 视图切换", async () => {
    api.sessionLoad.mockResolvedValue({
      ok: true,
      busy: false,
      running: false,
      messages: [
        { kind: "user", text: "hi" },
        { kind: "text", text: "你好" },
        { kind: "tool", name: "write", args: { a: 1 }, result: "ok" },
      ],
      graph: [
        {
          key: "round-1",
          id: "round-1",
          type: "round",
          label: "轮 1",
          parent: "ceo",
          status: "idle",
        },
        {
          key: "ags_1@t1",
          id: "ags_1",
          type: "node",
          label: "ags_1",
          parent: "round-1",
          status: "done",
        },
      ],
    });
    await store.loadSession("s1");
    const s = store.getState();
    expect(s.viewSession).toBe("s1");
    expect(s.view).toBe("chat");
    expect(s.messages.map((m) => m.kind)).toEqual(["user", "text", "tool"]);
    expect(s.messages[2].name).toBe("write");
    expect(s.messages[2].result).toBe("ok");
    expect(s.graph.nodes.map((n) => n.key)).toContain("ags_1@t1");
    expect(s.toasts.some((t) => t.text.includes("已载入会话 s1"))).toBe(true);
  });

  it("运行会话用持久结构事件补齐旧图谱，不重放聊天/日志或推进全局游标", async () => {
    const events = [
      ev("graph.task", { id: "old-task", parent: "ceo", seq: 8 }, "active"),
      ev("graph.dispatch", { node: "ags_1", task_id: "old-task", execution_id: "old-exec" }, "active"),
      ev("node.eval", { node: "ags_1", execution_id: "old-exec", passed: true, score: 95 }, "active"),
      ev("graph.task", { id: "new-task", parent: "ceo", seq: 9 }, "active"),
      ev("graph.dispatch", { node: "ags_1", task_id: "new-task", execution_id: "new-exec" }, "active"),
      ev("node.status", { node: "ags_1", execution_id: "new-exec", status: "running" }, "active"),
      ev("node.eval", { node: "ags_1", execution_id: "new-exec", passed: true, score: 98 }, "active"),
      ev("chat.user", { text: "最新任务" }, "active"),
      ev("hermes.line", { node: "ags_1", line: "不要重复写日志" }, "active"),
      ev("graph.dispatch", { node: "ags_other", execution_id: "other-exec" }, "other"),
    ].map((event, i) => ({ ...event, id: 1000 + i }));
    api.sessionLoad.mockResolvedValue({
      ok: true, busy: true, running: true,
      messages: [{ kind: "user", text: "最新任务" }, { kind: "report", text: "刚完成" }],
      graph: [
        ...[1, 2].map((i) => ({ key: `round-${i}`, id: `round-${i}`, type: "round", label: `轮 ${i}`, parent: "ceo", status: "idle" })),
        { key: "old-task", id: "old-task", type: "task", label: "1", parent: "round-1", status: "idle", extra: { seq: 1 } },
        { key: "ags_1@old-exec", id: "ags_1", type: "node", label: "ags_1", parent: "old-task", status: "running", extra: { execution_id: "old-exec" } },
      ],
      events,
    });
    await store.loadSession("active");
    const state = store.getState();
    const nodes = new Map(state.graph.nodes.map((node) => [node.key, node]));
    expect(nodes.get("old-task")).toMatchObject({ parent: "round-1", label: "1", extra: { seq: 1 } });
    expect(nodes.get("ags_1@old-exec")).toMatchObject({ status: "done", extra: { passed: true, score: 95 } });
    expect(nodes.get("new-task")).toMatchObject({ parent: "round-2", label: "1" });
    expect(nodes.get("ags_1@new-exec")).toMatchObject({ parent: "new-task", status: "done", extra: { passed: true, score: 98 } });
    expect(nodes.has("ags_other@other-exec")).toBe(false);
    expect(state.graph.roundSeq).toBe(2);
    expect(state.messages.map((message) => message.text)).toEqual(["最新任务", "刚完成"]);
    expect(store.logs.entry("ags_1")).toBeUndefined();
    // 结构快照高 ID 不可跳过完整 SSE 流中的较早聊天事件。
    store.handleEvent({ ...ev("system", { msg: "补偿事件" }, "active"), id: 1 });
    expect(store.getState().messages.at(-1)?.text).toBe("补偿事件");
  });

  // 2026-09-04：载入多轮会话默认聚焦最近一轮（全图太高不可读）；轮次 ≤3 不聚焦
  it("载入 >3 轮会话 → graphFocusRound = 最近一轮", async () => {
    const rounds = [1, 2, 3, 4, 5].map((i) => ({
      key: `round-${i}`,
      id: `round-${i}`,
      type: "round" as const,
      label: `轮 ${i}`,
      parent: "ceo",
      status: "idle",
    }));
    api.sessionLoad.mockResolvedValue({
      ok: true,
      busy: false,
      running: false,
      messages: [],
      graph: rounds,
    });
    await store.loadSession("multi");
    expect(store.getState().graphFocusRound).toBe("round-5");
  });

  it("载入 ≤3 轮会话 → graphFocusRound = null（全图）", async () => {
    const rounds = [1, 2, 3].map((i) => ({
      key: `round-${i}`,
      id: `round-${i}`,
      type: "round" as const,
      label: `轮 ${i}`,
      parent: "ceo",
      status: "idle",
    }));
    api.sessionLoad.mockResolvedValue({
      ok: true,
      busy: false,
      running: false,
      messages: [],
      graph: rounds,
    });
    await store.loadSession("few");
    expect(store.getState().graphFocusRound).toBeNull();
  });

  it("失败 → 错误 toast，视图不变", async () => {
    api.sessionLoad.mockRejectedValue(new Error("404"));
    await store.loadSession("nope");
    expect(store.getState().viewSession).toBeNull();
    expect(store.getState().toasts.some((t) => t.kind === "error")).toBe(true);
  });
});

describe("动作回执", () => {
  it("sendChat 排队 → warn toast", async () => {
    api.chat.mockResolvedValueOnce({ ok: true, queued: true });
    await store.sendChat(" 做个任务 ");
    expect(api.chat).toHaveBeenCalledWith("做个任务");
    expect(store.getState().toasts.some((t) => t.kind === "warn")).toBe(true);
  });

  it("sendChat 失败 → error toast", async () => {
    api.chat.mockRejectedValueOnce(new Error("500"));
    await store.sendChat("任务");
    expect(store.getState().toasts.some((t) => t.kind === "error")).toBe(true);
  });

  it("空文本直接返回", async () => {
    await store.sendChat("   ");
    expect(api.chat).not.toHaveBeenCalled();
  });

  it("chat.user 事件被后端截断 → 用本地发送的全文回填（长消息截断兜底）", async () => {
    api.chat.mockResolvedValueOnce({ ok: true });
    const long = "长".repeat(800);
    await store.sendChat(long);
    // 旧后端事件只带前 500 字
    store.handleEvent(ev("chat.user", { text: long.slice(0, 500) }));
    const userMsg = store.getState().messages.filter((m) => m.kind === "user").at(-1)!;
    expect(userMsg.text).toBe(long);
  });

  it("非截断的 chat.user 事件原样显示", async () => {
    api.chat.mockResolvedValueOnce({ ok: true });
    await store.sendChat("短任务");
    store.handleEvent(ev("chat.user", { text: "短任务" }));
    const userMsg = store.getState().messages.filter((m) => m.kind === "user").at(-1)!;
    expect(userMsg.text).toBe("短任务");
  });

  it("sendCommand 回执 → 本地 system 消息", async () => {
    api.command.mockResolvedValueOnce({ ok: true, message: "状态：ok" });
    await store.sendCommand("/status");
    expect(api.command).toHaveBeenCalledWith("/status");
    const last = store.getState().messages.at(-1)!;
    expect(last.kind).toBe("system");
    expect(last.text).toBe("状态：ok");
  });

  it("newSession 重置本地状态", async () => {
    store.handleEvent(ev("chat.user", { text: "任务" }, "auto-1"));
    api.sessionNew.mockResolvedValueOnce({ ok: true });
    await store.newSession();
    const s = store.getState();
    expect(s.graph.nodes).toHaveLength(1);
    expect(s.liveSession).toBeNull();
    expect(s.viewSession).toBeNull();
    expect(s.messages.at(-1)!.text).toContain("已新建对话");
  });

  it("nodeAction remove 后端拒绝 → 抛错 + error toast", async () => {
    api.nodeRemove.mockResolvedValueOnce({ ok: false, error: "busy" });
    await expect(store.nodeAction("ags_1", "remove")).rejects.toThrow();
    expect(store.getState().toasts.some((t) => t.kind === "error" && t.text.includes("busy"))).toBe(
      true,
    );
  });

  it("nodeAction skill-install 解析 from::name", async () => {
    api.nodeSkillInstall.mockResolvedValueOnce({ ok: true });
    await store.nodeAction("ags_2", "skill-install", "ags_1::web_scrape");
    expect(api.nodeSkillInstall).toHaveBeenCalledWith("ags_2", "ags_1", "web_scrape");
    expect(store.getState().toasts.some((t) => t.kind === "ok")).toBe(true);
  });
});

describe("setView", () => {
  it("可切换到 sessions 视图（回归：此前条件写反永远不生效）", () => {
    store.setView("sessions");
    expect(store.getState().view).toBe("sessions");
    store.setView("graph");
    expect(store.getState().view).toBe("graph");
  });
});

describe("节点日志缓冲（2026-09-03 keying 修复）", () => {
  it("node.brain → 大脑决策通道（此前只进聊天流，抽屉大脑标签永远空）", () => {
    store.handleEvent(
      ev("node.brain", { node: "ags_1", action: "plan_hermes", detail: "直接执行" }),
    );
    const e = store.logs.entry("ags_1");
    expect(e?.agentlog).toEqual(["[brain] plan_hermes 直接执行"]);
    expect(e?.hermes).toEqual([]);
  });

  it("hermes.line 按 node id 归属（不再串台到选中实例/空键）", () => {
    store.selectInst("ags_1@t1");
    store.handleEvent(ev("hermes.line", { node: "ags_2", profile: "ags_2", line: "B 行" }));
    expect(store.logs.entry("ags_1@t1")).toBeUndefined();
    expect(store.logs.entry("ags_2")?.hermes).toEqual(["B 行"]);
  });

  it("克隆 profile 绑定实例：dispatch(clone)→clone(profile)→行入 node@task", () => {
    store.handleEvent(
      ev("graph.dispatch", { parent: "ceo", task: "t1", node: "ags_1", mode: "clone" }),
    );
    store.handleEvent(ev("graph.clone", { node: "ags_1", profile: "ags_1_tmp_42" }));
    store.handleEvent(ev("hermes.line", { profile: "ags_1_tmp_42", line: "克隆输出" }));
    expect(store.logs.entry("ags_1@t1")?.hermes).toEqual(["克隆输出"]);
  });

  it("agentlog 未绑定克隆 profile → 节点级条目（抽屉按 nodeId 兜底）", () => {
    store.handleEvent(ev("hermes.agentlog", { profile: "ags_3_tmp_7", line: "agent 行" }));
    expect(store.logs.entry("ags_3_tmp_7")?.agentlog).toEqual(["agent 行"]);
  });
});

describe("进化轮事件", () => {
  it("evolve.round → evolveLast/banner + 横幅到期清除", () => {
    const round = {
      n_before: 3,
      n_after: 4,
      pressure: 0.5,
      births: [{ id: "ags_9", type: "seed", score: 50 }],
      eliminations: [{ id: "ags_2" }],
    };
    store.handleEvent(ev("evolve.round", round));
    let s = store.getState();
    expect(s.evolveLast).toEqual(round);
    expect(s.evolveBanner).toEqual(round);
    vi.advanceTimersByTime(9500);
    s = store.getState();
    expect(s.evolveLast).toEqual(round);
    expect(s.evolveBanner).toBeNull();
  });
});

it("历史会话重放恢复委派边及完整交互时间线", async () => {
  const events = [
    ev("graph.dispatch", { node: "ags_1", task_id: "t1" }, "archive"),
    ev("a2a.delegate", { delegation_id: "d1", parent: "ags_1", node: "ags_2", status: "running", summary: "任务书".repeat(500) }, "archive"),
    ev("a2a.verdict", { delegation_id: "d1", passed: true, detail: "复核全文".repeat(500) }, "archive"),
    ev("a2a.delegate", { delegation_id: "foreign", parent: "ags_9", node: "ags_8" }, "other"),
  ];
  api.sessionLoad.mockResolvedValue({ ok: true, messages: [], graph: [], events });
  api.state.mockResolvedValue(snap());
  await store.loadSession("archive");
  expect(store.getState().graph.a2aEdges.map(e => e.delegationId)).toEqual(["d1"]);
  expect(store.getDelegationEvents("d1")).toEqual(events.slice(1, 3));
  expect(store.getDelegationEvents("foreign")).toHaveLength(0);
});

it("旧后端未确认引导模式时，不把普通排队误报为已接受打断引导", async () => {
  api.chat.mockResolvedValue({ok:true,queued:true});
  api.state.mockResolvedValue(snap());
  await store.sendChat('纠偏','interrupt');
  expect(store.getState().toasts.at(-1)?.text).toContain('已按普通排队保存');
});

it("项目模式明确发送给所选会话，不跟随其他会话的实时事件切换归属", async () => {
  (store as any).set({projectSupport:true,viewSession:'chat-a',liveSession:'chat-a',projects:[{id:'p',name:'项目',workspace:'/p',sessions:[{id:'chat-a',title:'A'},{id:'chat-b',title:'B'}]}]});
  store.handleEvent(ev('chat.user',{text:'B正在工作'},'chat-b'));
  expect(store.getState().messages).toHaveLength(0);
  expect(store.getState().viewSession).toBe('chat-a');
  api.chat.mockResolvedValue({ok:true,queued:true});
  api.state.mockResolvedValue(snap());
  await store.sendChat('A的新消息');
  expect(api.chat).toHaveBeenCalledWith('A的新消息',{session:'chat-a'});
});

it("快速切换会话时，迟到的A响应不能覆盖已打开的B", async () => {
  (store as any).set({projectSupport:true,projects:[]});
  let resolveA!:(x:unknown)=>void,resolveB!:(x:unknown)=>void;
  api.sessionLoad.mockImplementation((name:string)=>new Promise(resolve=>{if(name==='a')resolveA=resolve;else resolveB=resolve;}));
  api.state.mockResolvedValue(snap());
  const a=store.loadSession('a'),b=store.loadSession('b');
  resolveB({ok:true,messages:[{kind:'user',text:'B内容',ts:1}],graph:[],events:[]});await b;
  resolveA({ok:true,messages:[{kind:'user',text:'A内容',ts:1}],graph:[],events:[]});await a;
  expect(store.getState().viewSession).toBe('b');
  expect(store.getState().messages.some(m=>m.text==='A内容')).toBe(false);
});

it("项目会话只接收自己的队列项", () => {
  (store as any).set({projectSupport:true,viewSession:'a'});
  store.handleEvent(ev('queue.update',{queue:[{id:'q1',session:'a',text:'A消息'},{id:'q2',session:'b',text:'B消息'}]}));
  expect(store.getState().queue.map(q=>q.id)).toEqual(['q1']);
});

it('重新载入暂停会话仍展示末次暂停原因',async()=>{
  api.sessionLoad.mockResolvedValue({ok:true,messages:[{kind:'user',text:'原始任务'}],graph:[],events:[ev('chat.ceo_end',{status:'blocked',error:'预算耗尽'},'paused')]});
  await store.loadSession('paused');
  expect(store.getState().messages.at(-1)?.text).toContain('预算耗尽');
  expect(store.getState().messages.at(-1)?.text).toContain('任务尚未完成');
});

it('旧后端没有项目接口时，仍在左侧列出按工作区归组的现有会话，不自动切换当前任务',async()=>{
  const {ApiError}=await import('@/lib/api');
  api.projects.mockRejectedValue(new ApiError(404,'not found',null));
  api.sessions.mockResolvedValue({ok:true,sessions:[{name:'old-a',preview:'历史任务',saved_at:'',msg_count:1}]});
  api.legacyWorkspaces.mockResolvedValue({workspaces:{'old-a':'D:\\work\\历史项目'}});
  await store.refreshProjects(true);
  expect(store.getState().projects[0].name).toBe('历史项目');
  expect(store.getState().projects[0].sessions[0].id).toBe('old-a');
  expect(store.getState().projectSupport).toBe(false);
  expect(api.sessionLoad).not.toHaveBeenCalled();
});

it('旧索引不可用时历史会话仍可显示；新版项目接口暂时失败不降级覆盖目录',async()=>{
  const {ApiError}=await import('@/lib/api');
  api.projects.mockRejectedValue(new ApiError(404,'not found',null));
  api.sessions.mockResolvedValue({ok:true,sessions:[{name:'old',preview:'历史',saved_at:'',msg_count:1}]});
  api.legacyWorkspaces.mockRejectedValue(new Error('missing'));
  await store.refreshProjects();
  expect(store.getState().projects[0].name).toBe('未记录工作区');
  api.projects.mockRejectedValue(new ApiError(503,'busy',null));
  await store.refreshProjects();
  expect(api.sessions).toHaveBeenCalledTimes(1);
  expect(store.getState().projects[0].sessions[0].id).toBe('old');
});

it('关闭页面重新建 store 后恢复各自草稿，发送清空后不再恢复',async()=>{
  const values=new Map<string,string>();
  vi.stubGlobal('localStorage',{getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)});
  store.saveDraft('a','A完整草稿');store.saveDraft('b','B完整草稿');
  vi.resetModules();
  const reopened=(await import('@/lib/store')).store;
  expect(reopened.draftFor('a')).toBe('A完整草稿');
  expect(reopened.draftFor('b')).toBe('B完整草稿');
  reopened.saveDraft('a','');
  vi.resetModules();
  const again=(await import('@/lib/store')).store;
  expect(again.draftFor('a')).toBe('');
  expect(again.draftFor('b')).toBe('B完整草稿');
});

it('旧后端重开页面时恢复上次选择的历史会话',async()=>{
  const {ApiError}=await import('@/lib/api');
  vi.stubGlobal('localStorage',{getItem:()=> 'saved-chat',setItem:vi.fn(),removeItem:vi.fn()});
  api.projects.mockRejectedValue(new ApiError(404,'not found',null));
  api.sessions.mockResolvedValue({ok:true,sessions:[{name:'saved-chat',preview:'历史任务',saved_at:'',msg_count:1}]});
  api.legacyWorkspaces.mockResolvedValue({workspaces:{}});
  api.sessionLoad.mockResolvedValue({ok:true,messages:[{kind:'user',text:'完整历史'}],graph:[],events:[]});
  await store.refreshProjects(true);
  expect(store.getState().viewSession).toBe('saved-chat');
  expect(store.getState().messages[0].text).toBe('完整历史');
});
