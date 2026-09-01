"""Per-smoke-call audit trail used by the periodic teacher_review.

Each `smoke_test_solver` invocation appends one JSON line to
`<run_dir>/smoke_log.jsonl`. Schema:

    {
      "ts": "<iso8601>",
      "iteration": 7,
      "ok": true,                    # shape check passed
      "shape_msg": "n=10",
      "preview_label": "mimic_10case" | "sample_10_cases" | "large_seed301" | "",
      "preview_avg": 1187.3 | null,
      "preview_per_case": {"large_seed301": 1456.0, ...} | {}
    }

The writer is best-effort: if the preview report file is missing or unparsable
the fields are nulled, never raised — smoke must not regress on log failure.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


_SMOKE_LOG_NAME = "smoke_log.jsonl"


def append_smoke_log(
    *,
    run_dir: Path,
    iteration: int,
    ok: bool,
    shape_msg: str,
    preview_label: str = "",
    preview_report_path: Path | None = None,
) -> None:
    """Append one entry. Silent on any I/O error."""
    entry: dict[str, Any] = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "iteration": int(iteration),
        "ok": bool(ok),
        "shape_msg": str(shape_msg or "")[:200],
        "preview_label": str(preview_label or ""),
        "preview_avg": None,
        "preview_per_case": {},
    }

    if preview_report_path is not None and preview_report_path.is_file():
        try:
            from fool.genius_file_client import read_report

            report = read_report(preview_report_path)
            avg = report.get("average_score")
            if isinstance(avg, (int, float)):
                entry["preview_avg"] = float(avg)
            per_case: dict[str, float] = {}
            for case in report.get("cases") or []:
                name = str(case.get("case_name", "")).strip()
                if not name:
                    continue
                try:
                    per_case[name] = float(case.get("score", 0.0))
                except (TypeError, ValueError):
                    continue
            entry["preview_per_case"] = per_case
        except Exception:  # noqa: BLE001 — best-effort logging
            pass

    try:
        log_path = run_dir / _SMOKE_LOG_NAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_smoke_log(run_dir: Path, *, since_iteration: int | None = None) -> list[dict[str, Any]]:
    """Read all entries from smoke_log.jsonl. Optionally filter to entries with
    iteration >= since_iteration."""
    log_path = run_dir / _SMOKE_LOG_NAME
    if not log_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if since_iteration is not None and int(obj.get("iteration", 0)) < since_iteration:
                    continue
                out.append(obj)
    except OSError:
        return []
    return out
