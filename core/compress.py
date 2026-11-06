"""Budgeted context compression preserving complete tool exchanges."""
import json
from typing import List
from config.config import config


def _count_tokens(messages: List[dict]) -> int:
    """Estimate all fields, including tool names and arguments."""
    return sum((len(json.dumps(m, ensure_ascii=False, default=str)) + 1) // 2 + 8
               for m in messages)


def _window(ctx_window=None):
    return int(ctx_window or getattr(config, "ceo_ctx_window", config.ctx_window))


def should_compress(messages: List[dict], ctx_window: int = None) -> bool:
    return _count_tokens(messages) > _window(ctx_window) * config.compress_trigger


def _groups(messages):
    """Retain exchanges with exactly one response for every unique call ID."""
    groups = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "tool":
            i += 1
            continue
        calls = (m.get("tool_calls") or []) if m.get("role") == "assistant" else []
        if not calls:
            groups.append([m])
            i += 1
            continue
        j = i + 1
        responses = []
        while j < len(messages) and messages[j].get("role") == "tool":
            responses.append(messages[j])
            j += 1
        ids = [c.get("id") for c in calls if isinstance(c, dict)]
        # Unrelated orphan responses must not destroy a valid adjacent exchange.
        responses = [r for r in responses if r.get("tool_call_id") in ids]
        response_ids = [r.get("tool_call_id") for r in responses]
        valid = (len(ids) == len(calls) and all(isinstance(t, str) and t for t in ids)
                 and len(set(ids)) == len(ids)
                 and len(response_ids) == len(ids)
                 and all(isinstance(t, str) and t for t in response_ids)
                 and len(set(response_ids)) == len(ids)
                 and set(response_ids) == set(ids))
        if valid:
            groups.append([m, *responses])
        elif m.get("content"):
            groups.append([{"role": "assistant", "content": m["content"]}])
        i = j
    return groups


def sanitize_tool_messages(messages: List[dict]) -> List[dict]:
    return [message for group in _groups(messages) for message in group]


def _brief(messages: List[dict], max_chars=2400) -> str:
    """No extra model call; durable task facts are retained separately."""
    requests = [str(m.get("content") or "") for m in messages
                if m.get("role") == "user" and m.get("content")]
    results = [str(m.get("content") or "") for m in messages
               if m.get("role") in ("tool", "assistant") and m.get("content")]
    first_budget = max(0, max_chars // 2)
    first = requests[0][:first_budget] if requests else ""
    remaining = max(0, max_chars - len(first) - 80)
    recent = "\n".join((requests[-2:] if len(requests) > 1 else []) + results[-2:])
    return f"原始请求片段：{first}\n最近请求和结果片段：{recent[-remaining:] if remaining else ''}"


def compress(messages: List[dict], budget_ratio: float = None,
             ctx_window: int = None) -> List[dict]:
    """Preserve system and complete recent exchanges inside a hard budget."""
    if not messages:
        return []
    ratio = config.compress_budget if budget_ratio is None else budget_ratio
    budget = max(1, int(_window(ctx_window) * ratio))
    systems = [m for m in messages if m.get("role") == "system"]
    system_cost = _count_tokens(systems)
    if system_cost >= budget:
        raise ValueError("System context exceeds compression budget; task facts must be preserved")
    groups = _groups([m for m in messages if m.get("role") != "system"])
    if system_cost + _count_tokens([m for g in groups for m in g]) <= budget:
        return systems + [m for g in groups for m in g]
    reserve = min(1200, max(64, (budget - system_cost) // 4))
    tail_budget = budget - system_cost - reserve
    kept, used = [], 0
    for group in reversed(groups):
        cost = _count_tokens(group)
        if used + cost > tail_budget:
            break
        kept.insert(0, group)
        used += cost
    dropped = groups[:len(groups) - len(kept)]
    available = budget - system_cost - used
    summary = {"role": "user", "content": "[历史片段摘要；完整事实以任务账本为准]\n" +
               _brief([m for g in dropped for m in g], max(0, (available - 80) * 2))}
    while summary["content"] and _count_tokens([summary]) > available:
        summary["content"] = summary["content"][:-32]
    result = list(systems)
    if summary["content"] and _count_tokens([summary]) <= available:
        result.append(summary)
    result.extend(m for group in kept for m in group)
    return result
