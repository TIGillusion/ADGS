// 临时目视验证脚本：起 mock 后端 → 建图 → 截图（静置 + hover 高亮）
// 用法: node e2e/screenshot.mjs  （需先手动启动 node e2e/mock-server.mjs）
import { chromium } from "@playwright/test";

const base = "http://127.0.0.1:8099";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto(base + "/");
await page.waitForTimeout(1200);

// 发一个任务让 mock 生成完整图谱事件
await page.locator("nav button", { hasText: "对话工作台" }).first().click();
const box = page.locator("textarea");
await box.fill("帮我抓取两个页面");
await box.press("Enter");
await page.waitForTimeout(6000);

// 切到图谱，等布局/动画稳定
await page.locator("nav button", { hasText: "演化图谱" }).first().click();
await page.waitForTimeout(3500);
await page.screenshot({ path: "test/.graph_live.png" });
console.log("saved test/.graph_live.png");

// hover 一个实例节点（布局钩子坐标 + canvas 在视口内的偏移 → 视口坐标）
const canvasBox = await page.locator("canvas").first().boundingBox();
const target = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  const lay = c && c.__adgsLayout;
  const view = c && c.__adgsView;
  if (!lay || !view || !lay.length) return null;
  const inst = lay.find((n) => n.key.includes("@")) ?? lay[lay.length - 1];
  return { sx: inst.x * view.k + view.x, sy: inst.y * view.k + view.y, key: inst.key };
});
if (target && canvasBox) {
  await page.mouse.move(canvasBox.x + target.sx, canvasBox.y + target.sy, { steps: 5 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: "test/.graph_hover.png" });
  console.log("saved test/.graph_hover.png  hovered =", target.key);
} else {
  console.log("no layout hook — canvas 未渲染?");
}
await browser.close();
