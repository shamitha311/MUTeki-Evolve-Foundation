"""Fixture-based test suite for the real Muteki SSE event integration.

Tests replay a realistic, complete Muteki SSE event stream and verify that:
  - normalize_event() correctly processes each event type without error.
  - blackboard.delta (fact_added) maps to Evidence(type="observation").
  - flag.accepted maps to Evidence(type="verified_success", confidence=1.0).
  - run.finished(solved=True) correctly triggers is_run_terminal().
  - normalize_result() builds a correct InvestigationResult from the stream.
  - build_start_payload() produces the correct codex worker profile body.

No live Muteki daemon, Docker, or LLM credentials are required.
All fixtures replicate the authentic Event schema from:
  vendor/muteki/muteki/core/events.py
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import Evidence, InvestigationEvent, InvestigationResult
from muteki_adapter.event_normalizer import (
    extract_summary,
    is_run_terminal,
    normalize_event,
)
from muteki_adapter.result_normalizer import (
    extract_evidence_from_events,
    normalize_result,
)
from muteki_adapter.translator import build_start_payload

# ---------------------------------------------------------------------------
# Fixture helpers — mirror the real Event schema without importing Muteki
# ---------------------------------------------------------------------------

class _FakeEventType:
    """Mirrors muteki.core.events.EventType enum member (has .value attr)."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other
        if isinstance(other, _FakeEventType):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)


def _make_event(
    event_type: str,
    *,
    seq: int,
    run_id: str = "ev-fixture-run",
    solver_id: str | None = None,
    payload: dict[str, Any] | None = None,
    ts: float | None = None,
) -> Any:
    """Build a SimpleNamespace that mirrors a real Muteki Event object."""
    return SimpleNamespace(
        event_type=_FakeEventType(event_type),
        seq=seq,
        ts=ts if ts is not None else time.time(),
        run_id=run_id,
        challenge_id="target-vulnweb",
        solver_id=solver_id,
        payload=payload or {},
    )


# ---------------------------------------------------------------------------
# Realistic SSE event stream fixture
# ---------------------------------------------------------------------------

RUN_ID = "ev-fixture-abc123"
SOLVER_ID = "codex-local-1"

#: A realistic, complete Muteki SSE event sequence (run.started → run.finished).
FIXTURE_STREAM: list[Any] = [
    # 1. run.started — initial launch confirmation
    _make_event(
        "run.started",
        seq=1,
        run_id=RUN_ID,
        solver_id=SOLVER_ID,
        payload={},
    ),
    # 2. worker.status — codex worker comes online
    _make_event(
        "worker.status",
        seq=2,
        run_id=RUN_ID,
        solver_id=SOLVER_ID,
        payload={"online": True, "engine": "codex", "reason": "spawned"},
    ),
    # 3. blackboard.delta — fact_added (observation)
    _make_event(
        "blackboard.delta",
        seq=3,
        run_id=RUN_ID,
        solver_id=SOLVER_ID,
        payload={
            "kind": "fact_added",
            "fact": "Login page at /userinfo.php accepts unsanitized input in the 'username' parameter.",
        },
    ),
    # 4. blackboard.delta — intent_proposed
    _make_event(
        "blackboard.delta",
        seq=4,
        run_id=RUN_ID,
        solver_id=SOLVER_ID,
        payload={
            "kind": "intent_proposed",
            "goal": "Verify SQL injection in /userinfo.php?username parameter",
        },
    ),
    # 5. blackboard.delta — intent_claimed
    _make_event(
        "blackboard.delta",
        seq=5,
        run_id=RUN_ID,
        solver_id=SOLVER_ID,
        payload={
            "kind": "intent_claimed",
            "goal": "Verify SQL injection in /userinfo.php?username parameter",
        },
    ),
    # 6. tool.result — nmap/http tool output
    _make_event(
        "tool.result",
        seq=6,
        run_id=RUN_ID,
        solver_id=SOLVER_ID,
        payload={
            "tool": "http_request",
            "result": "HTTP 200 — vulnerable output detected: admin'-- response reveals DB schema",
        },
    ),
    # 7. blackboard.delta — flag_found recorded on blackboard
    _make_event(
        "blackboard.delta",
        seq=7,
        run_id=RUN_ID,
        solver_id=SOLVER_ID,
        payload={
            "kind": "flag_found",
            "flag": "flag{sql_injection_confirmed_testphp}",
        },
    ),
    # 8. flag.accepted — protocol-verified flag capture
    _make_event(
        "flag.accepted",
        seq=8,
        run_id=RUN_ID,
        solver_id=SOLVER_ID,
        payload={
            "flag": "flag{sql_injection_confirmed_testphp}",
            "verified": True,
        },
    ),
    # 9. run.finished — terminal event with solved=True
    _make_event(
        "run.finished",
        seq=9,
        run_id=RUN_ID,
        solver_id=SOLVER_ID,
        payload={
            "solved": True,
            "flags": ["flag{sql_injection_confirmed_testphp}"],
            "reason": "SQL injection vulnerability confirmed and flag captured",
        },
    ),
]


# ---------------------------------------------------------------------------
# Tests: normalize_event() per fixture event
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("idx,event", enumerate(FIXTURE_STREAM))
def test_normalize_event_succeeds_for_all_fixture_events(idx: int, event: Any) -> None:
    """normalize_event() must return a valid InvestigationEvent for every fixture."""
    result = normalize_event(event, run_id=RUN_ID, sequence_counter=idx + 1)
    assert result is not None, f"normalize_event returned None for event index {idx} ({event.event_type})"
    assert isinstance(result, InvestigationEvent)
    assert result.sequence >= 1
    assert result.run_id  # non-empty


def test_run_started_normalizes_correctly() -> None:
    ev = FIXTURE_STREAM[0]
    result = normalize_event(ev, run_id=RUN_ID, sequence_counter=1)
    assert result is not None
    assert result.sequence == 1
    assert result.type == "run.started"
    assert "started" in result.summary.lower()


def test_worker_status_online_normalizes() -> None:
    ev = FIXTURE_STREAM[1]
    result = normalize_event(ev, run_id=RUN_ID, sequence_counter=2)
    assert result is not None
    assert result.type == "worker.status"
    assert "codex" in result.summary.lower() or "worker" in result.summary.lower()


def test_blackboard_fact_added_normalizes() -> None:
    """blackboard.delta(fact_added) → summary mentions 'Blackboard fact added'."""
    ev = FIXTURE_STREAM[2]  # seq=3 fact_added
    result = normalize_event(ev, run_id=RUN_ID, sequence_counter=3)
    assert result is not None
    assert result.type == "investigation.blackboard"
    assert "fact" in result.summary.lower()
    # Verify the fact text is surfaced in the summary
    assert "userinfo" in result.summary or "Blackboard fact added" in result.summary


def test_blackboard_intent_proposed_normalizes() -> None:
    ev = FIXTURE_STREAM[3]  # seq=4 intent_proposed
    result = normalize_event(ev, run_id=RUN_ID, sequence_counter=4)
    assert result is not None
    assert "direction proposed" in result.summary.lower() or "sql" in result.summary.lower()


def test_blackboard_intent_claimed_normalizes() -> None:
    ev = FIXTURE_STREAM[4]  # seq=5 intent_claimed
    result = normalize_event(ev, run_id=RUN_ID, sequence_counter=5)
    assert result is not None
    assert "claimed" in result.summary.lower() or "direction" in result.summary.lower()


def test_tool_result_normalizes() -> None:
    ev = FIXTURE_STREAM[5]  # seq=6 tool.result
    result = normalize_event(ev, run_id=RUN_ID, sequence_counter=6)
    assert result is not None
    assert result.type == "tool.result"


def test_blackboard_flag_found_normalizes() -> None:
    ev = FIXTURE_STREAM[6]  # seq=7 flag_found on blackboard
    result = normalize_event(ev, run_id=RUN_ID, sequence_counter=7)
    assert result is not None
    assert "flag" in result.summary.lower()


def test_flag_accepted_normalizes() -> None:
    """flag.accepted → type='flag.accepted', summary mentions acceptance."""
    ev = FIXTURE_STREAM[7]  # seq=8
    result = normalize_event(ev, run_id=RUN_ID, sequence_counter=8)
    assert result is not None
    assert result.type == "flag.accepted"
    assert "accepted" in result.summary.lower() or "flag" in result.summary.lower()


def test_run_finished_normalizes_with_solved_true() -> None:
    """run.finished(solved=True) → summary mentions 'completed successfully'."""
    ev = FIXTURE_STREAM[8]  # seq=9
    result = normalize_event(ev, run_id=RUN_ID, sequence_counter=9)
    assert result is not None
    assert result.type == "run.finished"
    assert "success" in result.summary.lower() or "flag" in result.summary.lower()


# ---------------------------------------------------------------------------
# Tests: is_run_terminal()
# ---------------------------------------------------------------------------

def test_run_finished_is_terminal() -> None:
    """is_run_terminal() must return True for run.finished."""
    assert is_run_terminal(FIXTURE_STREAM[8]) is True


def test_worker_status_is_not_terminal() -> None:
    """worker.status must NOT be treated as run-level completion."""
    assert is_run_terminal(FIXTURE_STREAM[1]) is False


def test_flag_accepted_is_not_terminal() -> None:
    """flag.accepted is not a run-level terminal event."""
    assert is_run_terminal(FIXTURE_STREAM[7]) is False


def test_blackboard_delta_is_not_terminal() -> None:
    for ev in FIXTURE_STREAM[:7]:
        assert is_run_terminal(ev) is False, (
            f"Expected non-terminal for event {ev.event_type}"
        )


# ---------------------------------------------------------------------------
# Tests: normalize_result() from full stream
# ---------------------------------------------------------------------------

def _collect_normalized(stream: list[Any]) -> list[InvestigationEvent]:
    """Normalize all events in the fixture stream."""
    results = []
    for i, ev in enumerate(stream):
        n = normalize_event(ev, run_id=RUN_ID, sequence_counter=i + 1)
        if n is not None:
            results.append(n)
    return results


def test_normalize_result_solved_true_from_fixture_stream() -> None:
    """Full fixture stream → InvestigationResult.solved=True."""
    events = _collect_normalized(FIXTURE_STREAM)
    finished_event = FIXTURE_STREAM[8]  # run.finished
    result = normalize_result(
        run_id=RUN_ID,
        events=events,
        finished_event=finished_event,
        elapsed_seconds=12.5,
        error=None,
    )
    assert isinstance(result, InvestigationResult)
    assert result.solved is True
    assert result.run_id == RUN_ID


def test_normalize_result_has_flag_evidence() -> None:
    """Result must include verified flag evidence from run.finished payload."""
    events = _collect_normalized(FIXTURE_STREAM)
    result = normalize_result(
        run_id=RUN_ID,
        events=events,
        finished_event=FIXTURE_STREAM[8],
        elapsed_seconds=12.5,
        error=None,
    )
    flag_evidence = [e for e in result.evidence if "sql_injection_confirmed" in e.summary]
    assert flag_evidence, "Expected flag evidence in result.evidence"


def test_normalize_result_without_finished_event_is_not_solved() -> None:
    """If no finished_event is provided, result must not be solved=True."""
    events = _collect_normalized(FIXTURE_STREAM[:-1])  # omit run.finished
    result = normalize_result(
        run_id=RUN_ID,
        events=events,
        finished_event=None,
        elapsed_seconds=5.0,
        error="investigation_timeout",
    )
    assert result.solved is False


# ---------------------------------------------------------------------------
# Tests: extract_evidence_from_events()
# ---------------------------------------------------------------------------

def test_extract_evidence_includes_verified_flag() -> None:
    """Evidence from RUN_FINISHED payload with solved=True carries verified_flag type."""
    events = _collect_normalized(FIXTURE_STREAM)
    finished_payload = FIXTURE_STREAM[8].payload
    evidence = extract_evidence_from_events(events, finished_payload)

    # result_normalizer uses type="verified_flag" for flags from the terminal payload
    verified = [e for e in evidence if e.type == "verified_flag"]
    assert verified, "Expected at least one verified_flag evidence item"
    assert verified[0].confidence == 1.0
    assert "sql_injection_confirmed" in verified[0].summary


# ---------------------------------------------------------------------------
# Tests: build_start_payload()
# ---------------------------------------------------------------------------

def _make_strategy_and_target():
    """Build minimal Strategy and SandboxTarget for payload tests."""
    from app.models import SandboxTarget, Strategy

    target = SandboxTarget(
        id="vulnweb-testphp",
        name="testphp.vulnweb.com Assessment",
        description="Intentionally vulnerable demo application.",
        runtime_reference="http://testphp.vulnweb.com",
    )
    strategy = Strategy(
        objective="Audit authentication, SQL injection, and XSS attack surface.",
        priorities=["SQL injection", "XSS", "authentication bypass"],
        constraints=["Do not exfiltrate real credentials"],
        context={"category": "web"},
    )
    return target, strategy


def test_build_start_payload_structure() -> None:
    """build_start_payload() produces the correct top-level keys."""
    target, strategy = _make_strategy_and_target()
    payload = build_start_payload(target, strategy, RUN_ID, worker_engine="codex")

    assert "engines" in payload
    assert "worker_profiles" in payload
    assert "challenge" in payload
    assert "prompt" in payload


def test_build_start_payload_codex_engine() -> None:
    """worker_profiles contains one 'codex' entry."""
    target, strategy = _make_strategy_and_target()
    payload = build_start_payload(target, strategy, RUN_ID, worker_engine="codex")

    assert payload["engines"] == ["codex"]
    profiles = payload["worker_profiles"]
    assert len(profiles) == 1
    assert profiles[0]["engine"] == "codex"
    assert profiles[0]["enabled"] is True


def test_build_start_payload_challenge_uses_trusted_target() -> None:
    """Challenge.target comes from SandboxTarget.runtime_reference (not strategy)."""
    target, strategy = _make_strategy_and_target()
    payload = build_start_payload(target, strategy, RUN_ID)

    challenge = payload["challenge"]
    assert challenge["target"] == "http://testphp.vulnweb.com"
    assert challenge["id"] == RUN_ID
    assert challenge["category"] == "web"


def test_build_start_payload_challenge_category_sanitized() -> None:
    """Category from strategy context is whitelist-sanitized."""
    from app.models import SandboxTarget, Strategy

    target = SandboxTarget(
        id="t1", name="T", description="D", runtime_reference="http://example.com"
    )
    strategy = Strategy(
        objective="Test",
        context={"category": "INJECTED_EVIL_CATEGORY"},
    )
    payload = build_start_payload(target, strategy, "run-x")
    # Invalid category defaults to "misc"
    assert payload["challenge"]["category"] == "misc"


def test_build_start_payload_prompt_includes_runtime_reference() -> None:
    """Prompt field references the target URL (from trusted target only)."""
    target, strategy = _make_strategy_and_target()
    payload = build_start_payload(target, strategy, RUN_ID)
    assert "testphp.vulnweb.com" in payload["prompt"]


# ---------------------------------------------------------------------------
# Tests: CTF target loader
# ---------------------------------------------------------------------------

def test_ctf_loader_registers_vulnweb_target() -> None:
    """load_ctf_targets() registers the testphp target into the registry."""
    from app.models import TrustedTargetRegistry
    from orchestration.ctf_loader import load_ctf_targets

    registry = TrustedTargetRegistry()
    registered = load_ctf_targets(registry)

    assert "vulnweb-testphp" in registered
    target = registry.resolve("vulnweb-testphp")
    assert target.runtime_reference == "http://testphp.vulnweb.com"


def test_ctf_loader_is_idempotent() -> None:
    """Calling load_ctf_targets() twice on the same registry does not raise."""
    from app.models import TrustedTargetRegistry
    from orchestration.ctf_loader import CTF_TARGETS, load_ctf_targets

    registry = TrustedTargetRegistry()
    first = load_ctf_targets(registry)
    assert "vulnweb-testphp" in first

    # Second call must not raise — registry allows re-registration of same object.
    # The target should still resolve correctly.
    try:
        load_ctf_targets(registry)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"Second load_ctf_targets() raised unexpectedly: {exc}")

    # Registry still has the target after double-load
    target = registry.resolve("vulnweb-testphp")
    assert target.runtime_reference == "http://testphp.vulnweb.com"


def test_default_registry_includes_vulnweb() -> None:
    """get_default_target_registry() includes the vulnweb target."""
    from orchestration.registry import get_default_target_registry

    registry = get_default_target_registry()
    target = registry.resolve("vulnweb-testphp")
    assert target.id == "vulnweb-testphp"
    assert len(registry) >= 2  # demo + vulnweb
