from __future__ import annotations

from pathlib import Path


def parse_case_text(case_text: str) -> tuple[dict[tuple[str, str], tuple[list[str], float, float]], set[str]]:
    lines = [line.strip() for line in case_text.splitlines() if line.strip()]
    start = 1 if lines and lines[0].startswith("task_id_list") else 0

    table: dict[tuple[str, str], tuple[list[str], float, float]] = {}
    all_tasks: set[str] = set()

    for line in lines[start:]:
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        # Preserve the input's literal bundle string as the table key.
        # The online judge does not canonicalize task ordering inside a
        # bundle, so neither do we — a solver that emits "T0002,T0001"
        # when the input said "T0001,T0002" must be rejected, not
        # silently accepted via local sort-normalization.
        task_text = parts[0].strip()
        if not task_text:
            continue
        raw_tasks = [x.strip() for x in task_text.split(",") if x.strip()]
        seen: set[str] = set()
        tasks: list[str] = []
        for t in raw_tasks:
            if t not in seen:
                seen.add(t)
                tasks.append(t)
        if not tasks:
            continue
        courier = parts[1].strip()
        try:
            score = float(parts[2])
            willingness = float(parts[3])
        except ValueError:
            continue

        key = (task_text, courier)
        cand = (tasks, score, willingness)
        prev = table.get(key)
        if prev is None:
            table[key] = cand
        else:
            prev_std = prev[1] / max(prev[2], 0.05)
            curr_std = score / max(willingness, 0.05)
            if curr_std < prev_std or (curr_std == prev_std and score < prev[1]):
                table[key] = cand

        all_tasks.update(tasks)

    return table, all_tasks


def normalize_solution_rows(raw_output: object) -> tuple[list[tuple[str, list[str]]], bool]:
    if not isinstance(raw_output, list):
        return [], True

    out: list[tuple[str, list[str]]] = []
    format_error = False
    for row in raw_output:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            format_error = True
            continue

        left, right = row
        # Preserve solver's emitted task ordering (no sort). The online
        # judge compares the output bundle string literally against the
        # input's bundle string; if the solver reorders tasks within a
        # merged bundle, the lookup must miss and the row must be flagged.
        if isinstance(left, (list, tuple)):
            raw_tasks = [str(x).strip() for x in left if str(x).strip()]
        else:
            raw_tasks = [x.strip() for x in str(left).split(",") if x.strip()]
        seen: set[str] = set()
        tasks: list[str] = []
        for t in raw_tasks:
            if t not in seen:
                seen.add(t)
                tasks.append(t)

        if isinstance(right, (list, tuple)):
            couriers = [str(x).strip() for x in right if str(x).strip()]
        else:
            couriers = [x.strip() for x in str(right).split(",") if x.strip()]

        if not tasks or not couriers:
            format_error = True
            continue
        out.append((",".join(tasks), couriers))

    return out, format_error


def _recursive_formula_no_bonus(pairs: list[tuple[float, float]], p_uncov: float = 100.0) -> float:
    tail = p_uncov
    for score, willingness in reversed(pairs):
        tail = willingness * score + (1.0 - willingness) * tail
    return tail


def _recursive_formula_bidirectional_avg(pairs: list[tuple[float, float]], p_uncov: float = 100.0) -> float:
    if not pairs:
        return p_uncov
    asc = sorted(pairs, key=lambda x: x[1])
    desc = list(reversed(asc))
    score_asc = _recursive_formula_no_bonus(asc, p_uncov=p_uncov)
    score_desc = _recursive_formula_no_bonus(desc, p_uncov=p_uncov)
    return 0.5 * (score_asc + score_desc)


def evaluate_case_solution(case_text: str, raw_output: object) -> dict:
    table, all_tasks = parse_case_text(case_text)
    chosen, format_error = normalize_solution_rows(raw_output)

    validation_errors: list[str] = []
    seen_tasks: dict[str, int] = {}
    seen_couriers: dict[str, int] = {}
    for idx, (task_text, couriers) in enumerate(chosen):
        tasks = task_text.split(",")
        row_couriers: set[str] = set()
        for courier in couriers:
            if courier in row_couriers:
                validation_errors.append(f"row {idx}: 行内骑手重复 {courier}")
            if courier in seen_couriers:
                validation_errors.append(
                    f"骑手 {courier} 跨行重复: row {seen_couriers[courier]} & {idx}"
                )
            if (task_text, courier) not in table:
                validation_errors.append(f"row {idx}: ({task_text},{courier}) 不在输入候选里")
            row_couriers.add(courier)
            seen_couriers[courier] = idx
        for task in tasks:
            if task in seen_tasks:
                validation_errors.append(
                    f"任务 {task} 跨行重复: row {seen_tasks[task]} & {idx}"
                )
            seen_tasks[task] = idx

    if validation_errors:
        total_tasks = len(all_tasks)
        max_penalty = 100.0 * total_tasks
        return {
            "valid": False,
            "format_error": bool(format_error),
            "message": "解不合法",
            "selected_lines": 0,
            "covered_tasks": 0,
            "total_tasks": int(total_tasks),
            "uncovered_tasks": int(total_tasks),
            "visible_total": 0.0,
            "official_like_score": float(round(max_penalty, 6)),
            "extra_notify": 0,
            "merged_rows": sum(1 for task_text, _ in chosen if "," in task_text),
            "invalid_rows": int(len(validation_errors)),
            "illegal_solution": True,
            "validation_errors": validation_errors[:20],
        }

    used_tasks: set[str] = set()
    used_couriers: set[str] = set()
    covered_tasks: set[str] = set()

    valid = not format_error
    visible_total = 0.0
    extra_notify = 0
    selected_lines = 0
    invalid_rows = 0
    merged_rows = 0

    for task_text, couriers in chosen:
        primary = couriers[0]
        row_invalid = False
        row_couriers: set[str] = set()
        if "," in task_text:
            merged_rows += 1
        key = (task_text, primary)
        cand = table.get(key)
        if cand is None:
            valid = False
            invalid_rows += 1
            continue

        tasks, score, willingness = cand
        if primary in used_couriers:
            valid = False
            row_invalid = True
        row_couriers.add(primary)
        if any(task in used_tasks for task in tasks):
            valid = False
            row_invalid = True

        selected_pairs: list[tuple[float, float]] = [(score, willingness)]
        backup_invalid = False
        for backup in couriers[1:]:
            if backup in row_couriers or backup in used_couriers:
                valid = False
                backup_invalid = True
            row_couriers.add(backup)
            bkey = (task_text, backup)
            bcand = table.get(bkey)
            if bcand is None:
                valid = False
                backup_invalid = True
                continue
            _, bscore, bw = bcand
            selected_pairs.append((bscore, bw))

        # Invalid rows are reported but excluded from score and coverage aggregation.
        if row_invalid or backup_invalid:
            invalid_rows += 1
            continue

        used_couriers.update(row_couriers)
        used_tasks.update(tasks)
        covered_tasks.update(tasks)

        row_p_uncov = 200.0 if "," in task_text else 100.0
        visible_total += _recursive_formula_bidirectional_avg(selected_pairs, p_uncov=row_p_uncov)
        extra_notify += max(0, len(couriers) - 1)
        selected_lines += 1

    uncovered_tasks = len(all_tasks - covered_tasks)
    official_like_score = visible_total + 100.0 * uncovered_tasks

    message = "ok"
    if format_error:
        message = "format_error"
    if invalid_rows > 0:
        message = f"{message}; invalid_rows={invalid_rows}" if message != "ok" else f"invalid_rows={invalid_rows}"

    return {
        "valid": bool(valid),
        "format_error": bool(format_error),
        "message": message,
        "selected_lines": int(selected_lines),
        "covered_tasks": int(len(covered_tasks)),
        "total_tasks": int(len(all_tasks)),
        "uncovered_tasks": int(uncovered_tasks),
        "visible_total": float(round(visible_total, 6)),
        "official_like_score": float(round(official_like_score, 6)),
        "extra_notify": int(extra_notify),
        "merged_rows": int(merged_rows),
        "invalid_rows": int(invalid_rows),
        "illegal_solution": False,
        "validation_errors": [],
    }


def evaluate_case_file(case_path: str | Path, raw_output: object) -> dict:
    text = Path(case_path).read_text(encoding="utf-8")
    return evaluate_case_solution(text, raw_output)
