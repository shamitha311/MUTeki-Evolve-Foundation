from __future__ import annotations

from pathlib import Path

from fool.harness.context import RoundOutcome, RoundState
from fool.harness.prompt import (
    build_prefix,
    build_round_header,
    format_tool_user_message,
)
from fool.harness.tools import build_default_registry


def test_prefix_contains_identity_constraints_and_tools(tmp_path: Path) -> None:
    registry = build_default_registry()
    prefix = build_prefix(registry)
    assert "AutoSolver Agent" in prefix
    assert "python3.6" in prefix or "Python 3.6" in prefix
    assert "标准库" in prefix or "stdlib" in prefix.lower()
    assert "solve(input_text)" in prefix
    assert "block_patch" in prefix
    # retrieve_guidance is intentionally disabled — the prompt must not mention it.
    assert "retrieve_guidance" not in prefix
    assert "多策略框架" not in prefix  # bottleneck guide must NOT be inlined into prefix
    assert "Data strategy playbook" in prefix
    # local smoke vs Genius submission scoring divergence is a hard fact and
    # must live in the cached prefix, not just the tool-result caveat.
    assert "本地 smoke 预览 vs Genius 提交评分" in prefix
    assert "不完全重合" in prefix
    assert "<tool>" in prefix
    assert "<final>" in prefix


def test_round_header_renders_state_and_history(tmp_path: Path) -> None:
    state = RoundState(
        iteration=4,
        best_score=120.5,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[
            RoundOutcome(iteration=1, score=200.0, hypothesis="baseline", outcome="improved"),
            RoundOutcome(iteration=2, score=130.0, hypothesis="tighter pruning", outcome="improved"),
            RoundOutcome(iteration=3, score=300.0, hypothesis="aggressive swap", outcome="regressed"),
        ],
        input_dir=tmp_path / "in",
        run_dir=tmp_path / "run",
    )
    msg = build_round_header(state)
    assert "Round: 4" in msg
    assert "Best score so far: 120.5" in msg
    assert "i=1" in msg and "improved" in msg
    assert "i=3" in msg and "regressed" in msg
    assert "Next step:" in msg


def test_round_header_handles_empty_history_and_no_best(tmp_path: Path) -> None:
    state = RoundState(
        iteration=1,
        best_score=None,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[],
        input_dir=tmp_path / "in",
        run_dir=tmp_path / "run",
    )
    msg = build_round_header(state)
    assert "Round: 1" in msg
    assert "Best score so far: none" in msg
    assert "Recent rounds:" in msg


def test_prefix_recommends_intent_and_round1_recap() -> None:
    registry = build_default_registry()
    prefix = build_prefix(registry)
    assert "<intent>" in prefix
    # round-1 recap remains a hard requirement even though per-step intent
    # was softened — see parser.py / runner.py intent_missing handling.
    assert "递进式复盘" in prefix


def test_round_header_includes_prior_plan_when_present(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "harness_v002.json").write_text(
        '{"iteration":2,"transcript":[],"final":{"plan":{'
        '"hypothesis":"prune low-w","analysis":"low_w drags avg",'
        '"target_buckets":["low_willingness_seed501"],'
        '"edit_plan":["sort by score/w","drop ties"]}}}',
        encoding="utf-8",
    )
    (run_dir / "report_v002.txt").write_text(
        "average_score=210.5 valid_cases=9/10\n- tiny_seed42: score=12.0\n",
        encoding="utf-8",
    )
    state = RoundState(
        iteration=3,
        best_score=210.5,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[
            RoundOutcome(iteration=2, score=210.5, hypothesis="prune low-w", outcome="improved"),
        ],
        input_dir=tmp_path / "in",
        run_dir=run_dir,
    )
    msg = build_round_header(state)
    assert "Prior round v002 plan" in msg
    assert "prune low-w" in msg
    assert "low_willingness_seed501" in msg
    assert "Prior round v002 report" in msg
    assert "average_score=210.5" in msg
    assert "递进式复盘" in msg


def test_round_header_guides_followup_when_prior_direction_improved(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = RoundState(
        iteration=3,
        best_score=180.0,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[
            RoundOutcome(iteration=2, score=180.0, hypothesis="limit scarce backups", outcome="improved"),
        ],
        input_dir=tmp_path / "in",
        run_dir=run_dir,
    )

    msg = build_round_header(state)

    assert "Teacher Review" in msg
    assert "已饱和方向" in msg
    assert "给出下一轮可执行的具体意见" in msg


def test_round_header_skips_prior_block_when_files_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = RoundState(
        iteration=2,
        best_score=None,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[],
        input_dir=tmp_path / "in",
        run_dir=run_dir,
    )
    msg = build_round_header(state)
    assert "Prior round v001 plan" not in msg
    assert "Prior round v001 report" not in msg
    assert "Next step:" in msg


def test_round_header_includes_prior_round_bucket_impact(tmp_path: Path) -> None:
    """Closed loop: prev plan declared target_buckets, header surfaces actual Δ."""
    import json as _json

    run_dir = tmp_path / "out" / "runs" / "run_x"
    run_dir.mkdir(parents=True)
    # version_index.json sits at out/version_index.json
    index_path = tmp_path / "out" / "version_index.json"
    index_path.write_text(
        _json.dumps({
            "next": 4,
            "entries": [
                {
                    "v": 1, "run_id": "run_x", "iteration": 1, "score": 1500.0,
                    "bucket_scores": {
                        "scarce_couriers_seed401": 3000.0,
                        "low_willingness_seed501": 2400.0,
                        "tiny_seed42": 350.0,
                    },
                },
                {
                    "v": 2, "run_id": "run_x", "iteration": 2, "score": 1400.0,
                    "bucket_scores": {
                        "scarce_couriers_seed401": 2700.0,   # ↓300 (target hit)
                        "low_willingness_seed501": 2450.0,   # ↑50  (collateral)
                        "tiny_seed42": 350.0,                # unchanged
                    },
                },
            ],
        }),
        encoding="utf-8",
    )
    (run_dir / "harness_v002.json").write_text(
        '{"iteration":2,"transcript":[],"final":{"plan":{'
        '"hypothesis":"scarce coverage greedy",'
        '"analysis":"focus scarce",'
        '"target_buckets":["scarce_seed401"],'
        '"edit_plan":["greedy coverage"]}}}',
        encoding="utf-8",
    )

    state = RoundState(
        iteration=3,
        best_score=1400.0,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[
            RoundOutcome(iteration=2, score=1400.0, hypothesis="scarce coverage greedy", outcome="improved"),
        ],
        input_dir=tmp_path / "in",
        run_dir=run_dir,
    )
    msg = build_round_header(state)

    # Target-bucket closed loop: short form 'scarce_seed401' must match
    # 'scarce_couriers_seed401' in bucket_scores
    assert "Prior round v2 bucket impact" in msg
    assert "scarce_seed401→scarce_couriers_seed401" in msg
    assert "Δ=-300.00" in msg  # target moved as intended
    # Full-bucket one-liner should also show collateral movement
    assert "all buckets Δ vs base" in msg
    assert "low:+50.00" in msg  # collateral regression on low_w
    assert "scarce:-300.00" in msg


def test_round_header_bucket_impact_skipped_without_index(tmp_path: Path) -> None:
    run_dir = tmp_path / "out" / "runs" / "run_y"
    run_dir.mkdir(parents=True)
    state = RoundState(
        iteration=3,
        best_score=None,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[],
        input_dir=tmp_path / "in",
        run_dir=run_dir,
    )
    msg = build_round_header(state)
    assert "bucket impact" not in msg


def test_format_tool_user_message_includes_name_status_and_args() -> None:
    msg = format_tool_user_message(
        name="profile_dataset",
        args={"path": "data/sample"},
        ok=True,
        content="cases=10\nschema=ok",
    )
    assert msg.startswith("[tool_result name=profile_dataset status=ok]")
    assert '"path": "data/sample"' in msg
    assert "cases=10" in msg


def test_format_tool_user_message_failure_status() -> None:
    msg = format_tool_user_message(name="patch_solver", args={}, ok=False, content="not found")
    assert "status=fail" in msg
    assert "not found" in msg
