"""Small, deterministic strategy memory for the evolution engine."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from app.models import InvestigationResult, ScoreReport, Strategy
from app.validation import validate_strategy


_STOP_WORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "using",
    "with",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if token not in _STOP_WORDS
    }


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return tuple(result)


def direction_matches(direction: str, text: str) -> bool:
    """Return whether a direction is represented in normalized result text."""

    direction_tokens = _tokens(direction)
    text_tokens = _tokens(text)
    if not direction_tokens:
        return False
    if len(direction_tokens) == 1:
        return bool(direction_tokens & text_tokens)
    return direction_tokens <= text_tokens


def record_text(result: InvestigationResult, score: ScoreReport) -> str:
    """Build the bounded text surface used for deterministic direction analysis."""

    evidence_text = " ".join(
        f"{item.type} {item.summary}" for item in result.evidence
    )
    return " ".join(
        [
            result.evidence_summary,
            " ".join(result.progress_signals),
            " ".join(result.event_summary),
            evidence_text,
            score.progress_level,
            " ".join(score.reasons),
            result.error or "",
        ]
    )


@dataclass(frozen=True, slots=True)
class StrategyMemoryRecord:
    """One completed iteration and the evidence used to evolve from it."""

    iteration: int
    strategy: Strategy
    investigation_result: InvestigationResult
    score_report: ScoreReport
    successful_directions: tuple[str, ...]
    failed_directions: tuple[str, ...]

    @property
    def result(self) -> InvestigationResult:
        return self.investigation_result

    @property
    def score(self) -> ScoreReport:
        return self.score_report

    @property
    def result_summary(self) -> str:
        return (
            self.investigation_result.evidence_summary
            or self.investigation_result.error
            or "No evidence summary was provided."
        )


def history_records(
    history: "StrategyMemory | Sequence[StrategyMemoryRecord]",
) -> tuple[StrategyMemoryRecord, ...]:
    """Normalize the public history inputs accepted by the engine."""

    if isinstance(history, StrategyMemory):
        return history.history()
    return tuple(history)


def derive_direction_outcomes(
    strategy: Strategy,
    result: InvestigationResult,
    score: ScoreReport,
    previous: Sequence[StrategyMemoryRecord] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Infer successful and failed high-level directions without scoring."""

    directions = tuple(strategy.priorities)
    text = record_text(result, score)
    previous_score = previous[-1].score_report.progress_score if previous else None
    score_improved = (
        previous_score is not None
        and score.progress_score > previous_score
    )
    has_positive_signal = bool(
        result.evidence
        or result.progress_signals
        or result.solved
        or score.solved
        or score_improved
    )

    successful = tuple(
        direction for direction in directions if direction_matches(direction, text)
    )
    if has_positive_signal and not successful and directions:
        successful = (directions[0],)

    stalled = (
        bool(result.error)
        or score.stagnated
        or (
            previous_score is not None
            and score.progress_score <= previous_score
            and not result.solved
            and not score.solved
        )
    )
    failed = tuple(direction for direction in directions if stalled and direction not in successful)
    return _unique(successful), _unique(failed)


class StrategyMemory:
    """An in-memory append-only history with a small configurable window."""

    def __init__(
        self,
        records: Sequence[StrategyMemoryRecord] = (),
        *,
        stagnation_threshold: int = 2,
        meaningful_progress_delta: float = 5.0,
    ) -> None:
        if stagnation_threshold < 2:
            raise ValueError("stagnation_threshold must be at least 2")
        if meaningful_progress_delta < 0:
            raise ValueError("meaningful_progress_delta must be non-negative")
        self._records = list(records)
        self.stagnation_threshold = stagnation_threshold
        self.meaningful_progress_delta = meaningful_progress_delta

    def record_iteration(
        self,
        strategy: Strategy,
        investigation_result: InvestigationResult,
        score_report: ScoreReport,
    ) -> StrategyMemoryRecord:
        approved_strategy = validate_strategy(strategy)
        successful, failed = derive_direction_outcomes(
            approved_strategy,
            investigation_result,
            score_report,
            self._records,
        )
        record = StrategyMemoryRecord(
            iteration=len(self._records) + 1,
            strategy=approved_strategy,
            investigation_result=investigation_result,
            score_report=score_report,
            successful_directions=successful,
            failed_directions=failed,
        )
        self._records.append(record)
        return record

    def history(self) -> tuple[StrategyMemoryRecord, ...]:
        return tuple(self._records)

    def latest_strategy(self) -> Strategy | None:
        return self._records[-1].strategy if self._records else None

    def latest_result(self) -> InvestigationResult | None:
        return self._records[-1].investigation_result if self._records else None

    def score_history(self) -> tuple[ScoreReport, ...]:
        return tuple(record.score_report for record in self._records)

    def successful_directions(self) -> tuple[str, ...]:
        return _unique(
            [
                direction
                for record in self._records
                for direction in record.successful_directions
            ]
        )

    def failed_directions(self) -> tuple[str, ...]:
        return _unique(
            [
                direction
                for record in self._records
                for direction in record.failed_directions
            ]
        )

    def repeated_failures(self, *, minimum_repetitions: int = 2) -> tuple[str, ...]:
        counts = Counter(
            direction
            for record in self._records
            for direction in record.failed_directions
        )
        return _unique(
            [
                direction
                for record in self._records
                for direction in record.failed_directions
                if counts[direction] >= minimum_repetitions
            ]
        )

    def is_stagnated(self, *, threshold: int | None = None) -> bool:
        window = threshold or self.stagnation_threshold
        if window < 2:
            raise ValueError("stagnation threshold must be at least 2")
        recent = self._records[-window:]
        if len(recent) < window or any(
            record.investigation_result.solved or record.score_report.solved
            for record in recent
        ):
            return False

        scores = [record.score_report.progress_score for record in recent]
        score_change = max(scores) - min(scores)
        summaries = [record.result_summary.casefold() for record in recent]
        same_strategy = len(
            {
                tuple(direction.casefold() for direction in record.strategy.priorities)
                for record in recent
            }
        ) == 1
        return (
            all(record.score_report.stagnated for record in recent)
            or all(record.investigation_result.error for record in recent)
            or (
                score_change < self.meaningful_progress_delta
                and (len(set(summaries)) == 1 or same_strategy)
            )
        )