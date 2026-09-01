from __future__ import annotations

import json as _json
from pathlib import Path

import pytest

from fool.harness.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    build_default_registry,
)
from fool.memory_store import FoolMemory


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        input_dir=tmp_path / "in",
        run_dir=tmp_path / "run",
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="profile",
    )


def test_unknown_tool_returns_error(tmp_path: Path) -> None:
    registry = ToolRegistry()
    result = registry.run("does_not_exist", _ctx(tmp_path), {})
    assert result.ok is False
    assert "unknown tool" in result.content


def test_repeated_identical_call_is_rejected(tmp_path: Path) -> None:
    calls: list[dict] = []

    def echo(ctx: ToolContext, args: dict) -> ToolResult:
        calls.append(args)
        return ToolResult(ok=True, content="ran")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="echo", description="", risky=False, schema={}, run=echo)
    )
    ctx = _ctx(tmp_path)

    first = registry.run("echo", ctx, {"x": 1})
    second = registry.run("echo", ctx, {"x": 1})
    assert first.ok is True
    assert second.ok is False
    assert "repeated" in second.content
    assert len(calls) == 1


def test_repeated_call_allowed_after_failure(tmp_path: Path) -> None:
    # Regression for run_20260605_114305 R3: model's read_version call hit a
    # wire-format issue that dropped `kind`; retries with the same args were
    # blocked by dedup, eating step budget. Dedup should only kick in after
    # a *successful* call, so the model can retry a failed call.
    attempt = {"n": 0}

    def flaky(ctx: ToolContext, args: dict) -> ToolResult:
        attempt["n"] += 1
        # First call fails; second call succeeds (simulating the model
        # repeating after correcting some out-of-band wire issue).
        if attempt["n"] == 1:
            return ToolResult(ok=False, content="error: 'kind' is required")
        return ToolResult(ok=True, content="ran")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="probe", description="", risky=False, schema={}, run=flaky)
    )
    ctx = _ctx(tmp_path)

    first = registry.run("probe", ctx, {"v": "latest"})
    second = registry.run("probe", ctx, {"v": "latest"})
    third = registry.run("probe", ctx, {"v": "latest"})
    assert first.ok is False
    assert second.ok is True  # not blocked by dedup
    assert third.ok is False and "repeated" in third.content  # now blocked


def test_dup_hit_error_echoes_last_result(tmp_path: Path) -> None:
    def stub(ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(ok=True, content="hit count = 7; covered = 0.42")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="probe", description="", risky=False, schema={}, run=stub)
    )
    ctx = _ctx(tmp_path)

    first = registry.run("probe", ctx, {"q": "x"})
    second = registry.run("probe", ctx, {"q": "x"})
    assert first.ok is True
    assert second.ok is False
    # Actionable signal: prior result is echoed so the model can read it
    # instead of re-emitting another variant that resolves to the same args.
    assert "hit count = 7" in second.content
    assert "covered = 0.42" in second.content


def test_dup_check_uses_canonical_arg_ordering(tmp_path: Path) -> None:
    def stub(ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(ok=True, content="ok")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="probe", description="", risky=False, schema={}, run=stub)
    )
    ctx = _ctx(tmp_path)

    # Same args, different dict insertion order — must still hit dup guard.
    registry.run("probe", ctx, {"a": 1, "b": 2})
    second = registry.run("probe", ctx, {"b": 2, "a": 1})
    assert second.ok is False
    assert "repeated" in second.content


def test_different_call_between_same_calls_breaks_dup_guard(tmp_path: Path) -> None:
    def stub(ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(ok=True, content="ok")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="a", description="", risky=False, schema={}, run=stub)
    )
    registry.register(
        ToolSpec(name="b", description="", risky=False, schema={}, run=stub)
    )
    ctx = _ctx(tmp_path)

    r1 = registry.run("a", ctx, {"x": 1})
    r2 = registry.run("b", ctx, {})
    r3 = registry.run("a", ctx, {"x": 1})
    # Only the LAST signature matters for dup detection.
    assert r1.ok and r2.ok and r3.ok


def test_output_is_clipped_only_when_spec_caps(tmp_path: Path) -> None:
    def long(ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(ok=True, content="A" * 10_000)

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="long",
            description="",
            risky=False,
            schema={},
            run=long,
            max_output=200,
        )
    )
    registry.register(
        ToolSpec(name="long_uncapped", description="", risky=False, schema={}, run=long)
    )

    capped = registry.run("long", _ctx(tmp_path), {})
    assert len(capped.content) <= 260
    assert "truncated" in capped.content

    uncapped = registry.run("long_uncapped", _ctx(tmp_path), {})
    assert len(uncapped.content) == 10_000
    assert "truncated" not in uncapped.content


def test_specs_returns_registered_tool_metadata(tmp_path: Path) -> None:
    def noop(ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(ok=True, content="")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="noop",
            description="does nothing",
            risky=False,
            schema={"x": "int=0"},
            run=noop,
        )
    )
    specs = registry.specs()
    assert specs == [
        {
            "name": "noop",
            "description": "does nothing",
            "risky": False,
            "schema": {"x": "int=0"},
        }
    ]


# --- read-only tool tests ---


def _seed_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _seed_input_dir(tmp_path: Path) -> Path:
    input_dir = tmp_path / "in"
    input_dir.mkdir(parents=True, exist_ok=True)
    return input_dir


def test_profile_dataset_default_returns_structural_slice(tmp_path: Path) -> None:
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="profile body",
    )
    registry = build_default_registry()
    result = registry.run("profile_dataset", ctx, {})
    assert result.ok is True
    # New default: structural section sliced across all 10 buckets.
    assert "structural" in result.content
    assert "courier_ratio" in result.content
    assert "tiny_seed42" in result.content


def test_profile_dataset_field_slice(tmp_path: Path) -> None:
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("profile_dataset", ctx, {"field": "courier_ratio"})
    assert result.ok is True
    assert "structural.courier_ratio" in result.content
    assert "large_seed301" in result.content


def test_profile_dataset_rejects_legacy_section(tmp_path: Path) -> None:
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run(
        "profile_dataset", ctx, {"bucket": "large_seed301", "section": "probes"}
    )
    assert result.ok is False
    assert "no longer accepts 'section'" in result.content


def test_list_strategy_templates_returns_summary(tmp_path: Path) -> None:
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("list_strategy_templates", ctx, {})
    assert result.ok is True
    assert len(result.content) > 0


def test_select_strategy_templates_is_unregistered(tmp_path: Path) -> None:
    registry = build_default_registry()
    names = {spec["name"] for spec in registry.specs()}
    assert "select_strategy_templates" not in names
    assert "list_strategy_templates" in names
    assert "read_strategy_template" in names


def test_retrieve_memory_tool_is_unregistered() -> None:
    registry = build_default_registry()
    names = {spec["name"] for spec in registry.specs()}
    assert "retrieve_memory" not in names


def test_retrieve_guidance_tool_is_unregistered() -> None:
    # retrieve_guidance is intentionally disabled; the BM25 corpus was
    # producing low-signal results. The underlying helper functions stay in
    # fool/memory_store.py for other callers, but the model cannot invoke it.
    registry = build_default_registry()
    names = {spec["name"] for spec in registry.specs()}
    assert "retrieve_guidance" not in names


def _make_ctx_with_draft(tmp_path: Path, code: str) -> "tuple[ToolRegistry, ToolContext, Path]":
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    # draft_solver is no longer in the live registry (block_patch is the only
    # editor); write the seed draft directly so the rest of the harness can
    # patch / snapshot / smoke-test against it.
    draft_path = run_dir / "draft.py"
    draft_path.write_text(code, encoding="utf-8")
    return registry, ctx, draft_path


def _block(search: str, replace: str) -> str:
    return (
        "<<<<<<< SEARCH\n"
        f"{search}\n"
        "=======\n"
        f"{replace}\n"
        ">>>>>>> REPLACE\n"
    )


def test_apply_patch_is_unregistered(tmp_path: Path) -> None:
    """apply_patch tool was retired in favor of block_patch."""
    registry, ctx, _ = _make_ctx_with_draft(tmp_path, "x = 1\n")
    result = registry.run("apply_patch", ctx, {"patch": "anything"})
    assert result.ok is False
    assert "unknown tool" in result.content


def test_block_patch_applies_single_block(tmp_path: Path) -> None:
    code = "a = 1\nb = 2\nc = 3\n"
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, code)
    envelope = _block("b = 2", "b = 20")
    result = registry.run("block_patch", ctx, {"blocks": envelope})
    assert result.ok is True, result.content
    assert draft.read_text() == "a = 1\nb = 20\nc = 3\n"
    assert "applied block_patch to draft.py" in result.content
    assert "+1/-1 lines" in result.content
    assert "first change at line 2" in result.content
    assert "post-patch preview" in result.content
    assert "b = 20" in result.content  # the new value appears in preview
    assert "L   1: a = 1" in result.content  # ±2 context lines included


def test_block_patch_applies_multiple_blocks_atomically(tmp_path: Path) -> None:
    code = "x = 1\ny = 2\nz = 3\nw = 4\n"
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, code)
    envelope = _block("y = 2", "y = 20") + _block("w = 4", "w = 40")
    result = registry.run("block_patch", ctx, {"blocks": envelope})
    assert result.ok is True, result.content
    assert draft.read_text() == "x = 1\ny = 20\nz = 3\nw = 40\n"


def test_block_patch_rolls_back_on_failed_block(tmp_path: Path) -> None:
    code = "a = 1\nb = 2\nc = 3\n"
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, code)
    envelope = _block("b = 2", "b = 20") + _block("not_present_anywhere", "q = 90")
    result = registry.run("block_patch", ctx, {"blocks": envelope})
    assert result.ok is False
    assert "did not match" in result.content
    assert draft.read_text() == code  # rolled back


def test_block_patch_picks_first_match_when_ambiguous(tmp_path: Path) -> None:
    # SEARCH appears twice; matcher takes the first occurrence. The model
    # disambiguates by widening SEARCH with surrounding lines.
    code = "x = 1\nx = 1\n"
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, code)
    result = registry.run("block_patch", ctx, {"blocks": _block("x = 1", "x = 2")})
    assert result.ok is True, result.content
    assert draft.read_text() == "x = 2\nx = 1\n"


def test_block_patch_widened_search_disambiguates(tmp_path: Path) -> None:
    code = "def a():\n    return 1\n\ndef b():\n    return 1\n"
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, code)
    envelope = _block("def b():\n    return 1", "def b():\n    return 2")
    result = registry.run("block_patch", ctx, {"blocks": envelope})
    assert result.ok is True, result.content
    assert draft.read_text() == "def a():\n    return 1\n\ndef b():\n    return 2\n"


def test_block_patch_tolerates_uniform_indent_shift(tmp_path: Path) -> None:
    # Draft block is indented 8 spaces; SEARCH/REPLACE come in at 4 spaces.
    # whitespace-flex matching aligns by stripping a uniform 4-space lead,
    # then re-adds 4 spaces to REPLACE on write.
    code = "def foo():\n    while x:\n        improved = True\n        do_stuff()\n"
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, code)
    envelope = _block("    improved = True\n    do_stuff()", "    improved = True\n    do_better()")
    result = registry.run("block_patch", ctx, {"blocks": envelope})
    assert result.ok is True, result.content
    assert "        do_better()" in draft.read_text()
    assert "fuzz=" in result.content


def test_block_patch_empty_search_appends_to_file(tmp_path: Path) -> None:
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "a = 1\n")
    envelope = (
        "<<<<<<< SEARCH\n"
        "=======\n"
        "appended = 99\n"
        ">>>>>>> REPLACE\n"
    )
    result = registry.run("block_patch", ctx, {"blocks": envelope})
    assert result.ok is True, result.content
    assert draft.read_text() == "a = 1\nappended = 99\n"


def test_block_patch_rejects_identity_replacement(tmp_path: Path) -> None:
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "a = 1\n")
    result = registry.run("block_patch", ctx, {"blocks": _block("a = 1", "a = 1")})
    assert result.ok is False
    assert "identical" in result.content
    assert draft.read_text() == "a = 1\n"


def test_block_patch_without_draft_errors(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("block_patch", ctx, {"blocks": _block("a", "b")})
    assert result.ok is False
    assert "no draft" in result.content.lower()


def test_block_patch_rejects_malformed_envelope(tmp_path: Path) -> None:
    registry, ctx, _ = _make_ctx_with_draft(tmp_path, "a = 1\n")
    result = registry.run("block_patch", ctx, {"blocks": "no markers here"})
    assert result.ok is False
    assert "SEARCH" in result.content


def test_snapshot_draft_is_not_registered(tmp_path: Path) -> None:
    """Model can no longer snapshot directly — block_patch handles it."""
    registry, ctx, _ = _make_ctx_with_draft(tmp_path, "x = 1\n")
    result = registry.run("snapshot_draft", ctx, {"label": "baseline"})
    assert result.ok is False
    assert "unknown tool" in result.content


def test_block_patch_auto_snapshots_pre_state(tmp_path: Path) -> None:
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\n")
    result = registry.run("block_patch", ctx, {"blocks": _block("x = 1", "x = 2")})
    assert result.ok is True, result.content
    assert "snapshot='v001'" in result.content
    assert draft.read_text() == "x = 2\n"

    restored = registry.run("restore_draft", ctx, {"label": "v001"})
    assert restored.ok is True
    assert draft.read_text() == "x = 1\n"


def test_block_patch_assigns_sequential_v_labels(tmp_path: Path) -> None:
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "a = 1\n")
    r1 = registry.run("block_patch", ctx, {"blocks": _block("a = 1", "a = 2")})
    r2 = registry.run("block_patch", ctx, {"blocks": _block("a = 2", "a = 3")})
    r3 = registry.run("block_patch", ctx, {"blocks": _block("a = 3", "a = 4")})
    assert "v001" in r1.content
    assert "v002" in r2.content
    assert "v003" in r3.content
    assert draft.read_text() == "a = 4\n"

    registry.run("restore_draft", ctx, {"label": "v002"})
    assert draft.read_text() == "a = 2\n"


def test_restore_draft_returns_fingerprint_and_incumbent_match(tmp_path: Path) -> None:
    from fool.harness.tools import _draft_read_clear_cache

    _draft_read_clear_cache()
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\n")
    # Make ctx.best_solver_path point to a real file matching the snapshot.
    best_path = tmp_path / "best.py"
    best_path.write_text("x = 1\n", encoding="utf-8")
    ctx_with_best = ToolContext(
        input_dir=ctx.input_dir,
        run_dir=ctx.run_dir,
        best_solver_path=best_path,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry.run("block_patch", ctx_with_best, {"blocks": _block("x = 1", "x = 2")})
    restored = registry.run("restore_draft", ctx_with_best, {"label": "v001"})
    assert restored.ok is True
    assert "sha=" in restored.content
    assert "bytes" in restored.content
    assert "identical_to_incumbent: yes" in restored.content


def test_restore_draft_reports_incumbent_mismatch(tmp_path: Path) -> None:
    from fool.harness.tools import _draft_read_clear_cache

    _draft_read_clear_cache()
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\n")
    best_path = tmp_path / "best.py"
    best_path.write_text("totally_different_incumbent = True\n", encoding="utf-8")
    ctx_with_best = ToolContext(
        input_dir=ctx.input_dir,
        run_dir=ctx.run_dir,
        best_solver_path=best_path,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry.run("block_patch", ctx_with_best, {"blocks": _block("x = 1", "x = 2")})
    restored = registry.run("restore_draft", ctx_with_best, {"label": "v001"})
    assert restored.ok is True
    assert "identical_to_incumbent: no" in restored.content
    assert "incumbent sha=" in restored.content


def test_read_current_draft_stubs_when_unchanged(tmp_path: Path) -> None:
    from fool.harness.tools import _draft_read_clear_cache

    _draft_read_clear_cache()
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\ny = 2\n")
    # Set a run_id so the cache key isn't empty.
    ctx = ToolContext(
        input_dir=ctx.input_dir,
        run_dir=ctx.run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
        run_id="test_run_A",
        iteration=3,
    )
    r1 = registry.run("read_current_draft", ctx, {})
    assert r1.ok and "L   1" in r1.content
    r2 = registry.run("read_current_draft", ctx, {"force_unique": "1"})
    # The duplicate-call gate would also fire; bypass by changing one arg.
    # The interesting assertion is that with the same args+state we'd get the stub.
    # Use a fresh ctx args dict to verify stub directly:
    r3 = registry.run("read_current_draft", ctx, {"note": "x"})
    assert r3.ok
    assert "unchanged since round 3" in r3.content
    assert "sha=" in r3.content


def test_read_current_draft_force_rereads(tmp_path: Path) -> None:
    from fool.harness.tools import _draft_read_clear_cache

    _draft_read_clear_cache()
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\n")
    ctx = ToolContext(
        input_dir=ctx.input_dir,
        run_dir=ctx.run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
        run_id="test_run_B",
        iteration=1,
    )
    registry.run("read_current_draft", ctx, {})
    forced = registry.run("read_current_draft", ctx, {"force": True})
    assert forced.ok
    assert "L   1" in forced.content
    assert "unchanged" not in forced.content


def test_read_current_draft_resends_after_edit(tmp_path: Path) -> None:
    from fool.harness.tools import _draft_read_clear_cache

    _draft_read_clear_cache()
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\n")
    ctx = ToolContext(
        input_dir=ctx.input_dir,
        run_dir=ctx.run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
        run_id="test_run_C",
        iteration=1,
    )
    registry.run("read_current_draft", ctx, {})
    registry.run("block_patch", ctx, {"blocks": _block("x = 1", "x = 999")})
    again = registry.run("read_current_draft", ctx, {"after_edit": "1"})
    # post-edit sha differs from cached → returns full body, not stub.
    assert "L   1" in again.content
    assert "999" in again.content


def test_restore_unknown_label_lists_available(tmp_path: Path) -> None:
    registry, ctx, _ = _make_ctx_with_draft(tmp_path, "a = 1\n")
    registry.run("block_patch", ctx, {"blocks": _block("a = 1", "a = 2")})
    result = registry.run("restore_draft", ctx, {"label": "missing"})
    assert result.ok is False
    assert "missing" in result.content
    assert "v001" in result.content


# ---- snapshot / list_versions view-consistency fixes ---------------------


class _StubVersionIndex:
    """Minimal VersionIndex stand-in for restore_draft fallback tests."""

    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries

    def resolve(self, spec, *, current_run_id: str = ""):
        if isinstance(spec, str):
            digits = spec.strip().lower().lstrip("v")
            if digits.isdigit():
                target = int(digits)
                for e in self._entries:
                    if int(e.get("v", -1)) == target:
                        return e
        return None

    def all_entries(self) -> list[dict]:
        return list(self._entries)

    def for_run(self, run_id: str) -> list[dict]:
        return [e for e in self._entries if e.get("run_id") == run_id]


def _ctx_with_index(ctx, index, run_id: str = "run_A"):
    return ToolContext(
        input_dir=ctx.input_dir,
        run_dir=ctx.run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
        run_id=run_id,
        version_index=index,
    )


def test_restore_draft_falls_back_to_version_index_when_no_local_snapshot(
    tmp_path: Path,
) -> None:
    # Simulate "v033 was shown by list_versions but produced in a prior run
    # so no local _snapshots/v033.py exists" — the fallback should restore
    # from the indexed submitted solver instead of failing.
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "current = 1\n")
    indexed_solver = tmp_path / "prior_run" / "solver_v033.py"
    indexed_solver.parent.mkdir(parents=True, exist_ok=True)
    indexed_solver.write_text("indexed_v33_body = True\n", encoding="utf-8")
    index = _StubVersionIndex(
        [{"v": 33, "run_id": "old_run", "solver_path": str(indexed_solver)}]
    )
    ctx_idx = _ctx_with_index(ctx, index)

    result = registry.run("restore_draft", ctx_idx, {"label": "v033"})
    assert result.ok is True, result.content
    assert draft.read_text() == "indexed_v33_body = True\n"
    assert "submitted solver of" in result.content
    assert "v033" in result.content


def test_restore_draft_prefers_index_over_local_snapshot(tmp_path: Path) -> None:
    # New semantics (2026-06-05): vN aligns with read_version — when both
    # the version_index entry and a local pre-patch snapshot exist, the
    # *submitted solver of round N* wins. Pre-patch snapshot is only used
    # when N is the current in-progress round (no index entry yet).
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\n")
    indexed_solver = tmp_path / "other.py"
    indexed_solver.write_text("from_index = True\n", encoding="utf-8")
    index = _StubVersionIndex(
        [{"v": 1, "run_id": "r", "solver_path": str(indexed_solver)}]
    )
    ctx_idx = _ctx_with_index(ctx, index)

    registry.run("block_patch", ctx_idx, {"blocks": _block("x = 1", "x = 2")})
    result = registry.run("restore_draft", ctx_idx, {"label": "v001"})
    assert result.ok is True
    assert draft.read_text() == "from_index = True\n"
    assert "submitted solver of" in result.content


def test_restore_draft_refuses_cross_run_when_iter_collides(tmp_path: Path) -> None:
    # The bug we're guarding against (observed in run_20260605_114305 R2):
    # model writes restore_draft(v=1) thinking "round 1 of THIS run", but
    # global v=1 is a stale bootstrap from a prior run. We must refuse and
    # tell the model the current-run vN that matches iter 1.
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\n")
    stale_solver = tmp_path / "stale.py"
    stale_solver.write_text("stale_bootstrap = True\n", encoding="utf-8")
    cur_solver = tmp_path / "cur.py"
    cur_solver.write_text("current_iter1 = True\n", encoding="utf-8")
    index = _StubVersionIndex(
        [
            {"v": 1, "run_id": "old_run", "iteration": 1, "solver_path": str(stale_solver)},
            {"v": 12, "run_id": "run_A", "iteration": 1, "solver_path": str(cur_solver)},
        ]
    )
    ctx_idx = _ctx_with_index(ctx, index)
    result = registry.run("restore_draft", ctx_idx, {"label": "v001"})
    assert result.ok is False
    assert "prior run" in result.content
    assert "v012" in result.content  # surfaces the correct current-run v
    # Draft must be unchanged (no silent stale overwrite).
    assert draft.read_text() == "x = 1\n"


def test_restore_draft_allows_cross_run_when_no_iter_collision(tmp_path: Path) -> None:
    # Cross-run restore is legit when the requested vN does NOT collide with
    # a current-run iter (e.g. v33 in a 3-iter run). Must still succeed.
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\n")
    prior_solver = tmp_path / "prior.py"
    prior_solver.write_text("prior_run_v33 = True\n", encoding="utf-8")
    cur_solver = tmp_path / "cur.py"
    cur_solver.write_text("# unused\n", encoding="utf-8")
    index = _StubVersionIndex(
        [
            {"v": 33, "run_id": "old_run", "iteration": 5, "solver_path": str(prior_solver)},
            {"v": 50, "run_id": "run_A", "iteration": 1, "solver_path": str(cur_solver)},
        ]
    )
    ctx_idx = _ctx_with_index(ctx, index)
    result = registry.run("restore_draft", ctx_idx, {"label": "v033"})
    assert result.ok is True
    assert draft.read_text() == "prior_run_v33 = True\n"


def test_list_versions_surfaces_iter_to_v_mapping_for_current_run(tmp_path: Path) -> None:
    registry, ctx, _ = _make_ctx_with_draft(tmp_path, "x = 1\n")
    index = _StubVersionIndex(
        [
            {"v": 12, "run_id": "run_A", "iteration": 1, "score": 800.0, "solver_path": "x"},
            {"v": 13, "run_id": "run_A", "iteration": 2, "score": 790.0, "solver_path": "x"},
        ]
    )
    ctx_idx = _ctx_with_index(ctx, index)
    result = registry.run("list_versions", ctx_idx, {"scope": "current_run"})
    assert result.ok is True
    assert "iter↔v map" in result.content
    assert "it01=v012" in result.content
    assert "it02=v013" in result.content


def test_restore_draft_falls_back_to_local_when_index_misses_current_round(
    tmp_path: Path,
) -> None:
    # In-progress round N: vN not yet in the index. restore_draft(vN) should
    # restore the pre-patch local snapshot = start-of-round-N state.
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "x = 1\n")
    index = _StubVersionIndex([])  # empty index → current round not registered yet
    ctx_idx = _ctx_with_index(ctx, index)

    registry.run("block_patch", ctx_idx, {"blocks": _block("x = 1", "x = 2")})
    result = registry.run("restore_draft", ctx_idx, {"label": "v001"})
    assert result.ok is True
    assert draft.read_text() == "x = 1\n"
    assert "pre-patch snapshot" in result.content


def test_restore_unknown_label_error_lists_both_sources(tmp_path: Path) -> None:
    registry, ctx, _ = _make_ctx_with_draft(tmp_path, "a = 1\n")
    indexed_solver = tmp_path / "submitted.py"
    indexed_solver.write_text("# v=42\n", encoding="utf-8")
    index = _StubVersionIndex(
        [{"v": 42, "run_id": "r", "solver_path": str(indexed_solver)}]
    )
    ctx_idx = _ctx_with_index(ctx, index)
    registry.run("block_patch", ctx_idx, {"blocks": _block("a = 1", "a = 2")})

    result = registry.run("restore_draft", ctx_idx, {"label": "v999"})
    assert result.ok is False
    # Error must surface both surfaces the model might be confused about.
    assert "local pre-patch snapshots" in result.content
    assert "recent indexed versions" in result.content
    assert "v042" in result.content
    assert "list_versions" in result.content


def test_block_patch_same_round_preserves_round_initial_snapshot(
    tmp_path: Path,
) -> None:
    # Multi-patch in the SAME round (same global_v) must not overwrite the
    # snapshot — otherwise restore_draft(label=vN) lands mid-round instead
    # of at the round's true initial state. Simulate same-round by giving
    # ctx a fixed global_v.
    registry, ctx, draft = _make_ctx_with_draft(tmp_path, "n = 0\n")
    ctx_same_round = ToolContext(
        input_dir=ctx.input_dir,
        run_dir=ctx.run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
        global_v=7,
    )
    r1 = registry.run("block_patch", ctx_same_round, {"blocks": _block("n = 0", "n = 1")})
    r2 = registry.run("block_patch", ctx_same_round, {"blocks": _block("n = 1", "n = 2")})
    assert "v007" in r1.content and "v007" in r2.content  # same label
    assert draft.read_text() == "n = 2\n"

    restored = registry.run("restore_draft", ctx_same_round, {"label": "v007"})
    assert restored.ok is True
    # MUST restore to round-initial state, not the intermediate "n = 1".
    assert draft.read_text() == "n = 0\n"


def test_smoke_test_solver_passes_for_valid_solver(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "def solve(input_text):\n    return [('t1','c1')]\n", encoding="utf-8"
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is True, result.content
    assert "PASS" in result.content


def test_smoke_test_solver_accepts_platform_example_courier_list(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "def solve(input_text: str) -> list:\n"
        "    return [('t1', ['c1'])]\n",
        encoding="utf-8",
    )

    result = registry.run("smoke_test_solver", ctx, {})

    assert result.ok is True, result.content
    assert "PASS" in result.content


def test_smoke_test_solver_rejects_future_import(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "from __future__ import annotations\n"
        "\n"
        "def solve(input_text):\n"
        "    return [('t1','c1')]\n",
        encoding="utf-8",
    )

    result = registry.run("smoke_test_solver", ctx, {})

    assert result.ok is False
    assert "unsupported top-level import" in result.content


def test_smoke_test_solver_allows_heapq_import(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "import heapq\n"
        "\n"
        "def solve(input_text):\n"
        "    return [('t1','c1')]\n",
        encoding="utf-8",
    )

    result = registry.run("smoke_test_solver", ctx, {})

    assert result.ok is True, result.content
    assert "PASS" in result.content


def test_smoke_test_solver_rejects_input_rows_without_exactly_four_columns(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\textra\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "def solve(input_text):\n"
        "    return [('t1','c1')]\n",
        encoding="utf-8",
    )

    result = registry.run("smoke_test_solver", ctx, {})

    assert result.ok is False
    assert "exactly 4 TAB columns" in result.content


def test_smoke_test_solver_fails_when_return_shape_wrong(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "def solve(input_text):\n    return 'oops'\n", encoding="utf-8"
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is False
    assert "list" in result.content.lower()


def test_smoke_test_solver_fails_on_unknown_task_id(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "def solve(input_text):\n    return [('t_unknown','c1')]\n", encoding="utf-8"
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is False
    assert "unknown task_id" in result.content or "not in input" in result.content


def test_smoke_test_solver_fails_on_duplicate_task_id(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
        "t2\tc2\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "def solve(input_text):\n    return [('t1','c1'),('t1','c2')]\n", encoding="utf-8"
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is False
    assert "duplicate" in result.content.lower()


def test_smoke_test_solver_rejects_solo_task_from_bundle_only_row(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1,t2\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "def solve(input_text):\n    return [('t2','c1')]\n", encoding="utf-8"
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is False
    assert "unknown task_id_list" in result.content or "not in input" in result.content


def test_smoke_test_solver_accepts_solo_task_when_solo_row_exists(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1,t2\tc1\t1.0\t1.0\n"
        "t2\tc2\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "def solve(input_text):\n    return [('t2','c2')]\n", encoding="utf-8"
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is True, result.content


def test_read_teacher_playbook_is_not_registered() -> None:
    registry = build_default_registry()
    names = {spec["name"] for spec in registry.specs()}
    assert "read_teacher_playbook" not in names


# --- read_version tool tests ---


def test_read_version_returns_solver_by_version(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "solver_v002.py").write_text("# solver v2\ndef solve(t): return []\n")
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 2, "kind": "solver"})
    assert result.ok is True
    assert "# solver v2" in result.content


def test_read_version_returns_report_by_version(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "report_v003.txt").write_text("Average Penalty Score\n12.34\n")
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 3, "kind": "report"})
    assert result.ok is True
    assert "12.34" in result.content


def test_read_version_returns_plan_by_version(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    payload = {
        "final": {
            "plan": {
                "hypothesis": "try greedy by willingness",
                "analysis": "low_w bucket is the bottleneck",
                "target_buckets": ["low_w_seed501"],
                "edit_plan": ["sort by willingness desc"],
            }
        }
    }
    (run_dir / "harness_v001.json").write_text(_json.dumps(payload))
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 1, "kind": "plan"})
    assert result.ok is True
    assert "try greedy by willingness" in result.content
    assert "low_w_seed501" in result.content


def test_read_version_not_found_returns_fail(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 9, "kind": "solver"})
    assert result.ok is False
    assert "v009" in result.content
    assert "not found" in result.content


def test_read_version_rejects_invalid_kind(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 1, "kind": "garbage"})
    assert result.ok is False
    assert "kind" in result.content


def test_read_version_rejects_non_positive_v(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 0, "kind": "solver"})
    assert result.ok is False
    assert "v" in result.content


def test_read_version_plan_missing_returns_notice(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "harness_v002.json").write_text(_json.dumps({"final": {}}))
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_version", ctx, {"v": 2, "kind": "plan"})
    assert result.ok is True
    assert "no plan" in result.content.lower()


def test_smoke_test_solver_fails_on_unknown_courier_id(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    (run_dir / "draft.py").write_text(
        "def solve(input_text):\n    return [('t1','c_unknown')]\n", encoding="utf-8"
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is False
    assert "unknown courier_id" in result.content or "not in input" in result.content


def test_score_locally_is_not_registered() -> None:
    """score_locally has been folded into smoke_test_solver."""
    registry = build_default_registry()
    names = {spec["name"] for spec in registry.specs()}
    assert "score_locally" not in names


def test_score_locally_helper_fails_when_dataset_missing(tmp_path: Path, monkeypatch) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    (run_dir / "draft.py").write_text(
        "def solve(t):\n    return []\n", encoding="utf-8"
    )
    import fool.harness.tools as _tools
    from fool.harness.context import FatalToolError

    monkeypatch.setattr(_tools, "_LARGE_SEED301", tmp_path / "missing.txt")
    with pytest.raises(FatalToolError) as excinfo:
        _tools._t_score_locally(ctx, {})
    assert "large_seed301" in str(excinfo.value)


def test_score_locally_helper_fails_without_draft(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    import fool.harness.tools as _tools

    result = _tools._t_score_locally(ctx, {})
    assert result.ok is False
    assert "draft" in result.content.lower()


import shutil as _shutil


def test_score_locally_helper_runs_genius_and_returns_score(tmp_path: Path) -> None:
    if _shutil.which("python3.6") is None:
        pytest.skip("python3.6 not available on this host")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    (run_dir / "draft.py").write_text(
        "def solve(t):\n    return []\n", encoding="utf-8"
    )
    import fool.harness.tools as _tools

    result = _tools._t_score_locally(ctx, {})
    assert result.ok is True, result.content
    assert "large_seed301" in result.content
    assert "total_score" in result.content or "Average Penalty Score" in result.content
    assert (run_dir / "_local_preview.txt").exists()
