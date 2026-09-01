from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class DialogWriter:
    """Stream messages to out/runs/<run_id>/dialog/round_NNN.jsonl as JSON lines."""

    def __init__(self, *, run_dir: Path, round_idx: int) -> None:
        self.run_dir = Path(run_dir)
        self.round_idx = int(round_idx)
        self.dialog_dir = self.run_dir / "dialog"
        self.dialog_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dialog_dir / f"round_{self.round_idx:03d}.jsonl"

    def append(self, msg: dict) -> None:
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            **msg,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
