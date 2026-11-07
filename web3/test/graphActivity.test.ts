import { describe, expect, it } from "vitest";
import { applyEventToGraph, graphInit } from "@/lib/graph";
import { displayGraph } from "@/lib/graphFlow";
import { edgeSignal } from "@/lib/graphActivity";
import { executionGraphStatus } from "@/lib/executionState";

const dispatch = () => applyEventToGraph(graphInit(),{id:1,type:"graph.dispatch",ts:100,data:{node:"a",execution_id:"e",task_id:"t",parent:"ceo"}},100000);
describe("消息流动与执行状态相互独立",()=>{
  it("派发流点有事件依据且自动过期，排队不会永远传输",()=>{
    const g=displayGraph(dispatch()); const n=g.nodes.find(n=>n.type==="node")!;
    expect(n.status).toBe("waiting");
    expect(edgeSignal(g,n.parent!,n.key,101000,true)?.kind).toBe("dispatch");
    expect(edgeSignal(g,n.parent!,n.key,104000,true)).toBeNull();
    expect(edgeSignal(g,n.parent!,n.key,101000,false)).toBeNull();
  });
  it("历史/未来时间戳不制造流点，等待与执行状态不会续期",()=>{
    const graph=dispatch();
    const g=displayGraph(applyEventToGraph(graph,{id:2,type:"node.status",ts:105,data:{node:"a",execution_id:"e",status:"running"}},105000));
    const n=g.nodes.find(n=>n.type==="node")!;
    expect(edgeSignal(g,n.parent!,n.key,99000,true)).toBeNull();
    expect(edgeSignal(g,n.parent!,n.key,106000,true)).toBeNull();
  });
  it("下属汇报沿原委派路径逆向流动，并在有限时间内结束",()=>{
    const g=displayGraph(applyEventToGraph(dispatch(),{id:2,type:"node.eval",ts:110,data:{node:"a",execution_id:"e",passed:true}},110000));
    const n=g.nodes.find(n=>n.type==="node")!;
    expect(edgeSignal(g,n.parent!,n.key,111000,true)?.kind).toBe("report");
    expect(edgeSignal(g,n.parent!,n.key,114000,true)).toBeNull();
  });
});
describe("运行时检查点语义",()=>{
  it.each([
    [{state:"running",phase:"queued",runtime_active:true},"waiting"],
    [{state:"running",phase:"execute",runtime_active:true,waiting_for_children:true},"delegating"],
    [{state:"running",phase:"acceptance",runtime_active:true},"evaluating"],
    [{state:"running",phase:"execute",runtime_active:false},"interrupted"],
    [{state:"passed",phase:"finished",runtime_active:false},"done"],
    [{state:"waiting_user",phase:"finished"},"waiting_user"],
    [{state:"infra_failure",phase:"finished"},"interrupted"],
  ])("%j → %s",(record,expected)=>expect(executionGraphStatus(record)).toBe(expected));
});
