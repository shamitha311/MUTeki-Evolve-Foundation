import json
from pathlib import Path
from fool.harness.dialog_writer import DialogWriter


def test_writer_creates_round_file_and_appends(tmp_path: Path):
    w = DialogWriter(run_dir=tmp_path, round_idx=2)
    w.append({"role": "user", "content": "hi"})
    w.append({"role": "assistant", "content": "hello"})
    path = tmp_path / "dialog" / "round_002.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["role"] == "user"
    assert json.loads(lines[1])["content"] == "hello"


def test_writer_appends_timestamp(tmp_path: Path):
    w = DialogWriter(run_dir=tmp_path, round_idx=1)
    w.append({"role": "user", "content": "x"})
    rec = json.loads((tmp_path / "dialog" / "round_001.jsonl").read_text().strip())
    assert "ts" in rec
    assert "T" in rec["ts"]


def test_writer_path_helper(tmp_path: Path):
    w = DialogWriter(run_dir=tmp_path, round_idx=10)
    assert w.path == tmp_path / "dialog" / "round_010.jsonl"
