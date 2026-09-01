from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fool.fool_loop import _dataset_memory_scope, build_run_lesson_record
from fool.genius_file_client import read_report
from fool.memory_store import FoolMemory


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_report_if_exists(path_text: str) -> dict[str, Any] | None:
    if not path_text:
        return None
    path = Path(path_text)
    if not path.exists():
        return None
    try:
        return read_report(path)
    except Exception:
        return None


def _extract_note_value(text: str, key: str) -> str:
    match = re.search(rf"^- {re.escape(key)}:\s*(.*)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _reflection_memory_from_notes(run_dir: Path, rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_iteration = {
        int(row.get("iteration", 0)): float(row.get("score", 0.0))
        for row in rounds
        if row.get("iteration") is not None
    }
    best_before = float("inf")
    memory: list[dict[str, Any]] = []
    for note_path in sorted(run_dir.glob("notes_v*.md")):
        match = re.search(r"notes_v(\d+)\.md$", note_path.name)
        if not match:
            continue
        iteration = int(match.group(1))
        text = note_path.read_text(encoding="utf-8", errors="replace")
        score = by_iteration.get(iteration)
        if score is None:
            continue
        delta = 0.0 if best_before == float("inf") else score - best_before
        hypothesis = _extract_note_value(text, "thought_hypothesis")
        outcome = _extract_note_value(text, "thought_outcome") or (
            "baseline" if best_before == float("inf") else ("improved" if delta < -1e-9 else "neutral")
        )
        fallback_reason = _extract_note_value(text, "fallback_reason")
        memory.append(
            {
                "iteration": iteration,
                "hypothesis": hypothesis,
                "outcome": outcome,
                "score_delta": delta,
                "_fallback_reason": "" if fallback_reason == "none" else fallback_reason,
            }
        )
        best_before = min(best_before, score)
    return memory


def summarize_runs(input_dir: str, runs_dir: Path) -> int:
    memory_scope = _dataset_memory_scope(input_dir)
    memory = FoolMemory(scope=memory_scope)
    count = 0
    for summary_path in sorted(runs_dir.glob("run_*/run_summary.json")):
        summary = _load_json(summary_path)
        if not summary:
            continue
        rounds = [row for row in summary.get("rounds", []) if isinstance(row, dict)]
        if not rounds:
            continue
        baseline_report = _read_report_if_exists(str(rounds[0].get("report_path", "")))
        best_round = min(rounds, key=lambda row: float(row.get("score", 0.0)))
        best_report = _read_report_if_exists(str(best_round.get("report_path", "")))
        if best_report is None:
            best_report = _read_report_if_exists(str(summary.get("best_report_path", "")))
        lesson = build_run_lesson_record(
            run_id=str(summary.get("run_id", summary_path.parent.name)),
            memory_scope=memory_scope,
            rounds=rounds,
            baseline_report=baseline_report,
            best_report=best_report,
            reflection_memory=_reflection_memory_from_notes(summary_path.parent, rounds),
        )
        if lesson is None:
            continue
        memory.record_run_lesson(lesson)
        count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize saved Fool runs into durable run lessons.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--runs-dir", default=str(ROOT / "out" / "runs"))
    args = parser.parse_args()
    count = summarize_runs(args.input_dir, Path(args.runs_dir))
    print(f"recorded_run_lessons={count}")


if __name__ == "__main__":
    main()
