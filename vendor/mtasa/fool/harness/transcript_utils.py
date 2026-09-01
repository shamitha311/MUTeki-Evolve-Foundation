"""Byte-bounded text truncation with line integrity.

Ported from ReMe (reme/memory/file_based/utils/file_utils.py).
Pure stdlib; no external dependencies.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 50 * 1024

TRUNCATION_NOTICE_MARKER = "<<<TRUNCATED>>>"


def _truncate_fresh(
    text: str,
    start_line: int,
    total_lines: int,
    max_bytes: int,
    file_path: str | None,
    encoding: str,
) -> str:
    text_bytes = text.encode(encoding)
    if len(text_bytes) <= max_bytes:
        return text

    truncated = text_bytes[:max_bytes]
    result = truncated.decode(encoding, errors="ignore")
    newline_count = result.count("\n")
    next_line = start_line + max(1, newline_count)

    if next_line <= total_lines:
        read_from = next_line
    elif start_line < total_lines:
        read_from = total_lines
    else:
        return result

    notice = (
        TRUNCATION_NOTICE_MARKER
        + f"\nThe output above was truncated."
        f"\nThe full content is saved to the file and contains {total_lines} lines in total."
        f"\nThis excerpt starts at line {start_line} and covers the next {max_bytes} bytes."
        f"\nIf the current content is not enough, call `read_file` with file_path={file_path or ''} "
        f"start_line={read_from} to read more."
    )

    return result + notice


def _retruncate(text: str, max_bytes: int, encoding: str) -> str:
    parts = text.split(TRUNCATION_NOTICE_MARKER, 1)
    original_content = parts[0]
    old_notice = parts[1]

    text_bytes = original_content.encode(encoding)
    if len(text_bytes) <= max_bytes + 100:
        return text

    start_match = re.search(r"starts at line (\d+)", old_notice)
    if not start_match:
        return text
    start_line_parsed = int(start_match.group(1))

    truncated_bytes = text_bytes[:max_bytes]
    result = truncated_bytes.decode(encoding, errors="ignore")
    newline_count = result.count("\n")
    next_line = start_line_parsed + max(1, newline_count)

    if not re.search(r"covers the next \d+ bytes", old_notice):
        return text
    new_notice = re.sub(r"covers the next \d+ bytes", f"covers the next {max_bytes} bytes", old_notice)
    new_notice = re.sub(r"start_line=\d+ to read more", f"start_line={next_line} to read more", new_notice)

    return result + TRUNCATION_NOTICE_MARKER + new_notice


def truncate_text_output(
    text: str,
    start_line: int = 1,
    total_lines: int = 0,
    max_bytes: int = DEFAULT_MAX_BYTES,
    file_path: str | None = None,
    encoding: str = "utf-8",
) -> str:
    """Truncate output by bytes with line integrity, leaving a continuation hint."""
    if not text:
        return text
    if max_bytes <= 0:
        return text

    try:
        if TRUNCATION_NOTICE_MARKER in text:
            return _retruncate(text, max_bytes=max_bytes, encoding=encoding)
        return _truncate_fresh(
            text,
            start_line=start_line,
            total_lines=total_lines or (text.count("\n") + 1),
            max_bytes=max_bytes,
            file_path=file_path,
            encoding=encoding,
        )
    except Exception:
        logger.warning("truncate_text_output failed, returning original text", exc_info=True)
        return text
