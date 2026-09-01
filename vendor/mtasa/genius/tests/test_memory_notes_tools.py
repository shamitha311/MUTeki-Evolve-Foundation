from pathlib import Path
import json
import pytest

from fool.harness.tools import build_default_registry, ToolContext
from fool.memory_notes import MemoryNotesStore


def _all_lessons_text(mem_root: Path) -> str:
    parts = []
    for p in (mem_root / "notes").glob("lesson_*.md"):
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _ctx(tmp_path: Path, *, memory_notes: MemoryNotesStore | None = None) -> ToolContext:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "tool_results").mkdir()
    (run_dir / "dialog").mkdir()
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    return ToolContext(
        input_dir=in_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
        memory_notes=memory_notes,
    )


def test_memory_write_tool_creates_lesson_note(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    ctx = _ctx(tmp_path, memory_notes=notes)
    reg = build_default_registry()
    res = reg.run("memory_write", ctx, {
        "title": "Test note",
        "body": "useful",
        "run_id": "r1",
        "iteration": 1,
    })
    assert res.ok
    body = _all_lessons_text(tmp_path / "mem")
    assert "Test note" in body


def test_memory_write_falls_back_to_ctx_run_id_and_iteration(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    ctx = _ctx(tmp_path, memory_notes=notes)
    ctx.run_id = "run_test_42"
    ctx.iteration = 7
    reg = build_default_registry()
    # Model omits run_id and iteration — should not error; harness fills in.
    res = reg.run("memory_write", ctx, {
        "title": "ctx fallback",
        "body": "test body",
    })
    assert res.ok, res.content
    written = _all_lessons_text(tmp_path / "mem")
    assert "run_test_42" in written
    assert "iteration: 7" in written


def test_memory_write_missing_body_returns_teaching_error(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    ctx = _ctx(tmp_path, memory_notes=notes)
    ctx.run_id = "r"
    reg = build_default_registry()
    res = reg.run("memory_write", ctx, {
        "title": "x",
    })
    assert not res.ok
    # Tells the model WHAT belongs in body, not str(KeyError('body')).
    assert "body" in res.content
    assert "future rounds" in res.content


def test_memory_write_explicit_run_id_overrides_ctx(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    ctx = _ctx(tmp_path, memory_notes=notes)
    ctx.run_id = "ctx_run"
    ctx.iteration = 1
    reg = build_default_registry()
    # Explicit args should still win — useful for cross-run consolidation.
    res = reg.run("memory_write", ctx, {
        "title": "x",
        "body": "y",
        "run_id": "explicit_run",
        "iteration": 9,
    })
    assert res.ok
    written = _all_lessons_text(tmp_path / "mem")
    assert "explicit_run" in written
    assert "iteration: 9" in written


def test_memory_search_tool_returns_snippets(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    notes.write_note(section="lesson", title="willingness wins",
                     body="seed401 +12%", run_id="r", iteration=1)
    ctx = _ctx(tmp_path, memory_notes=notes)
    reg = build_default_registry()
    res = reg.run("memory_search", ctx, {"query": "willingness"})
    assert res.ok
    payload = json.loads(res.content)
    assert payload["lessons"]
    assert "willingness" in payload["lessons"][0]["snippet"].lower()


def test_memory_search_empty_query_emits_hint(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    ctx = _ctx(tmp_path, memory_notes=notes)
    reg = build_default_registry()
    res = reg.run("memory_search", ctx, {"query": ""})
    assert res.ok
    payload = json.loads(res.content)
    assert payload["lessons"] == [] and payload["try_errors"] == []
    assert "empty query" in payload["hint"]


def test_memory_search_no_matches_emits_hint(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    notes.write_note(section="lesson", title="X", body="some content here",
                     run_id="r", iteration=1)
    ctx = _ctx(tmp_path, memory_notes=notes)
    reg = build_default_registry()
    res = reg.run("memory_search", ctx, {"query": "zzzzz_no_such_token"})
    assert res.ok
    payload = json.loads(res.content)
    assert payload["lessons"] == [] and payload["try_errors"] == []
    assert "no matches" in payload["hint"]


def test_read_tool_result_serves_spilled_file(tmp_path: Path):
    ctx = _ctx(tmp_path)
    uid = "deadbeef" * 4
    p = ctx.run_dir / "tool_results" / f"{uid}.txt"
    p.write_text("\n".join(f"L{i}" for i in range(1, 101)), encoding="utf-8")
    reg = build_default_registry()
    res = reg.run("read_tool_result", ctx, {"uuid": uid, "start_line": 50, "max_lines": 5})
    assert res.ok
    assert "L50" in res.content and "L54" in res.content
    assert "L60" not in res.content


def test_read_tool_result_rejects_invalid_uuid(tmp_path: Path):
    ctx = _ctx(tmp_path)
    reg = build_default_registry()
    res = reg.run("read_tool_result", ctx, {"uuid": "../../etc/passwd"})
    assert not res.ok


def test_read_dialog_is_not_registered():
    # read_dialog is intentionally disabled — payloads were >30KB and the
    # tool overlaps with read_version(kind='plan'|'report'|'harness_full').
    reg = build_default_registry()
    assert reg.get_spec("read_dialog") is None


def test_memory_tools_no_op_when_store_missing(tmp_path: Path):
    ctx = _ctx(tmp_path, memory_notes=None)
    reg = build_default_registry()
    res = reg.run("memory_write", ctx, {"title": "x", "body": "y",
                                          "run_id": "r", "iteration": 1})
    assert not res.ok
    assert "memory_notes not configured" in res.content
