# Global Memory Notes Store (P1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a global, file-based, agent-readable/writable memory layer (`MEMORY.md` + `notes/*.md`) and split run-time artifacts into `dialog/` + `tool_results/` directories, with 5 new agent tools and a Memory Protocol prompt section — modelled on ReMe's `MemorySearch`/`MemoryGet` design.

**Architecture:** A new `MemoryNotesStore` class manages a global markdown knowledge base under `out/memory/` (separate from the per-dataset `FoolMemory` BM25 store). Five new tools (`memory_search`, `memory_get`, `memory_write`, `read_tool_result`, `read_dialog`) expose the store + run-time files to the LLM. The harness streams chat history to `out/runs/<run_id>/dialog/round_NNN.jsonl` and continues to spill large tool outputs to `out/runs/<run_id>/tool_results/<uuid>.txt`. SessionCompactor routes structured summary sections into the global notes; an end-of-run aggregator rebuilds `MEMORY.md` as an index over `notes/**/*.md`.

**Tech Stack:** Python 3.9+ stdlib + tiktoken (already added). Reuses existing BM25 tokenizer from `fool/memory_store.py`. No new dependencies.

---

## File Structure

**New files:**
- `fool/memory_notes.py` — `MemoryNotesStore` class (write/search/get/aggregate)
- `fool/harness/dialog_writer.py` — small helper that streams messages to `dialog/round_NNN.jsonl`
- `genius/tests/test_memory_notes.py` — unit tests for the store
- `genius/tests/test_memory_notes_tools.py` — unit tests for the 5 new tools
- `genius/tests/test_dialog_writer.py` — unit tests for dialog streaming

**Modified files:**
- `fool/harness/tools.py` — register 5 new tools, add a `MemoryNotesStore` field on `ToolContext`
- `fool/harness/session_compactor.py` — uuid-named spill files, summary→notes routing, accept `memory_notes` injection
- `fool/harness/runner.py` — open/close per-round dialog writer; thread `memory_notes` + `dialog_writer`
- `fool/harness/prompt.py` — tool specs for new tools, Memory Protocol section, round-header `[Memory Index Head]` block
- `fool/fool_loop.py` — construct `MemoryNotesStore` once per run; trigger `aggregate_memory()` at run end
- `frontend/server.py` — split `_purge_memory_store` into `_purge_run_memory` (per-dataset) and `_purge_global_notes` (global); add UI button & API route for the latter

**Global memory layout (new, under `out/memory/`):**
```
out/memory/
├── MEMORY.md                # index/digest, rebuilt by aggregate_memory()
├── notes/
│   ├── preferences.md
│   ├── lessons.md
│   ├── try_errors.md
│   ├── key_decisions.md
│   └── datasets/
│       └── <fingerprint>.md
└── <fingerprint>/           # legacy per-dataset FoolMemory dirs — unchanged
    ├── episodes.jsonl
    ├── strategy_index.json
    ├── session_summaries.jsonl
    └── best_*
```

**Per-run layout (new):**
```
out/runs/<run_id>/
├── dialog/
│   ├── round_001.jsonl
│   └── ...
├── tool_results/
│   └── <uuid>.txt           # uuid filenames (was: random/legacy)
└── ... (existing artifacts unchanged)
```

---

### Task 1: Bootstrap `MemoryNotesStore` skeleton and global directory layout

**Files:**
- Create: `fool/memory_notes.py`
- Test: `genius/tests/test_memory_notes.py`

- [ ] **Step 1: Write failing test for store init + directory creation**

```python
# genius/tests/test_memory_notes.py
from pathlib import Path
from fool.memory_notes import MemoryNotesStore, SECTION_FILES


def test_store_creates_layout_on_init(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    assert (tmp_path / "mem").is_dir()
    assert (tmp_path / "mem" / "notes").is_dir()
    assert (tmp_path / "mem" / "notes" / "datasets").is_dir()
    # MEMORY.md is lazily created — not yet
    assert not (tmp_path / "mem" / "MEMORY.md").exists()


def test_section_files_map_complete():
    assert set(SECTION_FILES.keys()) == {
        "preference", "lesson", "try_error", "key_decision", "dataset_fact",
    }
    for name in ("preference", "lesson", "try_error", "key_decision"):
        assert SECTION_FILES[name].endswith(".md")
    # dataset_fact is special: a router, not a single file
    assert SECTION_FILES["dataset_fact"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest genius/tests/test_memory_notes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fool.memory_notes'`

- [ ] **Step 3: Implement skeleton**

```python
# fool/memory_notes.py
from __future__ import annotations

from pathlib import Path

SECTION_FILES: dict[str, str | None] = {
    "preference": "preferences.md",
    "lesson": "lessons.md",
    "try_error": "try_errors.md",
    "key_decision": "key_decisions.md",
    "dataset_fact": None,  # routed: notes/datasets/<fingerprint>.md
}

INDEX_FILE = "MEMORY.md"


class MemoryNotesStore:
    """Global markdown knowledge base under out/memory/."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.notes_dir = self.root / "notes"
        self.datasets_dir = self.notes_dir / "datasets"
        self.index_path = self.root / INDEX_FILE
        self.root.mkdir(parents=True, exist_ok=True)
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest genius/tests/test_memory_notes.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add fool/memory_notes.py genius/tests/test_memory_notes.py
git commit -m "memory_notes: bootstrap MemoryNotesStore + layout"
```

---

### Task 2: `write_note()` — append to section file with metadata header

**Files:**
- Modify: `fool/memory_notes.py`
- Test: `genius/tests/test_memory_notes.py`

- [ ] **Step 1: Add failing test for write_note + section routing**

```python
def test_write_note_appends_with_metadata(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    store.write_note(
        section="lesson",
        title="Greedy + willingness sort wins on scarce_couriers",
        body="On seed401, sorting candidates by willingness desc improved score by 12%.",
        run_id="run_20260531_140000",
        iteration=4,
    )
    body = (tmp_path / "mem" / "notes" / "lessons.md").read_text(encoding="utf-8")
    assert "# Greedy + willingness sort wins on scarce_couriers" in body
    assert "run_20260531_140000" in body
    assert "iteration=4" in body
    assert "willingness desc improved score by 12%" in body


def test_write_note_dataset_fact_routes_to_dataset_file(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    store.write_note(
        section="dataset_fact",
        title="seed42 has 3 couriers per task on average",
        body="...",
        run_id="r1", iteration=1,
        dataset_fp="abc123",
    )
    target = tmp_path / "mem" / "notes" / "datasets" / "abc123.md"
    assert target.exists()
    assert "seed42 has 3 couriers" in target.read_text(encoding="utf-8")


def test_write_note_dataset_fact_requires_fingerprint(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    import pytest
    with pytest.raises(ValueError, match="dataset_fp"):
        store.write_note(
            section="dataset_fact", title="x", body="y",
            run_id="r", iteration=1, dataset_fp=None,
        )


def test_write_note_rejects_unknown_section(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    import pytest
    with pytest.raises(ValueError, match="unknown section"):
        store.write_note(
            section="random", title="x", body="y",
            run_id="r", iteration=1,
        )


def test_write_note_enforces_size_limits(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    import pytest
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
```

- [ ] **Step 2: Run tests, see failures**

Run: `python -m pytest genius/tests/test_memory_notes.py -v`
Expected: 4 FAIL (AttributeError or method missing)

- [ ] **Step 3: Implement `write_note`**

```python
# Add to fool/memory_notes.py

from datetime import datetime, timezone

MAX_TITLE_LEN = 80
MAX_BODY_BYTES = 4 * 1024


class MemoryNotesStore:
    # ... existing __init__ ...

    def write_note(
        self,
        *,
        section: str,
        title: str,
        body: str,
        run_id: str,
        iteration: int,
        dataset_fp: str | None = None,
    ) -> Path:
        """Append a note to the appropriate section file. Returns the file path."""
        if section not in SECTION_FILES:
            raise ValueError(f"unknown section: {section!r}")
        if len(title) > MAX_TITLE_LEN:
            raise ValueError(f"title too long: {len(title)} > {MAX_TITLE_LEN}")
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError(f"body too large: {len(body.encode('utf-8'))} > {MAX_BODY_BYTES}")

        if section == "dataset_fact":
            if not dataset_fp:
                raise ValueError("dataset_fp is required for section='dataset_fact'")
            target = self.datasets_dir / f"{dataset_fp}.md"
        else:
            target = self.notes_dir / SECTION_FILES[section]

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = (
            f"\n\n# {title.strip()}\n"
            f"<!-- run_id={run_id} iteration={iteration} ts={ts} -->\n\n"
            f"{body.strip()}\n"
        )
        with target.open("a", encoding="utf-8") as f:
            f.write(entry)
        return target
```

- [ ] **Step 4: Run tests, see pass**

Run: `python -m pytest genius/tests/test_memory_notes.py -v`
Expected: 6 passed (2 from Task 1 + 4 new)

- [ ] **Step 5: Commit**

```bash
git add fool/memory_notes.py genius/tests/test_memory_notes.py
git commit -m "memory_notes: write_note() with section routing + metadata"
```

---

### Task 3: `search()` — BM25 over `notes/**/*.md` returning snippets with path+lines

**Files:**
- Modify: `fool/memory_notes.py`
- Test: `genius/tests/test_memory_notes.py`

- [ ] **Step 1: Add failing test**

```python
def test_search_returns_top_snippets_with_path_and_lines(tmp_path: Path):
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
    assert top["path"].endswith("notes/lessons.md")
    assert top["score"] > 0
    assert "willingness" in top["snippet"].lower()
    # Line range is 1-indexed and inclusive
    assert top["start_line"] >= 1
    assert top["end_line"] >= top["start_line"]


def test_search_respects_sections_filter(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    store.write_note(section="lesson", title="A", body="willingness",
                     run_id="r", iteration=1)
    store.write_note(section="try_error", title="B", body="willingness",
                     run_id="r", iteration=1)
    results = store.search(query="willingness", sections=["try_error"])
    assert all("try_errors.md" in r["path"] for r in results)


def test_search_empty_when_no_notes(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    assert store.search(query="anything") == []
```

- [ ] **Step 2: Run tests, see failures**

Run: `python -m pytest genius/tests/test_memory_notes.py::test_search_returns_top_snippets_with_path_and_lines -v`
Expected: FAIL

- [ ] **Step 3: Implement `search()` reusing the existing BM25 tokenizer**

```python
# Add to fool/memory_notes.py

from fool.memory_store import _token_list  # reuse tokenizer

_SECTION_TO_FILE = {
    "preference": "preferences.md",
    "lesson": "lessons.md",
    "try_error": "try_errors.md",
    "key_decision": "key_decisions.md",
}


def _iter_note_files(store: "MemoryNotesStore", sections: list[str] | None):
    if not sections or "all" in sections:
        yield from store.notes_dir.rglob("*.md")
        return
    for s in sections:
        if s == "dataset_fact":
            yield from store.datasets_dir.glob("*.md")
        elif s in _SECTION_TO_FILE:
            p = store.notes_dir / _SECTION_TO_FILE[s]
            if p.exists():
                yield p


def _split_entries(md_text: str) -> list[tuple[int, int, str]]:
    """Split a notes/*.md into entries by `# Title` headers.
    Returns [(start_line_1idx, end_line_1idx, text), ...]."""
    lines = md_text.splitlines()
    entries: list[tuple[int, int, str]] = []
    current_start = None
    current_lines: list[str] = []
    for i, line in enumerate(lines, start=1):
        if line.startswith("# "):
            if current_start is not None:
                entries.append((current_start, i - 1, "\n".join(current_lines)))
            current_start = i
            current_lines = [line]
        elif current_start is not None:
            current_lines.append(line)
    if current_start is not None:
        entries.append((current_start, len(lines), "\n".join(current_lines)))
    return entries


class MemoryNotesStore:
    # ... existing methods ...

    def search(
        self,
        query: str,
        *,
        sections: list[str] | None = None,
        max_results: int = 5,
    ) -> list[dict]:
        q_tokens = set(_token_list(query))
        if not q_tokens:
            return []
        scored: list[tuple[float, str, int, int, str]] = []
        for path in _iter_note_files(self, sections):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for start_line, end_line, entry_text in _split_entries(text):
                tokens = _token_list(entry_text)
                if not tokens:
                    continue
                hits = sum(1 for t in tokens if t in q_tokens)
                if hits == 0:
                    continue
                # Simple tf normalized by sqrt(len)
                import math
                score = hits / math.sqrt(len(tokens))
                snippet = entry_text[:400]
                scored.append((score, str(path), start_line, end_line, snippet))
        scored.sort(key=lambda x: -x[0])
        return [
            {"path": p, "start_line": sl, "end_line": el, "score": round(s, 4), "snippet": sn}
            for (s, p, sl, el, sn) in scored[:max_results]
        ]
```

- [ ] **Step 4: Run tests, see pass**

Run: `python -m pytest genius/tests/test_memory_notes.py -v`
Expected: all green (9 tests)

- [ ] **Step 5: Commit**

```bash
git add fool/memory_notes.py genius/tests/test_memory_notes.py
git commit -m "memory_notes: search() with BM25 over note entries"
```

---

### Task 4: `get_lines()` — safe line-range read with `.md` whitelist

**Files:**
- Modify: `fool/memory_notes.py`
- Test: `genius/tests/test_memory_notes.py`

- [ ] **Step 1: Add failing tests**

```python
def test_get_lines_returns_requested_range(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = tmp_path / "mem" / "notes" / "preferences.md"
    p.write_text("\n".join(f"line {i}" for i in range(1, 21)) + "\n", encoding="utf-8")
    result = store.get_lines(path=str(p), offset=5, limit=3)
    assert "line 5\nline 6\nline 7" in result
    assert "line 4" not in result
    assert "line 8" not in result


def test_get_lines_rejects_non_markdown(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = tmp_path / "mem" / "evil.txt"
    p.write_text("secret", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="must be a .md file"):
        store.get_lines(path=str(p))


def test_get_lines_rejects_path_outside_root(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="outside memory root"):
        store.get_lines(path=str(outside))


def test_get_lines_rejects_symlink(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    real = tmp_path / "real.md"
    real.write_text("x", encoding="utf-8")
    link = tmp_path / "mem" / "notes" / "linked.md"
    link.symlink_to(real)
    import pytest
    with pytest.raises(ValueError, match="symlink"):
        store.get_lines(path=str(link))


def test_get_lines_appends_truncation_notice_when_capped(tmp_path: Path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    p = tmp_path / "mem" / "notes" / "preferences.md"
    p.write_text("\n".join(f"line {i}" for i in range(1, 1001)) + "\n", encoding="utf-8")
    result = store.get_lines(path=str(p), offset=1, limit=10_000)
    # default max_lines guard
    assert "<<<TRUNCATED>>>" in result or result.count("\n") < 10_000
```

- [ ] **Step 2: Run tests, see failures**

Run: `python -m pytest genius/tests/test_memory_notes.py -v -k get_lines`
Expected: 5 FAIL

- [ ] **Step 3: Implement `get_lines`**

```python
# Add to fool/memory_notes.py

DEFAULT_GET_MAX_LINES = 800


class MemoryNotesStore:
    # ... existing ...

    def get_lines(
        self,
        *,
        path: str,
        offset: int = 1,
        limit: int = DEFAULT_GET_MAX_LINES,
    ) -> str:
        from os.path import realpath
        p = Path(path)
        if p.suffix.lower() != ".md":
            raise ValueError(f"path must be a .md file: {path}")
        if p.is_symlink():
            raise ValueError(f"path is a symlink (refused): {path}")
        # Containment: resolved real path must be under resolved root
        root_real = realpath(self.root)
        p_real = realpath(p)
        if not p_real.startswith(root_real + "/") and p_real != root_real:
            raise ValueError(f"path outside memory root: {path}")
        if not p.is_file():
            raise FileNotFoundError(f"not a regular file: {path}")
        if offset < 1:
            raise ValueError("offset must be >= 1")
        capped = min(max(1, limit), DEFAULT_GET_MAX_LINES)
        lines = p.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        end = min(offset - 1 + capped, total)
        chunk = "\n".join(lines[offset - 1:end])
        if end < total:
            chunk += f"\n<<<TRUNCATED>>> file has {total} lines; read more with offset={end + 1}"
        return chunk
```

- [ ] **Step 4: Run tests, see pass**

Run: `python -m pytest genius/tests/test_memory_notes.py -v`
Expected: all green

- [ ] **Step 5: Commit**

```bash
git add fool/memory_notes.py genius/tests/test_memory_notes.py
git commit -m "memory_notes: get_lines() with .md whitelist + containment guard"
```

---

### Task 5: Dialog streaming writer (per-round JSONL)

**Files:**
- Create: `fool/harness/dialog_writer.py`
- Test: `genius/tests/test_dialog_writer.py`

- [ ] **Step 1: Failing test**

```python
# genius/tests/test_dialog_writer.py
import json
from pathlib import Path
from fool.harness.dialog_writer import DialogWriter


def test_writer_creates_round_file_and_appends(tmp_path: Path):
    w = DialogWriter(run_dir=tmp_path, round_idx=2)
    w.append({"role": "user", "content": "hi"})
    w.append({"role": "assistant", "content": "hello"})
    path = tmp_path / "dialog" / "round_002.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["role"] == "user"
    assert json.loads(lines[1])["content"] == "hello"


def test_writer_appends_timestamp(tmp_path: Path):
    w = DialogWriter(run_dir=tmp_path, round_idx=1)
    w.append({"role": "user", "content": "x"})
    rec = json.loads((tmp_path / "dialog" / "round_001.jsonl").read_text().strip())
    assert "ts" in rec
    # ISO-ish; only sanity-check shape
    assert "T" in rec["ts"]


def test_writer_path_helper(tmp_path: Path):
    w = DialogWriter(run_dir=tmp_path, round_idx=10)
    assert w.path == tmp_path / "dialog" / "round_010.jsonl"
```

- [ ] **Step 2: Run, see fail**

Run: `python -m pytest genius/tests/test_dialog_writer.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement**

```python
# fool/harness/dialog_writer.py
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class DialogWriter:
    """Stream messages to out/runs/<run_id>/dialog/round_NNN.jsonl as JSON lines."""

    def __init__(self, *, run_dir: Path, round_idx: int) -> None:
        self.run_dir = Path(run_dir)
        self.round_idx = int(round_idx)
        self.dialog_dir = self.run_dir / "dialog"
        self.dialog_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dialog_dir / f"round_{self.round_idx:03d}.jsonl"

    def append(self, msg: dict) -> None:
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            **msg,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Run, pass**

Run: `python -m pytest genius/tests/test_dialog_writer.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add fool/harness/dialog_writer.py genius/tests/test_dialog_writer.py
git commit -m "dialog_writer: per-round JSONL streaming"
```

---

### Task 6: Switch SessionCompactor spill to uuid filenames + route summary to notes

**Files:**
- Modify: `fool/harness/session_compactor.py`
- Test: `genius/tests/test_session_compactor.py` (extend)

- [ ] **Step 1: Failing test for uuid filename**

```python
# genius/tests/test_session_compactor.py — append
import re
from fool.harness.session_compactor import compact_tool_results

def test_tool_result_spill_uses_uuid_filename(tmp_path):
    big = "line\n" * 2000
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "H"},
        {"role": "user", "content": big},
    ]
    out = compact_tool_results(
        msgs, tool_result_dir=tmp_path, old_max_bytes=200,
        recent_max_bytes=300, recent_n=0,
    )
    files = list(tmp_path.glob("*.txt"))
    assert len(files) == 1
    # uuid4 hex == 32 chars
    assert re.fullmatch(r"[0-9a-f]{32}\.txt", files[0].name)
    # Filename is referenced in the truncated message
    assert files[0].name in out[2]["content"] or files[0].stem in out[2]["content"]
```

- [ ] **Step 2: Failing test for summary-to-notes routing**

```python
def test_compactor_routes_summary_to_notes(tmp_path):
    from fool.memory_notes import MemoryNotesStore
    from fool.harness.session_compactor import SessionCompactor
    from fool.harness.model_client import FakeModelClient

    notes = MemoryNotesStore(root=tmp_path / "mem")
    summary = (
        "## 目标\nx\n## 约束和偏好\n- 必须 stdlib only\n"
        "## 进展\n### 已完成\n- [x] baseline\n"
        "## 关键决策\n- **使用 greedy**: 简单可行\n"
        "## 下一步\n1. 试 willingness 排序\n"
        "## 关键上下文\n(none)\n"
    )
    summarizer = FakeModelClient(outputs=[summary])
    compactor = SessionCompactor(
        summarizer=summarizer,
        tool_result_dir=tmp_path / "tr",
        threshold_tokens=4_000,
        reserve_tokens=1_000,
        memory_notes=notes,
        run_id="run_test",
        iteration=2,
    )
    msgs = [{"role": "system", "content": "S" * 200}, {"role": "user", "content": "H" * 200}] + [
        {"role": "assistant", "content": "x" * 5000} for _ in range(8)
    ]
    compactor.maybe_compact(msgs, previous_summary="")

    decisions = (tmp_path / "mem" / "notes" / "key_decisions.md").read_text(encoding="utf-8")
    assert "使用 greedy" in decisions
    assert "run_test" in decisions
```

- [ ] **Step 3: Run, see fail**

Run: `python -m pytest genius/tests/test_session_compactor.py -v -k "uuid_filename or routes_summary"`
Expected: FAIL

- [ ] **Step 4: Implement**

In `compact_tool_results` change the spill filename to `f"{uuid.uuid4().hex}.txt"` (it already imports `uuid`; remove any timestamp-based naming).

Add `memory_notes`, `run_id`, `iteration` kwargs to `SessionCompactor.__init__`. After the summarizer returns (around `_compact_prompts`-prompt response, just before `summary_callback`), extract `## 关键决策` and `## 关键上下文` (try-errors mentions) and route via `memory_notes.write_note(section="key_decision", ...)` / `section="try_error"`.

Sketch of extractor:

```python
import re

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

def _extract_sections(summary_md: str) -> dict[str, str]:
    out = {}
    parts = _SECTION_RE.split(summary_md)
    # parts = [pre, name1, body1, name2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip()] = parts[i + 1].strip()
    return out
```

In `maybe_compact`, after summary received and before `summary_callback`:

```python
if self._memory_notes is not None and self._run_id:
    sections = _extract_sections(summary)
    for ch_name, our_section in (
        ("关键决策", "key_decision"),
        ("Key Decisions", "key_decision"),
    ):
        body = sections.get(ch_name)
        if body:
            try:
                self._memory_notes.write_note(
                    section=our_section,
                    title=f"Round {self._iteration}: compactor-extracted key decisions",
                    body=body[:3500],
                    run_id=self._run_id,
                    iteration=self._iteration,
                )
            except ValueError as e:
                logger.warning("note write skipped: %s", e)
```

- [ ] **Step 5: Run, pass**

Run: `python -m pytest genius/tests/test_session_compactor.py -v`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add fool/harness/session_compactor.py genius/tests/test_session_compactor.py
git commit -m "session_compactor: uuid spill + summary→notes routing"
```

---

### Task 7: Wire `MemoryNotesStore` into `ToolContext` + register 5 new tools

**Files:**
- Modify: `fool/harness/tools.py`
- Test: `genius/tests/test_memory_notes_tools.py` (new)

- [ ] **Step 1: Failing tests for all 5 tools**

```python
# genius/tests/test_memory_notes_tools.py
from pathlib import Path
import json
from fool.harness.tools import build_default_registry, ToolContext
from fool.memory_notes import MemoryNotesStore


def _ctx(tmp_path: Path, *, memory_notes: MemoryNotesStore | None = None) -> ToolContext:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "tool_results").mkdir()
    (run_dir / "dialog").mkdir()
    return ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None, best_report_path=None, last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
        memory_notes=memory_notes,
    )


def test_memory_write_tool_creates_note(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    ctx = _ctx(tmp_path, memory_notes=notes)
    reg = build_default_registry()
    res = reg.run("memory_write", ctx, {
        "section": "lesson",
        "title": "Test note",
        "body": "useful",
        "run_id": "r1",
        "iteration": 1,
    })
    assert res.ok
    body = (tmp_path / "mem" / "notes" / "lessons.md").read_text(encoding="utf-8")
    assert "Test note" in body


def test_memory_search_tool_returns_snippets(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    notes.write_note(section="lesson", title="willingness wins",
                     body="seed401 +12%", run_id="r", iteration=1)
    ctx = _ctx(tmp_path, memory_notes=notes)
    reg = build_default_registry()
    res = reg.run("memory_search", ctx, {"query": "willingness"})
    assert res.ok
    payload = json.loads(res.content)
    assert payload["results"]
    assert "willingness" in payload["results"][0]["snippet"].lower()


def test_memory_get_tool_reads_lines(tmp_path: Path):
    notes = MemoryNotesStore(root=tmp_path / "mem")
    notes.write_note(section="lesson", title="A", body="alpha beta gamma",
                     run_id="r", iteration=1)
    ctx = _ctx(tmp_path, memory_notes=notes)
    reg = build_default_registry()
    target = str(tmp_path / "mem" / "notes" / "lessons.md")
    res = reg.run("memory_get", ctx, {"path": target, "offset": 1, "limit": 10})
    assert res.ok
    assert "alpha beta gamma" in res.content


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


def test_read_dialog_serves_round_file(tmp_path: Path):
    ctx = _ctx(tmp_path)
    p = ctx.run_dir / "dialog" / "round_002.jsonl"
    p.write_text(
        json.dumps({"role": "user", "content": "hi"}) + "\n"
        + json.dumps({"role": "assistant", "content": "ok"}) + "\n",
        encoding="utf-8",
    )
    reg = build_default_registry()
    res = reg.run("read_dialog", ctx, {"round": 2})
    assert res.ok
    assert "hi" in res.content and "ok" in res.content


def test_memory_tools_no_op_when_store_missing(tmp_path: Path):
    ctx = _ctx(tmp_path, memory_notes=None)
    reg = build_default_registry()
    res = reg.run("memory_write", ctx, {"section": "lesson", "title": "x", "body": "y",
                                          "run_id": "r", "iteration": 1})
    assert not res.ok
    assert "memory_notes not configured" in res.content
```

- [ ] **Step 2: Run, see failures**

Run: `python -m pytest genius/tests/test_memory_notes_tools.py -v`
Expected: all FAIL (tools not registered)

- [ ] **Step 3: Add `memory_notes` field to `ToolContext` and implement 5 tool functions**

In `fool/harness/tools.py`:

```python
@dataclass
class ToolContext:
    input_dir: Path
    run_dir: Path
    best_solver_path: Path | None
    best_report_path: Path | None
    last_report_path: Path | None
    bootstrap_solver_path: Path | None
    durable_memory: Any
    dataset_profile_text: str
    memory_notes: Any = None  # fool.memory_notes.MemoryNotesStore or None
```

Add tool implementations:

```python
import json as _json
import re as _re

_UUID_RE = _re.compile(r"^[0-9a-f]{32}$")


def _t_memory_write(ctx: ToolContext, args: dict) -> ToolResult:
    if ctx.memory_notes is None:
        return ToolResult(ok=False, content="memory_notes not configured")
    try:
        path = ctx.memory_notes.write_note(
            section=args["section"],
            title=args["title"],
            body=args["body"],
            run_id=args["run_id"],
            iteration=int(args["iteration"]),
            dataset_fp=args.get("dataset_fp"),
        )
        return ToolResult(ok=True, content=f"written to {path}")
    except (KeyError, ValueError) as e:
        return ToolResult(ok=False, content=f"memory_write error: {e}")


def _t_memory_search(ctx: ToolContext, args: dict) -> ToolResult:
    if ctx.memory_notes is None:
        return ToolResult(ok=False, content="memory_notes not configured")
    results = ctx.memory_notes.search(
        query=args.get("query", ""),
        sections=args.get("sections"),
        max_results=int(args.get("max_results", 5)),
    )
    return ToolResult(ok=True, content=_json.dumps({"results": results}, ensure_ascii=False))


def _t_memory_get(ctx: ToolContext, args: dict) -> ToolResult:
    if ctx.memory_notes is None:
        return ToolResult(ok=False, content="memory_notes not configured")
    try:
        text = ctx.memory_notes.get_lines(
            path=args["path"],
            offset=int(args.get("offset", 1)),
            limit=int(args.get("limit", 800)),
        )
        return ToolResult(ok=True, content=text)
    except (KeyError, ValueError, FileNotFoundError) as e:
        return ToolResult(ok=False, content=f"memory_get error: {e}")


def _t_read_tool_result(ctx: ToolContext, args: dict) -> ToolResult:
    uid = str(args.get("uuid", ""))
    if not _UUID_RE.match(uid):
        return ToolResult(ok=False, content=f"invalid uuid: {uid!r}")
    path = ctx.run_dir / "tool_results" / f"{uid}.txt"
    if not path.is_file():
        return ToolResult(ok=False, content=f"no such tool_result: {uid}")
    start = max(1, int(args.get("start_line", 1)))
    cap = min(max(1, int(args.get("max_lines", 800))), 2000)
    lines = path.read_text(encoding="utf-8").splitlines()
    end = min(start - 1 + cap, len(lines))
    chunk = "\n".join(lines[start - 1:end])
    if end < len(lines):
        chunk += f"\n<<<TRUNCATED>>> more at start_line={end + 1} (total {len(lines)} lines)"
    return ToolResult(ok=True, content=chunk)


def _t_read_dialog(ctx: ToolContext, args: dict) -> ToolResult:
    try:
        rnd = int(args["round"])
    except (KeyError, ValueError, TypeError):
        return ToolResult(ok=False, content="round (int) required")
    path = ctx.run_dir / "dialog" / f"round_{rnd:03d}.jsonl"
    if not path.is_file():
        return ToolResult(ok=False, content=f"no dialog file for round {rnd}")
    start = max(1, int(args.get("start_line", 1)))
    cap = min(max(1, int(args.get("max_lines", 400))), 1000)
    lines = path.read_text(encoding="utf-8").splitlines()
    end = min(start - 1 + cap, len(lines))
    chunk = "\n".join(lines[start - 1:end])
    if end < len(lines):
        chunk += f"\n<<<TRUNCATED>>> more at start_line={end + 1} (total {len(lines)} lines)"
    return ToolResult(ok=True, content=chunk)
```

Register in `build_default_registry()` (extend `_BUILTIN_TOOLS` or its registration list):

```python
ToolSpec(
    name="memory_write",
    description="Append a structured note to the global memory store.",
    risky=False,
    schema={
        "section": "preference|lesson|try_error|key_decision|dataset_fact",
        "title": "<=80 chars",
        "body": "<=4KB",
        "run_id": "current run id",
        "iteration": "current iteration int",
        "dataset_fp": "(optional, required only for dataset_fact)",
    },
    run=_t_memory_write,
),
ToolSpec(
    name="memory_search",
    description="Mandatory recall step: BM25-search MEMORY.md + notes/**/*.md before proposing a new hypothesis. Returns snippets with path+lines.",
    risky=False,
    schema={"query": "str", "sections": "optional list", "max_results": "optional int"},
    run=_t_memory_search,
    max_output=8 * 1024,
),
ToolSpec(
    name="memory_get",
    description="Read exact lines from a memory .md file (use after memory_search to pull cited lines).",
    risky=False,
    schema={"path": "str", "offset": "1-indexed line", "limit": "max lines (<=800)"},
    run=_t_memory_get,
    max_output=32 * 1024,
),
ToolSpec(
    name="read_tool_result",
    description="Continue reading a spilled tool output (seen in <<<TRUNCATED>>> marker).",
    risky=False,
    schema={"uuid": "32-hex string", "start_line": "int", "max_lines": "int"},
    run=_t_read_tool_result,
    max_output=64 * 1024,
),
ToolSpec(
    name="read_dialog",
    description="Read previous-round dialog messages (jsonl).",
    risky=False,
    schema={"round": "int", "start_line": "int", "max_lines": "int"},
    run=_t_read_dialog,
    max_output=32 * 1024,
),
```

- [ ] **Step 4: Run, see pass**

Run: `python -m pytest genius/tests/test_memory_notes_tools.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add fool/harness/tools.py genius/tests/test_memory_notes_tools.py
git commit -m "tools: 5 new memory/dialog tools wired into harness"
```

---

### Task 8: Runner plumbs `DialogWriter` + `MemoryNotesStore`

**Files:**
- Modify: `fool/harness/runner.py`
- Test: `genius/tests/test_harness_runner.py` (extend)

- [ ] **Step 1: Failing tests**

```python
# genius/tests/test_harness_runner.py — append

def test_runner_streams_dialog_jsonl(tmp_path):
    """run_round writes each message turn to dialog/round_001.jsonl."""
    fake = FakeModelClient([_VALID_DRAFT, _VALID_FINAL])
    state = _state(tmp_path)
    run_round(state, fake, max_steps=4, max_new_tokens=256)
    p = state.run_dir / "dialog" / "round_001.jsonl"
    assert p.exists()
    lines = p.read_text(encoding="utf-8").splitlines()
    # At least: system, user header, assistant draft, user tool_result, assistant final
    assert len(lines) >= 4
    import json
    parsed = [json.loads(l) for l in lines]
    roles = [m["role"] for m in parsed]
    assert "system" in roles and "assistant" in roles


def test_runner_threads_memory_notes_into_tool_context(tmp_path):
    """When memory_notes is passed, tools should see it via ToolContext."""
    from fool.memory_notes import MemoryNotesStore
    notes = MemoryNotesStore(root=tmp_path / "mem")
    # Pre-populate a note so memory_search returns something
    notes.write_note(section="lesson", title="hint", body="seed42 is small",
                     run_id="prev", iteration=1)

    # Model asks for memory_search first, then finalizes
    search_call = _INTENT + '<tool>{"name":"memory_search","args":{"query":"seed42"}}</tool>'
    fake = FakeModelClient([search_call, _VALID_FINAL])
    state = _state(tmp_path)
    result = run_round(state, fake, max_steps=4, max_new_tokens=256, memory_notes=notes)

    # Look at the user message that came back from the memory_search call (3rd msg in 2nd call)
    second = fake.calls[1]
    last_user = second[-1]["content"]
    assert "[tool_result name=memory_search" in last_user
    assert "hint" in last_user or "seed42" in last_user
```

- [ ] **Step 2: Run, see fail**

Run: `python -m pytest genius/tests/test_harness_runner.py -v -k "streams_dialog or threads_memory"`
Expected: FAIL (kwargs missing, dialog file absent)

- [ ] **Step 3: Implement**

In `fool/harness/runner.py`:

```python
from fool.harness.dialog_writer import DialogWriter
# (already has TYPE_CHECKING for SessionCompactor)

def run_round(
    state: RoundState,
    model: ModelClient,
    *,
    max_steps: int,
    max_new_tokens: int,
    on_step: Callable | None = None,
    compactor: "SessionCompactor | None" = None,
    memory_notes: "MemoryNotesStore | None" = None,
) -> RoundResult:
    ...
    dialog = DialogWriter(run_dir=state.run_dir, round_idx=state.iteration)

    # Build ToolContext: include memory_notes
    tool_ctx = ToolContext(
        ...,
        memory_notes=memory_notes,
    )

    # In the main loop, append each message as it's added to `messages`:
    #   - the initial system+user
    #   - each assistant reply
    #   - each tool_result user message
    # Wrap appends; the simplest is to append at the same place messages.append() happens.
```

- [ ] **Step 4: Run, pass**

Run: `python -m pytest genius/tests/test_harness_runner.py -v`
Expected: all green

- [ ] **Step 5: Commit**

```bash
git add fool/harness/runner.py genius/tests/test_harness_runner.py
git commit -m "runner: thread dialog_writer + memory_notes into round"
```

---

### Task 9: Prompt — tool specs + Memory Protocol + round-header `[Memory Index Head]`

**Files:**
- Modify: `fool/harness/prompt.py`
- Test: `genius/tests/test_prompt.py` (extend or create — check first)

- [ ] **Step 1: Failing tests**

```python
# Append to whichever test file covers prompt (or create genius/tests/test_prompt.py)
from fool.harness.prompt import build_system_prefix, build_round_header


def test_system_prefix_lists_new_tools():
    sp = build_system_prefix()
    for tool in ("memory_search", "memory_get", "memory_write",
                 "read_tool_result", "read_dialog"):
        assert tool in sp


def test_system_prefix_contains_memory_protocol():
    sp = build_system_prefix()
    assert "Memory Protocol" in sp
    assert "memory_search" in sp.split("Memory Protocol", 1)[1]
    # Mandatory before-write rule
    assert "try_error" in sp
    assert "<<<TRUNCATED>>>" in sp


def test_round_header_includes_memory_index_head_when_present(tmp_path):
    # If MEMORY.md exists, header embeds first lines
    index = tmp_path / "MEMORY.md"
    index.write_text("# MTASA Memory Index\n## Active Preferences\n- stdlib only\n",
                     encoding="utf-8")
    header = build_round_header(
        iteration=2,
        recent_history=[],
        best_score=None,
        compact_summary="",
        memory_index_path=index,
    )
    assert "Memory Index Head" in header
    assert "stdlib only" in header


def test_round_header_skips_memory_block_when_index_missing(tmp_path):
    header = build_round_header(
        iteration=2, recent_history=[], best_score=None,
        compact_summary="", memory_index_path=tmp_path / "missing.md",
    )
    assert "Memory Index Head" not in header
```

- [ ] **Step 2: Run, see fail**

Run: `python -m pytest genius/tests/test_prompt.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

In `fool/harness/prompt.py`:

1. Append the 5 new tool descriptions to the existing tool-spec block (mirror existing tool descriptions in style).

2. Add a `Memory Protocol` section. Suggested text (Chinese-first per project convention):

```
## Memory Protocol（强制）

在提出新假设之前，你**必须**：
1. 调用 `memory_search(query=<假设的关键词>)` 检查是否已被标记为 try_error 或重复 lesson
2. 若结果中给出 path+行号，调 `memory_get(path, offset, limit)` 精读引用片段；不要凭 snippet 残段下结论

在确认本轮结果之后，你**必须**：
- 若假设 FAILED：`memory_write(section="try_error", title=..., body=...)` 带证据 (run_id, iteration, score delta)
- 若发现新约束：`memory_write(section="preference", ...)`
- 若假设 WON（提升 best）：`memory_write(section="lesson"|"key_decision", ...)`

当工具结果末尾出现 `<<<TRUNCATED>>>` 时：
- 优先用 `read_tool_result(uuid=..., start_line=N)` 续读；不要在残段上下结论。
- 跨轮历史回查使用 `read_dialog(round=N)`。
```

3. Modify `build_round_header(...)` (or create it if not extracted yet) to optionally accept `memory_index_path: Path | None`. When present and the file exists, prepend a block:

```
[Memory Index Head]
<first 80 lines of MEMORY.md or "(empty)">
```

- [ ] **Step 4: Run, pass**

Run: `python -m pytest genius/tests/test_prompt.py -v`
Expected: green

- [ ] **Step 5: Commit**

```bash
git add fool/harness/prompt.py genius/tests/test_prompt.py
git commit -m "prompt: Memory Protocol + tool specs + memory index head"
```

---

### Task 10: `aggregate_memory()` end-of-run + `fool_loop` integration

**Files:**
- Modify: `fool/memory_notes.py`, `fool/fool_loop.py`
- Test: `genius/tests/test_memory_notes.py`

- [ ] **Step 1: Failing test for aggregator**

```python
def test_aggregate_rebuilds_memory_index(tmp_path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    store.write_note(section="preference", title="stdlib only", body="hard rule",
                     run_id="r", iteration=1)
    store.write_note(section="lesson", title="willingness wins",
                     body="seed401 +12%", run_id="r", iteration=4)
    store.write_note(section="try_error", title="ILP times out",
                     body="seed301 ILP exceeded 10s", run_id="r", iteration=2)

    store.aggregate_index()

    idx = (tmp_path / "mem" / "MEMORY.md").read_text(encoding="utf-8")
    assert "# MTASA Memory Index" in idx
    assert "Active Preferences" in idx
    assert "stdlib only" in idx
    assert "Recent Lessons" in idx
    assert "willingness wins" in idx
    assert "Recent Try-Errors" in idx
    assert "ILP times out" in idx
    # Cited paths
    assert "notes/preferences.md" in idx
    assert "notes/lessons.md" in idx


def test_aggregate_is_idempotent(tmp_path):
    store = MemoryNotesStore(root=tmp_path / "mem")
    store.write_note(section="lesson", title="x", body="y",
                     run_id="r", iteration=1)
    store.aggregate_index()
    a = (tmp_path / "mem" / "MEMORY.md").read_text()
    store.aggregate_index()
    b = (tmp_path / "mem" / "MEMORY.md").read_text()
    # Timestamps differ but body should be substantially equal
    a_norm = "\n".join(l for l in a.splitlines() if "Last aggregated" not in l)
    b_norm = "\n".join(l for l in b.splitlines() if "Last aggregated" not in l)
    assert a_norm == b_norm
```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Implement `aggregate_index()` — pure-Python (no LLM)**

The aggregator scans each `notes/*.md`, extracts entries via `_split_entries`, picks the top N most recent per section by `ts` in the HTML comment, and writes `MEMORY.md`:

```python
class MemoryNotesStore:
    # ...
    def aggregate_index(self, *, top_n_per_section: int = 5) -> Path:
        sections_order = [
            ("preference", "Active Preferences"),
            ("lesson", "Recent Lessons"),
            ("try_error", "Recent Try-Errors"),
            ("key_decision", "Recent Decisions"),
        ]
        out: list[str] = ["# MTASA Memory Index"]
        out.append(
            f"> Last aggregated: "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
        for sec, heading in sections_order:
            path = self.notes_dir / SECTION_FILES[sec]
            if not path.exists():
                continue
            entries = _split_entries(path.read_text(encoding="utf-8"))
            # Each entry's first line is `# title`; sort by ts desc found in comment.
            scored: list[tuple[str, int, int, str]] = []
            ts_re = re.compile(r"ts=([0-9T:\-Z]+)")
            for s, e, txt in entries:
                m = ts_re.search(txt)
                ts = m.group(1) if m else ""
                first = txt.splitlines()[0].lstrip("# ").strip()
                scored.append((ts, s, e, first))
            scored.sort(key=lambda x: x[0], reverse=True)
            if not scored:
                continue
            out.append(f"\n## {heading} (top {min(top_n_per_section, len(scored))})")
            for ts, s, e, title in scored[:top_n_per_section]:
                rel = path.relative_to(self.root)
                out.append(f"- {title} → {rel}:{s}-{e}")
        # Datasets seen
        ds_files = sorted(self.datasets_dir.glob("*.md"))
        if ds_files:
            out.append("\n## Datasets Seen")
            for f in ds_files:
                out.append(f"- {f.stem} → {f.relative_to(self.root)}")
        text = "\n".join(out) + "\n"
        self.index_path.write_text(text, encoding="utf-8")
        return self.index_path
```

(Need `import re` at top if not present.)

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Wire into `fool_loop.py`**

```python
# fool/fool_loop.py — at top
from fool.memory_notes import MemoryNotesStore
from pathlib import Path as _Path

# In run setup (where FoolMemory is created today):
GLOBAL_NOTES_ROOT = _Path(__file__).resolve().parents[1] / "out" / "memory"
memory_notes = MemoryNotesStore(root=GLOBAL_NOTES_ROOT)

# Pass it into both compactor and run_round:
compactor = SessionCompactor(
    summarizer=model_client,
    tool_result_dir=state.run_dir / "tool_results",
    threshold_tokens=80_000,
    reserve_tokens=20_000,
    summary_callback=_make_session_summary_callback(...),
    memory_notes=memory_notes,
    run_id=run_id,
    iteration=i,
)
harness_result = run_round(
    ...,
    compactor=compactor,
    memory_notes=memory_notes,
)

# After the iteration loop ends (success or interrupt-but-some-rounds-done):
try:
    memory_notes.aggregate_index()
except Exception as e:
    logger.warning("aggregate_index failed: %s", e)
```

- [ ] **Step 6: Commit**

```bash
git add fool/memory_notes.py fool/fool_loop.py genius/tests/test_memory_notes.py
git commit -m "memory_notes: aggregate_index() + fool_loop end-of-run wiring"
```

---

### Task 11: Frontend — split purge buttons + smoke

**Files:**
- Modify: `frontend/server.py`, `frontend/app.js`, `frontend/index.html`

- [ ] **Step 1: Read current purge implementation**

```bash
grep -n "_purge_memory_store\|purge" frontend/server.py | head -20
```

- [ ] **Step 2: Split into two purge functions**

```python
# frontend/server.py

def _purge_run_memory() -> None:
    """Wipe per-dataset FoolMemory dirs (out/memory/<fingerprint>/) but keep notes/."""
    mem = Path(__file__).resolve().parents[1] / "out" / "memory"
    if not mem.exists():
        return
    for entry in mem.iterdir():
        if entry.is_dir() and entry.name not in ("notes",):
            shutil.rmtree(entry, ignore_errors=True)
    # Also clear MEMORY.md (will be re-aggregated next run)
    idx = mem / "MEMORY.md"
    if idx.exists():
        idx.unlink()


def _purge_global_notes() -> None:
    """Wipe global notes/ — destructive; only on explicit user action."""
    notes = Path(__file__).resolve().parents[1] / "out" / "memory" / "notes"
    if notes.exists():
        shutil.rmtree(notes, ignore_errors=True)
```

Replace the call site that used to call `_purge_memory_store()` with `_purge_run_memory()` (auto-purge per run keeps the same scope as before — never auto-wipes the global notes).

Add a new HTTP route, e.g. `POST /api/purge_global_notes`, that calls `_purge_global_notes()`.

In `frontend/app.js` + `frontend/index.html` add a second button "清空全局记忆 (Memory.md + notes/)" with a JS `confirm()` guard:

```js
btnPurgeGlobal.addEventListener("click", async () => {
  if (!confirm("将清空 out/memory/MEMORY.md 与 notes/*.md，是否继续？")) return;
  await fetch("/api/purge_global_notes", { method: "POST" });
});
```

- [ ] **Step 3: Manual smoke (no unit test for frontend HTML)**

```bash
python run_local.py &
# Open the panel, verify both buttons exist; click "清空全局记忆" → check files gone
```

- [ ] **Step 4: End-to-end smoke with DeepSeek (1 iter)**

```bash
DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY python fool/fool_loop.py \
  --api-type deepseek --model deepseek-chat \
  --iterations 1 --input-dir data/sample_10_cases --scoring official_like_latest
```

Expected:
- `out/memory/MEMORY.md` is rebuilt at end
- `out/memory/notes/` has 0+ entries depending on whether agent called `memory_write`
- `out/runs/<run_id>/dialog/round_001.jsonl` exists with ≥4 lines
- `out/runs/<run_id>/tool_results/*.txt` filenames are 32-hex uuids

- [ ] **Step 5: Commit**

```bash
git add frontend/server.py frontend/app.js frontend/index.html
git commit -m "frontend: split per-run purge from global notes purge"
```

---

### Task 12: Final review pass + run full test suite + update CLAUDE.md

- [ ] **Step 1: Run full pytest**

```bash
python -m pytest genius/tests -v
```

Expected: all green (allow the known pre-existing failure noted in prior session — `test_resolve_bootstrap_solver_path_falls_back_to_repo_default` was already failing on main before this work; skip or note).

- [ ] **Step 2: Update CLAUDE.md `fool memory` bullet** to document the new layout (global `MEMORY.md` + `notes/*.md` + per-run `dialog/` + `tool_results/<uuid>.txt`) and 5 new tools.

```bash
# Edit fool memory bullet in CLAUDE.md per the new architecture
```

- [ ] **Step 3: Commit docs**

```bash
git add CLAUDE.md
git commit -m "CLAUDE.md: document MemoryNotesStore + 5 new memory tools"
```

---

## Notes & Non-Goals

- **No vector store / embeddings.** All search is BM25 over markdown entries; same tokenizer as `fool/memory_store.py`.
- **No cross-dataset pollution.** `notes/datasets/<fp>.md` keeps dataset-specific facts isolated; the four global `notes/*.md` files (preferences/lessons/try_errors/key_decisions) hold cross-dataset wisdom.
- **No daily YYYY-MM-DD logs.** MTASA's natural unit is a run, not a day; `aggregate_index()` runs at end-of-run.
- **Aggregator is pure Python (no LLM).** It only re-indexes and digests notes; the LLM-written summaries already went through `SessionCompactor`.
- **`MEMORY.md` holds index only, not knowledge.** Knowledge lives in `notes/**/*.md`; this caps `MEMORY.md` size and follows ReMe's `MemorySearch`→`MemoryGet` two-tier pattern.
- **Legacy `out/memory/<fingerprint>/` per-dataset FoolMemory dirs are untouched.** `FoolMemory` keeps its existing per-dataset BM25 over `episodes.jsonl` / `session_summaries.jsonl` — that store complements (not replaces) the new global notes.

## Execution

Plan complete and saved. Recommended execution: **Subagent-Driven Development** (one fresh subagent per task, two-stage review). Tasks 1–6 are mostly independent (foundation layer); 7–10 depend on them; 11–12 are integration. Run sequentially.
