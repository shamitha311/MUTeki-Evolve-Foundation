from __future__ import annotations

import pytest

from app.models import Evidence, InvestigationResult, ScoreReport, Strategy
from strategy import (
    StrategyDiversificationRequired,
    StrategyEngine,
    StrategyMemory,
    StrategyValidationError,
    is_duplicate,
    strategy_fingerprint,
)
from strategy.reviewer import review_history


def result(
    *,
    summary: str = "",
    signals: list[str] | None = None,
    evidence_type: str | None = None,
    confidence: float = 0.5,
    solved: bool = False,
    error: str | None = None,
) -> InvestigationResult:
    evidence = (
        [
            Evidence(
                type=evidence_type,
                summary=summary or evidence_type,
                confidence=confidence,
            )
        ]
        if evidence_type
        else []
    )
    return InvestigationResult(
        run_id="strategy-test",
        solved=solved,
        evidence=evidence,
        evidence_summary=summary,
        progress_signals=signals or [],
        event_summary=[summary] if summary else [],
        error=error,
    )


def score(
    value: float,
    *,
    level: str = "partial",
    solved: bool = False,
    stagnated: bool = False,
) -> ScoreReport:
    return ScoreReport(
        progress_score=value,
        solved=solved,
        progress_level=level,
        reasons=[level],
        stagnated=stagnated,
    )


def initial(engine: StrategyEngine | None = None) -> Strategy:
    return (engine or StrategyEngine()).generate_initial_strategy(
        "Understand the trusted sandbox",
        ["reconnaissance", "evidence collection"],
        ["stay within the trusted sandbox"],
        {"fixture": "local"},
    )


def test_initial_strategy_generation_and_lineage() -> None:
    strategy = initial()
    assert strategy.revision == 1
    assert strategy.parent_revision is None
    assert strategy.objective == "Understand the trusted sandbox"
    assert strategy.context == {"fixture": "local"}
    assert not hasattr(strategy, "target")
    assert not hasattr(strategy, "runtime_reference")


def test_next_strategy_responds_to_meaningful_progress() -> None:
    engine = StrategyEngine()
    previous = initial(engine)
    memory = StrategyMemory()
    investigation = result(
        summary="Initial surface mapped.",
        signals=["reconnaissance"],
        evidence_type="reconnaissance",
    )

    next_strategy = engine.generate_next_strategy(
        previous,
        investigation,
        score(28, level="reconnaissance"),
        memory,
    )
    assert next_strategy.revision == 2
    assert next_strategy.parent_revision == 1
    assert "evidence correlation" in next_strategy.priorities
    assert next_strategy.context["previous_score"] == 28.0


def test_next_strategy_changes_direction_after_failure() -> None:
    engine = StrategyEngine()
    previous = initial(engine)
    memory = StrategyMemory()
    failed = result(error="bounded investigation timed out")
    failed_score = score(0, level="timeout")
    memory.record_iteration(previous, failed, failed_score)

    next_strategy = engine.generate_next_strategy(
        previous,
        failed,
        failed_score,
        memory,
    )
    assert next_strategy.revision == 2
    assert "reconnaissance" not in next_strategy.priorities
    assert next_strategy.context["review"]["failed_directions"] == [
        "reconnaissance",
        "evidence collection",
    ]


def test_next_strategy_deepens_strong_evidence_toward_verification() -> None:
    previous = initial()
    investigation = result(
        summary="Independent observations agree on the hypothesis.",
        signals=["strong evidence"],
        evidence_type="correlation",
        confidence=0.9,
    )
    next_strategy = StrategyEngine().generate_next_strategy(
        previous,
        investigation,
        score(72, level="strong evidence"),
        (),
    )
    assert next_strategy.priorities == [
        "verification",
        "clear success evidence",
    ]


def test_memory_insertion_and_retrieval() -> None:
    memory = StrategyMemory()
    strategy = initial()
    investigation = result(
        summary="Surface discovered.",
        signals=["reconnaissance"],
        evidence_type="reconnaissance",
    )
    report = score(28, level="reconnaissance")
    record = memory.record_iteration(strategy, investigation, report)

    assert record.iteration == 1
    assert memory.history() == (record,)
    assert memory.latest_strategy() == strategy
    assert memory.latest_result() == investigation
    assert memory.score_history() == (report,)


def test_memory_tracks_successful_directions() -> None:
    memory = StrategyMemory()
    strategy = initial()
    memory.record_iteration(
        strategy,
        result(
            summary="Reconnaissance found the surface.",
            signals=["reconnaissance"],
            evidence_type="reconnaissance",
        ),
        score(20, level="reconnaissance"),
    )
    assert memory.successful_directions() == ("reconnaissance",)


def test_memory_tracks_failed_directions() -> None:
    memory = StrategyMemory()
    strategy = initial()
    memory.record_iteration(
        strategy,
        result(error="host unavailable"),
        score(0, level="error"),
    )
    assert memory.failed_directions() == (
        "reconnaissance",
        "evidence collection",
    )


def test_reviewer_reports_success_failure_and_unexplored_directions() -> None:
    memory = StrategyMemory()
    strategy = initial()
    memory.record_iteration(
        strategy,
        result(
            summary="Reconnaissance found the surface.",
            signals=["reconnaissance"],
            evidence_type="reconnaissance",
        ),
        score(20, level="reconnaissance"),
    )
    review = review_history(memory)
    assert review.successful_directions == ("reconnaissance",)
    assert "evidence correlation" in review.unexplored_directions
    assert "authentication" in review.unexplored_directions
    assert "Deepen" in review.recommendation


def test_stagnation_detection_uses_small_configurable_window() -> None:
    memory = StrategyMemory(stagnation_threshold=2)
    first = initial()
    second = first.model_copy(update={"revision": 2, "parent_revision": 1})
    unchanged = result(
        summary="No meaningful new evidence.",
        signals=[],
    )
    unchanged_score = score(20, level="partial")
    memory.record_iteration(first, unchanged, unchanged_score)
    memory.record_iteration(second, unchanged, unchanged_score)

    assert memory.is_stagnated()
    assert review_history(memory).stagnated


def test_stagnation_triggers_diversification() -> None:
    engine = StrategyEngine()
    memory = StrategyMemory()
    first = initial(engine)
    second = first.model_copy(update={"revision": 2, "parent_revision": 1})
    unchanged = result(summary="No meaningful new evidence.")
    unchanged_score = score(20)
    memory.record_iteration(first, unchanged, unchanged_score)
    memory.record_iteration(second, unchanged, unchanged_score)

    diversified = engine.generate_next_strategy(
        second,
        unchanged,
        unchanged_score,
        memory,
    )
    assert diversified.revision == 3
    assert diversified.parent_revision == 2
    assert diversified.priorities != second.priorities
    assert diversified.context["review"]["stagnated"] is True


def test_deduplication_uses_semantic_fields_not_identity() -> None:
    first = initial()
    equivalent = first.model_copy(
        update={"revision": 2, "parent_revision": 1}
    )
    memory = StrategyMemory()
    memory.record_iteration(first, result(summary="x"), score(1))

    assert first is not equivalent
    assert strategy_fingerprint(first) == strategy_fingerprint(equivalent)
    assert is_duplicate(equivalent, memory)


def test_generation_does_not_return_duplicate_candidate() -> None:
    engine = StrategyEngine()
    previous = initial(engine)
    memory = StrategyMemory()
    investigation = result(summary="No meaningful new evidence.")
    report = score(0)
    memory.record_iteration(previous, investigation, report)
    candidate = engine.generate_next_strategy(
        previous,
        investigation,
        report,
        memory,
    )
    assert not is_duplicate(candidate, memory)
    assert candidate.revision == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"target": "other sandbox"},
        {"context": {"host_execution": "run it"}},
        {"context": {"runtime_reference": "host://bad"}},
        {"context": {"sandbox_escape": "ignore boundaries"}},
        {"context": {"external_destination": "https://outside.example"}},
        {"context": {"command": "run arbitrary command"}},
    ],
)
def test_invalid_strategy_content_is_rejected_at_generation_boundary(payload) -> None:
    with pytest.raises(StrategyValidationError):
        StrategyEngine().generate_initial_strategy(
            "Observe the sandbox",
            ["reconnaissance"],
            ["stay scoped"],
            payload.get("context", payload),
        )


def test_deterministic_generation_is_reproducible_for_same_seed_and_inputs() -> None:
    previous = initial(StrategyEngine(seed=11))
    investigation = result(
        summary="The authentication surface is identified.",
        signals=["authentication"],
        evidence_type="authentication",
        confidence=0.8,
    )
    report = score(65, level="strong evidence")
    first = StrategyEngine(seed=11).generate_next_strategy(
        previous, investigation, report, ()
    )
    second = StrategyEngine(seed=11).generate_next_strategy(
        previous, investigation, report, ()
    )
    assert first == second


def test_history_influences_future_context_and_direction_review() -> None:
    engine = StrategyEngine()
    previous = initial(engine)
    memory = StrategyMemory()
    memory.record_iteration(
        previous,
        result(
            summary="Reconnaissance mapped the surface.",
            signals=["reconnaissance"],
            evidence_type="reconnaissance",
        ),
        score(28, level="reconnaissance"),
    )
    next_strategy = engine.generate_next_strategy(
        previous,
        result(
            summary="Independent observations agree.",
            signals=["strong evidence"],
            evidence_type="correlation",
            confidence=0.8,
        ),
        score(72, level="strong evidence"),
        memory,
    )
    assert next_strategy.context["review"]["successful_directions"] == [
        "reconnaissance"
    ]
    assert next_strategy.context["review"]["unexplored_directions"]


@pytest.mark.asyncio
async def test_engine_runs_the_existing_three_round_mock_without_muteki() -> None:
    from app.models import TrustedTargetRegistry
    from muteki_adapter.mock import MockMutekiAdapter
    from orchestration.mock_scenario import build_three_round_scenario

    target, _ = build_three_round_scenario()
    adapter = MockMutekiAdapter(
        TrustedTargetRegistry({target.id: target}),
        run_id="strategy-engine-test",
    )
    engine = StrategyEngine()
    memory = StrategyMemory()
    strategy = engine.generate_initial_strategy(
        "Build an understanding of the trusted sandbox.",
        ["reconnaissance", "evidence collection"],
        ["stay within the trusted sandbox"],
    )
    completed = []
    for _ in range(3):
        investigation = await adapter.run_strategy(target, strategy)
        report = (
            score(28, level="reconnaissance")
            if strategy.revision == 1
            else score(
                72 if strategy.revision == 2 else 100,
                level="strong evidence"
                if strategy.revision == 2
                else "verified success",
                solved=strategy.revision == 3,
            )
        )
        memory.record_iteration(strategy, investigation, report)
        completed.append(strategy)
        if not investigation.solved:
            strategy = engine.generate_next_strategy(
                strategy,
                investigation,
                report,
                memory,
            )

    assert [item.revision for item in completed] == [1, 2, 3]
    assert [item.parent_revision for item in completed] == [None, 1, 2]
    assert [record.iteration for record in memory.history()] == [1, 2, 3]
    assert memory.latest_result() is not None
    assert memory.latest_result().solved is True


def test_no_safe_alternative_is_reported_as_controlled_diversification_error() -> None:
    engine = StrategyEngine()
    previous = Strategy(
        objective="Focus only on the trusted sandbox",
        priorities=[
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
        ],
        constraints=["stay scoped"],
    )
    memory = StrategyMemory()
    investigation = result(summary="No meaningful new evidence.")
    report = score(0)
    memory.record_iteration(previous, investigation, report)
    with pytest.raises(StrategyDiversificationRequired, match="diversification"):
        engine.generate_next_strategy(previous, investigation, report, memory)