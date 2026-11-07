"""Bounded compatibility retry for explicitly rejected optional reasoning hints."""
import re
import time
import logging
import os


def create_with_compat(create, **kwargs):
    extra = kwargs.get("extra_body")
    if "reasoning_effort" not in kwargs and not (isinstance(extra, dict) and "reasoning_effort" in extra):
        return create(**kwargs)
    started = time.monotonic()
    try:
        return create(**kwargs)
    except Exception as exc:
        text = str(exc).lower()
        status = getattr(exc, "status_code", None)
        explicit = ("unsupportedparamserror" in text or
                    re.search(r"(?:not support|unsupported|not allowed).*reasoning_effort", text))
        if status not in (400, 422, 500) or not explicit or "reasoning_effort" not in text:
            raise
        retry = dict(kwargs)
        removed = "reasoning_effort" in retry
        retry.pop("reasoning_effort", None)
        extra = retry.get("extra_body")
        if isinstance(extra, dict) and "reasoning_effort" in extra:
            retry["extra_body"] = {k: v for k, v in extra.items() if k != "reasoning_effort"}
            removed = True
        if not removed:
            raise  # Never guess at proxy-injected parameters or unrelated fields.
        timeout = retry.get("timeout")
        if isinstance(timeout, (int, float)):
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise
            retry["timeout"] = remaining
        logging.getLogger(__name__).warning("ADGS provider compatibility: retrying once without rejected reasoning_effort")
        return create(**retry)  # One retry only; a second error propagates unchanged.


def install_openai_compat():
    """Install within the ADGS Hermes child only, before provider/client startup."""
    from openai.resources.chat.completions import Completions
    original = Completions.create
    if getattr(original, "_adgs_reasoning_compat", False):
        return

    def compatible(self, *args, **kwargs):
        if os.environ.get("ADGS_CONTROL_TOOLS") and kwargs.get("tools"):
            # Hermes defaults to auto; the CEO bridge requires an actual tool
            # receipt before dispatch. Memory/title requests without tools and
            # all ordinary Worker requests keep their existing behavior.
            kwargs["tool_choice"] = "required"
        return create_with_compat(lambda **request: original(self, *args, **request), **kwargs)

    compatible._adgs_reasoning_compat = True
    Completions.create = compatible
