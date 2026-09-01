from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    Evidence,
    InvestigationEvent,
    InvestigationResult,
    SandboxTarget,
    ScoreReport,
    Strategy,
    TrustedTargetRegistry,
)
from app.validation import StrategyValidationError, approve_strategy, validate_strategy


def target() -> SandboxTarget:
    return SandboxTarget(
        id="t1",
        name="Test sandbox",
        description="A trusted test target.",
        runtime_reference="mock://t1",
    )


def test_sandbox_target_requires_runtime_reference() -> None:
    with pytest.raises(ValidationError):
        SandboxTarget(id="t1", name="x", description="y", runtime_reference="")


def test_registry_accepts_only_exact_trusted_target() -> None:
    trusted = target()
    registry = TrustedTargetRegistry({trusted.id: trusted})
    assert registry.contains(trusted)
    assert not registry.contains(trusted.model_copy(update={"name": "changed"}))


def test_strategy_schema_is_high_level_and_has_lineage() -> None:
    strategy = validate_strategy(
        {
            "objective": "Understand the sandbox",
            "priorities": ["observe"],
            "constraints": ["stay scoped"],
            "revision": 2,
            "parent_revision": 1,
        }
    )
    assert strategy.revision == 2
    assert strategy.parent_revision == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"objective": "x", "target": "other"},
        {"objective": "x", "runtime_reference": "host://bad"},
        {"objective": "x", "context": {"command": "rm -rf /"}},
        {"objective": "x", "context": {"nested": {"docker": "run"}}},
    ],
)
def test_strategy_target_control_and_execution_are_rejected(payload) -> None:
    with pytest.raises(StrategyValidationError) as exc_info:
        validate_strategy(payload)
    assert exc_info.value.kind == "safety"


def test_strategy_model_also_rejects_dangerous_nested_context() -> None:
    with pytest.raises(ValidationError):
        Strategy(objective="observe", context={"nested": {"exec": "bad"}})


def test_strategy_cannot_override_runtime_reference() -> None:
    strategy = Strategy(objective="observe")
    assert not hasattr(strategy, "runtime_reference")
    with pytest.raises(StrategyValidationError):
        approve_strategy(
            target(),
            {"objective": "observe", "runtime_reference": "mock://other"},
            TrustedTargetRegistry({target().id: target()}),
        )


def test_untrusted_target_is_rejected_before_adapter_boundary() -> None:
    trusted = target()
    registry = TrustedTargetRegistry({trusted.id: trusted})
    untrusted = trusted.model_copy(update={"runtime_reference": "mock://tampered"})
    with pytest.raises(StrategyValidationError) as exc_info:
        approve_strategy(untrusted, {"objective": "observe"}, registry)
    assert exc_info.value.kind == "target"


def test_investigation_event_validates_iso_timestamp_and_sequence() -> None:
    event = InvestigationEvent(
        sequence=1,
        timestamp=datetime.now(timezone.utc).isoformat(),
        type="observation",
        run_id="run-1",
        summary="Observed a safe fixture.",
    )
    assert event.worker_id is None
    with pytest.raises(ValidationError):
        InvestigationEvent(
            sequence=0,
            timestamp=event.timestamp,
            type=event.type,
            run_id=event.run_id,
            summary=event.summary,
        )
    with pytest.raises(ValidationError):
        InvestigationEvent(
            sequence=1,
            timestamp="not-a-date",
            type="observation",
            run_id="run-1",
            summary="x",
        )


def test_evidence_confidence_is_bounded() -> None:
    Evidence(type="fact", summary="x", confidence=0.0)
    Evidence(type="fact", summary="x", confidence=1.0)
    with pytest.raises(ValidationError):
        Evidence(type="fact", summary="x", confidence=1.01)


def test_investigation_result_keeps_solved_independent_from_score() -> None:
    result = InvestigationResult(
        run_id="run-1",
        solved=True,
        evidence_summary="verified",
        progress_signals=["verified success"],
    )
    assert result.solved is True
    assert not hasattr(result, "progress_score")


def test_score_report_uses_0_to_100_progress_scale() -> None:
    report = ScoreReport(
        progress_score=50,
        solved=False,
        progress_level="partial",
    )
    assert report.progress_score == 50
    with pytest.raises(ValidationError):
        ScoreReport(progress_score=101, progress_level="invalid")
