from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXED_SCORING_MODE = "official_like_latest"


def _parse_detail_line(text: str) -> dict[str, int | float]:
    result: dict[str, int | float] = {}
    for item in text.removeprefix("details:").split(","):
        key, _, value = item.strip().partition("=")
        if not key or not value:
            continue
        result[key] = float(value) if "." in value else int(value)
    return result


def read_report(report_path: str | Path) -> dict[str, Any]:
    """Read Genius' standard TXT response into Fool's analysis structure."""
    lines = Path(report_path).read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("FATAL"):
        detail = lines[1] if len(lines) >= 2 else ""
        return {
            "average_score": 0.0,
            "solved_cases": 0,
            "total_cases": 0,
            "valid_cases": 0,
            "cases": [],
            "fatal_message": detail or lines[0],
        }
    if len(lines) < 4 or lines[0] != "Average Penalty Score":
        raise RuntimeError(f"Invalid Genius TXT report: {report_path}")

    result: dict[str, Any] = {
        "average_score": float(lines[1]),
        "solved_cases": int(lines[3].split("/")[0].strip()),
        "total_cases": int(lines[3].split("/")[1].strip()),
        "cases": [],
    }
    idx = 5
    while idx < len(lines) and lines[idx] != "Meta":
        if not lines[idx]:
            idx += 1
            continue
        if idx + 4 >= len(lines):
            raise RuntimeError(f"Truncated Genius TXT report: {report_path}")
        covered_text, pct_text = lines[idx + 2].split("(", 1)
        covered, total = covered_text.split("/", 1)
        case: dict[str, Any] = {
            "case_name": lines[idx],
            "score": float(lines[idx + 1]),
            "covered": int(covered),
            "total_tasks": int(total),
            "coverage_pct": float(pct_text.rstrip("%)")),
            "runtime_ms": int(lines[idx + 3].rstrip("ms")),
            "valid": True,
        }
        case.update(_parse_detail_line(lines[idx + 4]))
        # Normalize detail keys so Fool analysis can rely on stable field names.
        case["uncovered_tasks"] = int(
            case.get("uncovered_tasks", case.get("uncovered", max(case["total_tasks"] - case["covered"], 0)))
        )
        case["extra_notify"] = int(case.get("extra_notify", 0))
        case["merged_rows"] = int(case.get("merged_rows", case.get("merged", 0)))
        idx += 5
        if idx < len(lines) and lines[idx].startswith("ERROR:"):
            case["valid"] = False
            case["message"] = lines[idx].removeprefix("ERROR:").strip()
            idx += 1
        result["cases"].append(case)

    if idx < len(lines) and lines[idx] == "Meta":
        for line in lines[idx + 1 :]:
            key, sep, value = line.partition("=")
            if sep:
                result[key] = value
    result["valid_cases"] = int(result.get("valid_cases", 0))
    return result


def submit_solver(
    solver_path: str | Path,
    input_dir: str | Path,
    report_path: str | Path,
    scoring: str = FIXED_SCORING_MODE,
    log_path: str | Path | None = None,
) -> dict[str, Any]:
    """Submit a solver file to the Genius process and consume its TXT result."""
    cmd = [
        sys.executable,
        str(ROOT / "genius" / "run_submission.py"),
        "--solver",
        str(solver_path),
        "--input-dir",
        str(input_dir),
        "--scoring",
        scoring,
        "--report",
        str(report_path),
    ]
    if log_path is not None:
        cmd.extend(["--log-path", str(log_path)])
    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Genius submission failed").strip()
        raise RuntimeError(message)
    return read_report(report_path)
