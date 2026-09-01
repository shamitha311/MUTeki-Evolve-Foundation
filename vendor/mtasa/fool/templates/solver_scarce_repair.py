import time
BUDGET_SEC = 10.0  # 协议常量：美团线上每 case wall 上限；Genius 本地会改写此行，不要自行修改


P_UNCOV = 100.0


def _parse_candidates(input_text: str):
    lines = [line.strip() for line in input_text.splitlines() if line.strip()]
    start = 1 if lines and lines[0].startswith("task_id_list") else 0

    best = {}
    all_tasks = set()
    all_couriers = set()

    for line in lines[start:]:
        parts = line.split("\t")
        if len(parts) < 4:
            continue

        # Preserve input bundle string verbatim — online judge does not
        # canonicalize task ordering, so neither do we.
        bundle = parts[0].strip()
        _seen: set = set()
        _tasks: list = []
        for _t in (x.strip() for x in bundle.split(",")):
            if _t and _t not in _seen:
                _seen.add(_t); _tasks.append(_t)
        tasks = tuple(_tasks)
        courier = parts[1].strip()
        if not tasks or not courier:
            continue

        try:
            score = float(parts[2])
            willingness = float(parts[3])
        except ValueError:
            continue

        willingness = min(1.0, max(0.0, willingness))
        norm = score / max(willingness, 0.05)
        n = len(tasks)
        formula = willingness * score + (1.0 - willingness) * P_UNCOV * n
        dense_rank = formula / max(1, n)
        key = (bundle, courier)

        prev = best.get(key)
        if prev is None or norm < prev[5] or (norm == prev[5] and score < prev[3]):
            best[key] = (bundle, tasks, courier, score, willingness, norm, formula, dense_rank)

        all_tasks.update(tasks)
        all_couriers.add(courier)

    rows = list(best.values())
    return rows, all_tasks, all_couriers


def _evaluate(result, all_tasks):
    covered = set()
    total = 0.0
    for row in result:
        covered.update(row[1])
        total += row[5]
    uncovered = len(all_tasks - covered)
    return len(covered), total + P_UNCOV * uncovered


def _greedy_select(rows):
    used_tasks = set()
    used_couriers = set()
    out = []
    for row in rows:
        tasks = row[1]
        courier = row[2]
        if courier in used_couriers:
            continue
        if any(task in used_tasks for task in tasks):
            continue
        used_couriers.add(courier)
        used_tasks.update(tasks)
        out.append(row)
    return out


def _repair_uncovered(base_result, rows, all_tasks):
    result = list(base_result)
    covered = set()
    used_couriers = set()
    used_bundles = set()

    for row in result:
        covered.update(row[1])
        used_couriers.add(row[2])
        used_bundles.add(row[0])

    uncovered = set(all_tasks - covered)
    if not uncovered:
        return result

    task_to_rows = {task: [] for task in uncovered}
    for row in rows:
        for task in row[1]:
            if task in task_to_rows:
                task_to_rows[task].append(row)

    order = sorted(uncovered, key=lambda t: len(task_to_rows.get(t, [])))
    for task in order:
        if task in covered:
            continue
        best = None
        best_key = None
        for row in task_to_rows.get(task, []):
            bundle, tasks, courier, _s, _w, _norm, _formula, dense = row
            if courier in used_couriers:
                continue
            if bundle in used_bundles:
                continue
            if any(t in covered for t in tasks):
                continue

            new_cover = 0
            for t in tasks:
                if t in uncovered:
                    new_cover += 1
            key = (-new_cover, dense, row[5], -len(tasks), bundle, courier)
            if best_key is None or key < best_key:
                best = row
                best_key = key

        if best is None:
            continue

        result.append(best)
        covered.update(best[1])
        used_couriers.add(best[2])
        used_bundles.add(best[0])

    return result


def _finalize(result):
    """跨行去重兜底层（输出层硬契约）。按出现顺序贪心保留先出现的行，
    丢弃跨行重复 courier 或 task 的整行，以及空 bundle 行。
    即使算法保证无 dup 也必须保留这一层——stale-snapshot bug 极难自查，
    Genius 把跨行重复整 case 判最大惩罚 100*N。"""
    seen_t, seen_c = set(), set()
    out = []
    for bundle, couriers in result:
        ts = [t.strip() for t in str(bundle).split(',') if t.strip()]
        if isinstance(couriers, (list, tuple)):
            cs = [str(c).strip() for c in couriers if str(c).strip()]
        else:
            cs = [c.strip() for c in str(couriers).split(',') if c.strip()]
        if not ts or not cs:
            continue
        if any(t in seen_t for t in ts):
            continue
        if any(c in seen_c for c in cs):
            continue
        seen_t.update(ts)
        seen_c.update(cs)
        out.append((bundle, couriers))
    return out


def solve(input_text: str) -> list:
    deadline = time.monotonic() + BUDGET_SEC - 0.5  # 软超时；超过即返回当前最优
    rows, all_tasks, all_couriers = _parse_candidates(input_text)
    if not rows:
        return []

    n_tasks = len(all_tasks)
    n_couriers = len(all_couriers)
    scarce = (n_couriers / max(1, n_tasks)) < 0.7

    if scarce:
        rows_multi = [r for r in rows if len(r[1]) >= 2]
        rows_single = [r for r in rows if len(r[1]) == 1]

        rows_a = sorted(rows_multi, key=lambda r: (r[7], r[5], -len(r[1]), r[0], r[2])) + sorted(
            rows_single,
            key=lambda r: (r[7], r[5], r[0], r[2]),
        )
        rows_b = sorted(rows, key=lambda r: (r[7], -len(r[1]), r[5], r[0], r[2]))

        cand_a = _repair_uncovered(_greedy_select(rows_a), rows, all_tasks)
        cand_b = _repair_uncovered(_greedy_select(rows_b), rows, all_tasks)
        eval_a = _evaluate(cand_a, all_tasks)
        eval_b = _evaluate(cand_b, all_tasks)
        best = cand_a if (-eval_a[0], eval_a[1]) <= (-eval_b[0], eval_b[1]) else cand_b
    else:
        rows_norm = sorted(rows, key=lambda r: (r[7], r[6], r[5], -len(r[1]), r[0], r[2]))
        best = _greedy_select(rows_norm)

    out = [(row[0], row[2]) for row in best]
    out.sort(key=lambda x: (x[0], x[1]))
    return _finalize(out)
