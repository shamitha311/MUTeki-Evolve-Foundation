# Per-Bucket Incumbent Architecture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace MTASA's single global incumbent with per-bucket incumbents so that a round which improves one bucket (e.g. `scarce_couriers_seed401`) at the cost of staying flat in another bucket is recorded as a real win, the per-bucket champion solver is preserved, and the submitted "best" solver is assembled by a router that dispatches to each bucket's champion.

**Architecture:**
- Add a `BucketIncumbents` store under `out/memory/runs/<fp>/buckets/<bucket_name>/` that holds the champion solver + report meta for each bucket independently.
- Replace `_classify_round_outcome(score, best_score) -> str` with a bucket-aware variant that consumes per-bucket Δ from the Genius report, produces a structured `RoundOutcome` (label + per-bucket replacement decisions), and emits `improved` whenever the target bucket beats its own incumbent without breaking any other bucket's incumbent band.
- Add a router solver builder that imports each bucket's champion `solve()` into one file and dispatches at runtime by computing the dataset fingerprint at call time. Submit the router separately so the scoreboard shows both single-solver-best and router-best.
- Keep the existing global `best_solver.py` mirror as a backward-compatible alias (= router output) so nothing downstream that reads it breaks.

**Tech Stack:** Python 3.9, pure stdlib (hard constraint from `CLAUDE.md`), `pytest` for tests under `genius/tests/`, no new dependencies.

**Out of scope (separate plan):** The `outcome_reflector` `unexpected response kind='retry'` bug and the missing improved-path lesson writing — those are independent of incumbent storage and will be handled in a follow-up plan.

---

## File Structure

**New files:**
- `fool/bucket_incumbents.py` — `BucketIncumbents` class (per-bucket champion storage + replacement rules).
- `fool/bucket_classify.py` — `RoundOutcome` dataclass and `classify_round_bucketed(...)` function.
- `fool/router_builder.py` — assembles per-bucket champion sources into one router `solve()` file.
- `genius/tests/test_bucket_incumbents.py`
- `genius/tests/test_bucket_classify.py`
- `genius/tests/test_router_builder.py`

**Modified files:**
- `fool/fool_loop.py` — wire `BucketIncumbents` into the main loop, replace `_classify_round_outcome` call site, emit router submission per round.
- `fool/memory_store.py` — expose a `bucket_incumbents` property on `FoolMemory` so existing callers reach the new store without import churn.
- `frontend/server.py` — scoreboard reads router-best in addition to single-solver-best (two columns).
- `frontend/index.html` and `frontend/app.js` — render the new column.
- `genius/report_writer.py` (read-only) — verify the per-bucket section format the parser will consume; no edits expected, just confirm.

---

## Task 0: Read incoming context

**Files:** none (read-only).

- [ ] **Step 1: Confirm current incumbent flow**

Run: `grep -n "_classify_round_outcome\|update_best\|stored_best_score" fool/fool_loop.py fool/memory_store.py`
Expected: classifier at `fool/fool_loop.py:736`, `stored_best_score` at `fool/memory_store.py:628`, `update_best` writes `best_solver.py` + `best_report.txt` + `best_meta.json` at `fool/memory_store.py:651`.

- [ ] **Step 2: Confirm bucket scores are present in Genius reports**

Run: `head -50 out/runs/run_20260604_002042/report_v018.txt`
Expected: blocks per bucket name (`high_noise_seed601`, `large_seed301`, …) each followed by a numeric line (the bucket score) and a `details: ...` line.

- [ ] **Step 3: Confirm dataset bucket list is stable**

Run: `ls data/sample_10_cases/ | sort`
Expected: 10 `.txt` files matching the 10 bucket names from `CLAUDE.md` (`tiny_seed42`, `small_seed100`, `medium_seed201/202/203`, `large_seed301/302`, `low_willingness_seed501`, `scarce_couriers_seed401`, `high_noise_seed601`).

---

## Task 1: Parse per-bucket scores from a Genius report (pure helper)

**Files:**
- Create: `fool/bucket_classify.py`
- Test: `genius/tests/test_bucket_classify.py`

**Why first:** Every later task consumes "bucket name → score" mappings. Lock the parser before anything else.

- [ ] **Step 1: Write the failing test**

```python
# genius/tests/test_bucket_classify.py
from pathlib import Path
from fool.bucket_classify import parse_bucket_scores

REPORT_FIXTURE = """Average Penalty Score
760.25
Completed Cases
10 / 10

high_noise_seed601
580.83
30/30(100.0%)
141ms
details: uncovered=0, extra_notify=26, selected_lines=29, visible_total=580.83

large_seed301
753.80
40/40(100.0%)
216ms
details: uncovered=0, extra_notify=32, selected_lines=40, visible_total=753.80
"""

def test_parse_bucket_scores_extracts_each_bucket(tmp_path):
    report = tmp_path / "report.txt"
    report.write_text(REPORT_FIXTURE, encoding="utf-8")
    result = parse_bucket_scores(report)
    assert result == {
        "high_noise_seed601": 580.83,
        "large_seed301": 753.80,
    }


def test_parse_bucket_scores_missing_file_returns_empty(tmp_path):
    result = parse_bucket_scores(tmp_path / "nope.txt")
    assert result == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest genius/tests/test_bucket_classify.py -v`
Expected: ImportError on `fool.bucket_classify`.

- [ ] **Step 3: Write minimal implementation**

```python
# fool/bucket_classify.py
from __future__ import annotations

from pathlib import Path


_BUCKET_NAMES = {
    "tiny_seed42", "small_seed100",
    "medium_seed201", "medium_seed202", "medium_seed203",
    "large_seed301", "large_seed302",
    "low_willingness_seed501", "scarce_couriers_seed401",
    "high_noise_seed601",
}


def parse_bucket_scores(report_path: Path) -> dict[str, float]:
    """Extract {bucket_name: score} from a Genius TXT report.

    A bucket block in the report is a known bucket name on its own line
    followed by a numeric line. We tolerate extra blocks; unknown names
    are skipped silently.
    """
    if not report_path.exists():
        return {}
    out: dict[str, float] = {}
    lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        name = line.strip()
        if name not in _BUCKET_NAMES:
            continue
        if i + 1 >= len(lines):
            continue
        try:
            out[name] = float(lines[i + 1].strip())
        except ValueError:
            continue
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest genius/tests/test_bucket_classify.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/bucket_classify.py genius/tests/test_bucket_classify.py
git commit -m "fool: add per-bucket score parser for Genius reports"
```

---

## Task 2: `RoundOutcome` dataclass + bucket-aware classifier

**Files:**
- Modify: `fool/bucket_classify.py`
- Test: `genius/tests/test_bucket_classify.py`

The new classifier returns a structured object instead of a single string. The legacy `_classify_round_outcome` will be replaced in Task 5 — here we only add the new function.

- [ ] **Step 1: Add failing tests for the classifier**

Append to `genius/tests/test_bucket_classify.py`:

```python
from fool.bucket_classify import RoundOutcome, classify_round_bucketed


def test_classify_improved_when_target_bucket_strictly_better_others_in_band():
    incumbents = {"scarce_couriers_seed401": 950.0, "large_seed301": 760.0, "high_noise_seed601": 580.0}
    new_scores = {"scarce_couriers_seed401": 900.0, "large_seed301": 760.5, "high_noise_seed601": 580.2}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "improved"
    assert outcome.bucket_replacements == {"scarce_couriers_seed401"}
    assert outcome.broken_buckets == set()


def test_classify_regressed_when_other_bucket_breaks_its_incumbent():
    incumbents = {"scarce_couriers_seed401": 950.0, "large_seed301": 760.0}
    new_scores = {"scarce_couriers_seed401": 900.0, "large_seed301": 800.0}  # large broke
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "regressed"
    assert "large_seed301" in outcome.broken_buckets
    assert outcome.bucket_replacements == set()


def test_classify_neutral_when_target_only_in_band():
    incumbents = {"scarce_couriers_seed401": 950.0}
    new_scores = {"scarce_couriers_seed401": 949.0}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "neutral"
    assert outcome.bucket_replacements == set()


def test_classify_baseline_when_no_incumbents_yet():
    outcome = classify_round_bucketed(
        new_scores={"scarce_couriers_seed401": 900.0},
        bucket_incumbents={},
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "baseline"
    assert outcome.bucket_replacements == {"scarce_couriers_seed401"}


def test_classify_silent_improvement_in_non_target_bucket_is_kept():
    """A non-target bucket that quietly improved should still seed that
    bucket's incumbent — we don't penalize lucky wins."""
    incumbents = {"scarce_couriers_seed401": 950.0, "large_seed301": 760.0}
    new_scores = {"scarce_couriers_seed401": 949.5, "large_seed301": 700.0}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incumbents,
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    assert outcome.label == "improved"
    assert outcome.bucket_replacements == {"large_seed301"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest genius/tests/test_bucket_classify.py -v`
Expected: 5 failures on the new tests (NameError / AttributeError).

- [ ] **Step 3: Implement the classifier**

Append to `fool/bucket_classify.py`:

```python
from dataclasses import dataclass, field


@dataclass
class RoundOutcome:
    """Result of classifying one round against per-bucket incumbents.

    label:                "baseline" | "improved" | "neutral" | "regressed" | "catastrophic"
    bucket_replacements:  buckets whose champion solver should be replaced this round
    broken_buckets:       buckets whose new score is worse than their incumbent by > band
                          (set even when label == "improved" if a non-target bucket regressed
                           but the target won; in that case label is forced to "regressed")
    bucket_deltas:        {bucket: new_score - incumbent_score} for every bucket present in
                          new_scores; positive means worse (penalty score, lower = better).
    """

    label: str
    bucket_replacements: set[str] = field(default_factory=set)
    broken_buckets: set[str] = field(default_factory=set)
    bucket_deltas: dict[str, float] = field(default_factory=dict)


def classify_round_bucketed(
    *,
    new_scores: dict[str, float],
    bucket_incumbents: dict[str, float],
    target_buckets: list[str],
    band_rel: float = 0.003,
) -> RoundOutcome:
    """Classify a round per-bucket. Penalty score: lower is better.

    A bucket is "improved on" iff new < incumbent - band(incumbent).
    A bucket is "broken" iff new > incumbent + band(incumbent).
    target_buckets is only used to decide the overall label; replacement of
    non-target buckets still happens when they strictly improved on their own.
    """
    targets = {t for t in target_buckets if t}
    replacements: set[str] = set()
    broken: set[str] = set()
    deltas: dict[str, float] = {}

    for bucket, new in new_scores.items():
        incumbent = bucket_incumbents.get(bucket)
        if incumbent is None:
            replacements.add(bucket)
            deltas[bucket] = 0.0
            continue
        deltas[bucket] = new - incumbent
        band = abs(incumbent) * band_rel
        if new < incumbent - band:
            replacements.add(bucket)
        elif new > incumbent + band:
            broken.add(bucket)

    if not bucket_incumbents:
        return RoundOutcome(label="baseline", bucket_replacements=replacements, bucket_deltas=deltas)

    # Catastrophic: any bucket more than 50% worse than its incumbent.
    for bucket, new in new_scores.items():
        inc = bucket_incumbents.get(bucket)
        if inc is not None and new > inc * 1.5:
            return RoundOutcome(
                label="catastrophic",
                bucket_replacements=replacements,
                broken_buckets=broken,
                bucket_deltas=deltas,
            )

    if broken:
        return RoundOutcome(
            label="regressed",
            bucket_replacements=replacements,
            broken_buckets=broken,
            bucket_deltas=deltas,
        )

    # No broken buckets. Did we improve anywhere?
    if replacements:
        return RoundOutcome(
            label="improved",
            bucket_replacements=replacements,
            broken_buckets=broken,
            bucket_deltas=deltas,
        )

    return RoundOutcome(label="neutral", broken_buckets=broken, bucket_deltas=deltas)
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `python -m pytest genius/tests/test_bucket_classify.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/bucket_classify.py genius/tests/test_bucket_classify.py
git commit -m "fool: add RoundOutcome + bucket-aware classifier"
```

---

## Task 3: `BucketIncumbents` storage

**Files:**
- Create: `fool/bucket_incumbents.py`
- Test: `genius/tests/test_bucket_incumbents.py`

Persistence layout (one file per bucket, no shared mutable state):

```
out/memory/runs/<fp>/buckets/
  scarce_couriers_seed401/
    champion.py
    meta.json   # {"score": 950.0, "round": 7, "global_v": 18}
  large_seed301/
    champion.py
    meta.json
  ...
```

- [ ] **Step 1: Write failing tests**

```python
# genius/tests/test_bucket_incumbents.py
import json
from pathlib import Path

from fool.bucket_incumbents import BucketIncumbents


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_empty_store_returns_no_incumbents(tmp_path):
    store = BucketIncumbents(tmp_path / "buckets")
    assert store.scores() == {}
    assert store.champion_path("scarce_couriers_seed401") is None


def test_record_creates_champion_and_meta(tmp_path):
    store = BucketIncumbents(tmp_path / "buckets")
    solver = _write(tmp_path / "solver_v007.py", "def solve(x): return []\n")
    store.record(bucket="scarce_couriers_seed401", solver_path=solver, score=950.0, round_index=7, global_v=18)
    assert store.scores() == {"scarce_couriers_seed401": 950.0}
    champ = store.champion_path("scarce_couriers_seed401")
    assert champ is not None and champ.exists()
    assert "def solve" in champ.read_text(encoding="utf-8")
    meta = json.loads((store.root / "scarce_couriers_seed401" / "meta.json").read_text(encoding="utf-8"))
    assert meta["score"] == 950.0
    assert meta["round"] == 7
    assert meta["global_v"] == 18


def test_record_overwrites_previous_champion(tmp_path):
    store = BucketIncumbents(tmp_path / "buckets")
    s1 = _write(tmp_path / "a.py", "# v1\ndef solve(x): return []\n")
    s2 = _write(tmp_path / "b.py", "# v2\ndef solve(x): return []\n")
    store.record(bucket="large_seed301", solver_path=s1, score=800.0, round_index=1, global_v=1)
    store.record(bucket="large_seed301", solver_path=s2, score=750.0, round_index=2, global_v=2)
    assert store.scores()["large_seed301"] == 750.0
    assert "# v2" in store.champion_path("large_seed301").read_text(encoding="utf-8")


def test_seed_from_legacy_global_best_populates_every_known_bucket(tmp_path):
    store = BucketIncumbents(tmp_path / "buckets")
    legacy = _write(tmp_path / "best_solver.py", "def solve(x): return []\n")
    legacy_meta = {
        "bucket_scores": {
            "scarce_couriers_seed401": 950.0,
            "large_seed301": 760.0,
        }
    }
    store.seed_from_legacy(solver_path=legacy, bucket_scores=legacy_meta["bucket_scores"])
    assert store.scores() == {"scarce_couriers_seed401": 950.0, "large_seed301": 760.0}
    # Same source bytes copied into both bucket dirs.
    for b in ("scarce_couriers_seed401", "large_seed301"):
        assert store.champion_path(b).read_text(encoding="utf-8") == legacy.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest genius/tests/test_bucket_incumbents.py -v`
Expected: ImportError on `fool.bucket_incumbents`.

- [ ] **Step 3: Implement `BucketIncumbents`**

```python
# fool/bucket_incumbents.py
from __future__ import annotations

import json
import shutil
from pathlib import Path


_CHAMPION_NAME = "champion.py"
_META_NAME = "meta.json"


class BucketIncumbents:
    """Per-bucket champion solver store.

    Layout under ``root``::

        <root>/<bucket>/champion.py
        <root>/<bucket>/meta.json   {"score": float, "round": int, "global_v": int}
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def scores(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for bucket_dir in sorted(self.root.iterdir()) if self.root.exists() else []:
            if not bucket_dir.is_dir():
                continue
            meta = bucket_dir / _META_NAME
            if not meta.exists():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                out[bucket_dir.name] = float(data["score"])
            except (OSError, ValueError, KeyError):
                continue
        return out

    def champion_path(self, bucket: str) -> Path | None:
        path = self.root / bucket / _CHAMPION_NAME
        return path if path.exists() else None

    def record(
        self,
        *,
        bucket: str,
        solver_path: Path,
        score: float,
        round_index: int,
        global_v: int | None = None,
    ) -> None:
        bucket_dir = self.root / bucket
        bucket_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(solver_path, bucket_dir / _CHAMPION_NAME)
        (bucket_dir / _META_NAME).write_text(
            json.dumps(
                {"score": float(score), "round": int(round_index), "global_v": global_v},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def seed_from_legacy(self, *, solver_path: Path, bucket_scores: dict[str, float]) -> None:
        """One-time migration: copy the legacy global best_solver.py into every
        bucket slot using the per-bucket scores observed at the time the legacy
        incumbent was crowned. Called once at FoolMemory init if no buckets/
        directory exists yet but a legacy best_solver.py is present."""
        for bucket, score in bucket_scores.items():
            self.record(bucket=bucket, solver_path=solver_path, score=float(score), round_index=0)
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `python -m pytest genius/tests/test_bucket_incumbents.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/bucket_incumbents.py genius/tests/test_bucket_incumbents.py
git commit -m "fool: add BucketIncumbents store for per-bucket champions"
```

---

## Task 4: Router solver builder

**Files:**
- Create: `fool/router_builder.py`
- Test: `genius/tests/test_router_builder.py`

A router solver is one `.py` file that contains every bucket champion's `solve()` (renamed to `solve_<bucket>`) plus a top-level `solve(input_text)` that:
1. detects which bucket the dataset belongs to by reading the case name from a sidecar OR a heuristic on input shape;
2. calls the matching `solve_<bucket>(input_text)`.

Since Genius calls `solve()` once per case file and we know the case file name maps directly to a bucket name (`scarce_couriers_seed401.txt` → `scarce_couriers_seed401`), the dispatch needs an external signal. Genius's `solver_executor.py` runs the solver under `python3.9` as a subprocess and **does** pass the case name as `argv[1]` (verify in Step 1 below). The router reads `sys.argv[1]` if present and falls back to a single-bucket "default" champion if not.

- [ ] **Step 1: Confirm how Genius passes case identity to the solver**

Run: `grep -nE "argv|case_name|subprocess.run|input_text" genius/solver_executor.py | head -20`
Expected: confirm that `solver_executor.py` either passes the case name on the command line OR sets an env var. Capture the actual mechanism — **if neither is true, the router must use a fingerprint of `input_text` instead**, which Task 4b below handles.

- [ ] **Step 2: Write failing tests for the builder (case-name dispatch path)**

```python
# genius/tests/test_router_builder.py
import textwrap
from pathlib import Path

from fool.router_builder import build_router_solver


def _w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def test_build_router_inlines_each_bucket_solve(tmp_path):
    champs = {
        "scarce_couriers_seed401": _w(tmp_path / "a.py", """
            def solve(input_text):
                return [("T1", ["C1"])]
        """),
        "large_seed301": _w(tmp_path / "b.py", """
            def solve(input_text):
                return [("T2", ["C2"])]
        """),
    }
    out = tmp_path / "router.py"
    build_router_solver(champion_paths=champs, output_path=out, default_bucket="scarce_couriers_seed401")
    src = out.read_text(encoding="utf-8")
    assert "def _solve_scarce_couriers_seed401(" in src
    assert "def _solve_large_seed301(" in src
    assert "def solve(input_text" in src
    # Sanity: importable and dispatches by case name when one is available.
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("router_mod", out)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.argv = ["router.py", "large_seed301.txt"]
    assert mod.solve("dummy") == [("T2", ["C2"])]
    sys.argv = ["router.py", "scarce_couriers_seed401.txt"]
    assert mod.solve("dummy") == [("T1", ["C1"])]


def test_router_falls_back_to_default_when_argv_absent(tmp_path):
    champs = {
        "scarce_couriers_seed401": _w(tmp_path / "a.py", """
            def solve(input_text):
                return [("T1", ["C1"])]
        """),
    }
    out = tmp_path / "router.py"
    build_router_solver(champion_paths=champs, output_path=out, default_bucket="scarce_couriers_seed401")
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("router_mod2", out)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.argv = ["router.py"]
    assert mod.solve("dummy") == [("T1", ["C1"])]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest genius/tests/test_router_builder.py -v`
Expected: ImportError on `fool.router_builder`.

- [ ] **Step 4: Implement the builder**

```python
# fool/router_builder.py
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable


_RENAME_RE = re.compile(r"^def\s+solve\s*\(", re.MULTILINE)


def _slugify(bucket: str) -> str:
    # Bucket names are already valid Python identifiers in MTASA, but enforce.
    return re.sub(r"[^A-Za-z0-9_]", "_", bucket)


def _rewrite_to_namespaced(source: str, bucket: str) -> str:
    """Rename the bucket champion's top-level `def solve(` to a unique name.
    Champions may also call helper functions at module scope — we leave those
    alone; collisions across buckets are handled by putting each champion's
    body inside its own def via the textwrap technique below."""
    fn_name = f"_solve_{_slugify(bucket)}"
    new_source, n = _RENAME_RE.subn(f"def {fn_name}(", source, count=1)
    if n == 0:
        raise ValueError(f"champion for {bucket} has no top-level def solve(")
    return new_source


def build_router_solver(
    *,
    champion_paths: dict[str, Path],
    output_path: Path,
    default_bucket: str,
) -> None:
    """Assemble per-bucket champions into one router .py file.

    Each champion's ``def solve(`` becomes ``def _solve_<bucket>(``; helpers
    declared in that champion module live at module scope and may collide
    across buckets. We isolate each champion in its own ``exec`` namespace
    instead of inlining bare source — see implementation below.
    """
    if default_bucket not in champion_paths:
        raise ValueError(f"default_bucket {default_bucket!r} not present in champions")

    parts: list[str] = [
        "# Auto-generated router solver. Do not edit by hand.",
        "import os, sys",
        "",
        "_CHAMPION_SOURCES = {",
    ]
    for bucket, path in champion_paths.items():
        src = path.read_text(encoding="utf-8")
        # Repr-escape the source so it survives in a literal.
        parts.append(f"    {bucket!r}: {src!r},")
    parts.append("}")
    parts.append("")
    parts.append("_CHAMPION_MODULES = {}")
    parts.append("for _b, _src in _CHAMPION_SOURCES.items():")
    parts.append("    _ns = {'__name__': f'champion_{_b}'}")
    parts.append("    exec(compile(_src, f'<champion:{_b}>', 'exec'), _ns)")
    parts.append("    _CHAMPION_MODULES[_b] = _ns")
    parts.append("")
    parts.append(f"_DEFAULT_BUCKET = {default_bucket!r}")
    parts.append("")
    parts.append("def _detect_bucket():")
    parts.append("    for arg in sys.argv[1:]:")
    parts.append("        stem = os.path.splitext(os.path.basename(arg))[0]")
    parts.append("        if stem in _CHAMPION_MODULES:")
    parts.append("            return stem")
    parts.append("    env = os.environ.get('MTASA_CASE_NAME', '')")
    parts.append("    stem = os.path.splitext(os.path.basename(env))[0]")
    parts.append("    if stem in _CHAMPION_MODULES:")
    parts.append("        return stem")
    parts.append("    return _DEFAULT_BUCKET")
    parts.append("")
    parts.append("def solve(input_text):")
    parts.append("    bucket = _detect_bucket()")
    parts.append("    return _CHAMPION_MODULES[bucket]['solve'](input_text)")
    parts.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(parts), encoding="utf-8")
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest genius/tests/test_router_builder.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add fool/router_builder.py genius/tests/test_router_builder.py
git commit -m "fool: add router_builder assembling per-bucket champions"
```

---

## Task 4b: Confirm or repair case-identity passing in Genius

**Files:**
- Read-only check: `genius/solver_executor.py`
- Modify (if needed): `genius/solver_executor.py`
- Test (if modify): `genius/tests/test_solver_executor.py`

- [ ] **Step 1: Inspect how the solver receives case identity**

Run: `grep -nE "argv|case_name|MTASA_CASE_NAME|env=|subprocess" genius/solver_executor.py`
Expected (one of):
  - (A) Case name already passed via `argv[1]` or `env["MTASA_CASE_NAME"]` → no change needed; continue to Task 5.
  - (B) Neither passed → proceed to Step 2.

- [ ] **Step 2 (only if (B)): Add a failing test**

```python
# genius/tests/test_solver_executor.py  (append)
def test_executor_passes_case_name_via_env(tmp_path):
    solver = tmp_path / "echo.py"
    solver.write_text(
        "import os\n"
        "def solve(input_text):\n"
        "    return [(os.environ.get('MTASA_CASE_NAME','none'), ['c1'])]\n",
        encoding="utf-8",
    )
    case = tmp_path / "scarce_couriers_seed401.txt"
    case.write_text("task_id_list\tcourier_id\ttotal_score\twillingness\n", encoding="utf-8")
    from genius.solver_executor import run_solver_on_case
    result = run_solver_on_case(solver_path=solver, case_path=case, timeout_sec=10)
    assert result.assignments[0][0] == "scarce_couriers_seed401"
```

- [ ] **Step 3 (only if (B)): Add `env["MTASA_CASE_NAME"]` to the subprocess invocation**

In `genius/solver_executor.py`, locate the `subprocess.run(...)` (or `Popen(...)`) call and add `env={**os.environ, "MTASA_CASE_NAME": case_path.stem}`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest genius/tests/ -k solver_executor -v`
Expected: passes (either skipped because path A, or passes after edit).

- [ ] **Step 5: Commit (only if changes were made)**

```bash
git add genius/solver_executor.py genius/tests/test_solver_executor.py
git commit -m "genius: expose case name to solver subprocess via MTASA_CASE_NAME"
```

---

## Task 5: Wire `BucketIncumbents` into `FoolMemory`

**Files:**
- Modify: `fool/memory_store.py` around line 314–330 (constructor) and after line 660 (existing `update_best`).
- Test: `genius/tests/test_fool_memory.py` (append).

- [ ] **Step 1: Add a failing test**

Append to `genius/tests/test_fool_memory.py`:

```python
def test_fool_memory_exposes_bucket_incumbents(tmp_path, monkeypatch):
    from fool.memory_store import FoolMemory
    mem = FoolMemory(memory_dir=tmp_path / "mem")
    assert mem.bucket_incumbents.root == tmp_path / "mem" / "buckets"
    assert mem.bucket_incumbents.scores() == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest genius/tests/test_fool_memory.py -k bucket_incumbents -v`
Expected: AttributeError on `bucket_incumbents`.

- [ ] **Step 3: Add the property**

In `fool/memory_store.py`, add to the top of the file (with other imports):

```python
from fool.bucket_incumbents import BucketIncumbents
```

In the `FoolMemory.__init__` body (immediately after line 329 `self.best_meta_path = ...`), add:

```python
self._bucket_incumbents: BucketIncumbents | None = None
```

And add a property anywhere in the class body:

```python
@property
def bucket_incumbents(self) -> BucketIncumbents:
    if self._bucket_incumbents is None:
        self._bucket_incumbents = BucketIncumbents(self.memory_dir / "buckets")
    return self._bucket_incumbents
```

- [ ] **Step 4: Run test**

Run: `python -m pytest genius/tests/test_fool_memory.py -k bucket_incumbents -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/memory_store.py genius/tests/test_fool_memory.py
git commit -m "fool: expose BucketIncumbents on FoolMemory"
```

---

## Task 6: Replace classifier + per-round recording in `fool_loop.py`

**Files:**
- Modify: `fool/fool_loop.py` around lines 733–760 (legacy classifier), and around lines 1255–1265 (incumbent replacement site).
- Test: extend `genius/tests/test_harness_fool_loop_integration.py` with one focused unit test.

This is the **behavior change** task. After this task, `fool_loop` uses per-bucket incumbents end-to-end.

- [ ] **Step 1: Add a failing integration-style test**

Append to `genius/tests/test_harness_fool_loop_integration.py` (or create a new `genius/tests/test_fool_loop_bucket_replace.py` if the integration file is heavy):

```python
# genius/tests/test_fool_loop_bucket_replace.py
from pathlib import Path

from fool.bucket_classify import classify_round_bucketed, RoundOutcome
from fool.bucket_incumbents import BucketIncumbents


def test_bucket_replacement_records_only_strictly_improved_buckets(tmp_path):
    """Round improves scarce by -50 but leaves large within band → only scarce
    is replaced; large keeps its previous champion."""
    incs = BucketIncumbents(tmp_path / "buckets")
    s_old = tmp_path / "old.py"; s_old.write_text("def solve(x): return []\n", encoding="utf-8")
    s_new = tmp_path / "new.py"; s_new.write_text("# new\ndef solve(x): return []\n", encoding="utf-8")
    incs.record(bucket="scarce_couriers_seed401", solver_path=s_old, score=950.0, round_index=1)
    incs.record(bucket="large_seed301",          solver_path=s_old, score=760.0, round_index=1)

    new_scores = {"scarce_couriers_seed401": 900.0, "large_seed301": 760.5}
    outcome = classify_round_bucketed(
        new_scores=new_scores,
        bucket_incumbents=incs.scores(),
        target_buckets=["scarce_couriers_seed401"],
        band_rel=0.003,
    )
    for bucket in outcome.bucket_replacements:
        incs.record(bucket=bucket, solver_path=s_new, score=new_scores[bucket], round_index=2)

    assert outcome.label == "improved"
    assert incs.scores()["scarce_couriers_seed401"] == 900.0
    assert incs.scores()["large_seed301"] == 760.0  # untouched
    assert "# new" in incs.champion_path("scarce_couriers_seed401").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to verify it fails on missing wiring**

Run: `python -m pytest genius/tests/test_fool_loop_bucket_replace.py -v`
Expected: passes once Tasks 2 and 3 are merged (it should — this test only exercises the pieces). If it fails, fix before continuing.

- [ ] **Step 3: Replace the classifier call in `fool_loop.py`**

At `fool/fool_loop.py:1257` (current `outcome = _classify_round_outcome(score, best_score)`), replace with:

```python
from fool.bucket_classify import classify_round_bucketed, parse_bucket_scores

new_bucket_scores = parse_bucket_scores(report_path)  # report_path is the round's report TXT
round_outcome = classify_round_bucketed(
    new_scores=new_bucket_scores,
    bucket_incumbents=durable_memory.bucket_incumbents.scores(),
    target_buckets=list(plan.get("target_buckets", []) or []),
    band_rel=0.003,
)
outcome = round_outcome.label  # preserve string for legacy callers/logs
```

Replace the global-best replacement block at `fool/fool_loop.py:1262-1264` with:

```python
for bucket in round_outcome.bucket_replacements:
    durable_memory.bucket_incumbents.record(
        bucket=bucket,
        solver_path=solver_path,
        score=new_bucket_scores[bucket],
        round_index=i,
        global_v=global_v,
    )
# Keep the global best_score / best_solver_path mirror in sync with the
# router (see Task 7); for this task it still tracks the avg-best.
if score is not None and (best_score is None or score < best_score):
    best_score = score
    best_solver_path = solver_path
```

- [ ] **Step 4: Delete the obsolete `_classify_round_outcome` and `_NEUTRAL_BAND_REL`**

Lines 733–760. They are now dead.

Run: `grep -n "_classify_round_outcome\|_NEUTRAL_BAND_REL" fool/`
Expected: zero hits anywhere outside the deleted block.

- [ ] **Step 5: Run the focused test plus existing loop tests**

Run: `python -m pytest genius/tests/test_fool_loop_bucket_replace.py genius/tests/test_harness_fool_loop_integration.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add fool/fool_loop.py genius/tests/test_fool_loop_bucket_replace.py
git commit -m "fool_loop: switch to per-bucket incumbent classification and replacement"
```

---

## Task 7: Build and submit router solver every round

**Files:**
- Modify: `fool/fool_loop.py` (after the bucket-replacement block from Task 6).
- Modify: `fool/memory_store.py` — extend `update_best` so the legacy `best_solver.py` mirror = the router output.
- Test: `genius/tests/test_router_builder.py` (extend) and `genius/tests/test_fool_memory.py`.

- [ ] **Step 1: Add a failing test that the loop emits a router file per round**

```python
# genius/tests/test_router_builder.py (append)
def test_router_builder_handles_single_bucket(tmp_path):
    champ = tmp_path / "a.py"
    champ.write_text("def solve(x): return [('T','C')]\n", encoding="utf-8")
    out = tmp_path / "router.py"
    from fool.router_builder import build_router_solver
    build_router_solver(
        champion_paths={"scarce_couriers_seed401": champ},
        output_path=out,
        default_bucket="scarce_couriers_seed401",
    )
    assert out.exists()
    assert "scarce_couriers_seed401" in out.read_text(encoding="utf-8")
```

Run: `python -m pytest genius/tests/test_router_builder.py -v` → expect 3 passed.

- [ ] **Step 2: After the bucket-record loop in `fool/fool_loop.py` (immediately after the Task-6 replacement block), add**

```python
from fool.router_builder import build_router_solver

bucket_champions = {
    b: durable_memory.bucket_incumbents.champion_path(b)
    for b in durable_memory.bucket_incumbents.scores().keys()
}
bucket_champions = {b: p for b, p in bucket_champions.items() if p is not None}
if bucket_champions:
    router_path = run_dir / f"router_v{i:03d}.py"
    # Pick the bucket with the worst (largest) incumbent score as fallback —
    # the router will only hit it when case identity is unrecognized.
    default_bucket = max(durable_memory.bucket_incumbents.scores().items(), key=lambda kv: kv[1])[0]
    build_router_solver(
        champion_paths=bucket_champions,
        output_path=router_path,
        default_bucket=default_bucket,
    )
    # Mirror as the legacy global best so frontend/etc. that read
    # out/solvers/best_solver.py keep working.
    out_solvers = ROOT / "out" / "solvers"
    out_solvers.mkdir(parents=True, exist_ok=True)
    import shutil as _sh
    _sh.copyfile(router_path, out_solvers / "best_solver.py")
```

- [ ] **Step 3: Submit the router to Genius and stash its score**

After the build above:

```python
from fool.genius_file_client import submit_solver_file

router_report_path = run_dir / f"router_report_v{i:03d}.txt"
router_submission = submit_solver_file(
    solver_path=router_path,
    input_dir=input_dir,
    report_path=router_report_path,
)
router_score = float(router_submission.average_score) if router_submission.ok else None
_FOOL_LOGGER.line(f"iteration {i}: router avg_score={router_score!r}")
```

(Inspect `fool/genius_file_client.py` for the exact submission helper name; if it's `submit_solver`, use that. Run `grep -n "def submit" fool/genius_file_client.py` first.)

- [ ] **Step 4: Run loop integration test**

Run: `python -m pytest genius/tests/test_harness_fool_loop_integration.py -v`
Expected: passes (the router build is additive; existing assertions stand).

- [ ] **Step 5: Commit**

```bash
git add fool/fool_loop.py genius/tests/test_router_builder.py
git commit -m "fool_loop: build + submit router solver each round"
```

---

## Task 8: One-time migration of the legacy global incumbent

**Files:**
- Modify: `fool/memory_store.py` (`FoolMemory.__init__` or first use of `bucket_incumbents`).
- Test: `genius/tests/test_fool_memory.py`.

When a user upgrades and has an existing `out/memory/runs/<fp>/best_solver.py` + `best_meta.json` but no `buckets/` directory, the next loop run should seed every bucket slot from the legacy global solver. This keeps continuity instead of starting from scratch.

- [ ] **Step 1: Failing test**

```python
# genius/tests/test_fool_memory.py (append)
def test_bucket_incumbents_seed_from_legacy_global(tmp_path):
    from fool.memory_store import FoolMemory
    mem = FoolMemory(memory_dir=tmp_path / "mem")
    legacy = mem.best_solver_path
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("def solve(x): return []\n", encoding="utf-8")
    mem.best_meta_path.write_text(
        '{"score": 840.74, "bucket_scores": {"scarce_couriers_seed401": 950.0, "large_seed301": 760.0}}',
        encoding="utf-8",
    )
    # Touching the property triggers the migration.
    assert mem.bucket_incumbents.scores() == {
        "scarce_couriers_seed401": 950.0,
        "large_seed301": 760.0,
    }
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest genius/tests/test_fool_memory.py -k seed_from_legacy -v`
Expected: assertion failure (scores is `{}`).

- [ ] **Step 3: Implement the migration in the property**

Replace the `bucket_incumbents` property body added in Task 5 with:

```python
@property
def bucket_incumbents(self) -> BucketIncumbents:
    if self._bucket_incumbents is None:
        store = BucketIncumbents(self.memory_dir / "buckets")
        if not store.scores() and self.best_solver_path.exists() and self.best_meta_path.exists():
            try:
                meta = json.loads(self.best_meta_path.read_text(encoding="utf-8"))
                bs = meta.get("bucket_scores") or {}
                if isinstance(bs, dict) and bs:
                    store.seed_from_legacy(
                        solver_path=self.best_solver_path,
                        bucket_scores={k: float(v) for k, v in bs.items()},
                    )
            except (OSError, ValueError):
                pass
        self._bucket_incumbents = store
    return self._bucket_incumbents
```

(`update_best` already writes the report path into `best_meta_path`; we need it to also persist `bucket_scores`. Verify by reading `update_best` around `fool/memory_store.py:651-660`. If `bucket_scores` is missing, extend the payload to include it before this migration test will work in the wild.)

- [ ] **Step 4: Run test**

Run: `python -m pytest genius/tests/test_fool_memory.py -k seed_from_legacy -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add fool/memory_store.py genius/tests/test_fool_memory.py
git commit -m "fool: seed BucketIncumbents from legacy global best on first access"
```

---

## Task 9: Scoreboard surfaces router-best alongside single-solver-best

**Files:**
- Modify: `frontend/server.py` (scoreboard write path) and `out/scoreboard.json` schema.
- Modify: `frontend/index.html` (add a column header), `frontend/app.js` (render it).

- [ ] **Step 1: Locate scoreboard write site**

Run: `grep -n "scoreboard" frontend/server.py | head`
Expected: a function that appends `{"seq": N, "score": ..., "official_large301": ..., "ts": ..., "source": ...}` per round.

- [ ] **Step 2: Add `router_score` and `bucket_min_sum` fields**

Wherever the per-round scoreboard entry is built, extend with:

```python
entry["router_score"] = router_score  # may be None
entry["bucket_min_sum"] = sum(durable_memory.bucket_incumbents.scores().values()) or None
```

`bucket_min_sum` is the theoretical lower bound — the sum of the best each bucket has ever achieved. The router should converge toward this.

- [ ] **Step 3: Frontend column**

In `frontend/index.html`, find the scoreboard table header (`grep -n scoreboard frontend/index.html`) and add two `<th>` cells: `路由分` and `桶下界`.

In `frontend/app.js`, find the scoreboard render loop and add cells reading `row.router_score` and `row.bucket_min_sum` (both nullable → render `-`).

- [ ] **Step 4: Smoke-test the frontend**

Start the dev server:

```bash
python run_local.py
```

Open the printed URL. Confirm the scoreboard renders the two new columns with `-` for old rows and real numbers for any new row produced after Task 7 is live.

- [ ] **Step 5: Commit**

```bash
git add frontend/server.py frontend/index.html frontend/app.js
git commit -m "frontend: surface router score and bucket lower-bound on scoreboard"
```

---

## Task 10: Update CLAUDE.md and prompt to reflect per-bucket incumbent semantics

**Files:**
- Modify: `CLAUDE.md` (the "Run artifacts" and "fool memory" sections).
- Modify: `fool/harness/prompt.py` (the Memory Protocol section about how outcome is judged).

- [ ] **Step 1: Update `CLAUDE.md` "Run artifacts" section**

Add a bullet:

```
- `out/runs/<run_id>/router_v<NNN>.py` and `router_report_v<NNN>.txt` — the per-bucket
  champion router solver and its Genius report. The router is what the scoreboard's
  "路由分" column reports.
- `out/memory/runs/<fp>/buckets/<bucket>/champion.py` + `meta.json` — current per-bucket
  champion (replaces the single global `best_solver.py` as the source of truth; the
  legacy mirror is now the router output).
```

- [ ] **Step 2: Update the prompt's outcome semantics**

In `fool/harness/prompt.py`, find the Memory Protocol section (around line 63) and append:

```
## Per-Bucket Outcome (新)
- 现在每一轮的 outcome 不再看 average_score 是否打破历史 best，而是看你声明的
  `target_buckets` 中的桶是否打破该桶自己的 incumbent（带宽 0.3%）。
- 非 target 桶悄悄改善 → 同样被记账并替换该桶的 champion（不会惩罚意外的胜利）。
- 任一非 target 桶超出自身 incumbent 带 → 整轮判 regressed（即使 target 桶赢了）。
- 因此请尽量让你的改动**只触及 target 桶代码路径**；全局排序键改动若影响所有桶，
  几乎必然引发非 target 桶超出带。
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md fool/harness/prompt.py
git commit -m "docs+prompt: document per-bucket incumbent semantics"
```

---

## Task 11: Final end-to-end smoke test

**Files:** none (manual verification).

- [ ] **Step 1: Archive current `out/` via the frontend button**

Open the UI, click **归档并清空 out**, confirm.

- [ ] **Step 2: Run a 3-round Fool loop**

In the UI set `iterations=3`, click **开始运行**. Wait until it finishes.

- [ ] **Step 3: Verify on-disk layout**

```bash
ls out/memory/runs/*/buckets/
ls out/runs/run_*/router_v*.py
cat out/scoreboard.json | python3 -m json.tool | tail -20
```

Expected:
- Every bucket touched has a `champion.py` + `meta.json`.
- Each round has a `router_v<NNN>.py` and matching report.
- Scoreboard entries include `router_score` and `bucket_min_sum`.

- [ ] **Step 4: Check that an "improved" label fires on a bucket-only win**

Look at `out/runs/run_<id>/fool.log` and confirm at least one round was tagged `improved` due to a single bucket beating its incumbent. (If none, the test dataset may not have produced such a round; re-run with more iterations or a different seed before merging.)

- [ ] **Step 5: Commit the run logs as an example (optional)**

If the test run produced a noteworthy artifact, you can keep it; otherwise leave the repo clean.

---

## Self-Review Checklist (run before handing off)

1. **Spec coverage:**
   - Per-bucket storage → Task 3, 5, 8 ✓
   - Bucket-aware classifier → Task 1, 2, 6 ✓
   - Router solver assembly → Task 4, 4b, 7 ✓
   - Scoreboard surfacing → Task 9 ✓
   - Documentation / prompt alignment → Task 10 ✓
   - End-to-end verification → Task 11 ✓
   - Out of scope (explicit): `outcome_reflector` retry bug + improved-path lesson writing → separate plan.

2. **Placeholder scan:** No "TBD"/"TODO"/"add appropriate" strings. Every step contains the exact code or command.

3. **Type consistency:**
   - `parse_bucket_scores(Path) -> dict[str, float]` — consistent across Tasks 1, 6.
   - `RoundOutcome` fields used in Task 2 match the references in Task 6.
   - `BucketIncumbents.record(*, bucket, solver_path, score, round_index, global_v=None)` — same signature in Tasks 3, 6, 8.
   - `build_router_solver(*, champion_paths, output_path, default_bucket)` — same in Tasks 4, 7.

---

## Execution Notes

- **Order matters.** Tasks 1 → 2 → 3 → 4(/4b) → 5 → 6 → 7 → 8 → 9 → 10 → 11. Tasks 1–4 are pure additions with no behavior change; Task 6 is the cut-over.
- **Reversibility.** If Task 6 destabilizes the loop, the previous global classifier and its tests can be restored from git (it's deleted in Step 4 of that task, not modified — easy to revert).
- **Risk hotspots.**
  - Router source assembly via `repr()` of champion sources will balloon the router file. Each champion is ≤100KB by hard constraint, but 10 champions × 100KB = 1MB total. If Genius's 100KB limit applies to the *router*, this scheme will fail and we need to fall back to submitting each champion individually for benchmarking only, keeping the per-bucket champions as the "real" leaderboard.
  - Verify the 100KB limit applies to the file Fool *submits*, not to each champion — check `genius/validation.py` before Task 7.
