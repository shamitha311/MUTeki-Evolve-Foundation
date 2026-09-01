from __future__ import annotations

from pathlib import Path

import pytest

from genius.genius_judge import run_judge
from genius.smoke import SmokeFailed, _check_solver_contract, run_smoke


@pytest.fixture
def smoke_enabled(monkeypatch):
    """Re-enable smoke (conftest disables it for the rest of the suite)."""
    monkeypatch.delenv("GENIUS_SKIP_SMOKE", raising=False)


def test_smoke_passes_for_clean_finalize_solver(tmp_path: Path, smoke_enabled) -> None:
    # Single-pass greedy with global used_couriers tracking + _finalize
    # tail is the minimum contract every Fool-generated solver should
    # satisfy. solver_minimal.py is the simplest such template.
    template = Path(__file__).resolve().parents[2] / "fool" / "templates" / "solver_minimal.py"
    r = run_smoke(str(template))
    assert r.passed, r.render()
    assert r.cases_run >= 1


def test_smoke_rejects_bundle_local_greedy_that_duplicates_couriers(
    tmp_path: Path, smoke_enabled
) -> None:
    # Buggy: picks best courier per bundle independently without a
    # global used_couriers set. Smoke's adv_001 has a magnet courier
    # in two bundles, so this solver dups it cross-row.
    solver = tmp_path / "buggy.py"
    solver.write_text(
        "import time\nBUDGET_SEC = 10.0\n_ = time.monotonic()\n"
        "def solve(input_text):\n"
        "    lines = [l.strip() for l in input_text.splitlines() if l.strip()]\n"
        "    start = 1 if lines and lines[0].startswith('task_id_list') else 0\n"
        "    by_bundle = {}\n"
        "    for line in lines[start:]:\n"
        "        parts = line.split('\\t')\n"
        "        if len(parts) < 4: continue\n"
        "        tasks = ','.join(sorted({x.strip() for x in parts[0].split(',') if x.strip()}))\n"
        "        courier = parts[1].strip()\n"
        "        try: norm = float(parts[2]) / max(float(parts[3]), 0.05)\n"
        "        except ValueError: continue\n"
        "        by_bundle.setdefault(tasks, []).append((norm, courier))\n"
        "    out = []\n"
        "    for bundle, lst in by_bundle.items():\n"
        "        lst.sort()\n"
        "        out.append((bundle, lst[0][1]))\n"
        "    return out\n",
        encoding="utf-8",
    )
    r = run_smoke(str(solver))
    assert not r.passed
    assert any("跨行重复" in err for f in r.failures for err in f.validation_errors)


def test_run_judge_raises_smokefailed_before_scoring(tmp_path: Path, smoke_enabled) -> None:
    # End-to-end: smoke gate must fire inside run_judge and abort
    # before the per-case scoring loop runs.
    solver = tmp_path / "buggy.py"
    solver.write_text(
        "import time\nBUDGET_SEC = 10.0\n_ = time.monotonic()\n"
        "def solve(input_text):\n"
        "    return [('T0001,T0002', 'C0001'), ('T0003,T0004', 'C0001')]\n",
        encoding="utf-8",
    )
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    (case_dir / "trivial.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\nT0001\tC0001\t10\t1.0\n",
        encoding="utf-8",
    )
    with pytest.raises(SmokeFailed) as exc:
        run_judge(str(solver), str(case_dir))
    assert exc.value.result.failures
    assert any(
        "跨行重复" in err
        for f in exc.value.result.failures
        for err in f.validation_errors
    )


def test_smoke_rejects_polish_pattern_magnet_backup(
    tmp_path: Path, smoke_enabled
) -> None:
    # Pins the adv_003 polish-pattern protection: if the case file is
    # ever deleted or weakened (e.g., the C9999 magnet courier becomes
    # unattractive), this test breaks instead of the protection silently
    # disappearing. The buggy solver does a primary greedy + per-row
    # backup augmentation without a global used_couriers check, which
    # is the same shape as _polish_with_backups stale-snapshot bugs.
    solver = tmp_path / "buggy_polish.py"
    solver.write_text(
        "import time\nBUDGET_SEC = 10.0\n_ = time.monotonic()\n"
        "def solve(input_text):\n"
        "    lines = [l.strip() for l in input_text.splitlines() if l.strip()]\n"
        "    start = 1 if lines and lines[0].startswith('task_id_list') else 0\n"
        "    by_bundle = {}\n"
        "    for line in lines[start:]:\n"
        "        parts = line.split('\\t')\n"
        "        if len(parts) < 4: continue\n"
        "        tasks = ','.join(sorted({x.strip() for x in parts[0].split(',') if x.strip()}))\n"
        "        c = parts[1].strip()\n"
        "        try: s = float(parts[2]); w = float(parts[3])\n"
        "        except ValueError: continue\n"
        "        by_bundle.setdefault(tasks, []).append((s/max(w,0.05), s, w, c))\n"
        "    used_t, used_c = set(), set()\n"
        "    rows = []\n"
        "    for bundle, cands in by_bundle.items():\n"
        "        cands.sort()\n"
        "        ts = bundle.split(',')\n"
        "        for norm, s, w, c in cands:\n"
        "            if c in used_c: continue\n"
        "            if any(t in used_t for t in ts): continue\n"
        "            rows.append([bundle, [c], w]); used_c.add(c); used_t.update(ts); break\n"
        "    for row in rows:\n"
        "        bundle, chain, prim_w = row\n"
        "        if prim_w >= 0.6: continue\n"
        "        backups = sorted([(w, c) for _, _, w, c in by_bundle[bundle] if c not in chain], reverse=True)\n"
        "        for w, c in backups[:2]:\n"
        "            chain.append(c)\n"
        "    return [(r[0], r[1]) for r in rows]\n",
        encoding="utf-8",
    )
    r = run_smoke(str(solver))
    assert not r.passed
    failed_cases = {f.case_name for f in r.failures}
    assert "adv_003_backup_magnet_polish" in failed_cases, (
        f"adv_003 must catch this polish-pattern bug; failed cases were {failed_cases}"
    )
    assert any(
        "C9999" in err
        for f in r.failures
        if f.case_name == "adv_003_backup_magnet_polish"
        for err in f.validation_errors
    ), "expected the C9999 magnet courier to surface in validation_errors"


def test_smoke_rejects_bundle_resorting(tmp_path: Path, smoke_enabled) -> None:
    # Pins the adv_004 bundle-ordering protection. The input lists every
    # merged bundle in reverse-alphabetical order ("T0002,T0001" etc.).
    # A solver that sorts task IDs inside the bundle before emitting will
    # produce "T0001,T0002", which the strict evaluator no longer accepts.
    # If the case file is removed or weakened (e.g., bundles re-listed in
    # alphabetical order), this test fails instead of the protection
    # silently disappearing.
    solver = tmp_path / "buggy_sorting.py"
    solver.write_text(
        "import time\nBUDGET_SEC = 10.0\n_ = time.monotonic()\n"
        "def solve(input_text):\n"
        "    lines = [l.strip() for l in input_text.splitlines() if l.strip()]\n"
        "    start = 1 if lines and lines[0].startswith('task_id_list') else 0\n"
        "    by_bundle = {}\n"
        "    for line in lines[start:]:\n"
        "        parts = line.split('\\t')\n"
        "        if len(parts) < 4: continue\n"
        "        # Sorting the task IDs inside a bundle is the bug being tested.\n"
        "        tasks = ','.join(sorted({x.strip() for x in parts[0].split(',') if x.strip()}))\n"
        "        c = parts[1].strip()\n"
        "        try: norm = float(parts[2]) / max(float(parts[3]), 0.05)\n"
        "        except ValueError: continue\n"
        "        by_bundle.setdefault(tasks, []).append((norm, c))\n"
        "    used_t, used_c = set(), set()\n"
        "    out = []\n"
        "    for bundle, cands in by_bundle.items():\n"
        "        cands.sort()\n"
        "        ts = bundle.split(',')\n"
        "        for norm, c in cands:\n"
        "            if c in used_c: continue\n"
        "            if any(t in used_t for t in ts): continue\n"
        "            out.append((bundle, [c]))\n"
        "            used_c.add(c); used_t.update(ts); break\n"
        "    return out\n",
        encoding="utf-8",
    )
    r = run_smoke(str(solver))
    assert not r.passed
    failed_cases = {f.case_name for f in r.failures}
    assert "adv_004_bundle_ordering" in failed_cases, (
        f"adv_004 must catch bundle re-sorting; failed cases were {failed_cases}"
    )
    assert any(
        "不在输入候选里" in err
        for f in r.failures
        if f.case_name == "adv_004_bundle_ordering"
        for err in f.validation_errors
    ), "expected 不在输入候选里 (bundle key mismatch) in validation_errors"


def test_smoke_rejects_typing_import(tmp_path: Path, smoke_enabled) -> None:
    # `from typing import ...` triggers a 10/10 all-error online failure
    # even though typing is stdlib. The static gate must catch it before
    # any case runs.
    solver = tmp_path / "with_typing.py"
    solver.write_text(
        "from typing import List, Tuple\n"
        "def solve(input_text: str) -> list:\n"
        "    return []\n",
        encoding="utf-8",
    )
    r = run_smoke(str(solver))
    assert not r.passed
    assert r.cases_run == 0
    assert r.failures[0].case_name == "__contract__"
    assert any("typing" in err for err in r.failures[0].validation_errors)


def test_smoke_rejects_subscripted_return_annotation(
    tmp_path: Path, smoke_enabled
) -> None:
    # `-> List[Tuple[str, str]]` form must be rejected even without a
    # typing import (e.g. someone shadows List/Tuple locally).
    solver = tmp_path / "subscripted.py"
    solver.write_text(
        "List = list\n"
        "Tuple = tuple\n"
        "def solve(input_text: str) -> List[Tuple[str, str]]:\n"
        "    return []\n",
        encoding="utf-8",
    )
    r = run_smoke(str(solver))
    assert not r.passed
    assert r.failures[0].case_name == "__contract__"
    assert any("bare `list`" in err for err in r.failures[0].validation_errors)


def test_contract_gate_accepts_canonical_and_omitted_annotations(tmp_path: Path) -> None:
    # Direct unit test of the static gate — no python3.6 subprocess needed.
    # Each fixture must satisfy the BUDGET_SEC protocol so the signature
    # branch is what we're actually exercising.
    prefix = "import time\nBUDGET_SEC = 10.0\n_ = time.monotonic()\n"
    for body in (
        "def solve(input_text: str) -> list:\n    return []\n",
        "def solve(input_text):\n    return []\n",  # no annotation: also OK
        "from collections import defaultdict\n"
        "def solve(input_text: str) -> list:\n    return []\n",
    ):
        solver = tmp_path / "good.py"
        solver.write_text(prefix + body, encoding="utf-8")
        assert _check_solver_contract(str(solver)) == [], body


def test_contract_gate_rejects_missing_solve(tmp_path: Path) -> None:
    solver = tmp_path / "no_solve.py"
    solver.write_text("def helper(): return []\n", encoding="utf-8")
    errs = _check_solver_contract(str(solver))
    assert errs and "missing top-level" in errs[0]


def test_skip_smoke_param_bypasses_gate(tmp_path: Path, smoke_enabled) -> None:
    # The explicit kwarg escape hatch must work for direct callers.
    solver = tmp_path / "buggy.py"
    solver.write_text(
        "import time\nBUDGET_SEC = 10.0\n_ = time.monotonic()\n"
        "def solve(input_text):\n"
        "    return [('T0001', 'C001')]\n",
        encoding="utf-8",
    )
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    (case_dir / "trivial.txt").write_text(
        "task_id_list\tcourier_id\ttotal_score\twillingness\nT0001\tC001\t10\t1.0\n",
        encoding="utf-8",
    )
    # Without skip_smoke, this would raise SmokeFailed (buggy solver).
    out = run_judge(str(solver), str(case_dir), skip_smoke=True)
    assert out["total_cases"] == 1
