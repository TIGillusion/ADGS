// ============================================================
// AutoScroll 粘性跟随回归（2026-09-04：对话流自动滚动 + 手动上滑不被钉底）
//   1) 用户在底部 → 新内容到达自动滚到底
//   2) 主动上滑（scrollTop 减小）→ 无条件立即释放，新内容不弹回
//   3) 滚回底部附近 → 恢复跟随
//   4) 释放期间"回到底部"按钮出现，点击回底并恢复跟随
// jsdom 无布局：scrollHeight 恒 0，用 defineProperty 注入虚拟高度；
// clientHeight 恒 0，"距底 60px" 阈值按 scrollHeight - scrollTop 计算。
// ============================================================

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AutoScroll } from "@/components/ui";

function setScrollHeight(el: HTMLElement, h: number): void {
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: h });
}

function renderAtBottom(): { el: HTMLDivElement; rerender: (node: React.ReactElement) => void } {
  const { container, rerender } = render(
    <AutoScroll className="sc">
      <div>a</div>
    </AutoScroll>,
  );
  const el = container.querySelector(".sc") as HTMLDivElement;
  setScrollHeight(el, 100);
  el.scrollTop = 100; // 位于底部
  fireEvent.scroll(el); // prevTop=100, stick=true
  return { el, rerender };
}

function release(el: HTMLDivElement): void {
  el.scrollTop = 0; // 主动上滑
  fireEvent.scroll(el); // 0 < prevTop → 无条件释放
}

describe("AutoScroll", () => {
  it("底部粘性：新内容到达时自动滚到最底", () => {
    const { el, rerender } = renderAtBottom();
    setScrollHeight(el, 160);
    rerender(
      <AutoScroll className="sc">
        <div>a</div>
        <div>b</div>
      </AutoScroll>,
    );
    expect(el.scrollTop).toBe(160);
  });

  it("主动上滑立即释放：即使在 60px 阈值内也不弹回（2026-09-04 钉底反馈）", () => {
    const { el, rerender } = renderAtBottom();
    // 只上滑 30px（仍在旧阈值 60 内）→ 旧逻辑会保持跟随、新消息把位置弹回底部
    el.scrollTop = 70;
    fireEvent.scroll(el); // 70 < 100 → 释放
    expect(el.scrollTop).toBe(70);
    setScrollHeight(el, 160);
    rerender(
      <AutoScroll className="sc">
        <div>a</div>
        <div>b</div>
      </AutoScroll>,
    );
    expect(el.scrollTop).toBe(70); // 释放后新内容不得把滚动位置拉回底部
  });

  it("滚回底部附近恢复跟随", () => {
    const { el, rerender } = renderAtBottom();
    release(el);
    setScrollHeight(el, 150);
    el.scrollTop = 100; // 150-100=50 < 60 → 回到底部附近
    fireEvent.scroll(el);
    rerender(
      <AutoScroll className="sc">
        <div>a</div>
        <div>b</div>
      </AutoScroll>,
    );
    expect(el.scrollTop).toBe(150);
  });

  it("释放时显示「回到底部」按钮，点击回底并恢复跟随", () => {
    const { el, rerender } = renderAtBottom();
    release(el);
    rerender(
      <AutoScroll className="sc">
        <div>a</div>
        <div>b</div>
      </AutoScroll>,
    );
    const btn = screen.getByRole("button", { name: /回到底部/ });
    expect(btn).toBeVisible();
    setScrollHeight(el, 150);
    fireEvent.click(btn);
    expect(el.scrollTop).toBe(150);
    expect(screen.queryByRole("button", { name: /回到底部/ })).toBeNull();
  });
});
