import { useLayoutEffect, useRef, useState, type ReactNode, type CSSProperties } from "react";
import { Grip, X } from "lucide-react";
import { usePointerDelta } from "@/lib/usePointerDelta";

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, Math.max(min, max)));

export function FloatingPanel({ position, header, children, onClose }: {
  position: { x: number; y: number }; header: ReactNode; children: ReactNode; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState({ x: position.x + 14, y: position.y + 10, width: 440, height: 420 });
  useLayoutEffect(() => {
    const parent = ref.current?.parentElement;
    if (!parent) return;
    const contain = () => setFrame(f => {
      const width = Math.min(f.width, Math.max(0, parent.clientWidth - 16));
      const height = Math.min(f.height, Math.max(0, parent.clientHeight - 16));
      return { width, height, x: clamp(f.x, 8, parent.clientWidth - width - 8), y: clamp(f.y, 8, parent.clientHeight - height - 8) };
    });
    contain();
    const observer = new ResizeObserver(contain);
    observer.observe(parent);
    return () => observer.disconnect();
  }, []);
  const move = usePointerDelta(event => {
    if ((event.target as HTMLElement).closest("button,a,input,textarea,select")) return null;
    const origin = { ...frame };
    return (dx, dy) => {
      const parent = ref.current?.parentElement;
      if (parent) setFrame(f => ({ ...f, x: clamp(origin.x + dx, 8, parent.clientWidth - f.width - 8), y: clamp(origin.y + dy, 8, parent.clientHeight - f.height - 8) }));
    };
  });
  const resize = usePointerDelta(() => {
    const origin = { ...frame };
    return (dx, dy) => {
      const parent = ref.current?.parentElement;
      if (!parent) return;
      const maxW = Math.max(0, parent.clientWidth - origin.x - 8), maxH = Math.max(0, parent.clientHeight - origin.y - 8);
      setFrame(f => ({ ...f, width: clamp(origin.width + dx, Math.min(300, maxW), maxW), height: clamp(origin.height + dy, Math.min(180, maxH), maxH) }));
    };
  });
  return (
    <div ref={ref} role="dialog" aria-label="A2A 委派详情" className="surface-glass absolute z-30 flex flex-col rounded-xl" style={{ left: frame.x, top: frame.y, width: frame.width, height: frame.height }}>
      <div {...move} title="拖动标题栏移动详情" className="flex shrink-0 cursor-move select-none items-center gap-2 border-b border-ink-600/20 px-3 py-2.5" style={{ touchAction: "none" }}>
        <Grip size={14} className="shrink-0 text-ink-500" />
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{header}</div>
        <button className="link-btn ml-auto text-ink-500" onClick={onClose} aria-label="关闭"><X size={14} /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3 pb-5">{children}</div>
      <div {...resize} role="separator" aria-label="调整A2A详情大小" title="拖动调整详情大小" className="absolute bottom-0 right-0 h-5 w-5 cursor-nwse-resize rounded-br-xl text-ink-500" style={{ touchAction: "none" }}>
        <svg viewBox="0 0 20 20" className="h-full w-full" aria-hidden="true"><path d="M9 16l7-7m-3 7l3-3" stroke="currentColor" strokeWidth="1.5" /></svg>
      </div>
    </div>
  );
}

export function ResizableDrawerFrame({ defaultWidth, children }: { defaultWidth: number; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(defaultWidth);
  const maxWidth = () => ref.current?.parentElement?.clientWidth || window.innerWidth;
  const resize = usePointerDelta(() => {
    const origin = ref.current?.getBoundingClientRect().width ?? width;
    return dx => setWidth(clamp(origin - dx, Math.min(300, maxWidth()), maxWidth()));
  });
  return (
    <div ref={ref} className="drawer-in w-[var(--drawer-width)] absolute inset-y-0 right-0 z-30 flex max-w-full flex-col border-l border-ink-600/20 bg-abyss-900/95 backdrop-blur-xl" style={{ "--drawer-width": `${width}px` } as CSSProperties}>
      <div {...resize} data-drawer-resize role="separator" aria-label="调整节点详情宽度" aria-orientation="vertical" aria-valuenow={Math.round(width)} tabIndex={0}
        title="拖动调整宽度，双击恢复默认宽度" onDoubleClick={() => setWidth(defaultWidth)}
        onKeyDown={event => {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
          event.preventDefault();
          setWidth(w => clamp(w + (event.key === "ArrowLeft" ? 20 : -20), Math.min(300, maxWidth()), maxWidth()));
        }}
        className="absolute inset-y-0 -left-1 z-40 w-2 cursor-col-resize transition-colors hover:bg-life-400/30 focus-visible:bg-life-400/30 focus-visible:outline-none" style={{ touchAction: "none" }} />
      {children}
    </div>
  );
}
