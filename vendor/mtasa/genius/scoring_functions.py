from __future__ import annotations

from dataclasses import dataclass


FIXED_SCORING_MODE = "official_like_latest"


@dataclass
class ScoreInputs:
    covered: int
    total_tasks: int
    rows: int
    extra_notify: int
    merged_rows: int
    invalid_rows: int


def _base_penalty(x: ScoreInputs) -> float:
    uncovered = max(0, x.total_tasks - x.covered)
    return (
        100.0 * uncovered
        + 8.0 * x.rows
        + 15.0 * x.extra_notify
        + 12.0 * x.invalid_rows
    )


def official_like_latest(x: ScoreInputs) -> float:
    return _base_penalty(x) + 2.0 * x.merged_rows


SCORING_REGISTRY = {FIXED_SCORING_MODE: official_like_latest}


def available_scoring_modes() -> list[str]:
    return [FIXED_SCORING_MODE]


def compute_score(mode: str, x: ScoreInputs) -> float:
    if mode != FIXED_SCORING_MODE:
        raise ValueError(
            f"Scoring mode is fixed to {FIXED_SCORING_MODE}; received: {mode}"
        )
    return round(float(official_like_latest(x)), 4)
