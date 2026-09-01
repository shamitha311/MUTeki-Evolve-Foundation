from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


TASK_RE = re.compile(r"T\d+")


@dataclass
class NormalizedAssignment:
    tasks: list[str]
    couriers: list[str]


@dataclass
class ValidationResult:
    valid: bool
    format_error: bool
    covered: int
    total_tasks: int
    rows: int
    extra_notify: int
    merged_rows: int
    invalid_rows: int
    message: str


def discover_tasks(case_text: str) -> list[str]:
    return sorted(set(TASK_RE.findall(case_text)))


def _norm_list(value: object, sep: str = ",") -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(sep) if p.strip()]


def normalize_solver_output(raw_output: object) -> tuple[list[NormalizedAssignment], int, bool]:
    assignments: list[NormalizedAssignment] = []
    invalid_rows = 0
    format_error = False

    if not isinstance(raw_output, list):
        return assignments, 1, True

    for row in raw_output:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            invalid_rows += 1
            format_error = True
            continue
        tasks = _norm_list(row[0])
        couriers = _norm_list(row[1])
        if not tasks or not couriers:
            invalid_rows += 1
            format_error = True
            continue
        assignments.append(NormalizedAssignment(tasks=tasks, couriers=couriers))

    return assignments, invalid_rows, format_error


def validate_assignments(case_text: str, raw_output: object) -> ValidationResult:
    tasks_in_case = discover_tasks(case_text)
    assignments, invalid_rows, format_error = normalize_solver_output(raw_output)

    used_tasks: set[str] = set()
    used_couriers: set[str] = set()

    merged_rows = 0
    extra_notify = 0

    for row in assignments:
        if len(row.tasks) > 1:
            merged_rows += 1
        extra_notify += max(0, len(row.couriers) - 1)

        row_invalid = False
        row_couriers: set[str] = set()
        for courier_id in row.couriers:
            if courier_id in row_couriers or courier_id in used_couriers:
                row_invalid = True
            row_couriers.add(courier_id)
        if row_invalid:
            invalid_rows += 1
        used_couriers.update(row_couriers)

        for task_id in row.tasks:
            if task_id in used_tasks:
                invalid_rows += 1
            used_tasks.add(task_id)

    if tasks_in_case:
        covered = len(used_tasks.intersection(set(tasks_in_case)))
        total_tasks = len(tasks_in_case)
    else:
        covered = len(used_tasks)
        total_tasks = len(used_tasks)

    valid = invalid_rows == 0
    if valid:
        message = "ok"
    elif format_error:
        message = f"format_error: invalid_rows={invalid_rows}"
    else:
        message = f"invalid_rows={invalid_rows}"

    return ValidationResult(
        valid=valid,
        format_error=format_error,
        covered=covered,
        total_tasks=total_tasks,
        rows=len(assignments),
        extra_notify=extra_notify,
        merged_rows=merged_rows,
        invalid_rows=invalid_rows,
        message=message,
    )


def list_case_files(input_dir: str | Path) -> list[Path]:
    root = Path(input_dir)
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Input directory not found: {root}")
    return sorted(path for path in root.glob("*.txt") if path.is_file())
