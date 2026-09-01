"""Cross-run global version index.

A single JSON file (`out/version_index.json`) that gives every Fool round a
globally unique, monotonically increasing version number (`v`). The number
survives across runs and is only reset by the "清空全局记忆" frontend action.

Why a separate file (not per-run): the agent often wants to compare "best
solver three runs ago" against the current attempt; addressing those by
`(run_id, iteration)` is unwieldy, so we give each round a flat global v.

Concurrency: writes are short and protected by an OS-level fcntl lock so
parallel runs (rare but possible) don't clobber each other.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass
class VersionEntry:
    v: int
    run_id: str
    iteration: int
    dataset_fp: str
    ts: str
    solver_path: str = ""
    report_path: str = ""
    harness_path: str = ""
    reflect_path: str = ""
    score: float | None = None
    uncovered: int | None = None
    solved_cases: int | None = None
    total_cases: int | None = None
    outcome: str = ""
    plan_headline: str = ""
    # Per-case (bucket) breakdowns extracted from the Genius JSON report.
    # Keys are case_name (e.g. "scarce_seed401"); values are the case's
    # score / uncovered_tasks. Empty when the v didn't produce a report.
    bucket_scores: dict[str, float] = None  # type: ignore[assignment]
    bucket_uncovered: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.bucket_scores is None:
            object.__setattr__(self, "bucket_scores", {})
        if self.bucket_uncovered is None:
            object.__setattr__(self, "bucket_uncovered", {})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VersionIndex:
    """JSON-backed monotonic version registry shared across runs."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    # ----- IO -----------------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[Path]:
        """Best-effort exclusive lock around read-modify-write."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(".lock")
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            yield self.path
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            os.close(fd)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"next": 1, "entries": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"next": 1, "entries": []}
        if not isinstance(data, dict):
            return {"next": 1, "entries": []}
        data.setdefault("next", 1)
        data.setdefault("entries", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ----- mutate -------------------------------------------------------

    def allocate(
        self,
        *,
        run_id: str,
        iteration: int,
        dataset_fp: str,
        ts: str,
    ) -> int:
        """Reserve and return the next global v for a new round."""
        with self._locked():
            data = self._load()
            v = int(data["next"])
            entry = VersionEntry(
                v=v,
                run_id=run_id,
                iteration=iteration,
                dataset_fp=dataset_fp,
                ts=ts,
            )
            data["entries"].append(entry.to_dict())
            data["next"] = v + 1
            self._save(data)
            return v

    def update(self, v: int, patch: dict[str, Any]) -> None:
        """Merge fields into the entry with the matching v."""
        with self._locked():
            data = self._load()
            for e in data["entries"]:
                if int(e.get("v", -1)) == int(v):
                    e.update({k: val for k, val in patch.items() if val is not None})
                    self._save(data)
                    return

    def record_paths(
        self,
        v: int,
        *,
        solver_path: Path | str = "",
        report_path: Path | str = "",
        harness_path: Path | str = "",
        reflect_path: Path | str = "",
    ) -> None:
        patch = {
            "solver_path": str(solver_path) if solver_path else "",
            "report_path": str(report_path) if report_path else "",
            "harness_path": str(harness_path) if harness_path else "",
            "reflect_path": str(reflect_path) if reflect_path else "",
        }
        self.update(v, {k: val for k, val in patch.items() if val})

    def record_outcome(
        self,
        v: int,
        *,
        score: float | None = None,
        uncovered: int | None = None,
        solved_cases: int | None = None,
        total_cases: int | None = None,
        outcome: str = "",
        plan_headline: str = "",
        bucket_scores: dict[str, float] | None = None,
        bucket_uncovered: dict[str, int] | None = None,
    ) -> None:
        patch: dict[str, Any] = {}
        if score is not None:
            patch["score"] = float(score)
        if uncovered is not None:
            patch["uncovered"] = int(uncovered)
        if solved_cases is not None:
            patch["solved_cases"] = int(solved_cases)
        if total_cases is not None:
            patch["total_cases"] = int(total_cases)
        if outcome:
            patch["outcome"] = outcome
        if plan_headline:
            patch["plan_headline"] = plan_headline[:200]
        if bucket_scores:
            patch["bucket_scores"] = {str(k): float(v) for k, v in bucket_scores.items()}
        if bucket_uncovered:
            patch["bucket_uncovered"] = {
                str(k): int(v) for k, v in bucket_uncovered.items()
            }
        if patch:
            self.update(v, patch)

    def purge(self) -> None:
        """Drop the entire index. Called by the 清空全局记忆 button."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return

    # ----- query --------------------------------------------------------

    def all_entries(self) -> list[dict[str, Any]]:
        return list(self._load()["entries"])

    def get(self, v: int) -> dict[str, Any] | None:
        for e in self._load()["entries"]:
            if int(e.get("v", -1)) == int(v):
                return e
        return None

    def latest_for_run(self, run_id: str) -> dict[str, Any] | None:
        entries = [e for e in self._load()["entries"] if e.get("run_id") == run_id]
        return entries[-1] if entries else None

    def best(self, k: int = 10) -> list[dict[str, Any]]:
        scored = [e for e in self._load()["entries"] if e.get("score") is not None]
        scored.sort(key=lambda e: e.get("score", 0.0))
        return scored[:k]

    def for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [e for e in self._load()["entries"] if e.get("run_id") == run_id]

    def resolve(
        self, spec: Any, *, current_run_id: str = ""
    ) -> dict[str, Any] | None:
        """Resolve a flexible v-spec.

        Accepts:
          - int (positive global v, or negative = relative-to-current-run-tail)
          - 'latest' / 'last'                — last entry in current run, fallback global
          - 'best'                           — lowest-score entry (assumes lower=better)
          - 'v33' / '33' / '003'             — parsed as int
        """
        if isinstance(spec, int):
            if spec > 0:
                return self.get(spec)
            if spec < 0 and current_run_id:
                run_entries = self.for_run(current_run_id)
                if -spec <= len(run_entries):
                    return run_entries[spec]
            return None
        if isinstance(spec, str):
            s = spec.strip().lower()
            if s in ("latest", "last", "current"):
                if current_run_id:
                    cur = self.latest_for_run(current_run_id)
                    if cur:
                        return cur
                entries = self._load()["entries"]
                return entries[-1] if entries else None
            if s == "best":
                top = self.best(1)
                return top[0] if top else None
            # 'v33', '33', 'v003'
            digits = s.lstrip("v")
            if digits.isdigit():
                return self.get(int(digits))
        return None
