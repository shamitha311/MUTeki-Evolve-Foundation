"""Progress score calculation, level determination, and reason building.

This module is read-only and purely computational. It does not execute
anything, modify any model, call Muteki, or generate strategies.

Score semantics
---------------
progress_score is a 0-100 investigation progress measure.
It is NOT a percentage-solved claim. A score of 60 means:
  "the investigation has made substantial progress"
NOT:
  "60% of the vulnerability is solved"

Solved semantics
----------------
solved=True only when InvestigationResult.solved is True.
A high progress_score never implies solved.

Progress levels (STOP CONDITION 5: must match UI display strings)
------------------------------------------------------------------
The UI (artifacts/muteki-evolve/src/lib/replay.ts) already uses:
  "reconnaissance", "strong evidence", "verified success"
These must remain unchanged. The full set used here:
  "started"          — 0
  "reconnaissance"   — 1-35
  "partial evidence" — 36-59
  "strong evidence"  — 60-84
  "validated"        — 85-99
  "verified success" — 100 AND solved=True
"""
from __future__ import annotations

from app.models import InvestigationResult

from .config import DEFAULT_CONFIG, EvaluatorConfig
from .evidence_analyzer import EvidenceAnalysis


# ---------------------------------------------------------------------------
# Signal keyword tiers
# Each tier maps to a base score that represents the ceiling contribution
# from that class of progress signal.
# ---------------------------------------------------------------------------
_VERIFIED_KEYWORDS = frozenset(
    {
        "verified success",
        "verified",
        "success condition",
        "success",
        "verification",
        "resolved",
        "solved",
    }
)

_STRONG_KEYWORDS = frozenset(
    {
        "strong evidence",
        "strong",
        "correlated",
        "correlation",
        "hypothesis",
        "evidence correlation",
        "high confidence",
        "validated",
    }
)

_RECON_KEYWORDS = frozenset(
    {
        "reconnaissance",
        "surface",
        "recon",
        "observation",
        "attack surface",
        "surface map",
        "surface discovered",
        "initial",
        "useful recon",
    }
)

# Base scores for each keyword tier
_VERIFIED_BASE = 70.0
_STRONG_BASE = 50.0
_RECON_BASE = 18.0
_GENERIC_BASE = 5.0   # Any non-empty signal still shows some activity


def _signal_base_score(signals: list[str]) -> float:
    """Return the best base score contributed by progress_signals.

    Generic signals ("worker started", "command executed") score very low.
    Meaningful investigation signals score proportionally higher.
    The maximum across all signals is taken — having more signals of the
    same tier does not stack.
    """
    if not signals:
        return 0.0

    best = 0.0
    for signal in signals:
        normalized = signal.strip().lower()
        if any(kw in normalized for kw in _VERIFIED_KEYWORDS):
            best = max(best, _VERIFIED_BASE)
        elif any(kw in normalized for kw in _STRONG_KEYWORDS):
            best = max(best, _STRONG_BASE)
        elif any(kw in normalized for kw in _RECON_KEYWORDS):
            best = max(best, _RECON_BASE)
        elif normalized:
            best = max(best, _GENERIC_BASE)
    return best


def calculate_progress_score(
    result: InvestigationResult,
    analysis: EvidenceAnalysis,
    config: EvaluatorConfig = DEFAULT_CONFIG,
) -> float:
    """Compute a 0-100 investigation progress score.

    The score reflects quality of investigation progress — not a percentage
    solved, not worker count, not event count.

    Scoring model:
        signal_base          (0 – 70)  from progress_signals keyword tier
        + evidence_contribution (0 – 30) from unique evidence quality × confidence
        + diversity_bonus      (0 –  5)  from multiple evidence types
        ---
        clamped to [0.0, 100.0]

    Hard override: if result.solved is True, returns 100.0 immediately.
    """
    if result.solved:
        return 100.0

    # 1. Signal-based base score (meaningful signals only)
    signal_base = _signal_base_score(list(result.progress_signals))

    # 2. Evidence contribution: weighted by type quality × confidence, capped
    evidence_contribution = min(
        analysis.weighted_contribution * config.evidence_per_item_scale,
        config.max_evidence_contribution,
    )

    # 3. Diversity bonus: multiple evidence types indicate broader investigation
    diversity = min(
        len(analysis.unique_types) * config.diversity_per_type,
        config.max_diversity_bonus,
    )

    total = signal_base + evidence_contribution + diversity
    return max(0.0, min(100.0, total))


def determine_progress_level(score: float, solved: bool) -> str:
    """Map (score, solved) to a human-readable progress level string.

    The level strings must remain compatible with the UI display logic
    (STOP CONDITION 5). Do not rename or add levels without updating
    the UI contract.

    Levels:
        "verified success" — solved=True (any score)
        "validated"        — 85 ≤ score ≤ 99
        "strong evidence"  — 60 ≤ score ≤ 84
        "partial evidence" — 36 ≤ score ≤ 59
        "reconnaissance"   —  1 ≤ score ≤ 35
        "started"          — score == 0
    """
    if solved:
        return "verified success"
    if score <= 0.0:
        return "started"
    if score <= 35.0:
        return "reconnaissance"
    if score <= 59.0:
        return "partial evidence"
    if score <= 84.0:
        return "strong evidence"
    # 85–99 (solved=False is already checked above)
    return "validated"


def determine_solved(result: InvestigationResult) -> bool:
    """Solved is preserved directly from InvestigationResult.

    This function is the canonical place where solved semantics are enforced:
    the evaluator NEVER infers solved from score, confidence, or evidence count.
    Only an explicit InvestigationResult.solved=True is accepted.
    """
    return bool(result.solved)


def build_reasons(
    result: InvestigationResult,
    analysis: EvidenceAnalysis,
    score: float,
    level: str,
) -> list[str]:
    """Build deterministic, factual reasons explaining the score assignment.

    Reasons are derived only from the InvestigationResult and EvidenceAnalysis.
    No language generation is used. The same inputs always produce the same reasons.
    """
    reasons: list[str] = []

    # --- Verified success (short-circuit) ---
    if result.solved:
        reasons.append("Success condition verified.")
        if analysis.unique_evidence:
            reasons.append(
                f"Verified by {len(analysis.unique_evidence)} evidence item(s)."
            )
        return reasons

    # --- Error / timeout ---
    if result.error:
        reasons.append(f"Investigation ended with an error: {result.error}")

    # --- Progress signals ---
    if result.progress_signals:
        signals_text = ", ".join(result.progress_signals)
        reasons.append(f"Progress signals observed: {signals_text}.")
    else:
        reasons.append("No progress signals were provided.")

    # --- Evidence ---
    if not analysis.unique_evidence:
        reasons.append("No meaningful evidence was collected.")
    else:
        if analysis.duplicate_count > 0:
            reasons.append(
                f"{analysis.duplicate_count} duplicate evidence item(s) excluded "
                f"from scoring."
            )
        reasons.append(
            f"{len(analysis.unique_evidence)} unique evidence item(s) "
            f"contributed to the score."
        )
        if analysis.has_high_confidence:
            reasons.append(
                "At least one evidence item has high confidence (\u2265 0.75)."
            )
        if analysis.has_verified_success:
            # Evidence mentions success/verified but InvestigationResult.solved is False
            reasons.append(
                "Evidence contains verified or success signals, but "
                "InvestigationResult.solved is False — success is not confirmed."
            )

    # --- Evidence summary (context, not fabricated) ---
    if result.evidence_summary:
        reasons.append(f"Evidence summary: {result.evidence_summary}")

    # --- Level-specific explanation ---
    _LEVEL_REASONS: dict[str, str] = {
        "started": (
            "Investigation has not yet produced meaningful evidence or signals."
        ),
        "reconnaissance": (
            "Initial reconnaissance is underway; success has not been verified."
        ),
        "partial evidence": (
            "Partial evidence collected; further investigation is needed."
        ),
        "strong evidence": (
            "Strong evidence supports a leading hypothesis; verification is still required."
        ),
        "validated": (
            "High investigation progress achieved; the success condition has not "
            "been explicitly verified."
        ),
    }
    if level in _LEVEL_REASONS:
        reasons.append(_LEVEL_REASONS[level])

    # --- Universal unsolved reminder ---
    reasons.append("Success condition has not been verified.")

    return reasons
