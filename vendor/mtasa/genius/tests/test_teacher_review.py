"""Unit tests for fool/harness/teacher_review.py — pure logic only
(triggering, parsing, formatting, persistence). The LLM call path uses a
FakeModelClient.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fool.harness.model_client import FakeModelClient
from fool.harness.teacher_review import (
    _parse_verdict,
    format_review_block,
    maybe_run_review,
    should_trigger,
)


@dataclass
class _StubRound:
    score: float | None
    outcome: str = ""


def test_should_trigger_periodic_every_5() -> None:
    history = [_StubRound(score=100.0 - i) for i in range(5)]
    assert should_trigger(iteration=5, recent_history=history, last_reviewed_at=0) \
        == "periodic_every_5"
    assert should_trigger(iteration=10, recent_history=history, last_reviewed_at=0) \
        == "periodic_every_5"


def test_should_trigger_two_round_stagnation_before_2v2_plateau() -> None:
    # last 2 = avg 50.0, prev 2 = avg 50.005 → Δ < 0.01 → plateau
    history = [
        _StubRound(score=50.0),
        _StubRound(score=50.01),
        _StubRound(score=50.0),
        _StubRound(score=50.0),
    ]
    assert should_trigger(iteration=4, recent_history=history, last_reviewed_at=0) \
        == "stagnation_2round"


def test_should_trigger_two_neutral_rounds() -> None:
    history = [
        _StubRound(score=100.0, outcome="improved"),
        _StubRound(score=100.0, outcome="neutral"),
        _StubRound(score=100.0, outcome="neutral"),
    ]
    assert should_trigger(iteration=3, recent_history=history, last_reviewed_at=0) \
        == "stagnation_2round"


def test_should_trigger_dedupes_by_last_reviewed() -> None:
    history = [_StubRound(score=100.0)] * 5
    # Already reviewed at iter 5 → don't fire again at iter 5.
    assert should_trigger(iteration=5, recent_history=history, last_reviewed_at=5) is None


def test_should_trigger_returns_none_no_signal() -> None:
    history = [
        _StubRound(score=100.0),
        _StubRound(score=50.0),
        _StubRound(score=30.0),
    ]
    assert should_trigger(iteration=3, recent_history=history, last_reviewed_at=0) is None


def test_parse_verdict_strips_code_fences() -> None:
    raw = '```json\n{"observation": "stuck", "next_candidates": []}\n```'
    v = _parse_verdict(raw)
    assert v is not None
    assert v["observation"] == "stuck"


def test_parse_verdict_returns_none_on_garbage() -> None:
    assert _parse_verdict("no json here") is None
    assert _parse_verdict("") is None
    # Any valid JSON object is accepted; format_review_block tolerates missing keys.
    assert _parse_verdict('{"unrelated": 1}') == {"unrelated": 1}


def test_format_review_block_renders_chinese_lines() -> None:
    verdict = {
        "_trigger": "periodic_every_5",
        "_iteration": 5,
        "observation": "卡在 scarce_couriers，多轮零收益",
        "exhausted_directions": ["增加 backup 池", "提高 scarce 阈值"],
        "next_candidates": [
            {
                "mechanism": "chain_reopt",
                "rationale": "未尝试链路重排",
                "target_buckets": ["large_seed301", "medium_seed201"],
            },
            {
                "mechanism": "scoring_refine",
                "rationale": "score tie-break 未调优",
                "target_buckets": ["high_noise_seed601"],
            },
        ],
    }
    block = format_review_block(verdict)
    assert "Teacher Review" in block
    assert "scarce_couriers" in block
    assert "chain_reopt" in block
    assert "large_seed301" in block
    # exhausted bullet text appears
    assert "backup" in block


def test_maybe_run_review_writes_log_and_returns_payload(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    class _FakeVI:
        def for_run(self, run_id):
            # Provide 5 scored entries with bucket_scores, plateau-shaped.
            entries = []
            for i in range(1, 6):
                entries.append({
                    "v": i,
                    "iteration": i,
                    "run_id": run_id,
                    "score": 100.0,  # flat → plateau triggers
                    "outcome": "neutral",
                    "bucket_scores": {"large_seed301": 50.0, "medium_seed201": 50.0},
                })
            return entries

    history = [_StubRound(score=100.0) for _ in range(5)]

    verdict_json = json.dumps({
        "observation": "all flat",
        "exhausted_directions": ["x"],
        "next_candidates": [
            {"mechanism": "scoring_refine", "rationale": "y", "target_buckets": ["large_seed301"]},
        ],
    })
    model = FakeModelClient([verdict_json])

    out = maybe_run_review(
        run_dir=run_dir,
        iteration=5,
        recent_history=history,
        version_index=_FakeVI(),
        run_id="r1",
        judge_model=None,
        main_model=model,
        memory_notes=None,
    )
    assert out["_status"] == "ok"
    assert out["_trigger"] == "periodic_every_5"
    assert out["_iteration"] == 5
    prompt = model.last_messages[-1]["content"]
    assert "First 5 Round Overview" in prompt
    assert "Current Best Solver Code" in prompt
    assert "Bottleneck Optimization Guide" in prompt
    # Persistence
    log = run_dir / "teacher_reviews.jsonl"
    assert log.is_file()
    persisted = json.loads(log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert persisted["observation"] == "all flat"


def test_maybe_run_review_dedupes_within_same_iter(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # Pre-write a review at iter=5 so trigger should dedupe.
    (run_dir / "teacher_reviews.jsonl").write_text(
        json.dumps({"_iteration": 5, "observation": "old"}) + "\n",
        encoding="utf-8",
    )

    class _FakeVI:
        def for_run(self, run_id):
            return []

    history = [_StubRound(score=100.0) for _ in range(5)]
    model = FakeModelClient([])  # would crash if called
    out = maybe_run_review(
        run_dir=run_dir,
        iteration=5,
        recent_history=history,
        version_index=_FakeVI(),
        run_id="r1",
        judge_model=None,
        main_model=model,
        memory_notes=None,
    )
    assert out["_status"] == "no_trigger"
    # Model was not consulted
    assert model.calls == []


def test_maybe_run_review_force_trigger_runs_without_stagnation(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    class _FakeVI:
        def for_run(self, run_id):
            return [{
                "v": 1, "iteration": 1, "run_id": run_id, "score": 90.0,
                "outcome": "improved", "bucket_scores": {"large_seed301": 90.0},
            }]

    verdict_json = json.dumps({
        "observation": "follow up",
        "exhausted_directions": [],
        "next_candidates": [
            {"mechanism": "other", "rationale": "check whether advice helped", "target_buckets": ["large_seed301"]},
        ],
    })
    model = FakeModelClient([verdict_json])
    out = maybe_run_review(
        run_dir=run_dir,
        iteration=1,
        recent_history=[_StubRound(score=90.0, outcome="improved")],
        version_index=_FakeVI(),
        run_id="r1",
        judge_model=None,
        main_model=model,
        memory_notes=None,
        force_trigger="post_advice_followup",
    )
    assert out["_status"] == "ok"
    assert out["_trigger"] == "post_advice_followup"


def test_maybe_run_review_returns_none_on_unparseable(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    class _FakeVI:
        def for_run(self, run_id):
            return [{
                "v": 1, "iteration": 5, "run_id": run_id, "score": 100.0,
                "outcome": "neutral", "bucket_scores": {"large_seed301": 50.0},
            }]

    history = [_StubRound(score=100.0) for _ in range(5)]
    model = FakeModelClient(["this is not JSON at all"])
    out = maybe_run_review(
        run_dir=run_dir,
        iteration=5,
        recent_history=history,
        version_index=_FakeVI(),
        run_id="r1",
        judge_model=None,
        main_model=model,
        memory_notes=None,
    )
    assert out["_status"] == "parse_failed"
    assert "this is not JSON" in out.get("_detail", "")
    # No persistence on failure
    assert not (run_dir / "teacher_reviews.jsonl").exists()
    # Debug log captured the raw head
    debug = run_dir / "teacher_review_debug.log"
    assert debug.is_file()
    assert "parse_failed" in debug.read_text(encoding="utf-8")
