// ============================================================
// 展示格式化工具
// ============================================================

export function pct(v: number | undefined | null, digits = 0): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function score(v: number | undefined | null): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(1);
}

/** 分数 → 语义色类（用于文本着色） */
export function scoreTone(v: number | undefined | null): string {
  if (v == null) return "text-ink-500";
  if (v >= 80) return "text-ok-400";
  if (v >= 60) return "text-life-400";
  if (v >= 40) return "text-warn-400";
  return "text-danger-400";
}

/** Unix 时间戳 → MM-DD HH:mm */
export function fmtTime(ts: number | string | undefined | null): string {
  if (ts == null || ts === "") return "—";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** Unix 时间戳 → 完整时间（含秒） */
export function fmtTimeSec(ts: number | string | undefined | null): string {
  if (ts == null || ts === "") return "—";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 相对时间：x 秒/分钟/小时/天前 */
export function fmtAgo(ts: number | string | undefined | null): string {
  if (ts == null || ts === "") return "";
  const t = typeof ts === "number" ? ts * 1000 : new Date(ts).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `${Math.floor(s)} 秒前`;
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

/** 运行时长（秒 → h m s） */
export function fmtDuration(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const p = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}

/** 安全 JSON 字符串化（工具参数展示） */
export function fmtArgs(args: unknown): string {
  if (args == null) return "";
  if (typeof args === "string") return args;
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}

/** 截断文本 */
export function clampText(s: string | undefined | null, n: number): string {
  const t = s || "";
  return t.length > n ? t.slice(0, n) + "…" : t;
}

/** 节点 id 友好展示：ags_123 → ags_123（保留原样，用于等宽展示） */
export function nodeIdLabel(id: string): string {
  return id || "—";
}
