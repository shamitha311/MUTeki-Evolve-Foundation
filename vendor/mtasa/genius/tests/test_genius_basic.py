from __future__ import annotations

import sys
from pathlib import Path

import pytest

from genius.genius_judge import run_judge
from genius.scoring_functions import FIXED_SCORING_MODE
from genius.solver_executor import ensure_solver_size_ok


def test_genius_basic(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases"
    case_dir.mkdir(parents=True)
    (case_dir / "case1.txt").write_text("T0001\nT0002\n", encoding="utf-8")

    solver = tmp_path / "solver.py"
    solver.write_text(
        "def solve(input_text):\n"
        "    return [('T0001', 'C001'), ('T0002', 'C002')]\n",
        encoding="utf-8",
    )

    out = run_judge(str(solver), str(case_dir), FIXED_SCORING_MODE)
    assert out["total_cases"] == 1
    assert out["solved_cases"] == 1


def test_cross_row_duplicate_solution_is_max_penalty_and_reported(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases"
    case_dir.mkdir(parents=True)
    rows = ["task_id_list\tcourier_id\ttotal_score\twillingness"]
    rows.append("T0001\tC001\t10\t1.0")
    rows.append("T0001\tC002\t11\t1.0")
    for idx in range(2, 31):
        rows.append(f"T{idx:04d}\tC{idx + 1:03d}\t10\t1.0")
    (case_dir / "high_noise_seed601.txt").write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )

    solver = tmp_path / "solver.py"
    solver.write_text(
        "def solve(input_text):\n"
        "    return [('T0001', 'C001'), ('T0001', 'C002')]\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "report.txt"

    out = run_judge(
        str(solver),
        str(case_dir),
        FIXED_SCORING_MODE,
        report=str(report_path),
        python_cmd=sys.executable,
    )
    case = out["cases"][0]
    report_text = report_path.read_text(encoding="utf-8")

    assert out["average_score"] == 3000.0
    assert out["valid_cases"] == 0
    assert case["score"] == 3000.0
    assert case["valid"] is False
    assert case["illegal_solution"] is True
    assert case["message"] == "解不合法"
    assert "违规算例 1 个解不合法，已按最大惩罚计分" in report_text
    assert "违规算例：high_noise_seed601 3,000.00 解不合法 " in report_text


def test_solver_preflight_rejects_future_import(tmp_path: Path) -> None:
    solver = tmp_path / "solver.py"
    solver.write_text(
        "from __future__ import annotations\n"
        "\n"
        "def solve(input_text):\n"
        "    return []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported top-level import"):
        ensure_solver_size_ok(solver)


def test_solver_preflight_rejects_non_whitelisted_top_level_import(tmp_path: Path) -> None:
    solver = tmp_path / "solver.py"
    solver.write_text(
        "import math\n"
        "\n"
        "def solve(input_text):\n"
        "    return []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported top-level import"):
        ensure_solver_size_ok(solver)


def test_solver_preflight_allows_platform_known_safe_imports(tmp_path: Path) -> None:
    solver = tmp_path / "solver.py"
    solver.write_text(
        "import heapq\n"
        "import random\n"
        "import time\n"
        "from collections import defaultdict\n"
        "\n"
        "def solve(input_text):\n"
        "    buckets = defaultdict(list)\n"
        "    return buckets['empty']\n",
        encoding="utf-8",
    )

    ensure_solver_size_ok(solver)
