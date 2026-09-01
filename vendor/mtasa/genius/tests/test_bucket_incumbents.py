import json
from pathlib import Path

from fool.bucket_incumbents import BucketIncumbents


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_empty_store_returns_no_incumbents(tmp_path):
    store = BucketIncumbents(tmp_path / "buckets")
    assert store.scores() == {}
    assert store.champion_path("scarce_couriers_seed401") is None


def test_record_creates_champion_and_meta(tmp_path):
    store = BucketIncumbents(tmp_path / "buckets")
    solver = _write(tmp_path / "solver_v007.py", "def solve(x): return []\n")
    store.record(bucket="scarce_couriers_seed401", solver_path=solver, score=950.0, round_index=7, global_v=18)
    assert store.scores() == {"scarce_couriers_seed401": 950.0}
    champ = store.champion_path("scarce_couriers_seed401")
    assert champ is not None and champ.exists()
    assert "def solve" in champ.read_text(encoding="utf-8")
    meta = json.loads((store.root / "scarce_couriers_seed401" / "meta.json").read_text(encoding="utf-8"))
    assert meta["score"] == 950.0
    assert meta["round"] == 7
    assert meta["global_v"] == 18


def test_record_overwrites_previous_champion(tmp_path):
    store = BucketIncumbents(tmp_path / "buckets")
    s1 = _write(tmp_path / "a.py", "# v1\ndef solve(x): return []\n")
    s2 = _write(tmp_path / "b.py", "# v2\ndef solve(x): return []\n")
    store.record(bucket="large_seed301", solver_path=s1, score=800.0, round_index=1, global_v=1)
    store.record(bucket="large_seed301", solver_path=s2, score=750.0, round_index=2, global_v=2)
    assert store.scores()["large_seed301"] == 750.0
    assert "# v2" in store.champion_path("large_seed301").read_text(encoding="utf-8")


def test_seed_from_legacy_global_best_populates_every_known_bucket(tmp_path):
    store = BucketIncumbents(tmp_path / "buckets")
    legacy = _write(tmp_path / "best_solver.py", "def solve(x): return []\n")
    bucket_scores = {
        "scarce_couriers_seed401": 950.0,
        "large_seed301": 760.0,
    }
    store.seed_from_legacy(solver_path=legacy, bucket_scores=bucket_scores)
    assert store.scores() == bucket_scores
    for b in ("scarce_couriers_seed401", "large_seed301"):
        assert store.champion_path(b).read_text(encoding="utf-8") == legacy.read_text(encoding="utf-8")
