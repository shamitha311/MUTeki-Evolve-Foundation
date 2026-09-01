"""V4A apply_patch envelope applier — adapted from the canonical
OpenAI/codex reference (`apply_patch.py`).

Strict line classification: every line inside a hunk must start with `+`
(add), `-` (delete), or ` ` (context). An empty patch line is normalized to
a single-space context line. Anything else raises DiffError.

Three-tier fuzz match (exact / rstrip / strip) — but **added lines are
written verbatim**: no indent rewriting on `+`. The model owns the
indentation it emits. This is the deliberate change from our previous
matcher; silent indent rewriting was the root cause of the corruption seen
in run_20260530_101603.

Single-file (draft.py) only. `*** Update File: draft.py` may be omitted in
single-hunk envelopes; if present, the path must be draft.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DRAFT_FILENAME = "draft.py"


class DiffError(ValueError):
    """Patch parse or match failure.

    When raised by hunk-matching, the optional fields carry just enough
    context for callers to build a diagnostic without re-parsing.
    """

    def __init__(
        self,
        message: str,
        *,
        context_snippet: Optional[list[str]] = None,
        search_start: int = 0,
    ) -> None:
        super().__init__(message)
        self.context_snippet = context_snippet
        self.search_start = search_start


@dataclass
class _Chunk:
    orig_index: int = -1
    del_lines: list[str] = field(default_factory=list)
    ins_lines: list[str] = field(default_factory=list)


@dataclass
class _Action:
    chunks: list[_Chunk] = field(default_factory=list)


_SECTION_MARKERS = (
    "@@",
    "*** End Patch",
    "*** Update File:",
    "*** Delete File:",
    "*** Add File:",
    "*** End of File",
)


def _find_context_core(
    lines: list[str], context: list[str], start: int
) -> "tuple[int, int]":
    """Return (first_index, fuzz). fuzz: 0 exact, 1 rstrip, 100 strip.
    Returns (-1, 0) if no match. Picks the first match (no ambiguity check —
    callers disambiguate via `@@ anchor` lines, as in the reference)."""
    if not context:
        return start, 0
    n = len(context)
    if start + n > len(lines):
        return -1, 0
    end = len(lines) - n
    # Exact
    for i in range(start, end + 1):
        if lines[i : i + n] == context:
            return i, 0
    # rstrip
    target = [s.rstrip() for s in context]
    for i in range(start, end + 1):
        if [s.rstrip() for s in lines[i : i + n]] == target:
            return i, 1
    # strip
    target = [s.strip() for s in context]
    for i in range(start, end + 1):
        if [s.strip() for s in lines[i : i + n]] == target:
            return i, 100
    return -1, 0


def _find_context(
    lines: list[str], context: list[str], start: int, eof: bool
) -> "tuple[int, int]":
    if eof:
        idx, fuzz = _find_context_core(
            lines, context, max(0, len(lines) - len(context))
        )
        if idx != -1:
            return idx, fuzz
        idx, fuzz = _find_context_core(lines, context, start)
        return idx, fuzz + 10_000 if idx != -1 else 0
    return _find_context_core(lines, context, start)


def _peek_next_section(
    lines: list[str], index: int
) -> "tuple[list[str], list[_Chunk], int, bool]":
    """Consume one hunk body from `lines[index:]`. Returns
    (old_lines, chunks, new_index, eof)."""
    old: list[str] = []
    del_lines: list[str] = []
    ins_lines: list[str] = []
    chunks: list[_Chunk] = []
    mode = "keep"
    orig_index = index
    while index < len(lines):
        s = lines[index]
        if s.startswith(_SECTION_MARKERS):
            break
        if s == "***":
            break
        if s.startswith("***"):
            raise DiffError(f"Invalid line in hunk: {s!r}")
        index += 1
        last_mode = mode
        if s == "":
            s = " "  # bare blank line == single-space context line
        c = s[0]
        if c == "+":
            mode = "add"
        elif c == "-":
            mode = "delete"
        elif c == " ":
            mode = "keep"
        else:
            raise DiffError(
                "Invalid line (every hunk line must start with '+', '-', or ' '): "
                f"{s!r}"
            )
        s = s[1:]
        if mode == "keep" and last_mode != mode:
            if ins_lines or del_lines:
                chunks.append(
                    _Chunk(
                        orig_index=len(old) - len(del_lines),
                        del_lines=del_lines,
                        ins_lines=ins_lines,
                    )
                )
            del_lines, ins_lines = [], []
        if mode == "delete":
            del_lines.append(s)
            old.append(s)
        elif mode == "add":
            ins_lines.append(s)
        else:  # keep
            old.append(s)
    if ins_lines or del_lines:
        chunks.append(
            _Chunk(
                orig_index=len(old) - len(del_lines),
                del_lines=del_lines,
                ins_lines=ins_lines,
            )
        )
    eof = False
    if index < len(lines) and lines[index] == "*** End of File":
        index += 1
        eof = True
    if index == orig_index:
        raise DiffError("Empty hunk")
    return old, chunks, index, eof


def _parse_update_file(
    patch_lines: list[str], patch_idx: int, text: str
) -> "tuple[_Action, int, int]":
    action = _Action()
    file_lines = text.split("\n")
    index = 0
    fuzz_total = 0
    first_chunk = True
    while patch_idx < len(patch_lines) and not patch_lines[patch_idx].startswith(
        (
            "*** End Patch",
            "*** Update File:",
            "*** Delete File:",
            "*** Add File:",
            "*** End of File",
        )
    ):
        # `@@ <def_str>` skip-ahead anchor (def_str empty for bare `@@`)
        raw = patch_lines[patch_idx]
        def_str = ""
        consumed_at = False
        if raw.startswith("@@ "):
            def_str = raw[3:]
            patch_idx += 1
            consumed_at = True
        elif raw == "@@":
            patch_idx += 1
            consumed_at = True
        if not consumed_at and not first_chunk:
            raise DiffError(f"Expected '@@' to start next hunk, got: {raw!r}")
        if def_str.strip():
            found = False
            if def_str not in file_lines[:index]:
                for i in range(index, len(file_lines)):
                    if file_lines[i] == def_str:
                        index = i + 1
                        found = True
                        break
            if not found and def_str.strip() not in [
                s.strip() for s in file_lines[:index]
            ]:
                for i in range(index, len(file_lines)):
                    if file_lines[i].strip() == def_str.strip():
                        index = i + 1
                        fuzz_total += 1
                        found = True
                        break
        context, chunks, next_idx, eof = _peek_next_section(patch_lines, patch_idx)
        new_index, fuzz = _find_context(file_lines, context, index, eof)
        if new_index == -1:
            raise DiffError(
                f"Hunk context not found in {DRAFT_FILENAME} "
                f"(searched from line {index + 1})",
                context_snippet=context,
                search_start=index,
            )
        fuzz_total += fuzz
        for ch in chunks:
            ch.orig_index += new_index
            action.chunks.append(ch)
        index = new_index + len(context)
        patch_idx = next_idx
        first_chunk = False
    return action, patch_idx, fuzz_total


def _apply_action(text: str, action: _Action) -> str:
    orig = text.split("\n")
    dest: list[str] = []
    orig_i = 0
    for ch in action.chunks:
        if ch.orig_index > len(orig):
            raise DiffError(
                f"chunk orig_index {ch.orig_index} exceeds file length {len(orig)}"
            )
        if orig_i > ch.orig_index:
            raise DiffError(
                f"overlapping chunks: orig_i {orig_i} > chunk.orig_index {ch.orig_index}"
            )
        dest.extend(orig[orig_i : ch.orig_index])
        dest.extend(ch.ins_lines)
        orig_i = ch.orig_index + len(ch.del_lines)
    dest.extend(orig[orig_i:])
    return "\n".join(dest)


def apply_patch_to_text(draft_text: str, patch_text: str) -> "tuple[str, int]":
    """Apply a V4A patch envelope to draft_text. Returns (new_text, fuzz).

    Raises DiffError on any parse / match failure. Atomic by construction:
    nothing is written until every hunk has located its context.
    """
    text = patch_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not lines[0].startswith("*** Begin Patch"):
        raise DiffError("envelope must start with '*** Begin Patch'")
    if lines[-1] != "*** End Patch":
        raise DiffError("envelope must end with '*** End Patch'")
    body = lines[1:-1]

    idx = 0
    if idx < len(body) and body[idx].startswith("*** Update File:"):
        target = body[idx].split(":", 1)[1].strip()
        if target != DRAFT_FILENAME:
            raise DiffError(f"only {DRAFT_FILENAME!r} is patchable, got {target!r}")
        idx += 1
    elif idx < len(body) and body[idx].startswith(
        ("*** Add File:", "*** Delete File:")
    ):
        raise DiffError("only 'Update File: draft.py' is supported")
    if idx >= len(body):
        raise DiffError("envelope contains no hunks")

    action, _final_idx, fuzz = _parse_update_file(body, idx, draft_text)
    if not action.chunks:
        raise DiffError("envelope contains no hunks")
    if not any(ch.del_lines or ch.ins_lines for ch in action.chunks):
        raise DiffError(
            "envelope has only context lines — there is nothing to change. "
            "Mark removed lines with a leading '-' and added lines with '+'."
        )
    return _apply_action(draft_text, action), fuzz
