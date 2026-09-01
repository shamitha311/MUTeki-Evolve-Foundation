from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from genius.report_writer import write_report
from genius.official_like import evaluate_case_solution
from genius.solver_executor import (
    DEFAULT_CASE_TIMEOUT_SEC,
    DEFAULT_PYTHON_CMD,
    ONLINE_BUDGET_SEC,
    ensure_solver_size_ok,
    execute_solver_case,
    materialize_local_solver,
)
from genius.scoring_functions import (
    FIXED_SCORING_MODE,
    available_scoring_modes,
)
from genius.smoke import SmokeFailed, run_smoke
from genius.validation import list_case_files


def _case_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class _GeniusLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._t0 = time.perf_counter()

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def line(self, message: str) -> None:
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000)
        self._fh.write(f"[{self._ts()} +{elapsed_ms}ms] {message}\n")
        self._fh.flush()

    def block(self, title: str, body: str) -> None:
        if not body:
            return
        self.line(f"{title}:")
        for ln in body.splitlines():
            self._fh.write(f"    {ln}\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def _default_log_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return ROOT / "out" / "logs" / f"genius_{stamp}.log"


def run_judge(
    solver_path: str,
    input_dir: str,
    scoring: str = FIXED_SCORING_MODE,
    report: str | None = None,
    python_cmd: str = DEFAULT_PYTHON_CMD,
    max_case_seconds: float = DEFAULT_CASE_TIMEOUT_SEC,
    log_path: str | Path | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    skip_smoke: bool = False,
) -> dict[str, Any]:
    if scoring != FIXED_SCORING_MODE:
        raise ValueError(
            f"Scoring mode is fixed to {FIXED_SCORING_MODE}; received: {scoring}"
        )

    log_file = Path(log_path) if log_path else _default_log_path()
    logger = _GeniusLogger(log_file)
    logger.line(
        f"run_judge start solver={solver_path} input_dir={input_dir} "
        f"scoring={scoring} python_cmd={python_cmd} max_case_seconds={max_case_seconds}"
    )

    local_solver_path: Path | None = None
    try:
        ensure_solver_size_ok(solver_path)
        logger.line(f"solver size OK ({Path(solver_path).stat().st_size} bytes)")

        case_files = list_case_files(input_dir)
        if not case_files:
            logger.line(f"ERROR: no .txt cases in {input_dir}")
            raise ValueError(f"No .txt cases found in: {input_dir}")
        logger.line(f"discovered {len(case_files)} case file(s)")

        if skip_smoke:
            logger.line("smoke SKIPPED (skip_smoke=True; test/contract path)")
            smoke = None
        else:
            smoke = run_smoke(
                solver_path,
                python_cmd=python_cmd,
                max_case_seconds=max_case_seconds,
                local_budget_sec=max_case_seconds,
            )
        if smoke is None or smoke.passed:
            if smoke is not None:
                logger.line(f"smoke PASS ({smoke.cases_run} adversarial case(s))")
                local_solver_path = smoke.local_solver_path
                if local_solver_path is not None:
                    logger.line(
                        f"injected local BUDGET_SEC={max_case_seconds:g} into {local_solver_path} "
                        f"(online={ONLINE_BUDGET_SEC:g}); original solver untouched"
                    )
        else:
            logger.line(
                f"smoke FAIL ({len(smoke.failures)}/{smoke.cases_run} adversarial case(s))"
            )
            for f in smoke.failures:
                logger.block(f"smoke fail {f.case_name}", f.render())
            raise SmokeFailed(smoke)

        exec_solver_path = (
            local_solver_path if local_solver_path is not None else solver_path
        )

        case_rows: list[dict[str, Any]] = []
        valid_cases = 0

        for idx, case_file in enumerate(case_files, start=1):
            if progress_callback is not None:
                progress_callback(
                    {
                        "stage": "scoring",
                        "case_name": case_file.stem,
                        "case_index": idx,
                        "completed_cases": idx - 1,
                        "total_cases": len(case_files),
                        "progress_pct": round((idx / len(case_files)) * 100),
                    }
                )
            text = case_file.read_text(encoding="utf-8")
            logger.line(f"case[{idx}/{len(case_files)}] start name={case_file.stem}")

            exec_result = execute_solver_case(
                python_cmd=python_cmd,
                solver_path=exec_solver_path,
                case_path=case_file,
                timeout_sec=max_case_seconds,
            )

            stderr_text = str(exec_result.get("stderr", "") or "")
            if not exec_result.get("ok", False):
                error_type = str(exec_result.get("error_type", "unknown"))
                error_message = str(exec_result.get("error", "execution error"))
                runtime_ms = int(exec_result.get("runtime_ms", 0) or 0)
                logger.line(
                    f"case[{idx}] exec FAIL type={error_type} runtime_ms={runtime_ms} msg={error_message}"
                )
                logger.block("case stderr", stderr_text)

                if error_type in {"python_missing", "python_incompatible"}:
                    logger.line(f"FATAL: {error_type} — aborting run_judge")
                    raise RuntimeError(error_message)

                raw_output = []
                execution_ok = False
            else:
                raw_output = exec_result.get("output", [])
                runtime_ms = int(exec_result.get("runtime_ms", 0) or 0)
                error_message = ""
                execution_ok = True
                logger.line(
                    f"case[{idx}] exec OK runtime_ms={runtime_ms} output_rows={len(raw_output) if hasattr(raw_output, '__len__') else 'n/a'}"
                )
                if stderr_text:
                    logger.block("case stderr (non-fatal)", stderr_text)

            ev = evaluate_case_solution(text, raw_output)
            score = float(ev["official_like_score"])

            case_valid = bool(ev["valid"] and execution_ok)
            if case_valid:
                valid_cases += 1

            total = max(int(ev["total_tasks"]), 1)
            case_message = error_message or str(ev["message"])

            logger.line(
                f"case[{idx}] eval score={score:.4f} covered={ev['covered_tasks']}/{ev['total_tasks']} "
                f"uncovered={ev['uncovered_tasks']} extra_notify={ev['extra_notify']} "
                f"merged_rows={ev['merged_rows']} valid={case_valid} format_error={bool(ev['format_error'])} "
                f"msg={case_message}"
            )

            case_rows.append(
                {
                    "case_name": case_file.stem,
                    "score": score,
                    "covered": int(ev["covered_tasks"]),
                    "total_tasks": int(ev["total_tasks"]),
                    "coverage_pct": (float(ev["covered_tasks"]) / total) * 100.0,
                    "runtime_ms": runtime_ms,
                    "extra_notify": int(ev["extra_notify"]),
                    "merged_rows": int(ev["merged_rows"]),
                    "uncovered_tasks": int(ev["uncovered_tasks"]),
                    "selected_lines": int(ev["selected_lines"]),
                    "visible_total": float(ev["visible_total"]),
                    "valid": case_valid,
                    "format_error": bool(ev["format_error"]),
                    "message": case_message,
                    "illegal_solution": bool(ev.get("illegal_solution", False)),
                    "validation_errors": list(ev.get("validation_errors", []) or []),
                    "case_hash": _case_hash(text),
                }
            )

        average_score = sum(row["score"] for row in case_rows) / len(case_rows)
        report_obj: dict[str, Any] = {
            "average_score": round(average_score, 4),
            "solved_cases": len(case_rows),
            "total_cases": len(case_rows),
            "valid_cases": valid_cases,
            "scoring_mode": scoring,
            "python_cmd": python_cmd,
            "max_case_seconds": max_case_seconds,
            "constraints": {
                "python": "3.6 required",
                "solver_max_bytes": 100 * 1024,
                "case_timeout_sec": int(max_case_seconds) * len(case_rows),
            },
            "merge_uncertainty_note": "ordinary+backup treated stable; merge bundle behavior still under research",
            "cases": case_rows,
            "log_path": str(log_file),
        }

        if report:
            write_report(report_obj, report)
            logger.line(f"report written: {report}")

        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "finished",
                    "case_name": case_rows[-1]["case_name"],
                    "case_index": len(case_rows),
                    "completed_cases": len(case_rows),
                    "total_cases": len(case_rows),
                    "progress_pct": 100,
                }
            )

        logger.line(
            f"run_judge finish average_score={report_obj['average_score']:.4f} "
            f"valid={valid_cases}/{len(case_rows)}"
        )
        return report_obj
    except Exception as exc:
        logger.line(f"run_judge raised: {type(exc).__name__}: {exc}")
        raise
    finally:
        if local_solver_path is not None:
            try:
                local_solver_path.unlink()
            except OSError:
                pass
        logger.close()


def score_single_case(
    solver_path: str,
    case_path: str,
    python_cmd: str = DEFAULT_PYTHON_CMD,
    max_case_seconds: float = DEFAULT_CASE_TIMEOUT_SEC,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run solver on a single case file and return score / coverage / message."""
    logger = _GeniusLogger(Path(log_path) if log_path else _default_log_path())
    try:
        ensure_solver_size_ok(solver_path)
        case_file = Path(case_path)
        text = case_file.read_text(encoding="utf-8")
        logger.line(f"score_single_case solver={solver_path} case={case_path}")

        exec_result = execute_solver_case(
            python_cmd=python_cmd,
            solver_path=solver_path,
            case_path=case_file,
            timeout_sec=max_case_seconds,
        )

        stderr_text = str(exec_result.get("stderr", "") or "")
        if not exec_result.get("ok", False):
            error_type = str(exec_result.get("error_type", "unknown"))
            error_message = str(exec_result.get("error", "execution error"))
            logger.line(f"exec FAIL type={error_type} msg={error_message}")
            logger.block("stderr", stderr_text)
            if error_type in {"python_missing", "python_incompatible"}:
                raise RuntimeError(error_message)
            ev = evaluate_case_solution(text, [])
            return {
                "score": float(ev["official_like_score"]),
                "covered": int(ev["covered_tasks"]),
                "total": int(ev["total_tasks"]),
                "message": error_message,
                "valid": False,
            }

        raw_output = exec_result.get("output", [])
        ev = evaluate_case_solution(text, raw_output)
        logger.line(
            f"score={ev['official_like_score']:.4f} covered={ev['covered_tasks']}/{ev['total_tasks']} "
            f"uncovered={ev['uncovered_tasks']} valid={bool(ev['valid'])} msg={ev['message']}"
        )
        return {
            "score": float(ev["official_like_score"]),
            "covered": int(ev["covered_tasks"]),
            "total": int(ev["total_tasks"]),
            "message": str(ev["message"]),
            "valid": bool(ev["valid"]),
        }
    finally:
        logger.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Genius local judge")
    parser.add_argument("--solver", required=True, help="Solver Python file path")
    parser.add_argument("--input-dir", required=True, help="Directory of txt cases")
    parser.add_argument(
        "--scoring",
        default=FIXED_SCORING_MODE,
        choices=available_scoring_modes(),
        help="Scoring mode (fixed)",
    )
    parser.add_argument("--report", required=True, help="Output report txt path")
    parser.add_argument("--json", default="", help="Optional output JSON path")
    parser.add_argument("--python-cmd", default=DEFAULT_PYTHON_CMD, help="Python command for solver execution (must be Python 3.6)")
    parser.add_argument("--max-case-seconds", type=float, default=DEFAULT_CASE_TIMEOUT_SEC, help="Max runtime per case in seconds")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = run_judge(
        solver_path=args.solver,
        input_dir=args.input_dir,
        scoring=args.scoring,
        report=args.report,
        python_cmd=args.python_cmd,
        max_case_seconds=args.max_case_seconds,
    )
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"average_score={result['average_score']:.2f}")
    print(f"cases={result['solved_cases']}/{result['total_cases']}")
    print(f"report={args.report}")


if __name__ == "__main__":
    main()
