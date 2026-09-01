"""SEARCH/REPLACE block applier — simpler alternative to V4A apply_patch.

Format (per block, fenced inside `<blocks>...</blocks>` body):

    <<<<<<< SEARCH
    old text (any number of lines, copied verbatim from draft)
    =======
    new text
    >>>>>>> REPLACE

Multiple blocks may appear back-to-back. Applied in order, atomically:
on any failure, the whole batch is rejected and the draft is left unchanged.

Matching strategy (ported from aider's editblock_coder, stdlib only):

1. perfect line-tuple match
2. uniform-leading-whitespace match (the model elided/added a common indent)
3. ``...`` ellipsis match (block has gaps marked by a single-dot line)

No SequenceMatcher fuzz — failures must be exact-or-whitespace fixable.

Single-file (``draft.py``) only; no filename header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


DRAFT_FILENAME = "draft.py"

_HEAD_RE = re.compile(r"^<{5,9} SEARCH\s*$")
_DIVIDER_RE = re.compile(r"^={5,9}\s*$")
_TAIL_RE = re.compile(r"^>{5,9} REPLACE\s*$")


class BlockPatchError(ValueError):
    """Parse or match failure for a SEARCH/REPLACE batch."""

    def __init__(
        self,
        msg: str,
        *,
        block_index: int | None = None,
        search: str | None = None,
        nearest: str | None = None,
    ) -> None:
        super().__init__(msg)
        self.block_index = block_index
        self.search = search
        self.nearest = nearest


@dataclass(frozen=True)
class Block:
    search: str
    replace: str


def parse_blocks(envelope: str) -> list[Block]:
    """Extract Block(search, replace) pairs from an envelope.

    Tolerates leading/trailing whitespace and an optional outer code fence
    (``` ... ```). Raises BlockPatchError on malformed structure.
    """
    text = (envelope or "").strip()
    if not text:
        raise BlockPatchError("empty blocks envelope")

    lines = text.splitlines()

    # Strip a single outer ``` fence pair if present.
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]

    blocks: list[Block] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if not _HEAD_RE.match(line.rstrip()):
            raise BlockPatchError(
                f"expected '<<<<<<< SEARCH' marker, got line {i + 1}: {line!r}"
            )
        i += 1
        search_lines: list[str] = []
        while i < n and not _DIVIDER_RE.match(lines[i].rstrip()):
            search_lines.append(lines[i])
            i += 1
        if i >= n:
            raise BlockPatchError(
                f"block {len(blocks) + 1}: missing '=======' divider"
            )
        i += 1
        replace_lines: list[str] = []
        while i < n and not _TAIL_RE.match(lines[i].rstrip()):
            replace_lines.append(lines[i])
            i += 1
        if i >= n:
            raise BlockPatchError(
                f"block {len(blocks) + 1}: missing '>>>>>>> REPLACE' marker"
            )
        i += 1
        blocks.append(
            Block(
                search="\n".join(search_lines),
                replace="\n".join(replace_lines),
            )
        )

    if not blocks:
        raise BlockPatchError("envelope contained no SEARCH/REPLACE blocks")
    return blocks


def apply_blocks_to_text(original: str, envelope: str) -> tuple[str, int]:
    """Apply all blocks in ``envelope`` to ``original``.

    Returns (new_text, fuzz_count). Raises BlockPatchError if any block
    fails to match — the partial result is discarded by the caller.
    """
    blocks = parse_blocks(envelope)
    current = original
    fuzz = 0
    for idx, block in enumerate(blocks, start=1):
        new_text, used_fuzz = _apply_one(current, block)
        if new_text is None:
            nearest = _find_similar_chunk(block.search, current)
            raise BlockPatchError(
                _format_no_match(idx, block.search, nearest),
                block_index=idx,
                search=block.search,
                nearest=nearest,
            )
        current = new_text
        fuzz += used_fuzz
    return current, fuzz


# --- matching primitives ---------------------------------------------------


def _prep(text: str) -> tuple[str, list[str]]:
    if text and not text.endswith("\n"):
        text += "\n"
    return text, text.splitlines(keepends=True)


def _apply_one(whole: str, block: Block) -> tuple[str | None, int]:
    """Return (new_text, fuzz_level) or (None, 0) if no match found.

    fuzz_level: 0 perfect, 1 whitespace-flex, 2 ellipsis.
    Empty SEARCH → append REPLACE to end (creates if file empty).
    """
    if not block.search.strip():
        whole_norm = whole if not whole or whole.endswith("\n") else whole + "\n"
        new = whole_norm + (
            block.replace if block.replace.endswith("\n") or not block.replace
            else block.replace + "\n"
        )
        return new, 0

    whole_n, whole_lines = _prep(whole)
    search_n, search_lines = _prep(block.search)
    replace_n, replace_lines = _prep(block.replace)

    res = _perfect_replace(whole_lines, search_lines, replace_lines)
    if res is not None:
        return res, 0

    res = _whitespace_flex_replace(whole_lines, search_lines, replace_lines)
    if res is not None:
        return res, 1

    # Drop a spuriously-added leading blank line in SEARCH.
    if len(search_lines) > 2 and not search_lines[0].strip():
        trimmed = search_lines[1:]
        res = _perfect_replace(whole_lines, trimmed, replace_lines)
        if res is not None:
            return res, 1
        res = _whitespace_flex_replace(whole_lines, trimmed, replace_lines)
        if res is not None:
            return res, 1

    try:
        res = _try_dotdotdots(whole_n, search_n, replace_n)
        if res is not None:
            return res, 2
    except BlockPatchError:
        # Re-raise as match failure with sentinel: caller will produce
        # the standard "no match" diagnostic.
        return None, 0

    return None, 0


def _perfect_replace(
    whole_lines: list[str], part_lines: list[str], replace_lines: list[str]
) -> str | None:
    part_tup = tuple(part_lines)
    part_len = len(part_lines)
    if part_len == 0:
        return None
    for i in range(len(whole_lines) - part_len + 1):
        if tuple(whole_lines[i : i + part_len]) == part_tup:
            return "".join(whole_lines[:i] + replace_lines + whole_lines[i + part_len :])
    return None


def _whitespace_flex_replace(
    whole_lines: list[str], part_lines: list[str], replace_lines: list[str]
) -> str | None:
    # Outdent part_lines and replace_lines by the largest common leading-ws drop.
    leading = [
        len(p) - len(p.lstrip()) for p in part_lines if p.strip()
    ] + [
        len(p) - len(p.lstrip()) for p in replace_lines if p.strip()
    ]
    if leading and min(leading):
        n = min(leading)
        part_lines = [p[n:] if p.strip() else p for p in part_lines]
        replace_lines = [p[n:] if p.strip() else p for p in replace_lines]

    num = len(part_lines)
    if num == 0:
        return None
    for i in range(len(whole_lines) - num + 1):
        add = _match_but_for_leading_ws(whole_lines[i : i + num], part_lines)
        if add is None:
            continue
        adjusted = [add + r if r.strip() else r for r in replace_lines]
        return "".join(whole_lines[:i] + adjusted + whole_lines[i + num :])
    return None


def _match_but_for_leading_ws(
    whole_chunk: list[str], part_lines: list[str]
) -> str | None:
    if not all(
        whole_chunk[i].lstrip() == part_lines[i].lstrip() for i in range(len(whole_chunk))
    ):
        return None
    adds = {
        whole_chunk[i][: len(whole_chunk[i]) - len(part_lines[i])]
        for i in range(len(whole_chunk))
        if whole_chunk[i].strip()
    }
    if len(adds) != 1:
        return None
    return adds.pop()


_DOTS_RE = re.compile(r"(^\s*\.\.\.\n)", re.MULTILINE)


def _try_dotdotdots(whole: str, part: str, replace: str) -> str | None:
    part_pieces = re.split(_DOTS_RE, part)
    replace_pieces = re.split(_DOTS_RE, replace)
    if len(part_pieces) != len(replace_pieces):
        raise BlockPatchError("unpaired '...' in SEARCH/REPLACE block")
    if len(part_pieces) == 1:
        return None
    if not all(
        part_pieces[i] == replace_pieces[i] for i in range(1, len(part_pieces), 2)
    ):
        raise BlockPatchError("'...' markers must align between SEARCH and REPLACE")

    part_chunks = [part_pieces[i] for i in range(0, len(part_pieces), 2)]
    repl_chunks = [replace_pieces[i] for i in range(0, len(replace_pieces), 2)]

    out = whole
    for p, r in zip(part_chunks, repl_chunks):
        if not p and not r:
            continue
        if not p:
            if not out.endswith("\n"):
                out += "\n"
            out += r
            continue
        cnt = out.count(p)
        if cnt == 0:
            return None
        if cnt > 1:
            raise BlockPatchError(
                "ambiguous '...' chunk matched multiple times — disambiguate the SEARCH"
            )
        out = out.replace(p, r, 1)
    return out


# --- diagnostics -----------------------------------------------------------


def _find_similar_chunk(search: str, content: str, threshold: float = 0.6) -> str:
    """Return a representative slice of ``content`` that looks like ``search``.

    Stdlib SequenceMatcher; result is informational only. Empty string if no
    chunk passes the threshold.
    """
    from difflib import SequenceMatcher

    s_lines = search.splitlines()
    c_lines = content.splitlines()
    if not s_lines or len(c_lines) < len(s_lines):
        return ""

    best_ratio = 0.0
    best_i = -1
    span = len(s_lines)
    for i in range(len(c_lines) - span + 1):
        chunk = c_lines[i : i + span]
        ratio = SequenceMatcher(None, s_lines, chunk).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_i = i
    if best_ratio < threshold or best_i < 0:
        return ""
    pad = 2
    lo = max(0, best_i - pad)
    hi = min(len(c_lines), best_i + span + pad)
    return "\n".join(c_lines[lo:hi])


def _format_no_match(idx: int, search: str, nearest: str) -> str:
    rows = [
        f"block {idx}: SEARCH text did not match draft.py.",
        "the SEARCH section must reproduce existing draft lines exactly "
        "(or modulo uniform leading whitespace).",
        "----- SEARCH (this block) -----",
        search.rstrip("\n"),
        "-------------------------------",
    ]
    if nearest:
        rows.append("nearest similar slice of draft.py:")
        rows.append(nearest)
    rows.append(
        "tip: call read_current_draft, copy the target lines byte-for-byte "
        "into SEARCH, and re-send only the failing block."
    )
    return "\n".join(rows)
