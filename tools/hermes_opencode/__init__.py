"""OpenCode provider profile — ADGS 本地端点对接（2026-09-05）。

背景：用户要求模型真正走 hermes 的 opencode 提供商对接路径
（provider=opencode，日志/路由都是 opencode），只把请求 URL 指向
ADGS 自己的 vLLM 端点（http://8.138.131.119:9000/v1）。

实现方式（hermes 官方扩展点，仅用户插件 + .env，不依赖改注册表）：
  - hermes_cli/auth.py 里用户插件可用别名扩展覆盖硬编码别名——
    本插件注册 name="opencode"（含自别名 "opencode"），后加载覆盖
    内置 opencode-zen 插件的 "opencode"→"opencode-zen" 别名。
  - env_vars 非空（OPENCODE_API_KEY / OPENCODE_BASE_URL）→
    auth.py:450 的 PROVIDER_REGISTRY 自动扩展把它登记为合法 api-key
    provider（"New providers only need a plugins/model-providers/<name>/
    plugin — no edits to this file required"）。
  - 主 agent 通用 api-key 路径（runtime_provider.py:1787）在
    model.provider==provider 时优先读 config 的 model.base_url →
    vLLM 端点原样生效，api_key 走 .hermes/.env 的 OPENCODE_API_KEY。
  - 行为与 custom/vllm 对齐：default_max_tokens=65536 兜底、
    default_aux_model=""（辅助任务回落主模型）、无固定外部端点。
"""

from __future__ import annotations

from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class OpenCodeLocalProfile(ProviderProfile):
    """OpenCode(本地端点) — 行为镜像内置 custom profile。"""

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        ollama_num_ctx: int | None = None,
        **ctx: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        extra_body: dict[str, Any] = {}

        # Ollama 风格上下文窗口（与 custom 一致；vLLM 忽略未知字段）
        if ollama_num_ctx:
            options = extra_body.get("options", {})
            options["num_ctx"] = ollama_num_ctx
            extra_body["options"] = options

        # reasoning 关闭时下发 think=false（与 custom 一致）
        if reasoning_config and isinstance(reasoning_config, dict):
            _effort = (reasoning_config.get("effort") or "").strip().lower()
            _enabled = reasoning_config.get("enabled", True)
            if _effort == "none" or _enabled is False:
                extra_body["think"] = False

        return extra_body, {}

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """base_url 由 config/env 提供；未配置时不探测（与 custom 一致）。"""
        if not (base_url or self.base_url):
            return None
        return super().fetch_models(api_key=api_key, base_url=base_url, timeout=timeout)


opencode = OpenCodeLocalProfile(
    name="opencode",
    # 自别名覆盖内置 opencode-zen 插件注册的 "opencode"→"opencode-zen"
    # （用户插件后加载，register_provider last-writer-wins）。
    aliases=("opencode",),
    display_name="OpenCode",
    description="OpenCode (ADGS local OpenAI-compatible endpoint)",
    env_vars=("OPENCODE_API_KEY", "OPENCODE_BASE_URL"),
    base_url="",  # 端点来自 env OPENCODE_BASE_URL / config model.base_url
    default_max_tokens=65536,
    default_aux_model="",
)

register_provider(opencode)
