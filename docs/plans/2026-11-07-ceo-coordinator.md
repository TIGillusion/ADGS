# 固定 CEO 协调者与 Web3 适配 Implementation Plan

**Goal:** CEO 仅进行少量只读项目了解、任务规划、委派与依据汇报验收，聊天和图谱准确展示 Hermes CEO 的生命周期。

**Architecture:** 版本控制中的 SOUL 和专用 skill 同步到 CEO profile，每次 Hermes 决策显式预载协调技能，原生工具入口保留白名单。持久化 chat.ceo_activity 结构事件与 API 快照共享状态，Web3 聊天、图谱根节点和详情面板消费该状态，原轮次和委派批次语义保持。

**Tech Stack:** Python、Hermes CLI、Flask、React、TypeScript、Vitest、pytest、Playwright。

1. 新增 `config/ceo/SOUL.md`、`skills/adgs-ceo/SKILL.md` 和 `core/ceo_policy.py`，测试只同步 CEO、原人设备份、技能预载及执行工具拒绝。
2. 修改 `config/prompts.py`、`core/hermes_ceo.py`、`core/terminal_runtime.py`：明确协调边界，每次决策加载专用技能，恢复原会话时再次给出当前政策。
3. 修改 `core/ceo.py`、`web_app.py`：持久化与发出 CEO 活动，覆盖决策、只读了解、等待下属、审阅、提问、完成和中断。
4. 修改 Web3 状态、图谱投影、聊天页和抽屉：及时绑定 CEO execution、展示正确阶段；不增加用户轮次、不产生 CEO Worker 实例，不把原始 Hermes 消息混入正式聊天。
5. 增加技能边界、会话隔离、恢复、聊天和图谱一致性的回归测试；构建前端并用独立服务和真实 CEO Hermes 验证。
6. 服务空闲时备份并加载改动，校验历史会话，保存 Git 回滚点和外部 bundle。
