from __future__ import annotations

import json
from pathlib import Path

from fool.harness.session import HarnessSession


def test_session_writes_initial_skeleton(tmp_path: Path) -> None:
    session = HarnessSession(run_dir=tmp_path, iteration=2)
    assert session.path == tmp_path / "harness_v002.json"
    assert session.path.exists()
    data = json.loads(session.path.read_text())
    assert data["iteration"] == 2
    assert data["transcript"] == []
    assert data["final"] is None


def test_record_user_message_persists(tmp_path: Path) -> None:
    session = HarnessSession(run_dir=tmp_path, iteration=1)
    session.record_user("hello round")
    data = json.loads(session.path.read_text())
    assert data["transcript"][0]["role"] == "user"
    assert data["transcript"][0]["content"] == "hello round"


def test_record_assistant_and_tool_persist(tmp_path: Path) -> None:
    session = HarnessSession(run_dir=tmp_path, iteration=1)
    session.record_assistant("raw model output")
    session.record_tool(
        name="profile_dataset",
        args={"top_k": 3},
        ok=True,
        content="profile body",
    )
    data = json.loads(session.path.read_text())
    roles = [entry["role"] for entry in data["transcript"]]
    assert roles == ["assistant", "tool"]
    tool_entry = data["transcript"][1]
    assert tool_entry["name"] == "profile_dataset"
    assert tool_entry["args"] == {"top_k": 3}
    assert tool_entry["ok"] is True


def test_record_final_persists(tmp_path: Path) -> None:
    session = HarnessSession(run_dir=tmp_path, iteration=1)
    session.record_final(solver_code="def solve(t): return []", plan={"hypothesis": "x"})
    data = json.loads(session.path.read_text())
    assert data["final"]["solver_code"] == "def solve(t): return []"
    assert data["final"]["plan"] == {"hypothesis": "x"}
