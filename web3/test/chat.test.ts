// ============================================================
// 事件 → 聊天消息 映射单元测试
// ============================================================

import { describe, expect, it } from "vitest";
import { eventToMessage, groupChatMessages, mapSessionMessage, mapSessionMessages } from "@/lib/chat";
import type { BusEvent, ChatMessage, SessionMessage } from "@/types";
import { ceoGraphStatus, ceoPhaseLabel } from "@/lib/ceo";

const ev = (type: string, data: Record<string, unknown>, ts = 1700000000): BusEvent => ({
  type: type as BusEvent["type"],
  ts,
  data,
});

describe("eventToMessage", () => {
  it("纯答复的实时、历史和CEO图谱阶段一致", () => {
    const live = eventToMessage(ev('chat.ceo_finish',{status:'answered',summary:'已有结果'}));
    const history = mapSessionMessage({kind:'finish',status:'answered',text:'已有结果'});
    expect(live).toMatchObject({kind:'finish',status:'answered',text:'已有结果'});
    expect(history).toMatchObject({kind:'finish',status:'answered',text:'已有结果'});
    expect(ceoPhaseLabel({engine:'hermes',role:'coordinator',phase:'answered'})).toBe('答复完成');
    expect(ceoGraphStatus('answered')).toBe('done');
  });
  it("chat.user → user 消息", () => {
    const m = eventToMessage(ev("chat.user", { text: "做个爬虫" }));
    expect(m).toMatchObject({ kind: "user", text: "做个爬虫" });
    expect(m?.ts).toBe(1700000000);
  });

  it("system → system 消息", () => {
    const m = eventToMessage(ev("system", { msg: "后端启动" }));
    expect(m).toMatchObject({ kind: "system", text: "后端启动" });
  });

  it("chat.ceo_ask → ask", () => {
    const m = eventToMessage(ev("chat.ceo_ask", { question: "确认范围？" }));
    expect(m).toMatchObject({ kind: "ask", text: "确认范围？" });
  });

  it("chat.ceo_tool → tool（含名称/参数/结果——全量保留,2026-10-23）", () => {
    const long = "x".repeat(5000);
    const m = eventToMessage(
      ev("chat.ceo_tool", { name: "write_file", args: { path: "/a" }, result: long }),
    );
    expect(m?.kind).toBe("tool");
    expect(m?.name).toBe("write_file");
    expect(m?.args).toEqual({ path: "/a" });
    expect(m?.result).toHaveLength(5000);
  });

  it("chat.ceo_report → report（summary + next_plan）", () => {
    const m = eventToMessage(
      ev("chat.ceo_report", { summary: "阶段完成", next_plan: "下一步做X" }),
    );
    expect(m).toMatchObject({ kind: "report", text: "阶段完成", reason: "下一步做X" });
  });

  it("chat.ceo_finish → finish（工作流 + 交付物数组化）", () => {
    const m = eventToMessage(
      ev("chat.ceo_finish", {
        summary: "全部完成",
        workflow: "1→2→3",
        artifacts: ["a.md", "b.py", 42],
      }),
    );
    expect(m?.kind).toBe("finish");
    expect(m?.text).toBe("全部完成");
    expect(m?.workflow).toBe("1→2→3");
    expect(m?.artifacts).toEqual(["a.md", "b.py", "42"]);
  });

  it("chat.ceo_finish 无 artifacts 时给空数组", () => {
    const m = eventToMessage(ev("chat.ceo_finish", { summary: "s" }));
    expect(m?.artifacts).toEqual([]);
  });

  it("CEO 的受阻汇报保留状态和原因，历史恢复与实时消息一致", () => {
    const summary = "节点模型服务无法连接，恢复后继续原执行。";
    const live = eventToMessage(ev("chat.ceo_finish", { status: "blocked", summary }));
    const history = mapSessionMessage({ kind: "finish", status: "blocked", text: summary });
    expect(live).toMatchObject({ kind: "finish", status: "blocked", text: summary });
    expect(history).toMatchObject({ kind: "finish", status: "blocked", text: summary });
  });

  it("chat.ceo_end error → 出错文案", () => {
    const m = eventToMessage(
      ev("chat.ceo_end", { status: "error", error: "模型超时" }),
    );
    expect(m).toMatchObject({ kind: "end", status: "error", reason: "模型超时" });
    expect(m?.text).toBe("出错：模型超时");
  });

  it("chat.ceo_end limit → 上限文案（含原因）", () => {
    const m = eventToMessage(
      ev("chat.ceo_end", { status: "limit", reason: "步数达上限", steps: 30 }),
    );
    expect(m?.kind).toBe("end");
    expect(m?.text).toContain("决策达到上限");
    expect(m?.text).toContain("步数达上限");
  });

  it("chat.ceo_end limit 无原因", () => {
    const m = eventToMessage(ev("chat.ceo_end", { status: "limit" }));
    expect(m?.text).toBe("本轮决策达到上限，任务可能未完全结束，可继续追问");
  });

  it("chat.ceo_end 其它状态 → 任务结束", () => {
    const m = eventToMessage(ev("chat.ceo_end", { status: "ok" }));
    expect(m?.text).toBe("任务结束（ok）");
  });

  it('blocked 展示未完成状态与实际原因',()=>{
    const m=eventToMessage(ev('chat.ceo_end',{status:'blocked',error:'任务预算耗尽，原会话保留'}));
    expect(m?.text).toContain('任务尚未完成');
    expect(m?.text).toContain('任务预算耗尽');
  });

  it("node.brain → brain（节点+动作+详情）", () => {
    const m = eventToMessage(
      ev("node.brain", { node: "ags_1", action: "plan_hermes", detail: "计划A" }),
    );
    expect(m).toMatchObject({ kind: "brain", node: "ags_1", action: "plan_hermes", text: "计划A" });
  });

  it("node.eval → eval（passed/score 归一）", () => {
    const m = eventToMessage(
      ev("node.eval", { node: "ags_1", passed: true, score: "88.5", detail: "通过" }),
    );
    expect(m?.kind).toBe("eval");
    expect(m?.passed).toBe(true);
    expect(m?.score).toBe(88.5);
    expect(m?.text).toBe("通过");
  });

  it("node.eval 缺字段 → undefined", () => {
    const m = eventToMessage(ev("node.eval", { node: "ags_1" }));
    expect(m?.passed).toBeUndefined();
    expect(m?.score).toBeUndefined();
  });

  it("非聊天事件返回 null（graph/evolve/hermes 不进对话流）", () => {
    expect(eventToMessage(ev("graph.task", { id: "t1" }))).toBeNull();
    expect(eventToMessage(ev("evolve.round", { n_before: 3 }))).toBeNull();
    expect(eventToMessage(ev("hermes.line", { line: "x" }))).toBeNull();
    expect(eventToMessage(ev("node.status", { node: "a", status: "done" }))).toBeNull();
  });

  it("消息 id 唯一", () => {
    const a = eventToMessage(ev("chat.user", { text: "1" }));
    const b = eventToMessage(ev("chat.user", { text: "2" }));
    expect(a?.id).not.toBe(b?.id);
  });
});

// ---------------- 历史会话消息映射（2026-09-03：CEO 话术还原） ----------------

describe("mapSessionMessage", () => {
  it("没有发送时间的历史消息不伪造为当前时间",()=>{
    expect(mapSessionMessage({kind:"user",text:"旧消息"}).ts).toBe(0);
  });
  it("重开后依据持久用户事件恢复发送时间，保留消息全文",()=>{
    const events=[{...ev("chat.user",{text:"真正的输入"}),ts:1700000000}];
    expect(mapSessionMessages([{kind:"user",text:"真正的输入"}],events)[0]).toMatchObject({ts:1700000000,text:"真正的输入"});
  });
  it("重复相同输入依发生次序匹配，历史事件缺失时不猜测",()=>{
    const messages:SessionMessage[]=[{kind:"user",text:"继续"},{kind:"user",text:"继续"}];
    const events=[{...ev("chat.user",{text:"继续"}),ts:100},{...ev("chat.user",{text:"继续"}),ts:200}];
    expect(mapSessionMessages(messages,events).map(m=>m.ts)).toEqual([100,200]);
    expect(mapSessionMessages(messages,events.slice(1)).map(m=>m.ts)).toEqual([0,0]);
  });
  it("user/tool 原样映射", () => {
    const u: SessionMessage = { kind: "user", text: "做个爬虫" };
    expect(mapSessionMessage(u, "id-u")).toMatchObject({ kind: "user", text: "做个爬虫", id: "id-u" });
    const t: SessionMessage = { kind: "tool", name: "split_tasks", args: { n: 2 }, result: "ok" };
    expect(mapSessionMessage(t, "id-t")).toMatchObject({
      kind: "tool", name: "split_tasks", args: { n: 2 }, result: "ok",
    });
  });

  it("ask/report/finish 话术还原（不再退化成 text）", () => {
    expect(mapSessionMessage({ kind: "ask", text: "报告多长？" }, "i1")).toMatchObject({
      kind: "ask", text: "报告多长？",
    });
    expect(
      mapSessionMessage({ kind: "report", text: "已完成调研", reason: "开始写作" }, "i2"),
    ).toMatchObject({ kind: "report", text: "已完成调研", reason: "开始写作" });
    expect(
      mapSessionMessage(
        { kind: "finish", text: "交付", workflow: "1→2", artifacts: ["a.md"] }, "i3",
      ),
    ).toMatchObject({ kind: "finish", text: "交付", workflow: "1→2", artifacts: ["a.md"] });
  });

  it("未知 kind 兜底为 text（旧会话文件兼容）", () => {
    expect(mapSessionMessage({ kind: "text", text: "纯文本" }, "i4")).toMatchObject({
      kind: "text", text: "纯文本",
    });
  });
});

// ---------------- 连续工具分组（渲染层折叠） ----------------

const msg = (kind: ChatMessage["kind"], extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id: extra.id ?? `x-${kind}-${Math.random().toString(36).slice(2)}`,
  kind,
  ts: 1700000000,
  ...extra,
});

describe("groupChatMessages", () => {
  it("连续 tool 合并为一组，非 tool 打断", () => {
    const segs = groupChatMessages([
      msg("user", { id: "u1" }),
      msg("tool", { id: "t1", name: "a" }),
      msg("tool", { id: "t2", name: "b" }),
      msg("ask", { id: "k1" }),
      msg("tool", { id: "t3", name: "c" }),
    ]);
    expect(segs.map((s) => (s.type === "tools" ? s.items.length : -1))).toEqual([-1, 2, -1, 1]);
    expect(segs[0].type).toBe("msg");
    expect(segs[1]).toMatchObject({ type: "tools", items: [expect.objectContaining({ id: "t1" }), expect.objectContaining({ id: "t2" })] });
  });

  it("全工具流 = 单个大组；无工具 = 全单条", () => {
    const allTools = groupChatMessages([msg("tool", { id: "t1" }), msg("tool", { id: "t2" })]);
    expect(allTools).toHaveLength(1);
    expect(allTools[0].type).toBe("tools");
    const none = groupChatMessages([msg("user", { id: "u1" }), msg("ask", { id: "k1" })]);
    expect(none.every((s) => s.type === "msg")).toBe(true);
  });

  it("空列表 → 空", () => {
    expect(groupChatMessages([])).toEqual([]);
  });
});
