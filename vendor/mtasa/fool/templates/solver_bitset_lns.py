BUDGET_SEC = 10.0  # 协议常量：美团线上每 case wall 上限；Genius 本地会改写此行，不要自行修改


import random
import time
from collections import defaultdict


P_UNCOV = 100.0
LNS_BUDGET_S = 1.25
POOL_TOP = 12
DESTROY_MAX = 5


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
        key = (bundle, courier)

        prev = best.get(key)
        if prev is None or norm < prev[5] or (norm == prev[5] and score < prev[3]):
            best[key] = (bundle, tasks, courier, score, willingness, norm)
        all_tasks.update(tasks)

    rows = list(best.values())
    rows.sort(key=lambda r: (r[5], r[3], -len(r[1]), r[0], r[2]))
    return rows, sorted(all_tasks)


def _fallback_greedy(rows):
    used_tasks = set()
    used_couriers = set()
    out = []
    for bundle, tasks, courier, _s, _w, _norm in rows:
        if courier in used_couriers:
            continue
        if any(task in used_tasks for task in tasks):
            continue
        used_couriers.add(courier)
        used_tasks.update(tasks)
        out.append((bundle, courier))
    return out


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
    if not rows or not all_tasks:
        return []

    if len(all_tasks) > 55:
        return _finalize(_fallback_greedy(rows))

    task_idx = {task: i for i, task in enumerate(all_tasks)}
    full_mask = (1 << len(all_tasks)) - 1

    by_bundle = defaultdict(list)
    for row in rows:
        by_bundle[row[0]].append(row)

    columns = []
    for bundle, bundle_rows in by_bundle.items():
        bundle_rows = sorted(bundle_rows, key=lambda r: (r[5], r[3], r[2]))[:POOL_TOP]
        for row in bundle_rows:
            _b, tasks, courier, _score, _w, norm = row
            mask = 0
            for task in tasks:
                mask |= 1 << task_idx[task]
            columns.append((norm, bundle, courier, mask))

    if not columns:
        return []

    columns.sort(key=lambda c: (c[0], c[1], c[2]))

    cols_by_task = [[] for _ in all_tasks]
    for i, col in enumerate(columns):
        mask = col[3]
        bit = mask
        t = 0
        while bit:
            if bit & 1:
                cols_by_task[t].append(i)
            bit >>= 1
            t += 1

    task_order = sorted(range(len(all_tasks)), key=lambda t: len(cols_by_task[t]))

    def sol_cost(sel):
        used_mask = 0
        total = 0.0
        for idx in sel:
            total += columns[idx][0]
            used_mask |= columns[idx][3]
        uncovered = bin(full_mask ^ used_mask).count('1')
        return total + P_UNCOV * uncovered

    def repair(seed_sel):
        used_mask = 0
        used_couriers = set()
        used_bundles = set()
        out = []

        for idx in seed_sel:
            _norm, bundle, courier, mask = columns[idx]
            if bundle in used_bundles or courier in used_couriers or (mask & used_mask):
                continue
            out.append(idx)
            used_bundles.add(bundle)
            used_couriers.add(courier)
            used_mask |= mask

        for tidx in task_order:
            bit = 1 << tidx
            if used_mask & bit:
                continue
            for col_idx in cols_by_task[tidx]:
                _norm, bundle, courier, mask = columns[col_idx]
                if bundle in used_bundles or courier in used_couriers or (mask & used_mask):
                    continue
                out.append(col_idx)
                used_bundles.add(bundle)
                used_couriers.add(courier)
                used_mask |= mask
                break
        return out

    current = repair([])
    best = list(current)
    current_cost = sol_cost(current)
    best_cost = current_cost

    rng = random.Random(0)
    deadline = time.perf_counter() + LNS_BUDGET_S

    while time.perf_counter() < deadline:
        if not current:
            break
        drop = min(DESTROY_MAX, max(1, len(current) // 3), len(current))
        victims = set(rng.sample(range(len(current)), drop))
        seed = [current[i] for i in range(len(current)) if i not in victims]

        candidate = repair(seed)
        cand_cost = sol_cost(candidate)

        if cand_cost + 1e-9 < current_cost:
            current = candidate
            current_cost = cand_cost
            if cand_cost + 1e-9 < best_cost:
                best = list(candidate)
                best_cost = cand_cost

    result = []
    for idx in best:
        _norm, bundle, courier, _mask = columns[idx]
        result.append((bundle, courier))

    if not result:
        return _finalize(_fallback_greedy(rows))
    result.sort(key=lambda x: (x[0], x[1]))
    return _finalize(result)
