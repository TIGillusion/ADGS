import { expect, test } from "@playwright/test";

test("外部网络不可用时使用本机字体，页面与截图无需等待远程字体",async({page})=>{
  const remoteFonts:string[]=[];
  page.on("request",request=>{if(/fonts\.(googleapis|gstatic)\.com/.test(request.url()))remoteFonts.push(request.url());});
  await page.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/,route=>route.abort());
  await page.request.post("/e2e/reset");
  await page.goto("/");
  await page.waitForFunction(()=>window.__adgs_store?.getState().boot.status==="ok");
  await expect(page.getByRole("button",{name:"对话工作台",exact:true})).toBeVisible();
  await expect.poll(()=>page.evaluate(()=>document.fonts.status)).toBe("loaded");
  expect(remoteFonts).toEqual([]);
  await page.screenshot({timeout:5000});
});
