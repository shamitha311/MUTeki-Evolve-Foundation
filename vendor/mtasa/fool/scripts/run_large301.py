"""
本地验证 runner: 对 official/large_seed301.txt 跑指定 solver，
报告 partner_f / 覆盖 / 合单数, 并把结果写入 history/large301/<tag>.txt.

用法:
    python3 run_large301.py solver_p3            # 模块名, tag 默认同名
    python3 run_large301.py solver_p3 p3_v1      # 指定 tag

每次提交前都应跑一次, 把分数贴到对应 score 文档.
"""

from __future__ import annotations

import importlib
import os
import sys
import time


CASE_PATH = "data/official/large_seed301.txt"
OUT_DIR = "history/large301"
P_UNCOV = 100.0


def _load_solo_table(input_text: str):
    """单送行的 (task, courier) -> (score, willingness), 用于报告分桶时复算."""
    solo = {}
    lines = input_text.strip().splitlines()
    start = 1 if lines and lines[0].startswith("task_id_list") else 0
    for line in lines[start:]:
        parts = line.strip().split("\t")
        if len(parts) < 4:
            continue
        bundle = parts[0].strip()
        if "," in bundle:
            continue
        try:
            solo[(bundle, parts[1].strip())] = (float(parts[2]), float(parts[3]))
        except ValueError:
            pass
    return solo


def _lookup_row(input_text: str, bundle: str, courier: str):
    lines = input_text.strip().splitlines()
    start = 1 if lines and lines[0].startswith("task_id_list") else 0
    target_tasks = sorted(bundle.split(","))
    for line in lines[start:]:
        parts = line.strip().split("\t")
        if len(parts) < 4:
            continue
        b_tasks = sorted(parts[0].strip().split(","))
        if b_tasks == target_tasks and parts[1].strip() == courier:
            try:
                return float(parts[2]), float(parts[3])
            except ValueError:
                return None
    return None


def evaluate(input_text: str, assignments, total_tasks: int):
    """评估 assignments. len(couriers)>=2 走 grab-order 期望损失 (平台模型),
    无论 n 是否为 1; 单骑手 (len(couriers)==1) 走 Formula A: w*s + (1-w)*100*n.
    """
    score_sum = 0.0
    w_sum = 0.0
    w_rows = 0
    partner = 0.0
    covered = 0
    combo = 0
    multi_lists = 0
    for bundle, couriers in assignments:
        n = bundle.count(",") + 1
        if len(couriers) >= 2:
            items = []
            for c in couriers:
                row = _lookup_row(input_text, bundle, c)
                if row is None:
                    raise RuntimeError(f"未在输入中找到 ({bundle}, {c})")
                items.append(row)  # (score, w)
            for s, w in items:
                score_sum += s
                w_sum += w
                w_rows += 1

            def _recur(order):
                survive = 1.0
                e = 0.0
                for s, w in order:
                    e += survive * w * s
                    survive *= max(0.0, 1.0 - w)
                return e + P_UNCOV * survive * n

            desc = sorted(items, key=lambda r: -r[1])
            asc = sorted(items, key=lambda r: r[1])
            partner += 0.5 * (_recur(desc) + _recur(asc))
            multi_lists += 1
            if n >= 2:
                combo += 1
        else:
            courier = couriers[0]
            row = _lookup_row(input_text, bundle, courier)
            if row is None:
                raise RuntimeError(f"未在输入中找到 ({bundle}, {courier})")
            s, w = row
            score_sum += s
            w_sum += w
            w_rows += 1
            partner += w * s + (1 - w) * P_UNCOV * n
            if n >= 2:
                combo += 1
        covered += n
    uncov = total_tasks - covered
    partner_with_uncov = partner + uncov * P_UNCOV
    return {
        "assigns": len(assignments),
        "coverage": f"{covered}/{total_tasks}",
        "uncov": uncov,
        "combo": combo,
        "multi_lists": multi_lists,
        "score_sum": round(score_sum, 2),
        "w_avg": round(w_sum / w_rows, 3) if w_rows else 0.0,
        "partner_f": round(partner_with_uncov, 2),
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    module_name = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) >= 3 else module_name

    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    os.chdir(repo_root)
    sys.path.insert(0, repo_root)

    with open(CASE_PATH, "r", encoding="utf-8") as f:
        input_text = f.read()

    total_tasks = len({
        t.strip()
        for line in input_text.splitlines()[1:]
        for t in line.split("\t")[0].split(",")
        if t.strip()
    })

    solver = importlib.import_module(module_name)
    t0 = time.time()
    assignments = solver.solve(input_text)
    elapsed = time.time() - t0

    metrics = evaluate(input_text, assignments, total_tasks)
    metrics["solver"] = module_name
    metrics["elapsed_s"] = round(elapsed, 2)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{tag}.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        for bundle, couriers in assignments:
            f.write(f"{bundle}\t{','.join(couriers)}\n")

    print(f"=== {module_name}  (tag={tag}) ===")
    for k in ("solver", "elapsed_s", "assigns", "coverage", "uncov",
              "combo", "multi_lists", "score_sum", "w_avg", "partner_f"):
        print(f"  {k}: {metrics[k]}")
    print(f"  written: {out_path}")


if __name__ == "__main__":
    main()
