// ============================================================
// 历史会话 — 列表（预览/消息数/图谱规模）+ 载入恢复 + 新建对话
// 载入后：图谱 + 消息恢复进「对话工作台」，CEO 上下文同步激活
// ============================================================

import { useCallback, useEffect, useState } from "react";
import {
  Clock,
  FilePlus2,
  FolderOpen,
  MessageSquare,
  RefreshCw,
  Share2,
} from "lucide-react";
import { api } from "@/lib/api";
import { store, useAppState } from "@/lib/store";
import { Button, Chip, ConfirmModal, Empty, Spinner } from "@/components/ui";
import type { SessionListItem } from "@/types";

function fmtSavedAt(s: string): string {
  if (!s || s === "?") return "—";
  try {
    const d = new Date(s);
    if (isNaN(d.getTime())) return s;
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return s;
  }
}

export function SessionsView() {
  const [items, setItems] = useState<SessionListItem[] | null>(null);
  const [graphs, setGraphs] = useState<Map<string, number> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyLoad, setBusyLoad] = useState<string | null>(null);
  const [confirmNew, setConfirmNew] = useState(false);
  const [newBusy, setNewBusy] = useState(false);
  const viewSession = useAppState((s) => s.viewSession);
  const busy = useAppState((s) => s.busy);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [s, g] = await Promise.all([api.sessions(), api.sessionGraphs()]);
      setItems(s.sessions ?? []);
      const gm = new Map<string, number>();
      for (const item of g.sessions ?? []) gm.set(item.name, (item.graph ?? []).length);
      setGraphs(gm);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const doLoad = async (name: string) => {
    setBusyLoad(name);
    try {
      await store.loadSession(name);
    } finally {
      setBusyLoad(null);
    }
  };

  const doNew = async () => {
    setNewBusy(true);
    try {
      await store.newSession();
      setConfirmNew(false);
      void load();
    } finally {
      setNewBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      {/* 顶栏 */}
      <div className="flex h-[52px] shrink-0 items-center justify-between border-b border-ink-600/15 bg-abyss-900/40 px-5">
        <div className="flex items-center gap-3">
          <h2 className="font-display text-[15px] font-semibold text-ink-100">历史会话</h2>
          <span className="text-[11.5px] text-ink-600">
            载入后自动恢复图谱与对话，并激活该会话的 CEO 上下文
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={() => void load()} disabled={loading} className="!text-[12px]">
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} /> 刷新
          </Button>
          <Button
            variant="primary"
            onClick={() => setConfirmNew(true)}
            disabled={busy}
            className="!text-[12px]"
            title={busy ? "任务执行中无法新建对话" : "重置 CEO 上下文，开启新会话"}
          >
            <FilePlus2 size={13} /> 新建对话
          </Button>
        </div>
      </div>

      {/* 列表 */}
      <div className="bg-grid min-h-0 flex-1 overflow-y-auto p-5">
        {loading && (
          <div className="flex items-center gap-2.5 p-4 text-[12.5px] text-ink-500">
            <Spinner size={14} /> 正在读取会话库…
          </div>
        )}
        {error && !loading && (
          <div className="rounded-xl border border-danger-500/30 bg-danger-500/5 p-4 text-[12.5px] text-danger-400">
            读取失败：{error}
          </div>
        )}
        {!loading && !error && items && items.length === 0 && (
          <Empty
            icon={<FolderOpen size={36} strokeWidth={1.2} />}
            title="还没有历史会话"
            hint="完成任务后系统会自动保存会话（也可在对话中 /save 名称 手动保存）。保存的会话可以在此恢复图谱、对话与 CEO 上下文。"
          />
        )}

        {items && items.length > 0 && (
          <div className="mx-auto grid max-w-[980px] grid-cols-1 gap-3 lg:grid-cols-2">
            {items.map((s) => {
              const gCount = graphs?.get(s.name) ?? 0;
              const current = viewSession === s.name;
              return (
                <div
                  key={s.name}
                  className={`surface group flex flex-col p-4 transition-colors ${
                    current ? "!border-life-500/45" : "hover:!border-ink-500/40"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <MessageSquare size={14} className={current ? "text-life-400" : "text-ink-500"} />
                    <span className="num min-w-0 flex-1 truncate text-[13px] font-semibold text-ink-100">
                      {s.name}
                    </span>
                    {current && <Chip color="life">当前已载入</Chip>}
                  </div>

                  <div className="mt-2 line-clamp-2 min-h-[38px] text-[12.5px] leading-[1.65] text-ink-300">
                    {s.preview || "（无预览）"}
                  </div>

                  <div className="mt-3 flex items-center gap-3 text-[11px] text-ink-600">
                    <span className="flex items-center gap-1.5">
                      <Clock size={11} /> {fmtSavedAt(s.saved_at)}
                    </span>
                    <span className="num flex items-center gap-1.5">
                      <MessageSquare size={11} /> {s.msg_count} 条
                    </span>
                    {graphs != null && (
                      <Chip color={gCount > 1 ? "pulse" : "dim"} className="num !px-2 !py-0.5 !text-[10.5px]">
                        <Share2 size={10} /> 图谱 {gCount > 1 ? gCount - 1 : 0} 节点
                      </Chip>
                    )}
                  </div>

                  <div className="mt-3 flex justify-end">
                    <Button
                      variant={current ? "ghost" : "primary"}
                      className="!px-3.5 !py-2 !text-[12px]"
                      disabled={busyLoad === s.name}
                      onClick={() => void doLoad(s.name)}
                    >
                      {busyLoad === s.name ? (
                        <Spinner size={12} />
                      ) : (
                        <FolderOpen size={13} />
                      )}
                      {current ? "重新载入" : "载入会话"}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 新建确认 */}
      <ConfirmModal
        open={confirmNew}
        title="新建对话？"
        body={
          <>
            CEO 上下文将重置（历史会话全部保留在列表中，可随时切回）。
            {viewSession && (
              <span className="mt-2 block text-warn-400">
                当前载入了 {viewSession}，新建后其上下文不再激活。
              </span>
            )}
          </>
        }
        confirmText="新建"
        busy={newBusy}
        onConfirm={() => void doNew()}
        onClose={() => setConfirmNew(false)}
      />
    </div>
  );
}
