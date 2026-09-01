from __future__ import annotations

import math
import re as _re
from datetime import datetime, timezone
from pathlib import Path

from fool.memory_store import _token_list  # reuse tokenizer


# Section taxonomy. Files are named "<section>_<slug>.md" under notes/.
SECTION_FILES: dict[str, str | None] = {
    "preference": None,
    "lesson": None,
    "try_error": None,
    "key_decision": None,
}

SECTION_HEADINGS = [
    ("preference", "Active Preferences"),
    ("lesson", "Recent Lessons"),
    ("try_error", "Recent Try-Errors"),
    ("key_decision", "Recent Decisions"),
]

INDEX_FILE = "MEMORY.md"

MAX_TITLE_LEN = 80
MAX_DESCRIPTION_LEN = 200
MAX_BODY_BYTES = 4 * 1024

DEFAULT_GET_MAX_LINES = 800

MIN_CONFIDENCE = 0.05
DEFAULT_CONFIDENCE = 1.0

_FRONTMATTER_RE = _re.compile(r"^---\n(.*?)\n---\n", _re.DOTALL)
_SCOPE_RE = _re.compile(r"^scope:\s*(.+)$", _re.MULTILINE)
_FALSIFIES_RE = _re.compile(r"^falsifies:\s*(.+)$", _re.MULTILINE)


def _slugify(title: str, max_len: int = 60) -> str:
    """Filesystem-safe slug. Keeps unicode word chars (incl. CJK)."""
    s = title.strip().lower()
    s = _re.sub(r"[/\\:\*\?\"<>\|]", "", s)
    s = _re.sub(r"[^\w一-鿿\s\-]", "", s, flags=_re.UNICODE)
    s = _re.sub(r"\s+", "_", s)
    s = _re.sub(r"_+", "_", s)
    s = s.strip("_-")
    return (s[:max_len] or "note").rstrip("_-") or "note"


def _yaml_escape(value: str) -> str:
    v = value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ")
    return f"\"{v}\""


def _yaml_unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("\"", "'"):
        inner = v[1:-1]
        if v[0] == "\"":
            inner = inner.replace("\\\"", "\"").replace("\\\\", "\\")
        return inner
    return v


def _render_frontmatter(meta: dict) -> str:
    m = meta["metadata"]
    lines = [
        "---",
        f"name: {meta['name']}",
        f"title: {_yaml_escape(meta['title'])}",
        f"description: {_yaml_escape(meta['description'])}",
        "metadata:",
        f"  type: {m['type']}",
        f"  run_id: {m['run_id']}",
        f"  iteration: {m['iteration']}",
        f"  ts: {m['ts']}",
        f"  confidence: {float(m['confidence']):.2f}",
    ]
    if m.get("dataset_fp"):
        lines.append(f"  dataset_fp: {m['dataset_fp']}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text). Empty dict if no frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    fm: dict = {}
    metadata: dict = {}
    in_metadata = False
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("metadata:"):
            in_metadata = True
            continue
        if in_metadata and (line.startswith("  ") or line.startswith("\t")):
            k, _, v = line.strip().partition(":")
            metadata[k.strip()] = _yaml_unquote(v.strip())
            continue
        in_metadata = False
        k, _, v = line.partition(":")
        fm[k.strip()] = _yaml_unquote(v.strip())
    if metadata:
        fm["metadata"] = metadata
    return fm, body


def _file_section(path: Path, store: "MemoryNotesStore") -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    fm, _ = _parse_frontmatter(text)
    meta = fm.get("metadata") or {}
    sec = meta.get("type") or ""
    if sec in SECTION_FILES:
        return sec
    name = path.name
    for s in SECTION_FILES:
        if name.startswith(s + "_"):
            return s
    return "unknown"


def _iter_note_files(store: "MemoryNotesStore", sections: list[str] | None):
    if not sections or "all" in sections:
        yield from store.notes_dir.rglob("*.md")
        return
    want = set(sections)
    for path in store.notes_dir.rglob("*.md"):
        if _file_section(path, store) in want:
            yield path


def _derive_description(body: str, fallback_title: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("<!--") and not line.startswith("#"):
            return line[:MAX_DESCRIPTION_LEN]
    return fallback_title[:MAX_DESCRIPTION_LEN]


class MemoryNotesStore:
    """Global markdown knowledge base under out/memory/.

    One file per note. Filename = ``<section>_<slug>.md`` under ``notes/``.
    Each file starts with a YAML frontmatter block (name, title, description,
    metadata.{type,run_id,iteration,ts,confidence}). ``MEMORY.md`` is a flat
    per-section index with one line per note:
    ``- [Title](relative/path.md) — description``.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.notes_dir = self.root / "notes"
        self.index_path = self.root / INDEX_FILE
        self.root.mkdir(parents=True, exist_ok=True)
        self.notes_dir.mkdir(parents=True, exist_ok=True)

    # --- helpers ----------------------------------------------------------

    def _unique_path(self, base: Path) -> Path:
        if not base.exists():
            return base
        stem, suffix = base.stem, base.suffix
        parent = base.parent
        i = 2
        while True:
            cand = parent / f"{stem}_{i}{suffix}"
            if not cand.exists():
                return cand
            i += 1

    # --- public API -------------------------------------------------------

    def write_note(
        self,
        *,
        section: str,
        title: str,
        body: str,
        run_id: str,
        iteration: int,
        dataset_fp: str | None = None,
        confidence: float = DEFAULT_CONFIDENCE,
        description: str | None = None,
    ) -> Path:
        """Create one .md file per note and return its path."""
        if section not in SECTION_FILES:
            raise ValueError(f"unknown section: {section!r}")
        if len(title) > MAX_TITLE_LEN:
            raise ValueError(f"title too long: {len(title)} > {MAX_TITLE_LEN}")
        if len(body.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError(
                f"body too large: {len(body.encode('utf-8'))} > {MAX_BODY_BYTES}"
            )
        slug = _slugify(title)
        base = self.notes_dir / f"{section}_{slug}.md"
        name = f"{section}-{slug}"
        target = self._unique_path(base)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conf = max(MIN_CONFIDENCE, min(1.0, float(confidence)))
        desc = (description or _derive_description(body, title)).strip()
        desc = desc[:MAX_DESCRIPTION_LEN]

        metadata = {
            "type": section,
            "run_id": run_id,
            "iteration": int(iteration),
            "ts": ts,
            "confidence": conf,
        }
        if dataset_fp:
            metadata["dataset_fp"] = dataset_fp

        frontmatter = _render_frontmatter({
            "name": name,
            "title": title.strip(),
            "description": desc,
            "metadata": metadata,
        })
        target.write_text(
            frontmatter + "\n" + body.strip() + "\n", encoding="utf-8"
        )
        return target

    def append_evidence(
        self,
        *,
        path: Path,
        evidence: str,
        run_id: str,
        iteration: int,
        anchor_line: int | None = None,
    ) -> int:
        """Append a ``<!-- confirmed-by ... -->`` line to a note file.

        With per-file notes, ``anchor_line`` is accepted but ignored — the
        path already identifies the entry. Returns the 1-indexed line where
        the evidence was inserted.
        """
        path = Path(path).resolve()
        notes_root = self.notes_dir.resolve()
        try:
            path.relative_to(notes_root)
        except ValueError as exc:
            raise ValueError(f"path must be under {notes_root}: {path}") from exc
        if not path.exists():
            raise ValueError(f"path does not exist: {path}")

        ev = evidence.strip()
        if not ev:
            raise ValueError("evidence is empty")
        if len(ev.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError(
                f"evidence too large: {len(ev.encode('utf-8'))} > {MAX_BODY_BYTES}"
            )
        if "\n" in ev:
            ev = ev.replace("\n", " ").strip()

        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        insert_at = len(lines)
        while insert_at > 0 and lines[insert_at - 1].strip() == "":
            insert_at -= 1

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        evidence_line = (
            f"<!-- confirmed-by run_id={run_id} iteration={iteration} ts={ts} --> {ev}"
        )
        new_lines = lines[:insert_at] + [evidence_line] + lines[insert_at:]
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return insert_at + 1

    def search(
        self,
        query: str,
        *,
        sections: list[str] | None = None,
        max_results: int = 5,
        max_snippet_chars: int = 400,
    ) -> list[dict]:
        q_tokens = set(_token_list(query))
        if not q_tokens:
            return []
        snip_cap = max(100, min(int(max_snippet_chars), 2000))
        scored: list[tuple] = []
        for path in _iter_note_files(self, sections):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, body = _parse_frontmatter(text)
            title_for_search = fm.get("title", "")
            description_for_search = fm.get("description", "")
            search_text = f"{title_for_search}\n{description_for_search}\n{body or text}"
            tokens = _token_list(search_text)
            if not tokens:
                continue
            hits = sum(1 for t in tokens if t in q_tokens)
            if hits == 0:
                continue
            bm25 = hits / math.sqrt(len(tokens))
            meta = fm.get("metadata") or {}
            try:
                confidence = float(meta.get("confidence", DEFAULT_CONFIDENCE))
            except (TypeError, ValueError):
                confidence = DEFAULT_CONFIDENCE
            final = bm25 * max(MIN_CONFIDENCE, confidence)
            section = meta.get("type") or _file_section(path, self)
            title = fm.get("title", path.stem)
            description = fm.get("description", "")
            scope_m = _SCOPE_RE.search(body or "")
            falsifies_m = _FALSIFIES_RE.search(body or "")
            total_lines = len(text.splitlines())
            snippet_parts = []
            if title_for_search:
                snippet_parts.append(f"# {title_for_search}")
            if description_for_search:
                snippet_parts.append(description_for_search)
            snippet_parts.append((body or text).strip())
            snippet = "\n".join(snippet_parts)[:snip_cap]
            scored.append((
                final, section, str(path), 1, total_lines, snippet,
                confidence, title, description,
                scope_m.group(1).strip() if scope_m else "",
                falsifies_m.group(1).strip() if falsifies_m else "",
            ))
        scored.sort(key=lambda x: -x[0])
        out: list[dict] = []
        for (s, sec, p, sl, el, sn, conf, title, desc, scope, falsifies) in scored[:max_results]:
            row = {
                "path": p,
                "start_line": sl,
                "end_line": el,
                "score": round(s, 4),
                "snippet": sn,
                "section": sec,
                "confidence": round(conf, 2),
                "title": title,
                "description": desc,
            }
            if scope:
                row["scope"] = scope
            if falsifies:
                row["falsifies"] = falsifies
            out.append(row)
        return out

    def decay_confidence(
        self,
        *,
        query: str,
        sections: list[str] | None = None,
        factor: float = 0.5,
        max_entries: int = 5,
    ) -> list[dict]:
        """Multiply matching notes' confidence by ``factor`` in frontmatter."""
        if not (0.0 < factor < 1.0):
            return []
        hits = self.search(
            query=query,
            sections=sections or ["try_error"],
            max_results=max_entries,
        )
        changes: list[dict] = []
        for h in hits:
            path = Path(h["path"])
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, body = _parse_frontmatter(text)
            meta = dict(fm.get("metadata") or {})
            try:
                old = float(meta.get("confidence", DEFAULT_CONFIDENCE))
            except (TypeError, ValueError):
                old = DEFAULT_CONFIDENCE
            new = max(MIN_CONFIDENCE, old * factor)
            meta["confidence"] = new
            new_fm = _render_frontmatter({
                "name": fm.get("name", path.stem),
                "title": fm.get("title", path.stem),
                "description": fm.get("description", ""),
                "metadata": meta,
            })
            path.write_text(new_fm + "\n" + body.lstrip("\n"), encoding="utf-8")
            changes.append({
                "path": str(path),
                "start_line": 1,
                "old": round(old, 2),
                "new": round(new, 2),
            })
        return changes

    def aggregate_index(self, *, top_n_per_section: int = 5) -> Path:
        """Rebuild MEMORY.md as ``- [title](path) — description`` lines."""
        out: list[str] = ["# MTASA Memory Index"]
        out.append(
            f"> Last aggregated: "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )

        buckets: dict[str, list[tuple[str, str, str, Path]]] = {
            s: [] for s, _ in SECTION_HEADINGS
        }
        for path in self.notes_dir.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm, _ = _parse_frontmatter(text)
            if not fm:
                continue
            meta = fm.get("metadata") or {}
            section = meta.get("type") or _file_section(path, self)
            title = fm.get("title", path.stem)
            description = fm.get("description", "")
            ts = meta.get("ts", "")
            entry = (ts, title, description, path)
            if section in buckets:
                buckets[section].append(entry)

        for section, heading in SECTION_HEADINGS:
            entries = buckets.get(section) or []
            if not entries:
                continue
            entries.sort(key=lambda x: x[0], reverse=True)
            show_all = section in ("lesson", "try_error")
            limit = len(entries) if show_all else min(top_n_per_section, len(entries))
            suffix = "all" if show_all else f"top {limit}"
            out.append(f"\n## {heading} ({suffix})")
            for _ts, title, desc, path in entries[:limit]:
                rel = path.relative_to(self.root).as_posix()
                line = f"- [{title}]({rel})"
                if desc:
                    line += f" — {desc}"
                out.append(line)

        text = "\n".join(out) + "\n"
        self.index_path.write_text(text, encoding="utf-8")
        return self.index_path

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
