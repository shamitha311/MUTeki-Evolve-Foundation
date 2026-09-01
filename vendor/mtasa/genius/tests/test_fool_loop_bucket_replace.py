from fool.bucket_classify import classify_round_bucketed
from fool.bucket_incumbents import BucketIncumbents


def test_bucket_replacement_records_only_strictly_improved_buckets(tmp_path):
    """Round improves scarce by -50 but leaves large within band → only scarce
    is replaced; large keeps its previous champion."""
    incs = BucketIncumbents(tmp_path / "buckets")
    s_old = tmp_path / "old.py"
    s_old.write_text("def solve(x): return []\n", encoding="utf-8")
    s_new = tmp_path / "new.py"
    s_new.write_text("# new\ndef solve(x): return []\n", encoding="utf-8")
    incs.record(bucket="scarce_couriers_seed401", solver_path=s_old, score=950.0, round_index=1)
    incs.record(bucket="large_seed301", solver_path=s_old, score=760.0, round_index=1)

    new_scores = {"scarce_couriers_seed401": 900.0, "large_seed301": 760.5}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incs.scores(),
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    for bucket in outcome.bucket_replacements:
        incs.record(bucket=bucket, solver_path=s_new, score=new_scores[bucket], round_index=2)

    assert outcome.label == "improved"
    assert incs.scores()["scarce_couriers_seed401"] == 900.0
    assert incs.scores()["large_seed301"] == 760.0  # untouched
    assert "# new" in incs.champion_path("scarce_couriers_seed401").read_text(encoding="utf-8")
    assert "# new" not in incs.champion_path("large_seed301").read_text(encoding="utf-8")
