// ============================================================
// NodeDrawer 渲染回归 — 重点覆盖「打开抽屉时 React 崩溃」：
// LogBuffer.subscribe 曾为普通类方法，被 useSyncExternalStore
// 以裸函数调用时 this 为 undefined，导致整棵 React 树卸载。
// ============================================================

import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NodeDrawer } from "@/components/NodeDrawer";
import { store } from "@/lib/store";
import { api } from "@/lib/api";
import type { NodeDetail, SkillInfo } from "@/types";

vi.mock("@/lib/api", () => ({
  api: {
    nodeDetail: vi.fn(),
    nodes: vi.fn(),
    nodeLogs: vi.fn(),
    meshDelegations: vi.fn(),
  },
}));

const NODE: NodeDetail = {
  id: "ags_1",
  describe: "擅长网页抓取与结构化抽取",
  score: 88.5,
  success_rate: 0.83,
  tasks_done: 12,
  gen: 2,
  birth_type: "recombine",
  parents: ["ags_init_1"],
  status: "idle",
  created_at: 1750000000,
  updated_at: 1750003600,
  skills: ["web_scrape"],
  archived_at: null,
};

const SKILLS: SkillInfo[] = [
  { name: "web_scrape", desc: "网页抓取技能", content: "# web_scrape" },
  { name: "json_parse", desc: "稳健 JSON 解析", content: "# json_parse" },
];

beforeEach(() => {
  vi.mocked(api.nodeDetail).mockReset().mockResolvedValue({ ok: true, node: NODE, skills: SKILLS });
  vi.mocked(api.nodes).mockReset().mockResolvedValue({ ok: true, nodes: [], archived: [] });
  // 默认磁盘无日志（历史回退空态）；用例级覆盖
  vi.mocked(api.nodeLogs).mockReset().mockResolvedValue({ ok: true, hermes: null, agentlog: null });
  vi.mocked(api.meshDelegations).mockReset().mockResolvedValue({ ok: true, delegations: [], total: 0 });
  store.selectInst(null);
});

describe("NodeDrawer 渲染回归", () => {
  it("尚未建立Hermes会话的CEO显示固定职责，不请求Worker档案", async () => {
    store.selectInst("ceo");
    const { unmount } = render(<NodeDrawer />);
    expect(screen.getByTestId("ceo-profile")).toHaveTextContent("固定 CEO · Hermes");
    expect(screen.getByTestId("ceo-profile")).toHaveTextContent("adgs-ceo");
    expect(api.nodeDetail).not.toHaveBeenCalled();
    unmount();
  });
  it("裸节点 id 选中后抽屉挂载并加载档案（曾整树崩溃）", async () => {
    store.selectInst("ags_1");
    const { unmount } = render(<NodeDrawer />);
    // 档案加载成功（未崩溃、未停在 loading）
    expect(await screen.findByText("web_scrape")).toBeInTheDocument();
    expect(screen.getByText("json_parse")).toBeInTheDocument();
    expect(screen.getByText("综合评分")).toBeInTheDocument();
    unmount();
  });

  it("实例键（node@task）选中后同样挂载", async () => {
    store.selectInst("ags_1@t1_1");
    const { unmount } = render(<NodeDrawer />);
    expect(await screen.findByText("web_scrape")).toBeInTheDocument();
    unmount();
  });

  it("未选中任何节点时不渲染", () => {
    store.selectInst(null);
    const { container } = render(<NodeDrawer />);
    expect(container).toBeEmptyDOMElement();
  });

  it("取消选中后抽屉卸载（Esc 等价路径）", async () => {
    store.selectInst("ags_1");
    const { unmount } = render(<NodeDrawer />);
    expect(await screen.findByText("web_scrape")).toBeInTheDocument();
    act(() => {
      store.selectInst(null);
    });
    expect(screen.queryByText("web_scrape")).not.toBeInTheDocument();
    unmount();
  });

  it("实时缓冲空 → 拉磁盘历史日志尾部（2026-09-03：刷新后抽屉全空）", async () => {
    vi.mocked(api.nodeDetail).mockResolvedValue({
      ok: true,
      node: { ...NODE, id: "ags_hist_1" },
      skills: [],
    });
    vi.mocked(api.nodeLogs).mockResolvedValue({
      ok: true,
      hermes: {
        lines: ["历史行A", "历史行B"],
        total_lines: 120,
        mtime: 1750000000,
        size: 4096,
        truncated: true,
      },
      agentlog: {
        lines: ["agent 历史行"],
        total_lines: 5,
        mtime: 1750000000,
        size: 100,
        truncated: false,
      },
    });
    store.selectInst("ags_hist_1");
    const { unmount } = render(<NodeDrawer />);
    // 默认 hermes 标签：展示磁盘尾部
    expect(await screen.findByText("历史行B")).toBeInTheDocument();
    expect(screen.getByText(/历史日志/)).toBeInTheDocument();
    // 大脑决策标签：agentlog 历史
    fireEvent.click(screen.getByText("大脑决策"));
    expect(await screen.findByText("agent 历史行")).toBeInTheDocument();
    unmount();
  });

  it("磁盘无日志 → 空态提示（不卡在 loading）", async () => {
    vi.mocked(api.nodeDetail).mockResolvedValue({
      ok: true,
      node: { ...NODE, id: "ags_hist_2" },
      skills: [],
    });
    store.selectInst("ags_hist_2");
    const { unmount } = render(<NodeDrawer />);
    expect(await screen.findByText(/磁盘无该节点 hermes 执行日志/)).toBeInTheDocument();
    unmount();
  });

  it("节点有新活动时磁盘日志结论失效重查（null 哨兵过期，2026-09-27）", async () => {
    vi.mocked(api.nodeDetail).mockResolvedValue({
      ok: true,
      node: { ...NODE, id: "ags_hist_4" },
      skills: [],
    });
    // 第一次查询：磁盘无日志 → null 哨兵进缓存
    store.selectInst("ags_hist_4");
    const { unmount } = render(<NodeDrawer />);
    expect(await screen.findByText(/磁盘无该节点 hermes 执行日志/)).toBeInTheDocument();
    // 节点随后被派发任务 → store 失效缓存 → 抽屉开着也重查磁盘
    vi.mocked(api.nodeLogs).mockResolvedValue({
      ok: true,
      hermes: {
        lines: ["新产生的日志行"],
        total_lines: 1,
        mtime: 1750000000,
        size: 64,
        truncated: false,
      },
      agentlog: null,
    });
    act(() => {
      store.handleEvent({
        type: "graph.dispatch",
        ts: Date.now() / 1000,
        data: { parent: "ceo", task: "t9_1", node: "ags_hist_4", mode: "direct", goal: "x" },
      } as never);
    });
    expect(await screen.findByText("新产生的日志行")).toBeInTheDocument();
    unmount();
  });

  it("历史日志请求失败 → 错误提示", async () => {
    vi.mocked(api.nodeDetail).mockResolvedValue({
      ok: true,
      node: { ...NODE, id: "ags_hist_3" },
      skills: [],
    });
    vi.mocked(api.nodeLogs).mockRejectedValueOnce(new Error("500"));
    store.selectInst("ags_hist_3");
    const { unmount } = render(<NodeDrawer />);
    expect(await screen.findByText(/历史日志加载失败/)).toBeInTheDocument();
    unmount();
  });

  it("抽屉内容区可滚动（2026-09-03：内容超出屏幕高度时被裁切，日志标签无法到达）", async () => {
    store.selectInst("ags_1");
    const { container, unmount } = render(<NodeDrawer />);
    await screen.findByText("web_scrape");
    // 内容容器 = flex 列 + h-full + 滚动（复合选择器排除 LogPanel 等内部滚动区）
    const scrollable = container.querySelector(".flex.h-full.flex-col.overflow-y-auto");
    expect(scrollable).not.toBeNull();
    unmount();
  });

  it("技能区默认折叠（芯片行），点标题可展开/收起完整列表", async () => {
    store.selectInst("ags_1");
    const { unmount } = render(<NodeDrawer />);
    // 折叠态：技能名芯片可见（web_scrape），但完整列表的描述不渲染
    await screen.findByText("web_scrape");
    expect(screen.queryByText("网页抓取技能")).not.toBeInTheDocument();
    // 展开
    fireEvent.click(screen.getByText("技能库（2）"));
    expect(await screen.findByText("网页抓取技能")).toBeInTheDocument();
    expect(screen.getByText("稳健 JSON 解析")).toBeInTheDocument();
    // 收起
    fireEvent.click(screen.getByText("技能库（2）"));
    expect(screen.queryByText("网页抓取技能")).not.toBeInTheDocument();
    unmount();
  });
});

it("轮次与批次详情可点击，worker轮次键含@也不会误认成工作实例", async () => {
  const { applyEventToGraph, graphInit } = await import("@/lib/graph");
  const { displayGraph } = await import("@/lib/graphFlow");
  const events = [
    { type: "chat.user", ts: 1, data: { text: "完整用户输入尾部" } },
    { type: "graph.task", ts: 2, data: { id: "t-detail", stage_id: "s-detail", parent: "ceo", task_md: "任务书全部内容尾部" } },
    { type: "graph.dispatch", ts: 3, data: { node: "ags_detail", execution_id: "detail-exec", task_id: "t-detail", parent: "ceo", task_md: "任务书全部内容尾部" } },
    { type: "chat.ceo_tool", ts: 4, data: { name: "read_file", args: {path:"a"}, result: "操作结果完整尾部" } },
  ];
  const g = events.reduce((s,e)=>applyEventToGraph(s,e,e.ts*1000),graphInit());
  const original = store.getState().graph;
  (store as any).set({graph:g,selectedInst:"round-1"});
  const {unmount}=render(<NodeDrawer />);
  expect(screen.getByText("完整用户输入尾部")).toBeInTheDocument();
  fireEvent.click(screen.getByText(/CEO 操作 · read_file/));
  expect(screen.getByText(/操作结果完整尾部/)).toBeInTheDocument();
  const flow=displayGraph(g);
  const batch=flow.nodes.find(n=>n.type==='task')!;
  act(()=>store.selectInst(batch.key));
  fireEvent.click(screen.getByText(/ags_detail · detail-exec/));
  expect(screen.getByText("任务书全部内容尾部")).toBeInTheDocument();
  const workerRound=flow.nodes.find(n=>n.type==='round'&&n.parent==='ags_detail@detail-exec')!;
  act(()=>store.selectInst(workerRound.key));
  expect(screen.getByText("上级完整指令")).toBeInTheDocument();
  expect(screen.getByText("任务书全部内容尾部")).toBeInTheDocument();
  unmount();(store as any).set({graph:original,selectedInst:null});
});
