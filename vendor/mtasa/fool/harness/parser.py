from __future__ import annotations

import json
import re
from typing import Any


def extract_intent(raw: str) -> str:
    """Return the trimmed text of the first <intent>...</intent>, or ''.

    Intent is per-step narration the model writes before its tool/final tag.
    Parsing here only reads it for logging — it is not stripped from the raw
    assistant message, so the LLM's next turn still sees its own prior intent.
    """
    return _extract(str(raw), "intent")


def parse_model_output(raw: str) -> tuple[str, Any]:
    """Return ('tool', {name, args}), ('final', {plan}), or ('retry', notice).

    Tool calls take precedence when both <tool> and <final> appear.
    ``<intent>`` is strongly recommended (per OUTPUT_RULES) but no longer
    blocks parsing — a missing intent is surfaced by the runner via a
    non-blocking on_step event so the model does not burn a turn just to
    re-emit narration. Production telemetry showed ~50% of retries were
    pure intent-missing rejections that added no safety value.
    """
    raw = str(raw)
    tool_pos = raw.find("<tool>")
    tool_xml_pos = raw.find("<tool ")
    final_pos = raw.find("<final>")

    earliest_tool = _first_nonneg(tool_pos, tool_xml_pos)

    if earliest_tool != -1 and (final_pos == -1 or earliest_tool < final_pos):
        # Prefer JSON form if both appear; pick whichever opener came first.
        if tool_pos != -1 and (tool_xml_pos == -1 or tool_pos <= tool_xml_pos):
            return _parse_json_tool(raw)
        return _parse_xml_tool(raw)

    if final_pos != -1:
        return _parse_final(raw)

    if raw.strip():
        return "retry", _retry_notice(
            "model returned no <tool> or <final> tag", failure_type="no_tag"
        )
    return "retry", _retry_notice(
        "model returned an empty response", failure_type="empty_response"
    )


def _first_nonneg(*values: int) -> int:
    positives = [v for v in values if v >= 0]
    return min(positives) if positives else -1


# Tools whose payload is large free-form text and must arrive as a raw
# subtag, not JSON-encoded inside <args>. Kept here (not in tools.py) so the
# parser has zero coupling with the registry. Each entry maps tool name →
# the required body-field subtag.
RAW_BODY_TOOLS: dict[str, str] = {
    "block_patch": "blocks",
}


class _RetryNotice(str):
    """str subclass that also carries failure-type + tool-name metadata.

    Existing call sites and tests treat the notice as a plain str (substring
    checks, message bodies). Runner reads ``.failure_type`` / ``.tool_name``
    via getattr so it can bucket malformed events for P1 telemetry without
    breaking the parser's return type.
    """

    failure_type: str
    tool_name: str | None

    def __new__(
        cls,
        text: str,
        *,
        failure_type: str = "other",
        tool_name: str | None = None,
    ) -> "_RetryNotice":
        obj = str.__new__(cls, text)
        obj.failure_type = failure_type
        obj.tool_name = tool_name
        return obj


def _retry_notice(
    problem: str,
    *,
    failure_type: str = "other",
    tool_name: str | None = None,
) -> _RetryNotice:
    return _RetryNotice(
        f"Runtime notice: {problem}. Reply with a valid "
        '<tool name="..."><args>{...}</args></tool> call '
        "or a <final><plan>{...}</plan></final> answer.",
        failure_type=failure_type,
        tool_name=tool_name,
    )


def _wrong_wire_notice(name: str, body_field: str) -> _RetryNotice:
    """Targeted retry for tools that received JSON-wrapped body instead of raw subtag.

    The generic 'malformed JSON' message often pushes the model to fix JSON
    escaping rather than switch form. This message tells it the right shape
    directly, with one positive example.
    """
    return _RetryNotice(
        f"Runtime notice: tool '{name}' does not accept <args>{{\"{body_field}\": ...}} — "
        f"its payload must arrive as a raw <{body_field}>...</{body_field}> subtag "
        "(no JSON wrapping, no escaping). Correct shape:\n"
        f'<tool name="{name}"><{body_field}>\n'
        "...raw multi-line content here, write it literally...\n"
        f"</{body_field}></tool>\n"
        "Re-emit the call in that form.",
        failure_type="wrong_wire",
        tool_name=name,
    )


def _json_error_detail(body: str, exc: json.JSONDecodeError) -> str:
    """Render a JSONDecodeError with the offending fragment for retry hints."""
    lineno = getattr(exc, "lineno", 1)
    colno = getattr(exc, "colno", 1)
    msg = getattr(exc, "msg", str(exc))
    pos = getattr(exc, "pos", 0)
    lo = max(0, pos - 40)
    hi = min(len(body), pos + 40)
    fragment = body[lo:hi].replace("\n", "\\n")
    caret_offset = pos - lo
    caret = " " * max(0, caret_offset) + "^"
    return (
        f"{msg} at line {lineno} col {colno}; near: {fragment!r}\n"
        f"                                {caret}"
    )


def _extract(text: str, tag: str) -> str:
    start_tag = f"<{tag}>"
    end_tag = f"</{tag}>"
    start = text.find(start_tag)
    if start == -1:
        return ""
    start += len(start_tag)
    end = text.find(end_tag, start)
    if end == -1:
        return text[start:].strip()
    return text[start:end].strip()


def _extract_raw(text: str, tag: str) -> str:
    start_tag = f"<{tag}>"
    end_tag = f"</{tag}>"
    start = text.find(start_tag)
    if start == -1:
        return ""
    start += len(start_tag)
    end = text.find(end_tag, start)
    if end == -1:
        return text[start:]
    return text[start:end]


def _parse_json_tool(raw: str) -> tuple[str, Any]:
    body = _extract(raw, "tool")
    # Peek the tool name before JSON parse so a wrong-wire payload for
    # raw-body tools (block_patch) gets the targeted hint instead of a
    # generic "fix your JSON escaping" message.
    name_peek = _peek_tool_name(body)
    if name_peek in RAW_BODY_TOOLS:
        return "retry", _wrong_wire_notice(name_peek, RAW_BODY_TOOLS[name_peek])
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return "retry", _retry_notice(
            f"model returned malformed tool JSON: {_json_error_detail(body, exc)}",
            failure_type="json_decode",
            tool_name=name_peek,
        )
    except Exception as exc:  # noqa: BLE001
        return "retry", _retry_notice(
            f"model returned malformed tool JSON: {exc}",
            failure_type="json_decode",
            tool_name=name_peek,
        )
    if not isinstance(payload, dict):
        return "retry", _retry_notice(
            "tool payload must be a JSON object", failure_type="payload_not_object"
        )
    name = str(payload.get("name", "")).strip()
    if not name:
        return "retry", _retry_notice(
            "tool payload is missing 'name'", failure_type="missing_name"
        )
    args = payload.get("args", {})
    if args is None:
        payload["args"] = {}
    elif not isinstance(args, dict):
        return "retry", _retry_notice(
            "tool 'args' must be a JSON object",
            failure_type="args_not_object",
            tool_name=name,
        )
    return "tool", payload


_NAME_KEY_RE = re.compile(r'"name"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"')


def _peek_tool_name(body: str) -> str | None:
    """Best-effort tool name extraction from a (possibly malformed) JSON body."""
    m = _NAME_KEY_RE.search(body)
    return m.group(1) if m else None


_TOOL_OPEN_RE = re.compile(r"<tool(?P<attrs>[^>]*)>(?P<body>.*?)</tool>", re.S)
_ATTR_RE = re.compile(
    r"""([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)


def _parse_xml_tool(raw: str) -> tuple[str, Any]:
    match = _TOOL_OPEN_RE.search(raw)
    if not match:
        return "retry", _retry_notice(
            "xml tool tag did not close", failure_type="xml_unclosed"
        )
    attrs = {
        m.group(1): m.group(2) if m.group(2) is not None else m.group(3)
        for m in _ATTR_RE.finditer(match.group("attrs"))
    }
    name = str(attrs.pop("name", "")).strip()
    if not name:
        return "retry", _retry_notice(
            "xml tool missing name=", failure_type="missing_name"
        )

    body = match.group("body")

    # Wrong-wire guard: raw-body tools (block_patch) must use <{field}> subtag,
    # not <args>{"<field>": ...}. Stuffing SEARCH/REPLACE blocks into JSON is
    # the dominant source of malformed-JSON retries — reject early with a
    # form-switching hint instead of a generic JSON-escape complaint.
    if name in RAW_BODY_TOOLS:
        required_field = RAW_BODY_TOOLS[name]
        if f"<{required_field}>" not in body:
            return "retry", _wrong_wire_notice(name, required_field)

    args: dict[str, Any] = dict(attrs)
    _KNOWN_SUBTAGS = ("args", "content", "query", "code", "patch", "blocks", "label")
    for key in ("content", "query", "code", "patch", "blocks", "label"):
        if f"<{key}>" in body:
            args[key] = _extract_raw(body, key)

    # Reject unknown subtags. Previously these were silently dropped, which let
    # the model invent per-arg subtag forms like
    #   <tool name="read_tool_result"><uuid>...</uuid><start_line>72</start_line></tool>
    # producing args={}, which then triggered downstream auto-correct paths
    # (e.g. read_tool_result "latest spill" fallback) → wrong file → dead-loop.
    # Strip known-subtag regions first so raw multi-line bodies that legitimately
    # contain "<word>" text don't trigger false positives.
    scan_body = body
    for _kt in _KNOWN_SUBTAGS:
        scan_body = re.sub(rf"<{_kt}>.*?</{_kt}>", "", scan_body, flags=re.S)
    unknown = sorted({
        m.group(1) for m in re.finditer(r"<([A-Za-z_][A-Za-z0-9_]*)>", scan_body)
    })
    if unknown:
        first = unknown[0]
        return "retry", _retry_notice(
            f"tool '{name}' received unknown subtag(s) {unknown}; "
            f"pass scalar args as <args>{{\"{first}\": ...}}</args> JSON, "
            f"not <{first}>...</{first}> subtags",
            failure_type="unknown_subtag",
            tool_name=name,
        )

    if "<args>" in body:
        args_body = _extract(body, "args").strip()
        if args_body:
            try:
                parsed_args = json.loads(args_body)
            except json.JSONDecodeError as exc:
                return "retry", _retry_notice(
                    f"<args> body did not parse as JSON: {_json_error_detail(args_body, exc)}",
                    failure_type="json_decode",
                    tool_name=name,
                )
            except Exception as exc:  # noqa: BLE001
                return "retry", _retry_notice(
                    f"<args> body did not parse as JSON: {exc}",
                    failure_type="json_decode",
                    tool_name=name,
                )
            if not isinstance(parsed_args, dict):
                return "retry", _retry_notice(
                    "<args> JSON must be an object",
                    failure_type="args_not_object",
                    tool_name=name,
                )
            for k, v in parsed_args.items():
                args.setdefault(k, v)

    body_text = body.strip("\n")
    if name == "draft_solver" and "content" not in args and "code" not in args and body_text:
        args["code"] = body_text
    return "tool", {"name": name, "args": args}


def canonical_tool_render(
    name: str,
    args: dict[str, Any] | None,
    *,
    body_field: str | None = None,
) -> str:
    """Render a tool call to one canonical XML form.

    Two shapes, chosen by ``body_field``:
      - body_field set + that arg present → raw subtag form:
            <tool name="x"><{field}>...raw text...</{field}></tool>
        Remaining args (if any) go into an inner <args>{json}</args>.
      - otherwise → JSON args form:
            <tool name="x"><args>{pretty json}</args></tool>

    Used by dialog_writer to log a single uniform representation regardless
    of which surface form the model emitted.
    """
    args = dict(args or {})
    if body_field and body_field in args:
        raw_body = str(args.pop(body_field))
        inner = f"<{body_field}>{raw_body}</{body_field}>"
        if args:
            inner += "<args>" + _stable_json(args) + "</args>"
        return f'<tool name="{name}">{inner}</tool>'
    if not args:
        return f'<tool name="{name}"><args>{{}}</args></tool>'
    return f'<tool name="{name}"><args>{_stable_json(args)}</args></tool>'


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _parse_final(raw: str) -> tuple[str, Any]:
    final_body = _extract(raw, "final")
    if not final_body:
        return "retry", _retry_notice(
            "model returned an empty <final> answer", failure_type="final_empty"
        )
    plan_text = _extract_raw(final_body, "plan").strip()
    if not plan_text:
        return "retry", _retry_notice(
            "<final> must contain a <plan>{...}</plan> JSON block",
            failure_type="final_missing_plan",
        )
    try:
        plan = json.loads(plan_text)
    except json.JSONDecodeError as exc:
        return "retry", _retry_notice(
            f"<plan> JSON did not parse: {_json_error_detail(plan_text, exc)}",
            failure_type="plan_json_decode",
        )
    except Exception as exc:  # noqa: BLE001
        return "retry", _retry_notice(
            f"<plan> JSON did not parse: {exc}", failure_type="plan_json_decode"
        )
    if not isinstance(plan, dict):
        return "retry", _retry_notice(
            "<plan> must be a JSON object", failure_type="plan_not_object"
        )
    return "final", {"plan": plan}
