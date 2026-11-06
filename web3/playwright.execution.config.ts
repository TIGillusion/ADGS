import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
const python = existsSync("../.venv/Scripts/python.exe") ? resolve("../.venv/Scripts/python.exe") :
  existsSync("../.venv/bin/python") ? resolve("../.venv/bin/python") : "python";
export default defineConfig({
  testDir: "./e2e", testMatch: "execution-api.spec.ts", timeout: 60000,
  use: { baseURL: "http://127.0.0.1:8098", headless: true, viewport: { width: 1600, height: 900 } },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: { command: `"${python}" e2e/execution-api-server.py`, url: "http://127.0.0.1:8098/api/state", reuseExistingServer: false, timeout: 30000 },
});
