"""The deterministic three-round fixture required by Chunk 1."""

from dataclasses import dataclass

from app.models import InvestigationResult, SandboxTarget, ScoreReport, Strategy
from muteki_adapter.mock import MockMutekiAdapter


@dataclass(frozen=True)
class MockRound:
    strategy: Strategy
    result: InvestigationResult
    score: ScoreReport


def build_three_round_scenario() -> tuple[SandboxTarget, tuple[MockRound, ...]]:
    target = SandboxTarget(
        id="trusted-demo-target",
        name="Trusted demo sandbox",
        description="A deterministic local fixture target for contract tests.",
        runtime_reference="mock://trusted-demo-target",
    )
    strategies = (
        Strategy(
            objective="Build an initial understanding of the trusted sandbox.",
            priorities=["reconnaissance", "evidence collection"],
            constraints=["stay within the trusted sandbox"],
            revision=1,
        ),
        Strategy(
            objective="Correlate the strongest evidence and test the leading hypothesis.",
            priorities=["evidence correlation", "hypothesis testing"],
            constraints=["preserve the trusted target boundary"],
            context={"based_on": "round-1"},
            revision=2,
            parent_revision=1,
        ),
        Strategy(
            objective="Verify the success condition using the strongest evidence.",
            priorities=["verification", "clear success evidence"],
            constraints=["stop after verified success"],
            context={"based_on": "round-2"},
            revision=3,
            parent_revision=2,
        ),
    )
    reports = (
        ScoreReport(
            progress_score=28.0,
            solved=False,
            progress_level="reconnaissance",
            reasons=["Initial surface understanding is useful."],
        ),
        ScoreReport(
            progress_score=72.0,
            solved=False,
            progress_level="strong evidence",
            reasons=["Evidence is correlated but success is not verified."],
        ),
        ScoreReport(
            progress_score=100.0,
            solved=True,
            progress_level="verified success",
            reasons=["The success condition is verified."],
        ),
    )
    # Results are filled by run_three_round_scenario; fixture construction remains
    # side-effect free and makes the intended strategy lineage explicit.
    placeholder_results = tuple(
        InvestigationResult(run_id="mock-c1") for _ in strategies
    )
    return target, tuple(
        MockRound(strategy, result, report)
        for strategy, result, report in zip(
            strategies, placeholder_results, reports, strict=True
        )
    )


async def run_three_round_scenario() -> tuple[SandboxTarget, tuple[MockRound, ...]]:
    target, rounds = build_three_round_scenario()
    from app.models import TrustedTargetRegistry

    adapter = MockMutekiAdapter(
        TrustedTargetRegistry({target.id: target}), run_id="mock-c1"
    )
    completed: list[MockRound] = []
    for round_data in rounds:
        result = await adapter.run_strategy(target, round_data.strategy)
        completed.append(
            MockRound(round_data.strategy, result, round_data.score)
        )
    return target, tuple(completed)
