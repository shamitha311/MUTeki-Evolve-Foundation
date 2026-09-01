"""Deterministic, high-level strategy generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.models import InvestigationResult, ScoreReport, Strategy
from app.validation import validate_strategy

from .deduplication import is_duplicate
from .memory import (
    StrategyMemory,
    StrategyMemoryRecord,
    derive_direction_outcomes,
    history_records,
    record_text,
)
from .reviewer import Review, review_history


class StrategyDiversificationRequired(RuntimeError):
    """Controlled failure when no safe, non-duplicate alternative exists."""


def _progressed(
    previous: Strategy,
    result: InvestigationResult,
    score: ScoreReport,
    records: Sequence[StrategyMemoryRecord],
) -> bool:
    if result.solved or score.solved or result.evidence:
        return True
    if not records:
        return bool(result.progress_signals)
    return score.progress_score > records[-1].score_report.progress_score


def _requested_priorities(
    previous: Strategy,
    result: InvestigationResult,
    score: ScoreReport,
    review: Review,
    records: Sequence[StrategyMemoryRecord],
) -> tuple[str, ...]:
    text = record_text(result, score).casefold()
    if result.solved or score.solved:
        return ("verification", "clear success evidence")

    if review.stagnated:
        choices = review.unexplored_directions
        if choices:
            return tuple(choices[:2])

    if "authentication" in text:
        return ("authentication", "session handling")
    if "authorization" in text:
        return ("authorization", "input validation")

    if score.progress_score >= 70 or any(
        evidence.confidence >= 0.75 for evidence in result.evidence
    ):
        return ("verification", "clear success evidence")

    if _progressed(previous, result, score, records):
        previous_keys = " ".join(previous.priorities).casefold()
        if "reconnaissance" in previous_keys or "surface" in previous_keys:
            return ("evidence correlation", "hypothesis testing")
        if "evidence" in previous_keys:
            return ("hypothesis testing", "deeper surface analysis")
        if review.successful_directions:
            return (
                review.successful_directions[0],
                "deeper surface analysis",
            )

    if review.unexplored_directions:
        return tuple(review.unexplored_directions[:2])
    return ("deeper surface analysis", "verification")


def _context_for_next_strategy(
    previous: Strategy,
    result: InvestigationResult,
    score: ScoreReport,
    review: Review,
) -> dict[str, Any]:
    context = dict(previous.context)
    context.update(
        {
            "previous_score": score.progress_score,
            "previous_progress_level": score.progress_level,
            "evidence_summary": result.evidence_summary,
            "progress_signals": list(result.progress_signals),
            "review": {
                "summary": review.summary,
                "successful_directions": list(review.successful_directions),
                "failed_directions": list(review.failed_directions),
                "unexplored_directions": list(review.unexplored_directions),
                "recommendation": review.recommendation,
                "stagnated": review.stagnated,
            },
        }
    )
    return context


class StrategyEngine:
    """Application-level strategy evolution component.

    The optional seed is retained as an explicit configuration point. The
    default rule-based implementation does not use uncontrolled randomness;
    ordering is stable for every seed and input.
    """

    def __init__(self, *, seed: int = 0, stagnation_threshold: int = 2) -> None:
        self.seed = seed
        self.stagnation_threshold = stagnation_threshold

    def generate_initial_strategy(
        self,
        objective: str,
        priorities: Sequence[str],
        constraints: Sequence[str],
        context: Mapping[str, Any] | None = None,
    ) -> Strategy:
        strategy = validate_strategy(
            {
                "objective": objective,
                "priorities": list(priorities),
                "constraints": list(constraints),
                "context": dict(context or {}),
                "revision": 1,
                "parent_revision": None,
            }
        )
        return strategy

    def generate_next_strategy(
        self,
        previous_strategy: Strategy,
        investigation_result: InvestigationResult,
        score_report: ScoreReport,
        history: StrategyMemory | Sequence[StrategyMemoryRecord] = (),
    ) -> Strategy:
        previous = validate_strategy(previous_strategy)
        records = history_records(history)
        current_success, current_failed = derive_direction_outcomes(
            previous,
            investigation_result,
            score_report,
            records,
        )
        current_record = StrategyMemoryRecord(
            iteration=len(records) + 1,
            strategy=previous,
            investigation_result=investigation_result,
            score_report=score_report,
            successful_directions=current_success,
            failed_directions=current_failed,
        )
        review = review_history(
            (*records, current_record),
            stagnation_threshold=self.stagnation_threshold,
        )
        if review.stagnated and not review.unexplored_directions:
            raise StrategyDiversificationRequired(
                "strategy generation requires diversification; no unexplored "
                "direction was available"
            )
        priorities = _requested_priorities(
            previous,
            investigation_result,
            score_report,
            review,
            (*records, current_record),
        )

        context = _context_for_next_strategy(
            previous,
            investigation_result,
            score_report,
            review,
        )
        payload = {
            "objective": previous.objective,
            "priorities": list(priorities),
            "constraints": list(previous.constraints),
            "context": context,
            "revision": previous.revision + 1,
            "parent_revision": previous.revision,
        }
        candidate = validate_strategy(payload)

        seen = tuple(records) + (
            StrategyMemoryRecord(
                iteration=len(records) + 1,
                strategy=previous,
                investigation_result=investigation_result,
                score_report=score_report,
                successful_directions=current_success,
                failed_directions=current_failed,
            ),
        )
        if not is_duplicate(candidate, seen):
            return candidate

        alternatives = [
            direction
            for direction in review.unexplored_directions
            if direction.casefold() not in {item.casefold() for item in priorities}
        ]
        for alternative in alternatives:
            alternate = validate_strategy(
                {
                    **payload,
                    "priorities": [alternative, "verification"],
                    "context": {
                        **context,
                        "diversification": f"avoid duplicate direction: {alternative}",
                    },
                }
            )
            if not is_duplicate(alternate, seen):
                return alternate

        raise StrategyDiversificationRequired(
            "strategy generation requires diversification; no safe non-duplicate "
            "candidate was available"
        )

    def record_iteration(
        self,
        memory: StrategyMemory,
        strategy: Strategy,
        investigation_result: InvestigationResult,
        score_report: ScoreReport,
    ) -> StrategyMemoryRecord:
        return memory.record_iteration(strategy, investigation_result, score_report)

    def review_history(
        self,
        history: StrategyMemory | Sequence[StrategyMemoryRecord],
    ) -> Review:
        return review_history(
            history,
            stagnation_threshold=self.stagnation_threshold,
        )

    def is_duplicate(
        self,
        strategy: Strategy,
        history: StrategyMemory | Sequence[StrategyMemoryRecord],
    ) -> bool:
        return is_duplicate(strategy, history)


def generate_initial_strategy(
    objective: str,
    priorities: Sequence[str],
    constraints: Sequence[str],
    context: Mapping[str, Any] | None = None,
) -> Strategy:
    return StrategyEngine().generate_initial_strategy(
        objective,
        priorities,
        constraints,
        context,
    )


def generate_next_strategy(
    previous_strategy: Strategy,
    investigation_result: InvestigationResult,
    score_report: ScoreReport,
    history: StrategyMemory | Sequence[StrategyMemoryRecord] = (),
) -> Strategy:
    return StrategyEngine().generate_next_strategy(
        previous_strategy,
        investigation_result,
        score_report,
        history,
    )