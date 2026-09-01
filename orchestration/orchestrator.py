"""Closed-loop Orchestrator for autonomous security investigation runs.

Architecture flow:
    Trusted SandboxTarget
            ↓
       Orchestrator
            ↓
    Strategy Engine (Initial Strategy)
            ↓
    Fail-closed Strategy Validation
            ↓
      Muteki Adapter (Mock / Real)
            ↓
    Normalized InvestigationResult & Events
            ↓
     Evaluation Engine (ScoreReport)
            ↓
      Strategy Memory
            ↓
    Strategy Engine (Next Strategy / Diversification)
            ↓
       REPEAT until Solved or Max Iterations
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator, Sequence, Literal

from app.evaluation import evaluate
from app.models import (
    InvestigationEvent,
    InvestigationResult,
    SandboxTarget,
    ScoreReport,
    Strategy,
    TrustedTargetRegistry,
)
from app.validation import StrategyValidationError, approve_strategy, validate_target
from muteki_adapter.live_probe import LiveNetworkAdapter
from muteki_adapter.mock import MockMutekiAdapter
from strategy import StrategyEngine, StrategyMemory

from .registry import get_default_target_registry
from .types import (
    InvestigationRunState,
    IterationRecord,
    RunStatus,
    TerminationReason,
)


class OrchestrationError(RuntimeError):
    """Base error raised during investigation orchestration failures."""


class TargetNotTrustedError(OrchestrationError):
    """Raised when an untrusted target ID or tampered SandboxTarget is supplied."""


class Orchestrator:
    """Central closed-loop investigation orchestrator.

    Enforces target immutability, fail-closed validation, deterministic scoring,
    and memory-backed strategy evolution.
    """

    def __init__(
        self,
        registry: TrustedTargetRegistry | None = None,
        strategy_engine: StrategyEngine | None = None,
    ) -> None:
        self.registry = registry or get_default_target_registry()
        self.strategy_engine = strategy_engine or StrategyEngine()
        self._runs: dict[str, InvestigationRunState] = {}
        self._event_streams: dict[str, list[InvestigationEvent]] = {}
        self._cancel_flags: dict[str, bool] = {}

    def get_run_state(self, run_id: str) -> InvestigationRunState | None:
        """Retrieve current run state by application run ID."""
        return self._runs.get(run_id)

    def list_runs(self) -> tuple[InvestigationRunState, ...]:
        """List all active and completed investigation runs."""
        return tuple(self._runs.values())

    def get_run_events(self, run_id: str) -> tuple[InvestigationEvent, ...]:
        """Retrieve accumulated normalized investigation events for a run."""
        return tuple(self._event_streams.get(run_id, []))

    def cancel_run(self, run_id: str) -> bool:
        """Request safe cancellation of an active investigation run."""
        if run_id in self._runs:
            self._cancel_flags[run_id] = True
            state = self._runs[run_id]
            if state.status in (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.EVALUATING, RunStatus.IMPROVING):
                self._runs[run_id] = InvestigationRunState(
                    run_id=state.run_id,
                    target=state.target,
                    mode=state.mode,
                    max_iterations=state.max_iterations,
                    status=RunStatus.CANCELLED,
                    current_iteration=state.current_iteration,
                    history=state.history,
                    termination_reason=TerminationReason.CANCELLED,
                    best_score=state.best_score,
                    best_result=state.best_result,
                    error="Investigation cancelled by user request.",
                    started_at=state.started_at,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
            return True
        return False

    async def run_investigation(
        self,
        target_id: str,
        objective: str,
        *,
        run_id: str = "run-001",
        max_iterations: int = 3,
        mode: Literal["mock", "real"] = "mock",
        priorities: Sequence[str] | None = None,
        constraints: Sequence[str] | None = None,
    ) -> InvestigationRunState:
        """Execute a complete autonomous investigation loop until solved or max_iterations."""

        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")

        # Step 1: Trusted target resolution
        try:
            target = self.registry.resolve(target_id)
        except KeyError as exc:
            raise TargetNotTrustedError(
                f"target_id is not present in trusted target registry: '{target_id}'"
            ) from exc

        # Security check: Validate target contract
        validate_target(target, self.registry)

        initial_priorities = tuple(priorities or ["reconnaissance", "evidence collection"])
        initial_constraints = tuple(constraints or ["stay within the trusted sandbox"])

        # Initialize run state
        state = InvestigationRunState(
            run_id=run_id,
            target=target,
            mode=mode,
            max_iterations=max_iterations,
            status=RunStatus.RUNNING,
            current_iteration=0,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._runs[run_id] = state
        self._event_streams[run_id] = []
        self._cancel_flags[run_id] = False

        memory = StrategyMemory()

        # Step 2: Generate initial strategy
        try:
            current_strategy = self.strategy_engine.generate_initial_strategy(
                objective=objective,
                priorities=initial_priorities,
                constraints=initial_constraints,
                context={"run_id": run_id, "mode": mode},
            )
        except Exception as exc:
            completed_at = datetime.now(timezone.utc).isoformat()
            failed_state = InvestigationRunState(
                run_id=run_id,
                target=target,
                mode=mode,
                max_iterations=max_iterations,
                status=RunStatus.FAILED,
                current_iteration=0,
                termination_reason=TerminationReason.STRATEGY_GENERATION_FAILURE,
                error=f"Initial strategy generation failed: {exc}",
                completed_at=completed_at,
            )
            self._runs[run_id] = failed_state
            return failed_state

        history: list[IterationRecord] = []
        best_score = 0.0
        best_result: InvestigationResult | None = None

        # Choose adapter mode
        if mode == "mock":
            adapter = MockMutekiAdapter(self.registry, run_id=run_id)
        elif mode in ("live", "live_probe") or target.runtime_reference.startswith(("http://", "https://")):
            adapter = LiveNetworkAdapter(self.registry, run_id=run_id)
        else:
            # REAL mode path: uses MockMutekiAdapter as deterministic stand-in if muteki daemon is unconfigured
            adapter = MockMutekiAdapter(self.registry, run_id=run_id)

        for iteration in range(1, max_iterations + 1):
            if self._cancel_flags.get(run_id, False):
                break

            iter_started_at = datetime.now(timezone.utc).isoformat()

            # Update run status to RUNNING
            self._runs[run_id] = InvestigationRunState(
                run_id=run_id,
                target=target,
                mode=mode,
                max_iterations=max_iterations,
                status=RunStatus.RUNNING,
                current_iteration=iteration,
                history=tuple(history),
                best_score=best_score,
                best_result=best_result,
                started_at=state.started_at,
            )

            # Step 3: Validate Strategy before EVERY adapter execution
            try:
                approved_strategy = approve_strategy(target, current_strategy, self.registry)
            except StrategyValidationError as exc:
                completed_at = datetime.now(timezone.utc).isoformat()
                failed_state = InvestigationRunState(
                    run_id=run_id,
                    target=target,
                    mode=mode,
                    max_iterations=max_iterations,
                    status=RunStatus.FAILED,
                    current_iteration=iteration,
                    history=tuple(history),
                    termination_reason=TerminationReason.VALIDATION_FAILURE,
                    best_score=best_score,
                    best_result=best_result,
                    error=f"Strategy validation failed at iteration {iteration}: {exc}",
                    started_at=state.started_at,
                    completed_at=completed_at,
                )
                self._runs[run_id] = failed_state
                return failed_state

            # Target immutability check
            if (
                target.id != state.target.id
                or target.runtime_reference != state.target.runtime_reference
            ):
                completed_at = datetime.now(timezone.utc).isoformat()
                failed_state = InvestigationRunState(
                    run_id=run_id,
                    target=state.target,
                    mode=mode,
                    max_iterations=max_iterations,
                    status=RunStatus.FAILED,
                    current_iteration=iteration,
                    history=tuple(history),
                    termination_reason=TerminationReason.VALIDATION_FAILURE,
                    best_score=best_score,
                    best_result=best_result,
                    error="Target immutability boundary violated",
                    started_at=state.started_at,
                    completed_at=completed_at,
                )
                self._runs[run_id] = failed_state
                return failed_state

            # Step 4: Execute via Adapter
            try:
                result = await adapter.run_strategy(target, approved_strategy)
            except Exception as exc:
                completed_at = datetime.now(timezone.utc).isoformat()
                failed_state = InvestigationRunState(
                    run_id=run_id,
                    target=target,
                    mode=mode,
                    max_iterations=max_iterations,
                    status=RunStatus.FAILED,
                    current_iteration=iteration,
                    history=tuple(history),
                    termination_reason=TerminationReason.MUTEKI_FAILURE,
                    best_score=best_score,
                    best_result=best_result,
                    error=f"Adapter execution failed at iteration {iteration}: {exc}",
                    started_at=state.started_at,
                    completed_at=completed_at,
                )
                self._runs[run_id] = failed_state
                return failed_state

            # Collect stream events
            async for event in adapter.subscribe_events(run_id):
                if event not in self._event_streams[run_id]:
                    self._event_streams[run_id].append(event)

            # Step 5: Evaluate result via Evaluation Engine
            self._runs[run_id] = InvestigationRunState(
                run_id=run_id,
                target=target,
                mode=mode,
                max_iterations=max_iterations,
                status=RunStatus.EVALUATING,
                current_iteration=iteration,
                history=tuple(history),
                best_score=best_score,
                best_result=best_result,
                started_at=state.started_at,
            )

            score_history = [record.score for record in history]
            score_report = evaluate(result, history=score_history)

            # Record iteration record
            iter_completed_at = datetime.now(timezone.utc).isoformat()
            iter_record = IterationRecord(
                iteration=iteration,
                strategy=approved_strategy,
                result=result,
                score=score_report,
                started_at=iter_started_at,
                completed_at=iter_completed_at,
            )
            history.append(iter_record)

            # Update best score and best result
            if score_report.progress_score > best_score:
                best_score = score_report.progress_score
                best_result = result
            elif best_result is None:
                best_result = result

            # Record in Strategy Memory
            memory.record_iteration(approved_strategy, result, score_report)

            # Step 6: Termination Check: SOLVED
            if score_report.solved or result.solved:
                completed_at = datetime.now(timezone.utc).isoformat()
                final_state = InvestigationRunState(
                    run_id=run_id,
                    target=target,
                    mode=mode,
                    max_iterations=max_iterations,
                    status=RunStatus.SOLVED,
                    current_iteration=iteration,
                    history=tuple(history),
                    termination_reason=TerminationReason.SOLVED,
                    best_score=best_score,
                    best_result=best_result,
                    started_at=state.started_at,
                    completed_at=completed_at,
                )
                self._runs[run_id] = final_state
                return final_state

            # Termination Check: Timeout or Fatal Error in result
            if result.error == "investigation_timeout":
                completed_at = datetime.now(timezone.utc).isoformat()
                final_state = InvestigationRunState(
                    run_id=run_id,
                    target=target,
                    mode=mode,
                    max_iterations=max_iterations,
                    status=RunStatus.TIMED_OUT,
                    current_iteration=iteration,
                    history=tuple(history),
                    termination_reason=TerminationReason.TIMEOUT,
                    best_score=best_score,
                    best_result=best_result,
                    error=result.error,
                    started_at=state.started_at,
                    completed_at=completed_at,
                )
                self._runs[run_id] = final_state
                return final_state

            # Termination Check: MAX_ITERATIONS reached
            if iteration >= max_iterations:
                completed_at = datetime.now(timezone.utc).isoformat()
                final_state = InvestigationRunState(
                    run_id=run_id,
                    target=target,
                    mode=mode,
                    max_iterations=max_iterations,
                    status=RunStatus.MAX_ITERATIONS_REACHED,
                    current_iteration=iteration,
                    history=tuple(history),
                    termination_reason=TerminationReason.MAX_ITERATIONS_REACHED,
                    best_score=best_score,
                    best_result=best_result,
                    started_at=state.started_at,
                    completed_at=completed_at,
                )
                self._runs[run_id] = final_state
                return final_state

            # Step 7: Next Strategy Generation
            self._runs[run_id] = InvestigationRunState(
                run_id=run_id,
                target=target,
                mode=mode,
                max_iterations=max_iterations,
                status=RunStatus.IMPROVING,
                current_iteration=iteration,
                history=tuple(history),
                best_score=best_score,
                best_result=best_result,
                started_at=state.started_at,
            )

            try:
                current_strategy = self.strategy_engine.generate_next_strategy(
                    previous_strategy=approved_strategy,
                    investigation_result=result,
                    score_report=score_report,
                    history=memory,
                )
            except Exception as exc:
                completed_at = datetime.now(timezone.utc).isoformat()
                failed_state = InvestigationRunState(
                    run_id=run_id,
                    target=target,
                    mode=mode,
                    max_iterations=max_iterations,
                    status=RunStatus.FAILED,
                    current_iteration=iteration,
                    history=tuple(history),
                    termination_reason=TerminationReason.STRATEGY_GENERATION_FAILURE,
                    best_score=best_score,
                    best_result=best_result,
                    error=f"Next strategy generation failed at iteration {iteration}: {exc}",
                    started_at=state.started_at,
                    completed_at=completed_at,
                )
                self._runs[run_id] = failed_state
                return failed_state

        completed_at = datetime.now(timezone.utc).isoformat()
        final_state = InvestigationRunState(
            run_id=run_id,
            target=target,
            mode=mode,
            max_iterations=max_iterations,
            status=RunStatus.MAX_ITERATIONS_REACHED if not self._cancel_flags.get(run_id, False) else RunStatus.CANCELLED,
            current_iteration=len(history),
            history=tuple(history),
            termination_reason=TerminationReason.MAX_ITERATIONS_REACHED if not self._cancel_flags.get(run_id, False) else TerminationReason.CANCELLED,
            best_score=best_score,
            best_result=best_result,
            started_at=state.started_at,
            completed_at=completed_at,
        )
        self._runs[run_id] = final_state
        return final_state
