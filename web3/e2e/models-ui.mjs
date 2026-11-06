// 临时目视验证脚本：模型配置入口（概览页卡片 → 弹窗 → 保存 → 节点抽屉钉住模型）
// 用法: 先启动 node e2e/mock-server.mjs，再 node e2e/models-ui.mjs
import { chromium } from "@playwright/test";

const base = "http://127.0.0.1:8099";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.goto(base + "/");
await page.waitForTimeout(1200);

// 1) 系统概览：模型卡片 + 配置按钮
await page.locator("nav button", { hasText: "系统概览" }).first().click();
await page.waitForTimeout(800);
await page.screenshot({ path: "test/.models_overview.png" });
console.log("saved test/.models_overview.png");

// 2) 打开模型配置弹窗
await page.locator('button[aria-label="配置模型"]').click();
await page.waitForTimeout(800);
await page.screenshot({ path: "test/.models_modal.png" });
console.log("saved test/.models_modal.png");

// 3) 修改 hermes 执行模型 + CEO 模型 → 保存
const workerInputs = page.locator("section", { hasText: "hermes 执行器" }).locator("input").first();
await workerInputs.fill("qwen4-32b");
const ceoSection = page.locator("section", { hasText: "CEO" }).first();
await ceoSection.locator("input").first().fill("deepseek-v4-flash");
await page.screenshot({ path: "test/.models_modal_filled.png" });
await page.locator("button", { hasText: "保存并热生效" }).click();
await page.waitForTimeout(1200);
await page.screenshot({ path: "test/.models_saved.png" });
console.log("saved test/.models_saved.png");

// 4) 节点种群 → ags_1 抽屉：执行模型（出生时锁定）
await page.locator("nav button", { hasText: "节点种群" }).first().click();
await page.waitForTimeout(800);
await page.locator("text=ags_1").first().click();
await page.waitForTimeout(1000);
await page.screenshot({ path: "test/.models_drawer.png" });
console.log("saved test/.models_drawer.png");

await browser.close();
