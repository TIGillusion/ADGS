import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const root=path.resolve('..');
const evidence=path.join(root,'.adgs_runtime/reopen-check-20261103');
const baseline=JSON.parse(fs.readFileSync(path.join(evidence,'before.json'),'utf8'));
const profile=path.join(evidence,'browser-profile');
const errors=[];
const hash=value=>crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const launch=async()=>{
  const context=await chromium.launchPersistentContext(profile,{viewport:{width:1600,height:1000}});
  // Loading should not rewrite the saved graph while collecting file evidence.
  await context.route('**/api/session/graph',route=>route.fulfill({json:{ok:true}}));
  for(const page of context.pages())page.on('pageerror',error=>errors.push(String(error)));
  const page=context.pages()[0]||await context.newPage();
  await page.goto('http://127.0.0.1:8021/web3/');
  await page.waitForFunction(()=>window.__adgs_store?.getState().projects.length>0);
  return {context,page};
};
const fingerprint=page=>page.evaluate(()=>{
  const s=window.__adgs_store.getState();
  return {session:s.viewSession,messages:s.messages.filter(m=>m.kind!=='system').map(({id,ts,...m})=>m),
    nodes:s.graph.nodes.map(n=>[n.key,n.parent,n.type]),
    projectChats:s.projects.flatMap(p=>p.sessions).map(c=>c.id).sort()};
});
let {context,page}=await launch();
await page.locator(`[data-session-id="${baseline.session}"] .project-chat-open`).click();
await page.waitForFunction(id=>window.__adgs_store.getState().viewSession===id&&window.__adgs_store.getState().messages.some(m=>m.kind==='finish'),baseline.session);
const before=await fingerprint(page);
assert.equal(before.projectChats.length,baseline.sessions ? Object.keys(baseline.sessions).length : 53);
assert.equal(before.messages.filter(m=>m.kind==='finish').at(-1).artifacts.length,13);
await page.locator('textarea').last().fill('关闭重开校验草稿——不发送');
await page.getByTitle('切换到黑夜模式').click();
await page.locator('.artifact-path').last().scrollIntoViewIfNeeded();
const checkBounds=async()=>{
  const overflow=await page.locator('.artifact-path').evaluateAll(elements=>elements.filter(el=>{
    const a=el.getBoundingClientRect(),b=el.closest('.surface').getBoundingClientRect();
    return a.left<b.left-1 || a.right>b.right+1 || el.scrollWidth>el.clientWidth+1;
  }).length);
  assert.equal(overflow,0,'Long artifact paths must remain inside the message card');
};
await checkBounds();
await page.screenshot({path:'test/.reopen-artifacts-dark.png'});
await context.close();
({context,page}=await launch());
await page.waitForFunction(id=>window.__adgs_store.getState().viewSession===id&&window.__adgs_store.getState().messages.some(m=>m.kind==='finish'),baseline.session);
const after=await fingerprint(page);
assert.equal(hash(after),hash(before),'Chat, graph and project sidebar must survive a full browser close');
assert.equal(await page.locator('textarea').last().inputValue(),'关闭重开校验草稿——不发送');
assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
await checkBounds();
await page.setViewportSize({width:1000,height:850});await checkBounds();
await page.locator('.artifact-path').last().scrollIntoViewIfNeeded();
await page.screenshot({path:'test/.reopen-artifacts-narrow.png'});
// Remove only the synthetic draft from this dedicated test profile.
await page.locator('textarea').last().fill('');
assert.deepEqual(errors,[]);
await context.close();
console.log(`Full browser close/reopen: ${before.projectChats.length} chats, ${before.messages.length} rendered messages, graph, 13 final artifacts, draft and theme restored; wide/narrow overflow: PASS`);
