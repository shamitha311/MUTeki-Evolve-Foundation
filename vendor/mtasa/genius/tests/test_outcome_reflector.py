from __future__ import annotations

import json
from pathlib import Path

import pytest

from fool.harness.model_client import FakeModelClient
from fool.harness.outcome_reflector import reflect_and_write
from fool.memory_notes import MemoryNotesStore


def _store(tmp_path: Path) -> MemoryNotesStore:
    return MemoryNotesStore(root=tmp_path / "mem")


def _read_section(mem_root: Path, section: str) -> str:
    """Concatenate text of all per-note files for a section."""
    parts = []
    for p in (mem_root / "notes").glob(f"{section}_*.md"):
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _section_files(mem_root: Path, section: str) -> list[Path]:
    return list((mem_root / "notes").glob(f"{section}_*.md"))


def _plan() -> dict:
    return {
        "hypothesis": "non-low_w priority improved to score*w^3",
        "analysis": "w^3 sharpens high-willingness preference",
        "target_buckets": ["medium", "large"],
        "edit_plan": ["change priority formula", "keep low_w branch unchanged"],
    }


def _report(tmp_path: Path, content: str = "average_score=1156.32 valid_cases=10/10\n- tiny: score=123\n") -> Path:
    p = tmp_path / "report.txt"
    p.write_text(content, encoding="utf-8")
    return p


def test_update_tag_appends_evidence_to_known_anchor(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    notes.write_note(section="lesson", title="willingness cubed boost",
                     body="w^3 helped medium/large", run_id="prior", iteration=1)
    # Find the prior entry's anchor as search would return it.
    hits = notes.search("willingness cubed")
    assert hits
    anchor = hits[0]
    path_str = anchor["path"]
    start_line = anchor["start_line"]

    update_response = (
        f'<update path="{path_str}" line={start_line}>'
        "scarce 桶也确认 -8（n=39/40），w³ 同样生效。"
        "</update>"
    )
    model = FakeModelClient([update_response])

    res = reflect_and_write(
        model=model, memory_notes=notes,
        plan={
            "hypothesis": "willingness cubed extended to scarce",
            "analysis": "w^3 also lifts scarce",
            "target_buckets": ["scarce"],
            "edit_plan": [],
        },
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r2", iteration=5, dataset_fp="fp",
    )

    assert res["ok"]
    assert res["action"] == "updated"
    assert res["path"] == path_str
    body = Path(path_str).read_text(encoding="utf-8")
    assert "scarce 桶也确认 -8" in body
    assert "confirmed-by run_id=r2 iteration=5" in body


def test_update_rejects_path_not_in_prior_notes(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    notes.write_note(section="lesson", title="seed lesson",
                     body="body", run_id="r", iteration=1)
    rogue = tmp_path / "mem" / "notes" / "try_errors.md"
    rogue.write_text("\n\n# evil\n<!-- meta -->\n\nbody\n", encoding="utf-8")
    # Model points at try_errors.md but the prior_notes search only finds the
    # lesson entry — try_errors line is NOT in the prompt.
    update_response = f'<update path="{rogue}" line=3>injected evidence</update>'
    model = FakeModelClient([update_response])

    res = reflect_and_write(
        model=model, memory_notes=notes,
        plan={
            "hypothesis": "seed lesson",
            "analysis": "matches the existing seeded entry",
            "target_buckets": [],
            "edit_plan": [],
        },
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=2, dataset_fp="fp",
    )
    assert not res["ok"]
    assert "not in Similar prior notes" in res["reason"]
    # try_errors.md was untouched (still no "injected evidence").
    assert "injected evidence" not in rogue.read_text(encoding="utf-8")


def test_update_rejects_wrong_line_number(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    notes.write_note(section="lesson", title="anchored",
                     body="body", run_id="r", iteration=1)
    hits = notes.search("anchored")
    path_str = hits[0]["path"]
    # Use a line that's not the search-returned start_line.
    bad_line = hits[0]["start_line"] + 99
    update_response = f'<update path="{path_str}" line={bad_line}>ev</update>'
    model = FakeModelClient([update_response])

    res = reflect_and_write(
        model=model, memory_notes=notes,
        plan={"hypothesis": "anchored", "analysis": "x",
              "target_buckets": [], "edit_plan": []},
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=2, dataset_fp="fp",
    )
    assert not res["ok"]
    assert "not in Similar prior notes" in res["reason"]


def test_skip_tag_tolerates_missing_closing_tag(tmp_path: Path) -> None:
    """Production saw <skip>...。 (no closing tag) due to mid-stream truncation.
    Opening tag + non-empty body should still register as a skip."""
    notes = _store(tmp_path)
    model = FakeModelClient([
        "<skip>本轮结果与prev_best完全一致，未产生新证据。"
    ])
    res = reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="neutral", score=1.0, prev_best=1.0, score_delta=0.0,
        report_path=_report(tmp_path), run_id="r", iteration=8, dataset_fp="fp",
    )
    assert res["ok"]
    assert res["action"] == "skipped"
    assert "完全一致" in res["reason"]


def test_default_max_tokens_passed_to_model(tmp_path: Path) -> None:
    """Regression: 700 was too tight; default must be >= 1500 to fit Chinese
    bodies without mid-args truncation."""
    from fool.harness.outcome_reflector import _DEFAULT_MAX_NEW_TOKENS
    assert _DEFAULT_MAX_NEW_TOKENS >= 1500

    notes = _store(tmp_path)
    model = FakeModelClient([
        '<tool name="memory_write"><args>{"section":"lesson","title":"t","body":"b"}</args></tool>'
    ])
    reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=1, dataset_fp="fp",
    )
    assert model.last_max_tokens == _DEFAULT_MAX_NEW_TOKENS


def test_skip_tag_is_valid_no_write_outcome(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    model = FakeModelClient(["<skip>已在 lessons.md:9-14 命中相同机制</skip>"])

    res = reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=1, dataset_fp="fp",
    )
    assert res["ok"]
    assert res["action"] == "skipped"
    assert "命中相同机制" in res["reason"]
    # No file written.
    assert not _section_files(tmp_path / "mem", "lesson")


def test_prev_best_report_head_appears_in_prompt(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    prev_report = tmp_path / "prev.txt"
    prev_report.write_text("average_score=1200.50 valid_cases=10/10\n- tiny: score=200\n")
    model = FakeModelClient([
        '<tool name="memory_write"><args>{"section":"lesson",'
        '"title":"bucket-level delta visible","body":"tiny -77 vs prev"}</args></tool>'
    ])
    reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="improved", score=1156.32, prev_best=1200.50, score_delta=-44.18,
        report_path=_report(tmp_path),
        prev_best_report_path=prev_report,
        run_id="r", iteration=2, dataset_fp="fp",
    )
    user_msg = model.last_messages[-1]["content"]
    assert "Prev best report head" in user_msg
    assert "average_score=1200.50" in user_msg


def test_prior_notes_block_includes_search_hits(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    # Pre-seed a lesson so search has something to hit.
    notes.write_note(
        section="lesson",
        title="non-low_w willingness cubed boost",
        body="score * willingness^3 helped medium/large by -20",
        run_id="prior", iteration=1,
    )
    model = FakeModelClient([
        "<skip>same mechanism already captured</skip>"
    ])
    res = reflect_and_write(
        model=model, memory_notes=notes,
        plan={
            "hypothesis": "non-low_w priority cubed willingness",
            "analysis": "w^3 promotes high-willingness couriers",
            "target_buckets": ["medium"],
            "edit_plan": [],
        },
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=3, dataset_fp="fp",
    )
    user_msg = model.last_messages[-1]["content"]
    assert "Similar prior notes" in user_msg
    # The prior lesson title should appear in the snippet.
    assert "willingness cubed boost" in user_msg or "willingness^3" in user_msg
    assert res["prior_notes_seen"] >= 1


def test_harness_failed_returns_ok_skipped(tmp_path: Path) -> None:
    """harness_failed is a graceful skip, not an error."""
    notes = _store(tmp_path)
    model = FakeModelClient([])
    res = reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="harness_failed", score=None, prev_best=None, score_delta=None,
        report_path=None, run_id="r", iteration=1, dataset_fp="fp",
    )
    assert res["ok"]
    assert res["action"] == "skipped"
    assert model.calls == []


def test_writes_lesson_on_improved(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    model = FakeModelClient([
        '<tool name="memory_write"><args>{"section":"lesson",'
        '"title":"在 non-low_w 主派排序键改 w^3 提升 medium/large",'
        '"body":"scope: non-low_w 主派排序\\nbucket_delta: medium -10; large -8\\nfalsifies: [推断] 线性 priority\\nmechanism: [推断] w^3 让高意愿主派排序更靠前\\nconfidence: medium\\n\\nmedium/large 全覆盖且 -10/-8 提升。"}'
        '</args></tool>'
    ])

    res = reflect_and_write(
        model=model,
        memory_notes=notes,
        plan=_plan(),
        outcome="improved",
        score=1156.32,
        prev_best=1170.24,
        score_delta=-13.92,
        report_path=_report(tmp_path),
        run_id="run_test_1",
        iteration=4,
        dataset_fp="abcd1234deadbeef",
    )

    assert res["ok"], res["reason"]
    assert res["section"] == "lesson"
    body = _read_section(tmp_path / "mem", "lesson")
    assert "w^3" in body
    assert "run_id: run_test_1" in body


def test_writes_try_error_on_regressed(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    model = FakeModelClient([
        '<tool name="memory_write"><args>{"section":"try_error",'
        '"title":"在 scarce 桶 backup 阶段上限+1 导致覆盖回退",'
        '"body":"scope: scarce 桶 backup 阶段\\nbucket_delta: scarce +10\\nfalsifies: [推断] backup 上限再 +1\\nmechanism: [推断] 备份消耗了主派可用骑手，覆盖反降\\nconfidence: low"}'
        '</args></tool>'
    ])

    res = reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="regressed", score=1180.0, prev_best=1170.0, score_delta=10.0,
        report_path=_report(tmp_path), run_id="r2", iteration=5, dataset_fp="fp",
    )

    assert res["ok"]
    assert res["section"] == "try_error"
    assert bool(_section_files(tmp_path / "mem", "try_error"))


def test_skips_when_memory_notes_none(tmp_path: Path) -> None:
    res = reflect_and_write(
        model=FakeModelClient([]), memory_notes=None, plan=_plan(),
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=None, run_id="r", iteration=1, dataset_fp="fp",
    )
    assert not res["ok"]
    assert "memory_notes" in res["reason"]


def test_handles_non_tool_response(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    model = FakeModelClient(["I think we should write a lesson but I will explain instead."])

    res = reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=1, dataset_fp="fp",
    )

    assert not res["ok"]
    assert "unexpected" in res["reason"]
    # No file written.
    assert not _section_files(tmp_path / "mem", "lesson")


def test_handles_wrong_tool_call(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    model = FakeModelClient(['<tool name="profile_dataset"><args>{}</args></tool>'])

    res = reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=1, dataset_fp="fp",
    )
    assert not res["ok"]
    assert "unexpected" in res["reason"]


def test_rejects_invalid_section(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    model = FakeModelClient([
        '<tool name="memory_write"><args>{"section":"observation",'
        '"title":"x","body":"y"}</args></tool>'
    ])
    res = reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=1, dataset_fp="fp",
    )
    assert not res["ok"]
    assert "args" in res["reason"]


def test_writes_log_file(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    log_path = tmp_path / "run" / "reflect_v001.json"
    model = FakeModelClient([
        '<tool name="memory_write"><args>{"section":"lesson",'
        '"title":"在 X 阶段 t","body":"scope: X\\nbucket_delta: a:-1\\nfalsifies: [推断] n/a\\nmechanism: [推断] b\\nconfidence: low"}</args></tool>'
    ])
    res = reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=1, dataset_fp="fp",
        log_path=log_path,
    )
    assert res["ok"]
    assert log_path.exists()
    data = json.loads(log_path.read_text(encoding="utf-8"))
    assert data["result"]["ok"] is True
    assert data["response"].startswith("<tool")


def test_model_exception_returns_failure(tmp_path: Path) -> None:
    notes = _store(tmp_path)

    class Boom:
        def complete(self, messages, max_tokens):
            raise RuntimeError("api 500")

    res = reflect_and_write(
        model=Boom(), memory_notes=notes, plan=_plan(),
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=1, dataset_fp="fp",
    )
    assert not res["ok"]
    assert "api 500" in res["reason"]


def test_body_truncated_when_oversized(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    huge_body = (
        "scope: X\nbucket_delta: a:-1\nfalsifies: [推断] n/a\n"
        "mechanism: [推断] m\nconfidence: low\n\n"
        + "x" * 10000
    )  # > 4KB
    payload = {
        "section": "lesson",
        "title": "在 X 阶段巨大正文",
        "body": huge_body,
    }
    model = FakeModelClient([f'<tool name="memory_write"><args>{json.dumps(payload)}</args></tool>'])
    res = reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="improved", score=1.0, prev_best=2.0, score_delta=-1.0,
        report_path=_report(tmp_path), run_id="r", iteration=1, dataset_fp="fp",
    )
    assert res["ok"]
    body = _read_section(tmp_path / "mem", "lesson")
    assert "[truncated]" in body


def test_prompt_includes_outcome_and_plan(tmp_path: Path) -> None:
    notes = _store(tmp_path)
    model = FakeModelClient([
        '<tool name="memory_write"><args>{"section":"lesson","title":"t","body":"b"}</args></tool>'
    ])
    reflect_and_write(
        model=model, memory_notes=notes, plan=_plan(),
        outcome="improved", score=1156.32, prev_best=1170.24, score_delta=-13.92,
        report_path=_report(tmp_path), run_id="r", iteration=4, dataset_fp="fp",
    )
    user_msg = model.last_messages[-1]["content"]
    assert "outcome: improved" in user_msg
    assert "score_delta: -13.9" in user_msg
    assert "non-low_w priority improved to score*w^3" in user_msg
    assert "average_score=1156.32" in user_msg
