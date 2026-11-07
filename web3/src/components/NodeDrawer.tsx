// ============================================================
// 节点详情抽屉 — 选中图谱节点/实例后从右侧滑出
// - 实例：节点档案（生态位/评分/技能）+ 实时日志（hermes / 大脑决策）
// - 任务/轮次：轻量信息卡
// ============================================================

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  BrainCircuit,
  ChevronRight,
  ChevronUp,
  Copy,
  Dna,
  Pencil,
  RefreshCw,
  Terminal,
  Trash2,
  X,
} from "lucide-react";
import { ResizableDrawerFrame } from "@/components/PanelFrames";
import { MarkdownBody } from "@/components/MarkdownBody";
import { displayGraph } from "@/lib/graphFlow";
import type { BusEvent } from "@/types";
import { api } from "@/lib/api";
import { ExecutionMonitor } from "@/components/ExecutionMonitor";
import { ceoPhaseLabel } from "@/lib/ceo";
import type { CeoActivity } from "@/types";
import {
  nodeHistGet,
  nodeHistSet,
  nodeHistSubscribe,
  nodeHistVersion,
  type NodeHist,
} from "@/lib/nodeHistCache";
import { store, useAppState, type NodeLogEntry } from "@/lib/store";
import { fmtAgo, fmtTime, nodeIdLabel, pct, scoreTone } from "@/lib/format";
import { Button, Chip, ConfirmModal, ScoreBar, Spinner } from "@/components/ui";
import { splitInstKey } from "@/lib/graph";
import type { MeshDelegation, NodeDetail, NodeSummary, SkillInfo } from "@/types";

function toneClass(tone: string): string {
  return tone === "ok"
    ? "text-ok-400"
    : tone === "life"
      ? "text-life-400"
      : tone === "warn"
        ? "text-warn-400"
        : "text-danger-400";
}

// ---------------- 实时日志 ----------------

function useNodeLog(key: string): { entry: NodeLogEntry | undefined; version: number } {
  const version = useSyncExternalStore(store.logs.subscribe, () => store.logs.getVersion());
  const entry = store.logs.entry(key);
  return { entry, version };
}

function LogPanel({
  lines,
  accent,
  emptyHint,
}: {
  lines: string[];
  accent: string;
  emptyHint: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [following, setFollowing] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });
  return (
    <div className="relative">
    <div
      ref={ref}
      onScroll={() => {
        const el = ref.current;
        if (!el) return;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        setFollowing(stick.current);
      }}
      className="num h-full overflow-y-auto whitespace-pre-wrap rounded-lg border border-ink-600/15 bg-abyss-950/80 p-3 text-[11.5px] leading-[1.75] text-ink-300"
      style={{ maxHeight: 320, minHeight: 140 }}
    >
      {lines.length === 0 ? (
        <span className="text-ink-600">{emptyHint}</span>
      ) : (
        lines.map((l, i) => (
          <div key={i} style={l.startsWith("[err]") || l.includes("Error") ? { color: accent } : undefined}>
            {l}
          </div>
        ))
      )}
    </div>
    {!following && <button className="absolute bottom-2 right-3 rounded bg-abyss-800 px-2 py-1 text-[11px] text-life-300" onClick={() => { stick.current = true; setFollowing(true); if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }}>回到底部</button>}
    </div>
  );
}

// ---------------- 磁盘历史日志回退（实时缓冲为空时） ----------------
// 缓存放 @/lib/nodeHistCache（模块级 + 节点活动时由 store 失效）

function HistLog({
  lines,
  loading,
  error,
  ts,
  truncated,
  accent,
  emptyHint,
}: {
  lines: string[] | null | undefined;
  loading: boolean;
  error: string;
  ts: number;
  truncated: boolean;
  accent: string;
  emptyHint: string;
}) {
  if (loading) {
    return (
      <div className="num flex h-[140px] items-center justify-center gap-2 rounded-lg border border-ink-600/15 bg-abyss-950/80 text-[12px] text-ink-500">
        <Spinner size={13} /> 加载磁盘历史日志…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-lg border border-danger-500/25 bg-danger-500/5 p-3 text-[12px] leading-5 text-danger-400">
        历史日志加载失败：{error}
      </div>
    );
  }
  const ls = lines ?? [];
  return (
    <div>
      {ls.length > 0 && (
        <div className="mb-1 text-[10.5px] text-ink-600">
          历史日志 · 磁盘尾部{truncated ? "（截断）" : ""}
          {ts ? ` · 更新于 ${fmtTime(ts)}` : ""}
        </div>
      )}
      <LogPanel lines={ls} accent={accent} emptyHint={emptyHint} />
    </div>
  );
}

// ---------------- 实例详情 ----------------

function InstanceDetail({ nodeId, instKey, hideLogs = false }: { nodeId: string; instKey: string; hideLogs?: boolean }) {
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"hermes" | "brain">("hermes");
  const [editDesc, setEditDesc] = useState(false);
  const [descDraft, setDescDraft] = useState("");
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [sourceNode, setSourceNode] = useState("");
  const [sourceSkills, setSourceSkills] = useState<SkillInfo[]>([]);
  const [sourceSkill, setSourceSkill] = useState("");
  const [allNodes, setAllNodes] = useState<NodeSummary[]>([]);
  // 技能区默认折叠：技能多时整块展开会占掉大段高度（用户反馈"占太多"），
  // 折叠态只显示一行技能芯片，点「技能库」标题行展开完整列表/移植面板
  const [skillsOpen, setSkillsOpen] = useState(false);

  const selected = useAppState((s) => s.selectedInst);
  const { entry, version } = useNodeLog(instKey);
  const [entryFallback, setEntryFallback] = useState<NodeLogEntry | undefined>(undefined);
  // 日志缓冲可能按 nodeId 存储（事件未带 task 时）
  useEffect(() => {
    if (!entry) setEntryFallback(store.logs.entry(nodeId));
  }, [entry, nodeId, version]);

  const log = entry ?? entryFallback;

  // ---- 磁盘历史日志回退（2026-09-03 修复：刷新/查看历史任务后实时缓冲为空，
  // 抽屉两个标签全空）——实时日志无内容时拉取磁盘持久化日志尾部。
  // hermes = workspace/.adgs_logs/{id}_live.log；agentlog =
  // .hermes/profiles/{id}/logs/agent.log。实时行到达后自动切回实时展示。
  // 模块级缓存：抽屉组件跨选中保留 state（同位置同类型），缓存避免重复请求、
  // 同一节点重开抽屉时不闪空。
  const [hist, setHist] = useState<NodeHist | null>(null);
  const [histLoading, setHistLoading] = useState(false);
  const [histError, setHistError] = useState("");
  // 缓存失效版本号：抽屉开着时该节点有新活动（新任务派发/状态流转），
  // store 失效缓存 → 版本号变 → 重新拉取磁盘日志（含"无日志"哨兵过期）
  const histVer = useSyncExternalStore(nodeHistSubscribe, nodeHistVersion);
  // ---- A2A 协作档案：该节点作为委派方/被委派方的全部 mesh 记录 ----
  const [collab, setCollab] = useState<MeshDelegation[] | null>(null);
  const [collabOpen, setCollabOpen] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setCollab(null);
    api
      .meshDelegations(nodeId)
      .then((d) => { if (!cancelled) setCollab(d.delegations ?? []); })
      .catch(() => { if (!cancelled) setCollab([]); });
    return () => { cancelled = true; };
  }, [nodeId, histVer]);

  const liveEmpty =
    (log?.hermes?.length ?? 0) === 0 && (log?.agentlog?.length ?? 0) === 0;

  useEffect(() => {
    let cancelled = false;
    setHistError("");
    setHist(null);
    if (hideLogs) return;
    const cached = nodeHistGet(nodeId);
    if (cached !== undefined) {
      setHist(cached);
      setHistLoading(false);
      return;
    }
    if (!liveEmpty) return; // 有实时日志：不需要磁盘回退
    setHistLoading(true);
    api
      .nodeLogs(nodeId)
      .then((d) => {
        if (cancelled) return;
        if (!d.ok) throw new Error("获取磁盘日志失败");
        const shape: NodeHist = {
          hermes: d.hermes?.lines ?? null,
          agentlog: d.agentlog?.lines ?? null,
          ts: d.hermes?.mtime ?? d.agentlog?.mtime ?? 0,
          truncated: (d.hermes?.truncated ?? false) || (d.agentlog?.truncated ?? false),
        };
        nodeHistSet(nodeId, shape.hermes || shape.agentlog ? shape : null);
        if (shape.hermes || shape.agentlog) setHist(shape);
      })
      .catch((e) => { if (!cancelled) setHistError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setHistLoading(false); });
    return () => { cancelled = true; };
  }, [nodeId, liveEmpty, histVer]);

  const load = () => {
    setLoading(true);
    setError("");
    api
      .nodeDetail(nodeId)
      .then((d) => {
        if (!d.ok || !d.node) throw new Error(d.error ?? "节点不存在");
        setDetail(d.node);
        setSkills(d.skills ?? []);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  };

  useEffect(load, [nodeId]);

  const openInstall = async () => {
    setSkillsOpen(true);
    setInstalling(true);
    try {
      const ns = await api.nodes();
      setAllNodes(ns.nodes.filter((n) => n.id !== nodeId));
      setSourceNode("");
      setSourceSkill("");
      setSourceSkills([]);
    } finally {
      setInstalling(false);
    }
  };

  const pickSource = async (id: string) => {
    setSourceNode(id);
    setSourceSkill("");
    setSourceSkills([]);
    if (!id) return;
    try {
      const d = await api.nodeDetail(id);
      setSourceSkills(d.skills ?? []);
    } catch {
      setSourceSkills([]);
    }
  };

  if (selected !== instKey) return null;

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-5">
      {/* 头部 */}
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="num font-display text-[16px] font-bold text-life-400">
              {nodeIdLabel(nodeId)}
            </span>
            {entry?.phase && <Chip color="pulse">{entry.phase}</Chip>}
            {log && log.lastHeartbeat > 0 && (
              <span className="text-[10.5px] text-ink-600">
                心跳 {fmtAgo(log.lastHeartbeat / 1000)}
              </span>
            )}
          </div>
          {detail && (
            <div className="clamp-2 mt-1 text-[12px] leading-5 text-ink-400">
              {detail.describe || "（无生态位描述）"}
            </div>
          )}
        </div>
        <button
          className="cursor-pointer rounded-lg p-1.5 text-ink-500 transition-colors hover:bg-ink-600/10 hover:text-ink-200"
          onClick={() => store.selectInst(null)}
          aria-label="关闭"
        >
          <X size={16} />
        </button>
      </div>

      {loading && !detail && (
        <div className="flex items-center gap-2 text-[12px] text-ink-500">
          <Spinner size={13} /> 加载节点档案…
        </div>
      )}
      {error && <div className="text-[12px] text-danger-400">加载失败：{error}</div>}

      {detail && (
        <>
          {/* 档案 */}
          <div className="grid grid-cols-2 gap-2.5">
            <div className="rounded-xl border border-ink-600/15 bg-abyss-850/60 p-3">
              <div className="section-title mb-1.5">综合评分</div>
              <div className={`num text-[20px] font-semibold ${toneClass(scoreTone(detail.score ?? 0))}`}>
                {(detail.score ?? 0).toFixed(1)}
              </div>
              <div className="mt-2">
                <ScoreBar value={detail.score ?? 0} />
              </div>
            </div>
            <div className="rounded-xl border border-ink-600/15 bg-abyss-850/60 p-3">
              <div className="section-title mb-1.5">成功率</div>
              <div className="num text-[20px] font-semibold text-life-400">
                {pct(detail.success_rate ?? 0)}
              </div>
              <div className="mt-2">
                <ScoreBar value={Math.round((detail.success_rate ?? 0) * 100)} />
              </div>
            </div>
            <div className="rounded-xl border border-ink-600/15 bg-abyss-850/60 p-3">
              <div className="section-title mb-1.5">完成任务</div>
              <div className="num text-[20px] font-semibold text-ink-100">{detail.tasks_done ?? 0}</div>
            </div>
            <div className="rounded-xl border border-ink-600/15 bg-abyss-850/60 p-3">
              <div className="section-title mb-1.5">基因谱系</div>
              <div className="flex items-center gap-1.5 text-[12px] text-ink-300">
                <Dna size={13} className="text-gene-400" />
                第 {detail.gen} 代 · {detail.birth_type}
              </div>
              {detail.parents && detail.parents.length > 0 && (
                <div className="num mt-1 text-[10.5px] text-ink-500">
                  亲本：{detail.parents.map(nodeIdLabel).join(" + ")}
                </div>
              )}
            </div>
          </div>

          {/* 该节点 hermes 钉住的执行模型（出生时快照；2026-09-27 模型配置入口后
              改全局 worker 模型只影响之后新建的节点，这里可视化验证"旧保持"） */}
          {detail.model && (
            <div className="flex items-center gap-1.5 text-[11.5px] text-ink-500">
              <Terminal size={12} className="text-pulse-400" />
              <span>执行模型</span>
              <span className="num text-ink-300">{detail.model}</span>
              <span className="text-ink-600">（出生时锁定，模型配置不影响存量节点）</span>
            </div>
          )}

          {/* 生态位描述编辑 */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <div className="section-title">生态位描述</div>
              <button
                className="link-btn flex items-center gap-1 text-[11.5px]"
                onClick={() => {
                  setDescDraft(detail.describe ?? "");
                  setEditDesc(!editDesc);
                }}
              >
                <Pencil size={11} /> {editDesc ? "取消" : "编辑"}
              </button>
            </div>
            {editDesc ? (
              <div className="flex gap-2">
                <input
                  className="input flex-1"
                  value={descDraft}
                  onChange={(e) => setDescDraft(e.target.value)}
                  placeholder="这个节点的专长领域…"
                  onKeyDown={async (e) => {
                    if (e.key === "Enter") {
                      try {
                        await store.nodeAction(nodeId, "describe", descDraft);
                        setEditDesc(false);
                        load();
                      } catch {
                        /* toast 已提示 */
                      }
                    }
                  }}
                />
                <Button
                  variant="primary"
                  onClick={async () => {
                    try {
                      await store.nodeAction(nodeId, "describe", descDraft);
                      setEditDesc(false);
                      load();
                    } catch {
                      /* toast 已提示 */
                    }
                  }}
                >
                  保存
                </Button>
              </div>
            ) : (
              <div className="rounded-xl border border-ink-600/15 bg-abyss-850/40 p-3 text-[12.5px] leading-6 text-ink-200">
                {detail.describe || "（暂无描述 —— 点击编辑为它定义专长）"}
              </div>
            )}
          </div>

          {/* 技能 */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <button
                className="flex cursor-pointer items-center gap-1 text-ink-400 transition-colors hover:text-ink-200"
                onClick={() => setSkillsOpen((o) => !o)}
                aria-expanded={skillsOpen}
                title={skillsOpen ? "折叠技能列表" : "展开技能列表"}
              >
                {skillsOpen ? (
                  <ChevronUp size={13} className="text-ink-500" />
                ) : (
                  <ChevronRight size={13} className="text-ink-500" />
                )}
                <span className="section-title">技能库（{skills.length}）</span>
              </button>
              <button className="link-btn flex items-center gap-1 text-[11.5px]" onClick={() => void openInstall()}>
                <Copy size={11} /> 从其他节点移植
              </button>
            </div>
            {skillsOpen ? (
              skills.length > 0 ? (
                <div className="space-y-1.5">
                  {skills.map((s) => (
                    <div
                      key={s.name}
                      className="group flex items-center gap-2.5 rounded-xl border border-ink-600/15 bg-abyss-850/40 px-3 py-2"
                    >
                      <Terminal size={13} className="shrink-0 text-gene-400" />
                      <div className="min-w-0 flex-1">
                        <div className="num text-[12px] font-medium text-ink-100">{s.name}</div>
                        {s.desc && (
                          <div className="clamp-2 text-[11px] leading-[1.5] text-ink-500">{s.desc}</div>
                        )}
                      </div>
                      <button
                        className="cursor-pointer rounded-lg p-1 text-ink-600 opacity-0 transition-all hover:bg-danger-500/10 hover:text-danger-400 group-hover:opacity-100"
                        title="移除该技能"
                        onClick={async () => {
                          try {
                            await store.nodeAction(nodeId, "skill-remove", s.name);
                            load();
                          } catch {
                            /* toast 已提示 */
                          }
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-ink-600/25 p-3 text-center text-[11.5px] text-ink-600">
                  该节点暂无固化技能
                </div>
              )
            ) : skills.length > 0 ? (
              /* 折叠态：一行技能芯片（悬停看描述），把高度让给日志区 */
              <div className="flex flex-wrap gap-1.5">
                {skills.map((s) => (
                  <span key={s.name} title={s.desc || s.name}>
                    <Chip color="dim">{s.name}</Chip>
                  </span>
                ))}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-ink-600/25 p-3 text-center text-[11.5px] text-ink-600">
                该节点暂无固化技能
              </div>
            )}

            {/* 移植面板（仅展开态可见；openInstall 会先展开） */}
            {skillsOpen && installing && (
              <div className="surface-glass pop-in mt-2 space-y-2 p-3">
                <div className="text-[11.5px] text-ink-400">选择源节点（取其技能移植给 {nodeIdLabel(nodeId)}）</div>
                <select
                  className="input cursor-pointer"
                  value={sourceNode}
                  onChange={(e) => void pickSource(e.target.value)}
                >
                  <option value="">— 选择节点 —</option>
                  {allNodes.map((n) => (
                    <option key={n.id} value={n.id}>
                      {nodeIdLabel(n.id)}（{n.describe?.slice(0, 24) || "无描述"}）
                    </option>
                  ))}
                </select>
                {sourceNode && (
                  <select
                    className="input cursor-pointer"
                    value={sourceSkill}
                    onChange={(e) => setSourceSkill(e.target.value)}
                  >
                    <option value="">
                      {sourceSkills.length === 0 ? "该节点暂无技能" : "— 选择技能 —"}
                    </option>
                    {sourceSkills.map((s) => (
                      <option key={s.name} value={s.name}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                )}
                <div className="flex justify-end gap-2">
                  <Button onClick={() => setInstalling(false)}>取消</Button>
                  <Button
                    variant="primary"
                    disabled={!sourceNode || !sourceSkill}
                    onClick={async () => {
                      try {
                        await store.nodeAction(nodeId, "skill-install", `${sourceNode}::${sourceSkill}`);
                        setInstalling(false);
                        load();
                      } catch {
                        /* toast 已提示 */
                      }
                    }}
                  >
                    移植
                  </Button>
                </div>
              </div>
            )}
          </div>

          {/* A2A 协作档案 */}
          <div>
            <button
              className="flex cursor-pointer items-center gap-1 text-ink-400 transition-colors hover:text-ink-200"
              onClick={() => setCollabOpen((o) => !o)}
              aria-expanded={collabOpen}
            >
              {collabOpen ? (
                <ChevronUp size={13} className="text-ink-500" />
              ) : (
                <ChevronRight size={13} className="text-ink-500" />
              )}
              <span className="section-title">
                协作档案（{collab ? collab.length : "…"}）
              </span>
            </button>
            {collabOpen && (
              collab == null ? (
                <div className="rounded-xl border border-ink-600/15 bg-abyss-850/40 p-3 text-[12px] text-ink-500">
                  加载中…
                </div>
              ) : collab.length === 0 ? (
                <div className="rounded-xl border border-dashed border-ink-600/25 p-3 text-center text-[11.5px] text-ink-600">
                  暂无 A2A 协作记录
                </div>
              ) : (
                <div className="space-y-2">
                  {collab.map((d) => {
                    const asParent = d.parent_node_id === nodeId;
                    const other = asParent ? d.child_node_id : d.parent_node_id;
                    const role = asParent ? "委派给" : "受命于";
                    const v = d.verdict;
                    const esc = d.escalations ?? [];
                    return (
                      <div key={d.delegation_id} className="rounded-xl border border-ink-600/15 bg-abyss-850/40 p-2.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="num text-[11px] text-ink-400">
                            {role} {other ? nodeIdLabel(other) : "?"}
                          </span>
                          <Chip color={d.status === "passed" ? "ok" : d.status === "running" ? "life" : "dim"}>
                            {d.status === "passed" ? "通过" : d.status === "running" ? "执行中" : d.status}
                          </Chip>
                          {v && (
                            <Chip color={v.passed ? "ok" : "danger"} className="num">
                              上级 {v.passed ? "通过" : "驳回"}
                              {v.score != null ? ` ${v.score >= 0 ? "+" : ""}${Number(v.score).toFixed(1)}` : ""}
                            </Chip>
                          )}
                          {(d.shared_resources?.length ?? 0) > 0 && (
                            <Chip color="dim" className="num">{d.shared_resources!.join(" / ")}</Chip>
                          )}
                        </div>
                        {d.summary && (
                          <div className="clamp-2 mt-1 text-[11.5px] leading-5 text-ink-300">{d.summary}</div>
                        )}
                        {v?.note && (
                          <div className="clamp-2 mt-1 text-[11px] leading-5 text-ink-500">复核：{v.note}</div>
                        )}
                        {esc.map((e, i) => (
                          <div key={i} className="mt-1 text-[11px] leading-5 text-warn-400">
                            ⚠ 越级纠错（{nodeIdLabel(e.by)}）：{e.passed ? "通过" : "驳回"} {e.score >= 0 ? "+" : ""}{e.score.toFixed(1)} —— {e.note}
                          </div>
                        ))}
                      </div>
                    );
                  })}
                </div>
              )
            )}
          </div>

          {/* 实时 / 历史日志（实时缓冲为空 → 磁盘持久化尾部回退） */}
          {!hideLogs && <div>
            <div className="mb-1.5 flex items-center gap-1">
              <button
                className={`flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11.5px] font-medium transition-colors ${
                  tab === "hermes" ? "bg-life-500/15 text-life-300" : "text-ink-500 hover:text-ink-300"
                }`}
                onClick={() => setTab("hermes")}
              >
                <Terminal size={12} /> Hermes 执行日志
              </button>
              <button
                className={`flex cursor-pointer items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11.5px] font-medium transition-colors ${
                  tab === "brain" ? "bg-gene-500/15 text-gene-300" : "text-ink-500 hover:text-ink-300"
                }`}
                onClick={() => setTab("brain")}
              >
                <BrainCircuit size={12} /> 大脑决策
              </button>
            </div>
            {tab === "hermes" ? (
              (log?.hermes?.length ?? 0) > 0 ? (
                <LogPanel
                  lines={log!.hermes}
                  accent="#f4535f"
                  emptyHint="等待 hermes 输出…（执行开始后实时滚入）"
                />
              ) : (
                <HistLog
                  lines={hist?.hermes}
                  loading={histLoading}
                  error={histError}
                  ts={hist?.ts ?? 0}
                  truncated={hist?.truncated ?? false}
                  accent="#f4535f"
                  emptyHint="磁盘无该节点 hermes 执行日志（从未执行或日志已清理）"
                />
              )
            ) : (
              (log?.agentlog?.length ?? 0) > 0 ? (
                <LogPanel
                  lines={log!.agentlog}
                  accent="#b79cff"
                  emptyHint="等待大脑决策日志…"
                />
              ) : (
                <HistLog
                  lines={hist?.agentlog}
                  loading={histLoading}
                  error={histError}
                  ts={hist?.ts ?? 0}
                  truncated={hist?.truncated ?? false}
                  accent="#b79cff"
                  emptyHint="磁盘无该节点大脑决策日志（node.brain 事件不落盘；历史任务展示 hermes 智能体日志尾部）"
                />
              )
            )}
          </div>}

          {/* 危险操作 */}
          <div className="mt-auto flex items-center justify-between border-t border-ink-600/15 pt-3">
            <span className="num text-[10.5px] text-ink-600">
              创建 {detail.created_at ? fmtTime(detail.created_at) : "—"}
              {detail.updated_at ? ` · 更新 ${fmtAgo(detail.updated_at)}` : ""}
            </span>
            <div className="flex gap-2">
              {detail.archived_at && (
                <Button
                  onClick={async () => {
                    try {
                      await store.nodeAction(nodeId, "revive");
                      load();
                    } catch {
                      /* toast */
                    }
                  }}
                >
                  <RefreshCw size={13} /> 复活
                </Button>
              )}
              <Button variant="danger" onClick={() => setConfirmRemove(true)}>
                <Trash2 size={13} /> {detail.archived_at ? "永久删除" : "淘汰节点"}
              </Button>
            </div>
          </div>
        </>
      )}

      <ConfirmModal
        open={confirmRemove}
        title={detail?.archived_at ? "永久删除该节点？" : "淘汰该节点？"}
        body={
          detail?.archived_at ? (
            <>
              <span className="num font-medium text-ink-100">{nodeIdLabel(nodeId)}</span> 已处于归档状态，
              删除后不可恢复（其技能与其他节点的谱系引用不受影响）。
            </>
          ) : (
            <>
              将从活动池移除 <span className="num font-medium text-ink-100">{nodeIdLabel(nodeId)}</span>
              并归档。之后可在「节点种群」中复活或删除。
            </>
          )
        }
        confirmText="确认操作"
        danger
        onConfirm={async () => {
          setConfirmRemove(false);
          try {
            await store.nodeAction(nodeId, "remove");
            store.selectInst(null);
          } catch {
            /* toast */
          }
        }}
        onClose={() => setConfirmRemove(false)}
      />
    </div>
  );
}

// ---------------- 任务/轮次轻量卡 ----------------

function operationTitle(event: BusEvent): string {
  const d = event.data ?? {};
  if (event.type === "chat.ceo_tool") return `CEO 操作 · ${String(d.name ?? "工具")}`;
  const titles: Record<string, string> = {
    "chat.user": "收到用户输入", "chat.ceo_ask": "CEO 向用户提问", "chat.ceo_report": "CEO 进度汇报",
    "chat.ceo_finish": "CEO 最终汇报", "chat.ceo_end": "CEO 结束本轮执行",
    "graph.task": "准备委派任务", "graph.dispatch": "派发工作节点", "graph.instruction": "向执行实例发送指令",
    "a2a.delegate": d.status === "running" ? "向下级委派" : "下级执行汇报",
    "a2a.verdict": "上级复核", "a2a.escalate": "越级复核", "node.rework": "验收要求返工",
    "node.brain": "工作节点决策", "node.eval": "执行验收", "node.status": "执行状态变化",
  };
  return titles[event.type] ?? event.type;
}
function TaskDetail({ nodeKey }: { nodeKey: string }) {
  const graph = useAppState(s => displayGraph(s.graph));
  const node = graph.nodes.find(n => n.key === nodeKey);
  if (!node) return null;
  const children = graph.nodes.filter(n => n.parent === nodeKey || graph.links?.some(l => l.from === nodeKey && l.to === n.key));
  const operations = node.extra.operations ?? [];
  const input = node.extra.instruction ?? node.extra.user_text ?? node.extra.goal;
  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2"><Chip color={node.type === "round" ? "pulse" : "dim"}>{node.type === "round" ? "输入轮次" : "委派批次"}</Chip>
          <span className="font-display text-[15px] font-semibold text-ink-100">{node.type === "round" ? node.label : `序号 ${node.label}`}</span></div>
        <button aria-label="关闭" className="text-ink-500 hover:text-ink-200" onClick={() => store.selectInst(null)}><X size={16} /></button>
      </div>
      {node.type === "round" && <section>
        <div className="section-title mb-2">{node.parent === "ceo" ? "用户完整输入" : "上级完整指令"}</div>
        <div className="rounded-lg border border-ink-600/20 bg-abyss-950/50 p-3"><MarkdownBody text={input || "历史事件未保存输入原文，无法还原。"}/></div>
        {node.extra.source && <div className="mt-1 text-[11px] text-ink-500">来源：{node.extra.source}</div>}
      </section>}
      {node.type === "task" && <section>
        <div className="section-title mb-2">本批委派任务（{node.extra.contracts?.length ?? children.length}）</div>
        {(node.extra.contracts ?? []).map(c => <details key={c.id} className="mb-2 rounded-lg border border-ink-600/20 bg-abyss-950/50 p-3">
          <summary className="cursor-pointer text-[12px] text-life-300">{c.nodeId || "待路由节点"} · {c.executionId ? c.executionId.slice(0, 12) : c.taskId || c.id}</summary>
          {c.historical && <div className="mt-2 text-[11px] text-warn-400">历史记录可能只有任务摘要，完整原文未保存。</div>}
          <MarkdownBody className="mt-3" text={c.input || "历史事件未保存完整任务书。"}/>
          {c.instanceKey && <button className="link-btn mt-2 text-[11px]" onClick={() => store.selectInst(c.instanceKey!)}>打开执行实例</button>}
        </details>)}
        {!node.extra.contracts?.length && node.extra.goal && <MarkdownBody text={node.extra.goal}/>}
      </section>}
      <section>
        <div className="section-title mb-2">{node.type === "round" ? "本轮操作" : "派发、汇报与复核"}（{operations.length}）</div>
        {!operations.length && <div className="text-[12px] text-ink-500">暂无已保存的操作记录。</div>}
        {operations.map((e, i) => <details key={e.id ?? `${e.ts}-${i}`} className="mb-2 rounded-lg border border-ink-600/20 p-3">
          <summary className="cursor-pointer text-[12px] text-ink-200"><span className="num mr-2 text-ink-500">{fmtTime(e.ts)}</span>{operationTitle(e)}</summary>
          <pre className="mt-3 whitespace-pre-wrap break-words text-[11px] leading-5 text-ink-300">{JSON.stringify(e.data, null, 2)}</pre>
        </details>)}
      </section>
      <section>
        <div className="section-title mb-2">关联节点（{children.length}）</div>
        {children.map(c => <button key={c.key} className="mb-1 flex w-full items-center justify-between rounded-lg border border-ink-600/20 px-3 py-2 text-left text-[12px] text-ink-200" onClick={() => store.selectInst(c.key)}>
          <span>{c.type === "node" ? nodeIdLabel(c.id) : c.type === "task" ? `序号 ${c.label}` : c.label}</span><ChevronRight size={13} />
        </button>)}
      </section>
    </div>
  );
}

// ---------------- 抽屉主体 ----------------

function CeoProfile({ activity }: { activity?: CeoActivity }) {
  return <div className="p-5 space-y-4" data-testid="ceo-profile"><div className="flex items-center justify-between"><h3 className="font-semibold text-ceo-300">固定 CEO · Hermes</h3><button aria-label="关闭 CEO 档案" onClick={() => store.selectInst(null)}><X size={16} /></button></div>
    <div className="text-sm text-ink-300">{ceoPhaseLabel(activity)}{activity?.model && <span className="num ml-3 text-ink-500">{activity.model}</span>}</div>
    <p className="text-sm leading-7 text-ink-400">CEO 负责了解目标、制定计划、拆分委派、审阅汇报与安排返工。可以少量只读查看项目；代码、计算、文件修改、运行验证和报告落盘由 Worker 执行。</p>
    <div className="text-xs text-ink-500">专用技能：adgs-ceo · 模型可在系统设置中调整。</div>
    {activity?.hermes_session_id && <div className="num break-all text-xs text-ink-500">Hermes 会话：{activity.hermes_session_id}</div>}
  </div>;
}

export function NodeDrawer() {
  const selectedInst = useAppState((s) => s.selectedInst);
  const ceoExecution = useAppState((s) => s.snap?.ceo_execution_id);
  const ceo = useAppState((s) => s.snap?.ceo);
  const graph = useAppState((s) => displayGraph(s.graph));
  if (!selectedInst) return null;
  const inGraph = graph.nodes.find((n) => n.key === selectedInst);
  // 实例键（node@task）或不在图谱中的裸节点 id → 节点档案视图
  const isInstance = inGraph == null || inGraph.type === "node";
  const { nodeId } = splitInstKey(selectedInst);
  const ceoActivity = ceo ?? inGraph?.extra.ceo_activity;
  const executionId = selectedInst === "ceo" ? ceo?.execution_id || ceoExecution || inGraph?.extra.execution_id : inGraph?.extra.execution_id;
  return (
    <ResizableDrawerFrame key={executionId ? "execution" : "node"} defaultWidth={executionId ? 860 : 440}>
      {executionId ? <ExecutionMonitor key={executionId} executionId={executionId} nodeId={nodeId} coordinator={selectedInst === "ceo" ? ceoActivity || {engine:"hermes",role:"coordinator",phase:"idle"} : undefined} profile={selectedInst === "ceo" ? <CeoProfile activity={ceoActivity} /> : <InstanceDetail key={selectedInst} nodeId={nodeId} instKey={selectedInst} hideLogs />} /> : selectedInst === "ceo" ? <CeoProfile activity={ceoActivity} /> : isInstance ? (
        <InstanceDetail key={selectedInst} nodeId={nodeId} instKey={selectedInst} />
      ) : (
        <TaskDetail key={selectedInst} nodeKey={selectedInst} />
      )}
    </ResizableDrawerFrame>
  );
}
