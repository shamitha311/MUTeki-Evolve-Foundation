"""Unit and integration tests for Chunk 6 closed-loop Orchestrator."""

from __future__ import annotations

import pytest

from app.models import SandboxTarget, TrustedTargetRegistry
from app.validation import StrategyValidationError
from orchestration import (
    Orchestrator,
    RunStatus,
    TargetNotTrustedError,
    TerminationReason,
    get_default_target_registry,
)


@pytest.fixture
def registry() -> TrustedTargetRegistry:
    return get_default_target_registry()


@pytest.fixture
def orchestrator(registry: TrustedTargetRegistry) -> Orchestrator:
    return Orchestrator(registry=registry)


@pytest.mark.asyncio
async def test_orchestrator_runs_complete_three_round_solved_loop(
    orchestrator: Orchestrator,
) -> None:
    """Verify complete closed loop: initial strategy -> adapter -> evaluator -> memory -> solved termination."""
    state = await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="Understand and verify the demo target.",
        run_id="run-loop-1",
        max_iterations=3,
        mode="mock",
    )

    assert state.status == RunStatus.SOLVED
    assert state.termination_reason == TerminationReason.SOLVED
    assert state.current_iteration == 3
    assert len(state.history) == 3
    assert state.best_score == 100.0
    assert state.latest_score is not None
    assert state.latest_score.solved is True
    assert state.latest_score.progress_level == "verified success"

    # Verify strategy lineage across iterations
    r1, r2, r3 = state.history
    assert r1.strategy.revision == 1
    assert r2.strategy.revision == 2
    assert r2.strategy.parent_revision == 1
    assert r3.strategy.revision == 3
    assert r3.strategy.parent_revision == 2


@pytest.mark.asyncio
async def test_orchestrator_terminates_immediately_on_solved(
    orchestrator: Orchestrator,
) -> None:
    """Loop stops immediately when solved=True, even if max_iterations is larger."""
    state = await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="Quick test",
        run_id="run-loop-solved",
        max_iterations=10,
        mode="mock",
    )

    assert state.status == RunStatus.SOLVED
    assert state.current_iteration == 3  # Mock scenario solves at round 3
    assert len(state.history) == 3


@pytest.mark.asyncio
async def test_orchestrator_stops_at_max_iterations(
    orchestrator: Orchestrator,
) -> None:
    """Loop terminates cleanly with MAX_ITERATIONS_REACHED if unsolved."""
    state = await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="Bounded test",
        run_id="run-loop-max",
        max_iterations=2,
        mode="mock",
    )

    assert state.status == RunStatus.MAX_ITERATIONS_REACHED
    assert state.termination_reason == TerminationReason.MAX_ITERATIONS_REACHED
    assert state.current_iteration == 2
    assert len(state.history) == 2
    assert state.best_score > 0.0


@pytest.mark.asyncio
async def test_untrusted_target_id_is_rejected_before_execution(
    orchestrator: Orchestrator,
) -> None:
    """Security boundary: untrusted target_id raises TargetNotTrustedError before adapter invocation."""
    with pytest.raises(TargetNotTrustedError):
        await orchestrator.run_investigation(
            target_id="untrusted-hacker-target",
            objective="Attempt unauthorized access",
            run_id="run-untrusted",
        )


@pytest.mark.asyncio
async def test_malicious_strategy_override_fails_validation(
    registry: TrustedTargetRegistry,
) -> None:
    """Security boundary: Strategy carrying forbidden target/command overrides fails fail-closed validation."""
    from app.validation import approve_strategy
    from pydantic import ValidationError

    target = registry.resolve("trusted-demo-target")

    # Dict input to approve_strategy raises StrategyValidationError with kind == 'safety'
    with pytest.raises(StrategyValidationError) as exc_info:
        approve_strategy(
            target,
            {
                "objective": "Test",
                "context": {"command": "rm -rf /"},
            },
            registry,
        )
    assert exc_info.value.kind == "safety"


@pytest.mark.asyncio
async def test_target_immutability_is_preserved(
    orchestrator: Orchestrator,
) -> None:
    """Target ID and runtime_reference remain immutable throughout all iterations."""
    state = await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="Immutability check",
        run_id="run-immutability",
    )
    assert state.target.id == "trusted-demo-target"
    assert state.target.runtime_reference == "mock://trusted-demo-target"


@pytest.mark.asyncio
async def test_orchestrator_accumulates_event_stream(
    orchestrator: Orchestrator,
) -> None:
    """Normalized investigation events are accumulated in monotonic order."""
    await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="Event stream check",
        run_id="run-events",
    )
    events = orchestrator.get_run_events("run-events")
    assert len(events) > 0
    sequences = [e.sequence for e in events]
    assert sequences == sorted(sequences)


@pytest.mark.asyncio
async def test_orchestrator_run_state_queries_and_cancellation(
    orchestrator: Orchestrator,
) -> None:
    """Test get_run_state, list_runs, and cancel_run operations."""
    run_id = "run-cancel-test"
    await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="State query check",
        run_id=run_id,
        max_iterations=1,
    )

    state = orchestrator.get_run_state(run_id)
    assert state is not None
    assert state.run_id == run_id

    runs = orchestrator.list_runs()
    assert any(r.run_id == run_id for r in runs)

    cancelled = orchestrator.cancel_run(run_id)
    assert cancelled is True


@pytest.mark.asyncio
async def test_real_mode_path_executes_cleanly_and_preserves_mode_flag(
    orchestrator: Orchestrator,
) -> None:
    """Verify REAL mode path executes cleanly and preserves mode='real' in run state."""
    state = await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="Real mode validation test",
        run_id="run-real-mode",
        max_iterations=1,
        mode="real",
    )
    assert state.mode == "real"
    assert state.run_id == "run-real-mode"
    assert len(state.history) == 1


@pytest.mark.asyncio
async def test_orchestrator_multi_run_isolation(
    orchestrator: Orchestrator,
) -> None:
    """Verify independent investigation runs remain isolated with separate histories and states."""
    state1 = await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="Objective A",
        run_id="run-iso-A",
        max_iterations=1,
    )
    state2 = await orchestrator.run_investigation(
        target_id="trusted-demo-target",
        objective="Objective B",
        run_id="run-iso-B",
        max_iterations=1,
    )

    assert orchestrator.get_run_state("run-iso-A") == state1
    assert orchestrator.get_run_state("run-iso-B") == state2
    assert state1.run_id != state2.run_id
    assert state1.history[0].strategy.objective == "Objective A"
    assert state2.history[0].strategy.objective == "Objective B"

