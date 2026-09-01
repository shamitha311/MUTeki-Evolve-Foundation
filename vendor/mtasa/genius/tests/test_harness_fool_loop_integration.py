from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fool.harness.model_client import FakeModelClient
from fool import fool_loop
from fool.memory_store import FoolMemory as _RealFoolMemory


_INTENT = "<intent>step</intent>"
_DRAFT = (
    _INTENT
    + '<tool name="draft_solver"><code>'
    + "def solve(text):\n"
    + "    out = []\n"
    + "    for line in text.splitlines()[1:]:\n"
    + "        parts = line.split('\\t')\n"
    + "        if len(parts) < 2: continue\n"
    + "        tasks = parts[0].split(',')\n"
    + "        courier = parts[1]\n"
    + "        for t in tasks:\n"
    + "            out.append((t, courier))\n"
    + "    return out\n"
    + "</code></tool>"
)
_FINAL = (
    _INTENT
    + '<final><plan>{"hypothesis":"greedy","analysis":"a","target_buckets":["tiny"],"edit_plan":[]}</plan></final>'
)


def _isolate_memory_root(monkeypatch, tmp_path: Path) -> None:
    def _factory(*, scope: str | None = None):
        return _RealFoolMemory(memory_dir=tmp_path / "memory" / (scope or "default"))

    monkeypatch.setattr(fool_loop, "FoolMemory", _factory)


def test_fool_loop_uses_harness_and_writes_solver_per_round(tmp_path: Path, monkeypatch) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )

    monkeypatch.setattr(fool_loop, "ROOT", tmp_path)
    _isolate_memory_root(monkeypatch, tmp_path)

    # Each non-duplicate round: 1 harness output(s) + 1 post-outcome reflector call.
    # The reflector output here intentionally doesn't parse as memory_write — the
    # reflector handles that gracefully (ok=False, no crash).
    _REFLECT_NOOP = "skip"
    scripted = [_DRAFT, _FINAL, _REFLECT_NOOP]
    fake = FakeModelClient(scripted)

    def fake_model_factory(**kwargs):
        return fake

    def fake_submit(solver_path, input_dir, run_dir, iteration):
        report_path = Path(run_dir) / f"report_v{iteration:03d}.txt"
        report_path.write_text("score=10\n")
        return {"total_score": 10.0, "report_path": report_path, "report": {"total_score": 10.0}}

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


def test_fool_loop_skips_duplicate_submission_for_near_noop_round(
    tmp_path: Path, monkeypatch
) -> None:
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "tiny.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\n"
        "t1\tc1\t1.0\t1.0\n"
    )

    monkeypatch.setattr(fool_loop, "ROOT", tmp_path)
    _isolate_memory_root(monkeypatch, tmp_path)

    # iter 1 (non-duplicate): harness emits _FINAL, pre-final guard fires
    # (one extra LLM call — unparseable "skip" → status=skipped, accept),
    # then reflector sub-call.
    # iter 2 (duplicate-guard path): only harness _FINAL + guard call, no
    # reflector.
    fake = FakeModelClient([_FINAL, "skip", "skip", _FINAL, "skip"])

    def fake_model_factory(**kwargs):
        return fake

    submit_calls: list[int] = []

    def fake_submit(solver_path, input_dir, run_dir, iteration):
        submit_calls.append(int(iteration))
        report_path = Path(run_dir) / f"report_v{iteration:03d}.txt"
        report_path.write_text("Average Penalty Score\n10\n", encoding="utf-8")
        return {
            "total_score": 10.0,
            "report_path": report_path,
            "report": {
                "total_score": 10.0,
                "solved_cases": 1,
                "total_cases": 1,
                "cases": [{"case_name": "tiny", "uncovered_tasks": 0}],
            },
        }

    with patch.object(fool_loop, "build_model_client", fake_model_factory), patch.object(
        fool_loop, "submit_solver_to_genius", fake_submit
    ):
        summary = fool_loop.run_fool_loop(
            api_type="openai",
            api_key="ignored",
            model="ignored",
            iterations=2,
            input_dir=str(input_dir),
            scoring="official_like_latest",
            require_ai=False,
        )

    assert summary["iterations_completed"] == 2
    assert submit_calls == [1]
    assert len(summary["recent_history"]) == 2
    assert summary["recent_history"][1]["outcome"] == "duplicate_skipped"
