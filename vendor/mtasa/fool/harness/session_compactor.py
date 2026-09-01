"""Session-level transcript compaction for the Fool harness.

Combines:
- tiktoken-based token counting
- a context-size splitter (ported from ReMe AsMsgHandler.context_check)
- tool-result truncation (ported from ReMe ToolResultCompactor)
- a single LLM call to compress old turns into a structured summary

No vector store, no embeddings — the resulting summary text is fed back
into BM25 via FoolMemory.record_session_summary().
"""

from __future__ import annotations

import logging
import re as _re
from typing import Any, Iterable

import tiktoken
import uuid
from pathlib import Path

from fool.harness.transcript_utils import truncate_text_output
from fool.harness.model_client import ModelClient
from fool.harness._compact_prompts import (
    INITIAL_USER_MESSAGE_ZH,
    SYSTEM_PROMPT_ZH,
    UPDATE_USER_MESSAGE_ZH,
)

logger = logging.getLogger(__name__)

_SECTION_RE = _re.compile(r"^##\s+(.+?)\s*$", _re.MULTILINE)


def _extract_sections(summary_md: str) -> dict[str, str]:
    """Parse '## Heading' blocks into {heading: body}."""
    out = {}
    parts = _SECTION_RE.split(summary_md)
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip()] = parts[i + 1].strip()
    return out


_MESSAGE_OVERHEAD_TOKENS = 4


class TiktokenCounter:
    """tiktoken-backed token counter.

    Uses cl100k_base (the encoding for GPT-4 / GPT-3.5-turbo / Claude proxies),
    which is a reasonable cross-model approximation. We do not need exact
    counts — only enough precision to decide when to compact.
    """

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self._enc = tiktoken.get_encoding(encoding_name)

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        return len(self._enc.encode(text, disallowed_special=()))

    def count_messages(self, messages: Iterable[dict[str, str]]) -> int:
        total = 0
        for msg in messages:
            total += _MESSAGE_OVERHEAD_TOKENS
            total += self.count_text(str(msg.get("content", "")))
            total += self.count_text(str(msg.get("role", "")))
        return total


def split_messages_for_compaction(
    messages: list[dict[str, str]],
    *,
    counter: TiktokenCounter,
    threshold_tokens: int,
    reserve_tokens: int,
    pinned_head_indices: tuple[int, ...] = (0, 1),
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split messages into (to_compact, to_keep).

    Behavior (ported from ReMe AsMsgHandler.context_check, simplified for
    MTASA's flat message format):

    - If total tokens < threshold_tokens: returns ([], messages).
    - Otherwise walks from newest backward, accumulating messages into
      to_keep while accumulated_tokens <= reserve_tokens.
    - The latest message is always kept, even if it alone exceeds reserve.
    - Messages at pinned_head_indices (default: system prefix at 0 and
      round-header at 1) are always kept regardless of size.
    - Final order in both buckets matches the original input order.

    No tool_use/tool_result id alignment is needed because MTASA messages
    are flat strings — tool results are already inlined into "user" messages
    by format_tool_user_message().
    """
    if not messages:
        return [], []

    assert threshold_tokens > reserve_tokens, (
        f"threshold ({threshold_tokens}) must exceed reserve ({reserve_tokens})"
    )

    total = counter.count_messages(messages)
    if total < threshold_tokens:
        return [], list(messages)

    per_msg_tokens = [
        _MESSAGE_OVERHEAD_TOKENS
        + counter.count_text(str(m.get("content", "")))
        + counter.count_text(str(m.get("role", "")))
        for m in messages
    ]

    keep_indices: set[int] = set()
    # Always keep pinned head indices (system prefix, round header).
    for idx in pinned_head_indices:
        if 0 <= idx < len(messages):
            keep_indices.add(idx)

    # Always keep the latest message, even if it alone exceeds reserve.
    last_idx = len(messages) - 1
    keep_indices.add(last_idx)
    accumulated = per_msg_tokens[last_idx] + sum(
        per_msg_tokens[i] for i in keep_indices if i != last_idx
    )

    # Walk backward from second-to-last, adding while under reserve.
    for i in range(last_idx - 1, -1, -1):
        if i in keep_indices:
            continue
        if accumulated + per_msg_tokens[i] > reserve_tokens:
            logger.info(
                "split_messages: stopping at idx %d; adding %d tokens would exceed reserve %d (current %d)",
                i, per_msg_tokens[i], reserve_tokens, accumulated,
            )
            break
        keep_indices.add(i)
        accumulated += per_msg_tokens[i]

    to_compact: list[dict[str, str]] = []
    to_keep: list[dict[str, str]] = []
    for idx, msg in enumerate(messages):
        if idx in keep_indices:
            to_keep.append(msg)
        else:
            to_compact.append(msg)

    logger.info(
        "split_messages: total=%d threshold=%d reserve=%d -> compact=%d keep=%d",
        total, threshold_tokens, reserve_tokens, len(to_compact), len(to_keep),
    )
    return to_compact, to_keep


def compact_tool_results(
    messages: list[dict[str, str]],
    *,
    tool_result_dir: Path,
    old_max_bytes: int = 3_000,
    recent_max_bytes: int = 100 * 1024,
    recent_n: int = 1,
    pinned_head_indices: tuple[int, ...] = (0, 1),
) -> list[dict[str, str]]:
    """Truncate large "user" messages in place; save full content to disk.

    MTASA tool results are inlined as "user" messages by
    format_tool_user_message(), so we only inspect role=="user" messages
    past the pinned head (system prefix + round header).

    Truncation policy:
    - The trailing run of consecutive user messages (at minimum recent_n)
      uses recent_max_bytes (keeps recent tool evidence rich).
    - Older user messages use old_max_bytes (aggressive compression).
    - Anything under the budget is left untouched.
    - Full original content is written to tool_result_dir/<uuid>.txt, and
      the truncated message embeds a continuation hint pointing to it.

    Returns a new list (does not mutate input).
    """
    if not messages:
        return list(messages)

    tool_result_dir.mkdir(parents=True, exist_ok=True)

    pinned = set(idx for idx in pinned_head_indices if 0 <= idx < len(messages))

    # Identify "recent" user messages: the last recent_n user messages by index.
    # Also include any trailing consecutive user messages at the end of the list.
    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    # Trailing consecutive user run (from the very end of the message list).
    trailing_user_run = 0
    for msg in reversed(messages):
        if msg.get("role") == "user":
            trailing_user_run += 1
        else:
            break
    # recent_n governs the minimum number of recent user messages to protect.
    # Combine: protect at least recent_n last user messages OR the trailing run.
    recent_user_count = max(trailing_user_run, recent_n)
    if recent_user_count > 0 and user_indices:
        # The split point is just before the Nth-from-last user message.
        recent_start_user_idx = user_indices[-recent_user_count] if recent_user_count <= len(user_indices) else user_indices[0]
        split_index = recent_start_user_idx
    else:
        split_index = len(messages)  # nothing is "recent"

    out: list[dict[str, str]] = []
    for idx, msg in enumerate(messages):
        if idx in pinned or msg.get("role") != "user":
            out.append(dict(msg))
            continue

        content = str(msg.get("content", ""))
        if not content:
            out.append(dict(msg))
            continue

        is_recent = idx >= split_index
        max_bytes = recent_max_bytes if is_recent else old_max_bytes

        if len(content.encode("utf-8")) <= max_bytes + 100:
            out.append(dict(msg))
            continue

        # Save full content to disk for the LLM to read back if needed.
        fp = tool_result_dir / f"{uuid.uuid4().hex}.txt"
        fp.write_text(content, encoding="utf-8")
        line_count = content.count("\n") + 1
        # Ensure total_lines >= 2 so _truncate_fresh always appends the notice.
        # (When a long single-line string is cut at max_bytes, next_line > 1
        # and the "start_line < total_lines" guard would silently drop the notice.)
        total_lines = max(line_count, 2)
        truncated = truncate_text_output(
            content,
            start_line=1,
            total_lines=total_lines,
            max_bytes=max_bytes,
            file_path=str(fp),
        )
        out.append({**msg, "content": truncated})

    return out


def _format_messages_for_compaction(messages: list[dict[str, str]]) -> str:
    """Render messages into a single string for the compaction prompt body."""
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = str(msg.get("content", ""))
        parts.append(f"--- {role} ---\n{content}")
    return "\n\n".join(parts)


class SessionCompactor:
    """Orchestrates per-round transcript compaction.

    Usage in runner:

        compactor = SessionCompactor(
            summarizer=model,                      # same ModelClient used for solving
            tool_result_dir=state.run_dir / "tool_results",
            threshold_tokens=80_000,
            reserve_tokens=20_000,
            summary_callback=memory.record_session_summary_callback(state),
        )
        previous_summary = ""
        while True:
            messages, previous_summary = compactor.maybe_compact(messages, previous_summary)
            raw = model.complete(messages, max_tokens)
            ...

    Idempotency: calling maybe_compact twice in a row is a no-op (the
    second call sees the already-shrunk transcript under threshold).
    """

    def __init__(
        self,
        *,
        summarizer: ModelClient,
        tool_result_dir: Path,
        threshold_tokens: int = 80_000,
        reserve_tokens: int = 20_000,
        summary_max_tokens: int = 2_000,
        counter: TiktokenCounter | None = None,
        summary_callback=None,
        pinned_head_indices: tuple[int, ...] = (0, 1),
        old_max_bytes: int = 3_000,
        recent_max_bytes: int = 100 * 1024,
        recent_n: int = 1,
        memory_notes: Any | None = None,   # NEW
        run_id: str = "",                  # NEW
        iteration: int = 0,               # NEW
    ) -> None:
        assert threshold_tokens > reserve_tokens
        self._summarizer = summarizer
        self._tool_result_dir = Path(tool_result_dir)
        self._threshold = threshold_tokens
        self._reserve = reserve_tokens
        self._summary_max_tokens = summary_max_tokens
        self._counter = counter or TiktokenCounter()
        self._summary_callback = summary_callback
        self._pinned = pinned_head_indices
        self._old_max_bytes = old_max_bytes
        self._recent_max_bytes = recent_max_bytes
        self._recent_n = recent_n
        self._memory_notes = memory_notes
        self._run_id = run_id
        self._iteration = iteration

    def maybe_compact(
        self,
        messages: list[dict[str, str]],
        previous_summary: str,
    ) -> tuple[list[dict[str, str]], str]:
        """Returns (possibly-shrunk messages, possibly-updated summary).

        The summary returned is the new authoritative summary to pass back
        on the next call (so the LLM sees a continuous compressed history).
        If no compaction happened, returns (messages, previous_summary) unchanged.
        """
        if not messages:
            return messages, previous_summary

        # Step 1: cheap byte-only tool-output truncation.
        messages = compact_tool_results(
            messages,
            tool_result_dir=self._tool_result_dir,
            old_max_bytes=self._old_max_bytes,
            recent_max_bytes=self._recent_max_bytes,
            recent_n=self._recent_n,
            pinned_head_indices=self._pinned,
        )

        # Step 2: token check.
        if self._counter.count_messages(messages) < self._threshold:
            return messages, previous_summary

        # Step 3: split.
        to_compact, to_keep = split_messages_for_compaction(
            messages,
            counter=self._counter,
            threshold_tokens=self._threshold,
            reserve_tokens=self._reserve,
            pinned_head_indices=self._pinned,
        )

        if not to_compact:
            # Nothing actually movable (everything is pinned or latest); bail.
            logger.warning("maybe_compact: over threshold but no movable messages")
            return messages, previous_summary

        # Step 4: build compaction prompt.
        conversation_text = _format_messages_for_compaction(to_compact)
        if previous_summary:
            user_body = (
                f"# conversation\n{conversation_text}\n\n"
                f"# previous-summary\n{previous_summary}\n\n"
                + UPDATE_USER_MESSAGE_ZH
            )
        else:
            user_body = (
                f"# conversation\n{conversation_text}\n\n" + INITIAL_USER_MESSAGE_ZH
            )

        prompt = [
            {"role": "system", "content": SYSTEM_PROMPT_ZH},
            {"role": "user", "content": user_body},
        ]

        # Step 5: LLM call.
        try:
            summary = self._summarizer.complete(prompt, self._summary_max_tokens).strip()
        except Exception:
            logger.exception("maybe_compact: summarizer.complete failed; returning uncompacted")
            return messages, previous_summary

        if not summary or "##" not in summary:
            logger.warning("maybe_compact: summarizer returned invalid summary; returning uncompacted")
            return messages, previous_summary

        # Step 5b: route key decisions section into MemoryNotesStore.
        if self._memory_notes is not None and self._run_id:
            sections = _extract_sections(summary)
            for heading_alias, our_section in (
                ("关键决策", "key_decision"),
                ("Key Decisions", "key_decision"),
            ):
                body = sections.get(heading_alias)
                if body:
                    try:
                        self._memory_notes.write_note(
                            section=our_section,
                            title=f"Round {self._iteration}: compactor-extracted decisions"[:80],
                            body=body.encode("utf-8")[:3500].decode("utf-8", errors="ignore"),
                            run_id=self._run_id,
                            iteration=self._iteration,
                        )
                    except ValueError as e:
                        logger.warning("note write skipped: %s", e)
                    break  # only write once per summary (Chinese OR English heading)

        # Step 6: rebuild message list.
        pinned_msgs = [messages[i] for i in self._pinned if 0 <= i < len(messages)]
        non_pinned_keep = [m for m in to_keep if m not in pinned_msgs]
        synthetic_summary_msg = {
            "role": "user",
            "content": f"[Prior context summary]\n{summary}",
        }
        new_messages = [*pinned_msgs, synthetic_summary_msg, *non_pinned_keep]

        # Step 7: callback for persistence.
        if self._summary_callback is not None:
            try:
                self._summary_callback(summary, previous_summary)
            except Exception:
                logger.exception("maybe_compact: summary_callback raised; ignoring")

        return new_messages, summary
