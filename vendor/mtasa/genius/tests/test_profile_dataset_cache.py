from pathlib import Path

from fool.harness.tools import (
    ToolContext,
    _profile_dataset_clear_cache,
    _t_profile_dataset,
)


def _ctx(*, run_id: str, iteration: int, dataset_fp: str = "fp_alpha") -> ToolContext:
    return ToolContext(
        input_dir=Path("/tmp/unused"),
        run_dir=Path("/tmp/unused"),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
        run_id=run_id,
        iteration=iteration,
        dataset_fp=dataset_fp,
    )


def test_first_call_returns_full_content_second_returns_cached_stub():
    _profile_dataset_clear_cache()
    args = {"bucket": "scarce_couriers_seed401"}
    r1 = _t_profile_dataset(_ctx(run_id="run_A", iteration=1), args)
    assert r1.ok
    assert not r1.content.startswith("(cached)")
    full_len = len(r1.content)
    assert full_len > 50  # real JSON, not a stub

    r2 = _t_profile_dataset(_ctx(run_id="run_A", iteration=5), args)
    assert r2.ok
    assert r2.content.startswith("(cached)")
    assert "round 1" in r2.content
    assert f"{full_len}B" in r2.content
    assert len(r2.content) < full_len  # stub is much shorter


def test_different_args_do_not_collide():
    _profile_dataset_clear_cache()
    ctx = _ctx(run_id="run_B", iteration=1)
    r_bucket = _t_profile_dataset(ctx, {"bucket": "scarce_couriers_seed401"})
    r_field = _t_profile_dataset(ctx, {"field": "solo_dominates_combo_frac"})
    assert not r_bucket.content.startswith("(cached)")
    assert not r_field.content.startswith("(cached)")


def test_new_run_id_invalidates_cache():
    _profile_dataset_clear_cache()
    args = {"bucket": "large_seed301"}
    _t_profile_dataset(_ctx(run_id="run_C", iteration=1), args)
    # New run, same args → should re-issue full content, not stub from run_C.
    r = _t_profile_dataset(_ctx(run_id="run_D", iteration=1), args)
    assert not r.content.startswith("(cached)")


def test_bucket_list_and_no_args_skip_cache():
    """bucket='list' returns the bucket-name array; no-args returns the default
    structural cross-bucket slice. Both should remain functional after caching."""
    _profile_dataset_clear_cache()
    r_list = _t_profile_dataset(_ctx(run_id="run_E", iteration=1), {"bucket": "list"})
    assert r_list.ok
    assert "[" in r_list.content
    r_default = _t_profile_dataset(_ctx(run_id="run_E", iteration=1), {})
    assert r_default.ok
    assert "structural" in r_default.content


def test_bucket_plus_field_returns_both():
    _profile_dataset_clear_cache()
    ctx = _ctx(run_id="run_F", iteration=1)
    r = _t_profile_dataset(ctx, {"bucket": "large_seed301", "field": "score.mean"})
    assert r.ok
    assert "bucket=large_seed301 (all fields)" in r.content
    assert "field=score.mean across 10 buckets" in r.content


def test_field_ambiguous_requires_dotted():
    _profile_dataset_clear_cache()
    ctx = _ctx(run_id="run_G", iteration=1)
    r = _t_profile_dataset(ctx, {"field": "mean"})
    assert not r.ok
    assert "ambiguous" in r.content
    assert "score.mean" in r.content


def test_field_list_enumerates():
    _profile_dataset_clear_cache()
    ctx = _ctx(run_id="run_H", iteration=1)
    r = _t_profile_dataset(ctx, {"field": "list"})
    assert r.ok
    assert "structural.courier_ratio" in r.content
    assert "probes.solo_dominates_combo_frac" in r.content
