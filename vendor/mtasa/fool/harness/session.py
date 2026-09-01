from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now().isoformat()


class HarnessSession:
    """JSON-backed transcript for one Fool harness round."""

    def __init__(self, run_dir: Path, iteration: int) -> None:
        self.run_dir = Path(run_dir)
        self.iteration = iteration
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / f"harness_v{iteration:03d}.json"
        self._data: dict[str, Any] = {
            "iteration": iteration,
            "created_at": _now(),
            "transcript": [],
            "final": None,
        }
        self._flush()

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def record_user(self, content: str) -> None:
        self._data["transcript"].append(
            {"role": "user", "content": content, "ts": _now()}
        )
        self._flush()

    def record_assistant(self, content: str) -> None:
        self._data["transcript"].append(
            {"role": "assistant", "content": content, "ts": _now()}
        )
        self._flush()

    def record_tool(
        self,
        *,
        name: str,
        args: dict[str, Any],
        ok: bool,
        content: str,
        canonical_call: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "role": "tool",
            "name": name,
            "args": args,
            "ok": ok,
            "content": content,
            "ts": _now(),
        }
        if canonical_call is not None:
            entry["canonical_call"] = canonical_call
        self._data["transcript"].append(entry)
        self._flush()

    def record_final(self, *, solver_code: str, plan: dict[str, Any]) -> None:
        self._data["final"] = {
            "solver_code": solver_code,
            "plan": plan,
            "ts": _now(),
        }
        self._flush()

    def transcript(self) -> list[dict[str, Any]]:
        return list(self._data["transcript"])
