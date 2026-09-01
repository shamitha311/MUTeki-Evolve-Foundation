from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXED_SCORING_MODE = "official_like_latest"
ZSHRC_PATH = Path.home() / ".zshrc"
API_PROFILE_VARS = {
    "openai": {
        "keys": ("OPENAI_API_KEY",),
        "base_urls": ("OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_API_URL"),
    },
    "claude": {
        "keys": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
        "base_urls": ("ANTHROPIC_BASE_URL", "CLAUDE_BASE_URL", "ANTHROPIC_API_URL"),
    },
    "openrouter": {
        "keys": ("OPENROUTER_API_KEY",),
        "base_urls": ("OPENROUTER_BASE_URL", "OPENROUTER_API_URL"),
    },
    "deepseek": {
        "keys": ("DEEPSEEK_API_KEY",),
        "base_urls": ("DEEPSEEK_BASE_URL", "DEEPSEEK_API_URL"),
    },
    "aliyun": {
        "keys": ("DASHSCOPE_API_KEY",),
        "base_urls": ("DASHSCOPE_BASE_URL", "ALIYUN_BAILIAN_BASE_URL"),
    },
    "custom": {
        "keys": ("CUSTOM_API_KEY",),
        "base_urls": ("CUSTOM_BASE_URL", "CUSTOM_API_URL"),
    },
}

STATE_LOCK = threading.Lock()
RUN_THREAD: threading.Thread | None = None
STOP_EVENT = threading.Event()
PENDING_SCORE: dict[str, Any] = {
    "event": None,
    "solver_path": "",
    "input_dir": "",
    "scoring": "",
    "iteration": 0,
    "result": None,
}
PENDING_LOCK = threading.Lock()
APPROVAL_LOCK = threading.Lock()
PENDING_APPROVAL: dict[str, Any] = {
    "event": None,
    "iteration": 0,
    "accepted": False,
}

STATE: dict[str, Any] = {
    "running": False,
    "stage": "idle",
    "current_iteration": 0,
    "total_iterations": 0,
    "current_score": None,
    "best_score": None,
    "best_solver_path": "",
    "ai_connected": False,
    "ai_endpoint": "",
    "ai_message": "not_checked",
    "last_error": "",
    "completed_cases": "0/0",
    "latest_report_path": "",
    "latest_solver_path": "",
    "official_large301_score": None,
    "scoring_status": "",
    "thoughts": [],
    "llm_dialogue": [],
    "awaiting_approval": False,
    "approval_iteration": 0,
    "logs": [],
    "config": {
        "api_profile": "",
        "api_profile_explicit": False,
        "api_type": "openai",
        "api_key": "",
        "base_url": "",
        "model": "gpt-4.1-mini",
        "iterations": 5,
        "max_steps_per_round": 50,
        "bootstrap_solver_path": str(ROOT / "data" / "official" / "example_solution.txt"),
        "max_tokens": "8000",
        "effort_level": "low",
        "dataset_path": str(ROOT / "data" / "sample_10_cases"),
        "scoring_mode": FIXED_SCORING_MODE,
        "enable_multi_anchor": True,
        "verbose": True,
        "require_ai": True,
        "auto_score": True,
        "auto_accept": True,
        "max_case_seconds": 25.0,
    },
}


def ensure_runtime_dirs(root: Path) -> None:
    (root / "out" / "reports").mkdir(parents=True, exist_ok=True)
    (root / "out" / "solvers").mkdir(parents=True, exist_ok=True)
    (root / "out" / "runs").mkdir(parents=True, exist_ok=True)
    (root / "out" / "logs").mkdir(parents=True, exist_ok=True)


def _append_log(message: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {message}"
    with STATE_LOCK:
        STATE["logs"].append(line)
        STATE["logs"] = STATE["logs"][-500:]


def _parse_zshrc_assignments(path: Path = ZSHRC_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    assignment = re.compile(
        r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|([^\s#]+))"
    )
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = assignment.match(line)
        if not match:
            continue
        value = next((part for part in match.groups()[1:] if part is not None), "")
        values[match.group(1)] = value
    return values


def _discover_zshrc_profiles(path: Path = ZSHRC_PATH) -> list[dict[str, str]]:
    assignments = _parse_zshrc_assignments(path)
    profiles: list[dict[str, str]] = []
    for api_type, variables in API_PROFILE_VARS.items():
        key_name = next((name for name in variables["keys"] if assignments.get(name)), "")
        if not key_name:
            continue
        base_url = next(
            (assignments[name] for name in variables["base_urls"] if assignments.get(name)),
            "",
        )
        profiles.append(
            {
                "id": f"zshrc:{api_type}",
                "api_type": api_type,
                "label": f".zshrc - {api_type}",
                "key_name": key_name,
                "base_url": base_url,
            }
        )
    return profiles


def _effective_api_config(config: dict[str, Any]) -> dict[str, Any]:
    selected = str(config.get("api_profile", "")).strip()
    if selected.startswith("zshrc:"):
        assignments = _parse_zshrc_assignments()
        api_type = selected.split(":", 1)[1]
        variables = API_PROFILE_VARS.get(api_type)
        if variables is not None:
            api_key = next(
                (assignments[name] for name in variables["keys"] if assignments.get(name)),
                "",
            )
            base_url = next(
                (assignments[name] for name in variables["base_urls"] if assignments.get(name)),
                "",
            )
            if api_key:
                effective = dict(config)
                effective.update({"api_type": api_type, "api_key": api_key, "base_url": base_url})
                return effective
    return dict(config)


def _selected_api_profile(
    configured: str, explicit: bool, profiles: list[dict[str, str]]
) -> str:
    available_ids = {profile["id"] for profile in profiles}
    if configured in available_ids or (configured == "manual" and explicit):
        return configured
    if "zshrc:deepseek" in available_ids:
        return "zshrc:deepseek"
    if "zshrc:openrouter" in available_ids:
        return "zshrc:openrouter"
    return profiles[0]["id"] if profiles else "manual"


OFFICIAL_LARGE301_CASE = "data/official/large_seed301.txt"
DEFAULT_BOOTSTRAP_SOLVER = ROOT / "data" / "official" / "example_solution.txt"


def _is_readable_file(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
        with path.open("rb"):
            return True
    except OSError:
        return False


def _normalize_bootstrap_solver_path(path_text: str | None) -> str:
    raw = str(path_text or "").strip()
    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        if _is_readable_file(candidate):
            return str(candidate)
    if _is_readable_file(DEFAULT_BOOTSTRAP_SOLVER):
        return str(DEFAULT_BOOTSTRAP_SOLVER)
    return raw


def _read_report_json(report_path: Path) -> dict[str, Any]:
    json_path = report_path.with_suffix(".json")
    if not report_path.exists() or not json_path.exists():
        raise RuntimeError(f"Genius did not produce report files: {report_path}")
    return json.loads(json_path.read_text(encoding="utf-8"))


def _read_report_average_score(report_path: Path) -> float:
    lines = report_path.read_text(encoding="utf-8").splitlines()
    # When Genius hits a hard error (missing dataset dir, py3.9 not found, etc.)
    # it still writes the report TXT but with a FATAL header. Surface that line
    # so the user sees the actual cause instead of a generic "Invalid" message.
    if lines and lines[0].startswith("FATAL"):
        detail = lines[1] if len(lines) >= 2 else ""
        raise RuntimeError(f"Genius FATAL: {detail or lines[0]} (see {report_path})")
    if len(lines) < 2 or lines[0] != "Average Penalty Score":
        raise RuntimeError(f"Invalid Genius TXT report: {report_path}")
    return float(lines[1])


def _publish_scoring_progress(progress_path: Path) -> None:
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    name = str(progress.get("case_name", "-"))
    pct = int(progress.get("progress_pct", 0))
    completed = int(progress.get("completed_cases", 0))
    total = int(progress.get("total_cases", 0))
    label = "评分完成" if progress.get("stage") == "finished" else "正在评分"
    with STATE_LOCK:
        STATE["scoring_status"] = f"{label}: {name} ({pct}%)"
        STATE["completed_cases"] = f"{completed}/{total}"


def _submit_to_genius(
    solver_path: str | Path,
    input_dir: str | Path,
    report_path: Path,
    log_path: Path,
    scoring: str = FIXED_SCORING_MODE,
    publish_progress: bool = True,
) -> dict[str, Any]:
    progress_path = report_path.with_suffix(".progress.json")
    _safe_unlink(progress_path)
    with STATE_LOCK:
        max_case_seconds = float(STATE["config"].get("max_case_seconds") or 25.0)
    cmd = [
        sys.executable,
        str(ROOT / "genius" / "run_submission.py"),
        "--solver",
        str(solver_path),
        "--input-dir",
        str(input_dir),
        "--scoring",
        scoring,
        "--report",
        str(report_path),
        "--log-path",
        str(log_path),
        "--progress-file",
        str(progress_path),
        "--max-case-seconds",
        f"{max_case_seconds:g}",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while proc.poll() is None:
        if publish_progress:
            _publish_scoring_progress(progress_path)
        time.sleep(0.05)
    stdout, stderr = proc.communicate()
    if publish_progress:
        _publish_scoring_progress(progress_path)
    if proc.returncode != 0:
        raise RuntimeError((stderr or stdout or "Genius submission failed").strip())
    return _read_report_json(report_path)


def _score_official_large301(solver_path: str | Path) -> float | None:

    case_path = ROOT / OFFICIAL_LARGE301_CASE
    if not case_path.exists():
        _append_log(f"official large301 case missing: {case_path}")
        return None
    case_dir = ROOT / "out" / "runs" / "_official_metric_case"
    case_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(case_path, case_dir / case_path.name)
    report_path = ROOT / "out" / "reports" / "official_large301_latest.txt"
    log_path = ROOT / "out" / "logs" / f"genius_large301_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    try:
        report = _submit_to_genius(
            solver_path=solver_path,
            input_dir=case_dir,
            report_path=report_path,
            log_path=log_path,
            publish_progress=False,
        )
        case = report["cases"][0]
        score = round(float(case["score"]), 4)
        with STATE_LOCK:
            STATE["official_large301_score"] = score
        _append_log(
            f"official_large301 score={case['score']:.4f} "
            f"covered={case['covered']}/{case['total_tasks']}"
        )
        return score
    except Exception as exc:
        _append_log(f"official_large301 failed: {exc}")
        return None


def _scoreboard_path() -> Path:
    return ROOT / "out" / "scoreboard.json"


def _load_scoreboard() -> list[dict[str, Any]]:
    path = _scoreboard_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _compute_bucket_min_avg() -> float | None:
    """Average of every bucket's current champion score across all active
    datasets under out/memory/runs/*/buckets/. Represents the theoretical
    lower-bound average score the router would hit if each case ran its own
    bucket champion."""
    buckets_glob = (ROOT / "out" / "memory" / "runs").glob("*/buckets/*/meta.json")
    total = 0.0
    n = 0
    for meta_path in buckets_glob:
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            total += float(data["score"])
            n += 1
        except (OSError, ValueError, KeyError):
            continue
    return (total / n) if n else None


def _append_scoreboard(score: float, source: str, official_large301: float | None = None) -> None:
    path = _scoreboard_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = _load_scoreboard()
    if source.startswith("fool_iter_"):
        entries = [entry for entry in entries if str(entry.get("source", "")) != source]
    next_seq = max((int(e.get("seq", 0)) for e in entries), default=0) + 1
    bucket_min_avg = _compute_bucket_min_avg()
    entries.append(
        {
            "seq": next_seq,
            "score": float(score),
            "official_large301": None if official_large301 is None else float(official_large301),
            "bucket_min_avg": bucket_min_avg,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
        }
    )
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _save_runtime_config() -> None:
    cfg_path = ROOT / "out" / "runtime_config.json"
    with STATE_LOCK:
        config = dict(STATE["config"])
    config["api_key"] = ""
    cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def _load_runtime_config() -> None:
    cfg_path = ROOT / "out" / "runtime_config.json"
    if not cfg_path.exists():
        return
    try:
        payload = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    with STATE_LOCK:
        allowed = set(STATE["config"].keys())
        for key, value in payload.items():
            if key not in allowed:
                continue
            if key == "api_key" and not str(value).strip():
                continue
            STATE["config"][key] = value
        STATE["config"]["bootstrap_solver_path"] = _normalize_bootstrap_solver_path(
            str(STATE["config"].get("bootstrap_solver_path", ""))
        )
        STATE["config"]["scoring_mode"] = FIXED_SCORING_MODE
        STATE["total_iterations"] = int(STATE["config"].get("iterations", 0))


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except OSError:
        pass


def _purge_global_notes() -> str | None:
    """Destructive: archive the entire out/ directory to a timestamped zip under
    out_backups/, then wipe out/. Returns the archive path (str) on success, or
    None if there was nothing to archive."""
    import zipfile

    out_root = ROOT / "out"
    if not out_root.exists() or not out_root.is_dir():
        return None

    backups_root = ROOT / "out_backups"
    backups_root.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = backups_root / f"out_{stamp}.zip"

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in out_root.rglob("*"):
            if entry.is_file():
                zf.write(entry, entry.relative_to(out_root.parent))

    for child in out_root.iterdir():
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            continue

    return str(archive_path)


def _fool_event_callback(ev: dict[str, Any]) -> None:
    ev_type = ev.get("type")
    if ev_type == "log":
        _append_log(str(ev.get("message", "")))
        return

    if ev_type == "status":
        with STATE_LOCK:
            if "stage" in ev:
                STATE["stage"] = ev["stage"]
            if "iteration" in ev:
                STATE["current_iteration"] = ev["iteration"]
        return

    if ev_type == "ai_status":
        with STATE_LOCK:
            STATE["ai_connected"] = bool(ev.get("ok", False))
            STATE["ai_endpoint"] = str(ev.get("endpoint", ""))
            STATE["ai_message"] = str(ev.get("message", ""))
        _append_log(
            f"ai_status ok={ev.get('ok', False)} endpoint={ev.get('endpoint', '')}"
        )
        return

    if ev_type == "llm_io":
        entry = {
            "direction": str(ev.get("direction", "")),
            "purpose": str(ev.get("purpose", "")),
            "content": str(ev.get("content", "")),
            "ts": str(ev.get("ts", "")),
            "meta": ev.get("meta") if isinstance(ev.get("meta"), dict) else {},
        }
        with STATE_LOCK:
            STATE["llm_dialogue"].append(entry)
            STATE["llm_dialogue"] = STATE["llm_dialogue"][-50:]
        return

    if ev_type == "thought_start":
        iteration = int(ev.get("iteration", 0))
        item = {
            "iteration": iteration,
            "analysis": "",
            "reason": "",
            "hypothesis": "",
            "target_buckets": [],
            "edit_plan": [],
            "safety_checks": [],
            "planning_source": "harness",
            "fallback_reason": "",
            "steps": [],
            "outcome": "",
            "score": None,
            "score_delta": None,
            "guardrail_flag": "",
            "bucket_deltas": [],
        }
        with STATE_LOCK:
            existing = next(
                (it for it in STATE["thoughts"] if int(it.get("iteration", 0)) == iteration),
                None,
            )
            if existing is None:
                STATE["thoughts"].append(item)
        return

    if ev_type == "thought_intent":
        iteration = int(ev.get("iteration", 0))
        step = {
            "step": int(ev.get("step", 0)),
            "tool_step": 0,
            "tool_name": "(intent)",
            "tool_ok": True,
            "tool_content": str(ev.get("text", ""))[:1200],
        }
        with STATE_LOCK:
            for item in reversed(STATE["thoughts"]):
                if int(item.get("iteration", 0)) == iteration:
                    item.setdefault("steps", []).append(step)
                    item["steps"] = item["steps"][-80:]
                    break
        return

    if ev_type == "thought_step":
        iteration = int(ev.get("iteration", 0))
        step = {
            "step": int(ev.get("step", 0)),
            "tool_step": int(ev.get("tool_step", 0)),
            "tool_name": str(ev.get("tool_name", "")),
            "tool_ok": bool(ev.get("tool_ok", False)),
            "tool_content": str(ev.get("tool_content", ""))[:240],
        }
        with STATE_LOCK:
            for item in reversed(STATE["thoughts"]):
                if int(item.get("iteration", 0)) == iteration:
                    item.setdefault("steps", []).append(step)
                    item["steps"] = item["steps"][-50:]
                    break
        return

    if ev_type == "thought_final":
        iteration = int(ev.get("iteration", 0))
        with STATE_LOCK:
            for item in reversed(STATE["thoughts"]):
                if int(item.get("iteration", 0)) == iteration:
                    item.update(
                        {
                            "analysis": str(ev.get("analysis", "")),
                            "reason": str(ev.get("reason", "")),
                            "hypothesis": str(ev.get("hypothesis", "")),
                            "target_buckets": list(ev.get("target_buckets", [])),
                            "edit_plan": list(ev.get("edit_plan", [])),
                            "safety_checks": list(ev.get("safety_checks", [])),
                        }
                    )
                    break
        return

    if ev_type == "thought_result":
        iteration = int(ev.get("iteration", 0))
        with STATE_LOCK:
            for item in reversed(STATE["thoughts"]):
                if int(item.get("iteration", 0)) == iteration:
                    item.update(
                        {
                            "outcome": str(ev.get("outcome", "")),
                            "score": ev.get("score"),
                            "score_delta": ev.get("score_delta"),
                            "guardrail_flag": str(ev.get("guardrail_flag", "")),
                            "bucket_deltas": list(ev.get("bucket_deltas", []) or []),
                            "target_buckets": list(ev.get("target_buckets", []) or item.get("target_buckets") or []),
                        }
                    )
                    break
        return

    if ev_type == "baseline_scored":
        # Fresh-start baseline (iteration 0): seed STATE so the UI shows the
        # bootstrap solver's penalty as the initial best before round 1 runs.
        score = ev.get("score")
        solver_path = str(ev.get("solver_path", ""))
        report_path = str(ev.get("report_path", ""))
        with STATE_LOCK:
            STATE["best_score"] = score
            STATE["current_score"] = score
            STATE["best_solver_path"] = solver_path
            STATE["latest_report_path"] = report_path
            STATE["latest_solver_path"] = solver_path
            STATE["completed_cases"] = (
                f"{ev.get('solved_cases', 0)}/{ev.get('total_cases', 0)}"
            )
        official_score = _score_official_large301(solver_path) if solver_path else None
        if score is not None:
            try:
                _append_scoreboard(
                    float(score),
                    "fool_baseline",
                    official_large301=official_score,
                )
            except (TypeError, ValueError):
                pass
        _append_log(
            f"baseline score={score} cases={ev.get('solved_cases')}/{ev.get('total_cases')}"
        )
        return

    if ev_type == "iteration_result":
        with STATE_LOCK:
            STATE["current_iteration"] = int(ev.get("iteration", 0))
            STATE["current_score"] = ev.get("score")
            STATE["completed_cases"] = f"{ev.get('solved_cases', 0)}/{ev.get('total_cases', 0)}"
            STATE["latest_report_path"] = str(ev.get("report_path", ""))
            if ev.get("solver_path"):
                STATE["latest_solver_path"] = str(ev.get("solver_path", ""))
        _append_log(
            f"iter={ev.get('iteration')} score={ev.get('score')} "
            f"cases={ev.get('solved_cases')}/{ev.get('total_cases')}"
        )
        solver_path_for_official = ev.get("solver_path")
        official_score = (
            _score_official_large301(solver_path_for_official)
            if solver_path_for_official
            else None
        )
        if ev.get("score") is not None:
            _append_scoreboard(
                float(ev["score"]),
                f"fool_iter_{ev.get('iteration')}",
                official_large301=official_score,
            )


def _approval_provider(iteration: int, thought: dict[str, Any]) -> bool:
    with STATE_LOCK:
        auto_accept = bool(STATE["config"].get("auto_accept", True))
    if auto_accept:
        return True

    event = threading.Event()
    with APPROVAL_LOCK:
        PENDING_APPROVAL["event"] = event
        PENDING_APPROVAL["iteration"] = iteration
        PENDING_APPROVAL["accepted"] = False
    with STATE_LOCK:
        STATE["stage"] = f"waiting for iteration approval (iter {iteration})"
        STATE["awaiting_approval"] = True
        STATE["approval_iteration"] = iteration

    while not event.is_set():
        if STOP_EVENT.is_set():
            break
        event.wait(timeout=0.5)

    with APPROVAL_LOCK:
        accepted = bool(PENDING_APPROVAL["accepted"]) and not STOP_EVENT.is_set()
        PENDING_APPROVAL["event"] = None
        PENDING_APPROVAL["iteration"] = 0
        PENDING_APPROVAL["accepted"] = False
    with STATE_LOCK:
        STATE["awaiting_approval"] = False
        STATE["approval_iteration"] = 0
    return accepted



def _run_fool_worker() -> None:
    global RUN_THREAD
    try:
        from fool.fool_loop import run_fool_loop

        with STATE_LOCK:
            cfg = _effective_api_config(dict(STATE["config"]))
            STATE["last_error"] = ""
            # One-shot override: 继续 button feeds best_solver_path here.
            bootstrap_override = str(STATE.pop("_continue_bootstrap", "") or "")

        bootstrap_for_run = bootstrap_override or (str(cfg.get("bootstrap_solver_path", "")).strip() or None)

        result = run_fool_loop(
            api_type=cfg.get("api_type", "openai"),
            api_key=cfg.get("api_key", ""),
            base_url=cfg.get("base_url") or None,
            model=cfg.get("model", "gpt-4.1-mini"),
            iterations=int(cfg.get("iterations", 5)),
            input_dir=str(cfg.get("dataset_path", ROOT / "data" / "sample_10_cases")),
            scoring=FIXED_SCORING_MODE,
            bootstrap_solver_path=bootstrap_for_run,
            verbose=bool(cfg.get("verbose", True)),
            require_ai=bool(cfg.get("require_ai", True)),
            max_tokens=str(cfg.get("max_tokens", "8000")),
            max_steps_per_round=int(cfg.get("max_steps_per_round", 50)),
            effort_level=str(cfg.get("effort_level", "low")),
            stop_event=STOP_EVENT,
            event_callback=_fool_event_callback,
            approval_provider=_approval_provider,
        )

        with STATE_LOCK:
            STATE["best_score"] = result.get("best_score")
            STATE["best_solver_path"] = result.get("best_solver_path", "")
            STATE["running"] = False
            STATE["stage"] = "stopped" if STOP_EVENT.is_set() else "finished"
        _append_log("fool loop stopped" if STOP_EVENT.is_set() else "fool loop finished")
    except Exception as exc:  # pragma: no cover
        err_text = str(exc)
        with STATE_LOCK:
            STATE["running"] = False
            STATE["stage"] = "error"
            STATE["last_error"] = err_text
            lowered = err_text.lower()
            if (
                "ai connection failed" in lowered
                or "llm request failed" in lowered
                or "empty response from api" in lowered
            ):
                STATE["ai_connected"] = False
                STATE["ai_message"] = err_text[:200]
        _append_log(f"fool loop error: {exc}")
    finally:
        RUN_THREAD = None


def _read_json_payload(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _send_json(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _serve_file(handler: BaseHTTPRequestHandler, file_path: Path, content_type: str) -> None:
    if not file_path.exists():
        handler.send_error(HTTPStatus.NOT_FOUND, "Not found")
        return
    content = file_path.read_bytes()
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


class MTASAHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        _append_log(fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            _serve_file(self, ROOT / "frontend" / "index.html", "text/html; charset=utf-8")
            return
        if self.path == "/app.js":
            _serve_file(self, ROOT / "frontend" / "app.js", "application/javascript; charset=utf-8")
            return
        if self.path == "/styles.css":
            _serve_file(self, ROOT / "frontend" / "styles.css", "text/css; charset=utf-8")
            return

        if self.path == "/api/status":
            with STATE_LOCK:
                data = {
                    "running": STATE["running"],
                    "stage": STATE["stage"],
                    "current_iteration": STATE["current_iteration"],
                    "total_iterations": STATE["total_iterations"],
                    "current_score": STATE["current_score"],
                    "best_score": STATE["best_score"],
                    "best_solver_path": STATE["best_solver_path"],
                    "completed_cases": STATE["completed_cases"],
                    "ai_connected": STATE["ai_connected"],
                    "ai_endpoint": STATE["ai_endpoint"],
                    "ai_message": STATE["ai_message"],
                    "last_error": STATE["last_error"],
                    "official_large301_score": STATE.get("official_large301_score"),
                    "scoring_status": STATE.get("scoring_status", ""),
                    "awaiting_approval": STATE.get("awaiting_approval", False),
                    "approval_iteration": STATE.get("approval_iteration", 0),
                }
            _send_json(self, data)
            return

        if self.path == "/api/config":
            with STATE_LOCK:
                cfg = dict(STATE["config"])
            cfg["api_key"] = ""
            _send_json(self, cfg)
            return

        if self.path == "/api/api_profiles":
            with STATE_LOCK:
                selected = str(STATE["config"].get("api_profile", ""))
                explicit = bool(STATE["config"].get("api_profile_explicit", False))
            profiles = [
                {"id": profile["id"], "label": profile["label"], "api_type": profile["api_type"]}
                for profile in _discover_zshrc_profiles()
            ]
            selected = _selected_api_profile(selected, explicit, profiles)
            _send_json(self, {"profiles": profiles, "selected": selected})
            return

        if self.path == "/api/latest_report":
            with STATE_LOCK:
                rp = STATE.get("latest_report_path", "")
            text = ""
            if rp and Path(rp).exists():
                text = Path(rp).read_text(encoding="utf-8")
            _send_json(self, {"path": rp, "report": text})
            return

        if self.path == "/api/latest_report_json":
            with STATE_LOCK:
                rp = STATE.get("latest_report_path", "")
            cases: list[Any] = []
            payload_extra: dict[str, Any] = {}
            if rp:
                json_path = Path(rp).with_suffix(".json")
                if json_path.exists():
                    try:
                        data = json.loads(json_path.read_text(encoding="utf-8"))
                        cases = list(data.get("cases", []))
                        payload_extra = {
                            "average_score": data.get("average_score"),
                            "solved_cases": data.get("solved_cases"),
                            "total_cases": data.get("total_cases"),
                        }
                    except (OSError, json.JSONDecodeError):
                        pass
            _send_json(self, {"path": rp, "cases": cases, **payload_extra})
            return

        if self.path == "/api/latest_solver":
            with STATE_LOCK:
                sp = STATE.get("latest_solver_path", "")
            text = ""
            if sp and Path(sp).exists():
                text = Path(sp).read_text(encoding="utf-8")
            _send_json(self, {"path": sp, "code": text})
            return

        if self.path == "/api/best":
            with STATE_LOCK:
                data = {
                    "best_score": STATE.get("best_score"),
                    "best_solver_path": STATE.get("best_solver_path", ""),
                }
            _send_json(self, data)
            return

        if self.path == "/api/scoreboard":
            entries = _load_scoreboard()
            entries_sorted = sorted(entries, key=lambda e: float(e.get("score", float("inf"))))
            _send_json(self, {"entries": entries_sorted[:50]})
            return

        if self.path == "/api/logs":
            with STATE_LOCK:
                logs = list(STATE.get("logs", []))
            _send_json(self, {"logs": logs, "text": "\n".join(logs)})
            return

        if self.path == "/api/thoughts":
            with STATE_LOCK:
                thoughts = list(STATE.get("thoughts", []))
            _send_json(self, {"thoughts": thoughts})
            return

        if self.path == "/api/llm_dialogue":
            with STATE_LOCK:
                dialogue = list(STATE.get("llm_dialogue", []))
            _send_json(self, {"dialogue": dialogue})
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        global RUN_THREAD

        if self.path == "/api/config":
            payload = _read_json_payload(self)
            with STATE_LOCK:
                existing_api_key = str(STATE["config"].get("api_key", ""))
                STATE["config"].update(payload)
                STATE["config"]["bootstrap_solver_path"] = _normalize_bootstrap_solver_path(
                    str(STATE["config"].get("bootstrap_solver_path", ""))
                )
                if "api_profile" in payload:
                    STATE["config"]["api_profile_explicit"] = True

                incoming_api_key = str(payload.get("api_key", "")) if "api_key" in payload else None
                force_clear_api_key = bool(payload.get("force_clear_api_key", False))
                if (
                    incoming_api_key is not None
                    and not incoming_api_key.strip()
                    and existing_api_key.strip()
                    and not force_clear_api_key
                ):
                    STATE["config"]["api_key"] = existing_api_key

                STATE["config"]["scoring_mode"] = FIXED_SCORING_MODE
                try:
                    mcs = float(STATE["config"].get("max_case_seconds", 25.0))
                except (TypeError, ValueError):
                    mcs = 25.0
                STATE["config"]["max_case_seconds"] = max(1.0, min(120.0, mcs))
                STATE["total_iterations"] = int(STATE["config"].get("iterations", 0))
            if bool(payload.get("auto_accept", False)):
                with APPROVAL_LOCK:
                    if PENDING_APPROVAL.get("event"):
                        PENDING_APPROVAL["accepted"] = True
                        PENDING_APPROVAL["event"].set()
            _save_runtime_config()
            _append_log("config updated")
            _send_json(self, {"ok": True})
            return

        if self.path == "/api/scoreboard/clear":
            _scoreboard_path().write_text("[]", encoding="utf-8")
            _append_log("scoreboard cleared")
            _send_json(self, {"ok": True})
            return

        if self.path == "/api/purge_global_notes":
            # 1) Signal stop and unblock any pending waits so the worker can exit.
            STOP_EVENT.set()
            with PENDING_LOCK:
                if PENDING_SCORE.get("event"):
                    PENDING_SCORE["event"].set()
                PENDING_SCORE["event"] = None
                PENDING_SCORE["result"] = None
            with APPROVAL_LOCK:
                if PENDING_APPROVAL.get("event"):
                    PENDING_APPROVAL["event"].set()
                PENDING_APPROVAL["event"] = None
                PENDING_APPROVAL["iteration"] = 0
                PENDING_APPROVAL["accepted"] = False
            with STATE_LOCK:
                if STATE.get("running"):
                    STATE["stage"] = "stopping"

            # 2) Wait for the worker thread to actually exit before touching files.
            thread = RUN_THREAD
            stopped_cleanly = True
            if thread is not None and thread.is_alive():
                _append_log("purge: waiting for fool worker to stop")
                thread.join(timeout=30.0)
                stopped_cleanly = not thread.is_alive()
                if not stopped_cleanly:
                    _append_log(
                        "purge: worker still alive after 30s — aborting purge to avoid races",
                    )
                    _send_json(
                        self,
                        {
                            "ok": False,
                            "error": "Fool worker did not stop within 30s; try again after it finishes the current LLM call.",
                        },
                        status=409,
                    )
                    return

            # 3) Archive + wipe out/.
            archive = _purge_global_notes()

            # 4) Reset in-memory STATE so the UI reflects a clean slate.
            with STATE_LOCK:
                STATE["running"] = False
                STATE["stage"] = "idle"
                STATE["current_iteration"] = 0
                STATE["total_iterations"] = 0
                STATE["current_score"] = None
                STATE["best_score"] = None
                STATE["best_solver_path"] = ""
                STATE["completed_cases"] = "0/0"
                STATE["latest_report_path"] = ""
                STATE["latest_solver_path"] = ""
                STATE["official_large301_score"] = None
                STATE["scoring_status"] = ""
                STATE["thoughts"] = []
                STATE["llm_dialogue"] = []
                STATE["awaiting_approval"] = False
                STATE["approval_iteration"] = 0
                STATE["logs"] = []
                STATE["last_error"] = ""

            if archive:
                _append_log(f"out/ archived to {archive} and wiped")
                _send_json(self, {"ok": True, "archive": archive})
            else:
                _append_log("purge requested but out/ was empty")
                _send_json(self, {"ok": True, "archive": None})
            return

        if self.path == "/api/accept_iteration":
            with APPROVAL_LOCK:
                event = PENDING_APPROVAL.get("event")
                iteration = int(PENDING_APPROVAL.get("iteration", 0))
                if event is None:
                    _send_json(self, {"ok": False, "error": "当前没有待接受的轮次"}, status=409)
                    return
                PENDING_APPROVAL["accepted"] = True
                event.set()
            _append_log(f"iteration {iteration} accepted by user")
            _send_json(self, {"ok": True, "iteration": iteration})
            return

        if self.path == "/api/upload_dataset":
            payload = _read_json_payload(self)
            dataset_path = str(payload.get("dataset_path", "")).strip()
            if not dataset_path or not Path(dataset_path).exists():
                _send_json(self, {"ok": False, "error": "dataset_path not found"}, status=400)
                return
            with STATE_LOCK:
                STATE["config"]["dataset_path"] = dataset_path
            _append_log(f"dataset path set: {dataset_path}")
            _send_json(self, {"ok": True, "dataset_path": dataset_path})
            return

        if self.path == "/api/run_genius":
            try:
                payload = _read_json_payload(self)
                with STATE_LOCK:
                    cfg = dict(STATE["config"])

                edited_code = str(payload.get("code", "")) if isinstance(payload, dict) else ""

                with PENDING_LOCK:
                    pending_event = PENDING_SCORE.get("event")
                    pending_solver = PENDING_SCORE.get("solver_path", "")
                    pending_input_dir = PENDING_SCORE.get("input_dir", "")
                    pending_scoring = PENDING_SCORE.get("scoring", "")
                    pending_log = PENDING_SCORE.get("log_path", "")
                    pending_report = PENDING_SCORE.get("report_path", "")

                if pending_event is not None:
                    solver_path = Path(pending_solver)
                    if edited_code.strip():
                        solver_path.write_text(edited_code, encoding="utf-8")
                    input_dir_for_judge = pending_input_dir or str(cfg.get("dataset_path", ROOT / "data" / "sample_10_cases"))
                    scoring_for_judge = pending_scoring or FIXED_SCORING_MODE
                    report_path = (
                        Path(pending_report)
                        if pending_report
                        else ROOT / "out" / "reports" / "manual_latest_report.txt"
                    )
                    with STATE_LOCK:
                        STATE["current_score"] = None
                        STATE["completed_cases"] = "0/0"
                        STATE["scoring_status"] = "准备评分"
                    report_obj = _submit_to_genius(
                        solver_path=solver_path,
                        input_dir=input_dir_for_judge,
                        report_path=report_path,
                        log_path=Path(pending_log) if pending_log else ROOT / "out" / "logs" / "genius_pending.log",
                        scoring=scoring_for_judge,
                    )
                    judged_score = _read_report_average_score(report_path)
                    with STATE_LOCK:
                        STATE["latest_report_path"] = str(report_path)
                        STATE["latest_solver_path"] = str(solver_path)
                        STATE["current_score"] = judged_score
                        STATE["completed_cases"] = f"{report_obj['solved_cases']}/{report_obj['total_cases']}"
                    official_score = _score_official_large301(solver_path)
                    _append_scoreboard(judged_score, "manual_edit_pending", official_large301=official_score)
                    with PENDING_LOCK:
                        PENDING_SCORE["result"] = report_obj
                        if PENDING_SCORE.get("event"):
                            PENDING_SCORE["event"].set()
                    _append_log("manual score delivered to fool loop")
                    _send_json(self, {"ok": True, "report_path": str(report_path), "score": judged_score})
                    return

                if edited_code.strip():
                    manual_dir = ROOT / "out" / "runs" / "manual"
                    manual_dir.mkdir(parents=True, exist_ok=True)
                    solver_path = manual_dir / "solver_manual.py"
                    solver_path.write_text(edited_code, encoding="utf-8")
                else:
                    solver_path = ROOT / "out" / "solvers" / "best_solver.py"
                    if not solver_path.exists():
                        solver_path = ROOT / "fool" / "templates" / "solver_greedy.py"

                manual_log = ROOT / "out" / "logs" / f"genius_manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
                input_dir_manual = str(cfg.get("dataset_path", ROOT / "data" / "sample_10_cases"))
                with STATE_LOCK:
                    STATE["current_score"] = None
                    STATE["completed_cases"] = "0/0"
                    STATE["scoring_status"] = "准备评分"
                report_path = ROOT / "out" / "reports" / "manual_latest_report.txt"
                report_obj = _submit_to_genius(
                    solver_path=solver_path,
                    input_dir=input_dir_manual,
                    report_path=report_path,
                    log_path=manual_log,
                )

                judged_score = _read_report_average_score(report_path)
                best_solver_out = ROOT / "out" / "solvers" / "best_solver.py"
                best_report_out = ROOT / "out" / "reports" / "best_report.txt"

                with STATE_LOCK:
                    STATE["latest_report_path"] = str(report_path)
                    STATE["latest_solver_path"] = str(solver_path)
                    STATE["current_score"] = judged_score
                    STATE["completed_cases"] = f"{report_obj['solved_cases']}/{report_obj['total_cases']}"
                    current_best = STATE.get("best_score")
                    should_update_best = current_best is None or judged_score < float(current_best)
                    if should_update_best:
                        STATE["best_score"] = judged_score
                        STATE["best_solver_path"] = str(best_solver_out)

                if current_best is None or judged_score < float(current_best):
                    best_solver_out.parent.mkdir(parents=True, exist_ok=True)
                    best_report_out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(solver_path, best_solver_out)
                    shutil.copyfile(report_path, best_report_out)
                source = "manual_edit" if edited_code.strip() else "manual_best"
                official_score = _score_official_large301(solver_path)
                _append_scoreboard(judged_score, source, official_large301=official_score)
                _append_log("run_genius completed")
                _send_json(self, {"ok": True, "report_path": str(report_path)})
            except Exception as exc:  # pragma: no cover
                _append_log(f"run_genius error: {exc}")
                _send_json(self, {"ok": False, "error": str(exc)}, status=500)
            return

        if self.path == "/api/run_fool":
            try:
                from fool.llm_client import probe_llm_connection

                with STATE_LOCK:
                    cfg = _effective_api_config(dict(STATE["config"]))

                probe = probe_llm_connection(
                    api_type=str(cfg.get("api_type", "openai")),
                    api_key=str(cfg.get("api_key", "")),
                    model=str(cfg.get("model", "gpt-4.1-mini")),
                    base_url=str(cfg.get("base_url", "")).strip() or None,
                    timeout=25,
                    effort_level=str(cfg.get("effort_level", "low")),
                )
                with STATE_LOCK:
                    STATE["ai_connected"] = bool(probe.get("ok", False))
                    STATE["ai_endpoint"] = str(probe.get("endpoint", ""))
                    STATE["ai_message"] = str(probe.get("message", ""))

                if not probe.get("ok", False):
                    msg = (
                        f"AI not connected: {probe.get('message', 'unknown')} "
                        f"endpoint={probe.get('endpoint', '')}"
                    )
                    with STATE_LOCK:
                        STATE["stage"] = "error"
                        STATE["last_error"] = msg
                    _append_log(msg)
                    _send_json(self, {"ok": False, "error": msg}, status=400)
                    return
            except Exception as exc:
                _append_log(f"AI preflight error: {exc}")
                _send_json(self, {"ok": False, "error": str(exc)}, status=500)
                return

            with STATE_LOCK:
                dataset_path_str = str(STATE["config"].get("dataset_path", "")).strip()
            dataset_path = Path(dataset_path_str)
            if not dataset_path_str or not dataset_path.exists() or not dataset_path.is_dir():
                msg = (
                    f"数据集路径不存在或不是目录: {dataset_path_str or '(empty)'}. "
                    f"请在'数据集路径'里改成有效路径（默认 data/sample_10_cases）后再点开始。"
                )
                with STATE_LOCK:
                    STATE["stage"] = "error"
                    STATE["last_error"] = msg
                _append_log(msg)
                _send_json(self, {"ok": False, "error": msg}, status=400)
                return

            with STATE_LOCK:
                if STATE["running"]:
                    _send_json(self, {"ok": False, "error": "fool already running"}, status=409)
                    return
                STATE["running"] = True
                STATE["stage"] = "generating solver"
                STATE["current_iteration"] = 0
                STATE["total_iterations"] = int(STATE["config"].get("iterations", 0))
                STATE["llm_dialogue"] = []
                STATE["thoughts"] = []
                STATE["last_error"] = ""
            STOP_EVENT.clear()
            RUN_THREAD = threading.Thread(target=_run_fool_worker, daemon=True)
            RUN_THREAD.start()
            _append_log("run_fool started")
            _send_json(self, {"ok": True})
            return

        if self.path == "/api/continue":
            with STATE_LOCK:
                best_path = str(STATE.get("best_solver_path", "")).strip()
            if not best_path or not Path(best_path).exists():
                msg = "无上次最佳算法可继续。请先用「开始运行」跑出至少一轮。"
                _send_json(self, {"ok": False, "error": msg}, status=400)
                return

            try:
                from fool.llm_client import probe_llm_connection

                with STATE_LOCK:
                    cfg = _effective_api_config(dict(STATE["config"]))

                probe = probe_llm_connection(
                    api_type=str(cfg.get("api_type", "openai")),
                    api_key=str(cfg.get("api_key", "")),
                    model=str(cfg.get("model", "gpt-4.1-mini")),
                    base_url=str(cfg.get("base_url", "")).strip() or None,
                    timeout=25,
                    effort_level=str(cfg.get("effort_level", "low")),
                )
                with STATE_LOCK:
                    STATE["ai_connected"] = bool(probe.get("ok", False))
                    STATE["ai_endpoint"] = str(probe.get("endpoint", ""))
                    STATE["ai_message"] = str(probe.get("message", ""))

                if not probe.get("ok", False):
                    msg = (
                        f"AI not connected: {probe.get('message', 'unknown')} "
                        f"endpoint={probe.get('endpoint', '')}"
                    )
                    with STATE_LOCK:
                        STATE["stage"] = "error"
                        STATE["last_error"] = msg
                    _append_log(msg)
                    _send_json(self, {"ok": False, "error": msg}, status=400)
                    return
            except Exception as exc:
                _append_log(f"AI preflight error: {exc}")
                _send_json(self, {"ok": False, "error": str(exc)}, status=500)
                return

            with STATE_LOCK:
                if STATE["running"]:
                    _send_json(self, {"ok": False, "error": "fool already running"}, status=409)
                    return
                STATE["running"] = True
                STATE["stage"] = "generating solver"
                STATE["current_iteration"] = 0
                STATE["total_iterations"] = int(STATE["config"].get("iterations", 0))
                STATE["last_error"] = ""
                # Note: do NOT clear thoughts / llm_dialogue — continuation
                # naturally extends the prior context for the user.
                STATE["_continue_bootstrap"] = best_path
            STOP_EVENT.clear()
            RUN_THREAD = threading.Thread(target=_run_fool_worker, daemon=True)
            RUN_THREAD.start()
            _append_log(f"continue from best_solver={best_path}")
            _send_json(self, {"ok": True, "bootstrap": best_path})
            return

        if self.path == "/api/check_api":
            try:
                from fool.llm_client import probe_llm_connection

                with STATE_LOCK:
                    cfg = _effective_api_config(dict(STATE["config"]))
                probe = probe_llm_connection(
                    api_type=str(cfg.get("api_type", "openai")),
                    api_key=str(cfg.get("api_key", "")),
                    model=str(cfg.get("model", "gpt-4.1-mini")),
                    base_url=str(cfg.get("base_url", "")).strip() or None,
                    timeout=25,
                    effort_level=str(cfg.get("effort_level", "low")),
                )
                with STATE_LOCK:
                    STATE["ai_connected"] = bool(probe.get("ok", False))
                    STATE["ai_endpoint"] = str(probe.get("endpoint", ""))
                    STATE["ai_message"] = str(probe.get("message", ""))
                _append_log(
                    f"api_check ok={probe.get('ok', False)} endpoint={probe.get('endpoint', '')}"
                )
                _send_json(self, {"ok": bool(probe.get("ok", False)), **probe})
            except Exception as exc:
                with STATE_LOCK:
                    STATE["ai_connected"] = False
                    STATE["ai_message"] = str(exc)
                _send_json(self, {"ok": False, "error": str(exc)}, status=500)
            return

        if self.path == "/api/stop":
            STOP_EVENT.set()
            with PENDING_LOCK:
                if PENDING_SCORE.get("event"):
                    PENDING_SCORE["event"].set()
            with STATE_LOCK:
                # Worker thread may still be inside a blocking LLM call.
                # Don't claim it's stopped until the worker actually exits;
                # the worker thread sets stage="stopped" on the way out.
                if STATE.get("running"):
                    STATE["stage"] = "stopping"
                else:
                    STATE["stage"] = "stopped"
            with APPROVAL_LOCK:
                if PENDING_APPROVAL.get("event"):
                    PENDING_APPROVAL["event"].set()
            _append_log("stop requested")
            _send_json(self, {"ok": True})
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")


def start_server(root: Path, host: str = "127.0.0.1", port: int = 7860) -> None:
    global ROOT
    ROOT = root.resolve()
    ensure_runtime_dirs(ROOT)
    _load_runtime_config()
    server = ThreadingHTTPServer((host, port), MTASAHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
