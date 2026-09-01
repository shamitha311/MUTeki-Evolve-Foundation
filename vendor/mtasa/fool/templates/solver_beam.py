import time
BUDGET_SEC = 10.0  # 协议常量：美团线上每 case wall 上限；Genius 本地会改写此行，不要自行修改


P_UNCOV = 100.0
PER_TASK_TOP = 18
BEAM_WIDTH = 36


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
    return rows, sorted(all_tasks)


def _row_mask(tasks, task_idx):
    mask = 0
    for task in tasks:
        mask |= 1 << task_idx[task]
    return mask


def _fallback_greedy(rows):
    used_tasks = set()
    used_couriers = set()
    out = []
    rows = sorted(rows, key=lambda r: (r[7], r[6], r[5], -len(r[1]), r[0], r[2]))
    for bundle, tasks, courier, _s, _w, _norm, _formula, _dense in rows:
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

    row_masks = []
    for row in rows:
        row_masks.append(_row_mask(row[1], task_idx))

    per_task_rows = [[] for _ in all_tasks]
    for i, row in enumerate(rows):
        for task in row[1]:
            per_task_rows[task_idx[task]].append(i)

    for i in range(len(per_task_rows)):
        per_task_rows[i].sort(key=lambda ridx: (rows[ridx][7], rows[ridx][6], rows[ridx][5], ridx))
        if len(per_task_rows[i]) > PER_TASK_TOP:
            per_task_rows[i] = per_task_rows[i][:PER_TASK_TOP]

    task_order = sorted(range(len(all_tasks)), key=lambda t: len(per_task_rows[t]))

    # (sum_norm, covered_mask, used_couriers_frozenset, chosen_indices_tuple)
    states = [(0.0, 0, frozenset(), ())]

    for tidx in task_order:
        next_states = []
        for cur_norm, cur_mask, used_couriers, chosen in states:
            bit = 1 << tidx
            if cur_mask & bit:
                next_states.append((cur_norm, cur_mask, used_couriers, chosen))
                continue

            expanded = False
            for ridx in per_task_rows[tidx]:
                row = rows[ridx]
                courier = row[2]
                rmask = row_masks[ridx]
                if courier in used_couriers:
                    continue
                if rmask & cur_mask:
                    continue
                expanded = True
                next_states.append(
                    (
                        cur_norm + row[5],
                        cur_mask | rmask,
                        used_couriers | {courier},
                        chosen + (ridx,),
                    )
                )

            if not expanded:
                next_states.append((cur_norm, cur_mask, used_couriers, chosen))

        # Lightweight dedup then beam pruning.
        best_by_key = {}
        for state in next_states:
            cur_norm, cur_mask, used_couriers, chosen = state
            key = (cur_mask, tuple(sorted(used_couriers)))
            prev = best_by_key.get(key)
            if prev is None or cur_norm < prev[0]:
                best_by_key[key] = (cur_norm, cur_mask, used_couriers, chosen)

        states = list(best_by_key.values())
        states.sort(
            key=lambda st: (
                -bin(st[1]).count('1'),
                st[0],
                len(st[3]),
            )
        )
        if len(states) > BEAM_WIDTH:
            states = states[:BEAM_WIDTH]

    if not states:
        return _finalize(_fallback_greedy(rows))

    best_state = min(
        states,
        key=lambda st: (st[0] + P_UNCOV * bin(full_mask ^ st[1]).count('1'), -bin(st[1]).count('1')),
    )

    picked = []
    for ridx in best_state[3]:
        row = rows[ridx]
        picked.append((row[0], row[2]))

    if not picked:
        return _finalize(_fallback_greedy(rows))
    return _finalize(picked)
