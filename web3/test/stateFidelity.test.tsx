import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NodesView } from "@/components/NodesView";
import { OverviewView } from "@/components/OverviewView";
import { ChatView } from "@/components/ChatView";
import { store } from "@/lib/store";
import { applyEventToGraph, graphInit } from "@/lib/graph";
import { displayGraph } from "@/lib/graphFlow";
import type { BusEvent, GraphNode } from "@/types";
import { ExecutionMonitor } from "@/components/ExecutionMonitor";
import { api } from "@/lib/api";

let seq = 0;
const event = (type: string, data: Record<string, unknown>): BusEvent => ({id:++seq,type,ts:seq,data});
const fold = (events: BusEvent[]) => events.reduce((g,e)=>applyEventToGraph(g,e,e.ts*1000),graphInit());
const instance = (g: ReturnType<typeof fold>, id="e1") => displayGraph(g).nodes.find(n=>n.type==="node"&&n.extra.execution_id===id)!;
const dispatch = () => event("graph.dispatch",{node:"ags_1",task_id:"t1",execution_id:"e1",parent:"ceo",task_md:"完整任务"});

beforeEach(()=>{seq=0;});
describe("颜色表达后端数值",()=>{
  it("高分节点用通过色，缺失评分保持未知色",()=>{
    (store as unknown as {set:(state:unknown)=>void}).set({boot:{status:"ok"},selectedInst:null,snap:{population:{active:2,capacity:8},nodes:[
      {id:"ags_1",describe:"高分",score:109.7,success_rate:.9,tasks_done:2,gen:1,birth_type:"initial"},
      {id:"ags_2",describe:"未评分",score:null,success_rate:0,tasks_done:0,gen:1,birth_type:"initial"},
    ]}});
    const {container}=render(<NodesView/>);
    expect(screen.getByText("109.7")).toHaveClass("text-ok-400");
    expect(container.querySelectorAll(".node-score")[1]).toHaveTextContent("—");
    expect(container.querySelectorAll(".node-score")[1]).toHaveClass("text-ink-500");
  });
  it("概览均值不把未提供的数据当零分",()=>{
    (store as unknown as {set:(state:unknown)=>void}).set({snap:{population:{active:2,capacity:8},nodes:[
      {id:"a",score:90,success_rate:.8,tasks_done:1}, {id:"b",score:null,success_rate:null,tasks_done:0},
    ]}});
    render(<OverviewView/>);
    const card=screen.getByText("平均适应度").closest(".surface")!;
    expect(card).toHaveTextContent("90.0");expect(card).toHaveTextContent("平均成功率 80%");
  });
});
describe("聊天 CEO 状态配色",()=>{
  it("没有真实发送时间的旧输入明确说明时间未记录",()=>{
    (store as unknown as {set:(state:unknown)=>void}).set({busy:false,messages:[{id:'old',kind:'user',text:'旧输入',ts:0}],queue:[],queueLen:0,projects:[],projectSupport:false,viewSession:null,liveSession:null,snap:{ceo:{phase:'idle'}}});
    render(<ChatView/>);
    expect(screen.getByText('时间未记录')).toBeVisible();
  });
  it.each([
    ["paused","interrupted","--canvas-waiting"],
    ["blocked","blocked","--canvas-waiting"],
    ["failed","failed","--canvas-failed"],
    ["completed","done","--canvas-done"],
    ["idle","idle","--canvas-idle"],
    ["unexpected","unknown","--canvas-idle"],
    ["waiting_children","delegating","--canvas-delegating"],
  ])("%s 使用真实状态色而非固定绿点",(phase,status,color)=>{
    (store as unknown as {set:(state:unknown)=>void}).set({busy:phase==="waiting_children",messages:[],queue:[],queueLen:0,projects:[],projectSupport:false,viewSession:null,liveSession:null,snap:{ceo:{phase}}});
    render(<ChatView/>);
    const badge=screen.getByTestId("ceo-status");
    expect(badge).toHaveAttribute("data-status",status);
    expect(badge.getAttribute("style")).toContain(`var(${color})`);
    expect(badge.querySelector(".animate-spin")).toBeNull();
  });
});
describe("图谱执行状态精确投影",()=>{
  it("CEO 已提交决策但等待下属时，不把最近决策用时解释成任务总用时", async ()=>{
    const lookup=vi.spyOn(api,"execution").mockResolvedValue({ok:true,execution:{
      execution_id:"ceo-wait",node_id:"ceo",profile:"ceo",task_id:"ceo",run_id:"r",
      state:"completed",started_at:100,ended_at:117,
      hermes_process_alive:true,hermes_process_state:"waiting_dispatch",attempts:[],events:[],
    }});
    const view=render(<ExecutionMonitor executionId="ceo-wait" nodeId="ceo" profile={null}
      coordinator={{engine:"hermes",role:"coordinator",phase:"waiting_children",execution_id:"ceo-wait"}}/>);
    try {
      expect(await screen.findByText("等待调度结果")).toBeVisible();
      expect(screen.getByText("最近决策用时")).toHaveTextContent("00:17");
      expect(view.container.querySelector(".execution-state")).toHaveClass("state-delegating");
      expect(view.container.querySelector(".state-done")).toBeNull();
    } finally {view.unmount();lookup.mockRestore();}
  });
  it("已通过的常驻会话显示等待输入，不能变成忙碌", async ()=>{
    const lookup=vi.spyOn(api,"execution").mockResolvedValue({ok:true,execution:{
      execution_id:"resident-idle",node_id:"ags_1",profile:"ags_1",task_id:"t",run_id:"r",
      state:"passed",phase:"finished",runtime_active:false,ended_at:100,
      hermes_process_alive:true,hermes_process_state:"waiting_input",attempts:[],events:[],
    }});
    const view=render(<ExecutionMonitor executionId="resident-idle" nodeId="ags_1" profile={null}/>);
    try {
      expect(await screen.findByText("等待下一次输入")).toBeVisible();
      expect(view.container.querySelector(".execution-state")).toHaveClass("state-done");
      expect(view.container.querySelector(".state-running")).toBeNull();
    } finally { view.unmount();lookup.mockRestore(); }
  });
  it("A2A 中断不变成任务失败，仍可恢复",()=>{
    const g=fold([event("chat.user",{text:"开始"}),event("a2a.delegate",{node:"ags_1",parent:"ceo",child_execution_id:"e1",delegation_id:"d1",status:"running"}),event("a2a.delegate",{node:"ags_1",parent:"ceo",child_execution_id:"e1",delegation_id:"d1",status:"interrupted"})]);
    expect(instance(g).status).toBe("interrupted");
  });
  it("返工开始清除上次通过标记和结果，保留原执行身份",()=>{
    const g=fold([dispatch(),event("node.eval",{node:"ags_1",execution_id:"e1",passed:true,score:90,detail:"旧验收"}),event("graph.instruction",{node:"ags_1",parent:"ceo",execution_id:"e1",task_id:"t1",context_mode:"continue",instruction_id:"fix",instruction:"补充复核"})]);
    const n=instance(g);expect(n.status).toBe("waiting");expect(n.extra.passed).toBeUndefined();expect(n.extra.eval_detail).toBeUndefined();
  });
  it("基础设施中断不能残留历史通过标记",()=>{
    const g=fold([dispatch(),event("node.eval",{node:"ags_1",execution_id:"e1",passed:true}),event("node.status",{node:"ags_1",execution_id:"e1",status:"interrupted",infra:true,passed:false})]);
    expect(instance(g).extra.passed).toBeUndefined();
  });
  it("不认识的后端状态显示待核实，不能当成已完成或待命",()=>{
    const g=fold([dispatch(),event("node.status",{node:"ags_1",execution_id:"e1",status:"unexpected_state"})]);
    expect(instance(g).status).toBe("unknown");
  });
  it("过去批次的通过结果不随同一实例后续返工改变",()=>{
    const g=fold([dispatch(),event("node.eval",{node:"ags_1",execution_id:"e1",passed:true}),event("graph.instruction",{node:"ags_1",parent:"ceo",execution_id:"e1",task_id:"t1",context_mode:"continue",instruction_id:"fix",instruction:"补充复核"})]);
    const batches=displayGraph(g).nodes.filter((n:GraphNode)=>n.type==="task");
    expect(batches[0].extra.contracts?.[0].status).toBe("done");
    expect(batches[1].extra.contracts?.[0].status).toBe("waiting");
  });
});
