"""Small, dependency-free validation for model tool contracts."""
import math
import re

_SURROGATE_PAIR = re.compile("[\ud800-\udbff][\udc00-\udfff]")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def utf8_safe(text):
    """Return text strictly UTF-8 encodable without raising.

    json.loads keeps escaped surrogate pairs (astral characters such as emoji
    from model output or the Hermes CLI) as two surrogate code units, and
    Python's UTF-8 encoder rejects every surrogate — lone or paired. A single
    dirty character in a work_log otherwise kills sqlite writes, session
    saves and event inserts (2026-10-21 continue_task 中断实测). Re-combine
    valid pairs into real characters and replace leftovers with U+FFFD.
    """
    if not isinstance(text, str):
        return text
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        pass
    combined = _SURROGATE_PAIR.sub(
        lambda m: chr(0x10000 + ((ord(m.group()[0]) - 0xD800) << 10)
                     + ord(m.group()[1]) - 0xDC00),
        text)
    return _LONE_SURROGATE.sub("\ufffd", combined)


def utf8_safe_json(value):
    """Sanitize every string inside parsed JSON before it is persisted."""
    if isinstance(value, str):
        return utf8_safe(value)
    if isinstance(value, dict):
        return {utf8_safe(k): utf8_safe_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [utf8_safe_json(v) for v in value]
    return value


def validate_arguments(value, schema, path="arguments"):
    kind = schema.get("type")
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }
    if kind and not matches.get(kind, True):
        return f"{path}: expected {kind}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: unsupported value"
    if kind in ("integer", "number"):
        # Python integers are exact and finite; converting an arbitrary JSON
        # integer to double merely to validate it can raise OverflowError.
        if isinstance(value, float) and not math.isfinite(value):
            return f"{path}: must be finite"
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            return f"{path}: out of range"
    if kind == "string" and (len(value) < schema.get("minLength", 0)
                              or len(value) > schema.get("maxLength", math.inf)):
        return f"{path}: invalid length"
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return f"{path}: unknown fields"
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key}: required"
        for key, subschema in properties.items():
            if key in value:
                error = validate_arguments(value[key], subschema, f"{path}.{key}")
                if error:
                    return error
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            return f"{path}: empty array"
        if len(value) > schema.get("maxItems", math.inf):
            return f"{path}: too many items"
        for index, item in enumerate(value):
            error = validate_arguments(item, schema.get("items", {}), f"{path}[{index}]")
            if error:
                return error
    return None


def task_section(task, title):
    match = re.search(r"^##\s+" + re.escape(title) + r"\s*\n(.*?)(?=^##\s|\Z)",
                      task or "", re.M | re.S)
    return match.group(1).strip() if match else ""


def evidence_excerpt(text, budget):
    """Preserve the conclusion at the end, alongside the beginning of an evidence log."""
    text = str(text or "")
    if len(text) <= budget:
        return text
    head = max(0, budget // 4)
    tail = max(0, budget - head - 60)
    return text[:head] + "\n[中段已省略；完整证据保存在执行记录中]\n" + text[-tail:]
