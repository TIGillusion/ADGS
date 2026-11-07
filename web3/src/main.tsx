import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "@/App";
import { store } from "@/lib/store";
import "@/styles.css";
import "@/workbench.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// 启动：拉取初始状态 + 建立 SSE
void store.init();

// E2E/目检钩子：浏览器自动化可直接注入合成事件驱动 store（生产无害）
declare global {
  interface Window {
    __adgs_store?: typeof store;
  }
}
window.__adgs_store = store;
