import { afterEach, describe, expect, it, vi } from "vitest";
import { GraphCanvas, layoutTree } from "@/lib/graphCanvas";
import { graphInit } from "@/lib/graph";
import { flowFromLegacy } from "@/lib/graphFlow";

afterEach(()=>vi.restoreAllMocks());
describe("真实 Canvas 连线绘制",()=>{
  it("派发向子节点流动，汇报向上级流动，过期后不再画流点",()=>{
    const graph=graphInit();
    graph.nodes.push({key:"a@e",id:"a",type:"node",label:"a",parent:"ceo",status:"waiting",extra:{}});
    graph.flow={...flowFromLegacy(graph),signals:{["ceo\u0000a@e"]:{ts:100,eventId:"1",kind:"dispatch"}}};
    const arcs:number[][]=[];
    const ctx=new Proxy({measureText:()=>({width:20}),createLinearGradient:()=>({addColorStop:()=>{}}),arc:(...args:number[])=>arcs.push(args)}, {get:(obj,key)=>key in obj ? obj[key as keyof typeof obj] : ()=>{}});
    const canvas=document.createElement("canvas");canvas.getContext=vi.fn(()=>ctx) as any;
    const engine=new GraphCanvas(canvas,{getGraph:()=>graph,getLive:()=>true,selected:()=>null,handlers:{}});
    const e=engine as any;
    const layout=layoutTree(graph).nodes;
    const parent=layout.find(n=>n.n.key==="ceo")!, child=layout.find(n=>n.n.key==="a@e")!;
    e.anim=new Map(layout.map(l=>[l.n.key,{...l,scale:1,opacity:1}]));
    e.edges=[{from:parent,to:child}];e.edgeBirth.set("ceo\u0000a@e",0);
    const clock=vi.spyOn(Date,"now").mockReturnValue(101000);
    const position=(t:number)=>{arcs.length=0;e.drawEdges(ctx,t,1000);return arcs[0];};
    const a=position(0),b=position(.1),dx=child.x-parent.x,dy=child.y-parent.y;
    expect((b[0]-a[0])*dx+(b[1]-a[1])*dy).toBeGreaterThan(0);
    graph.flow.signals!["ceo\u0000a@e"].kind="report";
    const c=position(0),d=position(.1);
    expect((d[0]-c[0])*dx+(d[1]-c[1])*dy).toBeLessThan(0);
    clock.mockReturnValue(104000);arcs.length=0;e.drawEdges(ctx,.2,1000);
    expect(arcs).toHaveLength(0);
    engine.destroy();
  });
});
