import { expect, test } from "@playwright/test";

test("real Flask blueprint and SQLite terminal records integrate with xterm", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.locator("nav button", { hasText: "演化图谱" }).first().click();
  const canvas = page.locator("canvas").first();
  await expect.poll(() => canvas.evaluate((el) => Boolean((el as HTMLCanvasElement & { __adgsDrawnLayout?: { key: string; scale: number }[] }).__adgsDrawnLayout?.some((n) => n.key === "ags_1@blueprint-execution" && n.scale > .8)))).toBe(true);
  const position = await canvas.evaluate((el) => {
    const data = el as HTMLCanvasElement & { __adgsDrawnLayout: { key: string; x: number; y: number }[]; __adgsView: { x: number; y: number; k: number } };
    const node = data.__adgsDrawnLayout.find((n) => n.key === "ags_1@blueprint-execution")!;
    return { x: node.x * data.__adgsView.k + data.__adgsView.x, y: node.y * data.__adgsView.k + data.__adgsView.y };
  });
  await canvas.click({ position });
  await expect(page.locator(".xterm-rows")).toContainText("REAL FLASK BLUEPRINT");
  await expect(page.locator(".xterm-rows")).toContainText("中文输出与 ANSI 原样保留");
  await page.request.post("/integration/append");
  await expect(page.locator(".xterm-rows")).toContainText("PERSISTED LIVE CHUNK");
  await expect(page.locator(".terminal-footer")).toContainText("执行已结束");
  await page.getByRole("button", { name: "验收", exact: true }).click();
  await expect(page.locator(".acceptance-card").getByText("验收通过", { exact: true })).toBeVisible();
  await expect(page.getByText("持久化游标回放", { exact: true })).toBeVisible();
  const download = await page.request.get("/api/attempts/blueprint-attempt/download?format=text");
  const text = await download.text();
  expect(text).toContain("实时追加已写入 SQLite");
  expect(text).not.toContain("\x1b[");
  const replay = await page.request.get("/api/attempts/blueprint-attempt/terminal?after=3");
  expect(await replay.text()).toContain('"type": "end"');
  await page.getByRole("button", { name: "实时终端", exact: true }).click();
  await expect(page.locator(".xterm-rows")).toContainText("PERSISTED LIVE CHUNK");
  await page.getByRole("button", { name: "展开执行面板" }).click();
  await page.screenshot({ path: "test/terminal-real-blueprint.png", fullPage: true });
  expect(errors).toEqual([]);
});
