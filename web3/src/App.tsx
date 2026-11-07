import { useEffect, useRef, useState } from "react";
import {
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Share2,
  Sun,
  X,
} from "lucide-react";
import { store, useAppState } from "@/lib/store";
import { ChatView } from "@/components/ChatView";
import { GraphView } from "@/components/GraphView";
import { NodesView } from "@/components/NodesView";
import { SessionsView } from "@/components/SessionsView";
import { OverviewView } from "@/components/OverviewView";
import { NodeDrawer } from "@/components/NodeDrawer";
import { ProjectSidebar, ProjectInspector } from "@/components/ProjectSidebar";

// 主题切换：存 localStorage，切换 <html data-theme>（styles.css 的 :root 变量随之切换）。
// 模块加载时立即应用一次，避免刷新时先闪暗色再跳浅色。
const THEME_KEY = "adgs_theme";
function readInitialTheme(): "dark" | "light" {
  try {
    return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}
if (typeof document !== "undefined") {
  document.documentElement.dataset.theme = readInitialTheme();
}

function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light">(readInitialTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  const light = theme === "light";
  return (
    <button
      className="flex w-full cursor-pointer items-center justify-between rounded-lg px-2 py-1.5 text-[11.5px] text-ink-500 transition-colors hover:bg-ink-600/10 hover:text-ink-200"
      onClick={() => setTheme(light ? "dark" : "light")}
      title={light ? "切换到黑夜模式" : "切换到白天模式"}
    >
      <span>{light ? "白天模式" : "黑夜模式"}</span>
      {light ? <Sun size={14} /> : <Moon size={14} />}
    </button>
  );
}

function SseIndicator() {
  const sse = useAppState((s) => s.sse);
  const map = {
    open: { c: "#3ecf7f", t: "实时连接", pulse: true },
    connecting: { c: "#fbbf24", t: "连接中", pulse: true },
    reconnecting: { c: "#fbbf24", t: "重连中", pulse: true },
    error: { c: "#f4535f", t: "连接断开", pulse: false },
  } as const;
  const m = map[sse];
  return (
    <button
      className="flex cursor-pointer items-center gap-1.5 text-[11px] text-ink-500 hover:text-ink-300"
      title={sse === "error" ? "点击重连" : "SSE 实时事件流"}
      onClick={() => {
        if (sse === "error") store.reattachSse();
      }}
    >
      <span
        className={`dot ${m.pulse ? "dot-pulse" : ""}`}
        style={{ background: m.c, width: 7, height: 7 }}
      />
      {m.t}
    </button>
  );
}

function EvolveBanner() {
  const banner = useAppState((s) => s.evolveBanner);
  const [dismissed, setDismissed] = useState<number>(0);
  if (!banner) return null;
  if (banner.n_after === dismissed) return null;
  const gap = banner.score_gap;
  return (
    <div className="evolve-banner pointer-events-auto absolute left-1/2 top-4 z-40 flex -translate-x-1/2 items-center gap-3 rounded-2xl px-4 py-2.5">
      <Share2 size={15} className="shrink-0 text-gene-400" />
      <div className="text-[12.5px] text-ink-200">
        <span className="font-semibold text-gene-300">种群进化</span>
        <span className="mx-2 text-ink-600">·</span>
        <span className="num text-ink-100">
          {banner.n_before} → {banner.n_after}
        </span>
        <span className="mx-2 text-ink-600">·</span>
        繁衍 {banner.births.length}
        <span className="mx-2 text-ink-600">·</span>
        淘汰 {banner.eliminations.length}
        <span className="mx-2 text-ink-600">·</span>
        压力 {(banner.pressure ?? 0).toFixed(2)}
        {gap != null && (
          <>
            <span className="mx-2 text-ink-600">·</span>
            <span className={gap <= 0 ? "text-ok-400" : "text-warn-400"}>
              淘汰均分差 {gap > 0 ? "+" : ""}
              {gap.toFixed(1)}
            </span>
          </>
        )}
      </div>
      <button
        className="cursor-pointer text-ink-500 hover:text-ink-200"
        onClick={() => setDismissed(banner.n_after ?? 0)}
        aria-label="关闭"
      >
        <X size={13} />
      </button>
    </div>
  );
}

function Toasts() {
  const toasts = useAppState((s) => s.toasts);
  return (
    <div className="pointer-events-none fixed bottom-5 left-1/2 z-[60] flex -translate-x-1/2 flex-col items-center gap-2">
      {toasts.map((t) => {
        const c =
          t.kind === "ok"
            ? "border-ok-500/40 text-ok-400"
            : t.kind === "error"
              ? "border-danger-500/40 text-danger-400"
              : t.kind === "warn"
                ? "border-warn-400/40 text-warn-400"
                : "border-pulse-500/40 text-pulse-300";
        return (
          <div
            key={t.id}
            className={`toast-in surface-glass pointer-events-auto flex max-w-[480px] items-center gap-2 rounded-xl border px-4 py-2.5 text-[12.5px] ${c}`}
          >
            <span className="max-w-[400px] break-all">{t.text}</span>
            <button
              className="cursor-pointer text-ink-500 hover:text-ink-200"
              onClick={() => store.dismissToast(t.id)}
            >
              <X size={12} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

function Main() {
  const view = useAppState((s) => s.view);
  const boot = useAppState((s) => s.boot);
  const selectedInst = useAppState((s) => s.selectedInst);
  const viewRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 全局快捷键：Esc 关闭抽屉
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") store.selectInst(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (boot.status === "loading") {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4">
        <img src="/favicon.svg" alt="" className="h-16 w-16 animate-pulse rounded-2xl" />
        <div className="text-[13px] text-ink-500">正在连接 ADGS 后端…</div>
      </div>
    );
  }

  if (boot.status === "error") {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8">
        <div className="text-[15px] font-semibold text-danger-400">无法连接 ADGS 后端</div>
        <div className="max-w-[420px] text-center text-[12.5px] leading-6 text-ink-500">
          {boot.error}
          <br />
          请确认已运行 <code className="num text-ink-300">python web_app.py</code>（端口 8021），然后重试。
        </div>
        <button
          className="btn btn-primary cursor-pointer"
          onClick={() => location.reload()}
        >
          重新连接
        </button>
      </div>
    );
  }

  return (
    <div ref={viewRef} className="relative flex min-w-0 flex-1 flex-col">
      <EvolveBanner />
      {view === "chat" && <ChatView />}
      {view === "graph" && <GraphView embedded={false} />}
      {view === "nodes" && <NodesView />}
      {view === "sessions" && <SessionsView />}
      {view === "overview" && <OverviewView />}
      {selectedInst && <NodeDrawer />}
    </div>
  );
}

export default function App() {
  const [inspector, setInspector] = useState(() => { try { return localStorage.getItem("adgs_inspector") === "true"; } catch { return false; } });
  const [sidebar, setSidebar] = useState(() => { try { return localStorage.getItem("adgs_sidebar") !== "false" && window.innerWidth > 760; } catch { return true; } });
  useEffect(() => { try { localStorage.setItem("adgs_inspector", String(inspector)); } catch { /* storage unavailable */ } }, [inspector]);
  useEffect(() => { try { localStorage.setItem("adgs_sidebar", String(sidebar)); } catch { /* storage unavailable */ } }, [sidebar]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => { if (e.key === "Escape" && window.innerWidth <= 760) setSidebar(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  const view = useAppState(s=>s.view), selected = useAppState(s=>s.selectedInst);
  return (
    <div className={`project-shell ${sidebar ? "sidebar-open" : "sidebar-closed"} relative flex h-full w-full overflow-hidden`}>
      <button className="sidebar-toggle" aria-label={sidebar ? "收起项目导航" : "展开项目导航"} aria-expanded={sidebar} aria-controls="project-navigation" title={sidebar ? "收起项目导航" : "展开项目导航"} onClick={() => setSidebar(v => !v)}>{sidebar ? <PanelLeftClose size={18}/> : <PanelLeftOpen size={18}/>}</button>
      {sidebar && <button className="sidebar-backdrop" aria-label="关闭项目导航" onClick={() => setSidebar(false)}/>}
      <ProjectSidebar onNavigate={() => { if (window.innerWidth <= 760) setSidebar(false); }} footer={<><ThemeToggle /><SseIndicator /></>} onInspector={()=>setInspector(v=>!v)} />
      <Main />
      {inspector && view === "chat" && !selected && <ProjectInspector onClose={()=>setInspector(false)} />}
      <Toasts />
    </div>
  );
}
