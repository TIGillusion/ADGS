"""utils/context_probe.py — 启动时探测模型最大上下文

背景：/models 和 /model/info 端点都不返回真实上下文上限
（LiteLLM 的 max_input_tokens=null，DeepSeek 无 info 端点）。
唯一可靠的方法是实测：发送超长输入触发 API 报错，从报错信息里
正则提取 "maximum context length is N tokens"（DeepSeek/OpenAI 风格）。

实测一次代价较高（要发 1M~2M token 的大请求），所以结果缓存到
data/context_cache.json，key = url|model；之后启动直接读缓存。
模型上下文是稳定属性，缓存无需过期；模型升级后删缓存文件或改模型名即可。
"""
import json
import re
import urllib.request
import urllib.error
from pathlib import Path

_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "context_cache.json"

# 从报错信息提取上限（DeepSeek/OpenAI 风格："maximum context length is N tokens"）
_RE_MAX = re.compile(r"maximum context length is\s*(\d+)\s*tokens", re.I)
# 兜底：任意 "context length ... N" 表述
_RE_MAX2 = re.compile(r"context length[^\d]{0,40}(\d{4,})", re.I)


def _read_cache() -> dict:
    try:
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_cache(cache: dict):
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _probe_remote(url: str, model: str, key: str, timeout: int = 180):
    """实测：发送超长输入触发报错，从报错提取真实上限。失败返回 None。"""
    base = url.rstrip("/") + "/chat/completions"
    tokens = 1_000_000  # 从 1M 起（主流模型上限 <= 1M 会直接报错）
    while tokens <= 64_000_000:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "word " * tokens}],
            "max_tokens": 1,
        }
        # DeepSeek 需要关闭 thinking，否则可能吞输出/报错；其他后端忽略该字段
        if "deepseek" in model.lower():
            body["thinking"] = {"type": "disabled"}
        req = urllib.request.Request(
            base, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        try:
            urllib.request.urlopen(req, timeout=timeout)
            # 成功 = 模型上下文比当前 tokens 还大，加倍再试
            tokens *= 2
            continue
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            m = _RE_MAX.search(msg) or _RE_MAX2.search(msg)
            if m:
                val = int(m.group(1))
                return val if val > 0 else None
            return None  # 报错但非 context 超限（限流/鉴权等），无法提取
        except Exception:
            return None
    return None


def probe_context_window(url: str, model: str, key: str,
                         force: bool = False, timeout: int = 180):
    """探测模型最大上下文（带缓存）。返回 token 数；失败返回 None。

    force=True 时忽略缓存强制重新实测。
    """
    cache = {} if force else _read_cache()
    cache_key = f"{url.rstrip('/')}|{model}"
    if cache_key in cache:
        return cache[cache_key]
    val = _probe_remote(url, model, key, timeout)
    if val:
        cache[cache_key] = val
        _write_cache(cache)
    return val


if __name__ == "__main__":
    # 手动探测：python utils/context_probe.py
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config.config import config
    val = probe_context_window(config.ceo_url, config.ceo_model, config.ceo_key,
                               force=True)
    print(f"CEO 模型 {config.ceo_model} 最大上下文: {val} tokens"
          if val else "探测失败（回退 settings 值）")
