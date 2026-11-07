// ============================================================
// 节点种群 — 活动节点列表 + 归档（复活/删除）+ 搜索/筛选
// 数据源：/api/state（快，轮询刷新）；归档列表来自 /api/nodes（慢，懒加载缓存）
// ============================================================

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  Dna,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Users,
} from "lucide-react";
import { api } from "@/lib/api";
import { store, useAppState } from "@/lib/store";
import { nodeIdLabel, pct, scoreTone } from "@/lib/format";
import { Button, Chip, ConfirmModal, Empty, ScoreBar, Spinner } from "@/components/ui";
import type { NodeSummary } from "@/types";

const BIRTH_TEXT: Record<string, string> = {
  initial: "初始",
  recombine: "重组",
  whiteboard: "白板",
};

function toneText(tone: string): string {
  return tone === "ok"
    ? "text-ok-400"
    : tone === "life"
      ? "text-life-400"
      : tone === "warn"
        ? "text-warn-400"
        : "text-danger-400";
}

function NodeRow({ n, archived = false, meshOut = 0, meshIn = 0 }:
                 { n: NodeSummary; archived?: boolean; meshOut?: number; meshIn?: number }) {
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const selected = useAppState((s) => s.selectedInst);
  const active = selected === n.id;
  return (
    <div
      className={`node-row group cursor-pointer rounded-xl border p-3.5 transition-all ${
        active
          ? "border-life-500/40 bg-life-500/5"
          : "border-ink-600/15 bg-abyss-850/40 hover:border-ink-600/35 hover:bg-abyss-850/70"
      } ${archived ? "opacity-70" : ""}`}
      onClick={() => store.selectInst(n.id)}
    >
      <div className="node-row-main flex items-center gap-3">
        <span
          className="dot shrink-0"
          style={{ background: archived ? "var(--ink-600)" : "var(--color-life-500)", width: 8, height: 8 }}
        />
        <button onClick={(event) => {event.stopPropagation();store.selectInst(n.id);}}
          className="node-row-name num min-w-0 flex-1 truncate text-left text-[13px] font-semibold text-ink-100">
          {nodeIdLabel(n.id)}
        </button>
        <Chip color={n.birth_type === "recombine" ? "gene" : n.birth_type === "whiteboard" ? "pulse" : "dim"}>
          <Dna size={10} /> 第{n.gen}代 · {BIRTH_TEXT[n.birth_type] ?? n.birth_type}
        </Chip>
        <div className="node-taskcount num w-[52px] text-right text-[12px] text-ink-400">
          {n.tasks_done} 任务
        </div>
        {(meshOut > 0 || meshIn > 0) && (
          <div
            className="node-mesh num w-[86px] text-right text-[11px] text-gene-300"
            title={`A2A 委派：作为管理者发出 ${meshOut} 次 / 作为下属被委派 ${meshIn} 次`}
          >
            委派 {meshOut}↔{meshIn}
          </div>
        )}
        <div className={`node-score num w-[56px] text-right text-[13px] font-semibold ${toneText(scoreTone(n.score))}`}>
          {(n.score ?? 0).toFixed(1)}
        </div>
        <div className="node-success w-[110px]">
          <div className="mb-1 flex justify-between text-[10px] text-ink-600">
            <span>成功率</span>
            <span className="num">{pct(n.success_rate ?? 0)}</span>
          </div>
          <ScoreBar value={Math.round((n.success_rate ?? 0) * 100)} />
        </div>
        <div className="node-row-actions flex w-[92px] shrink-0 items-center justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          {archived && (
            <Button
              className="!px-2 !py-1 !text-[11px]"
              disabled={busy}
              onClick={async (e) => {
                e.stopPropagation();
                setBusy(true);
                try {
                  await store.nodeAction(n.id, "revive");
                } catch {
                  /* toast */
                } finally {
                  setBusy(false);
                }
              }}
            >
              <RefreshCw size={12} /> 复活
            </Button>
          )}
          <button
            className="cursor-pointer rounded-lg p-1.5 text-ink-600 transition-colors hover:bg-danger-500/10 hover:text-danger-400"
            title={archived ? "永久删除" : "淘汰节点"}
            onClick={(e) => {
              e.stopPropagation();
              setConfirmRemove(true);
            }}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>
      <div className="node-description clamp-1 mt-1.5 pl-5 text-[11.5px] leading-[1.5] text-ink-500">
        {n.describe || "（无生态位描述）"}
      </div>
      <ConfirmModal
        open={confirmRemove}
        title={archived ? `永久删除 ${nodeIdLabel(n.id)}？` : `淘汰 ${nodeIdLabel(n.id)}？`}
        body={
          archived ? (
            <>该节点已归档，删除将连同 hermes profile 一并移除，不可恢复。</>
          ) : (
            <>
              任务执行中无法移除。移除后该节点回到归档池（可在归档区复活）。
            </>
          )
        }
        confirmText="确认"
        danger
        busy={busy}
        onConfirm={async () => {
          setConfirmRemove(false);
          setBusy(true);
          try {
            await store.nodeAction(n.id, "remove");
          } catch {
            /* toast */
          } finally {
            setBusy(false);
          }
        }}
        onClose={() => setConfirmRemove(false)}
      />
    </div>
  );
}

export function NodesView() {
  const snap = useAppState((s) => s.snap);
  const boot = useAppState((s) => s.boot);
  const [q, setQ] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [archived, setArchived] = useState<NodeSummary[] | null>(null);
  const [loadingArchived, setLoadingArchived] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const loadArchived = useCallback(async () => {
    setLoadingArchived(true);
    try {
      const res = await api.nodes();
      setArchived(res.archived ?? []);
    } catch {
      setArchived([]);
    } finally {
      setLoadingArchived(false);
    }
  }, []);

  useEffect(() => {
    if (showArchived && archived == null) void loadArchived();
  }, [showArchived, archived, loadArchived]);

  const a2aEdges = useAppState((s) => s.graph.a2aEdges);
  const meshStats = useMemo(() => {
    const out = new Map<string, number>();
    const inn = new Map<string, number>();
    for (const e of a2aEdges) {
      if (e.fromNodeId) out.set(e.fromNodeId, (out.get(e.fromNodeId) ?? 0) + 1);
      if (e.toNodeId) inn.set(e.toNodeId, (inn.get(e.toNodeId) ?? 0) + 1);
    }
    return { out, inn };
  }, [a2aEdges]);

  const nodes = useMemo(() => {
    const list = snap?.nodes ?? [];
    const ql = q.trim().toLowerCase();
    const filtered = ql
      ? list.filter(
          (n) =>
            n.id.toLowerCase().includes(ql) ||
            (n.describe ?? "").toLowerCase().includes(ql),
        )
      : list;
    return [...filtered].sort(
      (a, b) => (b.score ?? 0) - (a.score ?? 0) || b.tasks_done - a.tasks_done,
    );
  }, [snap, q]);

  const archivedFiltered = useMemo(() => {
    const list = archived ?? [];
    const ql = q.trim().toLowerCase();
    return ql
      ? list.filter(
          (n) =>
            n.id.toLowerCase().includes(ql) ||
            (n.describe ?? "").toLowerCase().includes(ql),
        )
      : list;
  }, [archived, q]);

  return (
    <div className="nodes-view flex h-full min-h-0 flex-1 flex-col">
      {/* 顶栏 */}
      <div className="workbench-header flex h-[52px] shrink-0 items-center justify-between border-b border-ink-600/15 bg-abyss-900/40 px-5">
        <div className="flex items-center gap-3">
          <h2 className="font-display text-[15px] font-semibold text-ink-100">节点种群</h2>
          <Chip color="life" className="num">
            <Users size={11} /> {snap?.population?.active ?? 0} / {snap?.population?.capacity ?? "—"}
          </Chip>
          {snap?.population && (
            <span className="text-[11.5px] text-ink-600">
              承载量随种群表现动态伸缩
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-600" />
            <input
              className="input !w-[220px] !py-2 !pl-8"
              aria-label="搜索节点和生态位"
              placeholder="搜索节点 / 生态位…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
        </div>
      </div>

      {/* 列表 */}
      <div className="bg-grid min-h-0 flex-1 space-y-2 overflow-y-auto p-5">
        {boot.status === "ok" && nodes.length === 0 && !q && (
          <Empty
            icon={<Users size={34} strokeWidth={1.2} />}
            title="暂无活动节点"
            hint="等待进化繁衍出第一批节点（可手动触发进化）。"
          />
        )}
        {nodes.map((n) => (
          <NodeRow key={n.id} n={n}
            meshOut={meshStats.out.get(n.id) ?? 0}
            meshIn={meshStats.inn.get(n.id) ?? 0} />
        ))}

        {/* 归档区 */}
        <div className="pt-3">
          <button
            className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-[12.5px] font-medium text-ink-500 transition-colors hover:bg-ink-600/10 hover:text-ink-300"
            onClick={() => {
              if (!showArchived && archived == null) {
                setShowArchived(true);
              } else {
                setShowArchived(!showArchived);
                setRefreshKey((k) => k + 1);
              }
            }}
          >
            <svg
              width="11"
              height="11"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              style={{ transform: showArchived ? "rotate(90deg)" : "none", transition: "transform .15s" }}
            >
              <path d="m9 18 6-6-6-6" />
            </svg>
            <Archive size={13} />
            已淘汰节点{archived != null ? `（${archived.length}）` : ""}
          </button>
          {showArchived && (
            <div className="mt-2 space-y-2" key={refreshKey}>
              {loadingArchived && (
                <div className="flex items-center gap-2 p-3 text-[12px] text-ink-500">
                  <Spinner size={13} /> 正在读取归档节点（技能并行加载，约 3 秒）…
                </div>
              )}
              {archived != null && archivedFiltered.length === 0 && (
                <div className="rounded-xl border border-dashed border-ink-600/25 p-4 text-center text-[11.5px] text-ink-600">
                  归档池为空
                </div>
              )}
              {archivedFiltered.map((n) => (
                <NodeRow key={n.id} n={n} archived />
              ))}
              {showArchived && (
                <div className="flex justify-end pt-1">
                  <Button
                    className="!text-[11.5px]"
                    onClick={async () => {
                      await loadArchived();
                      setRefreshKey((k) => k + 1);
                    }}
                  >
                    <RefreshCw size={12} /> 刷新
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>

        {boot.status === "ok" && nodes.length === 0 && q && (
          <div className="pt-2">
            <Empty title="没有匹配的节点" hint={`未找到包含「${q}」的节点`} />
          </div>
        )}

        {/* 底部操作提示 */}
        <div className="flex items-center justify-between pt-4 text-[11px] text-ink-600">
          <span>点击节点查看详情 · 编辑生态位 · 移植技能 · 查看实时日志</span>
          <button
            className="link-btn flex items-center gap-1.5 text-[11.5px]"
            onClick={() => void store.runEvolve()}
          >
            <Sparkles size={12} /> 手动触发进化
          </button>
        </div>
      </div>
    </div>
  );
}
