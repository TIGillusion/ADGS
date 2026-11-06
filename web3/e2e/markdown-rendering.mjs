import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const session='auto-20261102-214253-1e88a3';
const file=path.resolve('../data/sessions',session+'.json');
const digest=()=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const before=digest();
const browser=await chromium.launch();
try {
const context=await browser.newContext({viewport:{width:1600,height:1100},permissions:['clipboard-read','clipboard-write']});
const page=await context.newPage();
const capture=async(element,name)=>{
  await page.locator('.chat-scroll').evaluate(el=>{el.scrollTop=0;el.dispatchEvent(new Event('scroll',{bubbles:true}));});
  await element.evaluate(el=>{
    const scroller=el.closest('.chat-scroll');
    scroller.scrollTop+=el.getBoundingClientRect().top-scroller.getBoundingClientRect().top-14;
  });
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.screenshot({path:`test/${name}`,animations:'disabled'});
};
const errors=[];page.on('pageerror',error=>errors.push(String(error)));
await page.route('**/api/**',route=>{
  const req=route.request();
  if(req.method()==='GET'||req.url().endsWith('/api/session/load'))return route.continue();
  if(req.url().endsWith('/api/session/graph'))return route.fulfill({json:{ok:true}});
  return route.abort();
});
await page.goto('http://127.0.0.1:8021/web3/');
await page.waitForFunction(()=>window.__adgs_store?.getState().projects.length);
await page.locator(`[data-session-id="${session}"] .project-chat-open`).click();
await page.waitForFunction(id=>window.__adgs_store.getState().viewSession===id&&window.__adgs_store.getState().messages.some(m=>m.kind==='finish'),session);
await page.getByTitle('切换到黑夜模式').click();
const real=page.locator('.surface').filter({has:page.getByText('任务完成',{exact:true})}).last();
assert(await real.locator('.markdown-body h2').count()>=5);
assert(await real.locator('.markdown-body strong').count()>3);
assert(await real.locator('.katex').count()>10);
assert(await real.locator('blockquote').count()>0);
await page.evaluate(()=>document.fonts.ready);
await capture(real,'.markdown-real-dark.png');

const sample=['## 排版与验收','',
  '**结论：验证通过，证明仍有缺口。** 行内公式 $p\\equiv1\\pmod{12}$ 与 `report.md` 现在可以直接阅读。','',
  '> 数值验证不等于数学证明；完成的工作与剩余缺口需要分别列出。','',
  '1. 检查任务证据','   - 对照原始需求','   - 独立重算关键值','2. 记录后续工作','',
  '| 任务 | 类型 | 证据 | 结论 |','| --- | --- | --- | --- |','| T1 | 独立复核 | 4480 次验证 | 待证明 |','| T2 | 定理 | 完整证明链 | 通过 |','',
  '- [x] 产出已保存','- [ ] 补齐剩余证明','',
  '$$','\\frac{4}{n}=\\frac{1}{x}+\\frac{1}{y}+\\frac{1}{z}','$$','',
  '```python','def verify(n):','    return n % 12 == 1','```','',
  '## 宽内容边界','',
  '$$','S='+Array.from({length:50},(_,i)=>`x_{${i}}`).join('+'),'$$','',
  '| '+Array.from({length:10},(_,i)=>`宽表列${i}`).join(' | ')+' |',
  '| '+Array(10).fill('---').join(' | ')+' |',
  '| '+Array(10).fill('完整记录与独立复核证据').join(' | ')+' |',
].join('\n');
await page.evaluate(({session,sample})=>window.__adgs_store.handleEvent({type:'chat.ceo_finish',ts:Date.now()/1000,session,data:{summary:sample,artifacts:[]}}),{session,sample});
const body=page.locator('.surface').filter({has:page.getByText('任务完成',{exact:true})}).last();
assert.equal(await body.locator('table').count(),2);
assert.equal(await body.locator('input[type=checkbox]').count(),2);
await body.getByRole('button',{name:'复制代码'}).click();
assert.equal((await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n'),'def verify(n):\n    return n % 12 == 1');
const bounds=async()=>{
  const size=await body.evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));
  assert(size.scroll<=size.width+1,`Markdown escapes message: ${JSON.stringify(size)}`);
};
await bounds();await capture(body,'.markdown-sample-dark.png');
await page.getByTitle('切换到白天模式').click();
await bounds();await capture(body,'.markdown-sample-light.png');
await page.setViewportSize({width:1000,height:900});
await bounds();await capture(body,'.markdown-sample-narrow.png');
assert.equal(digest(),before,'Rendering must not change saved conversation contents');
assert.deepEqual(errors,[]);
console.log('Markdown: actual saved report, headings/emphasis/lists/quotes/math, GFM tables/tasks, code copy, light/dark, narrow overflow and unchanged history: PASS');
} finally {await browser.close();}
