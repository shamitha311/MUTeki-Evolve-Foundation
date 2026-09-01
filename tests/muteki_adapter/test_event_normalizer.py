"""Tests for muteki_adapter.event_normalizer.

Tests verify:
  - Each important Muteki event type normalizes correctly
  - Sequence numbers are monotonic and at least 1
  - worker_id maps from solver_id
  - RUN_FINISHED is correctly identified as terminal
  - WORKER_FINISHED is NOT identified as terminal (critical correctness property)
  - Malformed events return None instead of raising
  - Unknown event types produce a valid fallback InvestigationEvent
"""

from __future__ import annotations

import datetime
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import InvestigationEvent
from muteki_adapter.event_normalizer import (
    MAPPED_EVENT_TYPES,
    extract_summary,
    is_run_terminal,
    normalize_event,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeEventType:
    """Mimic muteki.core.events.EventType enum member."""
    def __init__(self, value: str) -> None:
        self.value = value
    def __str__(self) -> str:
        return self.value


def _make_event(
    event_type: str = "run.started",
    seq: int = 5,
    ts: float | None = None,
    run_id: str = "",
    solver_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    return SimpleNamespace(
        event_type=_FakeEventType(event_type),
        seq=seq,
        ts=ts if ts is not None else time.time(),
        run_id=run_id,
        challenge_id="chal-1",
        solver_id=solver_id,
        payload=payload or {},
    )


# ── normalize_event basic contract ───────────────────────────────────────────

def test_normalize_run_started_returns_investigation_event():
    ev = _make_event("run.started", seq=1)
    result = normalize_event(ev, run_id="run-1", sequence_counter=1)
    assert isinstance(result, InvestigationEvent)
    assert result.sequence == 1
    assert result.run_id == "run-1"


def test_normalize_uses_muteki_seq_when_nonzero():
    ev = _make_event("run.started", seq=42)
    result = normalize_event(ev, run_id="r", sequence_counter=1)
    assert result is not None
    assert result.sequence == 42


def test_normalize_uses_counter_when_seq_zero():
    ev = _make_event("run.started", seq=0)
    result = normalize_event(ev, run_id="r", sequence_counter=7)
    assert result is not None
    assert result.sequence == 7


def test_sequence_is_always_at_least_1():
    ev = _make_event("run.started", seq=0)
    result = normalize_event(ev, run_id="r", sequence_counter=0)
    assert result is not None
    assert result.sequence >= 1


def test_timestamp_is_iso8601():
    ev = _make_event("run.started", ts=1700000000.0)
    result = normalize_event(ev, run_id="r", sequence_counter=1)
    assert result is not None
    # Should parse as ISO datetime
    dt = datetime.datetime.fromisoformat(result.timestamp)
    assert dt.tzinfo is not None


def test_worker_id_maps_from_solver_id():
    ev = _make_event("worker.status", solver_id="cli-claude")
    result = normalize_event(ev, run_id="r", sequence_counter=1)
    assert result is not None
    assert result.worker_id == "cli-claude"


def test_worker_id_is_none_when_no_solver_id():
    ev = _make_event("run.started", solver_id=None)
    result = normalize_event(ev, run_id="r", sequence_counter=1)
    assert result is not None
    assert result.worker_id is None


def test_run_id_taken_from_event():
    ev = _make_event("run.started", run_id="ev-xyz")
    result = normalize_event(ev, run_id="fallback", sequence_counter=1)
    assert result is not None
    assert result.run_id == "ev-xyz"


def test_run_id_falls_back_when_event_run_id_empty():
    ev = _make_event("run.started")
    ev.run_id = ""
    result = normalize_event(ev, run_id="adapter-run", sequence_counter=1)
    assert result is not None
    assert result.run_id == "adapter-run"


def test_malformed_event_returns_none():
    """A completely invalid event object must return None, not raise."""
    result = normalize_event(None, run_id="r", sequence_counter=1)
    assert result is None


def test_unknown_event_type_returns_valid_event():
    """An unmapped event type still produces a valid InvestigationEvent."""
    ev = _make_event("future.unknown.event.type")
    result = normalize_event(ev, run_id="r", sequence_counter=1)
    assert result is not None
    assert isinstance(result, InvestigationEvent)


# ── is_run_terminal ───────────────────────────────────────────────────────────

def test_run_finished_is_terminal():
    """RUN_FINISHED must be identified as the run-level terminal event."""
    ev = _make_event("run.finished")
    assert is_run_terminal(ev) is True


def test_worker_finished_is_NOT_terminal():
    """
    CRITICAL: WORKER_FINISHED must NEVER be treated as run-level terminal.
    Source comment: 'a worker ending must not finish the whole run'
    """
    ev = _make_event("worker.finished")
    assert is_run_terminal(ev) is False


def test_run_started_is_not_terminal():
    ev = _make_event("run.started")
    assert is_run_terminal(ev) is False


def test_insight_event_is_not_terminal():
    ev = _make_event("insight.event")
    assert is_run_terminal(ev) is False


def test_none_event_type_is_not_terminal():
    ev = SimpleNamespace(event_type=None)
    assert is_run_terminal(ev) is False


# ── extract_summary for key event types ───────────────────────────────────────

def test_run_started_summary():
    assert "started" in extract_summary("run.started", {}).lower()


def test_run_finished_solved_summary():
    payload = {"solved": True, "flags": ["flag{test}"], "flag": "flag{test}"}
    s = extract_summary("run.finished", payload)
    assert "success" in s.lower() or "completed" in s.lower()


def test_run_finished_unsolved_summary():
    payload = {"solved": False}
    s = extract_summary("run.finished", payload)
    assert "finished" in s.lower() or "no success" in s.lower()


def test_insight_flag_found_summary():
    payload = {"kind": "FlagFound", "flag": "flag{example}"}
    s = extract_summary("insight.event", payload)
    assert "flag" in s.lower()
    assert "flag{example}" in s


def test_insight_dead_end_summary():
    payload = {"kind": "DeadEndMarked", "text": "SQLi patched"}
    s = extract_summary("insight.event", payload)
    assert "dead end" in s.lower() or "path" in s.lower() or "eliminated" in s.lower()


def test_insight_fact_discovered_summary():
    payload = {"kind": "FactDiscovered", "text": "Admin panel at /admin"}
    s = extract_summary("insight.event", payload)
    assert "fact" in s.lower() or "discovered" in s.lower()


def test_worker_finished_summary_mentions_worker_not_run():
    s = extract_summary("worker.finished", {"solved": False})
    # The summary must indicate this is worker-level, not run-level completion
    assert "worker" in s.lower()
    # Must NOT claim the run is done
    assert "run" not in s.lower() or "continues" in s.lower()


def test_sharedgraph_verified_fact_summary_includes_confidence():
    payload = {"fact": "HTTP 200 on /secret", "verified": True, "confidence": 0.95}
    s = extract_summary("sharedgraph.delta", payload)
    assert "95%" in s or "verified" in s.lower()


def test_hitl_request_summary():
    payload = {"need": "The instance credential expired"}
    s = extract_summary("hitl.request", payload)
    assert "operator" in s.lower() or "input" in s.lower()


def test_blackboard_flag_found_summary():
    payload = {"kind": "flag_found", "actor": "cli-claude", "flag": "flag{bb}"}
    s = extract_summary("blackboard.delta", payload)
    assert "flag" in s.lower()


def test_worker_status_online_summary():
    payload = {"online": True, "engine": "claude", "reason": "started"}
    s = extract_summary("worker.status", payload)
    assert "online" in s.lower() or "worker" in s.lower()


def test_worker_status_offline_summary():
    payload = {"online": False, "engine": "codex", "reason": "finished"}
    s = extract_summary("worker.status", payload)
    assert "offline" in s.lower() or "worker" in s.lower()


# ── MAPPED_EVENT_TYPES completeness ───────────────────────────────────────────

def test_mapped_event_types_contains_key_types():
    """The mapping must include all critical run-lifecycle event types."""
    for key in ("run.started", "run.finished", "worker.finished",
                "insight.event", "sharedgraph.delta"):
        assert key in MAPPED_EVENT_TYPES, f"Missing mapping for {key!r}"


def test_worker_finished_maps_to_worker_level_type():
    """worker.finished must map to a worker-level (not run-level) type string."""
    mapped = MAPPED_EVENT_TYPES["worker.finished"]
    # Must not claim this is a run-level completion
    assert "run" not in mapped.lower() or "run.finished" != mapped
    assert "worker" in mapped.lower()
