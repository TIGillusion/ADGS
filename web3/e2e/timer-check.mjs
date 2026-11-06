// 临时目视验证脚本：运行中节点「执行中 mm:ss」为真实执行时长
// （2026-09-27 修复前是动画时钟 t*2%60 —— 每 30s 循环归零、刷新归零）
//
// 做法：拦截 /api/state 注入种子事件（ags_1 于 75 秒前转入 running，
// ts 为服务端时间戳），SSE 走真实 mock（无事件）。断言口径：
//   1. t0 显示 ≈ 01:15（真实起点，不是 00:0x）
//   2. +6s 后 ≈ 01:21（随真实时间增长）
//   3. 刷新页面后 ≈ 01:27+（事件重放恢复 runTs，不归零）
// 用法: 先启动 node e2e/mock-server.mjs，再 node e2e/timer-check.mjs
import { chromium } from "@playwright/test";

const base = "http://127.0.0.1:8099";
const bootTs = Math.floor(Date.now() / 1000);
const runTs = bootTs - 75; // ags_1 转入 running 的服务端时间戳
const dispatchTs = runTs - 5;

const seedEvents = [
  { type: "chat.user", ts: dispatchTs - 3, data: { text: "抓取页面A（计时验证）" } },
  {
    type: "graph.task",
    ts: dispatchTs - 1,
    data: { id: "t9_1_timer", name: "页面抓取A", goal: "抓取页面A", overall: "计时验证", parent: "ceo", seq: 1, count: 1 },
  },
  {
    type: "graph.dispatch",
    ts: dispatchTs,
    data: { parent: "ceo", task: "t9_1_timer", node: "ags_1", mode: "direct", goal: "抓取页面A" },
  },
  { type: "node.status", ts: runTs, data: { node: "ags_1", status: "running", task: "t9_1_timer" } },
];

const statePayload = () =>
  JSON.stringify({
    ok: true,
    workspace: "D:\\e2e\\workspace",
    population: { active: 3, capacity: 8 },
    models: { ceo: "m-ceo", brain: "m-brain", worker: "m-worker" },
    nodes: [
      { id: "ags_1", describe: "抓取节点", score: 88.5, success_rate: 0.83, tasks_done: 12, birth_type: "recombine", gen: 2 },
      { id: "ags_2", describe: "写作节点", score: 72, success_rate: 0.67, tasks_done: 9, birth_type: "initial", gen: 1 },
      { id: "ags_3", describe: "分析节点", score: 55.5, success_rate: 0.5, tasks_done: 4, birth_type: "whiteboard", gen: 3 },
    ],
    events: seedEvents,
    busy: true,
    queue_len: 0,
    boot_ts: bootTs,
  });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await page.route("**/api/state", (route) => route.fulfill({ contentType: "application/json", body: statePayload() }));

const shot = async (name) => {
  await page.locator("nav button", { hasText: "演化图谱" }).first().click();
  await page.waitForTimeout(1400);
  await page.screenshot({ path: `test/.timer_${name}.png` });
  console.log(`saved test/.timer_${name}.png`);
};

await page.goto(base + "/");
await page.waitForTimeout(1500);
await shot("t0");

await page.waitForTimeout(6000);
await shot("t6");

await page.reload();
await page.waitForTimeout(1800);
await shot("reload");

await browser.close();
