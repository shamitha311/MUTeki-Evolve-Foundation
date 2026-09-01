from pathlib import Path

from fool.bucket_classify import (
    RoundOutcome,
    classify_round_bucketed,
    parse_bucket_scores,
)


REPORT_FIXTURE = """Average Penalty Score
760.25
Completed Cases
10 / 10

high_noise_seed601
580.83
30/30(100.0%)
141ms
details: uncovered=0, extra_notify=26, selected_lines=29, visible_total=580.83

large_seed301
753.80
40/40(100.0%)
216ms
details: uncovered=0, extra_notify=32, selected_lines=40, visible_total=753.80
"""


def test_parse_bucket_scores_extracts_each_bucket(tmp_path):
    report = tmp_path / "report.txt"
    report.write_text(REPORT_FIXTURE, encoding="utf-8")
    result = parse_bucket_scores(report)
    assert result == {
        "high_noise_seed601": 580.83,
        "large_seed301": 753.80,
    }


def test_parse_bucket_scores_missing_file_returns_empty(tmp_path):
    result = parse_bucket_scores(tmp_path / "nope.txt")
    assert result == {}


def test_classify_improved_when_target_bucket_strictly_better_others_in_band():
    incumbents = {"scarce_couriers_seed401": 950.0, "large_seed301": 760.0, "high_noise_seed601": 580.0}
    new_scores = {"scarce_couriers_seed401": 900.0, "large_seed301": 760.5, "high_noise_seed601": 580.2}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "improved"
    assert outcome.bucket_replacements == {"scarce_couriers_seed401"}
    assert outcome.broken_buckets == set()


def test_classify_regressed_when_other_bucket_breaks_its_incumbent():
    incumbents = {"scarce_couriers_seed401": 950.0, "large_seed301": 760.0}
    new_scores = {"scarce_couriers_seed401": 900.0, "large_seed301": 800.0}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "regressed"
    assert "large_seed301" in outcome.broken_buckets
    assert outcome.bucket_replacements == {"scarce_couriers_seed401"}


def test_classify_neutral_label_but_strict_replace_when_target_only_in_band():
    """Sub-band improvement: label stays neutral (denoised) but the champion
    is still replaced (strict, so "桶下界" floor stays monotone)."""
    incumbents = {"scarce_couriers_seed401": 950.0}
    new_scores = {"scarce_couriers_seed401": 949.0}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "neutral"
    assert outcome.bucket_replacements == {"scarce_couriers_seed401"}


def test_classify_neutral_when_target_strictly_equal():
    """No strict improvement: neither replaced nor labeled improved."""
    incumbents = {"scarce_couriers_seed401": 950.0}
    new_scores = {"scarce_couriers_seed401": 950.0}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "neutral"
    assert outcome.bucket_replacements == set()


def test_classify_baseline_when_no_incumbents_yet():
    outcome = classify_round_bucketed(
        new_scores={"scarce_couriers_seed401": 900.0},
        bucket_incumbents={},
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "baseline"
    assert outcome.bucket_replacements == {"scarce_couriers_seed401"}


def test_classify_silent_improvement_in_non_target_bucket_is_kept():
    incumbents = {"scarce_couriers_seed401": 950.0, "large_seed301": 760.0}
    new_scores = {"scarce_couriers_seed401": 949.5, "large_seed301": 700.0}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "improved"
    # Both replaced: large_seed301 (super-band) and scarce_couriers (sub-band
    # but strictly < incumbent). Strict replacement keeps floor monotone.
    assert outcome.bucket_replacements == {"large_seed301", "scarce_couriers_seed401"}


def test_classify_catastrophic_when_any_bucket_more_than_50pct_worse():
    incumbents = {"large_seed301": 760.0}
    new_scores = {"large_seed301": 1200.0}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["large_seed301"],
        band_rel=0.003,
    )
    assert outcome.label == "catastrophic"
