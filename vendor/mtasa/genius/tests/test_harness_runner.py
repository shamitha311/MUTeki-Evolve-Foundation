from __future__ import annotations

import json
from pathlib import Path

import pytest

from fool.harness.context import HarnessFailure, RoundState
from fool.harness.model_client import FakeModelClient
from fool.harness.runner import run_round


def _state(tmp_path: Path) -> RoundState:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return RoundState(
        iteration=1,
        best_score=None,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[],
        input_dir=input_dir,
        run_dir=run_dir,
    )


_INTENT = "<intent>step</intent>"
_VALID_DRAFT = (
    _INTENT
    + '<tool name="draft_solver"><code>def solve(t):\n    return []\n</code></tool>'
)
_VALID_FINAL = (
    _INTENT
    + '<final><plan>{"hypothesis":"baseline","analysis":"a","target_buckets":[],"edit_plan":[]}</plan></final>'
)
_SMOKE_TOOL = _INTENT + '<tool>{"name":"smoke_test_solver","args":{}}</tool>'
_BLOCK_PATCH_TOOL = (
    _INTENT
    + '<tool name="block_patch"><blocks>\n'
    + '<<<<<<< SEARCH\n'
    + 'P_UNCOV = 100.0\n'
    + '=======\n'
    + 'P_UNCOV = 101.0\n'
    + '>>>>>>> REPLACE\n'
    + '</blocks></tool>'
)


def test_runner_returns_solver_and_plan_on_final(tmp_path: Path) -> None:
    fake = FakeModelClient([_VALID_DRAFT, _VALID_FINAL])
    result = run_round(_state(tmp_path), fake, max_steps=4, max_tokens=256)
    assert "def solve" in result.solver_code
    assert result.plan["hypothesis"] == "baseline"
    assert result.steps_taken == 2
    assert result.transcript_path.exists()


def test_runner_raises_when_max_steps_exceeded(tmp_path: Path) -> None:
    fake = FakeModelClient(
        [_INTENT + '<tool>{"name":"profile_dataset","args":{}}</tool>'] * 6
    )
    with pytest.raises(HarnessFailure, match="max_steps"):
        run_round(_state(tmp_path), fake, max_steps=3, max_tokens=256)


def test_runner_auto_seeds_draft_from_template_when_no_incumbent(tmp_path: Path) -> None:
    state = _state(tmp_path)
    fake = FakeModelClient([_VALID_FINAL])
    result = run_round(state, fake, max_steps=3, max_tokens=256)
    assert "def solve" in result.solver_code
    assert (state.run_dir / "draft.py").exists()


def test_runner_handles_retries_with_malformed_then_valid(tmp_path: Path) -> None:
    fake = FakeModelClient(
        ["I think we should...", _VALID_DRAFT, _VALID_FINAL]
    )
    result = run_round(_state(tmp_path), fake, max_steps=6, max_tokens=256)
    assert "def solve" in result.solver_code
    assert result.steps_taken == 3


def test_runner_raises_on_too_many_malformed(tmp_path: Path) -> None:
    fake = FakeModelClient(["junk"] * 20)
    with pytest.raises(HarnessFailure, match="malformed"):
        run_round(_state(tmp_path), fake, max_steps=4, max_tokens=256)


def test_runner_retry_notice_includes_intent_when_present(tmp_path: Path) -> None:
    # Model emits an intent but malformed tool JSON; on the retry turn it
    # should see its own intent text echoed back.
    malformed = '<intent>我要调用 read_current_draft 看一下当前草稿</intent><tool>{bad json}</tool>'
    fake = FakeModelClient([malformed, _VALID_DRAFT, _VALID_FINAL])
    events: list[tuple[str, dict]] = []
    run_round(
        _state(tmp_path),
        fake,
        max_steps=6,
        max_tokens=256,
        on_step=lambda k, p: events.append((k, p)),
    )
    retry_events = [p for k, p in events if k == "retry"]
    assert retry_events, "expected at least one retry event"
    assert "Your <intent> was" in retry_events[0]["message"]
    assert "read_current_draft" in retry_events[0]["message"]


def test_runner_emits_intent_event_when_present(tmp_path: Path) -> None:
    draft_with_intent = (
        "<intent>先起一个最小可行 solver，覆盖所有任务</intent>\n" + _VALID_DRAFT
    )
    final_with_intent = (
        "<intent>draft 已写好，提交看分</intent>\n" + _VALID_FINAL
    )
    fake = FakeModelClient([draft_with_intent, final_with_intent])
    events: list[tuple[str, dict]] = []

    def cb(kind, payload):
        events.append((kind, payload))

    run_round(_state(tmp_path), fake, max_steps=4, max_tokens=256, on_step=cb)
    intents = [p for k, p in events if k == "intent"]
    assert len(intents) == 2
    assert "最小可行" in intents[0]["text"]
    assert intents[0]["step"] == 1
    assert intents[1]["step"] == 2


def test_runner_uses_multi_turn_messages(tmp_path: Path) -> None:
    """Final call should see: system + initial user + (assistant + user) pairs."""
    fake = FakeModelClient([_VALID_DRAFT, _VALID_FINAL])
    run_round(_state(tmp_path), fake, max_steps=4, max_tokens=256)

    first = fake.calls[0]
    assert first[0]["role"] == "system"
    assert "AutoSolver Agent" in first[0]["content"]
    assert first[1]["role"] == "user"
    assert "Round: 1" in first[1]["content"]
    assert len(first) == 2

    second = fake.calls[1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert "draft_solver" in second[2]["content"]
    assert "[tool_result name=draft_solver" in second[3]["content"]


def test_runner_requires_smoke_after_draft_edit_before_final(tmp_path: Path) -> None:
    fake = FakeModelClient([_BLOCK_PATCH_TOOL, _VALID_FINAL, _SMOKE_TOOL, _VALID_FINAL])

    result = run_round(_state(tmp_path), fake, max_steps=8, max_tokens=256)

    assert result.plan["hypothesis"] == "baseline"
    second_call_last_user = fake.calls[2][-1]
    assert second_call_last_user["role"] == "user"
    assert "smoke validation required" in second_call_last_user["content"]

    transcript = json.loads(result.transcript_path.read_text(encoding="utf-8"))
    tool_names = [
        item.get("name")
        for item in transcript.get("transcript", [])
        if item.get("role") == "tool"
    ]
    assert "block_patch" in tool_names
    assert "smoke_test_solver" in tool_names


def test_runner_invokes_compactor_when_provided(tmp_path: Path) -> None:
    """When a SessionCompactor is passed, run_round calls maybe_compact() before each LLM step."""
    from fool.harness.session_compactor import SessionCompactor

    fake = FakeModelClient([_VALID_DRAFT, _VALID_FINAL])

    call_log: list[int] = []

    class SpyCompactor(SessionCompactor):
        def maybe_compact(self, messages, previous_summary):
            call_log.append(len(messages))
            return messages, previous_summary

    spy = SpyCompactor(
        summarizer=fake,
        tool_result_dir=tmp_path / "tr",
        threshold_tokens=100_000,
        reserve_tokens=20_000,
    )

    run_round(_state(tmp_path), fake, max_steps=4, max_tokens=256, compactor=spy)
    assert len(call_log) >= 2
    assert call_log[-1] >= call_log[0]


def test_runner_passes_memory_index_path_to_round_header(tmp_path: Path) -> None:
    """When memory_notes is provided AND MEMORY.md exists, the initial user
    message must embed the [Memory Index Head] block."""
    from fool.memory_notes import MemoryNotesStore
    notes = MemoryNotesStore(root=tmp_path / "mem")
    notes.write_note(section="preference", title="stdlib only",
                     body="hard rule", run_id="prev", iteration=1)
    notes.aggregate_index()

    fake = FakeModelClient([_VALID_DRAFT, _VALID_FINAL])
    run_round(_state(tmp_path), fake, max_steps=4, max_tokens=256, memory_notes=notes)
    first_user = fake.calls[0][1]["content"]
    assert "Memory Index Head" in first_user
    assert "stdlib only" in first_user


def test_runner_works_without_compactor(tmp_path: Path) -> None:
    """Backward compat: when compactor is omitted (default None), no compaction happens."""
    fake = FakeModelClient([_VALID_DRAFT, _VALID_FINAL])
    result = run_round(_state(tmp_path), fake, max_steps=4, max_tokens=256)
    assert "def solve" in result.solver_code


def test_runner_streams_dialog_jsonl(tmp_path):
    """run_round writes each message turn to dialog/round_001.jsonl."""
    fake = FakeModelClient([_VALID_DRAFT, _VALID_FINAL])
    state = _state(tmp_path)
    run_round(state, fake, max_steps=4, max_tokens=256)
    p = state.run_dir / "dialog" / "round_001.jsonl"
    assert p.exists()
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 4
    parsed = [json.loads(l) for l in lines]
    roles = [m["role"] for m in parsed]
    assert "system" in roles and "assistant" in roles


def test_runner_threads_memory_notes_into_tool_context(tmp_path):
    """When memory_notes is passed, tools should see it via ToolContext."""
    from fool.memory_notes import MemoryNotesStore
    notes = MemoryNotesStore(root=tmp_path / "mem")
    notes.write_note(section="lesson", title="hint", body="seed42 is small",
                     run_id="prev", iteration=1)

    search_call = _INTENT + '<tool>{"name":"memory_search","args":{"query":"seed42"}}</tool>'
    fake = FakeModelClient([search_call, _VALID_FINAL])
    state = _state(tmp_path)
    run_round(state, fake, max_steps=4, max_tokens=256, memory_notes=notes)

    second = fake.calls[1]
    last_user = second[-1]["content"]
    assert "[tool_result name=memory_search" in last_user
    assert "hint" in last_user or "seed42" in last_user


def test_runner_rejects_tool_without_intent(tmp_path: Path) -> None:
    """A tool call lacking <intent> must be refused, counted as malformed,
    and re-prompted with a notice asking the model to add an intent."""
    no_intent_tool = '<tool>{"name":"smoke_test_solver","args":{}}</tool>'
    fake = FakeModelClient([no_intent_tool, _SMOKE_TOOL, _VALID_FINAL])
    events: list[tuple[str, dict]] = []

    result = run_round(
        _state(tmp_path), fake,
        max_steps=4, max_tokens=256,
        on_step=lambda k, p: events.append((k, p)),
    )

    # The 2nd LLM call (after the first turn was rejected) should have a
    # user notice asking for intent. The 3rd call's smoke result confirms
    # the retry succeeded.
    retry_events = [
        p for k, p in events
        if k == "retry" and p.get("failure_type") == "intent_missing"
    ]
    assert len(retry_events) == 1
    assert "没有 <intent>" in retry_events[0]["message"]
    assert result.plan["hypothesis"] == "baseline"


def test_runner_rejects_final_without_intent(tmp_path: Path) -> None:
    """A <final> without <intent> is also rejected (same hardening)."""
    no_intent_final = (
        '<final><plan>{"hypothesis":"baseline","analysis":"a",'
        '"target_buckets":[],"edit_plan":[]}</plan></final>'
    )
    fake = FakeModelClient([no_intent_final, _VALID_FINAL])
    events: list[tuple[str, dict]] = []

    run_round(
        _state(tmp_path), fake,
        max_steps=4, max_tokens=256,
        on_step=lambda k, p: events.append((k, p)),
    )

    retry_events = [
        p for k, p in events
        if k == "retry" and p.get("failure_type") == "intent_missing"
    ]
    assert len(retry_events) == 1
    assert retry_events[0]["tool_name"] == "<final>"


def test_final_guard_blocks_then_accepts_revised_final(tmp_path: Path) -> None:
    """When final_guard_max_attempts>0 and the guard returns violations, the
    runner injects a critique and re-prompts; the model's next <final> is
    then accepted without a second guard call (attempts cap)."""
    guard_violation = (
        '{"consistent": false, "violations": '
        '[{"type":"hypothesis_diff","detail":"plan claims X but no patch did X"}]}'
    )
    fake = FakeModelClient([_VALID_FINAL, guard_violation, _VALID_FINAL])
    events: list[tuple[str, dict]] = []

    def cb(kind, payload):
        events.append((kind, payload))

    result = run_round(
        _state(tmp_path), fake,
        max_steps=4, max_tokens=256,
        on_step=cb,
        final_guard_max_attempts=1,
    )

    # The second <final> was accepted, first was rejected by guard.
    assert result.plan["hypothesis"] == "baseline"
    guard_events = [p for k, p in events if k == "final_guard"]
    assert len(guard_events) == 1
    assert guard_events[0]["status"] == "violations"
    assert guard_events[0]["violations"][0]["type"] == "hypothesis_diff"

    # The guard's critique should have been fed back to the model as a user
    # message before the redo.
    third_call_msgs = fake.calls[2]
    assert any("final_guard" in m["content"] for m in third_call_msgs)


def test_final_guard_passes_teacher_advice_to_judge(tmp_path: Path) -> None:
    """When teacher_review_block is provided, it must appear in the guard
    payload (the judge needs it to check advice_violation)."""
    guard_ok = '{"consistent": true, "violations": []}'
    fake = FakeModelClient([_VALID_FINAL, guard_ok])
    run_round(
        _state(tmp_path), fake,
        max_steps=4, max_tokens=256,
        final_guard_max_attempts=1,
        teacher_review_block="[Teacher Review] 已饱和方向: foo. 候选方向: bar.",
    )
    # The 2nd call is the guard; its user message should contain the advice.
    guard_msgs = fake.calls[1]
    user_payload = next(m["content"] for m in guard_msgs if m["role"] == "user")
    assert "Teacher Review" in user_payload
    assert "已饱和方向" in user_payload


def test_final_guard_disabled_by_default(tmp_path: Path) -> None:
    """Default attempts=0 → no extra LLM call, behavior unchanged."""
    fake = FakeModelClient([_VALID_FINAL])
    run_round(_state(tmp_path), fake, max_steps=4, max_tokens=256)
    # Only one call (the initial <final>); no guard call consumed an output.
    assert len(fake.calls) == 1


def test_final_guard_skips_on_unparseable_response(tmp_path: Path) -> None:
    """If the guard model returns gibberish, accept the final (don't loop)."""
    fake = FakeModelClient([_VALID_FINAL, "not json at all"])
    events: list[tuple[str, dict]] = []
    run_round(
        _state(tmp_path), fake,
        max_steps=4, max_tokens=256,
        on_step=lambda k, p: events.append((k, p)),
        final_guard_max_attempts=1,
    )
    guard_events = [p for k, p in events if k == "final_guard"]
    assert len(guard_events) == 1
    assert guard_events[0]["status"] == "skipped"


def test_runner_threads_teacher_review_block_into_round_header(tmp_path: Path) -> None:
    """When teacher_review_block is provided, it should appear above Round/Best in user header."""
    fake = FakeModelClient([_VALID_DRAFT, _VALID_FINAL])
    block = "[teacher review @ iteration 5]\n机制: sort_anchor"
    run_round(
        _state(tmp_path), fake,
        max_steps=4, max_tokens=256,
        teacher_review_block=block,
    )
    first_user = fake.calls[0][1]["content"]
    assert "teacher review" in first_user
    assert first_user.index("teacher review") < first_user.index("Round: 1")
