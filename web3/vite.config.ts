import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
import { legacySessionIndex } from "./build/legacySessionIndex";

// ADGS web3 — 构建产物 dist/ 由 Flask(web_app.py) 挂载在 /web3/ 子路径。
// base 用相对 "./"：资源引用相对 HTML 文档解析，
// 使同一 dist 既能挂 /web3/（生产 Flask）也能挂 /（E2E mock / dev）。
export default defineConfig({
  plugins: [react(), tailwindcss(), legacySessionIndex(path.resolve(__dirname,'../data/sessions'))],
  base: "./",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    port: 5199,
    // 开发时代理到真实后端（python web_app.py, 8021）
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8021",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./test/setup.ts"],
    css: false,
    include: ["test/**/*.test.{ts,tsx}"],
  },
});
