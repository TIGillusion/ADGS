import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { NodesView } from "@/components/NodesView";
import { OverviewView } from "@/components/OverviewView";
import { store } from "@/lib/store";
import { applyEventToGraph, graphInit } from "@/lib/graph";
import { displayGraph } from "@/lib/graphFlow";
import type { BusEvent, GraphNode } from "@/types";

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
describe("图谱执行状态精确投影",()=>{
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
