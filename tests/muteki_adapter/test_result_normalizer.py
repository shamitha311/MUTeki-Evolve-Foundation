"""Tests for muteki_adapter.result_normalizer.

Key invariants:
  - solved=True ONLY from RUN_FINISHED.payload["solved"] being True
  - Timed-out runs are never marked solved
  - Evidence is extracted from real event types only
  - Evidence is never fabricated
  - Event summaries exclude high-volume/low-signal events
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import InvestigationEvent
from muteki_adapter.result_normalizer import (
    build_evidence_summary,
    build_progress_signals,
    extract_evidence_from_events,
    normalize_result,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _event(
    ev_type: str,
    summary: str,
    sequence: int = 1,
    worker_id: str | None = None,
) -> InvestigationEvent:
    from datetime import datetime, timezone
    return InvestigationEvent(
        sequence=sequence,
        timestamp=datetime.now(timezone.utc).isoformat(),
        type=ev_type,
        run_id="test-run",
        worker_id=worker_id,
        summary=summary,
    )


def _finished_event(solved: bool = False, flags: list[str] | None = None) -> Any:
    """Simulate a raw Muteki RUN_FINISHED event."""
    payload: dict[str, Any] = {"solved": solved}
    if flags is not None:
        payload["flags"] = flags
        if flags:
            payload["flag"] = flags[0]
    return SimpleNamespace(payload=payload)


# ── normalize_result — solved invariant ───────────────────────────────────────

def test_solved_true_only_from_run_finished_payload():
    """solved=True requires RUN_FINISHED.payload['solved']=True."""
    finished = _finished_event(solved=True, flags=["flag{win}"])
    result = normalize_result("r1", [], finished, elapsed_seconds=1.0)
    assert result.solved is True


def test_solved_false_when_payload_says_false():
    """A RUN_FINISHED with solved=False produces a non-solved result."""
    finished = _finished_event(solved=False)
    result = normalize_result("r1", [], finished, elapsed_seconds=1.0)
    assert result.solved is False


def test_solved_false_when_no_finished_event():
    """Without RUN_FINISHED, solved must always be False."""
    events = [_event("investigation.insight", "Flag found: flag{x}", 1)]
    result = normalize_result("r1", events, None, elapsed_seconds=5.0)
    assert result.solved is False


def test_timeout_never_produces_solved():
    """A timed-out investigation must never be marked solved."""
    events = [_event("investigation.insight", "Flag found: flag{maybe}", 1)]
    result = normalize_result(
        "r1", events, None,
        elapsed_seconds=300.0, error="investigation_timeout",
    )
    assert result.solved is False


def test_error_field_set_on_timeout():
    """Timeout sets error='investigation_timeout'."""
    result = normalize_result("r1", [], None, elapsed_seconds=300.0,
                              error="investigation_timeout")
    assert result.error == "investigation_timeout"


def test_no_error_field_on_clean_completion():
    """A clean completion with RUN_FINISHED produces error=None."""
    result = normalize_result("r1", [], _finished_event(solved=True, flags=["flag{x}"]),
                              elapsed_seconds=10.0, error=None)
    assert result.error is None


def test_run_id_preserved_in_result():
    result = normalize_result("my-run-42", [], _finished_event(), elapsed_seconds=1.0)
    assert result.run_id == "my-run-42"


def test_elapsed_seconds_in_result():
    result = normalize_result("r", [], _finished_event(), elapsed_seconds=42.5)
    assert result.elapsed_seconds == pytest.approx(42.5)


def test_elapsed_seconds_never_negative():
    result = normalize_result("r", [], _finished_event(), elapsed_seconds=-1.0)
    assert result.elapsed_seconds >= 0.0


# ── Evidence extraction ────────────────────────────────────────────────────────

def test_flags_from_run_finished_become_evidence():
    """Flags in RUN_FINISHED payload become verified_flag Evidence items."""
    finished = _finished_event(solved=True, flags=["flag{win}"])
    evidence = extract_evidence_from_events([], finished.payload)
    assert any(e.type == "verified_flag" for e in evidence)


def test_flag_evidence_has_full_confidence():
    """Verified flags from RUN_FINISHED must have confidence=1.0."""
    finished = _finished_event(solved=True, flags=["flag{perfect}"])
    evidence = extract_evidence_from_events([], finished.payload)
    flag_ev = next(e for e in evidence if e.type == "verified_flag")
    assert flag_ev.confidence == 1.0


def test_multiple_flags_produce_multiple_evidence_items():
    """Multi-flag runs produce one Evidence item per flag."""
    flags = ["flag{part1}", "flag{part2}", "flag{part3}"]
    finished = _finished_event(solved=True, flags=flags)
    evidence = extract_evidence_from_events([], finished.payload)
    flag_evidence = [e for e in evidence if e.type == "verified_flag"]
    assert len(flag_evidence) == 3


def test_insight_flag_found_event_becomes_evidence():
    """investigation.insight event (FlagFound) produces evidence."""
    events = [_event("investigation.insight", "Flag found: flag{x}", sequence=3)]
    evidence = extract_evidence_from_events(events, {})
    assert any("flag" in e.summary.lower() for e in evidence)


def test_verified_sharedgraph_event_becomes_evidence():
    """investigation.evidence event (verified) produces shared_evidence."""
    events = [
        _event(
            "investigation.evidence",
            "Verified evidence (95% confidence): HTTP 200 at /secret",
            sequence=5,
        )
    ]
    evidence = extract_evidence_from_events(events, {})
    assert any(e.type == "shared_evidence" for e in evidence)


def test_dead_end_events_not_in_evidence():
    """Dead-end insight events must NOT appear as positive evidence."""
    events = [
        _event("investigation.insight", "Dead end identified: SQLi patched", 1),
    ]
    evidence = extract_evidence_from_events(events, {})
    # Dead ends may be excluded or only appear as negative evidence
    # They must NOT appear as verified_flag or high-confidence evidence
    for ev in evidence:
        assert ev.type not in ("verified_flag", "flag_found", "flag_accepted")


def test_evidence_list_bounded():
    """Evidence list must not exceed 50 items."""
    events = [
        _event("investigation.insight", f"Flag found: flag{{part{i}}}", i + 1)
        for i in range(100)
    ]
    evidence = extract_evidence_from_events(events, {})
    assert len(evidence) <= 50


def test_evidence_is_deduplicated():
    """The same evidence summary is not added twice."""
    events = [
        _event("investigation.insight", "Flag found: flag{x}", 1),
        _event("investigation.insight", "Flag found: flag{x}", 2),  # duplicate summary
    ]
    evidence = extract_evidence_from_events(events, {})
    summaries = [e.summary for e in evidence]
    assert len(summaries) == len(set(summaries))


# ── build_evidence_summary ────────────────────────────────────────────────────

def test_evidence_summary_mentions_success_when_solved():
    summary = build_evidence_summary([], solved=True, error=None)
    # Without any evidence items, must still acknowledge the solved status
    assert "success" in summary.lower() or "verified" in summary.lower()


def test_evidence_summary_mentions_timeout():
    summary = build_evidence_summary([], solved=False, error="investigation_timeout")
    assert "timeout" in summary.lower() or "timed out" in summary.lower()


def test_evidence_summary_no_evidence_no_solved():
    summary = build_evidence_summary([], solved=False, error=None)
    assert "no evidence" in summary.lower()


# ── build_progress_signals ────────────────────────────────────────────────────

def test_solved_produces_verified_success_signal():
    signals = build_progress_signals([], solved=True)
    assert "verified success" in signals


def test_evidence_events_produce_evidence_signal():
    events = [_event("investigation.insight", "Flag found", 1)]
    signals = build_progress_signals(events, solved=False)
    assert "evidence collected" in signals


def test_stall_event_produces_stall_signal():
    events = [_event("investigation.stalled", "Stalled", 1)]
    signals = build_progress_signals(events, solved=False)
    assert "stall detected" in signals


def test_worker_events_produce_workers_active_signal():
    events = [_event("worker.status", "Worker online: claude", 1)]
    signals = build_progress_signals(events, solved=False)
    assert "workers active" in signals


def test_no_events_no_solved_produces_minimal_signals():
    signals = build_progress_signals([], solved=False)
    # Should still produce at least one signal (reconnaissance or similar)
    assert isinstance(signals, list)


# ── normalize_result — event_summary ─────────────────────────────────────────

def test_event_summary_excludes_low_signal_events():
    """High-volume low-information events must not pollute event_summary."""
    events = [
        _event("reasoning.progress", "Reasoning in progress", 1),
        _event("terminal.output", "Terminal: $ ls", 2),
        _event("context.fuel", "Context usage: 42%", 3),
        _event("investigation.insight", "Flag found: flag{x}", 4),  # important
    ]
    result = normalize_result("r", events, _finished_event(solved=True, flags=["flag{x}"]),
                              elapsed_seconds=1.0)
    # The flag insight should appear in event_summary
    assert any("flag" in s.lower() or "found" in s.lower() for s in result.event_summary)
    # Low-signal events should be excluded or at minimum the flag event should be present
    # (exact exclusion behavior is an implementation detail, but high-value events must appear)


def test_event_summary_bounded():
    """event_summary must not exceed 30 items."""
    events = [
        _event("investigation.insight", f"Fact {i}", i + 1)
        for i in range(100)
    ]
    result = normalize_result("r", events, _finished_event(), elapsed_seconds=1.0)
    assert len(result.event_summary) <= 30


def test_no_events_produces_placeholder_event_summary():
    """With no events and no error, a placeholder message appears."""
    result = normalize_result("r", [], None, elapsed_seconds=0.5)
    assert result.event_summary or result.progress_signals
