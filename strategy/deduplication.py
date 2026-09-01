"""Deterministic semantic strategy fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

from app.models import Strategy

from .memory import StrategyMemory, StrategyMemoryRecord, history_records


def _normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_value(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, str):
        return _normalize_text(value)
    return value


def semantic_payload(strategy: Strategy) -> dict[str, Any]:
    """Return the fields that define meaningful strategy identity."""

    return {
        "objective": _normalize_text(strategy.objective),
        "priorities": sorted({_normalize_text(item) for item in strategy.priorities}),
        "constraints": sorted(
            {_normalize_text(item) for item in strategy.constraints}
        ),
        "context": _normalize_value(strategy.context),
    }


def strategy_fingerprint(strategy: Strategy) -> str:
    payload = json.dumps(
        semantic_payload(strategy),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def is_duplicate(
    strategy: Strategy,
    history: StrategyMemory | Sequence[StrategyMemoryRecord],
) -> bool:
    candidate = strategy_fingerprint(strategy)
    return any(
        strategy_fingerprint(record.strategy) == candidate
        for record in history_records(history)
    )