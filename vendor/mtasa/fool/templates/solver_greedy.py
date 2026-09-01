import time
BUDGET_SEC = 10.0  # 协议常量：美团线上每 case wall 上限；Genius 本地会改写此行，不要自行修改


P_UNCOV = 100.0


def _parse_candidates(input_text: str):
    lines = [line.strip() for line in input_text.splitlines() if line.strip()]
    start = 1 if lines and lines[0].startswith("task_id_list") else 0

    best = {}
    all_tasks = set()

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

    rows = list(best.values())
    rows.sort(key=lambda r: (r[7], r[6], r[5], -len(r[1]), r[0], r[2]))
    return rows, all_tasks


def _score_solution(result, lookup, all_tasks):
    covered = set()
    total = 0.0
    for bundle, courier in result:
        row = lookup.get((bundle, courier))
        if row is None:
            continue
        _b, tasks, _c, _s, _w, norm, _formula, _dense = row
        total += norm
        covered.update(tasks)
    total += P_UNCOV * len(all_tasks - covered)
    return total


def _greedy(rows):
    used_tasks = set()
    used_couriers = set()
    result = []
    for bundle, tasks, courier, _s, _w, _norm, _formula, _dense in rows:
        if courier in used_couriers:
            continue
        if any(task in used_tasks for task in tasks):
            continue
        used_couriers.add(courier)
        used_tasks.update(tasks)
        result.append((bundle, courier))
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
    rows, all_tasks = _parse_candidates(input_text)
    if not rows:
        return []

    lookup = {(r[0], r[2]): r for r in rows}

    # Anchor 1: density-aware ranking.
    result_a = _greedy(rows)

    # Anchor 2: conservative visible-first ordering.
    rows_b = sorted(rows, key=lambda r: (r[5], r[3], r[7], -len(r[1]), r[0], r[2]))
    result_b = _greedy(rows_b)

    score_a = _score_solution(result_a, lookup, all_tasks)
    score_b = _score_solution(result_b, lookup, all_tasks)
    return _finalize(result_a if score_a <= score_b else result_b)
