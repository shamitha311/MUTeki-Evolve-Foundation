from pathlib import Path

import pytest

from fool.memory_notes import MemoryNotesStore, SECTION_FILES, _parse_frontmatter


def test_store_creates_layout_on_init(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    assert (tmp_path / "mem").is_dir()
    assert (tmp_path / "mem" / "notes").is_dir()
    assert not (tmp_path / "mem" / "MEMORY.md").exists()


def test_section_files_map_complete():
    assert set(SECTION_FILES.keys()) == {
        "preference", "lesson", "try_error", "key_decision",
    }


def test_write_note_creates_per_note_file_with_frontmatter(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    path = store.write_note(
        section="lesson",
        title="Greedy + willingness sort wins on scarce_couriers",
        body="On seed401, sorting candidates by willingness desc improved score by 12%.",
        run_id="run_20260531_140000",
        iteration=4,
    )
    assert path.parent == tmp_path / "mem" / "notes"
    assert path.name.startswith("lesson_")
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    assert fm["name"].startswith("lesson-")
    assert "Greedy" in fm["title"]
    assert "willingness desc" in fm["description"]
    assert fm["metadata"]["type"] == "lesson"
    assert fm["metadata"]["run_id"] == "run_20260531_140000"
    assert fm["metadata"]["iteration"] == "4"
    assert "willingness desc improved score by 12%" in body


def test_write_note_collisions_get_unique_paths(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    a = store.write_note(section="lesson", title="same title", body="b1",
                         run_id="r", iteration=1)
    b = store.write_note(section="lesson", title="same title", body="b2",
                         run_id="r", iteration=2)
    assert a != b
    assert a.exists() and b.exists()


def test_write_note_rejects_unknown_section(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    with pytest.raises(ValueError, match="unknown section"):
        store.write_note(
            section="random", title="x", body="y",
            run_id="r", iteration=1,
        )


def test_write_note_enforces_size_limits(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    with pytest.raises(ValueError, match="body too large"):
        store.write_note(
            section="lesson", title="x", body="x" * 5_000,
            run_id="r", iteration=1,
        )
    with pytest.raises(ValueError, match="title too long"):
        store.write_note(
            section="lesson", title="x" * 100, body="y",
            run_id="r", iteration=1,
        )


def test_search_returns_per_note_results(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    store.write_note(section="lesson", title="Greedy willingness wins scarce_couriers",
                     body="seed401 +12% score with willingness sort",
                     run_id="r1", iteration=1)
    store.write_note(section="try_error", title="ILP times out on large",
                     body="seed301 ILP exceeded 10s budget",
                     run_id="r2", iteration=3)
    store.write_note(section="lesson", title="Random pick baseline",
                     body="useless on tiny_seed42",
                     run_id="r3", iteration=1)

    results = store.search(query="scarce couriers willingness", max_results=2)
    assert len(results) >= 1
    top = results[0]
    assert top["path"].endswith(".md")
    assert "/notes/lesson_" in top["path"]
    assert top["score"] > 0
    assert "willingness" in top["snippet"].lower()
    assert top["start_line"] == 1
    assert top["end_line"] >= 1
    assert top["title"]


def test_search_respects_sections_filter(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    store.write_note(section="lesson", title="A willingness", body="willingness body",
                     run_id="r", iteration=1)
    store.write_note(section="try_error", title="B willingness", body="willingness body",
                     run_id="r", iteration=1)
    results = store.search(query="willingness", sections=["try_error"])
    assert results
    assert all(r["section"] == "try_error" for r in results)


def test_search_empty_when_no_notes(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    assert store.search(query="anything") == []


def test_get_lines_returns_requested_range(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = tmp_path / "mem" / "notes" / "lesson_lines.md"
    p.write_text("\n".join(f"line {i}" for i in range(1, 21)) + "\n", encoding="utf-8")
    result = store.get_lines(path=str(p), offset=5, limit=3)
    assert "line 5\nline 6\nline 7" in result
    assert "line 4" not in result
    assert "line 8" not in result


def test_get_lines_rejects_non_markdown(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = tmp_path / "mem" / "evil.txt"
    p.write_text("secret", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a .md file"):
        store.get_lines(path=str(p))


def test_get_lines_rejects_path_outside_root(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="outside memory root"):
        store.get_lines(path=str(outside))


def test_get_lines_rejects_symlink(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    real = tmp_path / "real.md"
    real.write_text("x", encoding="utf-8")
    link = tmp_path / "mem" / "notes" / "linked.md"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        store.get_lines(path=str(link))


def test_get_lines_appends_truncation_notice_when_capped(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = tmp_path / "mem" / "notes" / "long.md"
    p.write_text("\n".join(f"line {i}" for i in range(1, 1001)) + "\n", encoding="utf-8")
    result = store.get_lines(path=str(p), offset=1, limit=10_000)
    assert "<<<TRUNCATED>>>" in result or result.count("\n") < 10_000


def test_aggregate_emits_link_per_note(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p1 = store.write_note(section="preference", title="stdlib only", body="hard rule",
                          run_id="r", iteration=1)
    p2 = store.write_note(section="lesson", title="willingness wins",
                          body="seed401 +12%", run_id="r", iteration=4)
    p3 = store.write_note(section="try_error", title="ILP times out",
                          body="seed301 ILP exceeded 10s", run_id="r", iteration=2)

    store.aggregate_index()

    idx = (tmp_path / "mem" / "MEMORY.md").read_text(encoding="utf-8")
    assert "# MTASA Memory Index" in idx
    assert "Active Preferences" in idx
    assert "Recent Lessons" in idx
    assert "Recent Try-Errors" in idx
    assert f"- [stdlib only]({p1.relative_to(tmp_path / 'mem').as_posix()})" in idx
    assert f"- [willingness wins]({p2.relative_to(tmp_path / 'mem').as_posix()})" in idx
    assert f"- [ILP times out]({p3.relative_to(tmp_path / 'mem').as_posix()})" in idx
    assert "hard rule" in idx
    assert "seed401 +12%" in idx
    assert "seed301 ILP exceeded 10s" in idx


def test_aggregate_is_idempotent(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    store.write_note(section="lesson", title="x", body="y",
                     run_id="r", iteration=1)
    store.aggregate_index()
    a = (tmp_path / "mem" / "MEMORY.md").read_text(encoding="utf-8")
    store.aggregate_index()
    b = (tmp_path / "mem" / "MEMORY.md").read_text(encoding="utf-8")
    a_norm = "\n".join(l for l in a.splitlines() if "Last aggregated" not in l)
    b_norm = "\n".join(l for l in b.splitlines() if "Last aggregated" not in l)
    assert a_norm == b_norm


def test_append_evidence_appends_to_note_file(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = store.write_note(section="lesson", title="willingness cubed",
                         body="w^3 -20 medium", run_id="r1", iteration=1)
    inserted = store.append_evidence(
        path=p,
        evidence="scarce also confirmed -8 (n=39/40)",
        run_id="r2", iteration=5,
    )
    text = p.read_text(encoding="utf-8")
    assert "scarce also confirmed -8" in text
    lines = text.splitlines()
    assert "<!-- confirmed-by run_id=r2 iteration=5" in lines[inserted - 1]


def test_append_evidence_accepts_anchor_line_kwarg(tmp_path: Path):
    """Backward compat: anchor_line accepted but ignored."""
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = store.write_note(section="lesson", title="t", body="b",
                         run_id="r", iteration=1)
    store.append_evidence(
        path=p, anchor_line=1, evidence="ev",
        run_id="r", iteration=2,
    )
    assert "ev" in p.read_text(encoding="utf-8")


def test_append_evidence_rejects_path_outside_notes_dir(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    rogue = tmp_path / "outside.md"
    rogue.write_text("# hi\n", encoding="utf-8")
    with pytest.raises(ValueError, match="under"):
        store.append_evidence(
            path=rogue, evidence="ev",
            run_id="r", iteration=1,
        )


def test_append_evidence_rejects_missing_path(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    missing = tmp_path / "mem" / "notes" / "lesson_nope.md"
    with pytest.raises(ValueError, match="does not exist"):
        store.append_evidence(
            path=missing, evidence="ev",
            run_id="r", iteration=1,
        )


def test_append_evidence_rejects_empty(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = store.write_note(section="lesson", title="t", body="b",
                         run_id="r", iteration=1)
    with pytest.raises(ValueError, match="empty"):
        store.append_evidence(path=p, evidence="   ",
                              run_id="r", iteration=2)


def test_append_evidence_collapses_multiline_to_single_line(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = store.write_note(section="lesson", title="t", body="b",
                         run_id="r", iteration=1)
    store.append_evidence(
        path=p,
        evidence="line one\nline two\nline three",
        run_id="r", iteration=2,
    )
    text = p.read_text(encoding="utf-8")
    target_lines = [l for l in text.splitlines() if "confirmed-by" in l]
    assert len(target_lines) == 1
    assert "line one line two line three" in target_lines[0]


def test_append_evidence_after_search(tmp_path: Path):
    """End-to-end: search returns path, append_evidence uses it directly."""
    store = MemoryNotesStore(root=tmp_path / "mem")
    store.write_note(section="lesson", title="willingness cubed boost",
                     body="w^3 helps medium/large", run_id="r1", iteration=1)
    hits = store.search("willingness cubed")
    assert hits
    hit = hits[0]
    inserted = store.append_evidence(
        path=Path(hit["path"]), anchor_line=hit["start_line"],
        evidence="scarce also -8", run_id="r2", iteration=4,
    )
    body = Path(hit["path"]).read_text(encoding="utf-8")
    assert "scarce also -8" in body
    assert inserted > 1


def test_decay_confidence_updates_frontmatter(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = store.write_note(
        section="try_error", title="t", body="willingness body",
        run_id="r", iteration=1, confidence=0.8,
    )
    changes = store.decay_confidence(query="willingness", factor=0.5)
    assert changes
    assert changes[0]["old"] == 0.8
    assert changes[0]["new"] == 0.4
    fm, _ = _parse_frontmatter(p.read_text(encoding="utf-8"))
    assert float(fm["metadata"]["confidence"]) == pytest.approx(0.4)
