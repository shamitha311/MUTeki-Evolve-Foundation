"""Orchestration state types, status enums, and termination reason contracts."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Sequence

from app.models import InvestigationResult, SandboxTarget, ScoreReport, Strategy


class RunStatus(str, Enum):
    """High-level lifecycle status of an autonomous investigation run."""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    EVALUATING = "EVALUATING"
    IMPROVING = "IMPROVING"
    SOLVED = "SOLVED"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


class TerminationReason(str, Enum):
    """Explicit reason explaining why an investigation run terminated."""

    SOLVED = "SOLVED"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    MUTEKI_FAILURE = "MUTEKI_FAILURE"
    TIMEOUT = "TIMEOUT"
    STRATEGY_GENERATION_FAILURE = "STRATEGY_GENERATION_FAILURE"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    CANCELLED = "CANCELLED"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


@dataclass(frozen=True, slots=True)
class IterationRecord:
    """Complete record of a single strategy iteration within an investigation run."""

    iteration: int
    strategy: Strategy
    result: InvestigationResult
    score: ScoreReport
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass(frozen=True, slots=True)
class InvestigationRunState:
    """Immutable view of the overall autonomous investigation run state."""

    run_id: str
    target: SandboxTarget
    mode: str
    max_iterations: int
    status: RunStatus
    current_iteration: int
    history: tuple[IterationRecord, ...] = field(default_factory=tuple)
    termination_reason: TerminationReason | None = None
    best_score: float = 0.0
    best_result: InvestigationResult | None = None
    error: str | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None

    @property
    def latest_strategy(self) -> Strategy | None:
        return self.history[-1].strategy if self.history else None

    @property
    def latest_result(self) -> InvestigationResult | None:
        return self.history[-1].result if self.history else None

    @property
    def latest_score(self) -> ScoreReport | None:
        return self.history[-1].score if self.history else None
