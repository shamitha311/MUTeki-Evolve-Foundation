from __future__ import annotations

from fool.harness.parser import (
    canonical_tool_render,
    extract_intent,
    parse_model_output,
)


def test_extract_intent_returns_trimmed_text() -> None:
    raw = '<intent>看一下上一轮报告</intent><tool>{"name":"read_last_report","args":{}}</tool>'
    assert extract_intent(raw) == "看一下上一轮报告"


def test_extract_intent_empty_when_absent() -> None:
    assert extract_intent('<tool>{"name":"x","args":{}}</tool>') == ""


def test_parse_ignores_intent_and_returns_tool() -> None:
    raw = (
        "<intent>读一下数据集画像，看哪个桶最差</intent>\n"
        '<tool>{"name":"profile_dataset","args":{}}</tool>'
    )
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload["name"] == "profile_dataset"


_INTENT = "<intent>step</intent>"


def test_parses_json_tool_call() -> None:
    raw = _INTENT + '<tool>{"name":"read_teacher_playbook","args":{}}</tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload == {"name": "read_teacher_playbook", "args": {}}


def test_parses_json_tool_call_with_args() -> None:
    raw = _INTENT + '<tool>{"name":"rank_bottlenecks","args":{"top_k":3}}</tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload == {"name": "rank_bottlenecks", "args": {"top_k": 3}}


def test_parses_xml_tool_call_with_content() -> None:
    raw = (
        _INTENT
        + '<tool name="draft_solver">'
        + "<content>def solve(t):\n    return []\n</content>"
        + "</tool>"
    )
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload["name"] == "draft_solver"
    assert payload["args"]["content"] == "def solve(t):\n    return []\n"


def test_parses_xml_snapshot_draft_with_label_body() -> None:
    raw = _INTENT + '<tool name="snapshot_draft"><label>baseline</label></tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload == {"name": "snapshot_draft", "args": {"label": "baseline"}}


def test_parses_xml_block_patch_with_blocks_body() -> None:
    raw = (
        _INTENT
        + '<tool name="block_patch"><blocks>\n'
        + "<<<<<<< SEARCH\nb\n=======\nB\n>>>>>>> REPLACE\n"
        + "</blocks></tool>"
    )
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload["name"] == "block_patch"
    assert "<<<<<<< SEARCH" in payload["args"]["blocks"]
    assert ">>>>>>> REPLACE" in payload["args"]["blocks"]


def test_parses_xml_tool_with_args_json_body() -> None:
    raw = (
        _INTENT
        + '<tool name="rank_bottlenecks"><args>{"top_k": 3}</args></tool>'
    )
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload == {"name": "rank_bottlenecks", "args": {"top_k": 3}}


def test_parses_xml_tool_with_empty_args_json() -> None:
    raw = _INTENT + '<tool name="profile_dataset"><args>{}</args></tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload == {"name": "profile_dataset", "args": {}}


def test_xml_args_json_malformed_returns_retry() -> None:
    raw = _INTENT + '<tool name="x"><args>{not json}</args></tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "retry"
    assert "<args>" in payload


def test_xml_args_non_object_returns_retry() -> None:
    raw = _INTENT + '<tool name="x"><args>[1,2,3]</args></tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "retry"


def test_xml_args_merges_with_attrs() -> None:
    raw = (
        _INTENT
        + '<tool name="memory_search" scope="global">'
        + '<args>{"query": "uncovered"}</args></tool>'
    )
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload["name"] == "memory_search"
    assert payload["args"]["query"] == "uncovered"
    assert payload["args"]["scope"] == "global"


def test_parses_final_with_plan() -> None:
    raw = _INTENT + '<final><plan>{"hypothesis":"x","analysis":"y"}</plan></final>'
    kind, payload = parse_model_output(raw)
    assert kind == "final"
    assert payload == {"plan": {"hypothesis": "x", "analysis": "y"}}


def test_malformed_tool_json_returns_retry() -> None:
    raw = _INTENT + "<tool>{not json}</tool>"
    kind, payload = parse_model_output(raw)
    assert kind == "retry"
    assert "malformed tool JSON" in payload


def test_missing_intent_no_longer_blocks_parse() -> None:
    # Soft intent: parser must succeed even without <intent>. The runner
    # surfaces the absence via an on_step("intent_missing") event instead
    # of burning a model turn on a retry.
    raw = '<tool>{"name":"profile_dataset","args":{}}</tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload == {"name": "profile_dataset", "args": {}}


def test_missing_intent_still_parses_xml_tool_form() -> None:
    raw = '<tool name="profile_dataset"><args>{}</args></tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload["name"] == "profile_dataset"


def test_missing_intent_still_parses_final() -> None:
    raw = '<final><plan>{"hypothesis":"x"}</plan></final>'
    kind, payload = parse_model_output(raw)
    assert kind == "final"
    assert payload["plan"]["hypothesis"] == "x"


def test_missing_tool_name_returns_retry() -> None:
    raw = '<tool>{"args":{}}</tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "retry"


def test_empty_final_returns_retry() -> None:
    raw = "<final></final>"
    kind, payload = parse_model_output(raw)
    assert kind == "retry"


def test_final_without_plan_returns_retry() -> None:
    raw = "<final>some prose without plan block</final>"
    kind, payload = parse_model_output(raw)
    assert kind == "retry"
    assert "<plan>" in payload


def test_no_tags_returns_retry() -> None:
    raw = "I think we should try X"
    kind, payload = parse_model_output(raw)
    assert kind == "retry"


def test_canonical_render_empty_args() -> None:
    assert (
        canonical_tool_render("profile_dataset", {})
        == '<tool name="profile_dataset"><args>{}</args></tool>'
    )


def test_canonical_render_json_args_sorted() -> None:
    out = canonical_tool_render("rank_bottlenecks", {"top_k": 3, "scope": "all"})
    assert out == (
        '<tool name="rank_bottlenecks"><args>'
        '{"scope": "all", "top_k": 3}</args></tool>'
    )


def test_canonical_render_body_field_keeps_raw_text() -> None:
    blocks = "<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n"
    out = canonical_tool_render(
        "block_patch", {"blocks": blocks}, body_field="blocks"
    )
    assert out == f'<tool name="block_patch"><blocks>{blocks}</blocks></tool>'
    assert "<<<<<<< SEARCH" in out  # unescaped


def test_canonical_render_body_field_with_extra_args() -> None:
    out = canonical_tool_render(
        "x", {"blocks": "raw", "label": "v3"}, body_field="blocks"
    )
    assert out == (
        '<tool name="x"><blocks>raw</blocks>'
        '<args>{"label": "v3"}</args></tool>'
    )


def test_parse_then_render_round_trips_args_json_form() -> None:
    raw = '<intent>x</intent><tool name="rank"><args>{"top_k": 5}</args></tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    rendered = canonical_tool_render(payload["name"], payload["args"])
    kind2, payload2 = parse_model_output("<intent>x</intent>" + rendered)
    assert kind2 == "tool"
    assert payload2 == payload


def test_tool_before_final_picks_tool() -> None:
    raw = _INTENT + '<tool>{"name":"profile_dataset","args":{}}</tool>then <final><plan>{}</plan></final>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload["name"] == "profile_dataset"


# ---- Enhanced retry notices: JSON error detail + position ----------------


def test_json_tool_malformed_includes_position_hint():
    raw = '<intent>x</intent><tool>{"name":"x", "args":{bad}}</tool>'
    kind, notice = parse_model_output(raw)
    assert kind == "retry"
    assert "line" in notice and "col" in notice
    # The offending fragment should be quoted back so the model sees its own typo.
    assert "bad" in notice


def test_args_xml_malformed_includes_position_hint():
    raw = '<intent>x</intent><tool name="rank"><args>{"top_k": ,}</args></tool>'
    kind, notice = parse_model_output(raw)
    assert kind == "retry"
    assert "line" in notice and "col" in notice
    assert "top_k" in notice


# ---- P0: raw-body tools must use subtag, not <args> JSON ------------------


def test_block_patch_xml_with_args_json_wrapper_is_rejected_with_targeted_hint():
    # Model put SEARCH/REPLACE into <args>{"blocks": "..."} instead of <blocks>
    # subtag. Parser must reject early with form-switching guidance — NOT a
    # generic "fix your JSON escaping" message.
    raw = (
        _INTENT
        + '<tool name="block_patch"><args>{"blocks": "<<<<<<< SEARCH\\nold\\n=======\\nnew\\n>>>>>>> REPLACE\\n"}</args></tool>'
    )
    kind, notice = parse_model_output(raw)
    assert kind == "retry"
    # Targeted hint: name the tool and the correct subtag, not just "malformed JSON".
    assert "block_patch" in notice
    assert "<blocks>" in notice
    assert "raw" in notice.lower()
    # Failure-type / tool-name metadata available for P1 telemetry.
    assert getattr(notice, "failure_type", None) == "wrong_wire"
    assert getattr(notice, "tool_name", None) == "block_patch"


def test_block_patch_json_tool_form_also_rejected_with_targeted_hint():
    # Same problem via the top-level JSON tool form:
    # <tool>{"name":"block_patch", "args":{"blocks":"..."}}</tool>
    raw = _INTENT + '<tool>{"name":"block_patch","args":{"blocks":"<<<<<<< SEARCH\\nx"}}</tool>'
    kind, notice = parse_model_output(raw)
    assert kind == "retry"
    assert "block_patch" in notice
    assert "<blocks>" in notice
    assert getattr(notice, "failure_type", None) == "wrong_wire"


def test_block_patch_with_correct_blocks_subtag_still_parses():
    # Regression guard: the wrong-wire check must not fire when the model
    # uses the correct raw subtag.
    raw = (
        _INTENT
        + '<tool name="block_patch"><blocks>\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n</blocks></tool>'
    )
    kind, payload = parse_model_output(raw)
    assert kind == "retry" or kind == "tool"
    # Specifically: not the wrong-wire path
    if kind == "retry":
        assert getattr(payload, "failure_type", None) != "wrong_wire"
    else:
        assert payload["name"] == "block_patch"
        assert "<<<<<<< SEARCH" in payload["args"]["blocks"]


def test_retry_notice_carries_failure_type_metadata():
    # Generic JSON malformed → failure_type=json_decode
    raw = _INTENT + '<tool name="rank"><args>{not json}</args></tool>'
    kind, notice = parse_model_output(raw)
    assert kind == "retry"
    assert getattr(notice, "failure_type", None) == "json_decode"
    assert getattr(notice, "tool_name", None) == "rank"


def test_plan_malformed_includes_position_hint():
    raw = '<final><plan>{"mechanism": "sort_anchor",}</plan></final>'
    kind, notice = parse_model_output(raw)
    assert kind == "retry"
    assert "line" in notice and "col" in notice
    assert "sort_anchor" in notice
