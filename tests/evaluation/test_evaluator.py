"""Comprehensive tests for the Chunk 5 Evaluation Engine.

Covers all 28 cases from Section 36 of the Chunk 5 brief, plus the
three-round deterministic fixture test (Section 37), stagnation tests,
and evidence analyzer tests.

Design constraints verified by these tests:
- No Muteki execution, no command execution, no target selection.
- No strategy generation.
- Deterministic: identical input → identical ScoreReport.
- Evidence-based: activity alone does not produce a high score.
- Solved requires InvestigationResult.solved, never inferred from score.
- Score is always in [0.0, 100.0].
- Progress levels are UI-compatible strings.
"""
from __future__ import annotations

import pytest

from app.evaluation import DEFAULT_CONFIG, EvaluatorConfig, evaluate
from app.evaluation.evidence_analyzer import analyze_evidence, deduplicate_evidence
from app.evaluation.scorer import (
    calculate_progress_score,
    determine_progress_level,
    determine_solved,
)
from app.evaluation.stagnation import detect_stagnation
from app.evaluation.validators import normalize_confidence, normalize_result
from app.models import Evidence, InvestigationResult, ScoreReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(
    *,
    run_id: str = "run-test",
    solved: bool = False,
    evidence: list[Evidence] | None = None,
    evidence_summary: str = "",
    progress_signals: list[str] | None = None,
    elapsed_seconds: float = 0.0,
    event_summary: list[str] | None = None,
    error: str | None = None,
) -> InvestigationResult:
    return InvestigationResult(
        run_id=run_id,
        solved=solved,
        evidence=evidence or [],
        evidence_summary=evidence_summary,
        progress_signals=progress_signals or [],
        elapsed_seconds=elapsed_seconds,
        event_summary=event_summary or [],
        error=error,
    )


def _evidence(
    *,
    type: str = "observation",
    summary: str = "Something was observed.",
    confidence: float = 0.5,
    source_event: int | None = None,
) -> Evidence:
    return Evidence(
        type=type,
        summary=summary,
        confidence=confidence,
        source_event=source_event,
    )


def _score_report(
    *,
    progress_score: float = 20.0,
    solved: bool = False,
    progress_level: str = "reconnaissance",
    reasons: list[str] | None = None,
    stagnated: bool = False,
) -> ScoreReport:
    return ScoreReport(
        progress_score=progress_score,
        solved=solved,
        progress_level=progress_level,
        reasons=reasons or ["Test reason."],
        stagnated=stagnated,
    )


# ---------------------------------------------------------------------------
# Section 36, Case 1: Empty investigation
# ---------------------------------------------------------------------------

class TestEmptyInvestigation:
    """Case 1: No events, no evidence, no signals, no verified result."""

    def test_empty_result_scores_zero(self) -> None:
        report = evaluate(_result())
        assert report.progress_score == 0.0

    def test_empty_result_not_solved(self) -> None:
        report = evaluate(_result())
        assert report.solved is False

    def test_empty_result_level_is_started(self) -> None:
        report = evaluate(_result())
        assert report.progress_level == "started"

    def test_empty_result_has_reasons(self) -> None:
        report = evaluate(_result())
        assert len(report.reasons) >= 1

    def test_empty_result_not_stagnated_without_history(self) -> None:
        report = evaluate(_result())
        assert report.stagnated is False


# ---------------------------------------------------------------------------
# Section 36, Case 2: Reconnaissance-only result
# ---------------------------------------------------------------------------

class TestReconnaissanceOnly:
    """Case 2: Reconnaissance signals, low-to-moderate evidence."""

    def test_recon_signal_produces_low_to_moderate_score(self) -> None:
        result = _result(progress_signals=["reconnaissance"])
        report = evaluate(result)
        assert 1.0 <= report.progress_score <= 45.0

    def test_recon_level_assigned(self) -> None:
        result = _result(progress_signals=["reconnaissance"])
        report = evaluate(result)
        assert report.progress_level == "reconnaissance"

    def test_recon_not_solved(self) -> None:
        result = _result(progress_signals=["reconnaissance"])
        report = evaluate(result)
        assert report.solved is False


# ---------------------------------------------------------------------------
# Section 36, Case 3: Partial evidence
# ---------------------------------------------------------------------------

class TestPartialEvidence:
    """Case 3: Some evidence but not strongly correlated."""

    def test_moderate_evidence_score_range(self) -> None:
        result = _result(
            evidence=[_evidence(type="observation", confidence=0.4)],
            progress_signals=["reconnaissance"],
            evidence_summary="Some observations made.",
        )
        report = evaluate(result)
        # Should be in partial or recon range — not strong
        assert report.progress_score < 60.0
        assert report.progress_level in ("reconnaissance", "partial evidence")


# ---------------------------------------------------------------------------
# Section 36, Case 4: Strong evidence
# ---------------------------------------------------------------------------

class TestStrongEvidence:
    """Case 4: Correlated, high-confidence evidence."""

    def test_strong_evidence_scores_high(self) -> None:
        result = _result(
            evidence=[_evidence(type="correlation", confidence=0.9)],
            progress_signals=["strong evidence"],
            evidence_summary="Strong correlated evidence.",
        )
        report = evaluate(result)
        assert report.progress_score >= 60.0
        assert report.progress_level in ("strong evidence", "validated")

    def test_strong_evidence_not_solved(self) -> None:
        result = _result(
            evidence=[_evidence(type="correlation", confidence=0.9)],
            progress_signals=["strong evidence"],
        )
        report = evaluate(result)
        assert report.solved is False


# ---------------------------------------------------------------------------
# Section 36, Case 5: Validated evidence
# ---------------------------------------------------------------------------

class TestValidatedEvidence:
    """Case 5: Near-verified state with high evidence quality."""

    def test_high_confidence_multiple_items_high_score(self) -> None:
        result = _result(
            evidence=[
                _evidence(type="correlation", confidence=0.95),
                _evidence(type="hypothesis_test", confidence=0.88, summary="Hypothesis confirmed."),
            ],
            progress_signals=["strong evidence", "hypothesis"],
            evidence_summary="Multiple high-confidence items corroborate the hypothesis.",
        )
        report = evaluate(result)
        assert report.progress_score >= 55.0
        assert report.progress_level in ("strong evidence", "validated")


# ---------------------------------------------------------------------------
# Section 36, Case 6: Verified solved result
# ---------------------------------------------------------------------------

class TestVerifiedSolvedResult:
    """Case 6: InvestigationResult.solved=True with verified success evidence."""

    def test_solved_scores_100(self) -> None:
        result = _result(
            solved=True,
            evidence=[_evidence(type="verified_success", confidence=1.0, summary="Success confirmed.")],
            progress_signals=["verified success"],
            evidence_summary="Verified success.",
        )
        report = evaluate(result)
        assert report.progress_score == 100.0

    def test_solved_level_is_verified_success(self) -> None:
        result = _result(solved=True)
        report = evaluate(result)
        assert report.progress_level == "verified success"

    def test_solved_flag_is_true(self) -> None:
        result = _result(solved=True)
        report = evaluate(result)
        assert report.solved is True

    def test_solved_not_stagnated(self) -> None:
        history = [_score_report(progress_score=20.0), _score_report(progress_score=20.0)]
        result = _result(solved=True)
        report = evaluate(result, history=history)
        assert report.stagnated is False


# ---------------------------------------------------------------------------
# Section 36, Case 7: Unsolved result with high progress
# ---------------------------------------------------------------------------

class TestUnsolvedHighProgress:
    """Case 7: High score is valid with solved=False."""

    def test_high_score_unsolved_is_valid(self) -> None:
        result = _result(
            solved=False,
            evidence=[_evidence(type="correlation", confidence=0.95, summary="Almost there.")],
            progress_signals=["strong evidence"],
            evidence_summary="Very close but not verified.",
        )
        report = evaluate(result)
        assert report.progress_score >= 50.0
        assert report.solved is False

    def test_score_near_100_does_not_imply_solved(self) -> None:
        """A score of 95 with solved=False must not set solved=True."""
        result = _result(
            solved=False,
            evidence=[
                _evidence(type="verified_success", confidence=0.99, summary="Looks solved."),
                _evidence(type="correlation", confidence=0.98, summary="Corroborated."),
            ],
            progress_signals=["verified success", "strong evidence"],
        )
        report = evaluate(result)
        assert report.solved is False
        assert report.progress_level != "verified success"


# ---------------------------------------------------------------------------
# Section 36, Case 8 & 25: Duplicate evidence
# ---------------------------------------------------------------------------

class TestDuplicateEvidence:
    """Cases 8 and 25: Duplicate items must not gain full extra credit."""

    def test_duplicate_scores_same_as_single(self) -> None:
        single_ev = _evidence(type="reconnaissance", confidence=0.6)
        result_single = _result(evidence=[single_ev])
        result_dup = _result(evidence=[single_ev, single_ev, single_ev])
        report_single = evaluate(result_single)
        report_dup = evaluate(result_dup)
        # Duplicates are dropped; scores must be identical
        assert report_single.progress_score == report_dup.progress_score

    def test_deduplicate_evidence_drops_repeated_items(self) -> None:
        ev = _evidence(type="fact", summary="Same finding.", confidence=0.7)
        unique = deduplicate_evidence([ev, ev, ev])
        assert len(unique) == 1

    def test_duplicate_count_in_analysis(self) -> None:
        ev = _evidence(type="observation", confidence=0.5)
        analysis = analyze_evidence([ev, ev, ev])
        assert analysis.duplicate_count == 2

    def test_duplicate_reason_mentioned(self) -> None:
        ev = _evidence(type="observation", confidence=0.5)
        result = _result(evidence=[ev, ev, ev])
        report = evaluate(result)
        dup_mentioned = any("duplicate" in r.lower() for r in report.reasons)
        assert dup_mentioned


# ---------------------------------------------------------------------------
# Section 36, Case 9: Low-confidence evidence
# ---------------------------------------------------------------------------

class TestLowConfidenceEvidence:
    """Case 9: Low-confidence evidence contributes little."""

    def test_low_confidence_scores_low(self) -> None:
        result = _result(
            evidence=[_evidence(type="observation", confidence=0.1)],
        )
        report = evaluate(result)
        # Very low confidence should keep score near zero
        assert report.progress_score < 30.0


# ---------------------------------------------------------------------------
# Section 36, Case 10: High-confidence evidence
# ---------------------------------------------------------------------------

class TestHighConfidenceEvidence:
    """Case 10: High-confidence evidence contributes more."""

    def test_high_confidence_scores_higher_than_low(self) -> None:
        low = evaluate(_result(evidence=[_evidence(confidence=0.1)]))
        high = evaluate(_result(evidence=[_evidence(confidence=0.95)]))
        assert high.progress_score > low.progress_score

    def test_high_confidence_noted_in_reasons(self) -> None:
        result = _result(evidence=[_evidence(confidence=0.9)])
        report = evaluate(result)
        assert any("high confidence" in r.lower() or "0.75" in r for r in report.reasons)


# ---------------------------------------------------------------------------
# Section 36, Case 11: Multiple evidence types
# ---------------------------------------------------------------------------

class TestMultipleEvidenceTypes:
    """Case 11: Diverse evidence types earn diversity bonus."""

    def test_multiple_types_score_higher_than_single_type(self) -> None:
        single_type = _result(
            evidence=[
                _evidence(type="reconnaissance", confidence=0.6, summary="Finding A."),
                _evidence(type="reconnaissance", confidence=0.6, summary="Finding B."),
            ]
        )
        multi_type = _result(
            evidence=[
                _evidence(type="reconnaissance", confidence=0.6, summary="Finding A."),
                _evidence(type="correlation", confidence=0.6, summary="Finding B."),
            ]
        )
        report_single = evaluate(single_type)
        report_multi = evaluate(multi_type)
        assert report_multi.progress_score >= report_single.progress_score


# ---------------------------------------------------------------------------
# Section 36, Case 12: Meaningful progress signals
# ---------------------------------------------------------------------------

class TestMeaningfulProgressSignals:
    """Case 12: Signals like "reconnaissance" or "strong evidence" are recognized."""

    @pytest.mark.parametrize("signal,min_score", [
        ("reconnaissance", 15.0),
        ("surface discovered", 15.0),
        ("strong evidence", 45.0),
        ("correlated", 45.0),
        ("verified success", 65.0),
        ("verification", 65.0),
    ])
    def test_meaningful_signal_produces_adequate_score(
        self, signal: str, min_score: float
    ) -> None:
        result = _result(progress_signals=[signal])
        report = evaluate(result)
        assert report.progress_score >= min_score, (
            f"Signal '{signal}' should produce score >= {min_score}, got {report.progress_score}"
        )


# ---------------------------------------------------------------------------
# Section 36, Case 13: Generic / non-progress signals
# ---------------------------------------------------------------------------

class TestGenericNonProgressSignals:
    """Case 13: Generic signals like "worker started" do not produce high scores."""

    def test_generic_signal_scores_low(self) -> None:
        result = _result(progress_signals=["worker started"])
        report = evaluate(result)
        assert report.progress_score < 20.0

    def test_command_executed_signal_scores_low(self) -> None:
        result = _result(progress_signals=["command executed"])
        report = evaluate(result)
        assert report.progress_score < 20.0

    def test_many_generic_signals_do_not_stack(self) -> None:
        result = _result(
            progress_signals=["step 1", "step 2", "step 3", "step 4", "step 5"]
        )
        report = evaluate(result)
        assert report.progress_score < 20.0


# ---------------------------------------------------------------------------
# Section 36, Case 14: Timeout
# ---------------------------------------------------------------------------

class TestTimeoutResult:
    """Case 14: Timeout result is handled gracefully without crashing."""

    def test_timeout_not_solved(self) -> None:
        result = _result(error="investigation_timeout")
        report = evaluate(result)
        assert report.solved is False

    def test_timeout_score_reflects_available_evidence(self) -> None:
        result = _result(
            error="investigation_timeout",
            evidence=[_evidence(type="reconnaissance", confidence=0.5)],
            progress_signals=["reconnaissance"],
        )
        report = evaluate(result)
        # Should have some score from available evidence before timeout
        assert report.progress_score > 0.0

    def test_timeout_score_not_zero_if_evidence_exists(self) -> None:
        result = _result(
            error="investigation_timeout",
            evidence=[_evidence(type="correlation", confidence=0.7, summary="Partial.")],
        )
        report = evaluate(result)
        assert report.progress_score > 0.0

    def test_timeout_reason_mentions_error(self) -> None:
        result = _result(error="investigation_timeout")
        report = evaluate(result)
        error_mentioned = any("investigation_timeout" in r or "error" in r.lower() for r in report.reasons)
        assert error_mentioned

    def test_empty_timeout_scores_zero(self) -> None:
        result = _result(error="investigation_timeout")
        report = evaluate(result)
        # No evidence, no signals → should be 0 or very low
        assert report.progress_score == 0.0


# ---------------------------------------------------------------------------
# Section 36, Case 15: Muteki error
# ---------------------------------------------------------------------------

class TestMutekiErrorResult:
    """Case 15: Error field set does not crash the evaluator."""

    def test_error_result_not_solved(self) -> None:
        result = _result(error="muteki_adapter_error")
        report = evaluate(result)
        assert report.solved is False

    def test_error_result_produces_valid_report(self) -> None:
        result = _result(error="connection_failed")
        report = evaluate(result)
        assert 0.0 <= report.progress_score <= 100.0
        assert report.progress_level


# ---------------------------------------------------------------------------
# Section 36, Case 16: Malformed evidence
# ---------------------------------------------------------------------------

class TestMalformedEvidence:
    """Case 16: Malformed confidence values are clamped, not crashing."""

    def test_normalize_confidence_nan(self) -> None:
        assert normalize_confidence(float("nan")) == 0.0

    def test_normalize_confidence_inf(self) -> None:
        assert normalize_confidence(float("inf")) == 0.0
        assert normalize_confidence(float("-inf")) == 0.0

    def test_normalize_confidence_above_1(self) -> None:
        assert normalize_confidence(1.5) == 1.0

    def test_normalize_confidence_below_0(self) -> None:
        assert normalize_confidence(-0.5) == 0.0

    def test_normalize_confidence_valid_passthrough(self) -> None:
        assert normalize_confidence(0.75) == 0.75

    def test_normalize_result_clamps_confidence(self) -> None:
        # Build a result with a valid model (confidence must be 0.0-1.0 per Pydantic)
        # We test normalization with a valid edge case
        ev = _evidence(confidence=0.0)
        result = _result(evidence=[ev])
        normalized = normalize_result(result)
        assert all(0.0 <= e.confidence <= 1.0 for e in normalized.evidence)


# ---------------------------------------------------------------------------
# Section 36, Case 17: Missing progress_signals
# ---------------------------------------------------------------------------

class TestMissingProgressSignals:
    """Case 17: Missing progress_signals defaults to empty list."""

    def test_missing_signals_does_not_crash(self) -> None:
        result = _result(progress_signals=[])
        report = evaluate(result)
        assert report is not None

    def test_missing_signals_with_evidence_still_scores(self) -> None:
        result = _result(
            evidence=[_evidence(type="reconnaissance", confidence=0.6)],
            progress_signals=[],
        )
        report = evaluate(result)
        assert report.progress_score > 0.0


# ---------------------------------------------------------------------------
# Section 36, Case 18: Missing event_summary
# ---------------------------------------------------------------------------

class TestMissingEventSummary:
    """Case 18: Missing event_summary does not crash or invent content."""

    def test_empty_event_summary_is_fine(self) -> None:
        result = _result(event_summary=[])
        report = evaluate(result)
        assert report is not None


# ---------------------------------------------------------------------------
# Section 36, Cases 19 & 20: Score boundaries
# ---------------------------------------------------------------------------

class TestScoreBoundaries:
    """Cases 19 and 20: Score must always be in [0.0, 100.0]."""

    def test_score_lower_boundary_empty(self) -> None:
        report = evaluate(_result())
        assert report.progress_score >= 0.0

    def test_score_upper_boundary_solved(self) -> None:
        result = _result(solved=True)
        report = evaluate(result)
        assert report.progress_score <= 100.0

    def test_score_upper_boundary_exactly_100_when_solved(self) -> None:
        result = _result(solved=True)
        report = evaluate(result)
        assert report.progress_score == 100.0

    def test_score_never_nan(self) -> None:
        import math
        report = evaluate(_result())
        assert not math.isnan(report.progress_score)

    def test_score_never_infinite(self) -> None:
        import math
        report = evaluate(_result(
            evidence=[_evidence(confidence=1.0)] * 100,
        ))
        assert not math.isinf(report.progress_score)
        assert report.progress_score <= 100.0


# ---------------------------------------------------------------------------
# Section 36, Cases 21 & 22: Score regression and improvement
# ---------------------------------------------------------------------------

class TestScoreRegressionAndImprovement:
    """Cases 21 and 22: Score may go up or down; both are allowed."""

    def test_score_can_decrease(self) -> None:
        """Score regression is valid and must not be blocked."""
        result_strong = _result(
            evidence=[_evidence(type="correlation", confidence=0.9)],
            progress_signals=["strong evidence"],
        )
        result_weak = _result(
            evidence=[_evidence(type="observation", confidence=0.2)],
            progress_signals=["reconnaissance"],
        )
        strong_score = evaluate(result_strong).progress_score
        weak_score = evaluate(result_weak).progress_score
        assert weak_score < strong_score  # regression is valid

    def test_score_can_increase(self) -> None:
        result_weak = _result(progress_signals=["reconnaissance"])
        result_strong = _result(
            evidence=[_evidence(type="correlation", confidence=0.9)],
            progress_signals=["strong evidence"],
        )
        weak = evaluate(result_weak).progress_score
        strong = evaluate(result_strong).progress_score
        assert strong > weak


# ---------------------------------------------------------------------------
# Section 36, Cases 23 & 24: Stagnation and no stagnation
# ---------------------------------------------------------------------------

class TestStagnation:
    """Cases 23 and 24: Stagnation detection from score history."""

    def test_stagnation_detected_on_repeated_low_scores(self) -> None:
        history = [
            _score_report(progress_score=20.0, progress_level="reconnaissance"),
            _score_report(progress_score=21.0, progress_level="reconnaissance"),
        ]
        result = _result(progress_signals=["reconnaissance"])
        report = evaluate(result, history=history)
        assert report.stagnated is True

    def test_no_stagnation_when_score_improves_significantly(self) -> None:
        history = [
            _score_report(progress_score=20.0, progress_level="reconnaissance"),
            _score_report(progress_score=65.0, progress_level="strong evidence"),
        ]
        result = _result(progress_signals=["strong evidence"])
        report = evaluate(result, history=history)
        assert report.stagnated is False

    def test_no_stagnation_without_enough_history(self) -> None:
        history = [_score_report(progress_score=20.0)]
        result = _result(progress_signals=["reconnaissance"])
        report = evaluate(result, history=history)
        assert report.stagnated is False

    def test_no_stagnation_when_solved(self) -> None:
        history = [
            _score_report(progress_score=20.0, progress_level="reconnaissance"),
            _score_report(progress_score=21.0, progress_level="reconnaissance"),
        ]
        result = _result(solved=True)
        report = evaluate(result, history=history)
        assert report.stagnated is False

    def test_stagnation_requires_min_window(self) -> None:
        config = EvaluatorConfig(no_progress_window=3)
        history = [
            _score_report(progress_score=20.0, progress_level="reconnaissance"),
            _score_report(progress_score=21.0, progress_level="reconnaissance"),
        ]
        # Only 2 items but window=3 → not enough history → not stagnated
        stagnated = detect_stagnation(history, config)
        assert stagnated is False


# ---------------------------------------------------------------------------
# Section 36, Case 26: Repeated low score
# ---------------------------------------------------------------------------

class TestRepeatedLowScore:
    """Case 26: Repeated low scores in history trigger stagnation."""

    def test_repeated_low_score_triggers_stagnation(self) -> None:
        history = [
            _score_report(progress_score=5.0, progress_level="started"),
            _score_report(progress_score=6.0, progress_level="started"),
        ]
        stagnated = detect_stagnation(history)
        assert stagnated is True


# ---------------------------------------------------------------------------
# Section 36, Case 27: Solved cannot be inferred from score alone
# ---------------------------------------------------------------------------

class TestSolvedNotInferredFromScore:
    """Case 27: High score never implies solved=True."""

    def test_score_95_unsolved_stays_false(self) -> None:
        result = _result(
            solved=False,
            evidence=[
                _evidence(type="verified_success", confidence=1.0, summary="Apparent success."),
            ],
            progress_signals=["verified success", "strong evidence"],
        )
        report = evaluate(result)
        assert report.solved is False

    def test_no_solved_level_when_not_solved(self) -> None:
        result = _result(
            solved=False,
            evidence=[_evidence(type="verified_success", confidence=1.0, summary="Apparently solved.")],
            progress_signals=["verified success"],
        )
        report = evaluate(result)
        assert report.progress_level != "verified success"

    def test_score_100_unsolved_is_validated_not_verified(self) -> None:
        """Even if score reaches 100 without solved=True, level must not be 'verified success'."""
        level = determine_progress_level(100.0, solved=False)
        assert level != "verified success"

    def test_determine_solved_returns_false_when_not_solved(self) -> None:
        result = _result(solved=False)
        assert determine_solved(result) is False

    def test_determine_solved_returns_true_only_when_explicitly_true(self) -> None:
        result = _result(solved=True)
        assert determine_solved(result) is True


# ---------------------------------------------------------------------------
# Section 36, Case 28: Deterministic output
# ---------------------------------------------------------------------------

class TestDeterministicOutput:
    """Case 28: Same inputs always produce the same ScoreReport."""

    def test_same_result_produces_same_report(self) -> None:
        result = _result(
            evidence=[_evidence(type="correlation", confidence=0.82)],
            progress_signals=["strong evidence"],
            evidence_summary="Correlated.",
        )
        report_a = evaluate(result)
        report_b = evaluate(result)
        assert report_a.progress_score == report_b.progress_score
        assert report_a.progress_level == report_b.progress_level
        assert report_a.solved == report_b.solved
        assert report_a.reasons == report_b.reasons

    def test_same_history_produces_same_stagnation(self) -> None:
        history = [
            _score_report(progress_score=20.0, progress_level="reconnaissance"),
            _score_report(progress_score=21.0, progress_level="reconnaissance"),
        ]
        result = _result(progress_signals=["reconnaissance"])
        r1 = evaluate(result, history=history)
        r2 = evaluate(result, history=history)
        assert r1.stagnated == r2.stagnated


# ---------------------------------------------------------------------------
# Section 37: Three-round deterministic fixture test
# ---------------------------------------------------------------------------

class TestThreeRoundFixture:
    """Section 37: Evaluator produces correct levels for the mock scenario results."""

    @pytest.fixture
    def round_one_result(self) -> InvestigationResult:
        """Mimics MockMutekiAdapter._round_one() output."""
        return _result(
            solved=False,
            evidence=[_evidence(type="reconnaissance", confidence=0.55, summary="Initial sandbox surface mapped.")],
            evidence_summary="Useful initial understanding, but no verified success.",
            progress_signals=["reconnaissance"],
            elapsed_seconds=1.0,
            event_summary=["Initial sandbox surface mapped."],
        )

    @pytest.fixture
    def round_two_result(self) -> InvestigationResult:
        """Mimics MockMutekiAdapter._round_two() output."""
        return _result(
            solved=False,
            evidence=[_evidence(type="correlation", confidence=0.82, summary="Independent observations agree on the leading hypothesis.")],
            evidence_summary="Strong evidence, but the success condition is not verified.",
            progress_signals=["strong evidence"],
            elapsed_seconds=2.0,
            event_summary=["Independent observations agree on the leading hypothesis."],
        )

    @pytest.fixture
    def round_three_result(self) -> InvestigationResult:
        """Mimics MockMutekiAdapter._round_three() output."""
        return _result(
            solved=True,
            evidence=[_evidence(type="verified_success", confidence=1.0, summary="The trusted mock success condition was verified.")],
            evidence_summary="Verified success.",
            progress_signals=["verified success"],
            elapsed_seconds=3.0,
            event_summary=["The trusted mock success condition was verified."],
        )

    def test_round_one_level_is_reconnaissance(
        self, round_one_result: InvestigationResult
    ) -> None:
        report = evaluate(round_one_result)
        assert report.progress_level == "reconnaissance", (
            f"Round 1 should be 'reconnaissance', got '{report.progress_level}' "
            f"(score={report.progress_score})"
        )

    def test_round_one_not_solved(
        self, round_one_result: InvestigationResult
    ) -> None:
        report = evaluate(round_one_result)
        assert report.solved is False

    def test_round_one_score_in_expected_range(
        self, round_one_result: InvestigationResult
    ) -> None:
        report = evaluate(round_one_result)
        assert 15.0 <= report.progress_score <= 45.0, (
            f"Round 1 score expected 15-45, got {report.progress_score}"
        )

    def test_round_two_level_is_strong_evidence(
        self, round_two_result: InvestigationResult
    ) -> None:
        report = evaluate(round_two_result)
        assert report.progress_level == "strong evidence", (
            f"Round 2 should be 'strong evidence', got '{report.progress_level}' "
            f"(score={report.progress_score})"
        )

    def test_round_two_not_solved(
        self, round_two_result: InvestigationResult
    ) -> None:
        report = evaluate(round_two_result)
        assert report.solved is False

    def test_round_two_score_in_expected_range(
        self, round_two_result: InvestigationResult
    ) -> None:
        report = evaluate(round_two_result)
        assert 55.0 <= report.progress_score <= 85.0, (
            f"Round 2 score expected 55-85, got {report.progress_score}"
        )

    def test_round_two_scores_higher_than_round_one(
        self,
        round_one_result: InvestigationResult,
        round_two_result: InvestigationResult,
    ) -> None:
        r1 = evaluate(round_one_result)
        r2 = evaluate(round_two_result)
        assert r2.progress_score > r1.progress_score

    def test_round_three_level_is_verified_success(
        self, round_three_result: InvestigationResult
    ) -> None:
        report = evaluate(round_three_result)
        assert report.progress_level == "verified success"

    def test_round_three_solved(
        self, round_three_result: InvestigationResult
    ) -> None:
        report = evaluate(round_three_result)
        assert report.solved is True

    def test_round_three_score_is_100(
        self, round_three_result: InvestigationResult
    ) -> None:
        report = evaluate(round_three_result)
        assert report.progress_score == 100.0

    def test_three_round_progression_is_deterministic(
        self,
        round_one_result: InvestigationResult,
        round_two_result: InvestigationResult,
        round_three_result: InvestigationResult,
    ) -> None:
        r1a = evaluate(round_one_result)
        r2a = evaluate(round_two_result, history=[r1a])
        r3a = evaluate(round_three_result, history=[r1a, r2a])

        r1b = evaluate(round_one_result)
        r2b = evaluate(round_two_result, history=[r1b])
        r3b = evaluate(round_three_result, history=[r1b, r2b])

        assert r1a.progress_score == r1b.progress_score
        assert r2a.progress_score == r2b.progress_score
        assert r3a.progress_score == r3b.progress_score


# ---------------------------------------------------------------------------
# Progress level mapping tests
# ---------------------------------------------------------------------------

class TestProgressLevelMapping:
    """Verify progress level strings are correct and UI-compatible."""

    @pytest.mark.parametrize("score,solved,expected_level", [
        (0.0, False, "started"),
        (1.0, False, "reconnaissance"),
        (35.0, False, "reconnaissance"),
        (36.0, False, "partial evidence"),
        (59.0, False, "partial evidence"),
        (60.0, False, "strong evidence"),
        (84.0, False, "strong evidence"),
        (85.0, False, "validated"),
        (99.9, False, "validated"),
        (100.0, True, "verified success"),
        (50.0, True, "verified success"),  # solved=True overrides score
    ])
    def test_progress_level_mapping(
        self, score: float, solved: bool, expected_level: str
    ) -> None:
        assert determine_progress_level(score, solved) == expected_level

    def test_ui_compatible_levels_are_used(self) -> None:
        """UI uses 'reconnaissance', 'strong evidence', 'verified success' — verify they appear."""
        ui_levels = {"reconnaissance", "strong evidence", "verified success"}
        all_levels = {
            determine_progress_level(s, f)
            for s, f in [
                (20.0, False), (70.0, False), (100.0, True)
            ]
        }
        assert ui_levels == all_levels


# ---------------------------------------------------------------------------
# Strategy Engine compatibility
# ---------------------------------------------------------------------------

class TestStrategyEngineCompatibility:
    """Verify ScoreReport shape is consumable by the Strategy Engine."""

    def test_report_has_all_expected_fields(self) -> None:
        report = evaluate(_result(progress_signals=["reconnaissance"]))
        assert hasattr(report, "progress_score")
        assert hasattr(report, "solved")
        assert hasattr(report, "progress_level")
        assert hasattr(report, "reasons")
        assert hasattr(report, "stagnated")

    def test_report_progress_level_is_non_empty_string(self) -> None:
        report = evaluate(_result())
        assert isinstance(report.progress_level, str)
        assert len(report.progress_level) > 0

    def test_report_reasons_is_list_of_strings(self) -> None:
        report = evaluate(_result())
        assert isinstance(report.reasons, (list, tuple))
        assert all(isinstance(r, str) for r in report.reasons)
