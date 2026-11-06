// 临时目视验证脚本：系统设置（概览卡片 → 三 tab 弹窗 → 保存 → 反馈）
// 用法: 先启动 node e2e/mock-server.mjs，再 node e2e/settings-ui.mjs
import { chromium } from "@playwright/test";

const base = "http://127.0.0.1:8099";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto(base + "/");
await page.waitForTimeout(1200);

// 1) 系统概览：模型卡片含 并行/压力 τ/探索 摘要行
await page.locator("nav button", { hasText: "系统概览" }).first().click();
await page.waitForTimeout(800);
await page.screenshot({ path: "test/.settings_overview.png" });
console.log("saved test/.settings_overview.png");

// 2) 打开系统设置 → 默认模型 tab
await page.locator('button[aria-label="配置模型"]').click();
await page.waitForTimeout(800);
await page.screenshot({ path: "test/.settings_tab_models.png" });
console.log("saved test/.settings_tab_models.png");

// 3) 运行 tab
await page.locator("button", { hasText: "运行" }).first().click();
await page.waitForTimeout(400);
await page.screenshot({ path: "test/.settings_tab_runtime.png" });
console.log("saved test/.settings_tab_runtime.png");

// 4) 调优 tab：改探索系数与专精权重后截图
await page.locator("button", { hasText: "调优" }).first().click();
await page.waitForTimeout(400);
const advSection = page.locator("div.grid", { hasText: "探索系数" });
await advSection.locator("input").nth(3).fill("0.6"); // 探索系数
await page.screenshot({ path: "test/.settings_tab_tuning.png" });
console.log("saved test/.settings_tab_tuning.png");

// 5) 回运行 tab 改并行上限 → 保存
await page.locator("button", { hasText: "运行" }).first().click();
await page.waitForTimeout(300);
await page.locator("div.grid input").first().fill("4");
await page.locator("button", { hasText: "保存并热生效" }).click();
await page.waitForTimeout(1200);
await page.screenshot({ path: "test/.settings_saved.png" });
console.log("saved test/.settings_saved.png");

await browser.close();
