"""Stagnation detection from ScoreReport history.

This module is read-only and purely computational. It does not execute
anything, modify any model, call Muteki, or generate strategies.

Stagnation means the investigation is no longer making meaningful progress
across recent iterations. A single unsuccessful iteration is never stagnated.
Solved iterations are never stagnated.
"""
from __future__ import annotations

from typing import Sequence

from app.models import ScoreReport

from .config import DEFAULT_CONFIG, EvaluatorConfig


def detect_stagnation(
    history: Sequence[ScoreReport],
    config: EvaluatorConfig = DEFAULT_CONFIG,
) -> bool:
    """Return True if recent score history indicates investigation stagnation.

    Requires at least `config.no_progress_window` prior ScoreReports.
    Never returns True if any recent report has solved=True.

    Stagnation is detected when ALL of the following hold over the window:
      - No report has solved=True.
      - Score movement is below `config.meaningful_progress_delta`.
      - All reports share the same progress_level (same level = no qualitative
        progress), OR all reports already had stagnated=True.

    Args:
        history: ScoreReports from previous iterations (oldest first).
                 The *current* result's ScoreReport is NOT included — the
                 evaluator detects stagnation in the *prior* history and
                 includes that signal in the current report.
        config:  Scoring configuration; uses DEFAULT_CONFIG if not provided.

    Returns:
        True if the prior history indicates stagnation, False otherwise.
    """
    window = config.no_progress_window
    if len(history) < window:
        return False

    recent = list(history[-window:])

    # Never stagnate if any recent iteration solved the investigation
    if any(report.solved for report in recent):
        return False

    # If every recent report already marked itself stagnated, propagate that
    if all(report.stagnated for report in recent):
        return True

    scores = [report.progress_score for report in recent]
    score_delta = max(scores) - min(scores)

    levels = [report.progress_level for report in recent]
    same_level = len(set(levels)) == 1

    # Stagnated: negligible score movement AND no qualitative level progress
    return score_delta < config.meaningful_progress_delta and same_level
