from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from genius.genius_judge import run_judge
from genius.report_writer import write_report
from genius.scoring_functions import FIXED_SCORING_MODE
from genius.solver_executor import DEFAULT_CASE_TIMEOUT_SEC, MAX_SOLVER_BYTES


def _write_progress(progress_file: str, payload: dict) -> None:
    if not progress_file:
        return
    path = Path(progress_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _fatal_report(args: argparse.Namespace, exc: BaseException) -> dict:
    """Build a minimal report carrying a fatal_message so callers (LLM
    via score_locally, or human via TXT) can still read the failure cause
    instead of only seeing a non-zero exit code.
    """
    return {
        "average_score": 0.0,
        "solved_cases": 0,
        "total_cases": 0,
        "valid_cases": 0,
        "scoring_mode": args.scoring,
        "python_cmd": args.python_cmd,
        "max_case_seconds": args.max_case_seconds,
        "constraints": {
            "python": "3.9 required",
            "solver_max_bytes": MAX_SOLVER_BYTES,
            "case_timeout_sec": int(args.max_case_seconds),
        },
        "merge_uncertainty_note": (
            "ordinary+backup treated stable; merge bundle behavior still under research"
        ),
        "cases": [],
        "fatal_message": f"{type(exc).__name__}: {exc}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one submission via Genius")
    parser.add_argument("--solver", required=True)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--scoring", default=FIXED_SCORING_MODE)
    parser.add_argument("--report", required=True)
    parser.add_argument("--python-cmd", default="python3.6")
    parser.add_argument("--max-case-seconds", type=float, default=DEFAULT_CASE_TIMEOUT_SEC)
    parser.add_argument("--log-path", default="")
    parser.add_argument("--progress-file", default="")
    args = parser.parse_args()

    try:
        report_obj = run_judge(
            solver_path=args.solver,
            input_dir=args.input_dir,
            scoring=args.scoring,
            python_cmd=args.python_cmd,
            max_case_seconds=args.max_case_seconds,
            log_path=args.log_path or None,
            progress_callback=(
                (lambda payload: _write_progress(args.progress_file, payload))
                if args.progress_file
                else None
            ),
        )
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        # Recoverable-by-LLM failures (solver >100KB, py39 incompatible,
        # missing solver/input file, etc.) used to crash run_submission and
        # hide the cause from the LLM. Surface them via a fatal-report so
        # the LLM can read the message and self-correct next round.
        report_obj = _fatal_report(args, exc)

    write_report(report_obj, args.report)
    print(f"written={args.report}")


if __name__ == "__main__":
    main()
