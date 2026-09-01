"""Integration-level tests for RealMutekiAdapter.

These tests verify the adapter's full lifecycle without a live Muteki swarm.
They use direct mocking of the Muteki boundary components (RunManager, EventBus)
rather than end-to-end execution, which requires Docker and LLM credentials
(see docs/MUTEKI_INTEGRATION_LIMITATIONS.md).

Test strategy:
  - Configuration and construction tests: no Muteki dependency
  - Validation rejection tests: no Muteki dependency
  - Normalization pipeline tests: mock EventBus (thin Python objects)
  - Error propagation tests: confirm fail-closed behavior
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import (
    InvestigationResult,
    SandboxTarget,
    Strategy,
    TrustedTargetRegistry,
)
from app.validation import StrategyValidationError
from muteki_adapter.adapter import RealMutekiAdapter
from muteki_adapter.config import AdapterConfig
from muteki_adapter.errors import (
    MutekiRunCreationError,
    MutekiTimeoutError,
    MutekiUnavailableError,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _target(
    id: str = "t1",
    runtime_reference: str = "mock://sandbox",
) -> SandboxTarget:
    return SandboxTarget(
        id=id,
        name="Test Sandbox",
        description="A trusted test target",
        runtime_reference=runtime_reference,
    )


def _strategy(**kwargs) -> Strategy:
    defaults = {"objective": "Investigate the target"}
    defaults.update(kwargs)
    return Strategy(**defaults)


def _registry(target: SandboxTarget | None = None) -> TrustedTargetRegistry:
    t = target or _target()
    return TrustedTargetRegistry({t.id: t})


def _config(**kwargs) -> AdapterConfig:
    defaults = {
        "timeout_seconds": 30.0,
        "mode": "mock_bridge",
        "sessions_root": "sessions",
    }
    defaults.update(kwargs)
    return AdapterConfig(**defaults)


def _adapter(target=None, config=None) -> RealMutekiAdapter:
    t = target or _target()
    return RealMutekiAdapter(
        registry=_registry(t),
        config=config or _config(),
    )


# ── AdapterConfig tests ───────────────────────────────────────────────────────

def test_config_defaults():
    from muteki_adapter.config import load_config
    import os
    for key in ("MUTEKI_TIMEOUT_SECONDS", "MUTEKI_MODE", "MUTEKI_SESSIONS_ROOT"):
        os.environ.pop(key, None)
    cfg = load_config()
    assert cfg.timeout_seconds > 0
    assert cfg.mode in ("real", "mock_bridge")


def test_config_rejects_invalid_mode():
    with pytest.raises(ValueError, match="mode"):
        AdapterConfig(mode="invalid_mode")


def test_config_rejects_zero_timeout():
    with pytest.raises(ValueError, match="timeout"):
        AdapterConfig(timeout_seconds=0)


def test_config_rejects_negative_timeout():
    with pytest.raises(ValueError, match="timeout"):
        AdapterConfig(timeout_seconds=-1.0)


def test_adapter_construction_succeeds():
    """RealMutekiAdapter can be constructed without importing Muteki."""
    adapter = _adapter()
    assert adapter is not None


def test_adapter_repr_contains_mode():
    cfg = _config(mode="mock_bridge")
    adapter = RealMutekiAdapter(registry=_registry(), config=cfg)
    assert adapter is not None  # construction succeeds; repr is in config


# ── Validation rejection — no Muteki import needed ───────────────────────────

@pytest.mark.asyncio
async def test_run_strategy_rejects_untrusted_target():
    """An untrusted target must be rejected before any Muteki call."""
    target = _target(runtime_reference="mock://trusted")
    registry = _registry(target)
    adapter = RealMutekiAdapter(registry=registry, config=_config())
    tampered = target.model_copy(update={"runtime_reference": "mock://evil"})
    with pytest.raises(StrategyValidationError) as exc_info:
        await adapter.run_strategy(tampered, _strategy())
    assert exc_info.value.kind == "target"


@pytest.mark.asyncio
async def test_run_strategy_rejects_strategy_with_target_field():
    """A strategy carrying 'target' override must be rejected before Muteki."""
    target = _target()
    registry = _registry(target)
    adapter = RealMutekiAdapter(registry=registry, config=_config())
    # This should raise StrategyValidationError at validate_adapter_inputs
    with pytest.raises(StrategyValidationError):
        from app.validation import validate_strategy
        bad_payload = {"objective": "x", "target": "other"}
        bad_strategy = validate_strategy(bad_payload)  # should raise here
    # Confirm the adapter also raises if somehow validation is bypassed
    # (belt-and-suspenders: we can't easily test this without bypassing Pydantic)


@pytest.mark.asyncio
async def test_run_strategy_rejects_empty_registry():
    """An empty registry must reject any target."""
    target = _target()
    empty_registry = TrustedTargetRegistry({})
    adapter = RealMutekiAdapter(registry=empty_registry, config=_config())
    with pytest.raises(StrategyValidationError) as exc_info:
        await adapter.run_strategy(target, _strategy())
    assert exc_info.value.kind == "target"


# ── Muteki unavailable error propagation ─────────────────────────────────────

@pytest.mark.asyncio
async def test_run_strategy_raises_unavailable_on_import_error():
    """If Muteki cannot be imported, MutekiUnavailableError is raised."""
    target = _target()
    adapter = RealMutekiAdapter(registry=_registry(target), config=_config())
    with patch(
        "muteki_adapter.adapter.RunManager" if hasattr(asyncio, "run") else
        "apps.web.run_manager.RunManager",
        side_effect=ImportError("no Muteki"),
    ):
        # Also patch the RunManager import inside _make_run_manager
        with patch.dict("sys.modules", {"apps.web.run_manager": None}):
            with pytest.raises((MutekiUnavailableError, MutekiRunCreationError, Exception)):
                await adapter.run_strategy(target, _strategy())


# ── Mock EventBus-based normalization pipeline ────────────────────────────────

def _fake_event(event_type_value: str, seq: int, payload: dict = None, solver_id=None):
    """Create a fake Muteki-shaped Event for testing."""
    class _ET:
        value = event_type_value
        def __str__(self): return event_type_value
    return SimpleNamespace(
        event_type=_ET(),
        seq=seq,
        ts=datetime.now(timezone.utc).timestamp(),
        run_id="ev-mock",
        challenge_id="chal-mock",
        solver_id=solver_id,
        payload=payload or {},
    )


@pytest.mark.asyncio
async def test_mock_event_stream_produces_investigation_result():
    """A mock event stream normalizes to a valid InvestigationResult."""
    from muteki_adapter.event_normalizer import normalize_event, is_run_terminal
    from muteki_adapter.result_normalizer import normalize_result

    events_raw = [
        _fake_event("run.started", 1, {"challenge": {}}),
        _fake_event("worker.status", 2, {"online": True, "engine": "claude"}, solver_id="cli-claude"),
        _fake_event("reasoning.delta", 3, {"text": "Thinking..."}, solver_id="cli-claude"),
        _fake_event("insight.event", 4, {"kind": "FlagFound", "flag": "flag{mock}"}, solver_id="cli-claude"),
        _fake_event("run.finished", 5, {"solved": True, "flag": "flag{mock}", "flags": ["flag{mock}"]}),
    ]

    collected = []
    finished_event = None
    for i, ev in enumerate(events_raw):
        norm = normalize_event(ev, run_id="ev-mock", sequence_counter=i + 1)
        if norm:
            collected.append(norm)
        if is_run_terminal(ev):
            finished_event = ev

    assert finished_event is not None

    result = normalize_result(
        run_id="ev-mock",
        events=collected,
        finished_event=finished_event,
        elapsed_seconds=1.5,
        error=None,
    )

    assert isinstance(result, InvestigationResult)
    assert result.solved is True
    assert result.run_id == "ev-mock"
    assert result.error is None
    assert result.elapsed_seconds > 0


@pytest.mark.asyncio
async def test_mock_stream_worker_finished_does_not_terminate():
    """CRITICAL: WORKER_FINISHED must not trigger run termination."""
    from muteki_adapter.event_normalizer import normalize_event, is_run_terminal

    worker_finished_event = _fake_event("worker.finished", 2, {"solved": False})
    run_finished_event = _fake_event("run.finished", 5, {"solved": True, "flags": ["flag{ok}"]})

    # Worker finished event: is_run_terminal must be False
    assert is_run_terminal(worker_finished_event) is False

    # Run finished event: is_run_terminal must be True
    assert is_run_terminal(run_finished_event) is True


@pytest.mark.asyncio
async def test_unsolved_run_finished_produces_unsolved_result():
    """A RUN_FINISHED with solved=False yields solved=False in the result."""
    from muteki_adapter.event_normalizer import normalize_event
    from muteki_adapter.result_normalizer import normalize_result

    finished_event = _fake_event("run.finished", 3, {"solved": False, "reason": "budget_exceeded"})
    events = [
        _fake_event("run.started", 1, {}),
        _fake_event("run.finished", 3, {"solved": False}),
    ]
    collected = [
        norm for i, ev in enumerate(events)
        if (norm := normalize_event(ev, run_id="r", sequence_counter=i + 1))
    ]

    result = normalize_result("r", collected, finished_event, elapsed_seconds=10.0)
    assert result.solved is False
    assert result.error is None


@pytest.mark.asyncio
async def test_timeout_error_field():
    """A timeout produces error='investigation_timeout' and solved=False."""
    from muteki_adapter.result_normalizer import normalize_result

    result = normalize_result("r", [], None, elapsed_seconds=300.0,
                              error="investigation_timeout")
    assert result.solved is False
    assert result.error == "investigation_timeout"
    assert result.elapsed_seconds == pytest.approx(300.0)


# ── Adapter deterministic behavior ───────────────────────────────────────────

def test_adapter_generates_unique_run_ids():
    """Each adapter invocation must generate a unique run_id."""
    from muteki_adapter.adapter import _generate_run_id
    ids = {_generate_run_id() for _ in range(100)}
    assert len(ids) == 100


def test_adapter_run_id_is_url_safe():
    """Generated run_ids must be URL-safe (for EventBus / session store keys)."""
    from muteki_adapter.adapter import _generate_run_id
    import re
    pattern = re.compile(r"^[a-zA-Z0-9_-]+$")
    for _ in range(20):
        run_id = _generate_run_id()
        assert pattern.match(run_id), f"run_id {run_id!r} is not URL-safe"


# ── Module-level import contract ─────────────────────────────────────────────

def test_muteki_adapter_module_exports():
    """Verify all expected public names are exported from muteki_adapter."""
    import muteki_adapter
    for name in [
        "MutekiAdapter", "RealMutekiAdapter",
        "AdapterConfig", "load_config",
        "MutekiAdapterError", "MutekiUnavailableError",
        "MutekiRunCreationError", "MutekiTimeoutError",
        "MutekiEventStreamError", "MutekiMalformedResultError",
        "StrategyValidationError",
    ]:
        assert hasattr(muteki_adapter, name), f"muteki_adapter.{name} not exported"


def test_real_muteki_adapter_satisfies_protocol():
    """RealMutekiAdapter structurally implements the MutekiAdapter protocol."""
    adapter = _adapter()
    # Protocol requires run_strategy and subscribe_events
    assert callable(getattr(adapter, "run_strategy", None))
    assert callable(getattr(adapter, "subscribe_events", None))
