from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMHTTPError(RuntimeError):
    """Remote API error with a readable provider response."""


_TRANSIENT_NET_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    ConnectionResetError,
    ConnectionAbortedError,
    socket.timeout,
    TimeoutError,
)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    finish_reason: str  # normalized: "stop" | "length" | "content_filter" | "tool_use" | "" (unknown)
    raw_finish: str     # provider-specific raw value, for logging
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0  # cache-hit portion of prompt_tokens (0 if provider lacks cache reporting)


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    max_retries: int = 5,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    last_exc: BaseException | None = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            return json.loads(text)
        except urllib.error.HTTPError as exc:
            if 500 <= exc.code < 600 and attempt < max_retries - 1:
                last_exc = exc
                time.sleep(2 ** attempt)
                continue
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
                error = data.get("error", {}) if isinstance(data, dict) else {}
                detail = str(error.get("message", raw)).strip()
            except json.JSONDecodeError:
                detail = raw.strip() or str(exc.reason)
            raise LLMHTTPError(f"HTTP {exc.code}: {detail}") from exc
        except _TRANSIENT_NET_ERRORS as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(
                f"LLM transient network error after {max_retries} attempts: {exc!r}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, _TRANSIENT_NET_ERRORS) and attempt < max_retries - 1:
                last_exc = exc
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(  # pragma: no cover — defensive
        f"LLM request exhausted retries without raising: last={last_exc!r}"
    )


_OPENAI_FINISH_MAP = {
    "stop": "stop",
    "length": "length",
    "content_filter": "content_filter",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
}

_CLAUDE_FINISH_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_use",
}


def _extract_openai_response(data: dict[str, Any]) -> LLMResponse:
    choices = data.get("choices") or []
    if not choices:
        return LLMResponse(text="", finish_reason="", raw_finish="")
    choice = choices[0]
    msg = choice.get("message") or {}
    content = msg.get("content", "")
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        text = "\n".join(parts)
    else:
        text = str(content)
    raw = str(choice.get("finish_reason") or "")
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached_tokens = int(details.get("cached_tokens") or 0)
    if not cached_tokens:
        cached_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)
    return LLMResponse(
        text=text,
        finish_reason=_OPENAI_FINISH_MAP.get(raw, raw),
        raw_finish=raw,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
    )


def _extract_claude_response(data: dict[str, Any]) -> LLMResponse:
    content = data.get("content") or []
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    raw = str(data.get("stop_reason") or "")
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or 0)
    cached_tokens = int(usage.get("cache_read_input_tokens") or 0)
    return LLMResponse(
        text="\n".join(parts),
        finish_reason=_CLAUDE_FINISH_MAP.get(raw, raw),
        raw_finish=raw,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
    )


def _resolve_chat_endpoint(api_type: str, base_url: str | None) -> str:
    if not base_url:
        default_map = {
            "openai": "https://api.openai.com/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "deepseek": "https://api.deepseek.com/chat/completions",
            "aliyun": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "custom": "https://api.openai.com/v1/chat/completions",
        }
        return default_map[api_type]

    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base

    if api_type == "openrouter":
        if base.endswith("/api"):
            return base + "/v1/chat/completions"
        if base.endswith("/api/v1") or base.endswith("/v1"):
            return base + "/chat/completions"

    if base.endswith("/api/v1") or base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/chat/completions"


EFFORT_LEVELS = ("low", "medium", "high", "xhigh")


def _apply_effort_to_payload(payload: dict[str, Any], api_type: str, effort_level: str) -> None:
    """Inject provider-specific reasoning controls into an OpenAI-shape payload."""
    level = (effort_level or "low").strip().lower()
    if level not in EFFORT_LEVELS:
        level = "low"
    if api_type in {"openrouter", "aliyun"}:
        payload["reasoning"] = {"effort": level}
    elif api_type == "deepseek":
        payload["reasoning_effort"] = level
    # openai / custom: no standard reasoning toggle, leave payload unchanged.


def call_llm_meta(
    api_type: str,
    api_key: str,
    model: str,
    messages: list,
    base_url: str | None = None,
    timeout: int = 120,
    max_tokens: int = 1200,
    json_mode: bool = False,
    effort_level: str = "low",
    system: str | None = None,
) -> LLMResponse:
    """Same as `call_llm` but also returns finish_reason / raw stop info.

    json_mode=True asks providers that support it (OpenAI / OpenRouter /
    DeepSeek) to constrain output to a single JSON object. Caller must
    still include the word "JSON" somewhere in the prompt.

    effort_level controls how much hidden reasoning a reasoning-capable
    model spends per call: "low" / "medium" / "high" / "xhigh" choose the
    provider's named effort tier. Providers without a reasoning toggle
    ignore the setting.
    """
    api_type = (api_type or "").strip().lower()
    if not api_key:
        raise ValueError("api_key is required for remote LLM calls")

    try:
        if api_type in {"openai", "openrouter", "deepseek", "aliyun", "custom"}:
            endpoint = _resolve_chat_endpoint(api_type, base_url)

            effective_messages = messages
            if system and not (messages and messages[0].get("role") == "system"):
                effective_messages = [{"role": "system", "content": system}, *messages]

            payload: dict[str, Any] = {
                "model": model,
                "messages": effective_messages,
                "temperature": 0.2,
                "max_tokens": max_tokens,
            }
            _apply_effort_to_payload(payload, api_type, effort_level)
            if json_mode and api_type in {"openai", "openrouter", "deepseek", "aliyun"}:
                payload["response_format"] = {"type": "json_object"}

            data = _post_json(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    **(
                        {
                            "HTTP-Referer": "http://localhost:7860",
                            "X-OpenRouter-Title": "MTASA",
                        }
                        if api_type == "openrouter"
                        else {}
                    ),
                },
                payload=payload,
                timeout=timeout,
            )
            return _extract_openai_response(data)

        if api_type == "claude":
            endpoint = (base_url.rstrip("/") if base_url else "https://api.anthropic.com/v1") + "/messages"
            claude_system = system
            claude_messages = messages
            if messages and messages[0].get("role") == "system":
                claude_system = claude_system or messages[0]["content"]
                claude_messages = messages[1:]
            claude_payload: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": claude_messages,
            }
            if claude_system:
                claude_payload["system"] = claude_system
            data = _post_json(
                endpoint,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                payload=claude_payload,
                timeout=timeout,
            )
            return _extract_claude_response(data)

        raise ValueError(f"Unsupported api_type: {api_type}")
    except LLMHTTPError:
        raise
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc


def call_llm(
    api_type: str,
    api_key: str,
    model: str,
    messages: list,
    base_url: str | None = None,
    timeout: int = 120,
    max_tokens: int = 1200,
    json_mode: bool = False,
    effort_level: str = "low",
) -> str:
    """Unified local LLM API wrapper used only during Fool learning loops."""
    return call_llm_meta(
        api_type=api_type,
        api_key=api_key,
        model=model,
        messages=messages,
        base_url=base_url,
        timeout=timeout,
        max_tokens=max_tokens,
        json_mode=json_mode,
        effort_level=effort_level,
    ).text


def probe_llm_connection(
    api_type: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
    timeout: int = 25,
    effort_level: str = "low",
) -> dict[str, str | bool]:
    if not api_key:
        return {
            "ok": False,
            "message": "API key is empty",
            "endpoint": _resolve_chat_endpoint(api_type, base_url)
            if api_type in {"openai", "openrouter", "deepseek", "aliyun", "custom"}
            else ((base_url.rstrip("/") if base_url else "https://api.anthropic.com/v1") + "/messages"),
        }

    endpoint = ""
    try:
        if api_type in {"openai", "openrouter", "deepseek", "aliyun", "custom"}:
            endpoint = _resolve_chat_endpoint(api_type, base_url)
        elif api_type == "claude":
            endpoint = (base_url.rstrip("/") if base_url else "https://api.anthropic.com/v1") + "/messages"

        text = call_llm(
            api_type=api_type,
            api_key=api_key,
            model=model,
            base_url=base_url,
            messages=[{"role": "user", "content": "Reply exactly: MTASA_OK"}],
            timeout=timeout,
            # Reasoning-capable models (OpenRouter, DeepSeek v4) spend an
            # internal token budget on thinking before emitting visible
            # content. Give them enough room or the probe sees an empty body.
            max_tokens=512 if api_type in {"openrouter", "deepseek"} else 32,
            effort_level=effort_level,
        )
        if not text.strip():
            return {
                "ok": False,
                "message": "API 已响应，但在连接测试输出额度内没有返回可见正文",
                "endpoint": endpoint,
            }
        return {"ok": True, "message": text.strip(), "endpoint": endpoint}
    except Exception as exc:
        message = str(exc)
        if api_type == "openrouter" and "Terms Of Service" in message:
            message += "（上游供应商策略拒绝，并非模型名称拼写错误）"
        return {"ok": False, "message": message, "endpoint": endpoint}
