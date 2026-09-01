from __future__ import annotations

import fool.llm_client as client


def test_aliyun_uses_dashscope_compatible_endpoint() -> None:
    assert (
        client._resolve_chat_endpoint("aliyun", None)
        == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )


def test_aliyun_probe_uses_openai_compatible_probe_budget(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_llm(**kwargs) -> str:
        captured["api_type"] = kwargs["api_type"]
        captured["max_tokens"] = kwargs["max_tokens"]
        return "MTASA_OK"

    monkeypatch.setattr(client, "call_llm", fake_call_llm)

    result = client.probe_llm_connection("aliyun", "token", "qwen-plus")

    assert result["ok"] is True
    assert captured["api_type"] == "aliyun"
    assert captured["max_tokens"] == 32


def test_openrouter_probe_allocates_reasoning_compatible_output_budget(monkeypatch) -> None:
    captured: dict[str, int] = {}

    def fake_call_llm(**kwargs) -> str:
        captured["max_tokens"] = int(kwargs["max_tokens"])
        return "MTASA_OK"

    monkeypatch.setattr(client, "call_llm", fake_call_llm)

    result = client.probe_llm_connection("openrouter", "token", "minimax/minimax-m2.5")

    assert result["ok"] is True
    assert captured["max_tokens"] == 512


def test_http_error_exposes_provider_message(monkeypatch) -> None:
    class ResponseError(client.urllib.error.HTTPError):
        def read(self) -> bytes:
            return b'{"error":{"message":"provider terms blocked this request"}}'

    def fail_urlopen(request, timeout):
        raise ResponseError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(client.urllib.request, "urlopen", fail_urlopen)

    try:
        client.call_llm("openrouter", "token", "openai/gpt-4.1-mini", [{"role": "user", "content": "x"}])
    except client.LLMHTTPError as exc:
        assert "HTTP 403" in str(exc)
        assert "provider terms blocked" in str(exc)
    else:
        raise AssertionError("expected provider HTTP error")


def test_openrouter_accepts_medium_effort(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'

    def fake_urlopen(request, timeout):
        import json

        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return DummyResponse()

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    result = client.call_llm_meta(
        api_type="openrouter",
        api_key="token",
        model="deepseek/deepseek-v4-pro",
        messages=[{"role": "user", "content": "hi"}],
        effort_level="medium",
    )

    assert result.text == "ok"
    assert captured["payload"]["reasoning"] == {"effort": "medium"}


def test_aliyun_accepts_xhigh_effort(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'

    def fake_urlopen(request, timeout):
        import json

        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return DummyResponse()

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    result = client.call_llm_meta(
        api_type="aliyun",
        api_key="token",
        model="qwen-plus",
        messages=[{"role": "user", "content": "hi"}],
        effort_level="xhigh",
    )

    assert result.text == "ok"
    assert captured["payload"]["reasoning"] == {"effort": "xhigh"}


def test_deepseek_accepts_xhigh_effort(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'

    def fake_urlopen(request, timeout):
        import json

        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return DummyResponse()

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)

    result = client.call_llm_meta(
        api_type="deepseek",
        api_key="token",
        model="deepseek-v4-pro",
        messages=[{"role": "user", "content": "hi"}],
        effort_level="xhigh",
    )

    assert result.text == "ok"
    assert captured["payload"]["reasoning_effort"] == "xhigh"
