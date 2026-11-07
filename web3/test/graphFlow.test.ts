import { describe, expect, it } from "vitest";
import { applyEventToGraph, graphInit, graphFromSaved, type GraphState } from "@/lib/graph";
import { displayGraph } from "@/lib/graphFlow";
import type { BusEvent, SavedGraphNode } from "@/types";
let seq = 0;
const event = (type: string, data: Record<string, unknown>): BusEvent => ({id:++seq,type,ts:seq,data});
const fold = (events: BusEvent[], start = graphInit()) => events.reduce((s,e)=>applyEventToGraph(s,e,e.ts*1000),start);
const tasks = (g:GraphState, parent?:string) => displayGraph(g).nodes.filter(n=>n.type==='task'&&(!parent||n.parent===parent));
const instances = (g:GraphState) => displayGraph(g).nodes.filter(n=>n.type==='node');
const rounds = (g:GraphState, parent:string) => displayGraph(g).nodes.filter(n=>n.type==='round'&&n.parent===parent);
const user = () => event('chat.user',{text:'用户输入全文'.repeat(700)});
const prepare=(task:string,ex:string,stage:string)=>event('graph.task',{id:task,task_id:task,execution_id:ex,stage_id:stage,parent:'ceo',task_md:`任务书 ${task}`});
const dispatch=(task:string,ex:string,node:string)=>event('graph.dispatch',{task_id:task,execution_id:ex,node,parent:'ceo',task_md:`任务书 ${task}`});

describe('统一输入轮次与派发批次',()=>{
 it('Hermes CEO 阶段绑定根节点，不能变成Worker或额外用户轮次',()=>{
  const activity=(phase:string)=>event('chat.ceo_activity',{node:'ceo',engine:'hermes',role:'coordinator',phase,execution_id:'ceo-exec',attempt_id:'decision-1'});
  const initial=fold([activity('deciding')]);expect(rounds(initial,'ceo')).toHaveLength(0);
  const g=fold([user(),activity('deciding'),prepare('t1','e1','s1'),dispatch('t1','e1','a'),activity('waiting_children')]);
  expect(rounds(g,'ceo')).toHaveLength(1);expect(instances(g)).toHaveLength(1);
  const ceo=displayGraph(g).nodes.find(n=>n.key==='ceo')!;
  expect(ceo.extra.execution_id).toBe('ceo-exec');expect(ceo.status).toBe('delegating');
  const saved=displayGraph(g).nodes.map(n=>({...n,parent:n.parent??'',extra:n.key==='ceo'?{...n.extra,flow_snapshot:g.flow}:n.extra})) as SavedGraphNode[];
  expect(displayGraph(graphFromSaved(saved)).nodes.find(n=>n.key==='ceo')?.extra.ceo_activity?.phase).toBe('waiting_children');
 });
 it('两个并发任务共用序号1；完成后下一批是序号2',()=>{
  const g=fold([user(),prepare('t1','e1','s1'),prepare('t2','e2','s1'),dispatch('t1','e1','a'),dispatch('t2','e2','b'),prepare('t3','e3','s2'),dispatch('t3','e3','c')]);
  const batches=tasks(g,'round-1');expect(batches.map(b=>b.label)).toEqual(['1','2']);
  expect(batches[0].extra.contracts).toHaveLength(2);expect(instances(g)).toHaveLength(3);
  expect(instances(g).filter(n=>n.parent===batches[0].key)).toHaveLength(2);
 });
 it('同一上下文返工复用实例、增加子轮次和上级批次，不增加CEO轮次',()=>{
  const g=fold([user(),prepare('t1','e1','s1'),dispatch('t1','e1','a'),event('graph.instruction',{node:'a',execution_id:'e1',task_id:'t1',parent:'ceo',context_mode:'new',task_md:'完整初始任务书'}),event('graph.instruction',{node:'a',execution_id:'e1',task_id:'t1',parent:'ceo',context_mode:'continue',instruction_id:'fix1',instruction:'请修复缺项'})]);
  expect(instances(g)).toHaveLength(1);expect(rounds(g,'ceo')).toHaveLength(1);expect(rounds(g,instances(g)[0].key)).toHaveLength(2);
  expect(tasks(g,'round-1')).toHaveLength(2);expect(g.flow?.links).toHaveLength(1);
  expect(rounds(g,instances(g)[0].key)[1].extra.user_text).toBe('请修复缺项');
 });
 it('同一个种群ID的新execution产生新实例，各自输入轮次独立',()=>{
  const g=fold([user(),prepare('t1','e1','s1'),dispatch('t1','e1','a'),prepare('t2','e2','s2'),dispatch('t2','e2','a')]);
  expect(instances(g).map(n=>n.extra.execution_id)).toEqual(['e1','e2']);
  for(const n of instances(g)) expect(rounds(g,n.key)).toHaveLength(1);
 });
 it('worker递归委派使用自己的轮次和批次，没有虚拟锚点和可点击A2A边',()=>{
  const initial=[user(),prepare('t1','e1','s1'),dispatch('t1','e1','a')];
  const g=fold([...initial,event('a2a.delegate',{delegation_id:'d1',node:'b',parent:'a',parent_execution_id:'e1',child_execution_id:'e2',batch_id:'parallel1',summary:'完整子任务书1',status:'running'}),event('a2a.delegate',{delegation_id:'d2',node:'c',parent:'a',parent_execution_id:'e1',child_execution_id:'e3',batch_id:'parallel1',summary:'完整子任务书2',status:'running'})]);
  const r=rounds(g,'a@e1')[0];expect(tasks(g,r.key)).toHaveLength(1);expect(tasks(g,r.key)[0].extra.contracts).toHaveLength(2);
  expect(instances(g)).toHaveLength(3);expect(displayGraph(g).a2aEdges).toEqual([]);
 });
 it('输入和工具结果不截断，并记录在所属CEO轮次',()=>{
  const input=user();const result='完整结果'.repeat(2000);
  const g=fold([input,event('chat.ceo_tool',{name:'read_file',args:{path:'x'},result})]);
  expect(rounds(g,'ceo')[0].extra.user_text).toBe(input.data.text);
  expect(rounds(g,'ceo')[0].extra.operations?.at(-1)?.data.result).toBe(result);
 });
 it('保存恢复和相同事件重放不重复产生轮次批次',()=>{
  const events=[user(),prepare('t1','e1','s1'),dispatch('t1','e1','a')];const g=fold(events);
  const saved=displayGraph(g).nodes.map(n=>({...n,parent:n.parent??'',extra:n.key==='ceo'?{...n.extra,flow_snapshot:g.flow}:n.extra})) as SavedGraphNode[];
  const restored=fold(events,graphFromSaved(saved));
  expect(displayGraph(restored).nodes).toEqual(displayGraph(g).nodes);expect(restored.flow?.links).toEqual(g.flow?.links);
 });
 it('缺少执行ID的历史委派仅归并该任务的占位实例，收到明确ID后补全',()=>{
  const g=fold([user(),prepare('t1','e1','s1'),dispatch('t1','e1','a'),event('a2a.delegate',{delegation_id:'abcdef1234',node:'b',parent:'a',status:'running',summary:'原任务'}),event('graph.instruction',{node:'b',execution_id:'e2',task_id:'mesh_abcdef12',parent:'a',context_mode:'new',task_md:'原任务全文'})]);
  const child=instances(g).find(n=>n.id==='b')!;expect(child.extra.execution_id).toBe('e2');expect(rounds(g,child.key)).toHaveLength(1);
  expect(tasks(g).find(b=>b.extra.contracts?.some(c=>c.nodeId==='b'))?.extra.contracts).toHaveLength(1);
 });
});

it('旧存档没有交互快照也会转换为统一层级，并标注摘要来源',()=>{
 const g=graphFromSaved([
  {key:'round-1',id:'round-1',type:'round',label:'轮 1',parent:'ceo',status:'idle',extra:{user_text:'原用户输入'}},
  {key:'t1_1_abc_0',id:'t1_1_abc_0',type:'task',label:'1',parent:'round-1',status:'idle'},
  {key:'t1_1_abc_1',id:'t1_1_abc_1',type:'task',label:'2',parent:'round-1',status:'idle'},
  {key:'a@e1',id:'a',type:'node',label:'a',parent:'t1_1_abc_0',status:'done',extra:{execution_id:'e1',goal:'旧任务摘要'}},
  {key:'b@e2',id:'b',type:'node',label:'b',parent:'t1_1_abc_1',status:'done',extra:{execution_id:'e2'}},
 ]);
 expect(tasks(g,'round-1')).toHaveLength(1);expect(tasks(g,'round-1')[0].extra.contracts).toHaveLength(2);
 expect(rounds(g,'a@e1')).toHaveLength(1);expect(tasks(g)[0].extra.contracts?.[0].historical).toBe(true);
});
it('相同task编号的不同上下文分别保留任务记录，状态先到也不重复初始轮次',()=>{
 const g=fold([user(),event('node.status',{node:'a',execution_id:'e1',task_id:'same',status:'running'}),dispatch('same','e1','a'),dispatch('same','e2','a')]);
 expect(instances(g)).toHaveLength(2);expect(rounds(g,'a@e1')).toHaveLength(1);
 const contracts=tasks(g).flatMap(n=>n.extra.contracts||[]);
 expect(contracts.filter(c=>c.executionId==='e1')).toHaveLength(1);
 expect(contracts.filter(c=>c.executionId==='e2')).toHaveLength(1);
});
