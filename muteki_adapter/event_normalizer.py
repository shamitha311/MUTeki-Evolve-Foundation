"""Muteki Event → InvestigationEvent normalization.

This module translates Muteki's internal Event objects into the project-owned
InvestigationEvent contract. No Muteki-specific types are exposed outside this
module.

Source-verified Muteki event shape (from vendor/muteki/muteki/core/events.py):
  Event(
    event_type: EventType,   # e.g. "run.started", "insight.event"
    seq: int,                # monotonic sequence, assigned by EventBus
    ts: float,               # epoch seconds
    run_id: str,
    challenge_id: str | None,
    solver_id: str | None,   # the worker/solver that emitted this event
    payload: dict[str, Any],
  )

Completion rule (source-verified from WORKER_FINISHED comment in events.py):
  RUN_FINISHED is the ONLY run-level terminal event.
  WORKER_FINISHED is worker-level and must NOT be interpreted as run completion.
"""

from __future__ import annotations

import datetime
import time
from typing import Any

from app.models import InvestigationEvent

__all__ = [
    "normalize_event",
    "is_run_terminal",
    "extract_summary",
    "MAPPED_EVENT_TYPES",
]

# Mapping from Muteki EventType values to application-level event type strings.
# Only events that carry meaningful information for the application are mapped.
# Events not in this set are still normalized (with type=event_type.value) but
# may be filtered out if they add no application-level information.
MAPPED_EVENT_TYPES: dict[str, str] = {
    "run.preparing":    "run.preparing",
    "run.started":      "run.started",
    "run.finished":     "run.finished",
    "run.titled":       "run.titled",
    "run.reopened":     "run.reopened",
    "worker.status":    "worker.status",
    "worker.finished":  "worker.finished",   # worker-level, NOT run completion
    "worker.lifecycle": "worker.lifecycle",
    "insight.event":    "investigation.insight",
    "solvegraph.delta": "investigation.graph.delta",
    "sharedgraph.delta":"investigation.evidence",
    "blackboard.delta": "investigation.blackboard",
    "reasoning.delta":  "reasoning.progress",
    "tool.start":       "tool.start",
    "tool.args":        "tool.args",
    "tool.result":      "tool.result",
    "terminal.output":  "terminal.output",
    "context.state":    "context.fuel",
    "hitl.request":     "operator.input.needed",
    "hitl.response":    "operator.input.provided",
    "control.command":  "control.command",
    "guard.stalled":    "investigation.stalled",
    "coordinator.guidance": "coordinator.guidance",
    "cost.update":      "cost.update",
    "flag.accepted":    "flag.accepted",
    "reason.intent":    "reason.intent",
    "node.summarized":  "node.summarized",
}

# Events that provide no additional application-level signal beyond their
# type name and that are high-volume/low-information — normalizable but
# typically filtered by the result normalizer when building event_summary.
_LOW_SIGNAL_TYPES = frozenset({
    "reasoning.delta",   # individual LLM token chunks — too granular
    "terminal.output",   # raw PTY byte stream
    "context.state",     # fuel gauge updates — internal diagnostic
    "cost.update",       # cost tracking — internal
    "node.summarized",   # UI gist — internal
})

# The run-level terminal event (source-verified).
_RUN_FINISHED_TYPE = "run.finished"


def _epoch_to_iso(ts: float) -> str:
    """Convert an epoch-seconds float to an ISO-8601 UTC string."""
    try:
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        return dt.isoformat()
    except (OSError, OverflowError, ValueError):
        return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat()


def extract_summary(event_type_value: str, payload: dict[str, Any]) -> str:
    """Derive a human-readable, non-Muteki-specific summary from an event.

    This is the application's view of what happened — not a dump of raw
    Muteki internals.
    """
    et = event_type_value

    if et == "run.started":
        return "Investigation run started"

    if et == "run.preparing":
        return "Muteki preparing run environment"

    if et == "run.finished":
        solved = payload.get("solved", False)
        flags = payload.get("flags") or []
        flag = payload.get("flag", "")
        if solved and flags:
            return f"Investigation completed successfully ({len(flags)} flag(s) found)"
        if solved and flag:
            return "Investigation completed: success condition verified"
        reason = payload.get("reason", "")
        if reason:
            return f"Investigation finished: {reason}"
        return "Investigation run finished (no success condition verified)"

    if et == "run.reopened":
        return "Run was reopened to continue investigation"

    if et == "run.titled":
        title = payload.get("title", "")
        return f"Run titled: {title}" if title else "Run title updated"

    if et in ("worker.status", "worker.lifecycle"):
        online = payload.get("online")
        engine = payload.get("engine", "")
        reason = payload.get("reason", "")
        status = payload.get("status", "")
        phase = payload.get("phase", "")
        detail = reason or status or phase or ""
        if online is True:
            return f"Worker online: {engine} ({detail})".strip(": ")
        if online is False:
            return f"Worker offline: {engine} ({detail})".strip(": ")
        if phase:
            return f"Worker lifecycle: {engine} {phase}".strip()
        return f"Worker activity: {engine} {detail}".strip()

    if et == "worker.finished":
        # Worker-level completion — must NOT be read as run completion.
        solved = payload.get("solved", False)
        return (
            "Worker finished (run continues)"
            if not solved
            else "Worker reported success (awaiting run.finished confirmation)"
        )

    if et == "insight.event":
        kind = payload.get("kind", "")
        if kind == "FlagFound":
            flag = payload.get("flag", "")
            return f"Flag found: {flag}" if flag else "Flag discovered"
        if kind == "DeadEndMarked":
            text = payload.get("text", "")
            return f"Dead end identified: {text}" if text else "Investigation path eliminated"
        if kind == "FactDiscovered":
            text = payload.get("text", "") or payload.get("fact", "")
            return f"Fact discovered: {text}" if text else "New fact added to graph"
        return f"Investigation insight: {kind}" if kind else "Investigation insight"

    if et == "sharedgraph.delta":
        fact = payload.get("fact", "")
        verified = payload.get("verified", False)
        confidence = payload.get("confidence", 0.0)
        prefix = "Verified evidence" if verified else "Candidate evidence"
        if fact:
            return f"{prefix} ({confidence:.0%} confidence): {fact[:200]}"
        return f"{prefix} recorded"

    if et == "blackboard.delta":
        kind = payload.get("kind", "")
        goal = payload.get("goal", "")
        reason = payload.get("reason", "")
        flag = payload.get("flag", "")
        fact = payload.get("fact", "")
        if kind == "intent_proposed" and goal:
            return f"Investigation direction proposed: {goal[:200]}"
        if kind == "intent_claimed" and goal:
            return f"Worker claimed direction: {goal[:200]}"
        if kind == "intent_concluded":
            return "Investigation direction completed"
        if kind == "fact_added" and fact:
            return f"Blackboard fact added: {fact[:200]}"
        if kind == "dead_end" and reason:
            return f"Dead end: {reason[:200]}"
        if kind == "flag_found" and flag:
            return f"Flag recorded on blackboard: {flag}"
        return f"Blackboard update: {kind}" if kind else "Blackboard updated"

    if et == "solvegraph.delta":
        kind = payload.get("kind", "")
        fact = payload.get("fact", "")
        statement = payload.get("statement", "")
        if kind == "evidence_added" and fact:
            return f"Evidence: {fact[:200]}"
        if kind in ("hypothesis_added", "hypothesis_status") and statement:
            status = payload.get("status", "")
            return f"Hypothesis {status}: {statement[:200]}" if status else f"Hypothesis: {statement[:200]}"
        if kind == "dead_end":
            return "Dead-end path identified in solve graph"
        if kind == "flag":
            return "Flag node added to solve graph"
        return f"Solve graph update: {kind}" if kind else "Solve graph updated"

    if et == "hitl.request":
        need = payload.get("need", "")
        return f"Operator input required: {need[:200]}" if need else "Operator input required"

    if et == "hitl.response":
        action = payload.get("action", "")
        return f"Operator responded: {action}" if action else "Operator response received"

    if et == "control.command":
        action = payload.get("action", "")
        status = payload.get("status", "")
        return f"Control: {action} ({status})" if status else f"Control: {action}"

    if et == "guard.stalled":
        return "Investigation stalled — coordinator paused for review"

    if et == "coordinator.guidance":
        return "Coordinator guidance injected"

    if et == "flag.accepted":
        return "Flag submission accepted"

    if et == "reason.intent":
        goal_met = payload.get("goal_met", False)
        count = len(payload.get("intents") or [])
        return (
            "Planner: investigation goal met"
            if goal_met
            else f"Planner: {count} investigation direction(s) proposed"
        )

    if et == "reasoning.delta":
        # High-volume individual token chunks — provide a generic summary
        return "Reasoning in progress"

    if et == "terminal.output":
        text = (payload.get("text") or "")[:80].strip()
        return f"Terminal: {text}" if text else "Terminal output"

    if et == "context.state":
        total = payload.get("total", 0)
        limit = payload.get("limit", 0)
        if limit and total:
            pct = int(100 * total / limit)
            return f"Context usage: {pct}% ({total}/{limit} tokens)"
        return "Context state updated"

    if et == "cost.update":
        usd = payload.get("usd", 0.0)
        return f"Cost update: ${usd:.4f}" if usd else "Cost update"

    # Fallback for unmapped or future event types
    return f"Event: {et}"


def normalize_event(
    muteki_event: Any,
    *,
    run_id: str,
    sequence_counter: int,
) -> InvestigationEvent | None:
    """Translate one Muteki Event into a project-owned InvestigationEvent.

    Args:
        muteki_event: A muteki.core.events.Event instance.
        run_id: The adapter's run identifier (used if event.run_id is absent).
        sequence_counter: Monotonic adapter-level counter, used when
            muteki_event.seq is 0 or unavailable.

    Returns:
        InvestigationEvent, or None if the event is internal-only and carries
        no application-meaningful information (e.g., pure UI scaffolding).

    Note: Even filtered event types return None gracefully rather than raising,
    so the caller's event loop is never interrupted by a normalization edge case.
    """
    try:
        event_type_value: str = getattr(muteki_event, "event_type", None)
        if hasattr(event_type_value, "value"):
            # It's an EventType enum — get its string value
            event_type_value = event_type_value.value

        # Use Muteki's seq if non-zero, else use the adapter-level counter
        seq_raw = getattr(muteki_event, "seq", 0) or 0
        sequence = int(seq_raw) if seq_raw > 0 else sequence_counter
        # Ensure sequence is always at least 1
        sequence = max(1, sequence)

        ts_raw = getattr(muteki_event, "ts", 0.0) or 0.0
        timestamp = _epoch_to_iso(float(ts_raw)) if ts_raw else _now_iso()

        event_run_id = str(getattr(muteki_event, "run_id", "") or run_id or "unknown")

        solver_id = getattr(muteki_event, "solver_id", None)
        worker_id: str | None = str(solver_id) if solver_id else None

        payload: dict[str, Any] = getattr(muteki_event, "payload", {}) or {}

        mapped_type = MAPPED_EVENT_TYPES.get(
            event_type_value, event_type_value or "unknown"
        )

        summary = extract_summary(event_type_value or "", payload)

        return InvestigationEvent(
            sequence=sequence,
            timestamp=timestamp,
            type=mapped_type,
            run_id=event_run_id,
            worker_id=worker_id,
            summary=summary,
        )

    except Exception:  # noqa: BLE001
        # Normalization errors must never crash the event loop.
        # If we cannot normalize, we silently skip the event and let the
        # caller continue collecting remaining events.
        return None


def is_run_terminal(muteki_event: Any) -> bool:
    """Return True if this event signals run-level completion.

    Source-verified: ONLY RUN_FINISHED is a run-level terminal event.
    WORKER_FINISHED must NEVER be treated as run completion.
    """
    event_type = getattr(muteki_event, "event_type", None)
    if hasattr(event_type, "value"):
        event_type = event_type.value
    return str(event_type) == _RUN_FINISHED_TYPE
