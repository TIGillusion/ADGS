import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';

const browser=await chromium.launch();
const page=await browser.newPage({viewport:{width:1600,height:1000}});
const errors=[];page.on('pageerror',error=>errors.push(String(error)));
// Read only against the still-running old server. History selection is verified
// using a stub response so no production session or graph is changed.
let selected=null;
await page.route('**/api/**',async route=>{
  const request=route.request();
  if(request.method()==='GET')return route.continue();
  if(request.url().endsWith('/api/session/load')){
    selected=request.postDataJSON().name;
    return route.fulfill({json:{ok:true,messages:[{kind:'user',text:'历史载入验证'}],graph:[],events:[]}});
  }
  return route.abort();
});
await page.goto(process.env.ADGS_URL||'http://127.0.0.1:8021/web3/');
await page.waitForFunction(()=>window.__adgs_store?.getState().projects.length>0);
const chats=await page.evaluate(()=>window.__adgs_store.getState().projects.flatMap(p=>p.sessions).map(s=>s.id));
assert(chats.length>0);
assert.equal(await page.locator('.project-chat').count(),chats.length);
const toggle=page.locator('.project-toggle').first();
assert.equal(await toggle.getAttribute('aria-expanded'),'true');
await toggle.click();assert.equal(await toggle.getAttribute('aria-expanded'),'false');
await toggle.click();assert.equal(await toggle.getAttribute('aria-expanded'),'true');
const search=page.getByRole('textbox',{name:'搜索项目和会话'});
await search.fill('此搜索不匹配任何现有会话');assert.equal(await page.locator('.project-chat').count(),0);
await search.fill('');
await page.screenshot({path:'test/.legacy-workspaces.png'});
await page.locator('.project-chat-open').first().click();
await page.getByText('历史载入验证',{exact:true}).waitFor();
assert.equal(await page.evaluate(()=>window.__adgs_store.getState().viewSession),selected);
assert.deepEqual(errors,[]);
await browser.close();
console.log(`Legacy sidebar: ${chats.length} existing chats, workspace groups, collapse, search, selection: PASS (production requests read only)`);
