// ============================================================
// 基础 UI 组件
// ============================================================

import React, { useEffect, useRef, useState } from "react";

export function Button({
  variant = "ghost",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger";
}) {
  const v =
    variant === "primary" ? "btn-primary" : variant === "danger" ? "btn-danger" : "btn-ghost";
  return <button className={`btn ${v} ${className}`} {...props} />;
}

export function Chip({
  color,
  children,
  className = "",
}: {
  color: "life" | "pulse" | "gene" | "ceo" | "ok" | "warn" | "danger" | "dim";
  children: React.ReactNode;
  className?: string;
}) {
  const map: Record<string, string> = {
    life: "border-life-500/40 bg-life-500/10 text-life-300",
    pulse: "border-pulse-500/40 bg-pulse-500/10 text-pulse-300",
    gene: "border-gene-500/40 bg-gene-500/10 text-gene-300",
    ceo: "border-ceo-500/40 bg-ceo-500/10 text-ceo-300",
    ok: "border-ok-500/40 bg-ok-500/10 text-ok-400",
    warn: "border-warn-400/40 bg-warn-400/10 text-warn-400",
    danger: "border-danger-500/40 bg-danger-500/10 text-danger-400",
    dim: "border-ink-600/40 bg-ink-600/10 text-ink-400",
  };
  return (
    <span className={`chip ${map[color]} ${className}`}>{children}</span>
  );
}

export function Dot({
  color,
  pulse = false,
}: {
  color: string;
  pulse?: boolean;
}) {
  return (
    <span
      className={`dot ${pulse ? "dot-pulse" : ""}`}
      style={{ background: color }}
    />
  );
}

export function Spinner({ size = 16, className = "" }: { size?: number; className?: string }) {
  return (
    <span
      className={`inline-block animate-spin rounded-full border-2 border-life-400/30 border-t-life-400 ${className}`}
      style={{ width: size, height: size }}
    />
  );
}

export function Empty({
  icon,
  title,
  hint,
}: {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      {icon && <div className="mb-2 text-ink-600">{icon}</div>}
      <div className="text-[13px] font-medium text-ink-400">{title}</div>
      {hint && <div className="max-w-[340px] text-xs leading-5 text-ink-600">{hint}</div>}
    </div>
  );
}

/** 模态框 */
export function Modal({
  open,
  title,
  children,
  onClose,
  width = 480,
}: {
  open: boolean;
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-abyss-950/70 backdrop-blur-sm"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="surface pop-in flex max-h-[80vh] flex-col p-5"
        style={{ width, maxWidth: "92vw" }}
        role="dialog"
        aria-modal="true"
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-display text-[15px] font-semibold text-ink-100">{title}</h3>
          <button
            className="cursor-pointer text-ink-500 transition-colors hover:text-ink-200"
            onClick={onClose}
            aria-label="关闭"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}

/** 确认对话框（危险操作二次确认） */
export function ConfirmModal({
  open,
  title,
  body,
  confirmText = "确认",
  danger = false,
  busy = false,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  body: React.ReactNode;
  confirmText?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal open={open} title={title} onClose={onClose} width={400}>
      <div className="text-[13px] leading-6 text-ink-200">{body}</div>
      <div className="mt-5 flex justify-end gap-2">
        <Button onClick={onClose} disabled={busy}>
          取消
        </Button>
        <Button
          variant={danger ? "danger" : "primary"}
          onClick={onConfirm}
          disabled={busy}
        >
          {busy ? "处理中…" : confirmText}
        </Button>
      </div>
    </Modal>
  );
}

/** 分数条 */
export function ScoreBar({ value, max = 100 }: { value: number; max?: number }) {
  const p = Math.max(0, Math.min(1, value / max));
  const color = value >= 80 ? "#3ecf7f" : value >= 60 ? "#2fe0bd" : value >= 40 ? "#fbbf24" : "#f4535f";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-abyss-600">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${p * 100}%`, background: color }}
      />
    </div>
  );
}

/** 自动滚动容器（聊天流）
 *  粘性跟随（2026-09-04 修订，用户反馈"手动往上滑要能滑动，不能被钉在底部"）：
 *    - 用户在底部：新内容到达自动滚到底
 *    - 用户主动上滑（scrollTop 减小）→ 无条件立即释放跟随，新内容不再拉回
 *      （此前的 60px 阈值：刚上滑一点仍在阈值内，下一条事件/8s 轮询就把
 *      位置弹回底部 → 体验上"固定在最底、滑不动"）
 *    - 滚回距底 60px 内，或点右下角"回到底部"按钮 → 恢复跟随
 *    - 释放期间右下角显示"回到底部"浮动按钮
 */
export function AutoScroll({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const prevTop = useRef(0);
  const [detached, setDetached] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || !stick.current) return;
    el.scrollTop = el.scrollHeight;
  }, [children]);

  return (
    <div
      ref={ref}
      className={className}
      onScroll={(e) => {
        const el = e.currentTarget;
        const prev = prevTop.current;
        prevTop.current = el.scrollTop;
        const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        if (el.scrollTop < prev) {
          // 主动上滑 → 立即释放（不设阈值，避免"刚滑一点就被弹回"）
          stick.current = false;
        } else if (nearBottom) {
          // 回到底部附近 → 恢复跟随
          stick.current = true;
        }
        setDetached(!stick.current);
      }}
    >
      {children}
      {/* 释放态的「回到底部」按钮：sticky 贴住滚动视口底部（absolute 会随内容
          滚出视野；sticky bottom 始终可见且不影响内容流） */}
      {detached && (
        <div className="pointer-events-none sticky bottom-3 flex justify-end pr-4">
          <button
            className="pointer-events-auto flex cursor-pointer items-center gap-1 rounded-full border border-ink-600/30 bg-abyss-850/90 px-3 py-1.5 text-[11px] text-ink-300 shadow-lg transition-colors hover:border-life-400/50 hover:text-life-300"
            onClick={() => {
              const el = ref.current;
              if (!el) return;
              stick.current = true;
              setDetached(false);
              el.scrollTop = el.scrollHeight;
            }}
            aria-label="回到底部"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ transform: "rotate(90deg)" }}>
              <path d="m6 9 6 6 6-6" />
            </svg>
            回到底部
          </button>
        </div>
      )}
    </div>
  );
}

/** 可折叠块 */
export function Collapsible({
  title,
  children,
  defaultOpen = false,
  tone = "dim",
}: {
  title: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  tone?: "dim" | "life";
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div
      className="overflow-hidden rounded-lg border border-ink-600/20"
      style={{ background: "var(--code-bg)" }}
    >
      <button
        className={`flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-[11.5px] font-medium ${
          tone === "life" ? "text-life-400" : "text-ink-400"
        }`}
        onClick={() => setOpen(!open)}
      >
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }}
        >
          <path d="m9 18 6-6-6-6" />
        </svg>
        {title}
      </button>
      {open && <div className="px-3 pb-2">{children}</div>}
    </div>
  );
}

/** 代码块 */
export function Code({ children }: { children: string }) {
  return (
    <pre className="num max-h-[220px] overflow-auto whitespace-pre-wrap rounded-lg border border-ink-600/20 bg-abyss-950/70 p-3 text-[11.5px] leading-[1.7] text-ink-200">
      {children}
    </pre>
  );
}
