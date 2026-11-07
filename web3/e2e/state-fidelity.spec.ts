import { expect, test, type Page } from "@playwright/test";

async function event(page:Page,type:string,data:Record<string,unknown>) {
  await page.evaluate(({type,data})=>window.__adgs_store!.handleEvent({type,data,ts:Date.now()/1000}),{type,data});
}
async function drawn(page:Page,key="a@e1") {
  return page.locator("canvas").evaluate((canvas,key)=>{
    const states=(canvas as HTMLCanvasElement & {__adgsNodeStates?:{key:string;status:string;color:string}[]}).__adgsNodeStates;
    return states?.find(item=>item.key===key);
  },key);
}
async function traffic(page:Page,kind?:string) {
  return page.locator("canvas").evaluate((canvas,kind)=>{
    const values=(canvas as HTMLCanvasElement & {__adgsTraffic?:{kind:string|null}[]}).__adgsTraffic || [];
    return values.filter(item=>kind ? item.kind===kind : item.kind!==null).length;
  },kind);
}
test.beforeEach(async({page})=>{
  await page.request.post("/e2e/reset");
  await page.route("**/api/state*",async route=>{
    const result=await route.fetch();
    await route.fulfill({json:{...await result.json(),busy:true,snapshot_at:Date.now()/1000}});
  });
  await page.goto("/");
  await page.waitForFunction(()=>window.__adgs_store?.getState().boot.status==="ok" && window.__adgs_store.getState().sse==="open");
  await event(page,"graph.dispatch",{node:"a",task_id:"t1",execution_id:"e1",parent:"ceo",task_md:"实际完整任务"});
  await page.getByRole("button",{name:"演化图谱",exact:true}).click();
  await expect.poll(()=>drawn(page)).toBeTruthy();
});

test("渲染颜色与真实语义一致，浅深主题同用图例色",async({page})=>{
  for(const [status,variable] of [["waiting","--canvas-waiting"],["running","--canvas-running"],["delegating","--canvas-delegating"],["evaluating","--canvas-evaluating"],["waiting_user","--canvas-waiting"],["interrupted","--canvas-waiting"],["unjudged","--canvas-waiting"],["blocked","--canvas-waiting"],["done","--canvas-done"],["failed","--canvas-failed"],["unknown","--canvas-idle"]]) {
    await event(page,"node.status",{node:"a",execution_id:"e1",status});
    const color=await page.evaluate(variable=>getComputedStyle(document.documentElement).getPropertyValue(variable).trim(),variable);
    await expect.poll(()=>drawn(page)).toMatchObject({status,color});
  }
  await page.getByTitle("切换到黑夜模式").click();
  await event(page,"node.status",{node:"a",execution_id:"e1",status:"done"});
  const color=await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue("--canvas-done").trim());
  await expect.poll(()=>drawn(page)).toMatchObject({status:"done",color});
  await page.getByText("节点状态颜色",{exact:true}).click();
  await expect(page.locator('[data-graph-status="interrupted"]')).toHaveText("中断");
  await expect(page.locator('[data-graph-status="waiting_user"]')).toHaveText("等待补充");
});

test("派发与汇报短时流动，排队、等待和断线不会无限传输",async({page})=>{
  await expect.poll(()=>traffic(page,"dispatch")).toBeGreaterThan(0);
  await page.waitForTimeout(3200);
  expect(await traffic(page)).toBe(0);
  await event(page,"node.status",{node:"a",execution_id:"e1",status:"delegating"});
  expect(await traffic(page)).toBe(0);
  await event(page,"node.eval",{node:"a",execution_id:"e1",passed:true,score:92});
  await expect.poll(()=>traffic(page,"report")).toBeGreaterThan(0);
  await page.route("**/api/events*",route=>route.abort());
  await page.evaluate(()=>window.__adgs_store!.reattachSse());
  await expect.poll(()=>traffic(page)).toBe(0);
  await expect(page.getByText(/连接中断，显示最近记录/)).toBeVisible();
});

test("详情等待状态与图谱一致，快照补回完成结果",async({page})=>{
  await page.route("**/api/executions/e1",route=>route.fulfill({json:{ok:true,execution:{execution_id:"e1",node_id:"a",profile:"a",task_id:"t1",run_id:"r",state:"running",phase:"execute",runtime_active:true,waiting_for_children:true,attempts:[],events:[]}}}));
  await event(page,"node.status",{node:"a",execution_id:"e1",status:"delegating"});
  await page.evaluate(()=>window.__adgs_store!.selectInst("a@e1"));
  await expect(page.locator(".execution-state")).toHaveText("等待下属汇报");
  await page.keyboard.press("Escape");
  await page.route("**/api/state*",async route=>{
    const result=await route.fetch();
    await route.fulfill({json:{...await result.json(),busy:false,snapshot_at:Date.now()/1000,executions:[{execution_id:"e1",node_id:"a",state:"passed",phase:"finished",runtime_active:false}]}});
  });
  await page.evaluate(()=>window.__adgs_store!.refreshState());
  await expect.poll(()=>drawn(page)).toMatchObject({status:"done"});
  expect(await traffic(page)).toBe(0);
});

test("同一种群的不同实例不串色，旧的传输不会因刷新复活",async({page})=>{
  await event(page,"graph.dispatch",{node:"a",task_id:"t1",execution_id:"e2",parent:"ceo"});
  await event(page,"node.status",{node:"a",execution_id:"e1",status:"done"});
  await expect.poll(()=>drawn(page,"a@e1")).toMatchObject({status:"done"});
  await expect.poll(()=>drawn(page,"a@e2")).toMatchObject({status:"waiting"});
  await page.waitForTimeout(3200);
  await page.getByRole("button",{name:"对话工作台",exact:true}).click();
  await page.getByRole("button",{name:"演化图谱",exact:true}).click();
  await expect.poll(()=>traffic(page)).toBe(0);
});

test("聊天顶部暂停、受阻与失败使用对应状态色，等待下属不显示执行旋转",async({page})=>{
  await page.getByRole("button",{name:"对话工作台",exact:true}).click();
  const badge=page.getByTestId("ceo-status");
  for(const [phase,status,variable] of [["paused","interrupted","--canvas-waiting"],["blocked","blocked","--canvas-waiting"],["failed","failed","--canvas-failed"],["completed","done","--canvas-done"],["idle","idle","--canvas-idle"],["waiting_children","delegating","--canvas-delegating"]]) {
    await event(page,"chat.ceo_activity",{execution_id:"ceo-fixture",phase,updated_at:Date.now()/1000});
    await expect(badge).toHaveAttribute("data-status",status);
    const expected=await page.evaluate(variable=>{
      const probe=document.createElement("span");probe.style.color=`var(${variable})`;document.body.append(probe);
      const color=getComputedStyle(probe).color;probe.remove();return color;
    },variable);
    await expect(badge).toHaveCSS("color",expected);
    await expect(badge.locator(".animate-spin")).toHaveCount(0);
  }
});
