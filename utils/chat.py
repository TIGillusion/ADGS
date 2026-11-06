"""ADGS v3 chat.py — OpenAI SDK 封装（答题卡模式）

CHAT：工具调用封装，核心是 call_tools()
  - 答题卡模式：tool_choice="required" 强制模型调用工具，参数天然是结构
  - 防抖链：required → 出错降级 auto → JSON 解析失败跳过 → 无效重试(2次)
  - vLLM 坑：多 system 消息 400（调用方保证单 system）；显式 tool_choice="auto" 某些版本 400

LLMAgent：旧封装保留兼容（auto 模式多轮对话）
"""
import json
import time
from core.provider_compat import create_with_compat
from core.contracts import validate_arguments
from typing import Callable, Dict, List, Optional

from openai import OpenAI
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall


def _clean_args(raw: str) -> Optional[dict]:
    """解析工具参数 JSON，带 ast.literal_eval 兜底"""
    from core.contracts import utf8_safe_json
    raw = raw.strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        # json.loads 不合并 \ud83c\udf0a 形式的代理对；模型输出里的 emoji
        # 若原样入库/落盘会在 utf-8 编码时炸掉整条链路。
        return utf8_safe_json(value) if isinstance(value, dict) else None
    except (ValueError, RecursionError):
        pass
    try:
        import ast
        d = ast.literal_eval(raw)
        return utf8_safe_json(d) if isinstance(d, dict) else None
    except Exception:
        return None


# ----------------------------------------------------------
# 全局调试级别（0-3），main.py 通过 set_debug_level 修改
#   0: 关闭     1: 工具调用概要   2: +往返耗时   3: 完整模型回复
# ----------------------------------------------------------
_DEBUG_LEVEL = 0


def set_debug_level(level: int):
    global _DEBUG_LEVEL
    _DEBUG_LEVEL = max(0, min(3, level))


def get_debug_level() -> int:
    return _DEBUG_LEVEL


def _dbg(level: int, tag: str, msg: str):
    """按级别打印调试日志"""
    if _DEBUG_LEVEL >= level:
        print(f"[dbg{level}|{tag}] {msg}", flush=True)


class CHAT:
    """答题卡模式 LLM 封装

    配置以不可变元组 _cfg=(client, model, extra_body) 持有：调用时整体快照读取，
    reconfigure() 一次性换元组（GIL 原子）——并发调用要么全旧要么全新，
    不会出现"新模型 + 旧 thinking 参数"的混搭（2026-09-27 web 模型热切换）。
    """

    def __init__(self, url: str, model: str, key: str, timeout: int = 600,
                 extra_body: dict = None):
        self._timeout = timeout
        self._extra_override = extra_body
        self._cfg = self._build(url, model, key)

    @staticmethod
    def _default_extra_body(model: str) -> dict:
        # 默认关闭 thinking（不同后端参数不同）：
        #   - Qwen3 系 vLLM：chat_template_kwargs.enable_thinking=False
        #   - DeepSeek 官方 API：thinking={"type":"disabled"}
        # 否则 thinking 模式会吞输出（content=None）或拒绝 tool_choice（400）
        if "deepseek" in (model or "").lower():
            return {"thinking": {"type": "disabled"}}
        if "qwen" in (model or "").lower():
            return {"chat_template_kwargs": {"enable_thinking": False}}
        return {}

    def _build(self, url: str, model: str, key: str) -> tuple:
        # One owner for retries; the SDK's retries must not multiply ours.
        client = OpenAI(base_url=url, api_key=key, timeout=self._timeout, max_retries=0)
        extra = (self._extra_override if self._extra_override is not None
                 else self._default_extra_body(model))
        return (client, model, extra)

    @property
    def model(self) -> str:
        """当前模型名（只读；热切换后即新值）"""
        return self._cfg[1]

    def reconfigure(self, url: str = None, model: str = None, key: str = None):
        """运行时热更新端点/模型/key（web 模型配置入口，2026-09-27）

        任一参数缺省 = 保持当前值；model 变化时按新模型族重算默认
        thinking 参数（deepseek 与 qwen 系参数不同，除非构造时显式传过
        extra_body）。对下一次调用生效，不中断进行中的调用。
        """
        client, cur_model, _ = self._cfg
        new_model = (model or cur_model or "").strip()
        new_url = (url or str(client.base_url)).rstrip("/")
        new_key = key or client.api_key
        self._cfg = self._build(new_url, new_model, new_key)

    # ---------------- 工具调用（答题卡） ----------------

    def call_tools(self, messages: List[dict], tools: List[dict],
                   tool_choice: str = "required",
                   max_retries: int = 2, timeout: float = None,
                   deadline: float = None) -> dict:
        """强制工具调用，返回 {"content", "tool_calls":[{id,name,arguments,raw}]}

        仅服务端明确不支持 tool_choice 时降级；协议修复和网络重试共享截止时间。
        无效工具调用最多修复 max_retries 次。
        """
        cap = self._timeout if timeout is None else min(self._timeout, max(0.0, float(timeout)))
        local_deadline = time.monotonic() + cap
        deadline = local_deadline if deadline is None else min(local_deadline, deadline)
        client, model, extra_body = self._cfg
        choice = tool_choice
        schemas = {t["function"]["name"]: t["function"].get("parameters", {}) for t in tools}
        last_content, last_error = "", ""
        errors = []
        request_messages = list(messages)
        for attempt in range(max(1, max_retries + 1)):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"content": last_content, "tool_calls": [], "error": "LLM call failed: total deadline exceeded",
                        "error_kind": "timeout", "attempts": attempt}
            t0 = time.monotonic()
            _dbg(1, "llm", f"request start model={model} attempt={attempt + 1}")
            try:
                kwargs = {"model": model, "messages": request_messages, "tools": tools,
                          "extra_body": extra_body, "timeout": remaining,
                          "parallel_tool_calls": False}
                if getattr(self, "_parallel_supported", True) is False:
                    kwargs.pop("parallel_tool_calls", None)
                if choice:
                    kwargs["tool_choice"] = choice
                resp = create_with_compat(client.chat.completions.create, **kwargs)
            except Exception as exc:
                last_error = str(exc)
                status = getattr(exc, "status_code", None)
                text = last_error.lower()
                choice_error = (status in (400, 422) and "tool_choice" in text
                                and any(x in text for x in ("not support", "unsupported", "invalid", "not allowed")))
                # Some providers reject the single-call hint; remove only that hint.
                parallel_error = status in (400, 422) and "parallel_tool_calls" in text
                retryable = (status in (408, 409, 429) or (isinstance(status, int) and status >= 500)
                             or any(x in type(exc).__name__.lower() for x in ("timeout", "connection")))
                if choice_error and choice:
                    choice = "auto" if choice == "required" else None
                elif parallel_error:
                    # Persist capability for this client without retrying unrelated 400s.
                    self._parallel_supported = False
                elif not retryable:
                    return {"content": "", "tool_calls": [], "error": f"LLM call failed: {exc}",
                            "error_kind": "provider_error", "http_status": status, "attempts": attempt + 1}
                else:
                    if attempt < max_retries:
                        time.sleep(min(2 ** attempt, max(0, deadline - time.monotonic())))
                if attempt == max_retries:
                    return {"content": "", "tool_calls": [], "error": f"LLM call failed: {exc}",
                            "error_kind": ("provider_error" if status else
                                           "timeout" if "timeout" in type(exc).__name__.lower() else "network_error"),
                            "http_status": status,
                            "attempts": attempt + 1}
                continue
            finished_at = time.monotonic()
            if finished_at >= deadline:
                return {"content": "", "tool_calls": [], "error": "LLM call failed: total deadline exceeded",
                        "error_kind": "timeout", "attempts": attempt + 1}
            _dbg(1, "llm", f"request end model={model} elapsed={finished_at - t0:.1f}s")
            if not resp.choices:
                errors = ["provider returned no choices"]
                last_content = ""
            else:
                msg = resp.choices[0].message
                from core.contracts import utf8_safe
                last_content = utf8_safe(msg.content or "")
                parsed, errors = [], []
                for call in msg.tool_calls or []:
                    if not isinstance(getattr(call, "id", None), str) or not call.id.strip():
                        errors.append("tool call id must be a nonempty string")
                        continue
                    name = call.function.name
                    arguments = _clean_args(call.function.arguments or "{}")
                    if name not in schemas:
                        errors.append(f"unknown tool {name}")
                        continue
                    error = ("arguments must be a JSON object" if arguments is None
                             else validate_arguments(arguments, schemas[name]))
                    if error:
                        errors.append(f"{name}: {error}")
                        continue
                    parsed.append({"id": call.id, "name": name, "arguments": arguments,
                                   "raw": call.function.arguments or ""})
                if len(parsed) > 1:
                    errors.append("Exactly one tool call is required per decision")
                if len(parsed) == 1 and not errors:
                    return {"content": last_content, "tool_calls": parsed, "error": "",
                            "attempts": attempt + 1}
                if not msg.tool_calls and choice != "required":
                    return {"content": last_content, "tool_calls": [], "error": "",
                            "attempts": attempt + 1}
                errors = errors or ["required tool call was not returned"]
            # Repair the protocol only; never request executing the actual task again.
            request_messages = request_messages + [{"role": "assistant", "content": last_content},
                {"role": "user", "content": "[协议修复] 仅补交合法工具参数，不要重新执行任务。" + "; ".join(errors)}]
        return {"content": last_content, "tool_calls": [],
                "error": "Protocol error: " + "; ".join(errors), "error_kind": "protocol_error",
                "attempts": max(1, max_retries + 1)}

    # ---------------- 纯文本 ----------------

    def get_full_response(self, messages: List[dict],
                          max_tokens: int = None, timeout: float = None,
                          deadline: float = None, *, reject_tool_calls: bool = False) -> str:
        """普通对话响应（非工具）"""
        client, model, extra_body = self._cfg  # 调用期快照
        t0 = time.time()
        try:
            started_at = time.monotonic()
            cap = self._timeout if timeout is None else min(self._timeout, max(0.0, float(timeout)))
            if deadline is not None:
                cap = min(cap, max(0.0, deadline - started_at))
            if cap <= 0:
                return "__LLM_ERROR__: total deadline exceeded"
            effective_deadline = started_at + cap
            kwargs = {"model": model, "messages": messages,
                      "extra_body": extra_body, "timeout": cap}
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            resp = create_with_compat(client.chat.completions.create, **kwargs)
            if time.monotonic() >= effective_deadline:
                return "__LLM_ERROR__: total deadline exceeded"
            from core.contracts import utf8_safe
            message = resp.choices[0].message
            if reject_tool_calls and (getattr(message, "tool_calls", None) or getattr(message, "function_call", None)):
                return "__LLM_ERROR__: unexpected tool call"
            content = utf8_safe(message.content or "")
            _dbg(1, "llm", f"model={model} 耗时={time.time()-t0:.1f}s (纯文本)")
            _dbg(3, "llm", f"[content] {content[:2000]}")
            return content
        except Exception as e:
            _dbg(1, "llm", f"调用失败: {e}")
            return f"__LLM_ERROR__: {e}"


# ================================================
# 旧 LLMAgent 封装（auto 模式多轮对话，兼容保留）
# ================================================

class LLMAgent:
    """通用 Agent：register_tool + run 多轮对话（auto 模式）"""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: str = "gpt-4o", system_prompt: str = "You are a helpful assistant."):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.system_prompt = system_prompt
        self.messages: List[dict] = [{"role": "system", "content": system_prompt}]
        self.tools_schema: List[dict] = []
        self.tools_map: Dict[str, Callable] = {}

    def register_tool(self, func: Callable, description: str, params_schema: Dict[str, object]):
        self.tools_schema.append({
            "type": "function",
            "function": {"name": func.__name__, "description": description,
                         "parameters": params_schema},
        })
        self.tools_map[func.__name__] = func
        print(f"[System] Tool registered: {func.__name__}")

    def _execute_tool_call(self, tool_call: ChatCompletionMessageToolCall) -> str:
        name = tool_call.function.name
        fn = self.tools_map.get(name)
        if not fn:
            return f"错误：未知工具 {name}"
        try:
            args = json.loads(tool_call.function.arguments or "{}")
            return str(fn(**args))
        except Exception as e:
            return f"工具执行错误: {e}"

    def run(self, user_input: str, max_turns: int = 5) -> str:
        self.messages.append({"role": "user", "content": user_input})
        for _ in range(max_turns):
            kwargs = {"model": self.model, "messages": self.messages}
            if self.tools_schema:
                kwargs["tools"] = self.tools_schema
                kwargs["tool_choice"] = "auto"
            resp = self.client.chat.completions.create(**kwargs)
            msg: ChatCompletionMessage = resp.choices[0].message
            if not msg.tool_calls:
                self.messages.append({"role": "assistant", "content": msg.content or ""})
                return msg.content or ""
            self.messages.append(msg)
            for tc in msg.tool_calls:
                result = self._execute_tool_call(tc)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        return "达到最大轮次"


def get_current_weather(location: str, unit: str = "celsius") -> str:
    return f"{location}: 25°C"


if __name__ == "__main__":
    agent = LLMAgent(model="gpt-4o")
    agent.register_tool(
        get_current_weather,
        "获取指定城市的当前天气",
        {"type": "object",
         "properties": {"location": {"type": "string", "description": "城市名称"}},
         "required": ["location"]},
    )
    print(agent.run("北京天气怎么样？"))
