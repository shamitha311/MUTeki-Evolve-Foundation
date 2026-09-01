from pathlib import Path

from fool.harness.context import RoundOutcome, RoundState
from fool.harness.prompt import build_prefix, build_round_header
from fool.harness.tools import build_default_registry


def _state(tmp_path: Path, iteration: int = 1) -> RoundState:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return RoundState(
        iteration=iteration,
        best_score=None,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[],
        input_dir=input_dir,
        run_dir=run_dir,
    )


def test_system_prefix_lists_new_tools():
    sp = build_prefix(build_default_registry())
    for tool in ("memory_search", "memory_write", "read_tool_result"):
        assert tool in sp
    # read_dialog is intentionally not registered; payloads were >30KB and the
    # tool overlaps with read_version(kind='plan'|'report'|'harness_full').
    assert "read_dialog" not in sp


def test_system_prefix_contains_memory_protocol():
    sp = build_prefix(build_default_registry())
    assert "Memory Protocol" in sp
    after = sp.split("Memory Protocol", 1)[1]
    assert "target_buckets" in after
    assert "incumbent" in after
    assert "memory_write" in after
    assert "<<<TRUNCATED>>>" in after


def test_round_header_includes_memory_index_head_when_present(tmp_path):
    index = tmp_path / "MEMORY.md"
    index.write_text("# MTASA Memory Index\n## Active Preferences\n- stdlib only\n",
                     encoding="utf-8")
    header = build_round_header(_state(tmp_path, iteration=2), memory_index_path=index)
    assert "Memory Index Head" in header
    assert "stdlib only" in header


def test_round_header_skips_memory_block_when_index_missing(tmp_path):
    header = build_round_header(
        _state(tmp_path, iteration=2),
        memory_index_path=tmp_path / "missing.md",
    )
    assert "Memory Index Head" not in header
