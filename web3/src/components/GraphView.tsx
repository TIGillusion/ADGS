// ============================================================
// 演化图谱视图 — Canvas 实时图谱 + 工具栏 + 图例 + 悬浮提示
// ============================================================

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Crosshair,
  Maximize2,
  Wand2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { store, useAppState } from "@/lib/store";
import { GraphCanvas, statusText } from "@/lib/graphCanvas";
import { nodeIdLabel, scoreTone } from "@/lib/format";
import { Chip, Empty } from "@/components/ui";
import { displayGraph } from "@/lib/graphFlow";
import { GRAPH_STATUS, activeGraphStatus, graphStatus } from "@/lib/executionState";
import { ceoPhaseLabel } from "@/lib/ceo";

function GraphTooltip({
  pos,
  nodeKey,
}: {
  pos: { x: number; y: number };
  nodeKey: string;
}) {
  const graph = useAppState((s) => displayGraph(s.graph));
  const node = useMemo(() => graph.nodes.find((n) => n.key === nodeKey), [graph, nodeKey]);
  if (!node) return null;
  const isInst = node.type === "node";
  const tone = scoreTone(node.extra.score);
  return (
    <div
      className="surface-glass pointer-events-none absolute z-30 w-[280px] rounded-xl p-3"
      style={{
        left: Math.min(pos.x + 14, (typeof window !== "undefined" ? window.innerWidth : 1200) - 300),
        top: Math.min(pos.y + 10, (typeof window !== "undefined" ? window.innerHeight : 800) - 220),
      }}
    >
      <div className="flex items-center gap-2">
        {node.type === "ceo" && <Chip color="ceo">CEO</Chip>}
        {node.type === "round" && <Chip color="pulse">轮次</Chip>}
        {node.type === "task" && <Chip color="dim">任务</Chip>}
        {isInst && <Chip color={GRAPH_STATUS[graphStatus(node.status)].tone}>{statusText(node.status)}</Chip>}
        {isInst && node.extra.mode === "clone" && <Chip color="gene">临时克隆</Chip>}
      </div>
      <div className="num mt-2 text-[13px] font-semibold text-ink-100">
        {isInst ? nodeIdLabel(node.id) : node.label}
      </div>
      {isInst && node.extra.goal && (
        <div className="clamp-2 mt-1 text-[11.5px] leading-[1.6] text-ink-400">{node.extra.goal}</div>
      )}
      {node.type === "task" && node.extra.goal && (
        <div className="clamp-2 mt-1 text-[11.5px] leading-[1.6] text-ink-400">{node.extra.goal}</div>
      )}
      <div className="mt-2 space-y-1 text-[11px] text-ink-500">
        {node.type === "ceo" && <><div>Hermes · 固定协调者 · {ceoPhaseLabel(node.extra.ceo_activity)}</div><div>制定计划、拆分委派、审阅汇报</div>{node.extra.ceo_activity?.model && <div className="num">{node.extra.ceo_activity.model}</div>}</>}
        {isInst && node.extra.score != null && (
          <div className="flex justify-between">
            <span>评分</span>
            <span className={`num ${tone}`}>
              {node.extra.score.toFixed(1)}
              {node.extra.passed != null && (node.extra.passed ? " · 通过" : " · 未通过")}
              {node.extra.reworks != null && node.extra.reworks > 0 ? ` · 返工${node.extra.reworks}` : ""}
            </span>
          </div>
        )}
        {node.type === "task" && node.extra.count != null && (
          <div className="flex justify-between">
            <span>子任务</span>
            <span className="num">
              {node.extra.seq != null ? `${node.extra.seq} / ` : ""}
              {node.extra.count}
            </span>
          </div>
        )}
        {node.type === "task" && node.extra.overall_goal && (
          <div className="clamp-2 pt-1">目标：{node.extra.overall_goal}</div>
        )}
      </div>
      <div className="mt-2 border-t border-ink-600/20 pt-1.5 text-[10px] text-ink-600">
        点击选中查看详情
      </div>
    </div>
  );
}

function Legend() {
  const sse=useAppState(s=>s.sse);
  return <div className="graph-legend surface-glass absolute bottom-4 left-4 z-20 rounded-xl px-3.5 py-2.5">
    <div className="flex flex-wrap items-center gap-3 text-[10.5px] text-ink-500">
      {[{label:"CEO",color:"--canvas-ceo"},{label:"轮次",color:"--canvas-round"},{label:"批次",color:"--canvas-task"}].map(item=><span key={item.label} className="flex items-center gap-1.5"><span className="dot" style={{background:`var(${item.color})`,width:7,height:7}}/>{item.label}</span>)}
      <details><summary className="cursor-pointer">节点状态颜色</summary><div className="graph-status-legend">{Object.entries(GRAPH_STATUS).map(([status,item])=><span key={status} data-graph-status={status}><i style={{background:`var(${item.color})`}}/>{item.label}</span>)}</div></details>
    </div>
    <div className="mt-1.5 text-[10px] text-ink-500">流点：近期派发 → / 汇报 ← · 3 秒后结束{sse!=="open" ? " · 连接中断，显示最近记录" : ""}</div>
  </div>;
}

function GraphToolbar({
  canvas,
  stats,
}: {
  canvas: GraphCanvas | null;
  stats: { active: number; rounds: number; tasks: number; nodes: number };
}) {
  const graph = useAppState((s) => displayGraph(s.graph));
  const focusRound = useAppState((s) => s.graphFocusRound);
  const roundKeys = useMemo(
    () => graph.nodes.filter((n) => n.type === "round" && n.parent === "ceo").map((n) => n.key),
    [graph],
  );
  return (
    <div className="flex items-center gap-2">
      <div className="mr-2 hidden items-center gap-2 md:flex">
        {stats.active > 0 && (
          <span className="flex items-center gap-1.5 rounded-full border border-life-500/30 bg-life-500/10 px-2.5 py-1 text-[11px] text-life-300">
            <span className="dot dot-pulse" style={{ background: "var(--color-life-500)", width: 6, height: 6 }} />
            {stats.active} 个节点活动
          </span>
        )}
        <span className="num rounded-full border border-ink-600/25 px-2.5 py-1 text-[11px] text-ink-500">
          {stats.rounds} 轮 · {stats.tasks} 批次 · {stats.nodes} 实例
        </span>
      </div>
      {/* 2026-09-04：按轮次聚焦——历史会话跑了很多轮，全图缩到不可读，
          选一轮只框住该轮子树（可读尺度）；"全部" 恢复全图 */}
      {roundKeys.length > 1 && (
        <select
          className="cursor-pointer rounded-lg border border-ink-600/25 bg-abyss-850/80 px-2 py-1 text-[11px] text-ink-300 outline-none focus:border-life-400/40"
          value={focusRound ?? "all"}
          onChange={(e) => {
            const v = e.target.value === "all" ? null : e.target.value;
            store.setGraphFocusRound(v);
            if (v) canvas?.fitSubtree(v);
            else canvas?.fit();
          }}
          title="按轮次聚焦图谱（全图太高时看单轮更清晰）"
          aria-label="按轮次聚焦"
        >
          <option value="all">全部轮次</option>
          {roundKeys.map((k) => (
            <option key={k} value={k}>
              {k.replace("round-", "轮 ")}
            </option>
          ))}
        </select>
      )}
      <button className="btn btn-ghost !px-2.5 !py-1.5" title="放大" onClick={() => canvas?.zoom(1.25)}>
        <ZoomIn size={14} />
      </button>
      <button className="btn btn-ghost !px-2.5 !py-1.5" title="缩小" onClick={() => canvas?.zoom(1 / 1.25)}>
        <ZoomOut size={14} />
      </button>
      <button
        className="btn btn-ghost !px-2.5 !py-1.5"
        title="适应全图（或双击画布）"
        onClick={() => {
          store.setGraphFocusRound(null);
          canvas?.fit();
        }}
      >
        <Maximize2 size={14} />
      </button>
      <button
        className="btn btn-ghost !px-2.5 !py-1.5"
        title="一键整理：原始 → 树状 → 行星 循环"
        onClick={() => {
          const result = store.cycleLayout();
          store.setGraphFocusRound(null);
          if (result === "original") {
            store.toast("ok", "已恢复整理前的布局");
          } else if (result === "tree") {
            store.toast("ok", "已整理布局：树状向下分支");
          } else {
            store.toast("ok", "已整理布局：以 CEO 为中心向四周发散");
          }
          canvas?.fit();
        }}
      >
        <Wand2 size={14} />
      </button>
      <button className="btn btn-ghost !px-2.5 !py-1.5" title="重置视角" onClick={() => canvas?.resetView()}>
        <Crosshair size={14} />
      </button>
    </div>
  );
}

export function GraphView({ embedded }: { embedded?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<GraphCanvas | null>(null);
  const [engine, setEngine] = useState<GraphCanvas | null>(null);
  const [hover, setHover] = useState<{ key: string; x: number; y: number } | null>(null);
  const graph = useAppState((s) => displayGraph(s.graph));
  const viewSession = useAppState((s) => s.viewSession);
  const projects = useAppState((s) => s.projects);
  const conversation = projects.flatMap(project => project.sessions).find(session => session.id === viewSession);
  const focusRound = useAppState((s) => s.graphFocusRound);
  const boot = useAppState((s) => s.boot);
  const stats = useMemo(() => {
    let active = 0, rounds = 0, tasks = 0, nodes = 0;
    for (const n of graph.nodes) {
      if (n.type === "round" && n.parent === "ceo") rounds++;
      else if (n.type === "task") tasks++;
      else if (n.type === "node") {
        nodes++;
        if (activeGraphStatus(n.status)) active++;
      }
    }
    return { active, rounds, tasks, nodes };
  }, [graph]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const eng = new GraphCanvas(canvas, {
      getGraph: () => displayGraph(store.getState().graph),
      getLive: () => { const state=store.getState(); return state.sse === "open" && state.busy && Date.now()-(state.stateReceivedAt || 0)<25000; },
      selected: () => store.getState().selectedInst,
      getFocusRound: () => store.getState().graphFocusRound,
      getLayoutMode: () => store.getState().graphLayoutMode,
      getManual: () => store.getState().graphManual,
      onManualChange: (map) => store.setGraphManual(map),
      handlers: {
        onSelect: (key) => {
          if (key === null) store.selectInst(null);
          else if (key === "ceo") store.selectInst("ceo");
          else store.selectInst(key);
        },
        onHover: (key, x, y) => setHover(key ? { key, x, y } : null),
      },
    });
    engineRef.current = eng;
    eng.start();
    setEngine(eng);
    return () => {
      eng.destroy();
      engineRef.current = null;
    };
  }, []);

  const empty = boot.status === "ok" && graph.nodes.length <= 1;

  return (
    <div className="relative flex h-full min-h-0 flex-1 flex-col">
      {!embedded && (
        <div className="graph-header flex h-[52px] shrink-0 items-center justify-between border-b border-ink-600/15 bg-abyss-900/40 px-5">
          <div className="graph-heading flex items-center gap-3">
            <h2 className="font-display text-[15px] font-semibold text-ink-100">演化图谱</h2>
            <span className="text-[11.5px] text-ink-600">
              {conversation?.title || "任务分解与委派关系"}
              {focusRound ? ` · 已聚焦 ${focusRound.replace("round-", "轮 ")}` : ""}
            </span>
          </div>
          <GraphToolbar canvas={engine} stats={stats} />
        </div>
      )}

      <div ref={wrapRef} className="bg-grid relative min-h-0 flex-1 overflow-hidden">
        <canvas
          ref={canvasRef}
          className="h-full w-full cursor-grab active:cursor-grabbing"
          style={{ touchAction: "none" }}
        />
        {empty && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <Empty
              title="图谱等待第一个任务"
              hint="在「对话工作台」中下达任务后，CEO 的分解、派发与节点执行会在这里实时生长。滚轮缩放 · 拖拽平移 · 双击适应。"
            />
          </div>
        )}
        {hover && <GraphTooltip pos={hover} nodeKey={hover.key} />}
        {!embedded && <div className="graph-help">滚轮缩放 · 拖拽平移 · 点击节点查看详情<br/>轮次记录输入，序号记录每批委派</div>}
        <Legend />
        {embedded && (
          <div className="absolute right-3 top-3 z-20">
            <GraphToolbar canvas={engine} stats={stats} />
          </div>
        )}
      </div>
    </div>
  );
}
