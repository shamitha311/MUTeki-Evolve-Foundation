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
    # to_compact comes first, then the "kept tail"; together they should
    # match the original middle slice in order
    assert all(
        msgs.index(a) < msgs.index(b)
        for a, b in zip(full_recombined, full_recombined[1:])
    )


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
    summary_text = "## 目标\n继续优化 solver\n## 进展\n### 已完成\n- [x] 基线"
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
        _msg("assistant", "x" * 5000) for _ in range(8)
    ]
    out, summary = compactor.maybe_compact(msgs, previous_summary="## 目标\n之前的目标\n")
    assert "更新版" in summary
    # Inspect the prompt the LLM received
    sent = summarizer.last_messages
    sent_user = next(m for m in sent if m["role"] == "user")
    assert "previous-summary" in sent_user["content"] or "之前的目标" in sent_user["content"]


import re


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


def test_compactor_routes_summary_to_notes(tmp_path):
    from fool.memory_notes import MemoryNotesStore

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

    parts = [
        p.read_text(encoding="utf-8")
        for p in (tmp_path / "mem" / "notes").glob("key_decision_*.md")
    ]
    assert parts, "no key_decision_*.md note produced"
    decisions = "\n".join(parts)
    assert "使用 greedy" in decisions
    assert "run_test" in decisions
