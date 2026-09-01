from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RoundOutcome:
    iteration: int
    score: float | None
    hypothesis: str
    outcome: str  # "improved" | "regressed" | "catastrophic" | "harness_failed"
    # Target buckets the model declared for this round. Used by the runner's
    # forced-exploration gate to detect "same module failed twice in a row" —
    # a more specific stagnation signal than just "non-improved" outcomes.
    target_buckets: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoundState:
    iteration: int
    best_score: float | None
    best_solver_path: Path | None
    best_report_path: Path | None
    recent_history: list[RoundOutcome]
    input_dir: Path
    run_dir: Path
    bootstrap_solver_path: Path | None = None


@dataclass(frozen=True)
class HarnessResult:
    solver_code: str
    plan: dict[str, Any]
    transcript_path: Path
    steps_taken: int


class HarnessFailure(RuntimeError):
    """Raised when the harness cannot produce a valid solver this round.

    The outer loop should record a 'harness_failed' outcome and continue.
    """

    def __init__(self, reason: str, *, transcript_path: Path | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transcript_path = transcript_path


class HarnessAborted(RuntimeError):
    """Raised when the round is interrupted mid-flight by an external stop
    signal (e.g. the user clicked Stop on the frontend). Distinct from
    HarnessFailure: the round did not fail to produce a solver — it was
    cancelled before it could.
    """

    def __init__(self, reason: str, *, transcript_path: Path | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transcript_path = transcript_path


class FatalToolError(RuntimeError):
    """Raised by a tool when the failure is infrastructure-level, not
    LLM-recoverable, so the entire fool loop should abort instead of
    iterating further. The tool registry must NOT swallow this.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
