"""Deterministic adapter mock for downstream chunks.

This is test infrastructure, not a fake production Muteki API. It deliberately
returns project-owned models and has no shell, worker, Docker, or host execution
path.
"""

from collections.abc import AsyncIterator

from app.models import (
    Evidence,
    InvestigationEvent,
    InvestigationResult,
    SandboxTarget,
    Strategy,
    TrustedTargetRegistry,
)
from app.validation import approve_strategy


class MockMutekiAdapter:
    """Replayable deterministic stand-in for Chunk 3/5/UI development."""

    def __init__(
        self,
        registry: TrustedTargetRegistry,
        *,
        run_id: str = "mock-c1",
    ) -> None:
        self.registry = registry
        self.run_id = run_id
        self._events: list[InvestigationEvent] = []
        self._results: list[InvestigationResult] = []
        self._sequence = 0

    async def run_strategy(
        self, target: SandboxTarget, strategy: Strategy
    ) -> InvestigationResult:
        approved = approve_strategy(target, strategy, self.registry)
        round_number = approved.revision
        if round_number == 1:
            result = self._round_one()
            event_types = (
                "reconnaissance.started",
                "evidence.observed",
                "round.completed",
            )
        elif round_number == 2:
            result = self._round_two()
            event_types = (
                "evidence.correlated",
                "hypothesis.strengthened",
                "round.completed",
            )
        elif round_number == 3:
            result = self._round_three()
            event_types = (
                "success.condition.verified",
                "evidence.observed",
                "round.completed",
            )
        else:
            result = InvestigationResult(
                run_id=self.run_id,
                evidence_summary="No scripted mock outcome for this revision.",
                progress_signals=["no scripted fixture"],
                event_summary=["unmapped strategy revision"],
            )
            event_types = ("round.completed",)

        for event_type in event_types:
            self._sequence += 1
            self._events.append(
                InvestigationEvent(
                    sequence=self._sequence,
                    timestamp=f"2026-01-01T00:00:{self._sequence:02d}Z",
                    type=event_type,
                    run_id=self.run_id,
                    worker_id="mock-worker-1",
                    summary=result.event_summary[0],
                )
            )
        self._results.append(result)
        return result

    def subscribe_events(self, run_id: str) -> AsyncIterator[InvestigationEvent]:
        async def stream() -> AsyncIterator[InvestigationEvent]:
            if run_id != self.run_id:
                return
            for event in tuple(self._events):
                yield event

        return stream()

    def _round_one(self) -> InvestigationResult:
        return InvestigationResult(
            run_id=self.run_id,
            solved=False,
            evidence=[
                Evidence(
                    type="reconnaissance",
                    summary="Initial sandbox surface mapped.",
                    confidence=0.55,
                    source_event=self._sequence + 2,
                )
            ],
            evidence_summary="Useful initial understanding, but no verified success.",
            progress_signals=["reconnaissance"],
            elapsed_seconds=1.0,
            event_summary=["Initial sandbox surface mapped."],
        )

    def _round_two(self) -> InvestigationResult:
        return InvestigationResult(
            run_id=self.run_id,
            solved=False,
            evidence=[
                Evidence(
                    type="correlation",
                    summary="Independent observations agree on the leading hypothesis.",
                    confidence=0.82,
                    source_event=self._sequence + 1,
                )
            ],
            evidence_summary="Strong evidence, but the success condition is not verified.",
            progress_signals=["strong evidence"],
            elapsed_seconds=2.0,
            event_summary=["Independent observations agree on the leading hypothesis."],
        )

    def _round_three(self) -> InvestigationResult:
        return InvestigationResult(
            run_id=self.run_id,
            solved=True,
            evidence=[
                Evidence(
                    type="verified_success",
                    summary="The trusted mock success condition was verified.",
                    confidence=1.0,
                    source_event=self._sequence + 1,
                )
            ],
            evidence_summary="Verified success.",
            progress_signals=["verified success"],
            elapsed_seconds=3.0,
            event_summary=["The trusted mock success condition was verified."],
        )
