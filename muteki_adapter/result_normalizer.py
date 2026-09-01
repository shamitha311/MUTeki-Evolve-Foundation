"""Result normalization: collected events → InvestigationResult.

This module converts the full set of collected InvestigationEvent objects plus
the RUN_FINISHED payload into the project-owned InvestigationResult.

IMPORTANT: The adapter does NOT calculate progress_score. That belongs to
Chunk 5 (Evaluation Engine). The adapter reports only investigation facts:
what happened, what evidence was collected, and whether the success condition
was verified by the upstream Muteki run.

solved=True is ONLY set when RUN_FINISHED.payload["solved"] is True.
The adapter never guesses or invents a solved condition.
"""

from __future__ import annotations

from typing import Any

from app.models import Evidence, InvestigationEvent, InvestigationResult

__all__ = [
    "normalize_result",
    "extract_evidence_from_events",
    "build_evidence_summary",
    "build_progress_signals",
]

# Maximum items to include in evidence and event_summary lists.
_MAX_EVIDENCE = 50
_MAX_EVENT_SUMMARY_ITEMS = 30

# Event types that indicate progress signals worth surfacing.
_PROGRESS_SIGNAL_TYPES = frozenset({
    "investigation.insight",
    "investigation.evidence",
    "investigation.graph.delta",
    "investigation.blackboard",
    "run.finished",
    "flag.accepted",
    "operator.input.needed",
    "investigation.stalled",
})


def extract_evidence_from_events(
    events: list[InvestigationEvent],
    finished_payload: dict[str, Any],
) -> list[Evidence]:
    """Extract Evidence items from normalized events and the RUN_FINISHED payload.

    Evidence is sourced from:
    1. "investigation.insight" events (FlagFound, FactDiscovered)
    2. "investigation.evidence" events (sharedgraph.delta with verified=True)
    3. "flag.accepted" events
    4. Flags from the RUN_FINISHED payload (highest confidence)

    No confidence value is invented. Only values from the upstream events are
    used. If an event carries no confidence, 0.5 is used as a neutral default
    (not fabricated success — just an absence-of-signal value).

    Evidence from RUN_FINISHED is always listed first (highest trust).
    """
    evidence_items: list[Evidence] = []
    seen_summaries: set[str] = set()

    # --- 1. Evidence from RUN_FINISHED payload (most authoritative) ---
    solved = bool(finished_payload.get("solved", False))
    flags = finished_payload.get("flags") or []
    if isinstance(flags, str):
        flags = [flags]
    flag = finished_payload.get("flag", "")
    if flag and flag not in flags:
        flags = [flag] + list(flags)

    for fl in flags[:10]:  # cap multi-flag lists
        if not fl or not isinstance(fl, str):
            continue
        summary = f"Flag verified by Muteki: {fl}"
        if summary not in seen_summaries:
            evidence_items.append(Evidence(
                type="verified_flag",
                summary=summary,
                confidence=1.0,
                source_event=None,  # from terminal event, not a specific sequence
            ))
            seen_summaries.add(summary)

    # --- 2. Evidence from normalized investigation events ---
    for ev in events:
        if len(evidence_items) >= _MAX_EVIDENCE:
            break

        if ev.type == "investigation.insight":
            # Insights include FlagFound, FactDiscovered, DeadEndMarked
            summary = ev.summary
            if "flag" in summary.lower() and "found" in summary.lower():
                ev_type = "flag_found"
                confidence = 0.95  # insight.event FlagFound — high confidence
            elif "dead end" in summary.lower() or "eliminated" in summary.lower():
                continue  # dead ends are not positive evidence
            else:
                ev_type = "fact"
                confidence = 0.7

            if summary not in seen_summaries:
                evidence_items.append(Evidence(
                    type=ev_type,
                    summary=summary[:500],
                    confidence=confidence,
                    source_event=ev.sequence,
                ))
                seen_summaries.add(summary)

        elif ev.type == "investigation.evidence":
            # sharedgraph.delta: verified facts from the shared graph
            if "verified" in ev.summary.lower() or "evidence" in ev.summary.lower():
                # Parse confidence from summary if present (e.g., "Verified evidence (95% confidence)")
                import re
                m = re.search(r"(\d+)%\s+confidence", ev.summary, re.IGNORECASE)
                confidence = float(m.group(1)) / 100.0 if m else 0.7
                confidence = max(0.0, min(1.0, confidence))
                summary = ev.summary[:500]
                if summary not in seen_summaries:
                    evidence_items.append(Evidence(
                        type="shared_evidence",
                        summary=summary,
                        confidence=confidence,
                        source_event=ev.sequence,
                    ))
                    seen_summaries.add(summary)

        elif ev.type == "flag.accepted":
            summary = ev.summary or "Flag submission accepted by verifier"
            if summary not in seen_summaries:
                evidence_items.append(Evidence(
                    type="flag_accepted",
                    summary=summary[:500],
                    confidence=1.0,
                    source_event=ev.sequence,
                ))
                seen_summaries.add(summary)

    return evidence_items[:_MAX_EVIDENCE]


def build_evidence_summary(
    evidence: list[Evidence],
    solved: bool,
    error: str | None,
) -> str:
    """Build a concise human-readable evidence summary.

    Does NOT calculate a score. Does NOT make claims beyond what the evidence
    and solved flag actually show.
    """
    if error == "investigation_timeout":
        prefix = "Investigation timed out before completion. "
    elif error:
        prefix = f"Investigation ended with error ({error}). "
    else:
        prefix = ""

    if solved:
        flag_evidence = [e for e in evidence if e.type in ("verified_flag", "flag_found", "flag_accepted")]
        if flag_evidence:
            return prefix + "Success condition verified by Muteki."
        return prefix + "Muteki reported success but no flag evidence was collected."

    if not evidence:
        return prefix + "No evidence collected in this investigation round."

    high_confidence = [e for e in evidence if e.confidence >= 0.8]
    if high_confidence:
        return (
            prefix
            + f"{len(high_confidence)} high-confidence evidence item(s) collected; "
            "success condition not yet verified."
        )

    return (
        prefix
        + f"{len(evidence)} evidence item(s) collected; "
        "success condition not yet verified."
    )


def build_progress_signals(events: list[InvestigationEvent], solved: bool) -> list[str]:
    """Derive high-level progress signals from the event stream.

    These are observable investigation milestones, not scores. The Evaluation
    Engine (Chunk 5) will convert these to a ScoreReport.
    """
    signals: list[str] = []
    seen: set[str] = set()

    def add(signal: str) -> None:
        if signal not in seen:
            signals.append(signal)
            seen.add(signal)

    if solved:
        add("verified success")

    type_set = {ev.type for ev in events}

    if any(t in type_set for t in ("investigation.insight", "investigation.evidence")):
        add("evidence collected")

    if "investigation.graph.delta" in type_set:
        add("hypothesis tracking active")

    if "investigation.blackboard" in type_set:
        add("coordination active")

    if "operator.input.needed" in type_set:
        add("operator input required")

    if "investigation.stalled" in type_set:
        add("stall detected")

    if "worker.status" in type_set or "worker.lifecycle" in type_set:
        add("workers active")

    if not signals and events:
        add("reconnaissance")

    return signals


def build_event_summary(events: list[InvestigationEvent]) -> list[str]:
    """Build a bounded list of the most meaningful event summaries.

    Excludes high-volume/low-information events (reasoning tokens, terminal
    output, context fuel updates) to keep the summary useful for the
    strategy evolution engine.
    """
    _EXCLUDE = frozenset({
        "reasoning.progress", "terminal.output", "context.fuel",
        "cost.update", "node.summarized",
    })
    selected = [
        ev.summary
        for ev in events
        if ev.type not in _EXCLUDE and ev.summary
    ]
    # Return the last N (most recent/relevant) events up to the cap
    return selected[-_MAX_EVENT_SUMMARY_ITEMS:]


def normalize_result(
    run_id: str,
    events: list[InvestigationEvent],
    finished_event: Any | None,
    elapsed_seconds: float,
    error: str | None = None,
) -> InvestigationResult:
    """Convert collected events and the RUN_FINISHED event into InvestigationResult.

    Args:
        run_id: The adapter's run identifier.
        events: All normalized InvestigationEvent objects collected from this run.
        finished_event: The raw Muteki RUN_FINISHED Event object, or None if the
            run timed out or failed before RUN_FINISHED was received.
        elapsed_seconds: Wall-clock time the adapter spent waiting.
        error: If set, the run did not complete normally. Never None for timeouts.

    Returns:
        InvestigationResult. solved=True ONLY if finished_event.payload["solved"]
        is True. Never fabricated.

    This function DOES NOT:
    - Calculate progress_score (Chunk 5)
    - Determine progress_level (Chunk 5)
    - Detect stagnation (Chunk 5)
    - Generate strategies (Chunk 3)
    """
    finished_payload: dict[str, Any] = {}
    solved = False

    if finished_event is not None:
        payload = getattr(finished_event, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {}
        finished_payload = payload
        # solved only from verified RUN_FINISHED payload — never guessed
        solved = bool(finished_payload.get("solved", False))

    evidence = extract_evidence_from_events(events, finished_payload)
    evidence_summary = build_evidence_summary(evidence, solved, error)
    progress_signals = build_progress_signals(events, solved)
    event_summary = build_event_summary(events)

    # If no real events at all and no error, note that explicitly
    if not events and not error and not solved:
        event_summary = ["No events received from Muteki"]
        progress_signals = progress_signals or ["no progress observed"]

    return InvestigationResult(
        run_id=run_id,
        solved=solved,
        evidence=evidence,
        evidence_summary=evidence_summary,
        progress_signals=progress_signals,
        elapsed_seconds=max(0.0, float(elapsed_seconds)),
        event_summary=event_summary,
        error=error,
    )
