import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1600,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(String(e)));
await page.goto(process.env.FLOW_REPLAY_URL || 'http://127.0.0.1:8107/');
await page.waitForFunction(()=>window.__adgs_store?.getState().boot.status==='ok');
let id=2000000000;
const e=(type,data)=>({id:++id,ts:id,type,data,session:'flow-isolated-test'});
const input='【隔离测试】用户完整输入\n'+ '详细要求。'.repeat(700)+'用户输入末尾';
const events=[e('chat.user',{text:input}),
 e('graph.task',{id:'t1',stage_id:'s1',parent:'ceo',task_md:'任务一完整任务书尾部'}),
 e('graph.task',{id:'t2',stage_id:'s1',parent:'ceo',task_md:'任务二完整任务书尾部'}),
 e('graph.dispatch',{node:'ags_a',execution_id:'ea',task_id:'t1',parent:'ceo',task_md:'任务一完整任务书尾部'}),
 e('graph.dispatch',{node:'ags_b',execution_id:'eb',task_id:'t2',parent:'ceo',task_md:'任务二完整任务书尾部'}),
 e('graph.instruction',{node:'ags_a',execution_id:'ea',task_id:'t1',parent:'ceo',instruction_id:'fix1',context_mode:'continue',instruction:'继续原上下文，只修复报告缺项'}),
 e('graph.task',{id:'t3',stage_id:'s3',parent:'ceo',task_md:'同配置新上下文任务'}),
 e('graph.dispatch',{node:'ags_a',execution_id:'ea-new',task_id:'t3',parent:'ceo',task_md:'同配置新上下文任务'}),
 e('a2a.delegate',{delegation_id:'d1',parent:'ags_a',parent_execution_id:'ea',node:'ags_c',child_execution_id:'ec',batch_id:'childbatch',summary:'子任务完整内容',status:'running'}),
 e('chat.ceo_tool',{name:'read_file',args:{path:'example'},result:'操作详情\n'+'完整结果。'.repeat(700)+'工具结果末尾'})];
await page.evaluate(events=>{const s=window.__adgs_store;s.set({graph:s.foldGraph(events),viewSession:'flow-isolated-test',graphManual:new Map(),graphLayoutMode:'tree',graphFocusRound:null,layoutCycle:null});},events);
await page.locator('nav button',{hasText:'演化图谱'}).first().click();await page.waitForTimeout(3200);
const snap=await page.evaluate(()=>({nodes:window.__adgs_store.getState().graph.flow.nodes,links:window.__adgs_store.getState().graph.flow.links}));
assert.equal(snap.nodes.filter(n=>n.type==='round'&&n.parent==='ceo').length,1);
assert.equal(snap.nodes.filter(n=>n.type==='task'&&n.parent==='round-1').length,3);
assert.equal(snap.nodes.filter(n=>n.type==='node'&&n.id==='ags_a').length,2);
assert.equal(snap.nodes.filter(n=>n.type==='round'&&n.parent==='ags_a@ea').length,2);
assert.equal(snap.links.length,1);
async function clickNode(key) {
 const point=await page.locator('canvas').evaluate((c,key)=>{const p=c.__adgsDrawnLayout.find(n=>n.key===key);const v=c.__adgsView;const b=c.getBoundingClientRect();return {x:b.x+v.x+p.x*v.k,y:b.y+v.y+p.y*v.k};},key);
 await page.mouse.click(point.x,point.y);await page.waitForTimeout(200);
}
await page.screenshot({path:'test/.unified-flow-tree.png'});
await clickNode('round-1');await page.getByText('用户完整输入',{exact:true}).waitFor();
assert((await page.locator('.drawer-in').innerText()).includes('用户输入末尾'));
await page.getByText('CEO 操作 · read_file',{exact:false}).click();assert((await page.locator('.drawer-in').innerText()).includes('工具结果末尾'));
await page.screenshot({path:'test/.unified-flow-round-detail.png'});await page.getByRole('button',{name:'关闭',exact:true}).click();
const batch=snap.nodes.find(n=>n.type==='task'&&n.parent==='round-1'&&n.label==='1');
await clickNode(batch.key);await page.getByText('本批委派任务（2）',{exact:true}).waitFor();
await page.locator('.drawer-in details summary').first().click();assert((await page.locator('.drawer-in').innerText()).includes('任务一完整任务书尾部'));
await page.screenshot({path:'test/.unified-flow-batch-detail.png'});await page.getByRole('button',{name:'关闭',exact:true}).click();
const round=snap.nodes.find(n=>n.type==='round'&&n.parent==='ags_a@ea'&&n.label==='轮 2');
await clickNode(round.key);await page.getByText('上级完整指令',{exact:true}).waitFor();assert((await page.locator('.drawer-in').innerText()).includes('继续原上下文，只修复报告缺项'));
await page.getByRole('button',{name:'关闭',exact:true}).click();
console.log('Specified scenarios: one CEO round; batches 1/2/3; same-context continuation; new-context instance; recursive worker delegation; full details: PASS');
const real=JSON.parse(readFileSync('test/.a2a-real-events.json','utf8'));
for(const session of ['a2a-live-test','mijia_lab']) {
 await page.evaluate(({real,session})=>{const s=window.__adgs_store;s.set({graph:s.foldGraph(real.filter(e=>e.session===session)),viewSession:session,graphManual:new Map(),graphLayoutMode:'radial',graphFocusRound:null});},{real,session});
 await page.getByTitle('适应全图').click().catch(async()=>{await page.locator('canvas').dblclick({position:{x:10,y:10}});});
 await page.waitForTimeout(3200);
 const data=await page.evaluate(()=>window.__adgs_store.getState().graph.flow);
 assert(data.nodes.some(n=>n.type==='node'));assert(data.nodes.some(n=>n.type==='round'&&n.parent!=='ceo'));
 const keys=new Set(data.nodes.map(n=>n.key));assert(data.nodes.every(n=>!n.parent||keys.has(n.parent)));
 await page.screenshot({path:`test/.unified-real-${session}.png`});
 console.log(session, data.nodes.length,'unified nodes',data.links.length,'additional continuation links');
}
assert.deepEqual(errors,[]);await browser.close();
