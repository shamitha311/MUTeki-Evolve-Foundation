from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    import threading

    from fool.harness.session_compactor import SessionCompactor
    from fool.memory_notes import MemoryNotesStore

from fool.harness.context import HarnessAborted, HarnessFailure, HarnessResult, RoundState
from fool.harness.model_client import ModelClient
from fool.harness.parser import (
    canonical_tool_render,
    extract_intent,
    parse_model_output,
)
from fool.harness.prompt import (
    build_prefix,
    build_round_header,
    format_tool_user_message,
)
from fool.harness.session import HarnessSession
from fool.harness.dialog_writer import DialogWriter
from fool.harness.tools import ToolContext, ToolRegistry, build_default_registry


StepCallback = Callable[[str, dict[str, Any]], None]


# Tools whose successful invocation invalidates any previous smoke check.
# After one of these, the model must call smoke_test_solver again before
# emitting <final>. This is the one remaining hard gate — the rest
# (forced_exploration / class_dedup / stagnation_novelty) were removed
# because they were over-engineered and overlapped with each other; review
# is now an out-of-loop periodic step (see fool/harness/teacher_review.py).
_EDIT_TOOLS_REQUIRING_SMOKE = {"block_patch", "restore_draft"}


def _latest_turn_for_log(messages: list[dict[str, str]]) -> tuple[str, str]:
    """Return the latest message's (role, content) — what the model will read
    as the newest turn this step. Earlier turns are stable cached history and
    not worth re-emitting to the UI on every step."""
    if not messages:
        return ("user", "")
    last = messages[-1]
    return (str(last.get("role", "user")), str(last.get("content", "")))


# Deterministic guard: catch the case where the model's <intent> text names
# one solver version (e.g. "在 v002 基线基础上...") but the very same step
# calls restore_draft / read_version targeting a *different* v. Observed in
# run_20260605_075509 R3 step 1: intent said "v002 基线" then restore_draft
# label=v001, wasting ~25 subsequent steps before being noticed.
_VERSION_TARGET_TOOLS = {"restore_draft", "read_version"}
# Match v-numbers in free-form intent text: "v001", "v 2", "版本 3", "v33".
# Anchored to avoid matching "v" inside identifiers like "device".
_INTENT_VERSION_RE = re.compile(
    # NOTE: cannot use \b after the digits — re's \w is unicode-aware so Chinese
    # chars are word-chars; "v002基线" gives no boundary between "2" and "基".
    # Use a negative lookahead (?!\d) to just block consuming a longer number.
    r"(?:(?<![A-Za-z0-9_])v\s*0*(\d{1,3})(?!\d)|版本\s*0*(\d{1,3})(?!\d))",
    re.IGNORECASE,
)
# Match v-number in a tool arg value: "v002", "002", "33", "v33".
_ARG_VERSION_RE = re.compile(r"v?\s*0*(\d{1,3})", re.IGNORECASE)


def _intent_mentioned_versions(text: str) -> set[int]:
    out: set[int] = set()
    for m in _INTENT_VERSION_RE.finditer(text or ""):
        token = m.group(1) or m.group(2)
        if not token:
            continue
        try:
            out.add(int(token))
        except ValueError:
            continue
    return out


def _tool_target_version(tool_name: str, args: dict[str, Any]) -> int | None:
    """Best-effort extraction of the v-number a tool call targets. Returns
    None for symbolic targets ('latest', 'best', negative relatives) — we
    only flag mismatches where both sides are concrete integers."""
    if tool_name == "restore_draft":
        raw = args.get("label")
    elif tool_name == "read_version":
        raw = args.get("v")
    else:
        return None
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    text = str(raw).strip().lower()
    if not text or text in {"latest", "best"} or text.startswith("-"):
        return None
    m = _ARG_VERSION_RE.fullmatch(text) or _ARG_VERSION_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _check_intent_version_mismatch(
    intent_text: str, tool_name: str, args: dict[str, Any]
) -> str | None:
    if tool_name not in _VERSION_TARGET_TOOLS:
        return None
    if not intent_text:
        return None
    target = _tool_target_version(tool_name, args)
    if target is None:
        return None
    mentioned = _intent_mentioned_versions(intent_text)
    if not mentioned or target in mentioned:
        return None
    return (
        f"[guard] intent 提到版本 v{sorted(mentioned)} 但本步 {tool_name} "
        f"实际目标是 v{target:03d}。如确认要切到 v{target:03d} 请在下一步 "
        "intent 里说明；否则用正确的 v 重试。"
    )


def _smoke_required_notice() -> str:
    return (
        "Runtime notice: smoke validation required after edits. "
        "Run smoke_test_solver before <final>."
    )


# ---- pre-final consistency guard ---------------------------------------
#
# One-shot LLM review fired right before a <final> is accepted. Catches the
# three drift patterns observed in run_20260605_075509:
#   - intent_action: intent text contradicts the tool it dispatched
#   - hypothesis_diff: final plan's hypothesis/edit_plan doesn't match what
#     the block_patches actually did (R2 v002: claimed solo-first贪心 but
#     restore_draft wiped it before final)
#   - smoke_vs_genius: analysis cites a smoke number as evidence of
#     improvement over a Genius baseline (R3 v003: "smoke 874.99 < 886.94")
#
# Tolerant: any exception or unparseable response → guard is skipped, final
# is accepted as-is.

import json as _json

_FINAL_GUARD_SYSTEM = """你是一个**只读的提交前审查器**。你看到下面的内容：
1. 本轮模型给出的 final.plan（hypothesis / analysis / edit_plan）
2. 本轮按时间顺序的 <intent> 与 tool 调用摘要
3. 本轮 block_patch 实际改动摘要
4. 本轮 smoke_test 输出摘要
5. （可选）本轮开始时注入的 [Teacher Review] advice 块

判断 final.plan 是否如实反映了本轮实际发生的事，重点检查四类偏差：
- intent_action: 某个 <intent> 文本与紧随的 tool 调用语义不符
- hypothesis_diff: hypothesis / edit_plan 描述的机制与实际 block_patch 改动不一致
- smoke_vs_genius: analysis 把 smoke 预览分数和 Genius 历史分数混为同一标尺当作进步证据
- advice_violation: 当存在 [Teacher Review] 块时，本轮 hypothesis 落在 advice『已饱和方向』内
  且 analysis / edit_plan 未给出可证伪的反驳证据；或 hypothesis 与 advice 候选方向完全无关
  且未在 analysis 中显式说明为何否决全部候选。**没有 advice 块时不要触发此项。**

只输出严格 JSON：
{"consistent": true|false, "violations": [{"type":"<intent_action|hypothesis_diff|smoke_vs_genius|advice_violation|other>","detail":"<≤120字的中文说明>"}]}
不要任何 Markdown 围栏或解释文字。如无偏差，输出 {"consistent": true, "violations": []}。"""


def _summarize_round_for_guard(
    plan: dict[str, Any],
    transcript: list[dict[str, Any]],
    *,
    teacher_review_block: str | None = None,
) -> str:
    """Pack the round's intents + tool calls + key results into a short
    review payload. Caps each section so the guard call stays cheap."""
    intents: list[tuple[int, str]] = []
    tool_calls: list[tuple[int, str, str]] = []
    block_patches: list[str] = []
    smoke_lines: list[str] = []

    step_idx = 0
    for turn in transcript:
        role = turn.get("role")
        if role == "assistant":
            step_idx += 1
            content = str(turn.get("content", ""))
            m = re.search(r"<intent>(.*?)</intent>", content, re.S)
            if m:
                intents.append((step_idx, m.group(1).strip()[:300]))
        elif role == "tool":
            name = str(turn.get("name", ""))
            args = turn.get("args") or {}
            # Compact arg repr: skip large body fields.
            arg_repr = ", ".join(
                f"{k}={str(v)[:60]}"
                for k, v in args.items()
                if k not in {"blocks", "body", "code"}
            )
            tool_calls.append((step_idx, name, arg_repr))
            content = str(turn.get("content", ""))
            if name == "block_patch":
                # First line tells line ranges + delta.
                head = content.splitlines()[0] if content else ""
                block_patches.append(f"step{step_idx}: {head[:200]}")
            elif name == "smoke_test_solver":
                # Pull the avg_score line.
                for line in content.splitlines():
                    if "avg_score" in line or "local_preview" in line:
                        smoke_lines.append(f"step{step_idx}: {line.strip()[:200]}")
                        break

    sections: list[str] = []
    if teacher_review_block:
        sections.append("# Teacher Review (advice for THIS round)")
        sections.append(str(teacher_review_block)[:1600])
        sections.append("")
    sections.append("# final.plan")
    sections.append(f"hypothesis: {str(plan.get('hypothesis', ''))[:500]}")
    sections.append(f"analysis: {str(plan.get('analysis', ''))[:800]}")
    ep = plan.get("edit_plan") or []
    if ep:
        sections.append("edit_plan:")
        for item in ep[:8]:
            sections.append(f"  - {str(item)[:200]}")
    tb = plan.get("target_buckets") or []
    if tb:
        sections.append(f"target_buckets: {', '.join(str(x) for x in tb[:10])}")

    sections.append("\n# Intents & tool calls (chronological)")
    # Interleave by step.
    intent_map = dict(intents)
    for step_idx_inner, name, arg_repr in tool_calls[:30]:
        intent_str = intent_map.get(step_idx_inner, "(no intent)")
        sections.append(f"step{step_idx_inner}: intent={intent_str[:160]}")
        sections.append(f"  → tool={name}({arg_repr[:160]})")

    if block_patches:
        sections.append("\n# block_patch results")
        sections.extend(block_patches[:10])
    if smoke_lines:
        sections.append("\n# smoke_test outputs")
        sections.extend(smoke_lines[:10])

    return "\n".join(sections)


def _parse_guard_verdict(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = _json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _run_final_guard(
    model: "ModelClient",
    plan: dict[str, Any],
    transcript: list[dict[str, Any]],
    *,
    max_tokens: int,
    teacher_review_block: str | None = None,
) -> dict[str, Any]:
    """Returns {"status": "ok|violations|skipped|error", "violations": [...],
    "raw": str, "detail": str}. Never raises."""
    try:
        payload = _summarize_round_for_guard(
            plan, transcript, teacher_review_block=teacher_review_block
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "skipped", "violations": [], "raw": "", "detail": f"build_failed: {exc}"}
    msgs = [
        {"role": "system", "content": _FINAL_GUARD_SYSTEM},
        {"role": "user", "content": payload},
    ]
    try:
        raw = model.complete(msgs, max_tokens)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "violations": [], "raw": "", "detail": f"{type(exc).__name__}: {exc}"}
    verdict = _parse_guard_verdict(raw)
    if verdict is None:
        return {"status": "skipped", "violations": [], "raw": str(raw)[:200], "detail": "parse_failed"}
    if verdict.get("consistent") is True:
        return {"status": "ok", "violations": [], "raw": str(raw)[:200], "detail": ""}
    violations = verdict.get("violations") or []
    if not isinstance(violations, list):
        violations = []
    return {"status": "violations", "violations": violations, "raw": str(raw)[:200], "detail": ""}


def _format_guard_feedback(violations: list[Any]) -> str:
    lines = [
        "[final_guard] 你的 <final> 暂未通过提交前一致性审查；",
        "请先修正下列问题再重新输出 <final>（必要时可继续调用 tools 改正）：",
    ]
    for v in violations[:5]:
        if isinstance(v, dict):
            vtype = str(v.get("type", "other"))
            detail = str(v.get("detail", "")).strip()
            lines.append(f"  - [{vtype}] {detail[:240]}")
        else:
            lines.append(f"  - {str(v)[:240]}")
    return "\n".join(lines)


def run_round(
    state: RoundState,
    model: ModelClient,
    *,
    registry: ToolRegistry | None = None,
    tool_context_factory=None,
    max_steps: int = 50,
    max_tokens: int = 4096,
    on_step: StepCallback | None = None,
    compactor: "SessionCompactor | None" = None,
    memory_notes: "MemoryNotesStore | None" = None,
    judge_model: ModelClient | None = None,  # used by pre-final consistency guard
    stop_event: "threading.Event | None" = None,
    teacher_review_block: str | None = None,
    # Pre-final consistency guard is opt-in (default 0 = disabled) so existing
    # tests with scripted FakeModelClient outputs aren't disturbed. fool_loop
    # turns it on with =1 for real runs.
    final_guard_max_attempts: int = 0,
    final_guard_max_tokens: int = 800,
) -> HarnessResult:
    """Drive one Fool round to a final solver via tool-calling.

    Multi-turn structure: system=prefix (stable, cacheable), user=round header,
    then assistant/user turns for each tool call. Messages are append-only so
    the prefix-cache hit rate grows with the conversation. Tool outputs are
    already size-clipped at the source in tools.py.

    The only in-loop gate is `smoke_required`: after a successful block_patch
    or restore_draft, the model must call smoke_test_solver before <final>.
    All other dedup / novelty / exploration enforcement was removed; see
    `teacher_review.py` and `fool_loop.py`'s duplicate guard for the
    replacements.

    Raises HarnessFailure if the round cannot produce a valid solver.
    """
    registry = registry or build_default_registry()
    if tool_context_factory is not None:
        tool_context = tool_context_factory(state)
    else:
        tool_context = _default_tool_context(state)
    if memory_notes is not None and tool_context.memory_notes is None:
        from dataclasses import replace as _dc_replace
        tool_context = _dc_replace(tool_context, memory_notes=memory_notes)

    # Auto-seed draft.py from the incumbent (or bootstrap/template) so the
    # round starts with a usable working copy. block_patch is then the only
    # way for the model to mutate it — draft_solver is no longer registered.
    draft_path = state.run_dir / "draft.py"
    if not draft_path.exists():
        for candidate in (
            state.best_solver_path,
            state.bootstrap_solver_path,
            Path(__file__).resolve().parents[1] / "templates" / "solver_greedy.py",
        ):
            if candidate and candidate.exists():
                draft_path.parent.mkdir(parents=True, exist_ok=True)
                draft_path.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
                break

    session = HarnessSession(run_dir=state.run_dir, iteration=state.iteration)
    prefix = build_prefix(registry)
    memory_index_path = memory_notes.index_path if memory_notes is not None else None
    round_header = build_round_header(
        state,
        memory_index_path=memory_index_path,
        teacher_review_block=teacher_review_block,
    )
    session.record_user(round_header)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": prefix},
        {"role": "user", "content": round_header},
    ]
    dialog = DialogWriter(run_dir=state.run_dir, round_idx=state.iteration)
    for m in messages:
        dialog.append(m)

    def _append(m: dict) -> None:
        messages.append(m)
        dialog.append(m)

    # smoke gate notice is the only same-round notice; dedupe its full text
    # so repeated firings collapse to a one-line stub.
    notices_shown: set[str] = set()

    def _inject_gate_notice(label: str, full_text: str) -> None:
        if label in notices_shown:
            body = f"(runtime notice: {label} gate still active; condition not yet resolved)"
        else:
            notices_shown.add(label)
            body = full_text
        if on_step is not None:
            on_step("retry", {"step": total_steps, "message": body})
        _append({"role": "user", "content": body})

    previous_summary: str = ""

    tool_steps = 0
    total_steps = 0
    malformed = 0
    # P1 telemetry: bucket malformed events by (failure_type, tool_name) so
    # post-run grep on fool.log reveals whether retries cluster on one tool
    # (e.g. block_patch wrong_wire) vs spread across plan/final/no_tag.
    malformed_by: dict[tuple[str, str | None], int] = {}
    # Hard cap on the round: total_steps (tool calls + malformed retries
    # combined) must not exceed max_steps. malformed cap is set equal so the
    # standalone "max_malformed_exceeded" failure path stays reachable when
    # the round is consumed entirely by retries.
    max_malformed = max_steps
    last_plan: dict[str, Any] | None = None
    pending_smoke_validation = False
    final_guard_attempts = 0
    # Notice fires in the last ~quarter of the budget so it scales with
    # max_steps (e.g. max_steps=25 → last 6; max_steps=8 → last 2).
    budget_notice_threshold = max(2, max_steps // 4)

    while total_steps < max_steps:
        remaining_steps = max_steps - total_steps
        if 0 < remaining_steps <= budget_notice_threshold and total_steps > 0:
            budget_notice = (
                f"Runtime notice: only {remaining_steps} step(s) remain before this "
                f"round's budget ({max_steps}) is exhausted (counts tool calls AND "
                "malformed retries). Stop exploring — emit <final> now with the current "
                "draft. If you keep calling tools you will hit max_steps and the round "
                "will fail with no solver submitted."
            )
            if on_step is not None:
                on_step("retry", {"step": total_steps, "message": budget_notice})
            _append({"role": "user", "content": budget_notice})
        if stop_event is not None and stop_event.is_set():
            raise HarnessAborted(
                "stopped_by_user: external stop signal received mid-round",
                transcript_path=session.path,
            )
        if on_step is not None:
            turn_role, turn_content = _latest_turn_for_log(messages)
            on_step(
                "llm_in",
                {
                    "step": total_steps + 1,
                    "prompt": turn_content,
                    "turn_role": turn_role,
                    "max_tokens": max_tokens,
                },
            )
        if compactor is not None:
            messages, previous_summary = compactor.maybe_compact(messages, previous_summary)
        raw = model.complete(messages, max_tokens)
        session.record_assistant(raw)
        total_steps += 1
        if on_step is not None:
            usage_payload: dict[str, Any] = {"step": total_steps, "raw": raw}
            last_response = getattr(model, "last_response", None)
            if last_response is not None:
                usage_payload["prompt_tokens"] = getattr(last_response, "prompt_tokens", 0)
                usage_payload["completion_tokens"] = getattr(last_response, "completion_tokens", 0)
                usage_payload["cached_tokens"] = getattr(last_response, "cached_tokens", 0)
            on_step("llm_out", usage_payload)

        _append({"role": "assistant", "content": raw})

        intent_text = extract_intent(raw)
        if on_step is not None:
            if intent_text:
                on_step("intent", {"step": total_steps, "text": intent_text})
            else:
                on_step("intent_missing", {"step": total_steps})

        kind, payload = parse_model_output(raw)

        # Enforce intent-before-action: a tool call or <final> without a
        # preceding <intent> is treated as malformed. R2/R3 of
        # run_20260605_075509 burned ~30 steps in a row without intent —
        # silent telemetry alone wasn't enough; making it cost step budget
        # forces the model to write the one-sentence rationale up front.
        if kind in ("tool", "final") and not intent_text:
            malformed += 1
            failure_type = "intent_missing"
            malformed_tool = payload["name"] if kind == "tool" else "<final>"
            malformed_by[(failure_type, malformed_tool)] = (
                malformed_by.get((failure_type, malformed_tool), 0) + 1
            )
            notice = (
                "你这一步没有 <intent>。runtime 拒绝执行本步。"
                "请补一句简短中文 <intent>（≤300 字，写'做什么 / 为什么 / "
                "期望信号'），紧接其后**重新**发出原本的工具调用或 <final>；"
                "若本步是 <final>，intent 里要说明这次 final 想交付什么结论。"
            )
            if on_step is not None:
                on_step(
                    "retry",
                    {
                        "step": total_steps,
                        "message": notice,
                        "failure_type": failure_type,
                        "tool_name": malformed_tool,
                    },
                )
            if malformed >= max_malformed:
                if on_step is not None:
                    on_step(
                        "malformed_summary",
                        {
                            "total": malformed,
                            "by_bucket": {
                                f"{ft}|{tn or '-'}": n
                                for (ft, tn), n in sorted(malformed_by.items())
                            },
                            "reason": "max_malformed_exceeded",
                        },
                    )
                raise HarnessFailure(
                    f"malformed: too many invalid model outputs ({malformed})",
                    transcript_path=session.path,
                )
            _append({"role": "user", "content": notice})
            continue

        if kind == "retry":
            malformed += 1
            failure_type = getattr(payload, "failure_type", "other")
            tool_name = getattr(payload, "tool_name", None)
            malformed_by[(failure_type, tool_name)] = (
                malformed_by.get((failure_type, tool_name), 0) + 1
            )
            notice = str(payload)
            if intent_text:
                # Feed the model's own stated intent back so the retry turn can
                # re-emit the intended tool call instead of guessing what shape
                # the runtime wants.
                notice = (
                    f"{notice}\n\nYour <intent> was: {intent_text[:300]}\n"
                    "Re-emit a valid tool call matching that intent."
                )
            if on_step is not None:
                on_step(
                    "retry",
                    {
                        "step": total_steps,
                        "message": notice,
                        "failure_type": failure_type,
                        "tool_name": tool_name,
                    },
                )
            if malformed >= max_malformed:
                if on_step is not None:
                    on_step(
                        "malformed_summary",
                        {
                            "total": malformed,
                            "by_bucket": {
                                f"{ft}|{tn or '-'}": n
                                for (ft, tn), n in sorted(malformed_by.items())
                            },
                            "reason": "max_malformed_exceeded",
                        },
                    )
                raise HarnessFailure(
                    f"malformed: too many invalid model outputs ({malformed})",
                    transcript_path=session.path,
                )
            _append({"role": "user", "content": notice})
            continue

        if kind == "tool":
            name = payload["name"]
            args = payload.get("args", {}) or {}

            spec = registry.get_spec(name)
            body_field = spec.body_field if spec is not None else None
            canonical_call = canonical_tool_render(name, args, body_field=body_field)
            dialog.append(
                {
                    "role": "tool_call",
                    "name": name,
                    "args": args,
                    "canonical": canonical_call,
                }
            )

            mismatch_notice = _check_intent_version_mismatch(intent_text, name, args)

            result = registry.run(name, tool_context, args)
            session.record_tool(
                name=name,
                args=args,
                ok=result.ok,
                content=result.content,
                canonical_call=canonical_call,
            )
            tool_user_text = format_tool_user_message(
                name=name,
                args=args,
                ok=result.ok,
                content=result.content,
            )
            if mismatch_notice:
                tool_user_text = f"{mismatch_notice}\n\n{tool_user_text}"
                if on_step is not None:
                    on_step(
                        "intent_action_mismatch",
                        {
                            "step": total_steps,
                            "tool": name,
                            "target_v": _tool_target_version(name, args),
                            "intent_versions": sorted(
                                _intent_mentioned_versions(intent_text)
                            ),
                            "intent": intent_text[:200],
                        },
                    )
            _append({"role": "user", "content": tool_user_text})
            tool_steps += 1
            if on_step is not None:
                on_step(
                    "tool",
                    {
                        "step": total_steps,
                        "tool_step": tool_steps,
                        "name": name,
                        "args": args,
                        "ok": result.ok,
                        "content": result.content,
                    },
                )

            if result.ok and name in _EDIT_TOOLS_REQUIRING_SMOKE:
                pending_smoke_validation = True
            elif result.ok and name == "smoke_test_solver":
                pending_smoke_validation = False
            continue

        if kind == "final":
            if pending_smoke_validation:
                malformed += 1
                if malformed >= max_malformed:
                    raise HarnessFailure(
                        f"smoke_gate: too many invalid steps ({malformed})",
                        transcript_path=session.path,
                    )
                _inject_gate_notice("smoke_gate", _smoke_required_notice())
                continue

            draft_path = state.run_dir / "draft.py"
            if not draft_path.exists():
                raise HarnessFailure(
                    "final emitted but no draft on disk; call draft_solver first",
                    transcript_path=session.path,
                )
            solver_code = draft_path.read_text(encoding="utf-8")

            last_plan = dict(payload["plan"])

            # Pre-final consistency guard: one LLM call (judge model if
            # available, else main model) that flags hypothesis/diff drift,
            # intent/action contradictions, and smoke-vs-Genius confusion. If
            # it fires we inject the violations as a user message and let the
            # model re-emit <final>; capped at final_guard_max_attempts so a
            # stuck model doesn't loop forever.
            if final_guard_attempts < final_guard_max_attempts:
                guard_model = judge_model or model
                guard_result = _run_final_guard(
                    guard_model,
                    last_plan,
                    session.transcript(),
                    max_tokens=final_guard_max_tokens,
                    teacher_review_block=teacher_review_block,
                )
                if on_step is not None:
                    on_step(
                        "final_guard",
                        {
                            "step": total_steps,
                            "attempt": final_guard_attempts + 1,
                            "status": guard_result["status"],
                            "violations": guard_result["violations"],
                            "detail": guard_result.get("detail", ""),
                        },
                    )
                final_guard_attempts += 1
                if guard_result["status"] == "violations" and guard_result["violations"]:
                    feedback = _format_guard_feedback(guard_result["violations"])
                    _append({"role": "user", "content": feedback})
                    # Edits during the redo invalidate the prior smoke pass —
                    # gate it again so we don't bypass smoke validation.
                    pending_smoke_validation = False
                    continue

            session.record_final(solver_code=solver_code, plan=last_plan)
            if on_step is not None:
                if malformed > 0:
                    on_step(
                        "malformed_summary",
                        {
                            "total": malformed,
                            "by_bucket": {
                                f"{ft}|{tn or '-'}": n
                                for (ft, tn), n in sorted(malformed_by.items())
                            },
                            "reason": "round_complete",
                        },
                    )
                on_step("final", {"step": total_steps, "plan": last_plan})
            return HarnessResult(
                solver_code=solver_code,
                plan=last_plan,
                transcript_path=session.path,
                steps_taken=total_steps,
            )

    raise HarnessFailure(
        f"max_steps reached ({max_steps}) without a final solver",
        transcript_path=session.path,
    )


def _default_tool_context(state: RoundState) -> ToolContext:
    return ToolContext(
        input_dir=state.input_dir,
        run_dir=state.run_dir,
        best_solver_path=state.best_solver_path,
        best_report_path=state.best_report_path,
        last_report_path=_infer_last_report_path(state),
        bootstrap_solver_path=state.bootstrap_solver_path,
        durable_memory=None,
        dataset_profile_text="",
        run_id=state.run_dir.name,
        iteration=state.iteration,
    )


def _infer_last_report_path(state: RoundState) -> Path | None:
    if state.iteration <= 1:
        return None
    candidate = state.run_dir / f"report_v{state.iteration - 1:03d}.txt"
    return candidate if candidate.exists() else None
