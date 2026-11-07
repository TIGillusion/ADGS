// ============================================================
// 系统概览 — 种群/模型/任务/进化 仪表盘 + 手动进化控制 + 最近事件
// ============================================================

import { useMemo, useState } from "react";
import {
  Activity,
  Cpu,
  Dna,
  FolderTree,
  Settings2,
  Share2,
  Sparkles,
  Zap,
} from "lucide-react";
import { store, useAppState } from "@/lib/store";
import { fmtAgo, fmtTime, nodeIdLabel, pct } from "@/lib/format";
import { Button, Chip, ConfirmModal, Empty, ScoreBar, Spinner } from "@/components/ui";
import { SettingsModal } from "@/components/SettingsModal";
import type { BusEvent } from "@/types";

function StatCard({
  label,
  value,
  sub,
  accent,
  action,
  children,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  accent?: string;
  action?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="surface flex flex-col p-4">
      <div className="section-title flex items-center justify-between gap-2">
        <span>{label}</span>
        {action}
      </div>
      <div
        className="num mt-2 font-display text-[26px] font-semibold leading-none"
        style={{ color: accent ?? "var(--ink-100)" }}
      >
        {value}
      </div>
      {sub && <div className="mt-1.5 text-[11.5px] text-ink-500">{sub}</div>}
      {children}
    </div>
  );
}

function EventLine({ ev }: { ev: BusEvent }) {
  const d = ev.data ?? {};
  let text = "";
  let color = "text-ink-500";
  switch (ev.type) {
    case "system":
      text = String(d.msg ?? "");
      color = "text-ink-400";
      break;
    case "evolve.round":
      text = `进化轮：${d.n_before} → ${d.n_after}（压力 ${d.pressure}）`;
      color = "text-gene-300";
      break;
    case "chat.user":
      text = `用户任务：${String(d.text ?? "").slice(0, 48)}`;
      color = "text-life-300";
      break;
    case "node.eval":
      text = `${nodeIdLabel(String(d.node ?? ""))} 评估 ${d.passed ? "通过" : "未通过"}（${d.score} 分）`;
      color = d.passed ? "text-ok-400" : "text-danger-400";
      break;
    default:
      text = ev.type;
  }
  return (
    <div className="flex items-start gap-2.5 border-b border-ink-600/10 py-2 last:border-0">
      <span className="num mt-0.5 shrink-0 text-[10.5px] text-ink-600">{fmtTime(ev.ts)}</span>
      <span className={`min-w-0 flex-1 break-all text-[12px] leading-[1.6] ${color}`}>{text}</span>
    </div>
  );
}

export function OverviewView() {
  const snap = useAppState((s) => s.snap);
  const busy = useAppState((s) => s.busy);
  const queueLen = useAppState((s) => s.queueLen);
  const sse = useAppState((s) => s.sse);
  const evolveLast = useAppState((s) => s.evolveLast);
  const [confirmEvolve, setConfirmEvolve] = useState(false);
  const [evolving, setEvolving] = useState(false);
  const [modelsOpen, setModelsOpen] = useState(false);

  const stats = useMemo(() => {
    const nodes = snap?.nodes ?? [];
    if (!nodes.length) return null;
    const scores=nodes.map(n=>n.score).filter((value):value is number=>typeof value === "number" && Number.isFinite(value));
    const rates=nodes.map(n=>n.success_rate).filter((value):value is number=>typeof value === "number" && Number.isFinite(value));
    const avgScore=scores.length ? scores.reduce((sum,value)=>sum+value,0)/scores.length : null;
    const avgRate=rates.length ? rates.reduce((sum,value)=>sum+value,0)/rates.length : null;
    const tasks = nodes.reduce((a, n) => a + (n.tasks_done ?? 0), 0);
    const top = [...nodes].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 3);
    return { avgScore, avgRate, tasks, top };
  }, [snap]);

  const recentEvents = useMemo(() => {
    const evs = snap?.events ?? [];
    const keep = new Set(["system", "evolve.round", "chat.user", "node.eval"]);
    return evs.filter((e) => keep.has(e.type)).slice(-14).reverse();
  }, [snap]);

  const runEvolve = async () => {
    setConfirmEvolve(false);
    setEvolving(true);
    try {
      await store.runEvolve();
    } finally {
      setEvolving(false);
    }
  };

  return (
    <div className="overview-view flex h-full min-h-0 flex-1 flex-col">
      <div className="workbench-header flex h-[52px] shrink-0 items-center justify-between border-b border-ink-600/15 bg-abyss-900/40 px-5">
        <div className="flex items-center gap-3">
          <h2 className="font-display text-[15px] font-semibold text-ink-100">系统概览</h2>
          <Chip
            color={sse === "open" ? "ok" : sse === "error" ? "danger" : "warn"}
          >
            <Activity size={11} />
            {sse === "open" ? "事件流正常" : sse === "error" ? "事件流断开" : "连接中"}
          </Chip>
        </div>
        <div className="flex items-center gap-2">
          {busy && (
            <span className="flex items-center gap-2 rounded-full border border-life-500/30 bg-life-500/10 px-3 py-1 text-[11.5px] text-life-300">
              <Spinner size={11} /> 任务执行中
              {queueLen > 0 && <span className="num">（队列 {queueLen}）</span>}
            </span>
          )}
          <Button variant="primary" onClick={() => setConfirmEvolve(true)} disabled={evolving} className="!text-[12px]">
            {evolving ? <Spinner size={13} /> : <Sparkles size={13} />}
            手动进化
          </Button>
        </div>
      </div>

      <div className="bg-grid min-h-0 flex-1 overflow-y-auto p-5">
        <div className="mx-auto max-w-[1080px]">
          {/* 统计卡 */}
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <StatCard
              label="活动种群"
              value={
                <>
                  {snap?.population?.active ?? "—"}
                  <span className="ml-1 text-[13px] text-ink-500">/ {snap?.population?.capacity ?? "—"}</span>
                </>
              }
              sub="承载量随平均适应度动态伸缩"
              accent="var(--color-life-400)"
            >
              {snap?.population && (
                <div className="mt-2.5">
                  <ScoreBar
                    value={Math.min(100, ((snap.population.active / Math.max(1, snap.population.capacity)) * 100) || 0)}
                  />
                </div>
              )}
            </StatCard>
            <StatCard
              label="平均适应度"
              value={stats?.avgScore != null ? stats.avgScore.toFixed(1) : "—"}
              sub={stats ? `平均成功率 ${pct(stats.avgRate)}` : "等待节点数据"}
              accent={
                stats?.avgScore == null ? "var(--ink-100)" : stats.avgScore >= 80 ? "var(--color-ok-500)" : stats.avgScore >= 60 ? "var(--color-life-500)" : stats.avgScore >= 40 ? "var(--color-warn-400)" : "var(--color-danger-500)"
              }
            >
              {stats?.avgScore != null && (
                <div className="mt-2.5">
                  <ScoreBar value={stats.avgScore} />
                </div>
              )}
            </StatCard>
            <StatCard
              label="累计任务"
              value={stats ? String(stats.tasks) : "—"}
              sub="全部节点完成的任务总数"
            />
            <StatCard
              label="模型"
              action={
                <button
                  className="cursor-pointer rounded-md p-1 text-ink-500 transition-colors hover:bg-ink-600/20 hover:text-life-300"
                  title="系统设置（模型 / 运行 / 调优）"
                  aria-label="配置模型"
                  onClick={() => setModelsOpen(true)}
                >
                  <Settings2 size={13} />
                </button>
              }
              value={<span className="text-[15px] leading-[1.5]">{snap?.models?.ceo ?? "—"}</span>}
              sub={
                <span className="num">
                  大脑 {snap?.models?.brain ?? "—"} · 执行 {snap?.models?.worker ?? "—"}
                </span>
              }
            >
              {snap?.runtime && (
                <div className="num mt-2 text-[10.5px] text-ink-600">
                  并行 {snap.runtime.max_workers}
                  {snap?.advanced && ` · 压力 τ ${snap.advanced.tau}`}
                  {snap?.advanced && ` · 探索 ${snap.advanced.explore_beta}`}
                </div>
              )}
            </StatCard>
          </div>

          {/* 第二行 */}
          <div className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-3">
            {/* 优胜节点 */}
            <div className="surface p-4">
              <div className="section-title mb-3 flex items-center gap-1.5">
                <Zap size={12} /> 当前优胜
              </div>
              {stats && stats.top.length > 0 ? (
                <div className="space-y-2">
                  {stats.top.map((n, i) => (
                    <button
                      key={n.id}
                      className="flex w-full cursor-pointer items-center gap-2.5 rounded-xl border border-ink-600/15 bg-abyss-850/40 px-3 py-2.5 text-left transition-colors hover:border-life-500/35"
                      onClick={() => {
                        store.setView("nodes");
                        store.selectInst(n.id);
                      }}
                    >
                      <span className="num flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-life-500/15 text-[11px] font-semibold text-life-300">
                        {i + 1}
                      </span>
                      <span className="num min-w-0 flex-1 truncate text-[12.5px] font-medium text-ink-100">
                        {nodeIdLabel(n.id)}
                      </span>
                      <span className="num text-[12px] font-semibold text-life-400">
                        {(n.score ?? 0).toFixed(1)}
                      </span>
                      <span className="num w-[48px] text-right text-[11px] text-ink-500">
                        {pct(n.success_rate ?? 0)}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="text-[12px] text-ink-600">暂无节点数据</div>
              )}
            </div>

            {/* 最近进化 */}
            <div className="surface p-4">
              <div className="section-title mb-3 flex items-center gap-1.5">
                <Share2 size={12} /> 最近一次进化
              </div>
              {evolveLast ? (
                <div className="space-y-2 text-[12px]">
                  <div className="flex items-center gap-2.5">
                    <span className="num text-[20px] font-semibold text-gene-300">
                      {evolveLast.n_before} → {evolveLast.n_after}
                    </span>
                    <Chip color="gene" className="num">压力 {(evolveLast.pressure ?? 0).toFixed(2)}</Chip>
                    {evolveLast.effective_capacity != null && (
                      <Chip color="dim" className="num">容量 {evolveLast.effective_capacity}</Chip>
                    )}
                  </div>
                  {evolveLast.score_gap != null && (
                    <div className={evolveLast.score_gap <= 0 ? "text-ok-400" : "text-warn-400"}>
                      淘汰均分差 {evolveLast.score_gap > 0 ? "+" : ""}
                      {evolveLast.score_gap.toFixed(1)}（负值 = 淘汰者更弱，进化有效）
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-2 pt-1">
                    <div className="rounded-xl border border-gene-500/20 bg-gene-500/5 p-2.5">
                      <div className="mb-1 text-[10.5px] text-gene-300">
                        繁衍 {evolveLast.births.length}
                      </div>
                      <div className="num space-y-0.5 text-[11px] text-ink-300">
                        {evolveLast.births.slice(0, 4).map((b) => (
                          <div key={b.id} className="truncate">
                            + {nodeIdLabel(b.id)}
                            {b.score != null ? `（${b.score.toFixed(1)}）` : ""}
                          </div>
                        ))}
                        {evolveLast.births.length > 4 && (
                          <div className="text-ink-600">… 共 {evolveLast.births.length}</div>
                        )}
                        {evolveLast.births.length === 0 && <div className="text-ink-600">无</div>}
                      </div>
                    </div>
                    <div className="rounded-xl border border-danger-500/20 bg-danger-500/5 p-2.5">
                      <div className="mb-1 text-[10.5px] text-danger-400">
                        淘汰 {evolveLast.eliminations.length}
                      </div>
                      <div className="num space-y-0.5 text-[11px] text-ink-300">
                        {evolveLast.eliminations.slice(0, 4).map((e) => (
                          <div key={e.id} className="truncate">
                            − {nodeIdLabel(e.id)}
                          </div>
                        ))}
                        {evolveLast.eliminations.length > 4 && (
                          <div className="text-ink-600">… 共 {evolveLast.eliminations.length}</div>
                        )}
                        {evolveLast.eliminations.length === 0 && <div className="text-ink-600">无</div>}
                      </div>
                    </div>
                  </div>
                </div>
              ) : (
                <Empty title="本轮启动后尚无进化" hint="任务推进或点击「手动进化」触发繁衍 + 淘汰" />
              )}
            </div>

            {/* 工作区 */}
            <div className="surface p-4">
              <div className="section-title mb-3 flex items-center gap-1.5">
                <FolderTree size={12} /> 工作区
              </div>
              <div className="num break-all rounded-xl border border-ink-600/15 bg-abyss-950/60 p-3 text-[12px] leading-6 text-ink-300">
                {snap?.workspace ?? "—"}
              </div>
              <div className="mt-3 flex items-center gap-2 text-[11px] text-ink-500">
                <Dna size={12} className="text-gene-400" />
                启动于 {snap ? fmtAgo(snap.boot_ts) : "—"}
                <span className="text-ink-600">·</span>
                <Cpu size={12} className="text-pulse-400" />
                节点执行器 hermes（CLI 并行）
              </div>
            </div>
          </div>

          {/* 最近事件 */}
          <div className="surface mt-3 p-4">
            <div className="section-title mb-2">最近事件</div>
            {recentEvents.length === 0 ? (
              <div className="py-3 text-center text-[12px] text-ink-600">暂无事件（事件流正常时自动滚入）</div>
            ) : (
              recentEvents.map((ev, i) => <EventLine key={`${ev.ts}-${i}`} ev={ev} />)
            )}
          </div>
        </div>
      </div>

      <SettingsModal open={modelsOpen} onClose={() => setModelsOpen(false)} />

      <ConfirmModal
        open={confirmEvolve}
        title="手动触发一轮进化？"
        body={
          <>
            将立即执行一轮「繁衍 + 淘汰」：适应度高的节点交叉重组产生子代，
            低分节点被淘汰归档。过程通常需要数秒到数十秒（涉及技能读取）。
            {busy && (
              <span className="mt-2 block text-warn-400">
                当前有任务在执行 —— 进化会等待任务锁释放，请耐心等待。
              </span>
            )}
          </>
        }
        confirmText="开始进化"
        busy={evolving}
        onConfirm={() => void runEvolve()}
        onClose={() => setConfirmEvolve(false)}
      />
    </div>
  );
}
