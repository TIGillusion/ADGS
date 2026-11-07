import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.request.post("/e2e/reset");
  await page.goto("/");
  await page.waitForFunction(() => window.__adgs_store?.getState().boot.status === "ok");
});

async function showLongResult(page: import("@playwright/test").Page) {
  await page.evaluate(() => {
    const store = window.__adgs_store!;
    store.handleEvent({type:"chat.user",ts:1,data:{text:"请完成复杂项目的独立复核，保存完整报告并清楚地说明证据。".repeat(12)}});
    store.handleEvent({type:"chat.ceo_finish",ts:2,data:{status:"completed",summary:"## 完成复核\n\n**结论与证据**\n\n" + "核对全部产物及计算记录。\n\n".repeat(20),artifacts:Array.from({length:24},(_,i)=>`D:\\中文项目\\长期研究\\round34\\报告${i}.md`)}});
  });
}

test("长文件列表能展开和收起，底部浮动按钮不造成滚动抖动", async ({ page }) => {
  await showLongResult(page);
  await expect(page.locator(".artifact-file")).toHaveCount(4);
  await page.getByRole("button",{name:"查看全部 24 个文件"}).click();
  await expect(page.locator(".artifact-file")).toHaveCount(24);
  // Real pointer click must scroll to a stable target without force or JS click.
  await page.getByRole("button",{name:"收起文件列表"}).click();
  await expect(page.locator(".artifact-file")).toHaveCount(4);
  await page.getByRole("button",{name:"查看全部 24 个文件"}).click();
  await page.getByRole("button",{name:"收起文件列表"}).click();
  await expect(page.locator(".artifact-file")).toHaveCount(4);
});

test("窄屏长用户消息保持可读宽度，导航使用可关闭抽屉", async ({ page }) => {
  await page.setViewportSize({width:390,height:900});
  await page.reload();
  await page.waitForFunction(() => window.__adgs_store?.getState().boot.status === "ok");
  await showLongResult(page);
  const bubble=page.locator(".msg-in.justify-end>div").first();
  expect((await bubble.boundingBox())!.width).toBeGreaterThan(220);
  await expect(page.locator(".project-sidebar")).toBeHidden();
  await page.getByRole("button",{name:"展开项目导航",exact:true}).click();
  await expect(page.locator(".project-sidebar")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".project-sidebar")).toBeHidden();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.getByRole("button",{name:"查看任务队列",exact:true}).click();
  const queue=await page.getByRole("dialog",{name:"任务队列",exact:true}).boundingBox();
  expect(queue!.x).toBeGreaterThanOrEqual(48);
  expect(queue!.x+queue!.width).toBeLessThanOrEqual(390);
});

test("浅深主题与导航偏好在刷新后保留", async ({ page }) => {
  await page.getByTitle("切换到黑夜模式").click();
  await page.getByRole("button",{name:"收起项目导航",exact:true}).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme","dark");
  await expect(page.locator(".project-sidebar")).toBeHidden();
  await page.getByRole("button",{name:"展开项目导航",exact:true}).click();
  await page.getByTitle("切换到白天模式").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme","light");
});

test("输入区随多行内容和窗口宽度变化调整高度", async ({ page }) => {
  const input=page.getByRole("textbox",{name:"消息内容"});
  const initial=(await input.boundingBox())!.height;
  await input.fill("一行\n二行\n三行\n四行");
  await expect.poll(async()=>(await input.boundingBox())!.height).toBeGreaterThan(initial+40);
  await input.fill("这是一条用于检查窗口变窄后输入区自动换行并增高的消息。".repeat(3));
  const wide=(await input.boundingBox())!.height;
  await page.getByRole("button",{name:"收起项目导航",exact:true}).click();
  await page.setViewportSize({width:390,height:900});
  await expect.poll(async()=>(await input.boundingBox())!.height).toBeGreaterThan(wide);
});

test("窄屏节点卡片与概览可读，节点可用键盘打开", async ({ page }) => {
  await page.setViewportSize({width:390,height:900});
  await page.reload();
  await page.waitForFunction(() => window.__adgs_store?.getState().boot.status === "ok");
  await page.getByRole("button",{name:"节点种群",exact:true}).click();
  await expect(page.locator(".node-row")).toHaveCount(3);
  const row=page.locator(".node-row").first();
  const box=(await row.boundingBox())!;
  const items=await row.locator(".node-row-main>*:visible").evaluateAll(els=>els.map(el=>{const b=el.getBoundingClientRect();return {left:b.left,right:b.right};}));
  expect(items.every(item=>item.left>=box.x && item.right<=box.x+box.width)).toBe(true);
  await page.getByRole("textbox",{name:"搜索节点和生态位"}).fill("不存在的生态位");
  await expect(page.getByText("没有匹配的节点",{exact:true})).toBeVisible();
  await page.getByRole("textbox",{name:"搜索节点和生态位"}).fill("");
  await row.locator(".node-row-name").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".drawer-in")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button",{name:"系统概览",exact:true}).click();
  await expect(page.locator(".overview-view")).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
