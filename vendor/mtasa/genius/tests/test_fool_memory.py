from __future__ import annotations

from pathlib import Path

from fool.fool_loop import _dataset_memory_scope, _resolve_bootstrap_solver_path
from fool.memory_store import FoolMemory
from fool.agent_tools import ToolContext, build_default_registry


def test_memory_retrieves_scored_experience_and_blocks_failed_repeat(tmp_path) -> None:
    memory = FoolMemory(tmp_path / "memory")
    memory.record(
        {
            "iteration": 1,
            "hypothesis": "Increase strict pruning for scarce couriers.",
            "reason": "Try reducing conflicts.",
            "target_buckets": ["scarce_couriers_seed401"],
            "outcome": "rollback",
            "score_delta": 310.0,
        }
    )
    memory.record(
        {
            "iteration": 2,
            "hypothesis": "Use willingness tie-break on noisy tasks.",
            "reason": "Avoid costly low-confidence choices.",
            "target_buckets": ["high_noise_seed601"],
            "outcome": "improved",
            "score_delta": -12.0,
        }
    )

    recalled = memory.retrieve(
        target_buckets=["scarce_couriers_seed401"],
        strategy_lane="coverage_recovery",
        query_text="uncovered courier conflict",
    )

    assert "Increase strict pruning" in recalled
    assert memory.blocked_hypotheses() == ["Increase strict pruning for scarce couriers."]
    assert (tmp_path / "memory" / "episodes.jsonl").exists()
    assert (tmp_path / "memory" / "MEMORY.md").exists()


def test_memory_retrieve_prefers_recent_gain_with_case_tags(tmp_path) -> None:
    memory = FoolMemory(tmp_path / "memory")
    memory.record(
        {
            "iteration": 1,
            "hypothesis": "old low_w sort tweak",
            "reason": "small low_w adjustment",
            "target_buckets": ["low_willingness_seed501"],
            "outcome": "improved",
            "score_delta": -8.0,
        }
    )
    memory.record(
        {
            "iteration": 2,
            "hypothesis": "recent low_w tie-break with willingness",
            "reason": "stronger low_w gain",
            "target_buckets": ["low_willingness_seed501"],
            "outcome": "improved",
            "score_delta": -36.0,
        }
    )

    recalled = memory.retrieve(
        target_buckets=["low_willingness_seed501"],
        strategy_lane="incremental_refine",
        query_text="willingness tie-break",
        keywords=["willingness", "tie-break"],
        case_tags=["low_w"],
        limit=2,
    )

    first_result_line = next(line for line in recalled.splitlines() if line.startswith("- iter="))
    assert "iter=2" in first_result_line
    assert "BM25-hybrid" in recalled


def test_memory_retrieve_guidance_uses_teacher_and_skill_docs(tmp_path) -> None:
    memory = FoolMemory(tmp_path / "memory")
    memory.record(
        {
            "iteration": 1,
            "hypothesis": "scarce coverage-first ordering",
            "reason": "recover uncovered in scarce",
            "target_buckets": ["scarce_couriers_seed401"],
            "outcome": "improved",
            "score_delta": -25.0,
        }
    )

    recalled = memory.retrieve_guidance(
        query_text="scarce coverage priority",
        keywords=["coverage", "priority"],
        case_tags=["scarce"],
        limit=3,
    )

    assert "BM25-hybrid" in recalled
    assert "Guidance steel-stamp" in recalled
    assert "source=" in recalled
    assert (
        "teacher/DATA_STRATEGY_PLAYBOOK.md" in recalled
        or "teacher/MTASA_BOTTLENECK_OPTIMIZATION_GUIDE_CN.md" in recalled
        or "skill.md" in recalled
    )


def test_dataset_memory_scope_changes_with_input_contents(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "case01.txt").write_text("same\n", encoding="utf-8")
    (right / "case01.txt").write_text("same\n", encoding="utf-8")

    assert _dataset_memory_scope(str(left)) == _dataset_memory_scope(str(right))

    (right / "case01.txt").write_text("different\n", encoding="utf-8")
    assert _dataset_memory_scope(str(left)) != _dataset_memory_scope(str(right))


def test_resolve_bootstrap_solver_path_falls_back_to_repo_default(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    default = root / "data" / "official" / "example_solution.txt"
    default.parent.mkdir(parents=True)
    default.write_text("def solve(input_text):\n    return []\n", encoding="utf-8")

    monkeypatch.setattr("fool.fool_loop.ROOT", root)
    resolved = _resolve_bootstrap_solver_path("/Users/zhuym/Documents/101camp/MTASA/data/official/example_solution.txt")

    assert resolved == default


def test_resolve_bootstrap_solver_path_keeps_valid_relative_path(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    custom = root / "fool" / "templates" / "solver_custom.py"
    custom.parent.mkdir(parents=True)
    custom.write_text("def solve(input_text):\n    return []\n", encoding="utf-8")

    monkeypatch.setattr("fool.fool_loop.ROOT", root)
    resolved = _resolve_bootstrap_solver_path("fool/templates/solver_custom.py")

    assert resolved == custom


def test_memory_records_brain_plans_before_scored_outcome(tmp_path: Path) -> None:
    memory = FoolMemory(tmp_path / "memory")
    memory.record_plan(
        {
            "iteration": 3,
            "hypothesis": "只调整 low_willingness 的排序规则",
            "strategy_lane": "incremental_refine",
            "target_buckets": ["low_willingness_seed501"],
            "edit_plan": ["保留 incumbent parser", "替换低意愿 tie-break"],
        }
    )

    recalled = memory.retrieve_plans(target_buckets=["low_willingness_seed501"])

    assert "low_willingness" in recalled
    assert "替换低意愿 tie-break" in recalled
    assert (tmp_path / "memory" / "plans.jsonl").exists()


def test_memory_records_and_retrieves_run_lessons(tmp_path: Path) -> None:
    memory = FoolMemory(tmp_path / "memory")
    memory.record_run_lesson(
        {
            "run_id": "run_a",
            "baseline_score": 1598.76,
            "best_score": 1102.48,
            "total_delta": -496.28,
            "bottlenecks": ["scarce_seed401", "low_w_seed501"],
            "winning_cases": ["large_seed301(-1258.4)"],
            "worked": ["template-backed loww_regret exploration"],
            "avoid": ["generic fallback threshold tweak"],
            "next_steps": ["protect broad gains and only target low_w/scarce"],
        }
    )
    memory.record_run_lesson(
        {
            "run_id": "run_a",
            "baseline_score": 1598.76,
            "best_score": 1101.00,
            "total_delta": -497.76,
            "bottlenecks": ["scarce_seed401"],
            "worked": ["updated lesson"],
            "avoid": ["repeat failed idea"],
            "next_steps": ["target visible cost"],
        }
    )

    recalled = memory.retrieve_run_lessons(target_buckets=["scarce_seed401"])

    assert "run_a" in recalled
    assert "1101.00" in recalled
    assert "target visible cost" in recalled
    assert len((tmp_path / "memory" / "run_lessons.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert (tmp_path / "memory" / "RUN_LESSONS.md").exists()


def test_rank_bottlenecks_reads_genius_report_field_names(tmp_path: Path) -> None:
    registry = build_default_registry()
    context = ToolContext(
        input_dir=tmp_path,
        run_dir=tmp_path,
        memory_scope="test",
        dataset_profile="profile",
        best_report={
            "cases": [
                {
                    "case_name": "scarce_seed401",
                    "score": 2624.04,
                    "covered": 22,
                    "total_tasks": 40,
                    "uncovered_tasks": 18,
                    "extra_notify": 0,
                }
            ]
        },
    )

    result = registry.run("rank_bottlenecks", context, {"top_k": 1})

    assert result.ok
    assert "scarce_seed401" in result.summary
    assert "coverage=22/40" in result.summary
    assert "uncovered=18" in result.summary


def test_record_session_summary_persists_and_surfaces_in_retrieve(tmp_path):
    memory = FoolMemory(tmp_path / "memory")

    memory.record_session_summary(
        run_id="run-001",
        iteration=3,
        target_buckets=["medium_seed201"],
        summary_text="## 目标\n收紧 willingness 加权\n## 进展\n### 已完成\n- [x] baseline\n",
    )

    # Persisted file exists at memory.session_summaries_path
    assert memory.session_summaries_path.exists()
    line = memory.session_summaries_path.read_text(encoding="utf-8").strip()
    assert "willingness" in line
    assert "run-001" in line

    # retrieve() output includes the session summaries block.
    # Note: retrieve() requires episodes to exist (it short-circuits with
    # "No durable memory recorded yet." when episodes are empty), so record
    # one dummy episode first.
    memory.record({
        "iteration": 1,
        "hypothesis": "baseline",
        "outcome": "baseline",
        "score_delta": 0.0,
        "target_buckets": ["medium_seed201"],
        "reason": "init",
    })

    out = memory.retrieve(
        target_buckets=["medium_seed201"],
        strategy_lane="willingness",
        query_text="willingness 加权",
    )
    assert "Prior session summaries" in out
    assert "willingness 加权" in out or "willingness" in out


def test_record_session_summary_appends_multiple(tmp_path):
    memory = FoolMemory(tmp_path / "memory")
    memory.record_session_summary(run_id="r1", iteration=1, target_buckets=["a"], summary_text="## S1\n")
    memory.record_session_summary(run_id="r1", iteration=2, target_buckets=["a"], summary_text="## S2\n")
    lines = [l for l in memory.session_summaries_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2


def test_record_session_summary_skips_empty(tmp_path):
    memory = FoolMemory(tmp_path / "memory")
    memory.record_session_summary(run_id="r1", iteration=1, target_buckets=[], summary_text="")
    memory.record_session_summary(run_id="r1", iteration=1, target_buckets=[], summary_text="   \n  ")
    assert not memory.session_summaries_path.exists() or memory.session_summaries_path.read_text(encoding="utf-8").strip() == ""


def test_fool_memory_exposes_bucket_incumbents(tmp_path):
    from fool.memory_store import FoolMemory
    mem = FoolMemory(memory_dir=tmp_path / "mem")
    assert mem.bucket_incumbents.root == tmp_path / "mem" / "buckets"
    assert mem.bucket_incumbents.scores() == {}


def test_bucket_incumbents_seed_from_legacy_global(tmp_path):
    from fool.memory_store import FoolMemory
    mem = FoolMemory(memory_dir=tmp_path / "mem")
    legacy = mem.best_solver_path
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("def solve(x): return []\n", encoding="utf-8")
    mem.best_meta_path.write_text(
        '{"score": 840.74, "bucket_scores": {"scarce_couriers_seed401": 950.0, "large_seed301": 760.0}}',
        encoding="utf-8",
    )
    assert mem.bucket_incumbents.scores() == {
        "scarce_couriers_seed401": 950.0,
        "large_seed301": 760.0,
    }
