from __future__ import annotations

import threading
import time
from pathlib import Path

from fool.genius_file_client import read_report, submit_solver
from frontend.server import (
    APPROVAL_LOCK,
    PENDING_APPROVAL,
    STATE,
    STATE_LOCK,
    STOP_EVENT,
    _approval_provider,
    _discover_zshrc_profiles,
    _parse_zshrc_assignments,
    _selected_api_profile,
    _submit_to_genius,
)
from genius.genius_judge import run_judge
from genius.scoring_functions import FIXED_SCORING_MODE


def _write_fixture(tmp_path: Path, count: int = 2) -> tuple[Path, Path]:
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    for idx in range(1, count + 1):
        (case_dir / f"case{idx:02d}.txt").write_text(
            "task_id_list\tcourier_id\ttotal_score\twillingness\n"
            "T0001\tC001\t10\t1.0\n",
            encoding="utf-8",
        )
    solver = tmp_path / "solver.py"
    solver.write_text(
        "def solve(input_text):\n"
        "    return [('T0001', 'C001')]\n",
        encoding="utf-8",
    )
    return case_dir, solver


def test_frontend_and_fool_consume_the_same_genius_txt_contract(tmp_path: Path) -> None:
    case_dir, solver = _write_fixture(tmp_path)
    frontend_report = tmp_path / "frontend_report.txt"
    fool_report = tmp_path / "fool_report.txt"

    _submit_to_genius(
        solver_path=solver,
        input_dir=case_dir,
        report_path=frontend_report,
        log_path=tmp_path / "frontend.log",
    )
    fool_result = submit_solver(
        solver_path=solver,
        input_dir=case_dir,
        report_path=fool_report,
        log_path=tmp_path / "fool.log",
    )
    frontend_result = read_report(frontend_report)

    assert frontend_result["average_score"] == fool_result["average_score"]
    assert [case["score"] for case in frontend_result["cases"]] == [
        case["score"] for case in fool_result["cases"]
    ]
    assert frontend_report.exists()
    assert fool_report.exists()


def test_progress_reports_case_names_from_ten_to_one_hundred_percent(tmp_path: Path) -> None:
    case_dir, solver = _write_fixture(tmp_path, count=10)
    events: list[dict] = []

    run_judge(
        str(solver),
        str(case_dir),
        FIXED_SCORING_MODE,
        progress_callback=events.append,
    )

    active = [event for event in events if event["stage"] == "scoring"]
    assert [event["case_name"] for event in active] == [
        f"case{idx:02d}" for idx in range(1, 11)
    ]
    assert [event["progress_pct"] for event in active] == list(range(10, 101, 10))

    _submit_to_genius(
        solver_path=solver,
        input_dir=case_dir,
        report_path=tmp_path / "status_report.txt",
        log_path=tmp_path / "status.log",
    )
    with STATE_LOCK:
        assert STATE["scoring_status"] == "评分完成: case10 (100%)"


def test_zshrc_profile_discovery_exposes_provider_not_secret(tmp_path: Path) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        "export OPENROUTER_API_KEY='secret-token'\n"
        'OPENROUTER_BASE_URL="https://example.invalid/api/v1"\n',
        encoding="utf-8",
    )

    assignments = _parse_zshrc_assignments(zshrc)
    profiles = _discover_zshrc_profiles(zshrc)

    assert assignments["OPENROUTER_API_KEY"] == "secret-token"
    assert profiles == [
        {
            "id": "zshrc:openrouter",
            "api_type": "openrouter",
            "label": ".zshrc - openrouter",
            "key_name": "OPENROUTER_API_KEY",
            "base_url": "https://example.invalid/api/v1",
        }
    ]
    public_profile = {key: profiles[0][key] for key in ("id", "label", "api_type")}
    assert "secret-token" not in str(public_profile)
    assert _selected_api_profile("", False, profiles) == "zshrc:openrouter"
    assert _selected_api_profile("manual", False, profiles) == "zshrc:openrouter"
    assert _selected_api_profile("manual", True, profiles) == "manual"


def test_zshrc_profile_discovery_supports_aliyun(tmp_path: Path) -> None:
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text(
        "export DASHSCOPE_API_KEY='dash-token'\n",
        encoding="utf-8",
    )

    profiles = _discover_zshrc_profiles(zshrc)

    assert profiles == [
        {
            "id": "zshrc:aliyun",
            "api_type": "aliyun",
            "label": ".zshrc - aliyun",
            "key_name": "DASHSCOPE_API_KEY",
            "base_url": "",
        }
    ]


def test_frontend_effort_options_include_medium_and_xhigh_for_aliyun_openrouter_and_deepseek() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    app_js = (repo_root / "frontend" / "app.js").read_text(encoding="utf-8")

    assert '"openrouter": ["low", "medium", "high", "xhigh"]' in app_js
    assert '"aliyun": ["low", "medium", "high", "xhigh"]' in app_js
    assert '"deepseek": ["low", "medium", "high", "xhigh"]' in app_js


def test_frontend_index_no_longer_hardcodes_effort_options() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    index_html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")

    assert '<option value="high">high</option>' not in index_html


def test_readme_lists_deepseek_with_full_effort_tiers() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert "`low` / `medium` / `high` / `xhigh`" in readme


def test_manual_iteration_approval_waits_then_releases() -> None:
    with STATE_LOCK:
        previous = STATE["config"].get("auto_accept", True)
        STATE["config"]["auto_accept"] = False
    STOP_EVENT.clear()
    result: list[bool] = []
    thread = threading.Thread(target=lambda: result.append(_approval_provider(3, {})))
    thread.start()
    try:
        for _ in range(100):
            with STATE_LOCK:
                waiting = bool(STATE.get("awaiting_approval", False))
            if waiting:
                break
            time.sleep(0.01)
        assert waiting is True
        with APPROVAL_LOCK:
            PENDING_APPROVAL["accepted"] = True
            PENDING_APPROVAL["event"].set()
        thread.join(timeout=1)
        assert result == [True]
    finally:
        STOP_EVENT.clear()
        with STATE_LOCK:
            STATE["config"]["auto_accept"] = previous
            STATE["awaiting_approval"] = False
            STATE["approval_iteration"] = 0
