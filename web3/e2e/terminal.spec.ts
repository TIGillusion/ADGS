import { expect, test } from "@playwright/test";

test("execution terminal: incremental ANSI output, events, acceptance and replay", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.request.post("/e2e/reset");
  await page.request.post("/e2e/terminal");
  await page.goto("/");
  await page.locator("nav button", { hasText: "演化图谱" }).first().click();
  const canvas = page.locator("canvas").first();
  await expect.poll(() => canvas.evaluate((el) => Boolean((el as HTMLCanvasElement & { __adgsLayout?: { key: string }[] }).__adgsLayout?.some((n) => n.key === "ags_1@exec-terminal-demo")))).toBe(true);
  await page.waitForTimeout(600); // Allow the graph's entrance animation to settle.
  const position = await canvas.evaluate((el) => {
    const data = el as HTMLCanvasElement & { __adgsLayout: { key: string; x: number; y: number }[]; __adgsView: { x: number; y: number; k: number } };
    const node = data.__adgsLayout.find((n) => n.key === "ags_1@exec-terminal-demo")!;
    return { x: node.x * data.__adgsView.k + data.__adgsView.x, y: node.y * data.__adgsView.k + data.__adgsView.y };
  });
  await canvas.click({ position });
  await expect(page.getByTestId("terminal-panel")).toBeVisible();
  await expect(page.locator(".xterm-rows")).toContainText("TypeScript checks passed");
  await page.request.post("/e2e/terminal/push", { data: { data: "\r\n\x1b[32mLIVE OUTPUT ARRIVED\x1b[0m\r\n", evaluate: true } });
  await expect(page.locator(".xterm-rows")).toContainText("LIVE OUTPUT ARRIVED");
  await page.getByRole("button", { name: "展开执行面板" }).click();
  await page.screenshot({ path: "test/terminal-observatory.png", fullPage: true });
  await page.getByRole("button", { name: "执行事件", exact: false }).click();
  await expect(page.locator(".execution-event").getByText("节点决策", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "验收", exact: true }).click();
  await expect(page.getByText("验收通过", { exact: true })).toBeVisible();
  await expect(page.locator(".acceptance-card").getByText("构建检查", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test/terminal-acceptance.png", fullPage: true });
  await page.getByRole("button", { name: "实时终端", exact: true }).click();
  await expect(page.locator(".xterm-rows")).toContainText("LIVE OUTPUT ARRIVED");
  await page.getByRole("textbox", { name: "搜索终端" }).fill("LIVE OUTPUT");
  await expect(page.locator(".terminal-search")).toContainText("1/1");
  await page.getByRole("button", { name: "增大字号" }).click();
  await expect(page.locator(".terminal-toolbar")).toContainText("13");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByTestId("terminal-panel")).toBeHidden();
  expect(errors).toEqual([]);
});
