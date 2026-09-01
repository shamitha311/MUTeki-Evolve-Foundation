from __future__ import annotations

import pytest

from fool.harness.model_client import FakeModelClient


def _msg(content: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": content}]


def test_fake_model_returns_scripted_outputs_in_order() -> None:
    fake = FakeModelClient(["one", "two"])
    assert fake.complete(_msg("p1"), 100) == "one"
    assert fake.complete(_msg("p2"), 100) == "two"
    assert [c[0]["content"] for c in fake.calls] == ["p1", "p2"]


def test_fake_model_raises_when_outputs_exhausted() -> None:
    fake = FakeModelClient([])
    with pytest.raises(RuntimeError, match="fake model ran out"):
        fake.complete(_msg("p"), 100)


def test_fake_model_records_max_tokens() -> None:
    fake = FakeModelClient(["x"])
    fake.complete(_msg("p"), 777)
    assert fake.last_max_tokens == 777
