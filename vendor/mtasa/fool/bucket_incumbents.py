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
        <root>/<bucket>/meta.json   {"score": float, "round": int, "global_v": int|null}
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def scores(self) -> dict[str, float]:
        if not self.root.exists():
            return {}
        out: dict[str, float] = {}
        for bucket_dir in sorted(self.root.iterdir()):
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
        """One-time migration: copy the legacy global ``best_solver.py`` into
        every bucket slot using the per-bucket scores observed when the legacy
        incumbent was crowned. Idempotent — caller decides whether to invoke."""
        for bucket, score in bucket_scores.items():
            self.record(bucket=bucket, solver_path=solver_path, score=float(score), round_index=0)
