# Fool Session Compactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port ReMe's "long-context compression + cross-session memory" capability into MTASA's Fool harness — using `tiktoken` for token counting, an LLM call for summarization, and ReMe's proven prompts — while keeping `fool/memory_store.py`'s BM25 retrieval untouched and adding no vector/embedding dependencies.

**Architecture:** Three new modules under `fool/harness/`: (1) `transcript_utils.py` ports ReMe's byte-level text truncation primitives (pure stdlib); (2) `_compact_prompts.py` holds ReMe's Chinese compaction prompt templates as Python constants; (3) `session_compactor.py` ties together a tiktoken-based token counter, a context-check splitter (ported from `AsMsgHandler.context_check`), tool-output truncation, and a one-shot LLM compaction call. `runner.py` invokes `SessionCompactor.maybe_compact()` before each `model.complete()` call. After a round ends, the produced summary is also persisted via a new `FoolMemory.record_session_summary()` method so it becomes BM25-retrievable next round.

**Tech Stack:** Python 3.12, `tiktoken` (new dep), `fool/llm_client.py` (existing), pytest. No agentscope, no embeddings, no vector store.

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `requirements.txt` (or `pyproject.toml` if exists) | Modify | Add `tiktoken>=0.7` |
| `fool/harness/transcript_utils.py` | Create | Byte-bounded text truncation with line integrity + `<<<TRUNCATED>>>` marker; ported from ReMe `file_utils.py` |
| `fool/harness/_compact_prompts.py` | Create | Chinese system + user prompts for compaction (ported from ReMe `compactor.yaml`) |
| `fool/harness/session_compactor.py` | Create | `TiktokenCounter`, `split_messages_for_compaction()`, `compact_tool_results()`, `SessionCompactor.maybe_compact()` |
| `fool/harness/runner.py` | Modify (line 119–145 region) | Construct `SessionCompactor` once per round; call `compactor.maybe_compact(messages)` before `model.complete()` |
| `fool/memory_store.py` | Modify (after line 547 `record_run_lesson`) | Add `record_session_summary(run_id, iteration, summary_text, target_buckets)` that appends to a new `session_summaries.jsonl` and feeds it into `retrieve()` |
| `genius/tests/test_transcript_utils.py` | Create | Truncation primitive tests |
| `genius/tests/test_session_compactor.py` | Create | Token counter, splitter, end-to-end compactor tests (with a fake LLM client) |
| `genius/tests/test_fool_memory.py` | Modify | Add tests for `record_session_summary` + its appearance in `retrieve()` output |
| `genius/tests/test_harness_runner.py` | Modify | Add test that long transcripts trigger compaction and short ones don't |

**Why this split:** `transcript_utils.py` is dependency-free pure text logic → trivially testable. `_compact_prompts.py` holds only strings → no logic. `session_compactor.py` is the only file that talks to both `tiktoken` and `llm_client` → single chokepoint for the new dependency surface. `runner.py` and `memory_store.py` get minimal diffs (one new import + one new method each).

**Message format reminder:** MTASA harness messages are flat `{"role": "system"|"user"|"assistant", "content": str}` dicts. Tool results are already flattened into `"user"` messages by `format_tool_user_message()` — there are **no** structured `tool_use`/`tool_result` blocks like in agentscope. This means we **skip** ReMe's tool-id-alignment logic (much simpler).

---

## Task 1: Add tiktoken dependency

**Files:**
- Modify: `requirements.txt` (verify path first; if missing, the project has no central pin file and we install ad-hoc)

- [ ] **Step 1: Check for a requirements file**

Run:
```bash
ls /Users/zhuym/Documents/101camp/MTASA/requirements.txt /Users/zhuym/Documents/101camp/MTASA/pyproject.toml 2>&1
```

Expected: one of them exists. If `requirements.txt` exists, modify it. If only `pyproject.toml`, add tiktoken under `[project] dependencies`. If neither, create `requirements.txt` containing only `tiktoken>=0.7`.

- [ ] **Step 2: Install tiktoken into the venv**

Run:
```bash
/Users/zhuym/Documents/101camp/MTASA/.venv/bin/pip install "tiktoken>=0.7"
```

Expected: `Successfully installed tiktoken-…`

- [ ] **Step 3: Verify import**

Run:
```bash
/Users/zhuym/Documents/101camp/MTASA/.venv/bin/python -c "import tiktoken; enc=tiktoken.get_encoding('cl100k_base'); print(len(enc.encode('hello 世界')))"
```

Expected: prints a small integer (around `5`).

- [ ] **Step 4: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add requirements.txt    # or pyproject.toml
git commit -m "deps: add tiktoken for fool session compactor"
```

---

## Task 2: Port `transcript_utils.py` (text truncation primitives)

**Files:**
- Create: `fool/harness/transcript_utils.py`
- Test: `genius/tests/test_transcript_utils.py`

This ports ReMe `/Users/zhuym/Documents/101camp/ReMe/reme/memory/file_based/utils/file_utils.py` with the only changes being (a) `from ....core.utils import get_logger` → `import logging; logger = logging.getLogger(__name__)`, and (b) we drop `read_file_safe()` (Fool doesn't need it).

- [ ] **Step 1: Write the failing tests**

Create `genius/tests/test_transcript_utils.py`:

```python
from fool.harness.transcript_utils import (
    DEFAULT_MAX_BYTES,
    TRUNCATION_NOTICE_MARKER,
    truncate_text_output,
)


def test_short_text_returned_unchanged():
    text = "hello\nworld\n"
    assert truncate_text_output(text, total_lines=2, max_bytes=1000) == text


def test_long_text_is_truncated_with_marker():
    text = "\n".join(f"line {i}" for i in range(200))  # ~1400 bytes
    out = truncate_text_output(text, total_lines=200, max_bytes=300, file_path="/tmp/foo.txt")
    assert TRUNCATION_NOTICE_MARKER in out
    assert "line 0" in out  # head preserved
    assert "line 199" not in out  # tail dropped
    assert "/tmp/foo.txt" in out  # continuation hint includes path
    assert "start_line=" in out


def test_truncation_preserves_line_integrity():
    text = "\n".join(f"line{i}" for i in range(50))
    out = truncate_text_output(text, total_lines=50, max_bytes=80)
    body = out.split(TRUNCATION_NOTICE_MARKER)[0]
    # Every line in body before the marker must be a complete line
    for line in body.rstrip("\n").split("\n"):
        assert line.startswith("line"), f"partial line leaked: {line!r}"


def test_retruncate_idempotent_on_already_truncated():
    text = "\n".join(f"line {i}" for i in range(200))
    first = truncate_text_output(text, total_lines=200, max_bytes=500, file_path="/tmp/x")
    second = truncate_text_output(first, max_bytes=500)
    assert second == first  # within 100-byte slack, no re-truncation


def test_retruncate_tightens_when_max_bytes_shrinks():
    text = "\n".join(f"line {i}" for i in range(200))
    first = truncate_text_output(text, total_lines=200, max_bytes=500, file_path="/tmp/x")
    smaller = truncate_text_output(first, max_bytes=200)
    assert len(smaller.encode("utf-8")) < len(first.encode("utf-8"))
    assert TRUNCATION_NOTICE_MARKER in smaller


def test_empty_text_passthrough():
    assert truncate_text_output("") == ""
    assert truncate_text_output("hi", max_bytes=0) == "hi"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_transcript_utils.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'fool.harness.transcript_utils'`.

- [ ] **Step 3: Create the module**

Create `fool/harness/transcript_utils.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_transcript_utils.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add fool/harness/transcript_utils.py genius/tests/test_transcript_utils.py
git commit -m "feat(fool): add transcript truncation utils ported from ReMe"
```

---

## Task 3: Add compaction prompts module

**Files:**
- Create: `fool/harness/_compact_prompts.py`

The prompts are pure constants — no tests needed, but a smoke import is included so a typo is caught.

- [ ] **Step 1: Write a smoke test**

Append to `genius/tests/test_transcript_utils.py`:

```python
def test_compact_prompts_exposes_required_strings():
    from fool.harness import _compact_prompts as cp

    assert "上下文压缩助手" in cp.SYSTEM_PROMPT_ZH
    assert "## 目标" in cp.INITIAL_USER_MESSAGE_ZH
    assert "previous-summary" in cp.UPDATE_USER_MESSAGE_ZH or "上次摘要" in cp.UPDATE_USER_MESSAGE_ZH
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_transcript_utils.py::test_compact_prompts_exposes_required_strings -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the prompts module**

Create `fool/harness/_compact_prompts.py`:

```python
"""Chinese prompts for harness transcript compaction.

Ported from ReMe (reme/memory/file_based/components/compactor.yaml,
_zh variants). MTASA-specific edits: replaced the generic "对话" wording
with "Fool harness 一轮内的多轮工具调用记录" so the LLM understands the
domain.
"""

SYSTEM_PROMPT_ZH = """你是一个上下文压缩助手。你的角色是为 Fool harness 一轮内的多轮工具调用记录创建结构化摘要，
这些摘要可以在未来轮次中用于恢复上下文。专注于保留关键信息，同时减少 token 数量。
保留：solver 当前的设计假设、已尝试的修改、Genius 评分反馈、被否决的方向与原因。
丢弃：完整 solver 源码、完整 Genius 报告原文（保留关键数字即可）、重复的 retry 提示。"""


INITIAL_USER_MESSAGE_ZH = """# 任务
根据上面的对话创建一个结构化摘要。

# 规则
- 保持每个部分简洁
- 保留确切的文件路径、函数名称和错误消息
- 保留 Genius 报告中的关键数字（total_score、coverage、uncovered_tasks 等）

# 输出格式

## 目标
[本轮要验证的核心假设是什么]

## 约束和偏好
- [用户/teacher playbook 提到的约束、偏好或要求]
- [或者如果没有提到则为"(none)"]

## 进展
### 已完成
- [x] [已完成的修改、已运行的工具]

### 进行中
- [ ] [当前工作]

### 阻塞
- [如果有任何阻碍进展的问题]

## 关键决策
- **[决策]**: [简短理由]

## 下一步
1. [接下来应该发生的事情的有序列表]

## 关键上下文
- [继续工作所需的数据、示例或参考]
- [或者如果不适用则为"(none)"]

按上述格式输出结构化摘要。"""


UPDATE_USER_MESSAGE_ZH = """# 任务
基于上面的对话和上一轮 previous-summary，更新结构化摘要。

# 规则
- 保留上次摘要中仍然适用的内容
- 把新的进展、决策、阻塞合并进来
- 移除已经被新证据推翻的旧假设
- 保留所有 Genius 评分关键数字

# 输出格式
与首次摘要相同（## 目标 / ## 约束和偏好 / ## 进展 / ## 关键决策 / ## 下一步 / ## 关键上下文）。"""
```

- [ ] **Step 4: Run to verify it passes**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_transcript_utils.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add fool/harness/_compact_prompts.py genius/tests/test_transcript_utils.py
git commit -m "feat(fool): add compaction prompts (zh, ported from ReMe)"
```

---

## Task 4: Implement `TiktokenCounter`

**Files:**
- Create (partial): `fool/harness/session_compactor.py`
- Test: `genius/tests/test_session_compactor.py`

- [ ] **Step 1: Write the failing tests**

Create `genius/tests/test_session_compactor.py`:

```python
from fool.harness.session_compactor import TiktokenCounter


def test_token_counter_counts_english():
    counter = TiktokenCounter()
    assert counter.count_text("hello world") > 0
    assert counter.count_text("hello world") < 10


def test_token_counter_handles_chinese():
    counter = TiktokenCounter()
    n = counter.count_text("你好世界 hello")
    assert n > 0


def test_token_counter_zero_for_empty():
    counter = TiktokenCounter()
    assert counter.count_text("") == 0


def test_token_counter_counts_messages():
    counter = TiktokenCounter()
    msgs = [
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    total = counter.count_messages(msgs)
    # Per-message overhead (~4 tokens) + content tokens
    assert total >= sum(counter.count_text(m["content"]) for m in msgs)
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_session_compactor.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the file with TiktokenCounter only**

Create `fool/harness/session_compactor.py`:

```python
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
from typing import Iterable

import tiktoken

logger = logging.getLogger(__name__)


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
```

- [ ] **Step 4: Run to verify it passes**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_session_compactor.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add fool/harness/session_compactor.py genius/tests/test_session_compactor.py
git commit -m "feat(fool): add TiktokenCounter for session compactor"
```

---

## Task 5: Implement `split_messages_for_compaction`

**Files:**
- Modify: `fool/harness/session_compactor.py` (append)
- Modify: `genius/tests/test_session_compactor.py` (append)

This ports the *useful core* of `AsMsgHandler.context_check`: walk messages from newest backward, keep them while `accumulated_tokens <= reserve`, push older into the "to-compact" bucket. We skip the tool_use/tool_result id alignment because MTASA messages are flat. We **do** keep two MTASA-specific rules:

1. The `system` prefix message (index 0) is **always** kept (it carries the cached prompt).
2. The round-header `user` message (index 1) is **always** kept (it carries `Prior round vNNN` context).

- [ ] **Step 1: Write the failing tests**

Append to `genius/tests/test_session_compactor.py`:

```python
from fool.harness.session_compactor import (
    TiktokenCounter,
    split_messages_for_compaction,
)


def _msg(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def test_split_returns_empty_when_under_threshold():
    counter = TiktokenCounter()
    msgs = [_msg("system", "sys"), _msg("user", "hi"), _msg("assistant", "ok")]
    to_compact, to_keep = split_messages_for_compaction(
        msgs, counter=counter, threshold_tokens=10_000, reserve_tokens=4_000,
    )
    assert to_compact == []
    assert to_keep == msgs


def test_split_keeps_system_and_round_header_when_over_threshold():
    counter = TiktokenCounter()
    # System and round-header are short; middle is a giant blob; tail is short.
    msgs = [
        _msg("system", "SYS-PREFIX"),
        _msg("user", "ROUND-HEADER"),
        _msg("assistant", "x" * 40_000),  # ~10k tokens
        _msg("user", "tool-output: " + "y" * 40_000),
        _msg("assistant", "final draft thoughts"),
    ]
    to_compact, to_keep = split_messages_for_compaction(
        msgs, counter=counter, threshold_tokens=8_000, reserve_tokens=2_000,
    )
    # System (idx 0) and round-header (idx 1) must be in to_keep
    assert to_keep[0]["content"] == "SYS-PREFIX"
    assert to_keep[1]["content"] == "ROUND-HEADER"
    # The tail (final assistant) must be in to_keep
    assert to_keep[-1]["content"] == "final draft thoughts"
    # Something must have been pushed to compact
    assert len(to_compact) >= 1


def test_split_never_drops_latest_message_even_if_huge():
    counter = TiktokenCounter()
    huge = "z" * 200_000
    msgs = [
        _msg("system", "S"),
        _msg("user", "H"),
        _msg("assistant", huge),  # latest; alone exceeds reserve
    ]
    to_compact, to_keep = split_messages_for_compaction(
        msgs, counter=counter, threshold_tokens=10_000, reserve_tokens=2_000,
    )
    assert msgs[-1] in to_keep  # latest never dropped


def test_split_preserves_message_order_in_each_bucket():
    counter = TiktokenCounter()
    msgs = [_msg("system", "S"), _msg("user", "H")] + [
        _msg("assistant" if i % 2 == 0 else "user", f"m{i}-" + "x" * 20_000)
        for i in range(6)
    ]
    to_compact, to_keep = split_messages_for_compaction(
        msgs, counter=counter, threshold_tokens=4_000, reserve_tokens=1_500,
    )
    # Check both buckets follow original order
    full_recombined = to_compact + [m for m in to_keep if m not in (msgs[0], msgs[1])]
    middle_original = [m for m in msgs[2:]]
    # to_compact comes first, then the "kept tail"; together they should
    # match the original middle slice in order
    assert all(
        msgs.index(a) < msgs.index(b)
        for a, b in zip(full_recombined, full_recombined[1:])
    )
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_session_compactor.py -v
```

Expected: 4 prior pass, 4 new fail with `ImportError: cannot import name 'split_messages_for_compaction'`.

- [ ] **Step 3: Append the splitter to `session_compactor.py`**

Append to `fool/harness/session_compactor.py`:

```python
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
```

- [ ] **Step 4: Run to verify they pass**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_session_compactor.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add fool/harness/session_compactor.py genius/tests/test_session_compactor.py
git commit -m "feat(fool): port context split for transcript compaction"
```

---

## Task 6: Implement `compact_tool_results`

**Files:**
- Modify: `fool/harness/session_compactor.py` (append)
- Modify: `genius/tests/test_session_compactor.py` (append)

MTASA-specific simplification: tool results are already flattened into `"user"` messages by `format_tool_user_message()`. We don't need to walk `tool_result` blocks. We just truncate the **content of large `"user"` messages** (which is overwhelmingly tool-result echo) using `truncate_text_output` from Task 2, saving the full content to `state.run_dir / "tool_results" / <uuid>.txt`.

A "recent window" of N most-recent tool messages gets a larger byte budget (so the model can still reason on its latest evidence).

- [ ] **Step 1: Write the failing tests**

Append to `genius/tests/test_session_compactor.py`:

```python
from pathlib import Path

from fool.harness.session_compactor import compact_tool_results
from fool.harness.transcript_utils import TRUNCATION_NOTICE_MARKER


def test_compact_tool_results_leaves_short_messages_alone(tmp_path: Path):
    msgs = [
        _msg("system", "S"),
        _msg("user", "H"),
        _msg("assistant", "thinking"),
        _msg("user", "tool result: short output"),
    ]
    out = compact_tool_results(
        msgs, tool_result_dir=tmp_path, old_max_bytes=200, recent_max_bytes=10_000,
    )
    assert out == msgs  # unchanged


def test_compact_tool_results_truncates_old_user_messages(tmp_path: Path):
    big = "line\n" * 2000  # ~10 KB
    msgs = [
        _msg("system", "S"),
        _msg("user", "H"),
        _msg("assistant", "thinking"),
        _msg("user", big),       # OLD - should be truncated
        _msg("assistant", "more thinking"),
        _msg("user", big),       # RECENT - kept larger
        _msg("assistant", "final"),
    ]
    out = compact_tool_results(
        msgs,
        tool_result_dir=tmp_path,
        old_max_bytes=200,
        recent_max_bytes=20_000,
        recent_n=1,
    )
    # The old user message is truncated
    assert TRUNCATION_NOTICE_MARKER in out[3]["content"]
    assert len(out[3]["content"].encode("utf-8")) < 2000
    # The recent user message is intact (within recent_max_bytes)
    assert TRUNCATION_NOTICE_MARKER not in out[5]["content"]
    # Saved file exists
    saved_files = list(tmp_path.glob("*.txt"))
    assert len(saved_files) >= 1


def test_compact_tool_results_does_not_truncate_system_or_assistant(tmp_path: Path):
    big = "x" * 10_000
    msgs = [
        _msg("system", big),
        _msg("user", "H"),
        _msg("assistant", big),
        _msg("user", big),
    ]
    out = compact_tool_results(
        msgs, tool_result_dir=tmp_path, old_max_bytes=200, recent_max_bytes=200, recent_n=0,
    )
    assert out[0]["content"] == big  # system untouched
    assert out[2]["content"] == big  # assistant untouched
    assert TRUNCATION_NOTICE_MARKER in out[3]["content"]  # user truncated
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_session_compactor.py -v
```

Expected: 8 prior pass, 3 new fail with `ImportError`.

- [ ] **Step 3: Append `compact_tool_results` to `session_compactor.py`**

First add this import near the top of `fool/harness/session_compactor.py` (just under `import tiktoken`):

```python
import uuid
from pathlib import Path

from fool.harness.transcript_utils import truncate_text_output
```

Then append at the end:

```python
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

    # Detect the trailing run of user messages (recent window).
    trailing_user_run = 0
    for msg in reversed(messages):
        if msg.get("role") == "user":
            trailing_user_run += 1
        else:
            break
    recent_window = max(trailing_user_run, recent_n)
    split_index = max(0, len(messages) - recent_window)

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
        truncated = truncate_text_output(
            content,
            start_line=1,
            total_lines=content.count("\n") + 1,
            max_bytes=max_bytes,
            file_path=str(fp),
        )
        out.append({**msg, "content": truncated})

    return out
```

- [ ] **Step 4: Run to verify they pass**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_session_compactor.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add fool/harness/session_compactor.py genius/tests/test_session_compactor.py
git commit -m "feat(fool): truncate large tool-output messages with disk spill"
```

---

## Task 7: Implement `SessionCompactor` orchestrator

**Files:**
- Modify: `fool/harness/session_compactor.py` (append)
- Modify: `genius/tests/test_session_compactor.py` (append)

This is the public API the runner calls. It owns:
- the `TiktokenCounter`
- thresholds (default: `threshold_tokens=80_000`, `reserve_tokens=20_000`)
- a `summary_callback` that records the produced summary somewhere (default: no-op — runner wires it to `FoolMemory.record_session_summary` in Task 9)
- an LLM-callable summarizer (uses `model.complete` via injection, so tests don't need real LLM)

The flow:
1. `compact_tool_results(messages)` — always cheap, no LLM, byte-only.
2. Token-count the result. If under threshold → return unchanged.
3. `split_messages_for_compaction(messages)` → `(to_compact, to_keep)`.
4. Build a compaction prompt: system = `SYSTEM_PROMPT_ZH`; user = formatted `to_compact` transcript + `INITIAL_USER_MESSAGE_ZH` (or `UPDATE_USER_MESSAGE_ZH` if `previous_summary` exists).
5. Call `summarizer.complete(prompt_messages, max_new_tokens)` → summary text.
6. Replace `to_compact` with a single synthetic `{"role": "user", "content": "[Prior context summary]\n" + summary}` message inserted right after the pinned head; keep `to_keep` after.
7. Fire `summary_callback(summary, previous_summary)` for persistence.

- [ ] **Step 1: Write the failing tests**

Append to `genius/tests/test_session_compactor.py`:

```python
from fool.harness.model_client import FakeModelClient
from fool.harness.session_compactor import SessionCompactor


def test_compactor_returns_input_when_under_threshold(tmp_path: Path):
    summarizer = FakeModelClient(outputs=["## 目标\n(not called)"])
    callback_log = []
    compactor = SessionCompactor(
        summarizer=summarizer,
        tool_result_dir=tmp_path / "tr",
        threshold_tokens=50_000,
        reserve_tokens=10_000,
        summary_callback=lambda s, p: callback_log.append((s, p)),
    )
    msgs = [_msg("system", "S"), _msg("user", "H"), _msg("assistant", "ok")]
    out, summary = compactor.maybe_compact(msgs, previous_summary="")
    assert out == msgs
    assert summary == ""
    assert callback_log == []
    assert summarizer.calls == []  # LLM NOT called


def test_compactor_calls_llm_when_over_threshold(tmp_path: Path):
    summary_text = "## 目标\n继续优化 solver\n## 进展\n### 已完成\n- [x] 基线\n"
    summarizer = FakeModelClient(outputs=[summary_text])
    callback_log = []
    compactor = SessionCompactor(
        summarizer=summarizer,
        tool_result_dir=tmp_path / "tr",
        threshold_tokens=4_000,
        reserve_tokens=1_000,
        summary_callback=lambda s, p: callback_log.append((s, p)),
    )
    # Build a transcript large enough to exceed threshold
    msgs = [_msg("system", "S" * 200), _msg("user", "H" * 200)] + [
        _msg("assistant" if i % 2 == 0 else "user", f"turn-{i} " + "x" * 5000)
        for i in range(8)
    ]
    out, summary = compactor.maybe_compact(msgs, previous_summary="")
    assert summary == summary_text
    assert callback_log == [(summary_text, "")]
    # System and round header still at the front
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    # A synthetic summary message follows
    summary_msgs = [m for m in out if "[Prior context summary]" in m.get("content", "")]
    assert len(summary_msgs) == 1
    assert summary_text in summary_msgs[0]["content"]
    # The newest message is preserved at the tail
    assert out[-1]["content"] == msgs[-1]["content"]


def test_compactor_uses_update_prompt_when_previous_summary_provided(tmp_path: Path):
    summarizer = FakeModelClient(outputs=["## 目标\n更新版\n"])
    compactor = SessionCompactor(
        summarizer=summarizer,
        tool_result_dir=tmp_path / "tr",
        threshold_tokens=4_000,
        reserve_tokens=1_000,
    )
    msgs = [_msg("system", "S" * 200), _msg("user", "H" * 200)] + [
        _msg("assistant", "x" * 5000) for _ in range(6)
    ]
    out, summary = compactor.maybe_compact(msgs, previous_summary="## 目标\n之前的目标\n")
    assert "更新版" in summary
    # Inspect the prompt the LLM received
    sent = summarizer.last_messages
    sent_user = next(m for m in sent if m["role"] == "user")
    assert "previous-summary" in sent_user["content"] or "之前的目标" in sent_user["content"]
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_session_compactor.py -v
```

Expected: 11 prior pass, 3 new fail with `ImportError: cannot import name 'SessionCompactor'`.

- [ ] **Step 3: Append `SessionCompactor` to `session_compactor.py`**

First add this import near the top:

```python
from fool.harness.model_client import ModelClient
from fool.harness._compact_prompts import (
    INITIAL_USER_MESSAGE_ZH,
    SYSTEM_PROMPT_ZH,
    UPDATE_USER_MESSAGE_ZH,
)
```

Then append at the end:

```python
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
            raw = model.complete(messages, max_new_tokens)
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
        summary_max_new_tokens: int = 2_000,
        counter: TiktokenCounter | None = None,
        summary_callback=None,
        pinned_head_indices: tuple[int, ...] = (0, 1),
        old_max_bytes: int = 3_000,
        recent_max_bytes: int = 100 * 1024,
        recent_n: int = 1,
    ) -> None:
        assert threshold_tokens > reserve_tokens
        self._summarizer = summarizer
        self._tool_result_dir = Path(tool_result_dir)
        self._threshold = threshold_tokens
        self._reserve = reserve_tokens
        self._summary_max_new_tokens = summary_max_new_tokens
        self._counter = counter or TiktokenCounter()
        self._summary_callback = summary_callback
        self._pinned = pinned_head_indices
        self._old_max_bytes = old_max_bytes
        self._recent_max_bytes = recent_max_bytes
        self._recent_n = recent_n

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
            summary = self._summarizer.complete(prompt, self._summary_max_new_tokens).strip()
        except Exception:
            logger.exception("maybe_compact: summarizer.complete failed; returning uncompacted")
            return messages, previous_summary

        if not summary or "##" not in summary:
            logger.warning("maybe_compact: summarizer returned invalid summary; returning uncompacted")
            return messages, previous_summary

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
```

- [ ] **Step 4: Run to verify they pass**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_session_compactor.py -v
```

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add fool/harness/session_compactor.py genius/tests/test_session_compactor.py
git commit -m "feat(fool): SessionCompactor — LLM-driven transcript compression"
```

---

## Task 8: Add `record_session_summary` to `FoolMemory`

**Files:**
- Modify: `fool/memory_store.py:547-560` (insert after `record_run_lesson`)
- Modify: `fool/memory_store.py:14-25` (add `SESSION_SUMMARIES_NAME` constant)
- Modify: `fool/memory_store.py:305-318` (add `self.session_summaries_path` in `__init__`)
- Modify: `fool/memory_store.py:344-450` (extend `retrieve` to surface recent session summaries)
- Modify: `genius/tests/test_fool_memory.py`

The summary is persisted as a JSONL row (run_id, iteration, target_buckets, text, ts) and surfaced in `retrieve()` output as a separate trailing block (so it doesn't disturb existing BM25 ranking — episodes remain the primary signal).

- [ ] **Step 1: Write the failing tests**

Append to `genius/tests/test_fool_memory.py` (or wherever existing memory tests live — check first with `grep -n "FoolMemory" genius/tests/test_fool_memory.py`):

```python
def test_record_session_summary_persists_and_surfaces_in_retrieve(tmp_path):
    from fool.memory_store import FoolMemory

    mem = FoolMemory(memory_dir=tmp_path, scope="test_scope")

    mem.record_session_summary(
        run_id="run-001",
        iteration=3,
        target_buckets=["medium_seed201"],
        summary_text="## 目标\n收紧 willingness 加权\n## 进展\n### 已完成\n- [x] baseline\n",
    )

    # Persisted file exists
    path = tmp_path / "test_scope" / "session_summaries.jsonl"
    assert path.exists()
    line = path.read_text(encoding="utf-8").strip()
    assert "willingness" in line
    assert "run-001" in line

    # retrieve() output includes a summary block
    out = mem.retrieve(
        target_buckets=["medium_seed201"],
        strategy_lane="willingness",
        query_text="willingness 加权",
    )
    assert "willingness 加权" in out or "Prior session summaries" in out


def test_record_session_summary_appends_multiple(tmp_path):
    from fool.memory_store import FoolMemory

    mem = FoolMemory(memory_dir=tmp_path, scope="test_scope")
    mem.record_session_summary(run_id="r1", iteration=1, target_buckets=["a"], summary_text="## S1\n")
    mem.record_session_summary(run_id="r1", iteration=2, target_buckets=["a"], summary_text="## S2\n")
    path = tmp_path / "test_scope" / "session_summaries.jsonl"
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_fool_memory.py -v -k session_summary
```

Expected: FAIL with `AttributeError: 'FoolMemory' object has no attribute 'record_session_summary'`.

- [ ] **Step 3: Add the constant**

In `fool/memory_store.py`, find the constants block around line 14–24 and add:

```python
SESSION_SUMMARIES_NAME = "session_summaries.jsonl"
```

immediately before `BEST_SOLVER_NAME = "best_solver.py"`.

- [ ] **Step 4: Initialize the path in `__init__`**

In `fool/memory_store.py:305-318`, inside `FoolMemory.__init__`, locate the line that sets `self.run_lessons_path = ...` and add immediately after:

```python
self.session_summaries_path = self.memory_dir / SESSION_SUMMARIES_NAME
```

- [ ] **Step 5: Add the `record_session_summary` method**

In `fool/memory_store.py`, immediately after the `record_run_lesson` method (around line 547–560), add:

```python
def record_session_summary(
    self,
    *,
    run_id: str,
    iteration: int,
    target_buckets: list[str],
    summary_text: str,
) -> None:
    """Append one harness-transcript summary to session_summaries.jsonl.

    Used by SessionCompactor as its summary_callback: every time the
    harness compresses a long transcript, the LLM-produced structured
    summary lands here, then becomes BM25-retrievable via retrieve().
    """
    if not summary_text or not summary_text.strip():
        return
    self.memory_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": str(run_id),
        "iteration": int(iteration),
        "target_buckets": [str(x) for x in (target_buckets or [])],
        "summary": summary_text.strip(),
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    with self.session_summaries_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
```

- [ ] **Step 6: Extend `retrieve` to surface recent session summaries**

In `fool/memory_store.py`, find the end of `retrieve()` (around line 449, just before `return "\n".join(lines)`). Insert this block right before `return`:

```python
# Append the 3 most recent session summaries matching any requested tag
# (lightweight surface-only; BM25 ranking is still episode-driven).
session_records = self._read_jsonl(self.session_summaries_path)
if session_records:
    matched: list[dict[str, Any]] = []
    for rec in reversed(session_records):
        rec_tags = _collect_case_tags(
            [str(x) for x in rec.get("target_buckets", [])]
        )
        if not requested_tags or (rec_tags & requested_tags) or not rec_tags:
            matched.append(rec)
        if len(matched) >= 3:
            break
    if matched:
        lines.append("")
        lines.append("Prior session summaries (most recent first):")
        for rec in matched:
            summary_head = str(rec.get("summary", ""))[:600]
            lines.append(
                "- run={run} iter={iter} buckets={buckets}: {summary}".format(
                    run=rec.get("run_id", "?"),
                    iter=rec.get("iteration", "?"),
                    buckets=",".join(str(x) for x in rec.get("target_buckets", [])) or "all",
                    summary=summary_head,
                )
            )
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_fool_memory.py -v
```

Expected: all prior tests + 2 new session_summary tests pass.

- [ ] **Step 8: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add fool/memory_store.py genius/tests/test_fool_memory.py
git commit -m "feat(fool): record session summaries and surface them in retrieve()"
```

---

## Task 9: Wire `SessionCompactor` into `runner.py`

**Files:**
- Modify: `fool/harness/runner.py` (imports + inside `run_round`)
- Modify: `genius/tests/test_harness_runner.py`

The runner builds a compactor once per round. The summarizer is the **same** `ModelClient` as the solver model (re-using the user's API key/model — no separate config). Before each `model.complete(messages, ...)` call (line 145), invoke `compactor.maybe_compact(messages, previous_summary)`. The callback writes via `FoolMemory.record_session_summary` if a memory object is in scope; otherwise no-op.

To keep `run_round`'s signature backward-compatible, add `compactor: SessionCompactor | None = None`. Default behavior (when `None`): construct a compactor only if the messages list crosses a soft threshold mid-round; if no `tool_result_dir` is available, skip. This means existing tests don't break.

- [ ] **Step 1: Write the failing test**

Append to `genius/tests/test_harness_runner.py` (peek at imports first to follow style):

```python
def test_run_round_invokes_compactor_when_provided(tmp_path, monkeypatch):
    """Smoke test: a SessionCompactor passed to run_round gets called per step."""
    from fool.harness.runner import run_round, RoundState
    from fool.harness.model_client import FakeModelClient
    from fool.harness.session_compactor import SessionCompactor

    # Model emits one immediate <final> so the round exits in one step.
    model = FakeModelClient(outputs=[
        '<final>\n{"plan": "noop", "solver": "def solve(input_text):\\n    return []\\n"}\n</final>'
    ])

    call_log = []

    class SpyCompactor(SessionCompactor):
        def maybe_compact(self, messages, previous_summary):
            call_log.append(len(messages))
            return messages, previous_summary

    spy = SpyCompactor(
        summarizer=model,
        tool_result_dir=tmp_path / "tr",
        threshold_tokens=100_000,
        reserve_tokens=20_000,
    )

    state = RoundState(
        run_dir=tmp_path / "run",
        iteration=1,
        # ... fill remaining required fields per RoundState definition
    )
    # NOTE: if RoundState has more required fields, set them to minimal values
    # by inspecting fool/harness/context.py first.

    try:
        run_round(state, model, max_steps=2, compactor=spy)
    except Exception:
        pass  # Don't care about final solver validity for this smoke test.

    assert len(call_log) >= 1
```

⚠️ **Before writing this test**, check `fool/harness/context.py` to learn `RoundState`'s required fields. If too many are required, write a `_make_minimal_state(tmp_path)` helper using whatever existing tests do — see how `genius/tests/test_harness_runner.py` constructs `RoundState` and copy that pattern.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_harness_runner.py::test_run_round_invokes_compactor_when_provided -v
```

Expected: FAIL with `TypeError: run_round() got an unexpected keyword argument 'compactor'`.

- [ ] **Step 3: Add `compactor` parameter to `run_round`**

In `fool/harness/runner.py:73-82`, change the `run_round` signature from:

```python
def run_round(
    state: RoundState,
    model: ModelClient,
    *,
    registry: ToolRegistry | None = None,
    tool_context_factory=None,
    max_steps: int = 50,
    max_new_tokens: int = 4096,
    on_step: StepCallback | None = None,
) -> HarnessResult:
```

to:

```python
def run_round(
    state: RoundState,
    model: ModelClient,
    *,
    registry: ToolRegistry | None = None,
    tool_context_factory=None,
    max_steps: int = 50,
    max_new_tokens: int = 4096,
    on_step: StepCallback | None = None,
    compactor: "SessionCompactor | None" = None,
) -> HarnessResult:
```

Add import at the top of `fool/harness/runner.py`:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fool.harness.session_compactor import SessionCompactor
```

- [ ] **Step 4: Invoke the compactor inside the loop**

Around `fool/harness/runner.py:119-145` (the message-init + while-loop), make two edits:

After the line `messages: list[dict[str, str]] = [` block (around line 119–122), add:

```python
    previous_summary: str = ""
```

Inside the `while tool_steps < max_steps:` loop, **immediately before** `raw = model.complete(messages, max_new_tokens)` (currently line 145), insert:

```python
        if compactor is not None:
            messages, previous_summary = compactor.maybe_compact(messages, previous_summary)
```

- [ ] **Step 5: Run the new test to verify it passes**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_harness_runner.py::test_run_round_invokes_compactor_when_provided -v
```

Expected: PASS.

- [ ] **Step 6: Run the full harness runner test file to check no regression**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests/test_harness_runner.py -v
```

Expected: all existing tests + the new one pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add fool/harness/runner.py genius/tests/test_harness_runner.py
git commit -m "feat(fool): wire SessionCompactor into run_round (opt-in)"
```

---

## Task 10: Wire compactor construction in `fool_loop.py`

**Files:**
- Modify: `fool/fool_loop.py` (locate the `run_round(...)` call site)

The loop is the natural place to build the compactor once per round (or once per run and reuse), inject the model as summarizer, and wire `summary_callback` to the memory store.

- [ ] **Step 1: Locate the run_round call site**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && grep -n "run_round" fool/fool_loop.py
```

Expected: 1–3 matches. Read the surrounding ~30 lines to understand how `state`, `model`, and `memory` are wired.

- [ ] **Step 2: Add the compactor construction**

In `fool/fool_loop.py`, immediately **before** the `run_round(...)` call inside the iteration loop, insert:

```python
from fool.harness.session_compactor import SessionCompactor

def _make_summary_callback(memory, run_id: str, iteration: int, target_buckets: list[str]):
    def _cb(summary: str, previous_summary: str) -> None:
        memory.record_session_summary(
            run_id=run_id,
            iteration=iteration,
            target_buckets=target_buckets,
            summary_text=summary,
        )
    return _cb

compactor = SessionCompactor(
    summarizer=model,
    tool_result_dir=state.run_dir / "tool_results",
    threshold_tokens=80_000,
    reserve_tokens=20_000,
    summary_callback=_make_summary_callback(
        memory=memory,
        run_id=state.run_id if hasattr(state, "run_id") else str(state.run_dir.name),
        iteration=state.iteration,
        target_buckets=getattr(state, "target_buckets", []) or [],
    ),
)
```

Then pass `compactor=compactor` as a kwarg to the existing `run_round(...)` call.

⚠️ **Field names**: if `state` does not expose `run_id` or `target_buckets`, adapt to whatever the actual `RoundState` exposes — check `fool/harness/context.py`. Put the `_make_summary_callback` function and `from fool.harness.session_compactor import SessionCompactor` at module top, not inside the loop.

- [ ] **Step 3: Manual smoke run (1 iteration, dry)**

If a sample dataset and API key are available:

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python fool/fool_loop.py \
  --api-type openai --api-key "$OPENAI_API_KEY" --model gpt-4.1-mini \
  --iterations 1 --input-dir data/sample_10_cases --scoring official_like_latest
```

Expected:
- Exit code 0.
- A directory `out/runs/<run_id>/tool_results/` may or may not contain `.txt` files (depends on whether any tool output exceeded the threshold).
- After 2 iterations or more with a long transcript, `out/memory/<fingerprint>/session_summaries.jsonl` exists and contains valid JSON lines.

If no API key is available, skip step 3 — the unit tests already cover the wiring.

- [ ] **Step 4: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add fool/fool_loop.py
git commit -m "feat(fool): wire SessionCompactor into fool_loop"
```

---

## Task 11: Update `CLAUDE.md` and docs

**Files:**
- Modify: `CLAUDE.md` (the Fool memory section, around the bullet that starts "**fool memory** — `fool/memory_store.py`...")

- [ ] **Step 1: Locate the section**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && grep -n "memory_store" CLAUDE.md
```

- [ ] **Step 2: Update the bullet**

In `CLAUDE.md`, find the line starting with `- **fool memory** — `fool/memory_store.py` writes scored episodes...` and replace the bullet with:

```markdown
- **fool memory** — `fool/memory_store.py` writes scored episodes and a compact
  strategy index under `out/memory/<dataset_fingerprint>/`; later API calls retrieve
  relevant outcomes only for the same dataset and block unproductive repeated
  hypotheses. Frontend approvals pause a proposed round before code generation and
  submission to Genius. The harness also runs `fool/harness/session_compactor.py`
  before each LLM call: when the round's transcript exceeds ~80k tokens, large
  tool outputs are spilled to `out/runs/<run_id>/tool_results/*.txt`, older turns
  are summarized into a structured Chinese plan by the same LLM, and the summary
  is appended to `session_summaries.jsonl` so it becomes BM25-retrievable next
  round (`record_session_summary` → `retrieve()` "Prior session summaries" block).
  No vector store or embedding model is used.
```

- [ ] **Step 3: Run the full test suite one more time**

```bash
cd /Users/zhuym/Documents/101camp/MTASA && .venv/bin/python -m pytest genius/tests -v
```

Expected: all tests pass (existing + new).

- [ ] **Step 4: Commit**

```bash
cd /Users/zhuym/Documents/101camp/MTASA
git add CLAUDE.md
git commit -m "docs(fool): document session compactor in CLAUDE.md"
```

---

## Self-Review

**1. Spec coverage** — checking against the brief from the conversation:

| Spec item | Task |
|---|---|
| Reuse `file_utils.py` directly | Task 2 |
| Reuse compactor.yaml prompts (zh) | Task 3 |
| Reuse `context_check` algorithm | Task 5 |
| Reuse tool_result truncation | Task 6 |
| Use tiktoken for token counting | Tasks 1, 4 |
| LLM compaction via `fool/llm_client.py` (existing ModelClient) | Task 7 |
| **Skip** vector store / embeddings | Implicit throughout — no faiss / chroma / openai-embeddings imports |
| Persist summaries so memory_store BM25 sees them | Task 8 |
| Wire into runner with backward-compat | Task 9 |
| Wire into fool_loop end-to-end | Task 10 |
| Docs | Task 11 |

All covered.

**2. Placeholder scan** — searched for "TBD", "TODO", "implement later", "appropriate error handling", "fill in details": none present in normative steps. Two warnings (⚠️ in Task 9 step 1 and Task 10 step 2) explicitly tell the implementer "go read context.py to learn the actual fields" — these are research instructions, not placeholders.

**3. Type consistency** —
- `TiktokenCounter` is the class name throughout (Tasks 4, 5, 7).
- `split_messages_for_compaction(messages, *, counter, threshold_tokens, reserve_tokens, pinned_head_indices)` — same signature in Tasks 5 and 7.
- `compact_tool_results(messages, *, tool_result_dir, old_max_bytes, recent_max_bytes, recent_n, pinned_head_indices)` — same in Tasks 6 and 7.
- `SessionCompactor.maybe_compact(messages, previous_summary) -> (messages, summary)` — same in Tasks 7, 9, 10.
- `FoolMemory.record_session_summary(*, run_id, iteration, target_buckets, summary_text)` — same in Tasks 8, 10.
- `compactor` kwarg name on `run_round` matches in Tasks 9 and 10.
- `TRUNCATION_NOTICE_MARKER` import path: `fool.harness.transcript_utils` in Tasks 2, 6.
- Prompt constant names `SYSTEM_PROMPT_ZH`, `INITIAL_USER_MESSAGE_ZH`, `UPDATE_USER_MESSAGE_ZH` — same in Tasks 3, 7.

All consistent.
