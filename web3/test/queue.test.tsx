// ============================================================
// 任务队列面板回归（2026-10-22）：排队消息可查看 / 编辑 / 撤销
// ============================================================

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ChatView } from "@/components/ChatView";
import { store } from "@/lib/store";
import { api } from "@/lib/api";
import type { QueueItem } from "@/types";

vi.mock("@/lib/api", async () => ({
  ApiError: (await vi.importActual<typeof import('@/lib/api')>('@/lib/api')).ApiError,
  api: {
    projects: vi.fn().mockResolvedValue({ok:false,projects:[]}),
    state: vi.fn(),
    queueUpdate: vi.fn(),
    queueRemove: vi.fn(),
    queueGuide: vi.fn(),
    chat: vi.fn(),
    command: vi.fn(),
  },
}));

const ITEM: QueueItem = {
  id: "abc123",
  text: "排队中的第一条消息",
  session: "queue-sess",
  queued_at: 1792600000,
};

function seedQueue(items: QueueItem[] | []) {
  // 直接写 store 状态（api.state 只在 init/refresh 调用）
  (store as unknown as {
    set: (p: Record<string, unknown>) => void;
  }).set({ queue: items, queueLen: items.length, busy: true });
}

beforeEach(() => {
  vi.mocked(api.queueUpdate).mockReset().mockResolvedValue({ ok: true });
  vi.mocked(api.queueRemove).mockReset().mockResolvedValue({ ok: true });
  seedQueue([]);
});

describe("ChatView 队列面板", () => {
  it("队列为空时按钮可点开空态提示", () => {
    render(<ChatView />);
    fireEvent.click(screen.getByTitle("任务队列（查看 / 编辑 / 撤销排队消息）"));
    expect(screen.getByText(/队列为空/)).toBeTruthy();
  });

  it("排队消息显示文本与会话，可撤销", async () => {
    seedQueue([ITEM]);
    render(<ChatView />);
    fireEvent.click(screen.getByTitle("任务队列（查看 / 编辑 / 撤销排队消息）"));
    expect(screen.getAllByText("排队中的第一条消息").length).toBeGreaterThan(0);
    expect(screen.getByText("queue-sess")).toBeTruthy();
    fireEvent.click(screen.getByTitle("撤销（不执行）"));
    await waitFor(() => expect(api.queueRemove).toHaveBeenCalledWith("abc123"));
  });

  it("编辑：进入编辑态改文本后保存，调用 queueUpdate", async () => {
    seedQueue([ITEM]);
    render(<ChatView />);
    fireEvent.click(screen.getByTitle("任务队列（查看 / 编辑 / 撤销排队消息）"));
    fireEvent.click(screen.getByTitle("编辑"));
    const box = screen.getByLabelText("编辑排队消息内容") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "修改后的消息" } });
    fireEvent.click(screen.getByTitle("保存修改"));
    await waitFor(() => expect(api.queueUpdate).toHaveBeenCalledWith("abc123", "修改后的消息"));
  });

  it("多条排队消息按序号展示", () => {
    seedQueue([ITEM, { ...ITEM, id: "def456", text: "第二条", session: null }]);
    render(<ChatView />);
    fireEvent.click(screen.getByTitle("任务队列（查看 / 编辑 / 撤销排队消息）"));
    expect(screen.getByText("#1")).toBeTruthy();
    expect(screen.getByText("#2")).toBeTruthy();
    expect(screen.getAllByText("第二条").length).toBeGreaterThan(0);
  });
});

it("发送默认排队，待处理消息上的引导原位提升该条目", async () => {
  vi.mocked(api.chat).mockResolvedValue({ok:true,queued:true});
  vi.mocked(api.queueGuide).mockResolvedValue({ok:true,mode:'interrupt'});
  (store as any).set({viewSession:'running-chat',liveSession:'running-chat',busy:true,projectSupport:false});
  seedQueue([ITEM]);
  render(<ChatView />);
  const input=screen.getByPlaceholderText('继续输入，消息会先排队；发送后可点「引导」…');
  fireEvent.change(input,{target:{value:'先核验已有结果'}});
  fireEvent.click(screen.getByRole('button',{name:'发送'}));
  await waitFor(()=>expect(api.chat).toHaveBeenCalledWith('先核验已有结果'));
  const calls=vi.mocked(api.chat).mock.calls.length;
  fireEvent.click(screen.getByRole('button',{name:'引导'}));
  await waitFor(()=>expect(api.queueGuide).toHaveBeenCalledWith('abc123'));
  expect(vi.mocked(api.chat).mock.calls.length).toBe(calls);
});

it("CEO等下属时聊天显示等待阶段，入口打开固定CEO面板", () => {
  (store as any).set({busy:true,snap:{ceo:{engine:'hermes',role:'coordinator',phase:'waiting_children',execution_id:'ceo-test'}}});
  render(<ChatView />);
  expect(screen.getAllByText(/等待下属汇报/).length).toBeGreaterThan(0);
  expect(screen.queryByText('CEO 正在决策')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'查看 CEO 协调活动'}));
  expect(store.getState().selectedInst).toBe('ceo');
});

it("中文输入法确认和 Shift+Enter 不提交，普通 Enter 提交一次", async () => {
  vi.mocked(api.chat).mockReset().mockResolvedValue({ok:true,queued:true});
  (store as any).set({viewSession:'ime-chat',liveSession:'ime-chat',busy:true,projectSupport:false,messages:[],queue:[],queueLen:0,snap:null});
  render(<ChatView/>);
  const input=screen.getByRole('textbox',{name:'消息内容'});
  fireEvent.change(input,{target:{value:'先检查已有结果'}});
  fireEvent.keyDown(input,{key:'Enter',isComposing:true});
  fireEvent.keyDown(input,{key:'Enter',keyCode:229});
  fireEvent.keyDown(input,{key:'Enter',shiftKey:true});
  expect(api.chat).not.toHaveBeenCalled();
  fireEvent.keyDown(input,{key:'Enter'});
  await waitFor(()=>expect(api.chat).toHaveBeenCalledTimes(1));
});

it("选择命令后保留会话草稿，Escape 关闭队列", () => {
  (store as any).set({viewSession:'command-draft',busy:false,messages:[],queue:[],queueLen:0,snap:null});
  render(<ChatView/>);
  fireEvent.change(screen.getByRole('textbox',{name:'消息内容'}),{target:{value:'/sta'}});
  fireEvent.click(screen.getByRole('button',{name:'/status 系统状态'}));
  expect(store.draftFor('command-draft')).toBe('/status ');
  fireEvent.click(screen.getByTitle('任务队列（查看 / 编辑 / 撤销排队消息）'));
  expect(screen.getByText(/队列为空/)).toBeInTheDocument();
  fireEvent.keyDown(window,{key:'Escape'});
  expect(screen.queryByText(/队列为空/)).not.toBeInTheDocument();
});

it("纯答复显示CEO答复，不宣称新的任务完成", () => {
  (store as any).set({busy:false,snap:{ceo:{engine:'hermes',role:'coordinator',phase:'answered'}},
    messages:[{id:'answer',kind:'finish',status:'answered',text:'已有六项均通过。',ts:1}],queue:[],queueLen:0});
  render(<ChatView />);
  expect(screen.getByText('CEO 答复')).toBeInTheDocument();
  expect(screen.getByText('已有六项均通过。')).toBeInTheDocument();
  expect(screen.queryByText('任务完成')).not.toBeInTheDocument();
  expect(screen.queryByText('CEO 汇报受阻，任务待恢复')).not.toBeInTheDocument();
});

