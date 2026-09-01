from __future__ import annotations

from typing import Protocol

from fool.llm_client import call_llm_meta


class ModelClient(Protocol):
    def complete(
        self, messages: list[dict[str, str]], max_tokens: int
    ) -> str: ...


class FakeModelClient:
    """Scripted ModelClient for deterministic tests."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, str]]] = []
        self.last_max_tokens: int | None = None

    def complete(
        self, messages: list[dict[str, str]], max_tokens: int
    ) -> str:
        self.calls.append([dict(m) for m in messages])
        self.last_max_tokens = max_tokens
        if not self.outputs:
            raise RuntimeError("fake model ran out of outputs")
        return self.outputs.pop(0)

    @property
    def last_messages(self) -> list[dict[str, str]]:
        return self.calls[-1] if self.calls else []


class LLMModelClient:
    """ModelClient backed by fool.llm_client.call_llm_meta."""

    def __init__(
        self,
        *,
        api_type: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: int = 360,
        effort_level: str = "low",
    ) -> None:
        self.api_type = api_type
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.effort_level = effort_level
        self.last_response = None  # type: ignore[assignment]

    def complete(
        self, messages: list[dict[str, str]], max_tokens: int
    ) -> str:
        response = call_llm_meta(
            api_type=self.api_type,
            api_key=self.api_key,
            model=self.model,
            messages=messages,
            base_url=self.base_url,
            timeout=self.timeout,
            max_tokens=max_tokens,
            json_mode=False,
            effort_level=self.effort_level,
        )
        self.last_response = response
        return response.text
