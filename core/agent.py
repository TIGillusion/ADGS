"""ADGS v3 agent.py — 节点智能体（Leader Agent）

每个节点的"领导者"。职责：
  1. 接收任务 markdown（含总体任务/当前任务/验收标准/权限）
  2. 理解任务：不理解 → ask_upper 向上层提问
  3. 决策：直接执行（plan_hermes）或拆分（split_tasks）
  4. hermes 完成后验收打分（evaluate）→ 更新成功率 + 描述分化

全部决策走"答题卡"模式（tool_choice=required 强制工具调用，参数即结构）
"""
import json
import time
from typing import List, Optional

from config.config import config
from utils.chat import CHAT
from core.contracts import validate_arguments, evidence_excerpt

# ================================================
# 答题卡工具 Schema（JSON Schema，与模型无关）
# ================================================

TOOL_ASK_UPPER = {
    "type": "function",
    "function": {
        "name": "ask_upper",
        "description": "任务信息不足或有歧义时，向上层节点提问确认任务细节。必须标注自己的身份和收到的任务要点，便于上层确认。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "具体问题"},
                "need_info": {"type": "array", "items": {"type": "string"},
                              "description": "需要上层补充的信息点"},
            },
            "required": ["question", "need_info"],
        },
    },
}

TOOL_PLAN_HERMES = {
    "type": "function",
    "function": {
        "name": "plan_hermes",
        "description": "理解任务后，为节点自己的 hermes 编写完整执行指令（markdown 任务书）：包含总体任务、当前任务、验收标准、权限边界、输出要求、技能固化判断。",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "当前任务的总体目标"},
                "steps": {"type": "array", "items": {"type": "object",
                          "properties": {"title": {"type": "string"},
                                         "detail": {"type": "string"}},
                          "required": ["title", "detail"]},
                          "description": "给 hermes 的分步执行步骤"},
                "acceptance": {"type": "array", "items": {"type": "string"},
                               "description": "验收标准（客观可检查）"},
                "permissions": {"type": "string",
                                "description": "权限告知：哪些文件/目录可改、哪些不能改"},
                "deliverables": {"type": "array", "items": {"type": "string"},
                                 "description": "必须交付的产物"},
                "skill_note": {"type": "string",
                               "description": "技能固化提示：若任务属于复杂工程(5+工具调用)、调试排错、或可复用模式，提示 hermes 按系统 SKILLS_GUIDANCE 用 skill_manage 创建 skill；简单一次性任务则提示不创建"},
            },
            "required": ["goal", "steps", "acceptance", "permissions", "deliverables", "skill_note"],
        },
    },
}

TOOL_SPLIT_TASKS = {
    "type": "function",
    "function": {
        "name": "split_tasks",
        "description": "任务过于复杂时拆分。只拆分当前这一步，子任务可并行；子任务之间可用 depends_on 声明依赖（0 基索引）。**优先并行**：能并行的子任务尽量一次拆出并行执行（共享同一序号、同时开工）；仅存在先后依赖时才串行。适合拆分的信号：≥3 个独立模块/文件/函数、需并行探索多个方向、单次 hermes 会话预计 >15 个工具调用、验收标准 ≥5 条且独立可并行。单一连贯修改（改 1-2 个文件/修一个 bug）不要拆分。",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "为什么要拆分"},
                "overall_goal": {"type": "string",
                                 "description": "并发委派的【总体任务】：这批拆分出去的子任务共同要完成的整体目标（每个子任务点开时都会展示它，用户据此知道这波并发在做什么）"},
                "subtasks": {"type": "array", "items": {"type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "goal": {"type": "string"},
                        "background": {"type": "string"},
                        "permissions": {"type": "string", "description": "继承原任务权限，仅可进一步收窄"},
                        "write_paths": {"type": "array", "items": {"type": "string"}, "description": "实际修改的工作区文件；只读任务为空"},
                        "shared_resources": {"type": "array", "items": {"type": "string"},
                                             "description": "涉及的外部互斥资源（设备/端口/远端服务，如\"米家:测试插座\"）。声明相同资源的子任务会被调度串行化，避免并行操作同一外部资源互相打架；互不相干的子任务不要声明"},
                        "requirements": {"type": "array", "items": {"type": "string"}},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "deliverables": {"type": "array", "items": {"type": "string"}},
                        "depends_on": {"type": "array", "items": {"type": "integer"},
                                       "description": "依赖的兄弟子任务索引"},
                    },
                    "required": ["name", "goal", "requirements", "acceptance_criteria", "deliverables"]},
                    "minItems": 1},
            },
            "required": ["reason", "overall_goal", "subtasks"],
        },
    },
}

ALL_TOOLS = [TOOL_ASK_UPPER, TOOL_PLAN_HERMES, TOOL_SPLIT_TASKS]
_TOOL_BY_NAME = {t["function"]["name"]: t for t in ALL_TOOLS}


def _clean_describe(text: str) -> str:
    """净化 LLM 生成的描述文本：剥离代码块标记 / 引号包裹 / 前缀废话

    LLM 偶发不遵守"纯描述文本"要求，返回 ```json {...}``` 或带前缀的结果。
    净化后若仍是明显的结构化数据（{ / [ 开头）或过短，返回 ""（调用方保留旧描述）。
    """
    t = (text or "").strip()
    if t.startswith("```"):
        # 代码块包裹：先剥离 ``` 再处理（注意不能提前 strip 掉反引号）
        end = t.find("```", 3)
        if end != -1:
            t = t[3:end].strip()
            lines = t.splitlines()
            if lines and lines[0].strip().lower() in (
                    "json", "markdown", "md", "text", "plain"):
                t = "\n".join(lines[1:]).strip()
        else:
            t = t.strip("`").strip()
    else:
        # 非代码块：剥离包裹引号
        t = t.strip('"\'`').strip()
    # 循环剥离常见前缀（"以下是节点描述：xxx" 需要剥离两层）
    while True:
        hit = False
        for pre in ("以下是", "节点描述：", "描述：", "生成结果：", "结果：", "输出："):
            if t.startswith(pre):
                t = t[len(pre):].strip()
                hit = True
                break
        if not hit:
            break
    # 明显结构化输出（JSON/数组）不是描述文本
    if t.startswith("{") or t.startswith("["):
        return ""
    return t


# ================================================
# AGENT
# ================================================

class AGENT:
    """节点智能体：任务决策（答题卡模式）"""

    def __init__(self, url: str, model: str, key: str,
                 node_id: str = "node", describe: str = "",
                 system_prompt: str = None):
        self.chat = CHAT(url=url, model=model, key=key)
        self.node_id = node_id
        self.describe = describe
        self.system_prompt = system_prompt or self._default_prompt()
        # brain 对话历史：单次任务生命周期内累积（决策/日志/修正/验收），
        # 超过 brain 上下文阈值时压缩。跨任务不保留（设计：接手任务上下文全新）。
        self.history: List[dict] = []

    def _default_prompt(self) -> str:
        return f"""你是 ADGS 达尔文进化智能体系统中的一个节点智能体（节点 {self.node_id}），
是节点内 hermes 执行者的领导者。

你的职责：
1. 接收上层节点（或 CEO）下发的任务 markdown，先确保完全理解。
2. 不理解或信息不足 → 调用 ask_upper 向上层提问确认。
3. 理解后决策：任务可一次完成 → plan_hermes 给 hermes 写执行指令；
   任务过于复杂 → split_tasks 拆分给下层节点并行执行。
4. hermes 完成后，对照验收标准验收并打分，汇报上层。

节点自我描述：{self.describe or "（初始节点，待分化）"}

【决策规则】
- 只规划【下一步】，不是全部。
- 任务书必须详细无歧义：总体任务、当前任务、验收标准、权限告知、交付物。
- 每次决策调用且仅调用一个工具。
- 决策与验收结论必须用对应工具提交（plan_hermes / split_tasks / ask_upper / accept_report）。格式不合法时先修正结论；无法判断不等于成果失败，不能据此要求重做已完成成果。

【拆分标准（自主判断，满足任一倾向拆分为宜）】
- 任务包含 ≥3 个彼此独立的模块/文件/函数需要分别实现或修改
- 任务需要并行探索多个方向（如先调查再实现、多个候选方案并行验证）
- 单次 hermes 会话预计超过 15 个工具调用或需要多轮长上下文才能完成
- 验收标准 ≥5 条且彼此独立、可并行验证
- 任务明确要求"拆成子任务"或"分别处理 X/Y/Z"

【并行优先】
- 能并行的子任务尽量一次拆出并行执行（共享同一序号、同时开工），不要拆一个等一个；
  只有存在明确先后依赖（后一个需要前一个的产物）才用 depends_on 串行。

倾向自己执行（plan_hermes）的标准：
- 任务是单一连贯的修改（改 1-2 个文件、修一个 bug、写一个函数）
- 子任务之间有强依赖、必须串行且上下文共享
- 拆分反而增加协调成本（子任务很小、拆分收益低）

困难且有清晰边界的工作，默认先 split_tasks 分工，不必等到自己失败才委派。
串行阶段也可使用 depends_on 拆分交接；只有必须共享同一执行上下文的连贯小任务才自己完成。
不要以“自己也能做”为理由包办大任务；单一连贯小任务仍用 plan_hermes，不为拆而拆。"""

    # ---------------- brain 上下文（累积 + 压缩） ----------------

    def reset_history(self):
        """清空 brain 对话历史（每次任务开始前调用）"""
        self.history = []

    def append_history(self, role: str, content: str):
        """累积一条 brain 历史（role ∈ user/assistant，纯文本）"""
        if content:
            self.history.append({"role": role, "content": content})

    def compress_history_if_needed(self) -> bool:
        """brain 上下文超阈值则压缩（用 brain 真实上下文窗口）

        区别于 CEO：CEO 用 config.ctx_window(1M)，brain 用 config.brain_ctx_window
        （qwen 27b 级模型实测 ctx ≈ 242688）。worker 不需要——hermes 自带压缩。
        """
        from core.compress import should_compress, compress
        if should_compress(self.history, ctx_window=config.brain_ctx_window):
            before = len(self.history)
            self.history = compress(self.history, ctx_window=config.brain_ctx_window)
            print(f"[agent:{self.node_id}] brain 上下文压缩：{before} 条 → "
                  f"{len(self.history)} 条（阈值 {config.brain_ctx_window}）", flush=True)
            return True
        return False

    def history_brief(self, max_chars: int = 1500) -> str:
        """生成累积历史的精简摘要（注入验收等后续决策）"""
        parts = []
        for m in self.history:
            role = "任务" if m.get("role") == "user" else "决策"
            c = (m.get("content") or "").strip()
            if not c:
                continue
            parts.append(f"[{role}] {c[:250]}")
        brief = "\n".join(parts)
        return brief[-max_chars:] if brief else ""

    # ---------------- 单工具调用（答题卡） ----------------

    def _call(self, tool_name: str, context: str,
              temperature: float = None) -> Optional[dict]:
        """强制调用指定工具，返回解析后的 arguments dict"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": context},
        ]
        tools = [_TOOL_BY_NAME[tool_name]]
        res = self.chat.call_tools(messages, tools,
                                   tool_choice="required")
        if res.get("error"):
            print(f"[agent:{self.node_id}] {tool_name} 调用失败: {res['error']}")
            return None
        if not res.get("tool_calls"):
            print(f"[agent:{self.node_id}] {tool_name} 未返回工具调用")
            return None
        return res["tool_calls"][0]["arguments"]

    def _render_task_md(self, data: dict) -> str:
        """把 plan_hermes 的解析结果渲染成 markdown 任务书（给 hermes 用）"""
        steps = "\n".join(
            f"{i+1}. **{s.get('title','')}** {s.get('detail','')}"
            for i, s in enumerate(data.get("steps", [])))
        acc = "\n".join(f"- {a}" for a in data.get("acceptance", []))
        skill_note = data.get("skill_note") or (
            "根据你的系统 SKILLS_GUIDANCE 指导：复杂任务(5+工具调用)、修复棘手错误、"
            "或发现非平凡工作流时，直接使用 skill_manage 创建 skill 固化（无需询问确认，"
            "本环境为无人值守自动运行）；简单一次性任务则不要创建。")
        return f"""# 任务书

## 总体目标
{data.get("goal","")}

## 执行步骤
{steps}

## 验收标准
{acc}

## 权限告知
{data.get("permissions","")}

## 交付物
{", ".join(data.get("deliverables", []))}

## 技能固化
{skill_note}
"""

    # ---------------- 决策入口 ----------------

    def decide(self, task_md: str, context: str = "", timeout=None) -> dict:
        """核心决策：接收任务，决定下一步动作（ask_upper / plan_hermes / split_tasks）

        传 ask + plan + split 三个工具，模型自主选择：
          - 信息不足 → ask_upper
          - 可执行 → plan_hermes
          - 过于复杂 → split_tasks（拆给下层并行）
        """
        ctx = f"""你刚收到一个任务。任务书如下：

{task_md}

{context}

请决策下一步：
- 若任务信息不足/有歧义 → ask_upper 提问
- 若可一次完成 → plan_hermes（为你的 hermes 编写详细指令）
- 若任务过于复杂、需拆分成多个并行子任务 → split_tasks
注意：任务书通常已足够详细，只有在关键信息缺失时才 ask_upper。"""
        tools = [_TOOL_BY_NAME["ask_upper"], _TOOL_BY_NAME["plan_hermes"],
                 _TOOL_BY_NAME["split_tasks"]]
        res = self.chat.call_tools(
            [{"role": "system", "content": self.system_prompt},
             {"role": "user", "content": ctx}],
            tools, tool_choice="required", **({"timeout": timeout} if timeout is not None else {}))
        if res.get("error"):
            print(f"[agent:{self.node_id}] 决策失败: {res['error']}")
            return {"action": "error", "detail": res["error"], "outcome": "infra_failure", "unjudged": True}
        tcs = res.get("tool_calls", [])
        if not tcs:
            return {"action": "error", "detail": "决策无工具调用"}
        name = tcs[0].get("name")
        args = tcs[0].get("arguments") or {}
        tool = _TOOL_BY_NAME.get(name)
        error = validate_arguments(args, tool["function"]["parameters"]) if tool else "未知工具"
        if error:
            return {"action": "error", "outcome": "protocol_error", "unjudged": True,
                    "detail": f"决策参数格式错误: {error}"}
        if name == "ask_upper":
            return {"action": "ask_upper", **args}
        if name == "plan_hermes":
            return {"action": "plan_hermes", **args,
                    "task_md": self._render_task_md(args)}
        if name == "split_tasks":
            return {"action": "split_tasks", **args}
        return {"action": "error", "detail": f"未知工具 {name}"}

    # ---------------- 验收与打分 ----------------

    @staticmethod
    def _acceptance_chunks(task_md: str, target: int = 12000) -> list[str]:
        """Split an oversized contract at line boundaries for independent review.

        The complete contract remains durable in the execution record.  Each review
        call receives one exact chunk plus its own evidence, so a long accumulated
        continuation cannot make the acceptance request impossible to send.
        """
        text = str(task_md or "")
        if not text:
            return [""]
        chunks, current, size = [], [], 0
        for line in text.splitlines(keepends=True):
            # A pathological single line must still make progress.
            if len(line) > target:
                if current:
                    chunks.append("".join(current))
                    current, size = [], 0
                for offset in range(0, len(line), target):
                    chunks.append(line[offset:offset + target])
                continue
            if current and size + len(line) > target:
                chunks.append("".join(current))
                current, size = [], 0
            current.append(line)
            size += len(line)
        if current:
            chunks.append("".join(current))
        return chunks or [text]

    def _accept_report(self, task_text: str, work_log: str, context_brief: str,
                       *, old_score: float, timeout=None, chunk_label: str = "") -> dict:
        """Run one bounded accept_report call and validate its protocol."""
        tool = {
            "type": "function",
            "function": {
                "name": "accept_report",
                "description": "验收判定：逐条对照验收标准给出 pass/fail 和分数。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "passed": {"type": "boolean"},
                        "score": {"type": "number", "minimum": -10, "maximum": 10},
                        "passed_items": {"type": "array", "items": {"type": "string"}},
                        "failed_items": {"type": "array", "items": {"type": "object",
                            "properties": {"criterion": {"type": "string"}, "why": {"type": "string"}},
                            "required": ["criterion", "why"]}},
                    },
                    "required": ["passed", "score", "passed_items", "failed_items"],
                },
            },
        }
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""请验收 hermes 的成果，并调用 accept_report。
这是完整合同的 {chunk_label or '当前'}。只判断本段明确出现的验收标准；不要因为其他段未包含在本次请求而判失败，也不要把缺少证据猜成通过。

【本段任务合同】
{task_text}

【任务上下文摘要】
{evidence_excerpt(context_brief or '（无）', 1200)}

【本段执行证据】
{evidence_excerpt(work_log, 7000)}
逐条给出本段通过项和失败项；本段没有足够证据时标为失败项并说明缺口。"""}
        ]
        deadline = time.monotonic() + timeout if timeout is not None else None
        for repair in range(2):
            remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
            res = self.chat.call_tools(messages, [tool], tool_choice="auto",
                                       **({"timeout": remaining} if remaining is not None else {}))
            if res.get("error"):
                return {"passed": False, "score": old_score, "unjudged": True,
                        "outcome": "evaluation_unavailable", "detail": res["error"]}
            tcs = res.get("tool_calls") or []
            args = tcs[0].get("arguments") if len(tcs) == 1 else None
            error = validate_arguments(args, tool["function"]["parameters"])
            if not error and tcs[0].get("name") != "accept_report":
                error = "必须提交 accept_report"
            # passed=True + failed_items = 总体通过、附带非阻断瑕疵（合法且常见，
            # 例如"完成但报告未逐字附原始 JSON"）——2026-10-24 T13 实测被旧规则
            # 误打回导致验收协议失败。真正非法的是反方向：
            if not error and not args["passed"] and not args["failed_items"]:
                error = "passed=False 但没有任何 failed_items；请逐条给出失败依据"
            if not error:
                raw_score = max(-10.0, min(10.0, float(args["score"])))
                return {"passed": bool(args["passed"]),
                        "score": max(0.0, old_score + raw_score * config.delta_coef),
                        "raw_score": raw_score, "outcome": "passed" if args["passed"] else "failed",
                        "passed_items": args["passed_items"], "failed_items": args["failed_items"],
                        "detail": f"通过: {args['passed_items']}; 失败: {args['failed_items']}"}
            messages.append({"role": "user", "content": f"验收格式需要修正：{error}。只重新提交本段结论，不要求重做成果。"})
        return {"passed": False, "score": old_score, "unjudged": True,
                "outcome": "protocol_error", "detail": f"验收格式无法解析: {error}"}

    def evaluate(self, task_md: str, work_log: str,
                 old_score: float = 0.5, context_brief: str = "", timeout=None) -> dict:
        """验收 hermes 的工作：答题卡模式（强制 accept_report 工具）

        打分规则（2026-08-20）：智能体根据 hermes 执行日志 + 自己判断验收，
        给出 -10~+10 的评分（+10=完美，0=一般，-10=完全失败），
        乘以 delta_coef（默认 0.2）累加到旧分：new = old + 打分 × coef，
        单轮波动 ±2，下限 0、无上限。
        context_brief：brain 累积历史摘要（含任务全过程的决策/日志/修正），
        让验收基于完整来龙去脉而非只看最后一次日志。
        """
        char_budget = max(4096, (getattr(config, "brain_ctx_window", 32768) - 4096) * 2)
        if len(task_md) + len(self.system_prompt) > char_budget:
            chunks = self._acceptance_chunks(task_md)
            results = []
            deadline = time.monotonic() + timeout if timeout is not None else None
            for index, chunk in enumerate(chunks, 1):
                remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
                result = self._accept_report(chunk, work_log, context_brief,
                    old_score=old_score, timeout=remaining,
                    chunk_label=f"第 {index}/{len(chunks)} 段")
                if result.get("unjudged"):
                    return {"passed": False, "score": old_score, "unjudged": True,
                            "outcome": result.get("outcome", "evaluation_unavailable"),
                            "detail": f"分块验收第 {index}/{len(chunks)} 段失败: {result.get('detail', '')}",
                            "chunks": results + [result]}
                results.append(result)
            failed = [item for result in results for item in result.get("failed_items", [])]
            passed_items = [item for result in results for item in result.get("passed_items", [])]
            raw_score = sum(float(result.get("raw_score", 0)) for result in results) / max(1, len(results))
            return {"passed": not failed and all(result.get("passed") for result in results),
                    "score": max(0.0, old_score + raw_score * config.delta_coef),
                    "raw_score": raw_score, "outcome": "passed" if not failed else "failed",
                    "passed_items": passed_items, "failed_items": failed,
                    "chunks": results,
                    "detail": f"分块验收 {len(results)} 段；通过 {len(passed_items)} 项；失败 {len(failed)} 项"}
        log_budget = max(1000, char_budget - len(task_md) - len(self.system_prompt) - 2500)
        tool = {
            "type": "function", "function": {"name": "accept_report", "description": "验收判定",
                "parameters": {"type": "object", "properties": {
                    "passed": {"type": "boolean"}, "score": {"type": "number"},
                    "passed_items": {"type": "array", "items": {"type": "string"}},
                    "failed_items": {"type": "array", "items": {"type": "object", "properties": {
                        "criterion": {"type": "string"}, "why": {"type": "string"}},
                        "required": ["criterion", "why"]}}},
                    "required": ["passed", "score", "passed_items", "failed_items"]}}}
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""请验收 hermes 的最终成果，并调用 accept_report。
格式错误只是无法判定，不代表成果失败。依据原始任务合同逐项核对，引用最终报告、测试和工具结果。
日志中的失败尝试不代表最终交付失败；缺少证据时具体说明缺项。

【原始任务与验收标准】
{task_md}

【任务上下文】
{evidence_excerpt(context_brief or "（无）", 1500)}

【执行证据】
{evidence_excerpt(work_log, log_budget)}
给出 -10~10 的本次任务评分，以及 passed_items / failed_items。"""}
        ]
        deadline = time.monotonic() + timeout if timeout is not None else None
        for repair in range(2):
            remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
            # Keep the legacy single-call path for contracts within the context budget.
            res = self.chat.call_tools(messages, [tool], tool_choice="auto",
                                       **({"timeout": remaining} if remaining is not None else {}))
            if res.get("error"):
                return {"passed": False, "score": old_score, "unjudged": True,
                        "outcome": "evaluation_unavailable", "detail": res["error"]}
            tcs = res.get("tool_calls") or []
            args = tcs[0].get("arguments") if len(tcs) == 1 else None
            error = validate_arguments(args, tool["function"]["parameters"])
            if not error and tcs[0].get("name") != "accept_report":
                error = "必须提交 accept_report"
            # passed=True + failed_items = 总体通过、附带非阻断瑕疵（合法且常见，
            # 例如"完成但报告未逐字附原始 JSON"）——2026-10-24 T13 实测被旧规则
            # 误打回导致验收协议失败。真正非法的是反方向：
            if not error and not args["passed"] and not args["failed_items"]:
                error = "passed=False 但没有任何 failed_items；请逐条给出失败依据"
            if not error:
                break
            messages.append({"role": "user", "content": f"验收格式需要修正：{error}。只重新提交结论，不要求重做成果。"})
        else:
            return {"passed": False, "score": old_score, "unjudged": True,
                    "outcome": "protocol_error", "detail": f"验收格式无法解析: {error}"}
        raw_score = max(-10.0, min(10.0, float(args["score"])))
        passed = args["passed"]
        score = max(0.0, old_score + raw_score * config.delta_coef)
        return {"passed": passed, "score": score, "raw_score": raw_score,
                "outcome": "passed" if passed else "failed",
                "passed_items": args["passed_items"], "failed_items": args["failed_items"],
                "detail": f"通过: {args['passed_items']}; 失败: {args['failed_items']}"}


    # ---------------- 描述生成（LLM 微调） ----------------

    def update_describe(self, old_describe: str, task_md: str,
                        work_log: str, skills: dict,
                        max_len: int = 500, timeout=60) -> str:
        """LLM 生成/微调节点描述（纯 LLM 结果，非代码拼接）

        规则：参考旧描述微调，合并本次任务经验与新技能，不超过 max_len 字。
        描述用于向量路由和生态位拥挤度计算 —— 必须真实反映节点当前能力。
        """
        skill_brief = ""
        if skills:
            skill_brief = "；".join(f"{k}({v[:60]})" for k, v in list(skills.items())[:5])
        ctx = f"""请生成节点 {self.node_id} 的能力描述。

【旧描述】（参考基础，可保留或微调）
{old_describe[:400]}

【本次完成任务摘要】
{task_md[:600]}

【hermes 工作日志要点】
{work_log[:800]}

【hermes 当前技能库】
{skill_brief or "（暂无自定义技能）"}

【输出要求】
- 生成一段简洁的节点能力描述（150~{max_len} 字），用于任务路由匹配
- 参考旧描述进行微调：保留已有能力，吸收本次新增经验
- 突出擅长领域、技术栈、可承担的任务类型
- 纯描述文本，不要任何格式标记"""
        try:
            text = self.chat.get_full_response(
                [{"role": "system",
                  "content": "你是 ADGS 节点智能体，负责维护本节点 hermes 的能力描述（生态位画像）。"},
                 {"role": "user", "content": ctx}],
                max_tokens=700, timeout=timeout)
            text = text.strip()
            if text.startswith("__LLM_ERROR__"):
                return old_describe
            # 净化：剥离代码块标记/引号包裹/前缀废话；结构化残留或过短 → 保留旧描述
            text = _clean_describe(text)
            if not text or len(text) < 10:
                return old_describe
            return text[:max_len]
        except Exception:
            return old_describe


if __name__ == "__main__":
    print("答题卡工具数:", len(ALL_TOOLS))
    print("工具名:", [t["function"]["name"] for t in ALL_TOOLS])
