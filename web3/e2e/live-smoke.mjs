import { chromium } from "@playwright/test";

// 真后端 smoke + 60s SSE 稳定性：验证看门狗修复后空闲连接不再 ~50s 误重建
const BASE = "http://127.0.0.1:8021/web3/";
const browser = await chromium.launch();
const page = await browser.newPage();
page.setDefaultTimeout(20000);
const pageErrors = [];
const failedReqs = [];
page.on("pageerror", (e) => pageErrors.push(e.message));
page.on("requestfailed", (r) => {
  if (!r.url().includes("favicon")) failedReqs.push(`${r.method()} ${r.url()}`);
});

await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.locator("nav button").first().waitFor({ timeout: 20000 });

const readInd = () =>
  page.locator("aside button", { hasText: /连接|实时/ }).first().textContent().then((t) => t?.trim()).catch(() => "(无)");

console.log("标题:", await page.title());
console.log("导航(真实徽标):", JSON.stringify(await page.locator("nav button").evaluateAll((bs) => bs.map((b) => b.textContent?.trim()).filter(Boolean))));

// 各视图标记
const navTo = async (l) => { await page.locator("nav button", { hasText: l }).first().click(); await page.waitForTimeout(l === "演化图谱" ? 2000 : 900); };
await navTo("对话工作台"); console.log("对话 textarea=" + (await page.locator("textarea").count()));
await navTo("演化图谱"); console.log("图谱 canvas=" + (await page.locator("canvas").count()));
await navTo("节点种群");
const ags = await page.locator(".num").evaluateAll((els) => els.map((e) => e.textContent?.trim()).filter((t) => t && /^ags_/i.test(t)));
console.log("节点 ags_ 渲染数=" + ags.length + " 样例=" + JSON.stringify(ags.slice(0, 4)));
await navTo("系统概览"); console.log("概览 bodyLen=" + (await page.evaluate(() => document.body.innerHTML.length)));

// SSE 稳定性：采样 60s，记录指示器迁移。修复前空闲会 ~50s 变「连接中」；修复后应稳定「实时连接」。
const transitions = [];
let last = await readInd();
transitions.push(`t=0s ${last}`);
for (let s = 5; s <= 60; s += 5) {
  await page.waitForTimeout(5000);
  const t = await readInd();
  if (t !== last) { transitions.push(`t=${s}s ${t}`); last = t; }
}
console.log("SSE 60s 迁移:", JSON.stringify(transitions));

console.log("pageerrors:", JSON.stringify(pageErrors));
console.log("requestfailed:", JSON.stringify(failedReqs));
await browser.close();
console.log(pageErrors.length || failedReqs.length ? "SMOKE: ISSUES" : "SMOKE: CLEAN");
