import { describe, expect, it } from "vitest";
import { applyEventToGraph, graphInit } from "@/lib/graph";
import { displayGraph } from "@/lib/graphFlow";
import { reconcileGraphSnapshot } from "@/lib/graphSnapshot";
import type { StateSnapshot } from "@/types";

const graph=()=>applyEventToGraph(graphInit(),{id:1,type:"graph.dispatch",ts:10,data:{node:"a",execution_id:"e",task_id:"t"}},10000);
const snapshot=(overrides:Partial<StateSnapshot>={})=>({snapshot_at:20,busy:true,executions:[],...overrides}) as StateSnapshot;
const node=(g:ReturnType<typeof graph>)=>displayGraph(g).nodes.find(n=>n.type==="node")!;
describe("后端快照修正图谱",()=>{
  it("补回漏掉的结束状态，不新增轮次、操作或消息流点",()=>{
    const g=graph(), count=g.flow?.nodes.length;
    const next=reconcileGraphSnapshot(g,snapshot({executions:[{execution_id:"e",node_id:"a",state:"passed",phase:"finished"}]}));
    expect(node(next).status).toBe("done");expect(node(next).extra.passed).toBe(true);
    expect(next.flow?.nodes).toHaveLength(count!);expect(next.flow?.signals).toEqual(g.flow?.signals);
    expect(next.flow?.seen).toEqual(g.flow?.seen);
  });
  it("只修正同一 execution 与 node 的实例，不凭种群ID归因",()=>{
    expect(node(reconcileGraphSnapshot(graph(),snapshot({executions:[{execution_id:"other",node_id:"a",state:"passed"}]}))).status).toBe("waiting");
    expect(node(reconcileGraphSnapshot(graph(),snapshot({executions:[{execution_id:"e",node_id:"other",state:"passed"}]}))).status).toBe("waiting");
  });
  it("迟到快照不能覆盖更晚的运行事件",()=>{
    const g=applyEventToGraph(graph(),{id:2,type:"node.status",ts:30,data:{node:"a",execution_id:"e",status:"running"}},30000);
    expect(node(reconcileGraphSnapshot(g,snapshot({executions:[{execution_id:"e",node_id:"a",state:"passed"}]}))).status).toBe("running");
  });
  it("历史运行检查点与真实待下属状态区分",()=>{
    const record={execution_id:"e",node_id:"a",state:"running",phase:"execute",runtime_active:false};
    expect(node(reconcileGraphSnapshot(graph(),snapshot({executions:[record]}))).status).toBe("interrupted");
    expect(node(reconcileGraphSnapshot(graph(),snapshot({executions:[{...record,runtime_active:true,waiting_for_children:true}]}))).status).toBe("delegating");
  });
  it("结束快照先到时，迟到验收仍补齐评分和证据，不复活旧运行状态",()=>{
    const confirmed=reconcileGraphSnapshot(graph(),snapshot({executions:[{execution_id:"e",node_id:"a",state:"passed"}]}));
    const evaluated=applyEventToGraph(confirmed,{id:2,type:"node.eval",ts:15,data:{node:"a",execution_id:"e",passed:true,score:95,detail:"真实验收证据"}},15000);
    expect(node(evaluated).status).toBe("done");expect(node(evaluated).extra.score).toBe(95);expect(node(evaluated).extra.eval_detail).toBe("真实验收证据");
    const late=applyEventToGraph(evaluated,{id:3,type:"node.status",ts:16,data:{node:"a",execution_id:"e",status:"running"}},16000);
    expect(node(late).status).toBe("done");
  });
});
