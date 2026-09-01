from __future__ import annotations

import json
from pathlib import Path


def format_report(report: dict) -> str:
    lines: list[str] = []
    fatal_message = report.get("fatal_message")
    if fatal_message:
        lines.append("FATAL")
        lines.append(str(fatal_message))
        lines.append("")
    lines.append("Average Penalty Score")
    lines.append(f"{report['average_score']:.2f}")
    lines.append("Completed Cases")
    lines.append(f"{report['solved_cases']} / {report['total_cases']}")
    lines.append("")

    for case in report["cases"]:
        lines.append(case["case_name"])
        lines.append(f"{case['score']:.2f}")
        lines.append(f"{case['covered']}/{case['total_tasks']}({case['coverage_pct']:.1f}%)")
        lines.append(f"{case['runtime_ms']}ms")
        lines.append(
            f"details: uncovered={case.get('uncovered_tasks', 0)}, "
            f"extra_notify={case.get('extra_notify', 0)}, "
            f"selected_lines={case.get('selected_lines', 0)}, "
            f"visible_total={case.get('visible_total', 0):.2f}"
        )
        if not case.get("valid", True):
            lines.append(f"ERROR: {case.get('message', 'invalid')}" )
        lines.append("")

    lines.append("Meta")
    illegal_cases = [
        case for case in report["cases"] if case.get("illegal_solution", False)
    ]
    if illegal_cases:
        lines.append(
            f"违规算例 {len(illegal_cases)} 个解不合法，已按最大惩罚计分"
        )
        for case in illegal_cases:
            lines.append(
                "违规算例："
                f"{case['case_name']} {case['score']:,.2f} 解不合法 {case['runtime_ms']}ms"
            )
    lines.append(f"scoring_mode={report['scoring_mode']}")
    lines.append(f"python_cmd={report.get('python_cmd', '')}")
    lines.append(f"max_case_seconds={report.get('max_case_seconds', '')}")
    lines.append(f"valid_cases={report['valid_cases']}")
    constraints = report.get("constraints", {})
    if constraints:
        lines.append(f"constraint_python={constraints.get('python', '')}")
        lines.append(f"constraint_solver_max_bytes={constraints.get('solver_max_bytes', '')}")
        lines.append(f"constraint_case_timeout_sec={constraints.get('case_timeout_sec', '')}")
    lines.append(f"merge_uncertainty_note={report.get('merge_uncertainty_note', '')}")
    return "\n".join(lines).rstrip() + "\n"


def write_report(report: dict, report_path: str | Path) -> str:
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = format_report(report)
    path.write_text(text, encoding="utf-8")
    json_path = path.with_suffix(".json")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
