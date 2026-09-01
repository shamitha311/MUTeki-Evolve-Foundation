"""Lightweight teacher/reviewer logic for strategy history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .memory import (
    StrategyMemory,
    StrategyMemoryRecord,
    _unique,
    direction_matches,
    history_records,
)


_DIRECTION_CATALOG = (
    "reconnaissance",
    "surface discovery",
    "evidence collection",
    "evidence correlation",
    "hypothesis testing",
    "authentication",
    "session handling",
    "authorization",
    "input validation",
    "deeper surface analysis",
    "verification",
    "clear success evidence",
)


def _direction_key(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass(frozen=True, slots=True)
class Review:
    summary: str
    successful_directions: tuple[str, ...]
    failed_directions: tuple[str, ...]
    unexplored_directions: tuple[str, ...]
    recommendation: str
    stagnated: bool


def _explored_directions(
    records: Sequence[StrategyMemoryRecord],
) -> tuple[str, ...]:
    return _unique(
        [
            direction
            for record in records
            for direction in (
                *record.strategy.priorities,
                *record.successful_directions,
                *record.failed_directions,
            )
        ]
    )


def _catalog_unexplored(
    records: Sequence[StrategyMemoryRecord],
    failed: Sequence[str],
) -> tuple[str, ...]:
    explored = {_direction_key(item) for item in _explored_directions(records)}
    failed_keys = {_direction_key(item) for item in failed}
    return tuple(
        direction
        for direction in _DIRECTION_CATALOG
        if _direction_key(direction) not in explored
        and _direction_key(direction) not in failed_keys
    )


def review_history(
    history: StrategyMemory | Sequence[StrategyMemoryRecord],
    *,
    stagnation_threshold: int = 2,
) -> Review:
    records = history_records(history)
    successful = _unique(
        [
            direction
            for record in records
            for direction in record.successful_directions
        ]
    )
    failed = _unique(
        [direction for record in records for direction in record.failed_directions]
    )
    if isinstance(history, StrategyMemory):
        stagnated = history.is_stagnated(threshold=stagnation_threshold)
    else:
        memory = StrategyMemory(
            records,
            stagnation_threshold=stagnation_threshold,
        )
        stagnated = memory.is_stagnated(threshold=stagnation_threshold)

    unexplored = _catalog_unexplored(records, failed)
    if not records:
        return Review(
            summary="No completed iterations are available for review.",
            successful_directions=(),
            failed_directions=(),
            unexplored_directions=_DIRECTION_CATALOG,
            recommendation="Begin with bounded reconnaissance and evidence collection.",
            stagnated=False,
        )

    latest = records[-1]
    if stagnated:
        first_new = unexplored[0] if unexplored else "a deeper analysis direction"
        summary = (
            "Recent iterations are not producing meaningful new progress; "
            "repeated directions should be diversified."
        )
        recommendation = f"Diversify toward {first_new}."
    elif successful:
        summary = (
            f"Progress is associated with {', '.join(successful)}; "
            "preserve those signals while narrowing the next investigation."
        )
        recommendation = (
            f"Deepen {successful[0]} and move toward the strongest "
            "unverified direction."
        )
    elif failed:
        summary = (
            f"The latest attempt did not advance the objective; "
            f"the weak directions are {', '.join(failed)}."
        )
        recommendation = (
            f"Reduce emphasis on {failed[0]} and choose an unexplored direction."
        )
    else:
        summary = (
            "The history contains a bounded result, but no direction has "
            "enough evidence to be called successful."
        )
        recommendation = "Collect a clearer signal before narrowing the strategy."

    if latest.investigation_result.error:
        summary = f"{summary} The latest iteration reported an error."

    return Review(
        summary=summary,
        successful_directions=successful,
        failed_directions=failed,
        unexplored_directions=unexplored,
        recommendation=recommendation,
        stagnated=stagnated,
    )