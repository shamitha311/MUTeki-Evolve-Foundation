def check_output_shape(output_obj: object) -> tuple:
    if not isinstance(output_obj, list):
        return False, "output is not list"
    for i, row in enumerate(output_obj):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            return False, f"row {i} malformed"
        task_field, courier_field = row
        if not str(task_field).strip():
            return False, f"row {i} empty task_id_list"
        if not str(courier_field).strip():
            return False, f"row {i} empty courier list"
    return True, "ok"


def _split_csv(text: object) -> list:
    return [x.strip() for x in str(text).split(",") if x.strip()]


def _discover_case_sets(input_text: str) -> tuple:
    tasks = set()
    couriers = set()
    lines = [line.strip() for line in input_text.splitlines() if line.strip()]
    start = 1 if lines and lines[0].startswith("task_id_list") else 0
    for line in lines[start:]:
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        for task in _split_csv(parts[0]):
            tasks.add(task)
        courier = parts[1].strip()
        if courier:
            couriers.add(courier)
    return tasks, couriers


def validate_solution_against_input(input_text: str, output_obj: object) -> dict:
    ok, message = check_output_shape(output_obj)
    case_tasks, case_couriers = _discover_case_sets(input_text)

    if not ok:
        return {
            "ok": False,
            "message": message,
            "rows": 0,
            "covered": 0,
            "total_tasks": len(case_tasks),
            "invalid_rows": 1,
            "extra_notify": 0,
        }

    used_tasks = set()
    used_couriers = set()
    invalid_rows = 0
    extra_notify = 0

    for i, row in enumerate(output_obj):
        task_field, courier_field = row
        tasks = _split_csv(task_field)
        couriers = _split_csv(courier_field)

        row_bad = False
        row_couriers = set()
        for courier in couriers:
            if courier in row_couriers or courier in used_couriers:
                row_bad = True
            row_couriers.add(courier)
            if case_couriers and courier not in case_couriers:
                row_bad = True

        for task in tasks:
            if task in used_tasks:
                row_bad = True
            if case_tasks and task not in case_tasks:
                row_bad = True

        if row_bad:
            invalid_rows += 1
            continue

        used_tasks.update(tasks)
        used_couriers.update(row_couriers)
        extra_notify += max(0, len(couriers) - 1)

    return {
        "ok": invalid_rows == 0,
        "message": "ok" if invalid_rows == 0 else f"invalid_rows={invalid_rows}",
        "rows": len(output_obj),
        "covered": len(used_tasks),
        "total_tasks": len(case_tasks),
        "invalid_rows": invalid_rows,
        "extra_notify": extra_notify,
    }
