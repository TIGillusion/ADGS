import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { FloatingPanel, ResizableDrawerFrame } from "@/components/PanelFrames";

beforeEach(() => {
  class Pointer extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit) { super(type, init); this.pointerId = init.pointerId ?? 1; }
  }
  vi.stubGlobal("PointerEvent", Pointer);
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function host() {
  const el = document.createElement("div");
  Object.defineProperties(el, { clientWidth: { value: 1000 }, clientHeight: { value: 700 } });
  document.body.append(el); return el;
}
function drag(el: HTMLElement, dx: number, dy = 0) {
  fireEvent.pointerDown(el, { pointerId: 1, button: 0, clientX: 500, clientY: 200 });
  fireEvent.pointerMove(el, { pointerId: 1, clientX: 500 + dx, clientY: 200 + dy });
  fireEvent.pointerUp(el, { pointerId: 1, clientX: 500 + dx, clientY: 200 + dy });
}
it("floating content moves by its title and resizes without leaving the graph", () => {
  render(<FloatingPanel position={{x:100,y:100}} header="A2A" onClose={() => {}}>完整对话</FloatingPanel>, {container:host()});
  const panel = screen.getByRole("dialog");
  const title = screen.getByTitle("拖动标题栏移动详情");
  drag(title, 100, 50);
  expect(panel.style.left).toBe("214px"); expect(panel.style.top).toBe("160px");
  drag(screen.getByRole("separator"), 100, 60);
  expect(panel.style.width).toBe("540px"); expect(panel.style.height).toBe("480px");
  drag(title, -2000, -2000);
  expect(panel.style.left).toBe("8px"); expect(panel.style.top).toBe("8px");
  drag(screen.getByRole("separator"), 3000, 3000);
  expect(panel.style.width).toBe("984px"); expect(panel.style.height).toBe("684px");
});
it("clicking Close does not start a move; cancel releases the drag", () => {
  const close = vi.fn();
  render(<FloatingPanel position={{x:100,y:100}} header="A2A" onClose={close}>内容</FloatingPanel>, {container:host()});
  fireEvent.pointerDown(screen.getByRole("button", {name:"关闭"}), {pointerId:1,button:0,clientX:20,clientY:20});
  fireEvent.click(screen.getByRole("button", {name:"关闭"})); expect(close).toHaveBeenCalledOnce();
  const title=screen.getByTitle("拖动标题栏移动详情"), panel=screen.getByRole("dialog");
  fireEvent.pointerDown(title,{pointerId:1,button:0,clientX:20,clientY:20});
  fireEvent.pointerCancel(title,{pointerId:1});
  fireEvent.pointerMove(title,{pointerId:1,clientX:100,clientY:100});
  expect(panel.style.left).toBe("114px");
});
it("drawer left edge grows leftwards, clamps, and resets on double click", () => {
  render(<ResizableDrawerFrame defaultWidth={440}>节点详情</ResizableDrawerFrame>, {container:host()});
  const edge=screen.getByRole("separator"),panel=edge.parentElement!;
  panel.getBoundingClientRect=() => ({width:440} as DOMRect);
  drag(edge,-180);expect(panel.style.getPropertyValue("--drawer-width")).toBe("620px");
  drag(edge,-3000);expect(panel.style.getPropertyValue("--drawer-width")).toBe("1000px");
  fireEvent.doubleClick(edge);expect(panel.style.getPropertyValue("--drawer-width")).toBe("440px");
  fireEvent.keyDown(edge,{key:"ArrowLeft"});expect(panel.style.getPropertyValue("--drawer-width")).toBe("460px");
});
