// ============================================================
// 系统设置弹窗（2026-09-27 模型 / 2026-09-28 运行与调优）
//
// 三个标签页：
//  - 模型：CEO / 决策大脑 / hermes 执行器（CEO 立即热生效；hermes 旧节点
//    钉住出生模型，新节点才用新模型）
//  - 运行：并行上限（信号量热 resize，立即生效）/ 委派深度 / 超时
//  - 调优：生存压力 τ、路由权重（专精 vs 得分）、探索系数（复用强者 vs
//    给机会）、采样参数 —— 每轮进化/每次派发实时读取，下一轮生效
// 保存时模型走 /api/models、参数走 /api/settings，均落盘、重启保留。
// ============================================================

import { useEffect, useState } from "react";
import {
  Gauge,
  KeyRound,
  Save,
  SlidersHorizontal,
  Sparkles,
  Terminal,
  Waypoints,
} from "lucide-react";
import { api } from "@/lib/api";
import { store } from "@/lib/store";
import { Button, Chip, Modal, Spinner } from "@/components/ui";
import type {
  AdvancedInfo,
  ModelGroupId,
  ModelGroupInfo,
  ModelUpdatePayload,
  RuntimeInfo,
  SettingsInfo,
  SettingsUpdatePayload,
} from "@/types";

// ---------------- 标签页 ----------------

type TabId = "models" | "runtime" | "tuning";

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  { id: "models", label: "模型", icon: <Sparkles size={13} /> },
  { id: "runtime", label: "运行", icon: <Gauge size={13} /> },
  { id: "tuning", label: "调优", icon: <SlidersHorizontal size={13} /> },
];

// ---------------- 模型（原 ModelsModal 逻辑） ----------------

interface GroupDraft {
  model: string;
  url: string;
  key: string;
}

type Drafts = Record<ModelGroupId, GroupDraft>;

const EMPTY_DRAFT: Drafts = {
  ceo: { model: "", url: "", key: "" },
  brain: { model: "", url: "", key: "" },
  worker: { model: "", url: "", key: "" },
};

const GROUP_META: {
  id: ModelGroupId;
  title: string;
  icon: React.ReactNode;
  hint: string;
}[] = [
  {
    id: "ceo",
    title: "CEO（任务入口 · 强推理）",
    icon: <Sparkles size={13} className="text-life-400" />,
    hint: "保存后立即生效；正在执行的任务从下一次模型调用起切换",
  },
  {
    id: "brain",
    title: "决策大脑（节点 Leader）",
    icon: <Waypoints size={13} className="text-gene-400" />,
    hint: "对下一个派发的任务生效",
  },
  {
    id: "worker",
    title: "hermes 执行器（节点干活）",
    icon: <Terminal size={13} className="text-pulse-400" />,
    hint: "已存在的节点保持各自模型不变；之后新建的节点使用此模型",
  },
];

// ---------------- 运行 / 调优字段元数据 ----------------

type NumDraft<R, A> = {
  runtime: Record<keyof R & string, string>;
  advanced: Record<keyof A & string, string>;
};

const RUNTIME_FIELDS: {
  key: keyof RuntimeInfo;
  label: string;
  hint: string;
}[] = [
  {
    key: "max_workers",
    label: "全局并行上限",
    hint: "同时执行的 hermes 进程数上限（跨节点/嵌套委派共享）。保存立即生效，正在排队的任务按新上限放行；不中断执行中的任务",
  },
  {
    key: "max_delegation_depth",
    label: "递归委派深度",
    hint: "节点拆分子任务的最大层数，超过后强制自己执行（防无限拆分）",
  },
  {
    key: "worker_timeout",
    label: "模型请求预算（秒）",
    hint: "决策和验收的单次请求预算，任务执行、排队和等待下属没有总墙钟上限",
  },
  {
    key: "inactivity_timeout",
    label: "无活动超时（秒）",
    hint: "持续没有模型内容、工具结果或子进程计算与读写进展才暂停。等待下属和执行名额不计时",
  },
];

const ADVANCED_FIELDS: {
  key: keyof AdvancedInfo;
  label: string;
  hint: string;
}[] = [
  {
    key: "tau",
    label: "生存压力 τ",
    hint: "每轮进化淘汰的力度（S 型曲线陡峭度）：越高淘汰越多、种群收缩越狠；调低更保守",
  },
  {
    key: "sim_weight",
    label: "专精权重",
    hint: "当前任务与节点专长的匹配权重；先筛选适配候选，无匹配专长时优先通用节点，再在相近候选中比较质量",
  },
  {
    key: "score_weight",
    label: "得分权重",
    hint: "历史成功率的有限质量修正；累计总分不直接决定任务分配，质量不能替代任务适配",
  },
  {
    key: "explore_beta",
    label: "探索系数",
    hint: "在适配相近候选中给冷启动节点成长机会，加成随已完成任务数衰减且有上限；0 仅关闭额外探索加成",
  },
  {
    key: "top_k",
    label: "路由候选数 top_k",
    hint: "每次派发从综合得分最高的前 N 个节点中采样",
  },
  {
    key: "temperature",
    label: "采样温度",
    hint: "候选采样的随机性：0 ≈ 确定性选最优，越高越雨露均沾",
  },
];

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-ink-500">{label}</span>
      {children}
    </label>
  );
}

function NumField({
  label,
  hint,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <Field label={label}>
        <input
          className="input num !py-2 !text-[12.5px]"
          value={value}
          inputMode="decimal"
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          spellCheck={false}
        />
      </Field>
      <div className="mt-1 text-[10.5px] leading-4 text-ink-600">{hint}</div>
    </div>
  );
}

// ---------------- 主组件 ----------------

export function SettingsModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<TabId>("models");
  const [drafts, setDrafts] = useState<Drafts>(EMPTY_DRAFT);
  const [modelsCur, setModelsCur] = useState<Record<ModelGroupId, ModelGroupInfo> | null>(null);
  const [settings, setSettings] = useState<SettingsInfo | null>(null);
  const [numDraft, setNumDraft] = useState<NumDraft<RuntimeInfo, AdvancedInfo>>({
    runtime: { max_workers: "", max_delegation_depth: "", worker_timeout: "", inactivity_timeout: "" },
    advanced: { tau: "", sim_weight: "", score_weight: "", explore_beta: "", top_k: "", temperature: "" },
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // 每次打开拉取当前配置（Key 不回传，留空 = 不修改）
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setDrafts(EMPTY_DRAFT);
    setTab("models");
    Promise.all([api.models(), api.settings()])
      .then(([m, s]) => {
        if (!alive) return;
        setModelsCur(m.models);
        setDrafts({
          ceo: { model: m.models.ceo.model, url: m.models.ceo.url, key: "" },
          brain: { model: m.models.brain.model, url: m.models.brain.url, key: "" },
          worker: { model: m.models.worker.model, url: m.models.worker.url, key: "" },
        });
        setSettings({ runtime: s.runtime, advanced: s.advanced });
        setNumDraft({
          runtime: Object.fromEntries(
            Object.entries(s.runtime).map(([k, v]) => [k, String(v)]),
          ) as NumDraft<RuntimeInfo, AdvancedInfo>["runtime"],
          advanced: Object.fromEntries(
            Object.entries(s.advanced).map(([k, v]) => [k, String(v)]),
          ) as NumDraft<RuntimeInfo, AdvancedInfo>["advanced"],
        });
      })
      .catch((e) => {
        if (alive) store.toast("error", `读取系统设置失败：${e instanceof Error ? e.message : String(e)}`);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open]);

  const setModelField = (g: ModelGroupId, f: keyof GroupDraft, v: string) => {
    setDrafts((d) => ({ ...d, [g]: { ...d[g], [f]: v } }));
  };

  const setNum = (group: "runtime" | "advanced", key: string, v: string) => {
    setNumDraft((d) => ({ ...d, [group]: { ...d[group], [key]: v } }));
  };

  const save = async () => {
    if (!modelsCur || !settings) return;
    // ---- 模型 diff（只提交变化字段；Key 留空 = 不修改） ----
    const modelPayload: ModelUpdatePayload = {};
    for (const { id } of GROUP_META) {
      const d = drafts[id];
      const cur = modelsCur[id];
      const entry: Record<string, string> = {};
      if (d.model.trim() && d.model.trim() !== cur.model) entry.model = d.model.trim();
      if (d.url.trim() && d.url.trim() !== cur.url) entry.url = d.url.trim();
      if (d.key.trim()) entry.key = d.key.trim();
      if (Object.keys(entry).length) {
        (modelPayload as Record<string, unknown>)[id] = entry;
      }
    }
    // ---- 参数 diff（非法数字不提交，让后端校验兜底） ----
    const runtime: Record<string, number> = {};
    for (const f of RUNTIME_FIELDS) {
      const v = Number(numDraft.runtime[f.key]);
      if (Number.isFinite(v) && v !== settings.runtime[f.key]) runtime[f.key] = v;
    }
    const advanced: Record<string, number> = {};
    for (const f of ADVANCED_FIELDS) {
      const v = Number(numDraft.advanced[f.key]);
      if (Number.isFinite(v) && v !== settings.advanced[f.key]) advanced[f.key] = v;
    }
    const hasModels = Object.keys(modelPayload).length > 0;
    const hasSettings = Object.keys(runtime).length > 0 || Object.keys(advanced).length > 0;
    if (!hasModels && !hasSettings) {
      store.toast("info", "没有修改，无需保存");
      onClose();
      return;
    }
    setSaving(true);
    try {
      if (hasModels) await store.saveModels(modelPayload);
      if (hasSettings) await store.saveSettings({ runtime, advanced } as SettingsUpdatePayload);
      onClose();
    } catch {
      /* 失败 toast 已由 store 弹出，留在弹窗让用户改 */
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} title="系统设置" onClose={onClose} width={560}>
      {loading ? (
        <div className="flex items-center justify-center gap-2 py-10 text-[12.5px] text-ink-500">
          <Spinner size={14} /> 读取当前配置…
        </div>
      ) : (
        <>
          {/* 标签页 */}
          <div className="mb-4 flex gap-1 rounded-xl border border-ink-600/15 bg-abyss-950/40 p-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                className={`flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-lg py-1.5 text-[12.5px] transition-colors ${
                  tab === t.id ? "bg-gene-500/15 text-gene-300" : "text-ink-500 hover:text-ink-300"
                }`}
                onClick={() => setTab(t.id)}
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>

          {tab === "models" && (
            <div className="space-y-4">
              {GROUP_META.map(({ id, title, icon, hint }) => (
                <section
                  key={id}
                  className="rounded-xl border border-ink-600/15 bg-abyss-850/40 p-3"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 text-[12.5px] font-medium text-ink-100">
                      {icon}
                      {title}
                    </span>
                    {id === "worker" && (
                      <Chip color="pulse" className="!text-[10px]">
                        旧保持 · 新用新
                      </Chip>
                    )}
                  </div>
                  <div className="grid grid-cols-[1fr_1.25fr_1fr] gap-2.5">
                    <Field label="模型名">
                      <input
                        className="input num !py-2 !text-[12.5px]"
                        value={drafts[id].model}
                        onChange={(e) => setModelField(id, "model", e.target.value)}
                        placeholder={modelsCur?.[id].model || "模型名"}
                        spellCheck={false}
                      />
                    </Field>
                    <Field label="API 地址">
                      <input
                        className="input num !py-2 !text-[12.5px]"
                        value={drafts[id].url}
                        onChange={(e) => setModelField(id, "url", e.target.value)}
                        placeholder={modelsCur?.[id].url || "https://…/v1"}
                        spellCheck={false}
                      />
                    </Field>
                    <Field label="API Key">
                      <div className="relative">
                        <KeyRound
                          size={13}
                          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-600"
                        />
                        <input
                          type="password"
                          className="input num !py-2 !pl-7 !text-[12.5px]"
                          value={drafts[id].key}
                          onChange={(e) => setModelField(id, "key", e.target.value)}
                          placeholder="留空 = 保持现有"
                          autoComplete="off"
                        />
                      </div>
                    </Field>
                  </div>
                  <div className="mt-1.5 text-[11px] leading-5 text-ink-600">{hint}</div>
                </section>
              ))}
            </div>
          )}

          {tab === "runtime" && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {RUNTIME_FIELDS.map((f) => (
                <NumField
                  key={f.key}
                  label={f.label}
                  hint={f.hint}
                  value={numDraft.runtime[f.key]}
                  onChange={(v) => setNum("runtime", f.key, v)}
                />
              ))}
            </div>
          )}

          {tab === "tuning" && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {ADVANCED_FIELDS.map((f) => (
                <NumField
                  key={f.key}
                  label={f.label}
                  hint={f.hint}
                  value={numDraft.advanced[f.key]}
                  onChange={(v) => setNum("advanced", f.key, v)}
                />
              ))}
            </div>
          )}

          <div className="mt-4 flex items-center justify-end gap-2">
            <Button onClick={onClose} disabled={saving}>
              取消
            </Button>
            <Button variant="primary" onClick={() => void save()} disabled={saving || loading}>
              {saving ? <Spinner size={13} /> : <Save size={13} />}
              保存并热生效
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}
