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


def test_compact_prompts_exposes_required_strings():
    from fool.harness import _compact_prompts as cp

    assert "上下文压缩助手" in cp.SYSTEM_PROMPT_ZH
    assert "## 目标" in cp.INITIAL_USER_MESSAGE_ZH
    assert "previous-summary" in cp.UPDATE_USER_MESSAGE_ZH or "上次摘要" in cp.UPDATE_USER_MESSAGE_ZH
