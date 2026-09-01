# Fool Harness Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hard-coded `_reflect_and_plan` + `_propose_solver` pipeline in `fool/fool_loop.py` with a per-round LLM-driven harness session, so the LLM autonomously decides what context to read and how to draft the solver, while the outer loop shrinks to: iterate → submit to Genius → update best pointer → record a 3-line history.

**Architecture:** New `fool/harness/` package implements one harness session per Fool iteration: stable prefix (identity + hard constraints + tool specs only — no teacher text inlined), 11 structured tools the LLM may call (8 read-only, 3 editor), parser supporting both JSON and XML-style tool calls, JSON session store under `out/runs/<run_id>/harness_v{i:03d}.json`. Outer `run_fool_loop` keeps Genius submission and best-pointer logic; all other prior heuristics (change_ratio, strategy_lane, blocked_hypotheses, judge_fitter, precheck retries, catastrophic rollback retries) are deleted.

**Tech Stack:** Python stdlib (subprocess, json, urllib, pathlib, dataclasses, re), pytest under `genius/tests/`, existing `fool/agent_tools/` modules reused as backing implementations for read-only tools, existing `fool/llm_client.call_llm_meta` reused for the LLM transport.

**Spec:** `docs/superpowers/specs/2026-05-29-fool-harness-refactor-design.md`

**File map:**
- New `fool/harness/__init__.py` — public surface: `run_round`, `RoundState`, `HarnessResult`, `HarnessFailure`, `ModelClient`, `FakeModelClient`
- New `fool/harness/context.py` — dataclasses
- New `fool/harness/parser.py` — `<tool>` / `<final>` parsing
- New `fool/harness/session.py` — session JSON store
- New `fool/harness/tools.py` — `ToolSpec`, `ToolRegistry`, all 11 tools
- New `fool/harness/prompt.py` — `build_prefix`, `build_round_message`
- New `fool/harness/runner.py` — `run_round` main loop, `HarnessFailure`
- New `fool/harness/model_client.py` — `ModelClient` protocol, `LLMModelClient`, `FakeModelClient`
- Modify `fool/fool_loop.py` — delete ~17 helpers, rewrite main loop body
- New tests:
  - `genius/tests/test_harness_parser.py`
  - `genius/tests/test_harness_session.py`
  - `genius/tests/test_harness_tools.py`
  - `genius/tests/test_harness_runner.py`
  - `genius/tests/test_harness_fool_loop_integration.py`

---

## Task 1: ModelClient protocol and FakeModelClient

**Files:**
- Create: `fool/harness/model_client.py`
- Create: `genius/tests/test_harness_model_client.py`

The harness must not depend on a live API. Introduce a tiny `ModelClient` protocol — exactly one method, `complete(prompt: str, max_new_tokens: int) -> str` — and a `FakeModelClient` that returns scripted outputs. The real implementation wraps `fool.llm_client.call_llm_meta`.

- [ ] **Step 1: Write the failing tests**

```python
# genius/tests/test_harness_model_client.py
from __future__ import annotations

import pytest

from fool.harness.model_client import FakeModelClient


def test_fake_model_returns_scripted_outputs_in_order() -> None:
    fake = FakeModelClient(["one", "two"])
    assert fake.complete("p1", 100) == "one"
    assert fake.complete("p2", 100) == "two"
    assert fake.prompts == ["p1", "p2"]


def test_fake_model_raises_when_outputs_exhausted() -> None:
    fake = FakeModelClient([])
    with pytest.raises(RuntimeError, match="fake model ran out"):
        fake.complete("p", 100)


def test_fake_model_records_max_new_tokens() -> None:
    fake = FakeModelClient(["x"])
    fake.complete("p", 777)
    assert fake.last_max_new_tokens == 777
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest genius/tests/test_harness_model_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fool.harness'`

- [ ] **Step 3: Implement model_client.py**

```python
# fool/harness/model_client.py
from __future__ import annotations

from typing import Protocol

from fool.llm_client import call_llm_meta


class ModelClient(Protocol):
    def complete(self, prompt: str, max_new_tokens: int) -> str: ...


class FakeModelClient:
    """Scripted ModelClient for deterministic tests."""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []
        self.last_max_new_tokens: int | None = None

    def complete(self, prompt: str, max_new_tokens: int) -> str:
        self.prompts.append(prompt)
        self.last_max_new_tokens = max_new_tokens
        if not self.outputs:
            raise RuntimeError("fake model ran out of outputs")
        return self.outputs.pop(0)


class LLMModelClient:
    """ModelClient backed by fool.llm_client.call_llm_meta."""

    def __init__(
        self,
        *,
        api_type: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: int = 180,
        effort_level: str = "low",
    ) -> None:
        self.api_type = api_type
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.effort_level = effort_level

    def complete(self, prompt: str, max_new_tokens: int) -> str:
        response = call_llm_meta(
            api_type=self.api_type,
            api_key=self.api_key,
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            base_url=self.base_url,
            timeout=self.timeout,
            max_tokens=max_new_tokens,
            json_mode=False,
            effort_level=self.effort_level,
        )
        return response.text
```

Also create `fool/harness/__init__.py` with a single line:

```python
# fool/harness/__init__.py
from fool.harness.model_client import FakeModelClient, LLMModelClient, ModelClient

__all__ = ["FakeModelClient", "LLMModelClient", "ModelClient"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest genius/tests/test_harness_model_client.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/harness/__init__.py fool/harness/model_client.py genius/tests/test_harness_model_client.py
git commit -m "fool/harness: add ModelClient protocol + FakeModelClient"
```

---

## Task 2: Parser for `<tool>` and `<final>`

**Files:**
- Create: `fool/harness/parser.py`
- Create: `genius/tests/test_harness_parser.py`

Port mini-coding-agent's parser. Two output shapes are valid:
1. `<tool>{"name":"...","args":{...}}</tool>` — JSON tool call
2. `<tool name="write_file" path="x"><content>...</content></tool>` — XML-style for multi-line content
3. `<final>...</final>` — terminal answer; for our harness this body must contain a `<plan>{...}</plan>` JSON block

The parser returns a tagged result: `("tool", payload_dict)`, `("final", payload_dict)`, or `("retry", notice_str)`.

- [ ] **Step 1: Write the failing tests**

```python
# genius/tests/test_harness_parser.py
from __future__ import annotations

from fool.harness.parser import parse_model_output


def test_parses_json_tool_call() -> None:
    raw = '<tool>{"name":"read_teacher_checklist","args":{}}</tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload == {"name": "read_teacher_checklist", "args": {}}


def test_parses_json_tool_call_with_args() -> None:
    raw = '<tool>{"name":"rank_bottlenecks","args":{"top_k":3}}</tool>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload == {"name": "rank_bottlenecks", "args": {"top_k": 3}}


def test_parses_xml_tool_call_with_content() -> None:
    raw = (
        '<tool name="draft_solver">'
        "<content>def solve(t):\n    return []\n</content>"
        "</tool>"
    )
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload["name"] == "draft_solver"
    assert payload["args"]["content"] == "def solve(t):\n    return []\n"


def test_parses_final_with_plan() -> None:
    raw = '<final><plan>{"hypothesis":"x","analysis":"y"}</plan></final>'
    kind, payload = parse_model_output(raw)
    assert kind == "final"
    assert payload == {"plan": {"hypothesis": "x", "analysis": "y"}}


def test_malformed_tool_json_returns_retry() -> None:
    raw = "<tool>{not json}</tool>"
    kind, payload = parse_model_output(raw)
    assert kind == "retry"
    assert "malformed tool JSON" in payload


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


def test_tool_before_final_picks_tool() -> None:
    raw = '<tool>{"name":"profile_dataset","args":{}}</tool>then <final><plan>{}</plan></final>'
    kind, payload = parse_model_output(raw)
    assert kind == "tool"
    assert payload["name"] == "profile_dataset"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest genius/tests/test_harness_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement parser.py**

```python
# fool/harness/parser.py
from __future__ import annotations

import json
import re
from typing import Any


def parse_model_output(raw: str) -> tuple[str, Any]:
    """Return ('tool', {name, args}), ('final', {plan}), or ('retry', notice).

    Tool calls take precedence when both <tool> and <final> appear.
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
        return "retry", _retry_notice("model returned no <tool> or <final> tag")
    return "retry", _retry_notice("model returned an empty response")


def _first_nonneg(*values: int) -> int:
    positives = [v for v in values if v >= 0]
    return min(positives) if positives else -1


def _retry_notice(problem: str) -> str:
    return (
        f"Runtime notice: {problem}. Reply with a valid <tool>...</tool> call "
        "or a <final><plan>{...}</plan></final> answer."
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
    try:
        payload = json.loads(body)
    except Exception:
        return "retry", _retry_notice("model returned malformed tool JSON")
    if not isinstance(payload, dict):
        return "retry", _retry_notice("tool payload must be a JSON object")
    name = str(payload.get("name", "")).strip()
    if not name:
        return "retry", _retry_notice("tool payload is missing 'name'")
    args = payload.get("args", {})
    if args is None:
        payload["args"] = {}
    elif not isinstance(args, dict):
        return "retry", _retry_notice("tool 'args' must be a JSON object")
    return "tool", payload


_TOOL_OPEN_RE = re.compile(r"<tool(?P<attrs>[^>]*)>(?P<body>.*?)</tool>", re.S)
_ATTR_RE = re.compile(
    r"""([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)


def _parse_xml_tool(raw: str) -> tuple[str, Any]:
    match = _TOOL_OPEN_RE.search(raw)
    if not match:
        return "retry", _retry_notice("xml tool tag did not close")
    attrs = {
        m.group(1): m.group(2) if m.group(2) is not None else m.group(3)
        for m in _ATTR_RE.finditer(match.group("attrs"))
    }
    name = str(attrs.pop("name", "")).strip()
    if not name:
        return "retry", _retry_notice("xml tool missing name=")

    body = match.group("body")
    args: dict[str, Any] = dict(attrs)
    for key in ("content", "old_text", "new_text", "query", "code"):
        if f"<{key}>" in body:
            args[key] = _extract_raw(body, key)

    body_text = body.strip("\n")
    if name == "draft_solver" and "content" not in args and "code" not in args and body_text:
        args["code"] = body_text
    return "tool", {"name": name, "args": args}


def _parse_final(raw: str) -> tuple[str, Any]:
    final_body = _extract(raw, "final")
    if not final_body:
        return "retry", _retry_notice("model returned an empty <final> answer")
    plan_text = _extract_raw(final_body, "plan").strip()
    if not plan_text:
        return "retry", _retry_notice("<final> must contain a <plan>{...}</plan> JSON block")
    try:
        plan = json.loads(plan_text)
    except Exception:
        return "retry", _retry_notice("<plan> JSON did not parse")
    if not isinstance(plan, dict):
        return "retry", _retry_notice("<plan> must be a JSON object")
    return "final", {"plan": plan}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest genius/tests/test_harness_parser.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/harness/parser.py genius/tests/test_harness_parser.py
git commit -m "fool/harness: add <tool>/<final> parser"
```

---

## Task 3: Context and result dataclasses

**Files:**
- Create: `fool/harness/context.py`

Round inputs/outputs as plain frozen dataclasses.

- [ ] **Step 1: Implement context.py**

No test needed — these are passive data containers; they will be exercised by tasks 9–11 through the runner tests.

```python
# fool/harness/context.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RoundOutcome:
    iteration: int
    score: float | None
    hypothesis: str
    outcome: str  # "improved" | "regressed" | "catastrophic" | "harness_failed"


@dataclass(frozen=True)
class RoundState:
    iteration: int
    best_score: float | None
    best_solver_path: Path | None
    best_report_path: Path | None
    recent_history: list[RoundOutcome]
    input_dir: Path
    run_dir: Path
    bootstrap_solver_path: Path | None = None


@dataclass(frozen=True)
class HarnessResult:
    solver_code: str
    plan: dict[str, Any]
    transcript_path: Path
    steps_taken: int


class HarnessFailure(RuntimeError):
    """Raised when the harness cannot produce a valid solver this round.

    The outer loop should record a 'harness_failed' outcome and continue.
    """

    def __init__(self, reason: str, *, transcript_path: Path | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transcript_path = transcript_path
```

- [ ] **Step 2: Smoke-import the module**

Run: `python -c "from fool.harness.context import RoundState, RoundOutcome, HarnessResult, HarnessFailure; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Update package exports**

Edit `fool/harness/__init__.py` to:

```python
# fool/harness/__init__.py
from fool.harness.context import (
    HarnessFailure,
    HarnessResult,
    RoundOutcome,
    RoundState,
)
from fool.harness.model_client import FakeModelClient, LLMModelClient, ModelClient

__all__ = [
    "FakeModelClient",
    "HarnessFailure",
    "HarnessResult",
    "LLMModelClient",
    "ModelClient",
    "RoundOutcome",
    "RoundState",
]
```

- [ ] **Step 4: Commit**

```bash
git add fool/harness/context.py fool/harness/__init__.py
git commit -m "fool/harness: add RoundState/HarnessResult dataclasses"
```

---

## Task 4: SessionStore

**Files:**
- Create: `fool/harness/session.py`
- Create: `genius/tests/test_harness_session.py`

One JSON file per round under `run_dir/harness_v{iteration:03d}.json`. Tracks transcript entries and final payload. Append in-memory, flush to disk after every record so a crashed run still leaves a partial transcript.

- [ ] **Step 1: Write the failing tests**

```python
# genius/tests/test_harness_session.py
from __future__ import annotations

import json
from pathlib import Path

from fool.harness.session import HarnessSession


def test_session_writes_initial_skeleton(tmp_path: Path) -> None:
    session = HarnessSession(run_dir=tmp_path, iteration=2)
    assert session.path == tmp_path / "harness_v002.json"
    assert session.path.exists()
    data = json.loads(session.path.read_text())
    assert data["iteration"] == 2
    assert data["transcript"] == []
    assert data["final"] is None


def test_record_user_message_persists(tmp_path: Path) -> None:
    session = HarnessSession(run_dir=tmp_path, iteration=1)
    session.record_user("hello round")
    data = json.loads(session.path.read_text())
    assert data["transcript"][0]["role"] == "user"
    assert data["transcript"][0]["content"] == "hello round"


def test_record_assistant_and_tool_persist(tmp_path: Path) -> None:
    session = HarnessSession(run_dir=tmp_path, iteration=1)
    session.record_assistant("raw model output")
    session.record_tool(
        name="profile_dataset",
        args={"top_k": 3},
        ok=True,
        content="profile body",
    )
    data = json.loads(session.path.read_text())
    roles = [entry["role"] for entry in data["transcript"]]
    assert roles == ["assistant", "tool"]
    tool_entry = data["transcript"][1]
    assert tool_entry["name"] == "profile_dataset"
    assert tool_entry["args"] == {"top_k": 3}
    assert tool_entry["ok"] is True


def test_record_final_persists(tmp_path: Path) -> None:
    session = HarnessSession(run_dir=tmp_path, iteration=1)
    session.record_final(solver_code="def solve(t): return []", plan={"hypothesis": "x"})
    data = json.loads(session.path.read_text())
    assert data["final"]["solver_code"] == "def solve(t): return []"
    assert data["final"]["plan"] == {"hypothesis": "x"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest genius/tests/test_harness_session.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement session.py**

```python
# fool/harness/session.py
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now().isoformat()


class HarnessSession:
    """JSON-backed transcript for one Fool harness round."""

    def __init__(self, run_dir: Path, iteration: int) -> None:
        self.run_dir = Path(run_dir)
        self.iteration = iteration
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / f"harness_v{iteration:03d}.json"
        self._data: dict[str, Any] = {
            "iteration": iteration,
            "created_at": _now(),
            "transcript": [],
            "final": None,
        }
        self._flush()

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def record_user(self, content: str) -> None:
        self._data["transcript"].append(
            {"role": "user", "content": content, "ts": _now()}
        )
        self._flush()

    def record_assistant(self, content: str) -> None:
        self._data["transcript"].append(
            {"role": "assistant", "content": content, "ts": _now()}
        )
        self._flush()

    def record_tool(
        self,
        *,
        name: str,
        args: dict[str, Any],
        ok: bool,
        content: str,
    ) -> None:
        self._data["transcript"].append(
            {
                "role": "tool",
                "name": name,
                "args": args,
                "ok": ok,
                "content": content,
                "ts": _now(),
            }
        )
        self._flush()

    def record_final(self, *, solver_code: str, plan: dict[str, Any]) -> None:
        self._data["final"] = {
            "solver_code": solver_code,
            "plan": plan,
            "ts": _now(),
        }
        self._flush()

    def transcript(self) -> list[dict[str, Any]]:
        return list(self._data["transcript"])
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest genius/tests/test_harness_session.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/harness/session.py genius/tests/test_harness_session.py
git commit -m "fool/harness: add per-round HarnessSession"
```

---

## Task 5: Tool registry scaffolding (no tools yet)

**Files:**
- Create: `fool/harness/tools.py`
- Create: `genius/tests/test_harness_tools.py`

Define `ToolSpec`, `ToolContext`, `ToolResult`, `ToolRegistry`. Registry handles unknown-tool errors, repeated-call detection (two consecutive identical calls return error), and output clipping. No actual tools yet — they come in tasks 6–8.

- [ ] **Step 1: Write the failing tests**

```python
# genius/tests/test_harness_tools.py
from __future__ import annotations

from pathlib import Path

from fool.harness.tools import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        input_dir=tmp_path / "in",
        run_dir=tmp_path / "run",
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="profile",
    )


def test_unknown_tool_returns_error(tmp_path: Path) -> None:
    registry = ToolRegistry()
    result = registry.run("does_not_exist", _ctx(tmp_path), {})
    assert result.ok is False
    assert "unknown tool" in result.content


def test_repeated_identical_call_is_rejected(tmp_path: Path) -> None:
    calls: list[dict] = []

    def echo(ctx: ToolContext, args: dict) -> ToolResult:
        calls.append(args)
        return ToolResult(ok=True, content="ran")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="echo", description="", risky=False, schema={}, run=echo)
    )
    ctx = _ctx(tmp_path)

    first = registry.run("echo", ctx, {"x": 1})
    second = registry.run("echo", ctx, {"x": 1})
    assert first.ok is True
    assert second.ok is False
    assert "repeated" in second.content
    assert len(calls) == 1


def test_output_is_clipped(tmp_path: Path) -> None:
    def long(ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(ok=True, content="A" * 10_000)

    registry = ToolRegistry(max_tool_output=200)
    registry.register(
        ToolSpec(name="long", description="", risky=False, schema={}, run=long)
    )
    result = registry.run("long", _ctx(tmp_path), {})
    assert result.ok is True
    assert len(result.content) <= 260  # 200 + truncation tail
    assert "truncated" in result.content


def test_specs_returns_registered_tool_metadata(tmp_path: Path) -> None:
    def noop(ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(ok=True, content="")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="noop",
            description="does nothing",
            risky=False,
            schema={"x": "int=0"},
            run=noop,
        )
    )
    specs = registry.specs()
    assert specs == [
        {
            "name": "noop",
            "description": "does nothing",
            "risky": False,
            "schema": {"x": "int=0"},
        }
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest genius/tests/test_harness_tools.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement tools.py scaffolding**

```python
# fool/harness/tools.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

MAX_TOOL_OUTPUT_DEFAULT = 4000


@dataclass
class ToolContext:
    input_dir: Path
    run_dir: Path
    best_solver_path: Path | None
    best_report_path: Path | None
    last_report_path: Path | None
    bootstrap_solver_path: Path | None
    durable_memory: Any  # fool.memory_store.FoolMemory or None
    dataset_profile_text: str


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str


ToolFn = Callable[[ToolContext, dict[str, Any]], ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risky: bool
    schema: dict[str, str]
    run: ToolFn


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


class ToolRegistry:
    def __init__(self, *, max_tool_output: int = MAX_TOOL_OUTPUT_DEFAULT) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._call_log: list[tuple[str, str]] = []
        self.max_tool_output = max_tool_output

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "risky": tool.risky,
                "schema": dict(tool.schema),
            }
            for tool in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    def run(
        self, name: str, context: ToolContext, args: dict[str, Any] | None
    ) -> ToolResult:
        args = args or {}
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, content=f"error: unknown tool '{name}'")

        signature = (name, _stable_args(args))
        if len(self._call_log) >= 1 and self._call_log[-1] == signature:
            return ToolResult(
                ok=False,
                content=(
                    f"error: repeated identical call to {name}; "
                    "choose a different tool or emit <final>"
                ),
            )

        try:
            result = tool.run(context, args)
        except Exception as exc:
            self._call_log.append(signature)
            return ToolResult(ok=False, content=f"error: tool {name} failed: {exc}")

        self._call_log.append(signature)
        return ToolResult(ok=result.ok, content=_clip(result.content, self.max_tool_output))


def _stable_args(args: dict[str, Any]) -> str:
    import json

    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False)
    except Exception:
        return repr(sorted(args.items()))
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest genius/tests/test_harness_tools.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/harness/tools.py genius/tests/test_harness_tools.py
git commit -m "fool/harness: add ToolRegistry with clip and repeat-call guard"
```

---

## Task 6: Read-only tools batch

**Files:**
- Modify: `fool/harness/tools.py`
- Modify: `genius/tests/test_harness_tools.py`

Add 8 read-only tools. Each is a small adapter that calls existing code (`agent_tools.analysis_tools`, `agent_tools.template_tools`, `memory_store`, `genius_file_client.read_report`).

| name | what it returns |
|---|---|
| `read_teacher_checklist` | `teacher/EXPERIMENT_REVIEW_CHECKLIST.md` |
| `read_teacher_playbook` | `teacher/DATA_STRATEGY_PLAYBOOK.md` |
| `read_last_report` | latest written round report under `run_dir` (or a notice) |
| `read_incumbent_solver` | best if exists; else bootstrap; else `fool/templates/solver_greedy.py` |
| `profile_dataset` | `context.dataset_profile_text` |
| `rank_bottlenecks` | wraps `agent_tools.analysis_tools.rank_bottlenecks` |
| `retrieve_memory` | `context.durable_memory.retrieve(...)` if memory present |
| `list_strategy_templates` | wraps `agent_tools.template_tools.list_strategy_templates` |

For the wrapping tools, the existing functions accept the old `agent_tools.base.ToolContext`. We create a small adapter helper that synthesizes one from the harness `ToolContext`.

- [ ] **Step 1: Add tests for each read-only tool**

Append to `genius/tests/test_harness_tools.py`:

```python
# --- read-only tool tests ---

import json as _json

from fool.harness.tools import build_default_registry


def _seed_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _seed_input_dir(tmp_path: Path) -> Path:
    input_dir = tmp_path / "in"
    input_dir.mkdir(parents=True, exist_ok=True)
    return input_dir


def test_read_teacher_checklist_returns_file_content(tmp_path: Path) -> None:
    registry = build_default_registry()
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    result = registry.run("read_teacher_checklist", ctx, {})
    assert result.ok is True
    assert "EXPERIMENT" in result.content or "checklist" in result.content.lower()


def test_read_incumbent_solver_prefers_best(tmp_path: Path) -> None:
    best = tmp_path / "best.py"
    best.write_text("def solve(t): return []\n# best\n")
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=best,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_incumbent_solver", ctx, {})
    assert result.ok is True
    assert "# best" in result.content


def test_read_incumbent_solver_falls_back_to_bootstrap(tmp_path: Path) -> None:
    boot = tmp_path / "boot.py"
    boot.write_text("def solve(t): return []\n# boot\n")
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=boot,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_incumbent_solver", ctx, {})
    assert result.ok is True
    assert "# boot" in result.content


def test_read_incumbent_solver_falls_back_to_greedy_template(tmp_path: Path) -> None:
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_incumbent_solver", ctx, {})
    assert result.ok is True
    assert "def solve" in result.content


def test_profile_dataset_returns_context_text(tmp_path: Path) -> None:
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="profile body",
    )
    registry = build_default_registry()
    result = registry.run("profile_dataset", ctx, {})
    assert result.ok is True
    assert "profile body" in result.content


def test_read_last_report_returns_notice_when_absent(tmp_path: Path) -> None:
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_last_report", ctx, {})
    assert result.ok is True
    assert "no" in result.content.lower()


def test_read_last_report_returns_file_when_present(tmp_path: Path) -> None:
    report = tmp_path / "report_v001.txt"
    report.write_text("# Report v1\nscore=42\n")
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=report,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("read_last_report", ctx, {})
    assert result.ok is True
    assert "score=42" in result.content


def test_list_strategy_templates_returns_summary(tmp_path: Path) -> None:
    ctx = ToolContext(
        input_dir=_seed_input_dir(tmp_path),
        run_dir=_seed_run_dir(tmp_path),
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("list_strategy_templates", ctx, {})
    assert result.ok is True
    assert len(result.content) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest genius/tests/test_harness_tools.py -v -k "read_ or profile_ or list_strategy"`
Expected: FAIL with `ImportError: cannot import name 'build_default_registry'`.

- [ ] **Step 3: Implement the read-only tools**

Append to `fool/harness/tools.py`:

```python
# --- read-only tools ---

from pathlib import Path as _Path

from fool.agent_tools.base import ToolContext as _AgentCtx
from fool.agent_tools.analysis_tools import rank_bottlenecks as _agent_rank_bottlenecks
from fool.agent_tools.template_tools import (
    TOOLS as _TEMPLATE_TOOLS,
)

_FOOL_ROOT = _Path(__file__).resolve().parents[2]
_TEACHER_CHECKLIST = _FOOL_ROOT / "teacher" / "EXPERIMENT_REVIEW_CHECKLIST.md"
_TEACHER_PLAYBOOK = _FOOL_ROOT / "teacher" / "DATA_STRATEGY_PLAYBOOK.md"
_GREEDY_TEMPLATE = _FOOL_ROOT / "fool" / "templates" / "solver_greedy.py"


def _read_text_or_notice(path: _Path, label: str) -> ToolResult:
    if not path.exists():
        return ToolResult(ok=True, content=f"({label} not found at {path})")
    return ToolResult(ok=True, content=path.read_text(encoding="utf-8", errors="replace"))


def _t_read_teacher_checklist(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    return _read_text_or_notice(_TEACHER_CHECKLIST, "teacher checklist")


def _t_read_teacher_playbook(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    return _read_text_or_notice(_TEACHER_PLAYBOOK, "teacher playbook")


def _t_read_last_report(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.last_report_path and ctx.last_report_path.exists():
        return ToolResult(
            ok=True,
            content=ctx.last_report_path.read_text(encoding="utf-8", errors="replace"),
        )
    return ToolResult(ok=True, content="no previous round report yet")


def _t_read_incumbent_solver(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    for candidate in (
        ctx.best_solver_path,
        ctx.bootstrap_solver_path,
        _GREEDY_TEMPLATE,
    ):
        if candidate and candidate.exists():
            return ToolResult(
                ok=True,
                content=candidate.read_text(encoding="utf-8", errors="replace"),
            )
    return ToolResult(ok=False, content="no incumbent solver available")


def _t_profile_dataset(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    text = (ctx.dataset_profile_text or "").strip() or "(dataset profile unavailable)"
    return ToolResult(ok=True, content=text)


def _load_report(path: _Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    try:
        from fool.genius_file_client import read_report

        return read_report(path)
    except Exception:
        return None


def _agent_ctx(ctx: ToolContext) -> _AgentCtx:
    return _AgentCtx(
        input_dir=ctx.input_dir,
        run_dir=ctx.run_dir,
        memory_scope="harness",
        dataset_profile=ctx.dataset_profile_text,
        best_report=_load_report(ctx.best_report_path),
        last_report=_load_report(ctx.last_report_path),
    )


def _t_rank_bottlenecks(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    inner = _agent_rank_bottlenecks(_agent_ctx(ctx), args)
    return ToolResult(ok=inner.ok, content=inner.summary)


def _t_retrieve_memory(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.durable_memory is None:
        return ToolResult(ok=True, content="(no durable memory configured)")
    query = str(args.get("query", "")).strip()
    target_buckets = list(args.get("target_buckets", []) or [])
    try:
        body = ctx.durable_memory.retrieve(
            target_buckets=target_buckets,
            strategy_lane="harness",
            query_text=query,
        )
    except Exception as exc:
        return ToolResult(ok=False, content=f"memory retrieve failed: {exc}")
    return ToolResult(ok=True, content=str(body) if body else "(memory empty)")


def _t_list_strategy_templates(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    for tool in _TEMPLATE_TOOLS:
        if tool.name == "list_strategy_templates":
            inner = tool.run(_agent_ctx(ctx), args)
            return ToolResult(ok=inner.ok, content=inner.summary)
    return ToolResult(ok=False, content="list_strategy_templates implementation missing")


_READ_ONLY_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="read_teacher_checklist",
        description="Read teacher/EXPERIMENT_REVIEW_CHECKLIST.md",
        risky=False,
        schema={},
        run=_t_read_teacher_checklist,
    ),
    ToolSpec(
        name="read_teacher_playbook",
        description="Read teacher/DATA_STRATEGY_PLAYBOOK.md",
        risky=False,
        schema={},
        run=_t_read_teacher_playbook,
    ),
    ToolSpec(
        name="read_last_report",
        description="Read the previous round's Genius report",
        risky=False,
        schema={},
        run=_t_read_last_report,
    ),
    ToolSpec(
        name="read_incumbent_solver",
        description="Read the current best solver (or bootstrap/template)",
        risky=False,
        schema={},
        run=_t_read_incumbent_solver,
    ),
    ToolSpec(
        name="profile_dataset",
        description="Show the dataset profile",
        risky=False,
        schema={},
        run=_t_profile_dataset,
    ),
    ToolSpec(
        name="rank_bottlenecks",
        description="Rank scoring bottlenecks across cases",
        risky=False,
        schema={"top_k": "int=4"},
        run=_t_rank_bottlenecks,
    ),
    ToolSpec(
        name="retrieve_memory",
        description="Query durable Fool memory",
        risky=False,
        schema={"query": "str", "target_buckets": "list[str]=[]"},
        run=_t_retrieve_memory,
    ),
    ToolSpec(
        name="list_strategy_templates",
        description="List available strategy templates",
        risky=False,
        schema={},
        run=_t_list_strategy_templates,
    ),
]


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec in _READ_ONLY_SPECS:
        registry.register(spec)
    return registry
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest genius/tests/test_harness_tools.py -v`
Expected: all passed (previous 4 + 8 new).

- [ ] **Step 5: Commit**

```bash
git add fool/harness/tools.py genius/tests/test_harness_tools.py
git commit -m "fool/harness: add 8 read-only tools and build_default_registry"
```

---

## Task 7: Editor tools — `draft_solver`, `patch_solver`

**Files:**
- Modify: `fool/harness/tools.py`
- Modify: `genius/tests/test_harness_tools.py`

Both write to a single draft file: `run_dir / "draft.py"`. Refuse paths outside `run_dir`. `patch_solver` requires the old text to occur exactly once in the current draft.

- [ ] **Step 1: Write the failing tests**

Append to `genius/tests/test_harness_tools.py`:

```python
def test_draft_solver_creates_file_in_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    code = "def solve(t):\n    return []\n"
    result = registry.run("draft_solver", ctx, {"code": code})
    assert result.ok is True
    assert (run_dir / "draft.py").read_text() == code


def test_patch_solver_requires_unique_match(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    registry.run("draft_solver", ctx, {"code": "x = 1\nx = 1\n"})

    result = registry.run("patch_solver", ctx, {"old_text": "x = 1", "new_text": "x = 2"})
    assert result.ok is False
    assert "exactly once" in result.content


def test_patch_solver_applies_when_unique(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    registry.run(
        "draft_solver",
        ctx,
        {"code": "def solve(t):\n    return []\n"},
    )

    result = registry.run(
        "patch_solver",
        ctx,
        {"old_text": "return []", "new_text": "return [(\"a\", \"b\")]"},
    )
    assert result.ok is True
    assert "(\"a\", \"b\")" in (run_dir / "draft.py").read_text()


def test_patch_solver_without_draft_errors(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=tmp_path / "in",
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    result = registry.run("patch_solver", ctx, {"old_text": "a", "new_text": "b"})
    assert result.ok is False
    assert "no draft" in result.content.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest genius/tests/test_harness_tools.py -v -k "draft_ or patch_"`
Expected: 4 FAIL.

- [ ] **Step 3: Implement editor tools**

Append to `fool/harness/tools.py`:

```python
DRAFT_FILENAME = "draft.py"


def _draft_path(ctx: ToolContext) -> _Path:
    return ctx.run_dir / DRAFT_FILENAME


def _t_draft_solver(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    code = args.get("code", args.get("content", ""))
    if not isinstance(code, str) or not code.strip():
        return ToolResult(ok=False, content="error: draft_solver requires non-empty 'code'")
    path = _draft_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return ToolResult(ok=True, content=f"wrote {DRAFT_FILENAME} ({len(code)} chars)")


def _t_patch_solver(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = _draft_path(ctx)
    if not path.exists():
        return ToolResult(
            ok=False,
            content="error: no draft to patch; call draft_solver first",
        )
    old_text = str(args.get("old_text", ""))
    if not old_text:
        return ToolResult(ok=False, content="error: 'old_text' must not be empty")
    if "new_text" not in args:
        return ToolResult(ok=False, content="error: missing 'new_text'")
    new_text = str(args["new_text"])
    body = path.read_text(encoding="utf-8")
    count = body.count(old_text)
    if count != 1:
        return ToolResult(
            ok=False,
            content=f"error: 'old_text' must occur exactly once, found {count}",
        )
    path.write_text(body.replace(old_text, new_text, 1), encoding="utf-8")
    return ToolResult(ok=True, content=f"patched {DRAFT_FILENAME}")


_EDITOR_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="draft_solver",
        description="Write the full solver source to the per-round draft",
        risky=True,
        schema={"code": "str"},
        run=_t_draft_solver,
    ),
    ToolSpec(
        name="patch_solver",
        description="Replace a single exact text block in the draft",
        risky=True,
        schema={"old_text": "str", "new_text": "str"},
        run=_t_patch_solver,
    ),
]
```

Then modify `build_default_registry` to include editor specs. Replace its body with:

```python
def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec in _READ_ONLY_SPECS:
        registry.register(spec)
    for spec in _EDITOR_SPECS:
        registry.register(spec)
    return registry
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest genius/tests/test_harness_tools.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add fool/harness/tools.py genius/tests/test_harness_tools.py
git commit -m "fool/harness: add draft_solver/patch_solver editor tools"
```

---

## Task 8: `smoke_test_solver`

**Files:**
- Modify: `fool/harness/tools.py`
- Modify: `genius/tests/test_harness_tools.py`

Runs the current draft against the first `*.txt` file in `ctx.input_dir` using `python3.9` (matching `genius.solver_executor.DEFAULT_PYTHON_CMD`). Validates: returns `list[tuple[str,str]]`, completes within 10 s.

The smoke test invokes the draft via a one-off harness script that imports the draft, calls `solve(input_text)`, and prints a verdict line. We run that script as a subprocess so a misbehaving solver can't crash the test process.

- [ ] **Step 1: Write the failing tests**

Append to `genius/tests/test_harness_tools.py`:

```python
def test_smoke_test_solver_passes_for_valid_solver(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    registry.run(
        "draft_solver",
        ctx,
        {"code": "def solve(t):\n    return [('t1','c1')]\n"},
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is True, result.content
    assert "PASS" in result.content


def test_smoke_test_solver_fails_when_return_shape_wrong(tmp_path: Path) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text("h\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ctx = ToolContext(
        input_dir=input_dir,
        run_dir=run_dir,
        best_solver_path=None,
        best_report_path=None,
        last_report_path=None,
        bootstrap_solver_path=None,
        durable_memory=None,
        dataset_profile_text="",
    )
    registry = build_default_registry()
    registry.run(
        "draft_solver",
        ctx,
        {"code": "def solve(t):\n    return 'oops'\n"},
    )
    result = registry.run("smoke_test_solver", ctx, {})
    assert result.ok is False
    assert "list" in result.content.lower()
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest genius/tests/test_harness_tools.py -v -k smoke_test_solver`
Expected: FAIL with "unknown tool 'smoke_test_solver'".

- [ ] **Step 3: Implement smoke_test_solver**

Append to `fool/harness/tools.py`:

```python
import subprocess as _subprocess

from genius.solver_executor import DEFAULT_PYTHON_CMD as _PY_CMD

_SMOKE_HARNESS = r"""
import json
import sys
import importlib.util

draft_path = sys.argv[1]
sample_path = sys.argv[2]

spec = importlib.util.spec_from_file_location("draft", draft_path)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
except Exception as exc:
    print(json.dumps({"ok": False, "msg": f"import error: {exc}"}))
    sys.exit(0)

if not hasattr(module, "solve") or not callable(module.solve):
    print(json.dumps({"ok": False, "msg": "missing callable solve()"}))
    sys.exit(0)

with open(sample_path, "r", encoding="utf-8") as fh:
    text = fh.read()

try:
    result = module.solve(text)
except Exception as exc:
    print(json.dumps({"ok": False, "msg": f"solve() raised: {exc}"}))
    sys.exit(0)

if not isinstance(result, list):
    print(json.dumps({"ok": False, "msg": "solve() did not return list"}))
    sys.exit(0)
for pair in result:
    if not (isinstance(pair, tuple) and len(pair) == 2):
        print(json.dumps({"ok": False, "msg": "solve() items must be 2-tuples"}))
        sys.exit(0)
    if not (isinstance(pair[0], str) and isinstance(pair[1], str)):
        print(json.dumps({"ok": False, "msg": "solve() tuples must be (str,str)"}))
        sys.exit(0)

print(json.dumps({"ok": True, "msg": f"PASS n={len(result)}"}))
"""


def _t_smoke_test_solver(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    draft = _draft_path(ctx)
    if not draft.exists():
        return ToolResult(ok=False, content="error: no draft to smoke test")

    samples = sorted(_Path(ctx.input_dir).glob("*.txt"))
    if not samples:
        return ToolResult(ok=False, content="error: no *.txt sample in input_dir")
    sample = samples[0]

    harness_path = ctx.run_dir / "_smoke_harness.py"
    harness_path.write_text(_SMOKE_HARNESS, encoding="utf-8")

    try:
        proc = _subprocess.run(
            [_PY_CMD, str(harness_path), str(draft), str(sample)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except _subprocess.TimeoutExpired:
        return ToolResult(ok=False, content="error: smoke test timed out after 10s")

    stdout = proc.stdout.strip()
    if proc.returncode != 0 or not stdout:
        return ToolResult(
            ok=False,
            content=f"error: smoke harness exit={proc.returncode} stderr={proc.stderr.strip()}",
        )
    try:
        import json as _json

        verdict = _json.loads(stdout.splitlines()[-1])
    except Exception:
        return ToolResult(ok=False, content=f"error: malformed verdict: {stdout}")
    return ToolResult(ok=bool(verdict.get("ok")), content=str(verdict.get("msg", "")))


# add to _EDITOR_SPECS
_EDITOR_SPECS.append(
    ToolSpec(
        name="smoke_test_solver",
        description="Run draft solver locally on one sample and verify return shape",
        risky=False,
        schema={},
        run=_t_smoke_test_solver,
    )
)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest genius/tests/test_harness_tools.py -v -k smoke_test_solver`
Expected: 2 passed. (If `python3.9` is unavailable the test will skip or fail with a clear message; the CLAUDE.md hard constraint mandates `python3.9` for solver execution, so it must be installed.)

- [ ] **Step 5: Commit**

```bash
git add fool/harness/tools.py genius/tests/test_harness_tools.py
git commit -m "fool/harness: add smoke_test_solver tool"
```

---

## Task 9: Prompt builder

**Files:**
- Create: `fool/harness/prompt.py`
- Create: `genius/tests/test_harness_prompt.py`

Builds the stable prefix once and a per-step round message. Prefix contains: identity, hard constraints (lifted from `CLAUDE.md`), tool list (name/schema/risky/description), output-format rules. Round message contains: `Round i`, `Best score so far`, `Recent rounds (≤3 lines)`, `Transcript:` rendering of tool history.

- [ ] **Step 1: Write the failing tests**

```python
# genius/tests/test_harness_prompt.py
from __future__ import annotations

from pathlib import Path

from fool.harness.context import RoundOutcome, RoundState
from fool.harness.prompt import build_prefix, build_round_message
from fool.harness.tools import build_default_registry


def test_prefix_contains_identity_constraints_and_tools(tmp_path: Path) -> None:
    registry = build_default_registry()
    prefix = build_prefix(registry)
    assert "You are Fool" in prefix
    assert "python3.9" in prefix or "Python 3.9" in prefix
    assert "stdlib" in prefix.lower()
    assert "solve(input_text)" in prefix
    assert "draft_solver" in prefix
    assert "<tool>" in prefix
    assert "<final>" in prefix
    # MUST NOT inline teacher checklist text
    assert "EXPERIMENT_REVIEW_CHECKLIST" not in prefix or "read_teacher_checklist" in prefix


def test_round_message_renders_state_and_history(tmp_path: Path) -> None:
    state = RoundState(
        iteration=4,
        best_score=120.5,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[
            RoundOutcome(iteration=1, score=200.0, hypothesis="baseline", outcome="improved"),
            RoundOutcome(iteration=2, score=130.0, hypothesis="tighter pruning", outcome="improved"),
            RoundOutcome(iteration=3, score=300.0, hypothesis="aggressive swap", outcome="regressed"),
        ],
        input_dir=tmp_path / "in",
        run_dir=tmp_path / "run",
    )
    transcript = [
        {"role": "assistant", "content": "<tool>...</tool>"},
        {"role": "tool", "name": "profile_dataset", "args": {}, "ok": True, "content": "p"},
    ]
    msg = build_round_message(state, transcript)
    assert "Round: 4" in msg
    assert "Best score so far: 120.5" in msg
    assert "i=1" in msg and "improved" in msg
    assert "i=3" in msg and "regressed" in msg
    assert "[tool:profile_dataset]" in msg


def test_round_message_handles_empty_history_and_no_best(tmp_path: Path) -> None:
    state = RoundState(
        iteration=1,
        best_score=None,
        best_solver_path=None,
        best_report_path=None,
        recent_history=[],
        input_dir=tmp_path / "in",
        run_dir=tmp_path / "run",
    )
    msg = build_round_message(state, [])
    assert "Round: 1" in msg
    assert "Best score so far: none" in msg
    assert "Recent rounds:" in msg
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest genius/tests/test_harness_prompt.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement prompt.py**

```python
# fool/harness/prompt.py
from __future__ import annotations

import json
from typing import Any

from fool.harness.context import RoundState
from fool.harness.tools import ToolRegistry

_HARD_CONSTRAINTS = """\
Hard runtime constraints (enforced by Genius — never bypass):
- Solver runs under python3.9 with Python stdlib only (no numpy, no OR-tools, no CP-SAT).
- Solver file must be <= 100KB and complete per-case in <= 10 seconds.
- Solver entrypoint: solve(input_text: str) -> list[tuple[str, str]]
- Input is TAB-delimited with 4 columns: task_id_list, courier_id, total_score, willingness.
  task_id_list may contain commas (merged bundle); commas are NOT CSV separators.
- Scoring is fixed to official_like_latest. Do not invent scoring switches.
"""

_OUTPUT_RULES = """\
Output rules:
- Each step you must reply with EXACTLY one of:
    <tool>{"name":"<tool_name>","args":{...}}</tool>
  or, for multi-line code, the XML form:
    <tool name="draft_solver"><code>...</code></tool>
  or the terminal form:
    <final><plan>{"hypothesis":"...","analysis":"...","target_buckets":[...],"edit_plan":[...]}</plan></final>
- When you emit <final>, the harness takes the current draft.py as the solver and submits it to Genius.
- Never invent tool results. Never repeat the same tool call with the same arguments.
- Required arguments must not be empty.
"""

_IDENTITY = """\
You are Fool, an iterative solver-improver for a courier–task assignment problem.
Your goal: improve the solver's total_score on a fixed dataset across iterations.
You drive each round by calling tools to read context (teacher checklist, last report,
incumbent solver, dataset profile, durable memory) and to draft / patch / smoke-test
a candidate solver. When you are ready, emit <final> and the harness submits it.
"""


def build_prefix(registry: ToolRegistry) -> str:
    tool_lines: list[str] = []
    for spec in registry.specs():
        schema_parts = ", ".join(f"{k}: {v}" for k, v in spec["schema"].items())
        risk = "risky" if spec["risky"] else "safe"
        tool_lines.append(f"- {spec['name']}({schema_parts}) [{risk}] {spec['description']}")
    tools_block = "Tools:\n" + "\n".join(tool_lines)

    return "\n\n".join(
        [
            _IDENTITY.strip(),
            _HARD_CONSTRAINTS.strip(),
            tools_block,
            _OUTPUT_RULES.strip(),
        ]
    )


def build_round_message(state: RoundState, transcript: list[dict[str, Any]]) -> str:
    best = "none" if state.best_score is None else f"{state.best_score}"
    if state.recent_history:
        history_lines = [
            f"  i={item.iteration} score={item.score} "
            f"hypothesis={item.hypothesis!r} outcome={item.outcome}"
            for item in state.recent_history
        ]
        history_block = "Recent rounds:\n" + "\n".join(history_lines)
    else:
        history_block = "Recent rounds: (none yet)"

    transcript_lines: list[str] = []
    for entry in transcript:
        role = entry.get("role")
        if role == "tool":
            args = json.dumps(entry.get("args", {}), sort_keys=True, ensure_ascii=False)
            transcript_lines.append(f"[tool:{entry.get('name','?')}] {args}")
            transcript_lines.append(str(entry.get("content", "")))
        elif role == "assistant":
            transcript_lines.append(f"[assistant] {entry.get('content','')}")
        else:
            transcript_lines.append(f"[{role}] {entry.get('content','')}")
    transcript_block = "Transcript so far:\n" + (
        "\n".join(transcript_lines) if transcript_lines else "  (empty)"
    )

    return "\n\n".join(
        [
            f"Round: {state.iteration}",
            f"Best score so far: {best}",
            history_block,
            transcript_block,
            "Next step:",
        ]
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest genius/tests/test_harness_prompt.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/harness/prompt.py genius/tests/test_harness_prompt.py
git commit -m "fool/harness: add prompt builders"
```

---

## Task 10: Runner

**Files:**
- Create: `fool/harness/runner.py`
- Create: `genius/tests/test_harness_runner.py`
- Modify: `fool/harness/__init__.py`

`run_round(state, model, ...)` orchestrates: build prefix once → loop up to `max_steps`: build round message from transcript → `model.complete` → parse → dispatch (tool / retry / final) → record. Returns `HarnessResult`. Raises `HarnessFailure` on max-steps, too-many malformed, or final with no draft on disk.

- [ ] **Step 1: Write the failing tests**

```python
# genius/tests/test_harness_runner.py
from __future__ import annotations

from pathlib import Path

import pytest

from fool.harness.context import HarnessFailure, RoundOutcome, RoundState
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


_VALID_DRAFT = (
    '<tool name="draft_solver"><code>def solve(t):\n    return []\n</code></tool>'
)
_VALID_FINAL = '<final><plan>{"hypothesis":"baseline","analysis":"a","target_buckets":[],"edit_plan":[]}</plan></final>'


def test_runner_returns_solver_and_plan_on_final(tmp_path: Path) -> None:
    fake = FakeModelClient([_VALID_DRAFT, _VALID_FINAL])
    result = run_round(_state(tmp_path), fake, max_steps=4, max_new_tokens=256)
    assert "def solve" in result.solver_code
    assert result.plan["hypothesis"] == "baseline"
    assert result.steps_taken == 2
    assert result.transcript_path.exists()


def test_runner_raises_when_max_steps_exceeded(tmp_path: Path) -> None:
    # always asks for the same tool — repeated guard fires, no final
    fake = FakeModelClient(['<tool>{"name":"profile_dataset","args":{}}</tool>'] * 6)
    with pytest.raises(HarnessFailure, match="max_steps"):
        run_round(_state(tmp_path), fake, max_steps=3, max_new_tokens=256)


def test_runner_raises_when_final_has_no_draft(tmp_path: Path) -> None:
    fake = FakeModelClient([_VALID_FINAL])
    with pytest.raises(HarnessFailure, match="no draft"):
        run_round(_state(tmp_path), fake, max_steps=3, max_new_tokens=256)


def test_runner_handles_retries_with_malformed_then_valid(tmp_path: Path) -> None:
    fake = FakeModelClient(
        ["I think we should...", _VALID_DRAFT, _VALID_FINAL]
    )
    result = run_round(_state(tmp_path), fake, max_steps=6, max_new_tokens=256)
    assert "def solve" in result.solver_code
    assert result.steps_taken == 3


def test_runner_raises_on_too_many_malformed(tmp_path: Path) -> None:
    fake = FakeModelClient(["junk"] * 20)
    with pytest.raises(HarnessFailure, match="malformed"):
        run_round(_state(tmp_path), fake, max_steps=4, max_new_tokens=256)
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest genius/tests/test_harness_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement runner.py**

```python
# fool/harness/runner.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from fool.harness.context import HarnessFailure, HarnessResult, RoundState
from fool.harness.model_client import ModelClient
from fool.harness.parser import parse_model_output
from fool.harness.prompt import build_prefix, build_round_message
from fool.harness.session import HarnessSession
from fool.harness.tools import ToolContext, ToolRegistry, build_default_registry


def run_round(
    state: RoundState,
    model: ModelClient,
    *,
    registry: ToolRegistry | None = None,
    tool_context_factory=None,
    max_steps: int = 12,
    max_new_tokens: int = 4096,
) -> HarnessResult:
    """Drive one Fool round to a final solver via tool-calling.

    Raises HarnessFailure if the round cannot produce a valid solver.
    """
    registry = registry or build_default_registry()
    tool_context = (
        tool_context_factory(state)
        if tool_context_factory is not None
        else _default_tool_context(state)
    )

    session = HarnessSession(run_dir=state.run_dir, iteration=state.iteration)
    prefix = build_prefix(registry)

    transcript: list[dict[str, Any]] = []
    tool_steps = 0
    malformed = 0
    max_malformed = max(max_steps * 2, max_steps + 4)
    last_plan: dict[str, Any] | None = None

    while tool_steps < max_steps:
        round_message = build_round_message(state, transcript)
        prompt = f"{prefix}\n\n{round_message}"
        session.record_user(round_message)

        raw = model.complete(prompt, max_new_tokens)
        session.record_assistant(raw)
        kind, payload = parse_model_output(raw)

        if kind == "retry":
            malformed += 1
            if malformed > max_malformed:
                raise HarnessFailure(
                    f"malformed: too many invalid model outputs ({malformed})",
                    transcript_path=session.path,
                )
            transcript.append({"role": "assistant", "content": str(payload)})
            continue

        if kind == "tool":
            name = payload["name"]
            args = payload.get("args", {}) or {}
            result = registry.run(name, tool_context, args)
            entry = {
                "role": "tool",
                "name": name,
                "args": args,
                "ok": result.ok,
                "content": result.content,
            }
            session.record_tool(name=name, args=args, ok=result.ok, content=result.content)
            transcript.append(entry)
            tool_steps += 1
            continue

        if kind == "final":
            last_plan = dict(payload["plan"])
            draft_path = state.run_dir / "draft.py"
            if not draft_path.exists():
                raise HarnessFailure(
                    "final emitted but no draft on disk; call draft_solver first",
                    transcript_path=session.path,
                )
            solver_code = draft_path.read_text(encoding="utf-8")
            session.record_final(solver_code=solver_code, plan=last_plan)
            return HarnessResult(
                solver_code=solver_code,
                plan=last_plan,
                transcript_path=session.path,
                steps_taken=tool_steps + 1,
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
    )


def _infer_last_report_path(state: RoundState) -> Path | None:
    if state.iteration <= 1:
        return None
    candidate = state.run_dir / f"report_v{state.iteration - 1:03d}.txt"
    return candidate if candidate.exists() else None
```

Update `fool/harness/__init__.py`:

```python
# fool/harness/__init__.py
from fool.harness.context import (
    HarnessFailure,
    HarnessResult,
    RoundOutcome,
    RoundState,
)
from fool.harness.model_client import FakeModelClient, LLMModelClient, ModelClient
from fool.harness.runner import run_round
from fool.harness.tools import ToolContext, ToolRegistry, build_default_registry

__all__ = [
    "FakeModelClient",
    "HarnessFailure",
    "HarnessResult",
    "LLMModelClient",
    "ModelClient",
    "RoundOutcome",
    "RoundState",
    "ToolContext",
    "ToolRegistry",
    "build_default_registry",
    "run_round",
]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest genius/tests/test_harness_runner.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/harness/runner.py fool/harness/__init__.py genius/tests/test_harness_runner.py
git commit -m "fool/harness: add run_round main loop"
```

---

## Task 11: Integrate harness into `fool_loop.py`

**Files:**
- Modify: `fool/fool_loop.py`
- Create: `genius/tests/test_harness_fool_loop_integration.py`

Rewrite the iteration body of `run_fool_loop` to call `fool.harness.run_round`. Outer loop now does only: stop check → build `RoundState` → `run_round` → write solver → `submit_solver` → classify outcome → update best (single-directional) → record `RoundOutcome` → push memory.

Delete the helpers listed in the spec ("文件改动" → "修改"). Delete the matching CLI flags: `--round2-strategy-lane`, `--large301-precheck-retries`, `--same-iteration-rollback-retries`. Keep `--solver-round-max-tokens` but treat it as a single integer (use the largest value if a schedule string is given, with a deprecation log).

- [ ] **Step 1: Write an integration test driven by FakeModelClient**

```python
# genius/tests/test_harness_fool_loop_integration.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fool.harness.model_client import FakeModelClient
from fool import fool_loop


_DRAFT = (
    '<tool name="draft_solver"><code>'
    "def solve(text):\n"
    "    out = []\n"
    "    for line in text.splitlines()[1:]:\n"
    "        parts = line.split('\\t')\n"
    "        if len(parts) < 2: continue\n"
    "        tasks = parts[0].split(',')\n"
    "        courier = parts[1]\n"
    "        for t in tasks:\n"
    "            out.append((t, courier))\n"
    "    return out\n"
    "</code></tool>"
)
_FINAL = '<final><plan>{"hypothesis":"greedy","analysis":"a","target_buckets":["tiny"],"edit_plan":[]}</plan></final>'


def test_fool_loop_uses_harness_and_writes_solver_per_round(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )

    # patch ROOT/out so we use tmp_path
    monkeypatch.setattr(fool_loop, "ROOT", tmp_path)

    # script: round 1 = draft+final
    scripted = [_DRAFT, _FINAL]
    fake = FakeModelClient(scripted)

    def fake_model_factory(**kwargs):
        return fake

    def fake_submit(solver_path, input_dir_path, run_dir, iteration):
        report_path = Path(run_dir) / f"report_v{iteration:03d}.txt"
        report_path.write_text("score=10\n")
        return {"total_score": 10.0, "report_path": report_path}

    with patch.object(fool_loop, "build_model_client", fake_model_factory), \
         patch.object(fool_loop, "submit_solver_to_genius", fake_submit):
        summary = fool_loop.run_fool_loop(
            api_type="openai",
            api_key="ignored",
            model="ignored",
            iterations=1,
            input_dir=str(input_dir),
            scoring="official_like_latest",
            require_ai=False,
        )

    assert summary["iterations_completed"] == 1
    assert summary["best_score"] == 10.0
    solver_v001 = next((tmp_path / "out" / "runs").rglob("solver_v001.py"))
    assert "def solve" in solver_v001.read_text()
```

- [ ] **Step 2: Run integration test to verify it fails**

Run: `python -m pytest genius/tests/test_harness_fool_loop_integration.py -v`
Expected: FAIL — `fool_loop` still does the old pipeline and does not expose `build_model_client` / `submit_solver_to_genius`.

- [ ] **Step 3: Rewrite the outer loop**

Open `fool/fool_loop.py` and replace the body of `run_fool_loop` (currently spanning roughly lines 1699–2700) with the version below. **Delete** these helpers and their imports/usages (they are no longer reachable): `_reflect_and_plan`, `_propose_solver`, `_fallback_reflection_plan`, `_safe_parse_json_object`, `_is_valid_reflection_plan`, `_normalize_reflection_plan`, `_summarize_reflection_memory`, `_recent_failed_hypotheses`, `_pick_strategy_lane`, `_resolve_round2_lane`, `_build_portfolio_focus_policy`, `_recent_non_improving_streak`, `_run_large301_precheck`, `_record_tool_results`, `_render_template_reference`, `_solver_change_ratio`, `_catastrophic_regression_reason`, `_parse_round_token_schedule`, `_resolve_round_token_budget`. Remove `from fool.judge_fitter import …` and `from fool.agent_tools import …` from the top of the file. Keep `_FoolLogger`, `_emit`, `_classify_case_type`, `_build_solver_data_contract`, `_build_dataset_profile`, `_summarize_report_for_prompt`, `_build_case_delta_feedback`, `_case_score_map`, `_report_totals`, `build_run_lesson_record`, `_dataset_memory_scope`, `_resolve_bootstrap_solver_path` — they're consumed by the outer loop or by other tests.

Add at the top:

```python
from fool.harness import (
    FakeModelClient,
    HarnessFailure,
    LLMModelClient,
    ModelClient,
    RoundOutcome,
    RoundState,
    build_default_registry,
    run_round,
)
from fool.harness.runner import _default_tool_context  # for context wiring
from fool.harness.tools import ToolContext
```

Add two seams used by the integration test:

```python
def build_model_client(*, api_type, api_key, model, base_url, effort_level) -> ModelClient:
    return LLMModelClient(
        api_type=api_type,
        api_key=api_key,
        model=model,
        base_url=base_url,
        effort_level=effort_level,
    )


def submit_solver_to_genius(solver_path, input_dir, run_dir, iteration):
    """Submit a solver to Genius and return {"total_score": float, "report_path": Path}."""
    from fool.genius_file_client import submit_solver, read_report

    report_path = Path(run_dir) / f"report_v{iteration:03d}.txt"
    submit_solver(
        solver_path=Path(solver_path),
        input_dir=Path(input_dir),
        report_path=report_path,
    )
    report = read_report(report_path)
    total = float(report.get("total_score", float("inf")))
    return {"total_score": total, "report_path": report_path, "report": report}
```

Replace `run_fool_loop` body with:

```python
def run_fool_loop(
    api_type: str,
    api_key: str,
    model: str,
    iterations: int,
    input_dir: str,
    scoring: str,
    base_url: str | None = None,
    bootstrap_solver_path: str | None = None,
    verbose: bool = True,
    require_ai: bool = True,
    solver_round_max_tokens: str | int = 16000,
    effort_level: str = "low",
    stop_event: Event | None = None,
    event_callback: EventCallback | None = None,
    approval_provider: Callable[[int, dict[str, Any]], bool] | None = None,
    max_steps_per_round: int = 12,
) -> dict[str, Any]:
    stop_event = stop_event or Event()

    if scoring != FIXED_SCORING_MODE:
        raise ValueError(
            f"Scoring mode is fixed to {FIXED_SCORING_MODE}; received: {scoring}"
        )
    if require_ai and not api_key:
        raise RuntimeError("API key is required; refusing to run without AI.")
    if require_ai:
        probe = probe_llm_connection(
            api_type=api_type,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=25,
            effort_level=effort_level,
        )
        if not probe.get("ok", False):
            raise RuntimeError(f"AI connection failed: {probe.get('message','?')}")

    out_root = ROOT / "out"
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir = out_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    global _FOOL_LOGGER
    _FOOL_LOGGER = _FoolLogger(run_dir / "fool.log")
    _FOOL_LOGGER.line(f"run_fool_loop start run_id={run_id} iterations={iterations}")

    bootstrap_path = _resolve_bootstrap_solver_path(bootstrap_solver_path)
    dataset_profile = _build_dataset_profile(input_dir)
    memory_scope = _dataset_memory_scope(input_dir)
    durable_memory = FoolMemory(scope=memory_scope)
    registry = build_default_registry()
    model_client = build_model_client(
        api_type=api_type,
        api_key=api_key,
        model=model,
        base_url=base_url,
        effort_level=effort_level,
    )

    if isinstance(solver_round_max_tokens, str) and "," in solver_round_max_tokens:
        _FOOL_LOGGER.line("solver_round_max_tokens schedule deprecated; using max value")
        budget = max(int(x) for x in solver_round_max_tokens.split(",") if x.strip())
    else:
        budget = int(solver_round_max_tokens)

    best_score: float | None = durable_memory.stored_best_score()
    best_solver_path: Path | None = (
        durable_memory.best_solver_path
        if best_score is not None and durable_memory.best_solver_path.exists()
        else None
    )
    best_report_path: Path | None = (
        durable_memory.best_report_path
        if best_score is not None and durable_memory.best_report_path.exists()
        else None
    )
    recent_history: list[RoundOutcome] = []
    rounds_done = 0

    for i in range(1, iterations + 1):
        if stop_event.is_set():
            break

        state = RoundState(
            iteration=i,
            best_score=best_score,
            best_solver_path=best_solver_path,
            best_report_path=best_report_path,
            recent_history=list(recent_history[-3:]),
            input_dir=Path(input_dir),
            run_dir=run_dir,
            bootstrap_solver_path=bootstrap_path,
        )

        def tool_context_factory(_state: RoundState) -> ToolContext:
            return ToolContext(
                input_dir=_state.input_dir,
                run_dir=_state.run_dir,
                best_solver_path=_state.best_solver_path,
                best_report_path=_state.best_report_path,
                last_report_path=(
                    _state.run_dir / f"report_v{_state.iteration - 1:03d}.txt"
                    if _state.iteration > 1
                    else None
                ),
                bootstrap_solver_path=_state.bootstrap_solver_path,
                durable_memory=durable_memory,
                dataset_profile_text=dataset_profile,
            )

        _emit(event_callback, {"type": "status", "stage": "harness", "iteration": i})
        try:
            harness_result = run_round(
                state,
                model_client,
                registry=registry,
                tool_context_factory=tool_context_factory,
                max_steps=max_steps_per_round,
                max_new_tokens=budget,
            )
        except HarnessFailure as exc:
            _FOOL_LOGGER.line(f"iteration {i}: harness_failed: {exc.reason}")
            recent_history.append(
                RoundOutcome(iteration=i, score=None, hypothesis="", outcome="harness_failed")
            )
            rounds_done += 1
            continue

        solver_path = run_dir / f"solver_v{i:03d}.py"
        solver_path.write_text(harness_result.solver_code, encoding="utf-8")

        submission = submit_solver_to_genius(
            solver_path=solver_path,
            input_dir=Path(input_dir),
            run_dir=run_dir,
            iteration=i,
        )
        score = submission["total_score"]
        report_path = submission["report_path"]

        outcome = _classify_round_outcome(score, best_score)
        if outcome == "improved":
            best_score = score
            best_solver_path = solver_path
            best_report_path = report_path
            shutil.copy2(solver_path, out_root / "solvers" / "best_solver.py")
            shutil.copy2(report_path, out_root / "reports" / "best_report.txt")
            durable_memory.update_best(
                score=score,
                solver_path=solver_path,
                report_path=report_path,
            )

        hypothesis = str(harness_result.plan.get("hypothesis", ""))
        recent_history.append(
            RoundOutcome(iteration=i, score=score, hypothesis=hypothesis, outcome=outcome)
        )
        durable_memory.record(
            build_run_lesson_record(
                iteration=i,
                plan=harness_result.plan,
                score=score,
                outcome=outcome,
                report_path=report_path,
            )
        )
        rounds_done += 1
        _emit(
            event_callback,
            {
                "type": "round_complete",
                "iteration": i,
                "score": score,
                "outcome": outcome,
                "hypothesis": hypothesis,
            },
        )

    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "iterations_completed": rounds_done,
        "best_score": best_score,
        "best_solver_path": str(best_solver_path) if best_solver_path else "",
        "best_report_path": str(best_report_path) if best_report_path else "",
        "recent_history": [outcome.__dict__ for outcome in recent_history],
    }


def _classify_round_outcome(score: float | None, best_score: float | None) -> str:
    if score is None:
        return "harness_failed"
    if best_score is None or score < best_score:
        return "improved"
    if score > best_score * 1.5:
        return "catastrophic"
    return "regressed"
```

(If `FoolMemory` lacks `update_best` or `build_run_lesson_record` has a different signature, adjust the calls to match the actual API — `git grep` to confirm — but do not change the spec's outer-loop behavior.)

- [ ] **Step 4: Update `_parse_args` and remove dead flags**

In `_parse_args` (`fool/fool_loop.py`), remove these arguments and any forwarding to `run_fool_loop`:
- `--round2-strategy-lane`
- `--large301-precheck-retries`
- `--same-iteration-rollback-retries`
- `--auto-keep-best`
- `--enable-teacher-review`

Verify the CLI still parses:

Run: `python -m fool.fool_loop --help`
Expected: help text prints without errors and no longer lists the removed flags.

- [ ] **Step 5: Run all tests**

Run: `python -m pytest genius/tests -v`
Expected: all previous tests still pass + new integration test passes.

- [ ] **Step 6: Commit**

```bash
git add fool/fool_loop.py genius/tests/test_harness_fool_loop_integration.py
git commit -m "fool: replace pipeline with harness run_round per iteration"
```

---

## Task 12: Manual smoke run on `sample_10_cases`

**Files:**
- None modified — this is a manual verification + small README update.

- [ ] **Step 1: Run 2 real-LLM iterations on the sample dataset**

Run (with your API key in env):
```bash
python -m fool.fool_loop \
  --api-type openai --api-key "$OPENAI_API_KEY" --model gpt-4.1-mini \
  --iterations 2 --input-dir data/sample_10_cases \
  --scoring official_like_latest
```

Expected: terminal shows `round_complete` events for iterations 1 and 2; `out/runs/<run_id>/harness_v001.json` and `harness_v002.json` exist with non-empty `final.solver_code`; `out/runs/<run_id>/solver_v001.py` exists; if score improved, `out/solvers/best_solver.py` was updated.

- [ ] **Step 2: Inspect transcripts**

```bash
ls out/runs/$(ls out/runs/ | tail -1)/
python -c "import json,sys; d=json.load(open(sys.argv[1])); print('steps:', len(d['transcript']), 'final:', bool(d['final']))" \
  out/runs/$(ls out/runs/ | tail -1)/harness_v001.json
```

Expected: transcript shows tool calls (read_*, draft_solver, smoke_test_solver, final), not just a single final.

- [ ] **Step 3: Update fool/README.md**

In `fool/README.md`, replace any description of `_reflect_and_plan` / `_propose_solver` with a short paragraph on the harness:

```markdown
## Per-round harness

Each iteration runs `fool.harness.run_round`. The LLM drives the round by
calling tools (`read_teacher_checklist`, `read_incumbent_solver`,
`profile_dataset`, `rank_bottlenecks`, `retrieve_memory`,
`list_strategy_templates`, `draft_solver`, `patch_solver`,
`smoke_test_solver`, ...) until it emits `<final><plan>{...}</plan></final>`.
The outer loop only submits the resulting solver to Genius and updates the
best pointer.
```

- [ ] **Step 4: Commit**

```bash
git add fool/README.md
git commit -m "fool: document harness flow in README"
```

---

## Self-review notes

- **Spec coverage**: outer 4 duties (loop / submit / single-direction best / 3-line history) — Task 11. Harness six components — Tasks 3 (context), 9 (prefix+memory implicit), 10 (transcript+loop), 4 (session), 5–8 (tools+validation), delegation explicitly skipped (spec non-goal). Tool list of 11 — Tasks 6 (8 read-only), 7 (2 editor), 8 (smoke). `<final>` with `<plan>` — Tasks 2 (parser), 10 (runner). Hard constraints in prefix — Task 9. Deletion list — Task 11 step 3. CLI flag removal — Task 11 step 4. Manual sample-dataset run — Task 12.
- **Names consistent across tasks**: `RoundState`, `RoundOutcome`, `HarnessResult`, `HarnessFailure`, `ToolContext`, `ToolSpec`, `ToolRegistry`, `ToolResult`, `build_default_registry`, `run_round`, `FakeModelClient`, `LLMModelClient`, `ModelClient`, `HarnessSession`. All match between definition (Tasks 1, 3, 4, 5, 10) and use (Tasks 9, 10, 11).
- **No placeholders**: every step shows full code. The one judgement call ("If `FoolMemory` lacks `update_best`...") is bounded by "adjust to match actual API; do not change outer-loop behavior".
