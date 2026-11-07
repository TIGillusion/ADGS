// ============================================================
// ADGS web3 E2E — 全流程（mock 后端）
// 场景：启动/导航 → 对话全生命周期 → 图谱画布 → 节点抽屉 →
//       种群管理（归档/复活） → 历史会话载入 → 斜杠命令 →
//       手动进化横幅 → 新建会话 → 排队提示
// ============================================================

import { expect, test, beforeEach, type Page, type Request } from "@playwright/test";

async function e2eState(page: Page) {
  const res = await page.request.get("/e2e/state");
  return res.json() as Promise<{
    busy: boolean;
    queueLen: number;
    savedGraphs: {
      name: string;
      graph: { key: string; id?: string; type: string; parent?: string; label?: string;
        extra?: { contracts?: { taskId?: string; id: string }[] } }[];
    }[];
    chats: { text: string }[];
    commands: string[];
  }>;
}

async function reset(page: Page) {
  await page.request.post("/e2e/reset");
}

/** 导航按钮带徽标子元素（如「3」），无障碍名不等于纯文案，统一用 hasText 点击 */
async function navTo(page: Page, label: string) {
  await page.getByRole("button", { name: label, exact: true }).click();
}

async function loadHistory(page: Page, name: string) {
  await page.locator(`[data-session-id="${name}"] .project-chat-open`).click();
  await expect.poll(() => page.evaluate(() => window.__adgs_store!.getState().viewSession)).toBe(name);
}

async function expectBusy(page: Page, busy: boolean) {
  await expect.poll(() => page.evaluate(() => window.__adgs_store!.getState().busy)).toBe(busy);
}

test.beforeEach(async ({ page }) => {
  await reset(page);
});

test("启动：布局 + SSE 实时连接 + 种群快览", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator(".project-sidebar-heading")).toHaveText(/ADGS\s+Workspace/);
  // /api/state 加载后侧栏显示 3/8
  await expect(page.locator("aside").getByText("3", { exact: true }).first()).toBeVisible();
  // SSE 建立后指示器变为「实时连接」
  await expect(page.getByText("实时连接", { exact: true })).toBeVisible({ timeout: 15000 });
  // 五个导航项（按钮带徽标子元素，用 hasText 部分匹配）
  for (const label of ["对话工作台", "演化图谱", "节点种群", "系统概览"]) {
    await expect(page.locator("nav button", { hasText: label })).toBeVisible();
  }
});

test("对话：完整任务生命周期 + 图谱落盘", async ({ page }) => {
  await page.goto("/");
  await navTo(page, "对话工作台");

  const box = page.locator("textarea");
  await box.fill("帮我抓取两个页面");
  await box.press("Enter");

  // 用户消息回显
  await expect(page.getByText("帮我抓取两个页面", { exact: true }).first()).toBeVisible();
  // 忙碌标记出现（导航徽章「忙」）
  await expectBusy(page, true);
  // CEO 报告 + 交付物
  await expect(page.getByText("已拆解为两个子任务")).toBeVisible({ timeout: 20000 });
  await expect(page.getByText("两个页面抓取完成，结果已落盘")).toBeVisible({ timeout: 25000 });
  // 系统收尾 + 忙碌解除
  await expect(page.getByText("任务完成", { exact: true }).first()).toBeVisible({ timeout: 15000 });
  await expectBusy(page, false);

  // 图谱防抖落盘（1.5s）：mock 端收到含实例键的图谱
  const st = await e2eState(page);
  expect(st.savedGraphs.length).toBeGreaterThanOrEqual(1);
  const keys = st.savedGraphs.flatMap((g) => g.graph.map((n) => n.key));
  expect(keys).toContain("ags_1@t2_1_e2e");
  expect(keys).toContain("ags_1@t2_2_e2e");
  expect(keys).toContain("round-1");
  // 任务节点 label = 每轮序号（从 1 开始）：后端 seq 漂移（ask_user 挂起/
  // 异常恢复后累计）不得带到显示（2026-09-03 用户实测"轮 4 从 2 开始"修复）
  const taskLabels = st.savedGraphs
    .flatMap((g) => g.graph)
    .filter((n) => n.type === "task" && n.parent === "round-1")
    .map((n) => n.label);
  expect(taskLabels).toEqual(["1", "2"]);
});

test("图谱：Canvas 渲染 + 统计条 + 图例", async ({ page }) => {
  await page.goto("/");
  // 先发一个任务让图谱有内容
  await navTo(page, "对话工作台");
  const box = page.locator("textarea");
  await box.fill("抓取任务");
  await box.press("Enter");
  await expect(page.getByText("任务完成", { exact: true }).first()).toBeVisible({ timeout: 25000 });

  await navTo(page, "演化图谱");
  await expect(page.locator("canvas").first()).toBeVisible();
  // 1 轮 · 2 任务 · 2 实例
  await expect(page.getByText("1 轮 · 2 批次 · 2 实例")).toBeVisible({ timeout: 10000 });
  // 图例
  await expect(page.getByText("轮次", { exact: true })).toBeVisible();
  await page.getByText("节点状态颜色", { exact: true }).click();
  await expect(page.locator('[data-graph-status="done"]')).toBeVisible();
});

test("节点：卡片 + 搜索过滤 + 归档区 + 抽屉", async ({ page }) => {
  await page.goto("/");
  await navTo(page, "节点种群");

  // 活动节点卡片（按分数降序：ags_1 在前）
  await expect(page.getByText("ags_1", { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("ags_3", { exact: true })).toBeVisible();

  // 搜索过滤
  const search = page.getByPlaceholder("搜索节点 / 生态位…");
  await search.fill("抓取");
  await expect(page.getByText("ags_1", { exact: true })).toBeVisible();
  await expect(page.getByText("ags_3", { exact: true })).toBeHidden();
  await search.fill("");
  await expect(page.getByText("ags_3", { exact: true })).toBeVisible();

  // 归档区（点击「已淘汰节点」展开并懒加载）
  await page.locator("button", { hasText: "已淘汰节点" }).click();
  await expect(page.getByText("ags_0", { exact: true })).toBeVisible({ timeout: 15000 });

  // 打开节点抽屉：技能 + 描述（loading 文案是瞬态的，直接等结果）
  await page.getByText("ags_1", { exact: true }).first().click();
  await expect(page.getByText("web_scrape", { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.getByText("json_parse", { exact: true })).toBeVisible();
  // Esc 关闭
  await page.keyboard.press("Escape");
  await expect(page.getByText("web_scrape", { exact: true })).toBeHidden({ timeout: 5000 });
});

test("节点：归档复活 + 移除确认", async ({ page }) => {
  await page.goto("/");
  await navTo(page, "节点种群");
  await page.locator("button", { hasText: "已淘汰节点" }).click();
  await expect(page.getByText("ags_0", { exact: true })).toBeVisible({ timeout: 15000 });

  // 复活
  await page.locator("button[title='复活'], button:has-text('复活')").first().click();
  await expect(page.getByText("ags_0 已复活回活动池")).toBeVisible({ timeout: 10000 });

  // 移除（抽屉内危险操作，需确认）。
  // 注意：归档折叠开关「已淘汰节点（1）」文本含「淘汰节点」子串，
  // 用 hasText 会先命中它。危险按钮的 class 是 btn-danger，必须精确限定。
  await page.getByText("ags_0", { exact: true }).first().click();
  await page.locator("button.btn-danger", { hasText: "淘汰节点" }).click();
  await page.getByRole("button", { name: "确认操作" }).click();
  await expect(page.getByText("ags_0 已移除")).toBeVisible({ timeout: 10000 });
});

test("会话：列表 + 载入历史（图谱+转录）+ 隔离占位", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator('[data-session-id="demo-session"]')).toBeVisible();
  await loadHistory(page, "demo-session");
  await expect(page.getByText("已载入会话 demo-session")).toBeVisible({ timeout: 10000 });

  // 切到对话工作台：历史转录 + 隔离占位
  await navTo(page, "对话工作台");
  await expect(page.locator(".chat-scroll").getByText("把这篇英文摘要翻译成中文并润色", { exact: true })).toBeVisible();
  await expect(
    page.getByPlaceholder(/历史会话已激活（demo-session）/),
  ).toBeVisible();

  // 2026-09-03：CEO 话术还原 + 连续工具折叠组
  // CEO 提问卡片（此前历史恢复只剩工具，CEO 话术丢失）
  await expect(page.getByText("翻译需要保留原文术语吗？", { exact: true })).toBeVisible();
  // 连续工具合并成「工具 ×2」组，默认收缩（芯片行折叠，点击才展开逐条详情）
  await expect(page.getByText("工具 ×2")).toBeVisible();
  await page.getByText("工具 ×2").click();
  await expect(page.getByText("split_tasks", { exact: true })).toBeVisible();

  // 图谱视图恢复历史图谱（含旧版 clone 节点）
  await navTo(page, "演化图谱");
  await expect(page.getByText("1 轮 · 1 批次 · 1 实例")).toBeVisible({ timeout: 10000 });

  // 历史视图标记
  await expect(page.locator('[data-session-id="demo-session"]')).toHaveClass(/selected/);
});

test("历史会话：多轮图谱载入 → 自动聚焦最近轮 + 轮次下拉（2026-09-04）", async ({ page }) => {
  // 背景：历史会话跑了很多轮，tidy tree 纵向很高，fit 全图缩到不可读（用户误以为
  // "丢节点"）。修复：载入 >3 轮会话默认聚焦最近一轮；图谱工具栏提供轮次下拉。
  await page.goto("/");
  await loadHistory(page, "multi-round");
  await expect(page.getByText("已载入会话 multi-round")).toBeVisible({ timeout: 10000 });

  await navTo(page, "演化图谱");
  // 5 轮会话：统计条 + 默认聚焦最近轮（轮 5）
  await expect(page.getByText("5 轮 · 1 批次 · 1 实例")).toBeVisible({ timeout: 10000 });
  await expect(page.getByText(/已聚焦 轮 5/)).toBeVisible();
  // 轮次下拉存在且默认选中"轮 5"
  const sel = page.getByLabel("按轮次聚焦");
  await expect(sel).toBeVisible();
  expect(await sel.inputValue()).toBe("round-5");
  // 切到"全部轮次" → 解除聚焦（回全图）
  await sel.selectOption("all");
  await expect(page.getByText(/已聚焦 轮 5/)).toBeHidden({ timeout: 5000 });
  // 再选"轮 3" → 重新聚焦
  await sel.selectOption("round-3");
  await expect(page.getByText(/已聚焦 轮 3/)).toBeVisible();
});

test("历史会话：点开实例 → 抽屉磁盘日志回退（2026-09-03 修复）", async ({ page }) => {
  // 背景：历史会话实例无实时日志（刷新/载入后实时缓冲为空）→ 抽屉 hermes/大脑
  // 两标签应回退到磁盘持久化日志尾部（/api/node/<id>/logs），而不是全空。
  await page.goto("/");
  await loadHistory(page, "demo-session");
  await expect(page.getByText("已载入会话 demo-session")).toBeVisible({ timeout: 10000 });

  // 历史图谱：ceo → 轮 1 → 任务 1 → ags_2 实例（单链）
  await navTo(page, "演化图谱");
  await expect(page.getByText("1 轮 · 1 批次 · 1 实例")).toBeVisible({ timeout: 10000 });

  // Canvas 点击：双击强制 fit（幂等），再按 fit 后的确定性坐标点击 ags_2 实例。
  // 行星布局为楔形星系密排（节点随枝向旋转；单链 = 径向堆叠，沿正下方延伸），
  // demo-session 单链 ceo → round → task → ags_2 垂直向下，但坐标不再手算——
  // 布局与相机实时挂在 canvas.__adgsLayout / __adgsView 钩子上（见下）。
  // 布局/相机动画存在时序抖动，点击后若抽屉未开，在预期点附近小范围探测
  // （双击 fit 幂等；探测网格 ≤ 预期点 ±40px），保证测的是"抽屉功能"而非坐标。
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("图谱 canvas 不可见");
  const { x: cx, y: cy, width: cw, height: ch } = box;
  // 载入 demo 后 render 已自动 fit（stats 断言确认 4 节点）。GraphCanvas 每帧把
  // 布局与相机挂到 canvas.__adgsLayout / __adgsView —— 直接读真实屏幕坐标点击，
  // 不依赖手算 fit 公式（布局/动画改动不再破坏本测试）。
  let targetPx: number | null = null;
  let targetPy: number | null = null;
  for (let i = 0; i < 20; i++) {
    const meta = await canvas.evaluate((el) => {
      const c = el as HTMLCanvasElement & {
        __adgsLayout?: { key: string; x: number; y: number; w: number; h: number }[];
        __adgsDrawnLayout?: { key: string; x: number; y: number; scale: number }[];
        __adgsView?: { x: number; y: number; k: number };
      };
      const node = (c.__adgsDrawnLayout ?? []).find((n) => n.key === "ags_2@t1_1_old");
      const v = c.__adgsView;
      if (!node || !v || node.scale < 0.7) return null;
      return { sx: node.x * v.k + v.x, sy: node.y * v.k + v.y };
    });
    if (meta) {
      targetPx = cx + meta.sx;
      targetPy = cy + meta.sy;
      break;
    }
    await page.waitForTimeout(250);
  }
  if (targetPx == null || targetPy == null) throw new Error("无法从画布读取 ags_2 布局坐标");
  await page.mouse.click(targetPx, targetPy);

  // 抽屉：节点档案（ags_2 技能）+ 磁盘日志回退（hermes 标签默认展示磁盘尾部）
  await expect(page.getByText("long_form_writing", { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(page.getByText(/历史日志 · 磁盘尾部/)).toBeVisible();
  await expect(page.getByText("ags_2 磁盘历史行A")).toBeVisible();
  await expect(page.getByText("ags_2 磁盘历史行B")).toBeVisible();

  // 大脑决策标签：agentlog 磁盘历史
  await page.getByRole("button", { name: /大脑决策/ }).click();
  await expect(page.getByText("ags_2 agent 历史行")).toBeVisible({ timeout: 5000 });

  // Esc 关闭
  await page.keyboard.press("Escape");
  await expect(page.getByText(/历史日志 · 磁盘尾部/)).toBeHidden({ timeout: 5000 });
});

test("斜杠命令：补全 + 执行回执", async ({ page }) => {
  await page.goto("/");
  await navTo(page, "对话工作台");

  const box = page.locator("textarea");
  await box.fill("/st");
  // 补全浮层出现 /status
  await expect(page.locator("button", { hasText: "/status" }).first()).toBeVisible({ timeout: 5000 });
  await page.locator("button", { hasText: "/status" }).first().click();
  await box.press("Enter");

  await expect(page.getByText("状态：idle · 种群 3/8 · 队列 0")).toBeVisible({ timeout: 10000 });
  const st = await e2eState(page);
  expect(st.commands).toContain("/status");
});

test("进化：手动触发 + 横幅 + 种群刷新", async ({ page }) => {
  await page.goto("/");
  await navTo(page, "系统概览");

  await page.getByRole("button", { name: "手动进化", exact: true }).click();
  // 确认弹层
  await expect(page.getByText("手动触发一轮进化？")).toBeVisible();
  await page.getByRole("button", { name: "开始进化" }).click();

  // 进化横幅（3 → 4）
  await expect(page.getByText("种群进化")).toBeVisible({ timeout: 10000 });
  await expect(page.getByText("3 → 4").first()).toBeVisible();

  // 概览「最近一次进化」卡片刷新
  await navTo(page, "系统概览");
  await expect(page.getByText("最近一次进化")).toBeVisible();
});

test("旧后端缺少项目接口时保留历史，禁用新会话入口", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator('[data-session-id="demo-session"]')).toBeVisible();
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeDisabled();
  await loadHistory(page,"demo-session");
  await expect(page.getByText("翻译需要保留原文术语吗？", {exact:true})).toBeVisible();
});

test("排队：执行中再发任务 → 排队提示", async ({ page }) => {
  await page.goto("/");
  await navTo(page, "对话工作台");

  const box = page.locator("textarea");
  await box.fill("第一个任务");
  await box.press("Enter");
  // 任务开始后立刻再发
  await expectBusy(page, true);
  await box.fill("第二个任务");
  await box.press("Enter");

  await expect(page.getByText("任务已排队：当前任务结束后执行，可在队列编辑或撤销")).toBeVisible({
    timeout: 10000,
  });
  const st = await e2eState(page);
  expect(st.queueLen).toBe(1);
});

test("序号：跨轮 seq 漂移 → 每轮任务 label 从 1 开始", async ({ page }) => {
  // 回归：2026-09-03 用户实测"轮 4 直接从 2 开始"。mock 的 seq 游标跨提交
  // 累计（模拟真实后端 ask_user 挂起/异常恢复后 _task_seq 不重置的漂移），
  // 前端必须按轮重编号：每轮任务 label 都是 1..N，与后端累计 seq 解耦。
  await page.goto("/");
  await navTo(page, "对话工作台");
  const box = page.locator("textarea");

  // 第一个任务（本轮后端 seq 从某值继续）
  await box.fill("帮我抓取两个页面");
  await box.press("Enter");
  await expect(page.getByText("任务完成", { exact: true }).first()).toBeVisible({ timeout: 25000 });
  await expectBusy(page, false);

  // 第二个任务（后端 seq 继续累计，不重置）
  await box.fill("再抓两个页面");
  await box.press("Enter");

  // 2026-09-04：此前用 ".first()" 等「任务完成」——它立刻匹配到第一个任务的
  // 卡片标题就通过，断言抢在第二个任务图谱落盘（派发后 ~2s 防抖）之前执行，
  // 结果取决于机器负载（竞态）。改为直接轮询 mock 落盘，等第二任务的 2 轮图出现。
  // 2026-09-04（再次收紧）：仅等"2 轮"不够——round-2 的两个任务在派发间隙落盘
  // （debounce 快照可能只含 1 个任务）。等到"2 轮 × 各 2 任务"的稳定落盘：
  // 图谱只增不删，该条件一旦满足，后续落盘必然仍满足（无竞态窗口）。
  await expect(async () => {
    const s = await e2eState(page);
    const latest = s.savedGraphs[s.savedGraphs.length - 1]?.graph ?? [];
    const rounds = latest.filter((n) => n.type === "round" && n.parent === "ceo");
    expect(rounds.length).toBe(2);
    for (const r of rounds) {
      const tasks = latest.filter((n) => n.type === "task" && n.parent === r.key);
      expect(tasks.length).toBe(2);
    }
  }).toPass({ timeout: 30000 });

  const st = await e2eState(page);
  const latest = st.savedGraphs[st.savedGraphs.length - 1].graph;
  const rounds = latest.filter((n) => n.type === "round" && n.parent === "ceo");
  expect(rounds.length).toBe(2, "两个任务 = 两轮");
  // 每轮的任务 label 必须是从 1 开始的连续序号
  for (const r of rounds) {
    const labels = latest
      .filter((n) => n.type === "task" && n.parent === r.key)
      .map((n) => n.label);
    expect(labels.length).toBe(2);
    expect(labels).toEqual(["1", "2"], `轮 ${r.key} 的 label 应从 1 开始，实际 ${labels}`);
  }
  // 第二轮任务的后端原始 seq 嵌在 id（t2_{seq}_e2e）中，应 > 第一轮的 seq
  const roundTaskIds = (rk: string) =>
    latest.filter((n) => n.type === "task" && n.parent === rk)
      .flatMap((n) => (n.extra?.contracts ?? []).map(c => c.taskId || c.id));
  const seqOf = (id: string) => Number(id.match(/t2_(\d+)_e2e/)?.[1] ?? 0);
  const r1max = Math.max(...roundTaskIds(rounds[0].key).map(seqOf));
  const r2min = Math.min(...roundTaskIds(rounds[1].key).map(seqOf));
  expect(r1max).toBeGreaterThan(0);
  expect(r2min).toBeGreaterThan(r1max, "第二轮后端 seq 累计（漂移），前端 label 仍从 1 开始");
});

test("对话流：新消息自动滚到底部（2026-09-04 粘性跟随）", async ({ page }) => {
  await page.goto("/");
  await navTo(page, "对话工作台");
  const box = page.locator("textarea");
  await box.fill("帮我抓取两个页面");
  await box.press("Enter");

  // 新消息持续到达且用户未滚动 → 容器应始终贴底
  await expect(page.getByText("任务完成", { exact: true }).first()).toBeVisible({ timeout: 25000 });
  await page.waitForTimeout(800); // 吸收收尾系统消息渲染 + 自动滚动
  const atBottom = await page.evaluate(() => {
    const el = document.querySelector(".chat-scroll") as HTMLDivElement | null;
    if (!el) return false;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  });
  expect(atBottom).toBe(true, "新消息到达后消息流应自动贴底");
});

test("对话流：手动上滑不被钉底（2026-09-04：上滑即释放跟随 + 回到底部按钮）", async ({ page }) => {
  await page.goto("/");
  await navTo(page, "对话工作台");
  const box = page.locator("textarea");

  // 两个任务积累消息量，确保消息流溢出视口（可滚动）
  await box.fill("帮我抓取两个页面");
  await box.press("Enter");
  await expect(page.getByText("任务完成", { exact: true }).first()).toBeVisible({ timeout: 25000 });
  await expectBusy(page, false);
  await box.fill("再抓两个页面");
  await box.press("Enter");

  // 等消息流溢出（第二任务执行中，SSE 事件持续到达）
  const el = page.locator(".chat-scroll");
  await expect
    .poll(async () => (await el.evaluate((n) => n.scrollHeight - n.clientHeight)) > 120, {
      timeout: 25000,
    })
    .toBe(true);

  // 主动上滑到顶部 → 释放跟随
  await el.evaluate((n) => {
    n.scrollTop = 0;
    n.dispatchEvent(new Event("scroll"));
  });
  // 事件仍在到达（任务未完成）→ 位置必须停在顶部，不被拉回底部
  await page.waitForTimeout(2500);
  expect(await el.evaluate((n) => n.scrollTop)).toBe(0, "上滑释放后新内容不得把位置拉回底部");

  // 释放态出现「回到底部」按钮，点击后回到最底并恢复跟随
  const btn = page.getByRole("button", { name: /回到底部/ });
  await expect(btn).toBeVisible({ timeout: 5000 });
  await btn.click();
  await expect
    .poll(async () => (await el.evaluate((n) => n.scrollHeight - n.scrollTop - n.clientHeight)) < 60)
    .toBe(true);
  await expect(btn).toBeHidden();
});

test("错误态：后端不可达 → 重连入口", async ({ page, request }: { page: Page; request: Request }) => {
  // 通过拦截 /api/state 制造后端失败
  await page.route("**/api/state", (route) =>
    route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ ok: false, error: "down" }) }),
  );
  await page.route("**/api/events", (route) => route.abort());
  await page.goto("/");
  await expect(page.getByText("无法连接 ADGS 后端")).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole("button", { name: "重新连接" })).toBeVisible();
  void request;
});

test("图谱：拖动节点 → 整棵子树跟随，无关节点不动（2026-09-05）", async ({ page }) => {
  // 载入 multi-round（round-5 下带 t5_1_multi → ags_3），拖 round-5 后：
  // round-5 + 其子孙全部平移同样增量；round-1（非其子树）坐标不变。
  await page.goto("/");
  await loadHistory(page,"multi-round");
  await expect(page.getByText("已载入会话 multi-round")).toBeVisible({ timeout: 10000 });

  await navTo(page, "演化图谱");
  // >3 轮 → 自动聚焦最近轮（round-5）；切回全部轮次看整图
  const sel = page.getByLabel("按轮次聚焦");
  await expect(sel).toBeVisible({ timeout: 10000 });
  await sel.selectOption("all");
  await expect(page.getByText(/已聚焦 轮 5/)).toBeHidden({ timeout: 5000 });

  const canvas = page.locator("canvas").first();
  await canvas.waitFor({ state: "visible" });
  // 等待布局钩子出现所需节点（每帧更新：key → 屏幕坐标）
  const readPos = async (key: string) =>
    canvas.evaluate((el, k) => {
      const c = el as HTMLCanvasElement & {
        __adgsLayout?: { key: string; x: number; y: number; w: number; h: number }[];
        __adgsView?: { x: number; y: number; k: number };
      };
      const n = (c.__adgsLayout ?? []).find((x) => x.key === k);
      const v = c.__adgsView;
      if (!n || !v) return null;
      return { sx: n.x * v.k + v.x, sy: n.y * v.k + v.y };
    }, key);

  let before: Record<string, { sx: number; sy: number }> | null = null;
  for (let i = 0; i < 30; i++) {
    const a = await readPos("round-5");
    const b = await readPos("round-1");
    const c = await readPos("round-5/batch/t5_1_multi");
    const d = await readPos("ags_3@t5_1_multi");
    if (a && b && c && d) {
      before = { r5: a, r1: b, task: c, inst: d };
      break;
    }
    await page.waitForTimeout(200);
  }
  if (!before) throw new Error("画布布局钩子未就绪（round-5/t5_1_multi/实例/round-1 缺失）");

  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas 不可见");
  // 从 round-5 中心按住，拖到右下 (+180,+120)，松手
  await page.mouse.move(box.x + before.r5.sx, box.y + before.r5.sy);
  await page.mouse.down();
  await page.mouse.move(box.x + before.r5.sx + 180, box.y + before.r5.sy + 120, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(700); // 松手后 store 落盘 + 动画稳定

  let after: Record<string, { sx: number; sy: number }> | null = null;
  for (let i = 0; i < 20; i++) {
    const a = await readPos("round-5");
    const b = await readPos("round-1");
    const c = await readPos("round-5/batch/t5_1_multi");
    const d = await readPos("ags_3@t5_1_multi");
    if (a && b && c && d) {
      after = { r5: a, r1: b, task: c, inst: d };
      break;
    }
    await page.waitForTimeout(150);
  }
  if (!after) throw new Error("拖动后布局钩子缺失");

  const dx = after.r5.sx - before.r5.sx;
  const dy = after.r5.sy - before.r5.sy;
  expect(Math.hypot(dx, dy)).toBeGreaterThan(20); // 确实发生了拖动
  // 整棵子树刚性跟随：task 与实例相对 round-5 的偏移拖动前后不变（<4px 容差）
  const relBefore = [
    [before.task.sx - before.r5.sx, before.task.sy - before.r5.sy],
    [before.inst.sx - before.r5.sx, before.inst.sy - before.r5.sy],
  ];
  const relAfter = [
    [after.task.sx - after.r5.sx, after.task.sy - after.r5.sy],
    [after.inst.sx - after.r5.sx, after.inst.sy - after.r5.sy],
  ];
  for (let i = 0; i < 2; i++) {
    expect(Math.abs(relAfter[i][0] - relBefore[i][0])).toBeLessThan(4);
    expect(Math.abs(relAfter[i][1] - relBefore[i][1])).toBeLessThan(4);
  }
  // round-1（非 round-5 子树）不动
  expect(Math.abs(after.r1.sx - before.r1.sx)).toBeLessThan(4);
  expect(Math.abs(after.r1.sy - before.r1.sy)).toBeLessThan(4);
});
