import { useRef, type PointerEvent as ReactPointerEvent } from "react";

/** Capture keeps a drag active even when the pointer leaves the narrow handle. */
export function usePointerDelta(start: (event: ReactPointerEvent<HTMLElement>) => ((dx: number, dy: number) => void) | null) {
  const active = useRef<{ id: number; x: number; y: number; update: (dx: number, dy: number) => void } | null>(null);
  const end = (event: ReactPointerEvent<HTMLElement>) => {
    if (active.current?.id !== event.pointerId) return;
    active.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };
  return {
    onPointerDown: (event: ReactPointerEvent<HTMLElement>) => {
      if (event.button !== 0 || active.current) return;
      const update = start(event);
      if (!update) return;
      event.preventDefault();
      event.stopPropagation();
      active.current = { id: event.pointerId, x: event.clientX, y: event.clientY, update };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    onPointerMove: (event: ReactPointerEvent<HTMLElement>) => {
      const drag = active.current;
      if (drag?.id === event.pointerId) drag.update(event.clientX - drag.x, event.clientY - drag.y);
    },
    onPointerUp: end,
    onPointerCancel: end,
    onLostPointerCapture: () => { active.current = null; },
  };
}
