"""Input normalization and malformed result handling for the Evaluation Engine.

This module is read-only. It sanitizes InvestigationResult inputs so the
evaluator can handle edge cases (invalid confidence values, empty fields, etc.)
without crashing. It does not execute anything, call Muteki, or generate
strategies.

Design principle: fail safely. If a field cannot be normalized, use a
conservative default (e.g., 0.0 confidence) rather than raising.
"""
from __future__ import annotations

import math

from app.models import Evidence, InvestigationResult


def normalize_confidence(value: float) -> float:
    """Clamp a confidence value to [0.0, 1.0].

    Returns 0.0 for NaN, infinity, or any non-numeric input.
    """
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return 0.0


def _normalize_evidence_item(item: Evidence) -> Evidence | None:
    """Return a normalized Evidence item, or None if it is irrecoverable.

    Currently normalizes confidence. If confidence is already valid, returns
    the original item unchanged (no object creation overhead).
    """
    try:
        clamped = normalize_confidence(item.confidence)
        if clamped == item.confidence:
            return item
        return Evidence(
            type=item.type,
            summary=item.summary,
            confidence=clamped,
            source_event=item.source_event,
        )
    except Exception:  # noqa: BLE001
        return None


def normalize_result(result: InvestigationResult) -> InvestigationResult:
    """Return a safely normalized InvestigationResult for evaluation.

    Handles:
    - Invalid or out-of-range confidence values (clamped to [0.0, 1.0]).
    - Irrecoverably malformed evidence items (silently dropped).
    - All other fields are passed through unchanged.

    If no evidence items require modification, returns the original object
    unmodified for efficiency.
    """
    normalized_items: list[Evidence] = []
    changed = False

    for item in result.evidence:
        cleaned = _normalize_evidence_item(item)
        if cleaned is None:
            changed = True  # dropped a malformed item
            continue
        normalized_items.append(cleaned)
        if cleaned is not item:
            changed = True

    if not changed:
        return result

    return InvestigationResult(
        run_id=result.run_id,
        solved=result.solved,
        evidence=normalized_items,
        evidence_summary=result.evidence_summary,
        progress_signals=list(result.progress_signals),
        elapsed_seconds=result.elapsed_seconds,
        event_summary=list(result.event_summary),
        error=result.error,
    )
