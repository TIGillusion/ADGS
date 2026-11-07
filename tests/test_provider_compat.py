import pytest
from core.provider_compat import create_with_compat


class Rejection(Exception):
    status_code = 500


def test_proxy_fallback_rejection_retries_without_named_hint(monkeypatch):
    calls = []
    clock = iter([10, 12])
    monkeypatch.setattr("core.provider_compat.time.monotonic", lambda: next(clock))
    messages, tools = [{"role": "user", "content": "task"}], [{"name": "fixture"}]
    extra = {"reasoning_effort": "high", "keep": True}

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise Rejection("litellm.UnsupportedParamsError: model does not support parameters: ['reasoning_effort']")
        return "ok"

    assert create_with_compat(create, model="worker", messages=messages, tools=tools,
                              reasoning_effort="high", extra_body=extra, timeout=10) == "ok"
    assert len(calls) == 2 and calls[1]["timeout"] == 8
    assert "reasoning_effort" not in calls[1]
    assert calls[1]["extra_body"] == {"keep": True}
    assert extra["reasoning_effort"] == "high"
    assert calls[1]["messages"] is messages and calls[1]["tools"] is tools


@pytest.mark.parametrize("message,hint", [("Connection error", True),
    ("litellm.UnsupportedParamsError: ['reasoning_effort']", False)])
def test_no_retry_for_network_error_or_absent_hint(message, hint):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise Rejection(message)
    with pytest.raises(Rejection):
        create_with_compat(create, **({"reasoning_effort": "high"} if hint else {}))
    assert len(calls) == 1


def test_second_failure_is_not_retried_again():
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise Rejection("litellm.UnsupportedParamsError: ['reasoning_effort']")
    with pytest.raises(Rejection):
        create_with_compat(create, reasoning_effort="high")
    assert len(calls) == 2


def test_expired_budget_is_not_retried(monkeypatch):
    clock = iter([10, 12])
    monkeypatch.setattr("core.provider_compat.time.monotonic", lambda: next(clock))
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        raise Rejection("litellm.UnsupportedParamsError: ['reasoning_effort']")
    with pytest.raises(Rejection):
        create_with_compat(create, reasoning_effort="high", timeout=1)
    assert len(calls) == 1


def test_hermes_sdk_install_recovers_actual_http_500(monkeypatch):
    import json
    import httpx
    from openai import OpenAI
    from openai.resources.chat.completions import Completions
    from core.provider_compat import install_openai_compat
    original = Completions.create
    monkeypatch.setattr(Completions, "create", original)
    bodies = []

    def transport(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(500, json={"error": {"message":
                "litellm.UnsupportedParamsError: parameters: ['reasoning_effort']"}})
        return httpx.Response(200, json={"id": "fixture", "object": "chat.completion",
            "created": 0, "model": "fixture", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "recovered"}, "finish_reason": "stop"}]})

    install_openai_compat()
    installed = Completions.create
    install_openai_compat()
    assert Completions.create is installed
    with OpenAI(api_key="fixture", base_url="http://fixture.invalid/v1", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(transport))) as client:
        result = client.chat.completions.create(model="fixture", reasoning_effort="high",
                    messages=[{"role": "user", "content": "original task"}], timeout=5)
    assert result.choices[0].message.content == "recovered"
    assert len(bodies) == 2 and "reasoning_effort" not in bodies[1]
    assert bodies[1]["messages"] == bodies[0]["messages"]


def test_required_tool_choice_is_scoped_to_ceo_control_process(monkeypatch):
    import json
    import httpx
    from openai import OpenAI
    from openai.resources.chat.completions import Completions
    from core.provider_compat import install_openai_compat
    monkeypatch.setattr(Completions, "create", Completions.create)
    install_openai_compat()
    bodies = []
    def transport(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id":"fixture","object":"chat.completion","created":0,
            "model":"fixture","choices":[{"index":0,"message":{"role":"assistant","content":"fixture"},"finish_reason":"stop"}]})
    tools = [{"type":"function","function":{"name":"report","parameters":{"type":"object"}}}]
    with OpenAI(api_key="fixture",base_url="http://fixture.invalid/v1",max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(transport))) as client:
        monkeypatch.delenv("ADGS_CONTROL_TOOLS", raising=False)
        client.chat.completions.create(model="fixture",messages=[{"role":"user","content":"worker"}],tools=tools)
        monkeypatch.setenv("ADGS_CONTROL_TOOLS", "control-schema.json")
        client.chat.completions.create(model="fixture",messages=[{"role":"user","content":"ceo"}],tools=tools)
        client.chat.completions.create(model="fixture",messages=[{"role":"user","content":"title"}])
    assert "tool_choice" not in bodies[0]
    assert bodies[1]["tool_choice"] == "required"
    assert "tool_choice" not in bodies[2]
