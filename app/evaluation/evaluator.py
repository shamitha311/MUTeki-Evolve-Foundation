"""Top-level Evaluation Engine for Chunk 5.

This module provides the canonical evaluate() function that converts a
normalized InvestigationResult (and optional score history) into a ScoreReport.

Architecture position:
    Muteki Adapter
        ↓
    InvestigationResult
        ↓
    evaluate()          ← this module
        ↓
    ScoreReport
        ↓
    Strategy Memory / Strategy Evolution Engine

Guarantees:
    - Read-only: no command execution, no Muteki calls, no target selection,
      no strategy generation, no runtime_reference modification.
    - Deterministic: identical (result, history, config) → identical ScoreReport.
    - Evidence-based: scores reward meaningful investigation progress,
      not activity count or elapsed time.
    - Independent solved semantics: solved is never inferred from the score.
"""
from __future__ import annotations

from typing import Sequence

from app.models import InvestigationResult, ScoreReport

from .config import DEFAULT_CONFIG, EvaluatorConfig
from .evidence_analyzer import analyze_evidence
from .scorer import (
    build_reasons,
    calculate_progress_score,
    determine_progress_level,
    determine_solved,
)
from .stagnation import detect_stagnation
from .validators import normalize_result


def evaluate(
    investigation_result: InvestigationResult,
    history: Sequence[ScoreReport] | None = None,
    config: EvaluatorConfig | None = None,
) -> ScoreReport:
    """Convert an InvestigationResult into a ScoreReport.

    Args:
        investigation_result:
            The normalized result produced by the Muteki Adapter (or mock).
            Mock and real results are indistinguishable as long as they
            conform to the project-owned InvestigationResult contract.

        history:
            Optional sequence of ScoreReports from prior iterations,
            oldest first. Used only for stagnation detection.
            The current result is NOT included in this sequence.

        config:
            Optional scoring configuration. Uses DEFAULT_CONFIG when None.

    Returns:
        ScoreReport with:
            progress_score  — 0-100, investigation progress (not % solved)
            solved          — True only when InvestigationResult.solved is True
            progress_level  — human-readable level string (UI-compatible)
            reasons         — factual, deterministic explanation of the score
            stagnated       — True if prior history shows no meaningful progress

    The same inputs always produce the same ScoreReport (deterministic).
    """
    cfg = config or DEFAULT_CONFIG
    prior_scores: list[ScoreReport] = list(history or [])

    # Step 1: Normalize — clamp invalid confidence, drop irrecoverable items
    result = normalize_result(investigation_result)

    # Step 2: Analyze evidence quality and deduplication
    analysis = analyze_evidence(result.evidence)

    # Step 3: Compute progress score (0–100)
    score = calculate_progress_score(result, analysis, cfg)

    # Step 4: Determine solved — ONLY from InvestigationResult.solved, never from score
    solved = determine_solved(result)

    # Step 5: Determine progress level (UI-compatible string)
    level = determine_progress_level(score, solved)

    # Step 6: Build explanatory reasons
    reasons = build_reasons(result, analysis, score, level)

    # Step 7: Detect stagnation from prior history
    # Solved iterations are never stagnated.
    stagnated = (
        False
        if solved
        else detect_stagnation(prior_scores, cfg)
    )

    return ScoreReport(
        progress_score=score,
        solved=solved,
        progress_level=level,
        reasons=reasons,
        stagnated=stagnated,
    )
