import { defineConfig } from "@playwright/test";

// ADGS web3 E2E — 由本地 mock 后端（e2e/mock-server.mjs）托管 dist/ 产物，
// 覆盖完整任务生命周期 / 图谱 / 节点 / 会话 / 指令 / 进化场景
export default defineConfig({
  testDir: "./e2e",
  testIgnore: "**/execution-api.spec.ts",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8099",
    headless: true,
    viewport: { width: 1600, height: 900 },
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: "node e2e/mock-server.mjs",
    url: "http://127.0.0.1:8099/api/state",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
