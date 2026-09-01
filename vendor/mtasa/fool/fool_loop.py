from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
_GLOBAL_NOTES_ROOT = Path(__file__).resolve().parents[1] / "out" / "memory"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fool.genius_file_client import FIXED_SCORING_MODE, read_report, submit_solver
from fool.llm_client import probe_llm_connection
from fool.memory_store import FoolMemory
from fool.harness import (
    HarnessAborted,
    HarnessFailure,
    LLMModelClient,
    ModelClient,
    RoundOutcome,
    RoundState,
    build_default_registry,
    run_round,
)
from fool.bucket_classify import (
    RoundOutcome as BucketRoundOutcome,
    classify_round_bucketed,
)
from fool.harness.outcome_reflector import reflect_and_write
from fool.harness.session_compactor import SessionCompactor
from fool.harness.teacher_review import format_review_block, maybe_run_review
from fool.harness.tools import ToolContext
from fool.memory_notes import MemoryNotesStore


EventCallback = Callable[[dict[str, Any]], None]


def _make_session_summary_callback(memory, run_id: str, iteration: int):
    """Build a SessionCompactor summary_callback that persists to FoolMemory.

    target_buckets is [] because the callback fires mid-round, before
    the harness emits its <final> plan with actual buckets. FoolMemory.retrieve()
    surfaces these records on any query (empty rec_tags matches everything).
    """
    def _cb(summary: str, previous_summary: str) -> None:
        memory.record_session_summary(
            run_id=run_id,
            iteration=iteration,
            target_buckets=[],
            summary_text=summary,
        )
    return _cb


class _FoolLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._t0 = time.perf_counter()

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def line(self, message: str) -> None:
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000)
        self._fh.write(f"[{self._ts()} +{elapsed_ms}ms] {message}\n")
        self._fh.flush()

    def block(self, title: str, body: str) -> None:
        if not body:
            return
        self.line(f"{title}:")
        for ln in str(body).splitlines():
            self._fh.write(f"    {ln}\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


_FOOL_LOGGER: _FoolLogger | None = None


def _emit(callback: EventCallback | None, payload: dict[str, Any]) -> None:
    logger = _FOOL_LOGGER
    if logger is not None:
        kind = str(payload.get("type", "event"))
        if kind == "log":
            logger.line(f"log: {payload.get('message', '')}")
        elif kind == "status":
            iter_part = f" iter={payload['iteration']}" if "iteration" in payload else ""
            logger.line(f"status: stage={payload.get('stage', '')}{iter_part}")
        elif kind == "ai_status":
            logger.line(
                f"ai_status: ok={payload.get('ok')} endpoint={payload.get('endpoint', '')} "
                f"message={str(payload.get('message', ''))[:200]}"
            )
        elif kind == "thought_intent":
            logger.line(
                f"intent: iter={payload.get('iteration')} step={payload.get('step')} "
                f"text={str(payload.get('text', ''))[:300]}"
            )
        elif kind == "iteration_result":
            logger.line(
                f"iteration_result: iter={payload.get('iteration')} score={payload.get('score')} "
                f"cases={payload.get('solved_cases')}/{payload.get('total_cases')} "
                f"solver={payload.get('solver_path', '')} report={payload.get('report_path', '')}"
            )
        else:
            logger.line(f"{kind}: {payload}")
    if callback:
        callback(payload)


def _classify_case_type(
    case_name: str,
    tasks: int,
    cand_per_task: float,
    avg_w: float,
    score_cv: float,
) -> tuple[str, str]:
    name = case_name.lower()
    if name.startswith("tiny"):
        return "tiny", "name_prefix=tiny"
    if name.startswith("small"):
        return "small", "name_prefix=small"
    if name.startswith("medium"):
        return "medium", "name_prefix=medium"
    if name.startswith("large"):
        return "large", "name_prefix=large"
    if name.startswith("low_willingness") or name.startswith("low_w"):
        return "low_willingness", "name_prefix=low_willingness"
    if name.startswith("scarce_couriers") or name.startswith("scarce"):
        return "scarce_couriers", "name_prefix=scarce"
    if name.startswith("high_noise"):
        return "high_noise", "name_prefix=high_noise"

    if tasks <= 8:
        return "tiny", "tasks<=8"
    if tasks <= 16:
        return "small", "tasks<=16"
    if avg_w <= 0.28:
        return "low_willingness", "avg_w<=0.28"
    if tasks >= 35 and cand_per_task <= 500:
        return "scarce_couriers", "tasks>=35 and cand_per_task<=500"
    if tasks >= 25 and score_cv >= 0.50:
        return "high_noise", "tasks>=25 and score_cv>=0.50"
    if tasks >= 35:
        return "large", "tasks>=35"
    if tasks >= 25:
        return "medium", "tasks>=25"
    return "normal", "fallback"


def _build_solver_data_contract() -> str:
    return (
        "业务背景（必须理解）：你是一位资深黑客马拉松程序员，在迭代美团骑手分配算法。"
        "每一行输出代表一个通知/分配决策：task_id_list 是一个订单或合单任务集合，"
        "courier_id_or_backup_list_string 是主骑手加可选备选骑手列表。合单是把多个 task_id 用逗号放在同一个 task_id_list 中，"
        "不是让同一个骑手在多行重复接单。备选骑手也是资源占用，不能在其他行再次出现。\n\n"
        "MTASA I/O contract (MUST follow exactly):\n"
        "1) solve signature must be: solve(input_text: str) -> list[tuple[str, str]].\n"
        "2) Input is TAB-delimited rows, not CSV. Use split('\\t') for row columns.\n"
        "3) Input columns are exactly: task_id_list, courier_id, total_score, willingness.\n"
        "4) task_id_list may be merged (e.g. T1,T2); the comma here is inside one cell, not a column separator.\n"
        "5) Do NOT invent columns like task/courier/visible/distance; those columns do not exist.\n"
        "6) Output each row as (task_id_list, courier_id_or_backup_list_string).\n"
        "7) Keep stable format and do not output markdown/text explanations.\n"
        "8) 严重扣分：任何 courier_id 无论作为主骑手、备选骑手、合单行骑手，只要在任意输出行重复出现，"
        "该行会被判 invalid，相当于其中任务失去覆盖并吃 100 分 uncovered 惩罚。"
        "任何 task_id 重复覆盖也同样 invalid。生成输出时必须维护 used_couriers（包含主骑手和全部备选）"
        "和 used_tasks，候选行只要重复骑手或重复任务就跳过。\n"
        "8a) Skip the header line (`task_id_list\\tcourier_id\\ttotal_score\\twillingness`) before parsing numeric fields; "
        "attempting float() on the header crashes the solver.\n"
        "9) Keep pure Python stdlib only, Python 3.6 compatible, deterministic behavior.\n"
        "10) Keep runtime under 30s per case and solver file <=100KB.\n\n"
        "Portfolio strategy:\n"
        "- 不要死磕单个 scarce 桶。scarce 的 uncovered 很显眼，但 high-score normal/dense 桶数量多、基数大，"
        "经常有更稳定的平均分收益。\n"
        "- 如果同一 scarce 假设连续 2 轮 neutral/rollback，下一轮必须转向 large/medium/low_w/high_noise 等高分满覆盖桶，"
        "优先做低风险 tie-break/score ordering，而不是继续扩大 scarce 搜索。\n\n"
        "10 benchmark case buckets and classification hints:\n"
        "- tiny_seed42: very small task count (~6).\n"
        "- small_seed100: small task count (~15).\n"
        "- medium_seed201: medium scale (~30 tasks), normal willingness.\n"
        "- medium_seed202: medium scale (~30 tasks), normal willingness.\n"
        "- medium_seed203: medium scale (~30 tasks), normal willingness.\n"
        "- large_seed301: large scale (~40 tasks), dense candidates.\n"
        "- large_seed302: large scale (~40 tasks), dense candidates.\n"
        "- low_willingness_seed501: avg willingness notably low (hard hidden penalties).\n"
        "- scarce_couriers_seed401: large tasks with low candidate/task density.\n"
        "- high_noise_seed601: high score variance / noisy candidate quality.\n"
    )


def _build_dataset_profile(input_dir: str) -> str:
    root = Path(input_dir)
    files = sorted(root.glob("*.txt"))
    if not files:
        return "No dataset files found."

    lines = []
    for file_path in files:
        text = file_path.read_text(encoding="utf-8")
        rows = [line.strip() for line in text.splitlines() if line.strip()]
        start = 1 if rows and rows[0].startswith("task_id_list") else 0
        table: dict[tuple[str, str], tuple[list[str], float, float]] = {}
        tasks: set[str] = set()
        for row in rows[start:]:
            parts = row.split("\t")
            if len(parts) < 4:
                continue
            row_tasks = sorted({x.strip() for x in parts[0].split(",") if x.strip()})
            if not row_tasks:
                continue
            try:
                score = float(parts[2])
                willingness = float(parts[3])
            except ValueError:
                continue
            key = (",".join(row_tasks), parts[1].strip())
            candidate = (row_tasks, score, willingness)
            previous = table.get(key)
            if previous is None:
                table[key] = candidate
            else:
                previous_std = previous[1] / max(previous[2], 0.05)
                current_std = score / max(willingness, 0.05)
                if current_std < previous_std or (
                    current_std == previous_std and score < previous[1]
                ):
                    table[key] = candidate
            tasks.update(row_tasks)
        ws = [v[2] for v in table.values()]
        scores = [v[1] for v in table.values()]
        avg_w = (sum(ws) / len(ws)) if ws else 0.0
        cand_per_task = (len(table) / max(len(tasks), 1)) if tasks else 0.0
        if scores:
            mean_score = statistics.mean(scores)
            score_cv = (statistics.pstdev(scores) / max(abs(mean_score), 1e-9))
        else:
            score_cv = 0.0
        case_type, rule = _classify_case_type(
            case_name=file_path.stem,
            tasks=len(tasks),
            cand_per_task=cand_per_task,
            avg_w=avg_w,
            score_cv=score_cv,
        )
        lines.append(
            f"- {file_path.stem}: type={case_type}, tasks={len(tasks)}, candidates={len(table)}, "
            f"cand_per_task={cand_per_task:.2f}, avg_w={avg_w:.4f}, score_cv={score_cv:.4f}, rule={rule}"
        )
    return "\n".join(lines)


def _summarize_report_for_prompt(report_obj: dict[str, Any], title: str, top_k: int = 5) -> str:
    cases = list(report_obj.get("cases", []))
    if not cases:
        return f"{title}: no case details"

    sorted_cases = sorted(cases, key=lambda c: float(c.get("score", 0.0)), reverse=True)
    lines = [
        f"{title}: avg_score={float(report_obj.get('average_score', 0.0)):.4f}, "
        f"valid_cases={int(report_obj.get('valid_cases', 0))}/{int(report_obj.get('total_cases', 0))}"
    ]
    for case in sorted_cases[:top_k]:
        lines.append(
            f"- {case.get('case_name', '')}: score={float(case.get('score', 0.0)):.2f}, "
            f"coverage={int(case.get('covered', 0))}/{int(case.get('total_tasks', 0))}, "
            f"uncovered={int(case.get('uncovered_tasks', 0))}, extra_notify={int(case.get('extra_notify', 0))}, "
            f"merged={int(case.get('merged_rows', 0))}"
        )
    return "\n".join(lines)


def _build_case_delta_feedback(current: dict[str, Any], reference: dict[str, Any]) -> str:
    curr_map = {str(c.get("case_name", "")): c for c in current.get("cases", [])}
    ref_map = {str(c.get("case_name", "")): c for c in reference.get("cases", [])}
    lines: list[str] = []
    for case_name in sorted(curr_map.keys()):
        if case_name not in ref_map:
            continue
        curr = curr_map[case_name]
        ref = ref_map[case_name]
        delta_score = float(curr.get("score", 0.0)) - float(ref.get("score", 0.0))
        delta_uncovered = int(curr.get("uncovered_tasks", 0)) - int(ref.get("uncovered_tasks", 0))
        if abs(delta_score) < 1e-6 and delta_uncovered == 0:
            continue
        lines.append(
            f"- {case_name}: delta_score={delta_score:+.2f}, delta_uncovered={delta_uncovered:+d}, "
            f"coverage={int(curr.get('covered', 0))}/{int(curr.get('total_tasks', 0))}"
        )
    if not lines:
        return "No per-case delta versus incumbent."
    return "\n".join(lines)


def _case_score_map(report_obj: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if report_obj is None:
        return {}
    return {
        str(case.get("case_name", "")): case
        for case in report_obj.get("cases", [])
        if case.get("case_name")
    }


def build_run_lesson_record(
    *,
    run_id: str,
    memory_scope: str,
    rounds: list[dict[str, Any]],
    baseline_report: dict[str, Any] | None,
    best_report: dict[str, Any] | None,
    reflection_memory: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not rounds or best_report is None:
        return None

    baseline_score = float(
        (baseline_report or {}).get("average_score", rounds[0].get("score", 0.0))
        or rounds[0].get("score", 0.0)
        or 0.0
    )
    best_score = float(best_report.get("average_score", min(float(r.get("score", 0.0)) for r in rounds)))
    best_cases = _case_score_map(best_report)
    baseline_cases = _case_score_map(baseline_report)

    bottleneck_rows = sorted(
        best_cases.values(),
        key=lambda case: (
            float(case.get("score", 0.0)),
            int(case.get("uncovered_tasks", 0)),
        ),
        reverse=True,
    )
    bottlenecks = [str(case.get("case_name", "")) for case in bottleneck_rows[:3] if case.get("case_name")]

    winning_cases: list[str] = []
    for name, best_case in best_cases.items():
        base_case = baseline_cases.get(name)
        if not base_case:
            continue
        delta = float(best_case.get("score", 0.0)) - float(base_case.get("score", 0.0))
        if delta < -1e-6:
            winning_cases.append(f"{name}({delta:+.1f})")
    winning_cases = sorted(
        winning_cases,
        key=lambda item: float(re.search(r"\(([+-][0-9.]+)\)", item).group(1)) if re.search(r"\(([+-][0-9.]+)\)", item) else 0.0,
    )[:5]

    improved = [item for item in reflection_memory if str(item.get("outcome")) == "improved"]
    failed = [
        item
        for item in reflection_memory
        if str(item.get("outcome")) in {"rollback", "regressed", "neutral", "duplicate_skipped"}
        or str(item.get("_fallback_reason", "")).startswith("reflection_exception")
    ]
    worked: list[str] = []
    for item in improved[-4:]:
        hypo = str(item.get("hypothesis", "")).strip()
        if hypo:
            worked.append(f"{hypo} delta={float(item.get('score_delta', 0.0)):+.1f}")
    if winning_cases:
        worked.append("保留已证明有效的跨桶收益: " + ", ".join(winning_cases))

    avoid: list[str] = []
    for item in failed[-5:]:
        hypo = str(item.get("hypothesis", "")).strip()
        if not hypo:
            continue
        delta = float(item.get("score_delta", 0.0))
        avoid.append(f"{hypo} delta={delta:+.1f}")
    if any(str(item.get("_fallback_reason", "")).startswith("reflection_exception") for item in reflection_memory):
        avoid.append("反思超时后不要复用泛化 fallback；必须基于 best_report 重新选目标和模板")

    remaining_gap_to_1100 = max(0.0, (best_score - 1100.0) * max(len(best_cases), 1))
    next_steps = [
        "从 durable best 继续改，不从零重写 solver",
        "保护 high_noise/large/medium/small/tiny 的既有收益，只允许局部影响 low_w/scarce",
    ]
    if remaining_gap_to_1100 > 0:
        next_steps.append(f"突破 1100 需要总分至少再降 {remaining_gap_to_1100:.1f}，优先从 low_w/scarce 高分桶拿收益")
    for case in bottleneck_rows[:2]:
        name = str(case.get("case_name", ""))
        score = float(case.get("score", 0.0))
        covered = int(case.get("covered", 0))
        total = int(case.get("total_tasks", 0))
        next_steps.append(f"重点分析 {name}: score={score:.1f}, coverage={covered}/{total}")

    return {
        "run_id": run_id,
        "memory_scope": memory_scope,
        "baseline_score": baseline_score,
        "best_score": best_score,
        "total_delta": best_score - baseline_score,
        "round_count": len(rounds),
        "best_iteration": min(rounds, key=lambda row: float(row.get("score", 0.0))).get("iteration"),
        "bottlenecks": bottlenecks,
        "winning_cases": winning_cases,
        "worked": worked[:6],
        "avoid": avoid[:6],
        "next_steps": next_steps[:6],
    }


def _report_totals(report_obj: dict[str, Any]) -> tuple[int, int, int]:
    cases = list(report_obj.get("cases", []))
    uncovered_total = sum(int(c.get("uncovered_tasks", 0)) for c in cases)
    extra_total = sum(int(c.get("extra_notify", 0)) for c in cases)
    merged_total = sum(int(c.get("merged_rows", 0)) for c in cases)
    return uncovered_total, extra_total, merged_total


def _resolve_bootstrap_solver_path(path_text: str | None) -> Path | None:
    default_path = ROOT / "data" / "official" / "example_solution.txt"
    default_suffix = ("data", "official", "example_solution.txt")

    def _is_readable_file(candidate: Path) -> bool:
        try:
            if not candidate.is_file():
                return False
            with candidate.open("rb"):
                return True
        except OSError:
            return False

    raw = str(path_text or "").strip()
    if raw:
        path = Path(raw)
        if path.is_absolute():
            if (
                path != default_path
                and path.parts[-len(default_suffix):] == default_suffix
                and _is_readable_file(default_path)
            ):
                return default_path
        else:
            path = ROOT / path
        if _is_readable_file(path):
            return path

    if raw and _is_readable_file(default_path):
        return default_path

    return None


def _dataset_memory_scope(input_dir: str) -> str:
    digest = hashlib.sha256()
    paths = sorted(Path(input_dir).glob("*.txt"))
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16] if paths else "empty_dataset"


def _normalize_solver_source(code: str) -> str:
    lines: list[str] = []
    for raw in code.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"\s+#.*$", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _solver_signatures(code: str) -> tuple[str, str, str]:
    canonical = _normalize_solver_source(code)
    exact_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    structural = re.sub(r"\b\d+(?:\.\d+)?\b", "NUM", canonical)
    structural = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\"", "STR", structural)
    structural_hash = hashlib.sha256(structural.encode("utf-8")).hexdigest()
    return exact_hash, structural_hash, canonical


def _is_tiny_solver_delta(base_canonical: str, candidate_canonical: str) -> bool:
    if not base_canonical or not candidate_canonical:
        return False
    if base_canonical == candidate_canonical:
        return True
    ratio = difflib.SequenceMatcher(None, base_canonical, candidate_canonical).ratio()
    return ratio >= 0.995


def _bucket_deltas_vs_prev(
    *,
    cur: dict[str, float],
    prev_report_path: Path | None,
) -> list[dict]:
    """Compute per-bucket Δ for the reflector. Returns [] if no prev available.

    Reads the JSON sibling of prev_report_path (`*.json`) to recover the
    incumbent's per-case scores.
    """
    if not cur or prev_report_path is None:
        return []
    prev_json = prev_report_path.with_suffix(".json")
    if not prev_json.is_file():
        return []
    try:
        import json as _json
        prev_obj = _json.loads(prev_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    prev_map: dict[str, float] = {}
    for c in prev_obj.get("cases") or []:
        name = str(c.get("case_name", ""))
        if not name:
            continue
        try:
            prev_map[name] = float(c.get("score", 0.0))
        except (TypeError, ValueError):
            continue
    out: list[dict] = []
    for name in sorted(set(cur) | set(prev_map)):
        p = prev_map.get(name)
        c = cur.get(name)
        if p is None or c is None:
            continue
        out.append({"bucket": name, "prev": p, "cur": c, "delta": c - p})
    return out


def _detect_stagnation(recent_history: list, *, window: int = 3, threshold: float = 1.0) -> bool:
    """True iff the last `window` rounds all have outcome != 'improved' AND
    |score - prev_score| < threshold on each step. Used to decide whether to
    decay old try_errors so the next round can revisit blocked directions."""
    if len(recent_history) < window:
        return False
    tail = recent_history[-window:]
    if any(r.outcome == "improved" for r in tail):
        return False
    scores = [getattr(r, "score", None) for r in tail]
    if any(s is None for s in scores):
        return False
    for a, b in zip(scores, scores[1:]):
        if abs(float(b) - float(a)) >= threshold:
            return False
    return True


def _case_counts_from_report_path(report_path: Path | None) -> tuple[int, int]:
    if report_path is None or not report_path.exists():
        return (0, 0)
    try:
        report = read_report(report_path)
    except Exception:
        return (0, 0)
    cases = list(report.get("cases", []))
    solved_cases = sum(1 for case in cases if int(case.get("uncovered_tasks", 0)) == 0)
    return solved_cases, len(cases)


def build_model_client(
    *, api_type: str, api_key: str, model: str, base_url: str | None, effort_level: str
) -> ModelClient:
    return LLMModelClient(
        api_type=api_type,
        api_key=api_key,
        model=model,
        base_url=base_url,
        effort_level=effort_level,
    )


def submit_solver_to_genius(
    solver_path: Path | str,
    input_dir: Path | str,
    run_dir: Path | str,
    iteration: int,
) -> dict[str, Any]:
    """Submit a solver to Genius and return {total_score, report_path, report}."""
    report_path = Path(run_dir) / f"report_v{iteration:03d}.txt"
    submit_solver(
        solver_path=Path(solver_path),
        input_dir=Path(input_dir),
        report_path=report_path,
    )
    report = read_report(report_path)
    fatal_message = report.get("fatal_message")
    if fatal_message:
        # Genius could not actually score the solver (e.g. >100KB, py39
        # incompatible). The synthetic average_score=0.0 in the fatal
        # report would otherwise be promoted as "best" by the < comparison.
        raise HarnessFailure(f"genius fatal: {fatal_message}")
    # Genius reports use average_score; expose as total_score for the harness contract.
    total = float(report.get("average_score", float("inf")))
    return {"total_score": total, "report_path": report_path, "report": report}


def _score_baseline_solver(
    *,
    bootstrap_path: Path,
    input_dir: Path,
    run_dir: Path,
    out_root: Path,
    durable_memory: "FoolMemory",
    event_callback: EventCallback | None,
    version_index: "VersionIndex | None" = None,
    run_id: str = "",
    dataset_fp: str = "",
) -> dict[str, Any]:
    """Score the bootstrap solver via Genius and seed it as the initial best.

    Writes the solver as ``solver_v000.py`` and the report as
    ``report_v000.txt`` inside ``run_dir`` so they sit alongside the regular
    per-iteration artifacts, mirrors them to ``out/solvers/best_solver.py`` /
    ``out/reports/best_report.txt``, and records the score in durable memory
    so a subsequent run does not re-score the same solver.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    baseline_solver_path = run_dir / "solver_v000.py"
    baseline_solver_path.write_text(
        bootstrap_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _emit(
        event_callback,
        {"type": "status", "stage": "baseline_scoring", "iteration": 0},
    )
    _emit(
        event_callback,
        {
            "type": "log",
            "message": f"scoring bootstrap solver as baseline: {bootstrap_path}",
        },
    )
    submission = submit_solver_to_genius(
        solver_path=baseline_solver_path,
        input_dir=input_dir,
        run_dir=run_dir,
        iteration=0,
    )
    score = float(submission["total_score"])
    report_path = Path(submission["report_path"])

    if version_index is not None:
        baseline_v = version_index.allocate(
            run_id=run_id,
            iteration=0,
            dataset_fp=dataset_fp,
            ts=datetime.now().isoformat(timespec="seconds"),
        )
        version_index.record_paths(
            baseline_v,
            solver_path=baseline_solver_path,
            report_path=report_path,
        )
        report_obj_for_v = submission.get("report", {}) or {}
        cases_for_v = list(report_obj_for_v.get("cases", []))
        solved_for_v = sum(
            1 for c in cases_for_v if int(c.get("uncovered_tasks", 0)) == 0
        )
        version_index.record_outcome(
            baseline_v,
            score=score,
            solved_cases=solved_for_v,
            total_cases=len(cases_for_v),
            outcome="baseline",
            plan_headline="bootstrap solver scored as initial incumbent",
            bucket_scores={
                str(c.get("case_name", "?")): float(c.get("score", 0.0))
                for c in cases_for_v
            },
            bucket_uncovered={
                str(c.get("case_name", "?")): int(c.get("uncovered_tasks", 0))
                for c in cases_for_v
            },
        )

    (out_root / "solvers").mkdir(parents=True, exist_ok=True)
    (out_root / "reports").mkdir(parents=True, exist_ok=True)
    shutil.copy2(baseline_solver_path, out_root / "solvers" / "best_solver.py")
    shutil.copy2(report_path, out_root / "reports" / "best_report.txt")
    try:
        durable_memory.store_best_if_better(
            score=score,
            solver_path=baseline_solver_path,
            report_path=report_path,
        )
    except AttributeError:
        pass

    report_obj = submission.get("report", {}) or {}
    cases = list(report_obj.get("cases", []))
    solved_cases = sum(1 for c in cases if int(c.get("uncovered_tasks", 0)) == 0)
    total_cases = len(cases)
    _emit(
        event_callback,
        {
            "type": "baseline_scored",
            "iteration": 0,
            "score": score,
            "solver_path": str(baseline_solver_path),
            "report_path": str(report_path),
            "solved_cases": solved_cases,
            "total_cases": total_cases,
        },
    )
    _emit(
        event_callback,
        {
            "type": "log",
            "message": (
                f"baseline scored: penalty={score:.2f} "
                f"cases={solved_cases}/{total_cases} (lower is better)"
            ),
        },
    )
    return {
        "score": score,
        "solver_path": baseline_solver_path,
        "report_path": report_path,
    }


def run_fool_loop(
    api_type: str,
    api_key: str,
    model: str,
    iterations: int,
    input_dir: str,
    scoring: str,
    base_url: str | None = None,
    bootstrap_solver_path: str | None = None,
    verbose: bool = True,
    require_ai: bool = True,
    max_tokens: str | int = 8000,
    effort_level: str = "low",
    stop_event: Event | None = None,
    event_callback: EventCallback | None = None,
    approval_provider: Callable[[int, dict[str, Any]], bool] | None = None,
    max_steps_per_round: int = 50,
) -> dict[str, Any]:
    stop_event = stop_event or Event()

    if scoring != FIXED_SCORING_MODE:
        raise ValueError(
            f"Scoring mode is fixed to {FIXED_SCORING_MODE}; received: {scoring}"
        )
    if require_ai and not api_key:
        raise RuntimeError("API key is required; refusing to run without AI.")
    if require_ai:
        probe = probe_llm_connection(
            api_type=api_type,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=25,
            effort_level=effort_level,
        )
        if not probe.get("ok", False):
            raise RuntimeError(f"AI connection failed: {probe.get('message','?')}")

    out_root = ROOT / "out"
    run_id = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir = out_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    global _FOOL_LOGGER
    _FOOL_LOGGER = _FoolLogger(run_dir / "fool.log")
    _FOOL_LOGGER.line(f"run_fool_loop start run_id={run_id} iterations={iterations}")

    bootstrap_path = _resolve_bootstrap_solver_path(bootstrap_solver_path)
    dataset_profile = _build_dataset_profile(input_dir)
    memory_scope = _dataset_memory_scope(input_dir)
    durable_memory = FoolMemory(scope=memory_scope)
    memory_notes_store = MemoryNotesStore(root=_GLOBAL_NOTES_ROOT)
    from fool.version_index import VersionIndex
    version_index = VersionIndex(out_root / "version_index.json")
    registry = build_default_registry()
    model_client = build_model_client(
        api_type=api_type,
        api_key=api_key,
        model=model,
        base_url=base_url,
        effort_level=effort_level,
    )

    if isinstance(max_tokens, str) and "," in max_tokens:
        _FOOL_LOGGER.line("max_tokens schedule deprecated; using max value")
        budget = max(int(x) for x in max_tokens.split(",") if x.strip())
    else:
        budget = int(max_tokens)

    best_score: float | None = durable_memory.stored_best_score()
    best_solver_path: Path | None = (
        durable_memory.best_solver_path
        if best_score is not None and durable_memory.best_solver_path.exists()
        else None
    )
    best_report_path: Path | None = (
        durable_memory.best_report_path
        if best_score is not None and durable_memory.best_report_path.exists()
        else None
    )

    # Fresh start (no durable memory): score the bootstrap/official solver
    # so iteration 1 has a real baseline penalty to compare against, instead
    # of inheriting best_score=None and treating the first attempt as
    # "baseline" regardless of quality.
    if best_score is None and bootstrap_path is not None and bootstrap_path.exists():
        try:
            baseline = _score_baseline_solver(
                bootstrap_path=bootstrap_path,
                input_dir=Path(input_dir),
                run_dir=run_dir,
                out_root=out_root,
                durable_memory=durable_memory,
                event_callback=event_callback,
                version_index=version_index,
                run_id=run_id,
                dataset_fp=memory_scope,
            )
        except HarnessFailure as exc:
            _FOOL_LOGGER.line(f"baseline scoring skipped: {exc.reason}")
            _emit(
                event_callback,
                {"type": "log", "message": f"baseline scoring failed: {exc.reason}"},
            )
        else:
            best_score = baseline["score"]
            best_solver_path = baseline["solver_path"]
            best_report_path = baseline["report_path"]

    scored_exact_hashes: set[str] = set()
    best_solver_canonical = ""
    best_solver_structure_hash = ""
    if best_solver_path is not None and best_solver_path.exists():
        try:
            seeded_code = best_solver_path.read_text(encoding="utf-8", errors="replace")
            seeded_exact_hash, seeded_structure_hash, seeded_canonical = _solver_signatures(
                seeded_code
            )
            scored_exact_hashes.add(seeded_exact_hash)
            best_solver_canonical = seeded_canonical
            best_solver_structure_hash = seeded_structure_hash
        except OSError:
            pass

    recent_history: list[RoundOutcome] = []
    rounds_done = 0
    pending_teacher_review_block: str | None = None  # injected into next round_header
    teacher_review_followup_iteration: int | None = None

    def _run_teacher_review_after_round(iteration: int) -> None:
        nonlocal pending_teacher_review_block, teacher_review_followup_iteration
        # Out-of-loop teacher review: fires every 5 rounds or after two
        # consecutive no-progress rounds. The verdict (observation +
        # exhausted directions + 2-3 candidate mechanisms) is rendered into a
        # block and prepended to the NEXT round's user header — suggestion
        # only, no hard gating. Any failure is logged and the loop continues.
        force_trigger = None
        if teacher_review_followup_iteration == iteration:
            force_trigger = "post_advice_followup"
            teacher_review_followup_iteration = None
        try:
            verdict = maybe_run_review(
                run_dir=run_dir,
                iteration=iteration,
                recent_history=recent_history,
                version_index=version_index,
                run_id=run_id,
                judge_model=None,  # fall back to main model_client
                main_model=model_client,
                memory_notes=memory_notes_store,
                force_trigger=force_trigger,
                max_tokens=budget,
            )
            status = (verdict or {}).get("_status")
            if status == "ok":
                pending_teacher_review_block = format_review_block(verdict)
                teacher_review_followup_iteration = iteration + 1
                _FOOL_LOGGER.line(
                    f"iteration {iteration}: teacher_review fired "
                    f"(trigger={verdict.get('_trigger')}); "
                    f"{len(verdict.get('next_candidates') or [])} candidate(s) for next round"
                )
            elif status == "no_trigger":
                _FOOL_LOGGER.line(f"iteration {iteration}: teacher_review skipped (no_trigger)")
            else:
                detail = (verdict or {}).get("_detail", "")
                _FOOL_LOGGER.line(
                    f"iteration {iteration}: teacher_review skipped ({status}) "
                    f"detail={detail[:120]!r}"
                )
        except Exception as exc:  # noqa: BLE001
            _FOOL_LOGGER.line(f"iteration {iteration}: teacher_review crashed: {exc}")

    for i in range(1, iterations + 1):
        if stop_event.is_set():
            break

        # Allocate this round's global version before any harness work so
        # tool snapshots / planning can reference it.
        global_v = version_index.allocate(
            run_id=run_id,
            iteration=i,
            dataset_fp=memory_scope,
            ts=datetime.now().isoformat(timespec="seconds"),
        )

        state = RoundState(
            iteration=i,
            best_score=best_score,
            best_solver_path=best_solver_path,
            best_report_path=best_report_path,
            recent_history=list(recent_history[-3:]),
            input_dir=Path(input_dir),
            run_dir=run_dir,
            bootstrap_solver_path=bootstrap_path,
        )

        def tool_context_factory(_state: RoundState) -> ToolContext:
            return ToolContext(
                input_dir=_state.input_dir,
                run_dir=_state.run_dir,
                best_solver_path=_state.best_solver_path,
                best_report_path=_state.best_report_path,
                last_report_path=(
                    _state.run_dir / f"report_v{_state.iteration - 1:03d}.txt"
                    if _state.iteration > 1
                    else None
                ),
                bootstrap_solver_path=_state.bootstrap_solver_path,
                durable_memory=durable_memory,
                dataset_profile_text=dataset_profile,
                memory_notes=memory_notes_store,
                run_id=_state.run_dir.name,
                iteration=_state.iteration,
                dataset_fp=memory_scope,
                global_v=global_v,
                version_index=version_index,
            )

        _emit(event_callback, {"type": "status", "stage": "harness", "iteration": i})
        _emit(event_callback, {"type": "thought_start", "iteration": i})

        def _on_step(kind: str, payload: dict[str, Any], _iter: int = i) -> None:
            if kind == "llm_in":
                prompt_text = str(payload.get("prompt", ""))
                if len(prompt_text) > 4000:
                    prompt_text = prompt_text[:4000] + "\n…(截断)"
                _emit(
                    event_callback,
                    {
                        "type": "llm_io",
                        "direction": "out",
                        "purpose": "harness",
                        "content": prompt_text,
                        "ts": time.strftime("%H:%M:%S"),
                        "meta": {
                            "iteration": _iter,
                            "step": int(payload.get("step", 0)),
                            "turn_role": str(payload.get("turn_role", "user")),
                            "max_tokens": int(payload.get("max_tokens", 0)),
                        },
                    },
                )
                return
            if kind == "llm_out":
                raw_text = str(payload.get("raw", ""))
                if len(raw_text) > 6000:
                    raw_text = raw_text[:6000] + "\n…(截断)"
                prompt_tokens = int(payload.get("prompt_tokens", 0) or 0)
                completion_tokens = int(payload.get("completion_tokens", 0) or 0)
                cached_tokens = int(payload.get("cached_tokens", 0) or 0)
                if prompt_tokens or completion_tokens:
                    hit_pct = (
                        f"{cached_tokens * 100 // prompt_tokens}%"
                        if prompt_tokens
                        else "?"
                    )
                    _FOOL_LOGGER.line(
                        f"iter={_iter} step={int(payload.get('step', 0))} "
                        f"prompt_tokens={prompt_tokens} cached={cached_tokens} ({hit_pct}) "
                        f"completion_tokens={completion_tokens}"
                    )
                _emit(
                    event_callback,
                    {
                        "type": "llm_io",
                        "direction": "in",
                        "purpose": "harness",
                        "content": raw_text,
                        "ts": time.strftime("%H:%M:%S"),
                        "meta": {
                            "iteration": _iter,
                            "step": int(payload.get("step", 0)),
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "cached_tokens": cached_tokens,
                        },
                    },
                )
                return
            if kind == "intent":
                _emit(
                    event_callback,
                    {
                        "type": "thought_intent",
                        "iteration": _iter,
                        "step": int(payload.get("step", 0)),
                        "text": str(payload.get("text", ""))[:1200],
                    },
                )
                return
            if kind == "tool":
                _emit(
                    event_callback,
                    {
                        "type": "thought_step",
                        "iteration": _iter,
                        "step": int(payload.get("step", 0)),
                        "tool_step": int(payload.get("tool_step", 0)),
                        "tool_name": str(payload.get("name", "")),
                        "tool_args": payload.get("args", {}),
                        "tool_ok": bool(payload.get("ok", False)),
                        "tool_content": str(payload.get("content", ""))[:240],
                    },
                )
            elif kind == "final":
                plan = payload.get("plan") or {}
                _emit(
                    event_callback,
                    {
                        "type": "thought_final",
                        "iteration": _iter,
                        "analysis": str(plan.get("analysis", "")),
                        "reason": str(plan.get("reason", "")),
                        "hypothesis": str(plan.get("hypothesis", "")),
                        "target_buckets": list(plan.get("target_buckets", []) or []),
                        "edit_plan": list(plan.get("edit_plan", []) or []),
                        "safety_checks": list(plan.get("safety_checks", []) or []),
                    },
                )
            elif kind == "retry":
                _emit(
                    event_callback,
                    {
                        "type": "thought_step",
                        "iteration": _iter,
                        "step": int(payload.get("step", 0)),
                        "tool_name": "(malformed)",
                        "tool_ok": False,
                        "tool_content": str(payload.get("message", ""))[:240],
                    },
                )
            elif kind == "final_guard":
                status = str(payload.get("status", ""))
                vio = payload.get("violations") or []
                vio_brief = "; ".join(
                    f"{v.get('type', '?')}:{str(v.get('detail', ''))[:80]}"
                    for v in vio
                    if isinstance(v, dict)
                )[:300]
                _FOOL_LOGGER.line(
                    f"iter={_iter} final_guard attempt={payload.get('attempt')} "
                    f"status={status} violations={len(vio)}"
                    + (f" [{vio_brief}]" if vio_brief else "")
                    + (f" detail={payload.get('detail', '')[:120]}" if payload.get('detail') else "")
                )
                _emit(
                    event_callback,
                    {
                        "type": "thought_step",
                        "iteration": _iter,
                        "step": int(payload.get("step", 0)),
                        "tool_name": "(final_guard)",
                        "tool_ok": status in {"ok", "skipped", "error"},
                        "tool_content": f"status={status} violations={len(vio)} {vio_brief}"[:240],
                    },
                )

        compactor = SessionCompactor(
            summarizer=model_client,
            tool_result_dir=state.run_dir / "tool_results",
            threshold_tokens=80_000,
            reserve_tokens=20_000,
            summary_callback=_make_session_summary_callback(
                memory=durable_memory,
                run_id=run_id,
                iteration=i,
            ),
            memory_notes=memory_notes_store,
            run_id=run_id,
            iteration=i,
        )
        try:
            harness_result = run_round(
                state,
                model_client,
                registry=registry,
                tool_context_factory=tool_context_factory,
                max_steps=max_steps_per_round,
                max_tokens=budget,
                on_step=_on_step,
                compactor=compactor,
                memory_notes=memory_notes_store,
                stop_event=stop_event,
                teacher_review_block=pending_teacher_review_block,
                final_guard_max_attempts=1,
            )
            pending_teacher_review_block = None  # consumed
        except HarnessAborted as exc:
            _FOOL_LOGGER.line(f"iteration {i}: aborted: {exc.reason}")
            _emit(
                event_callback,
                {"type": "log", "message": f"iteration {i} aborted by stop signal"},
            )
            break
        except HarnessFailure as exc:
            _FOOL_LOGGER.line(f"iteration {i}: harness_failed: {exc.reason}")
            recent_history.append(
                RoundOutcome(iteration=i, score=None, hypothesis="", outcome="harness_failed", target_buckets=())
            )
            rounds_done += 1
            continue

        if stop_event.is_set():
            _FOOL_LOGGER.line(
                f"iteration {i}: stop requested after harness, skipping Genius submission"
            )
            _emit(
                event_callback,
                {
                    "type": "log",
                    "message": f"iteration {i} stopped after harness; skipping submission",
                },
            )
            break

        solver_path = run_dir / f"solver_v{i:03d}.py"
        solver_path.write_text(harness_result.solver_code, encoding="utf-8")
        version_index.record_paths(
            global_v,
            solver_path=solver_path,
            harness_path=harness_result.transcript_path,
        )

        hypothesis = str(harness_result.plan.get("hypothesis", ""))
        candidate_exact_hash, candidate_structure_hash, candidate_canonical = _solver_signatures(
            harness_result.solver_code
        )
        duplicate_reason: str | None = None
        if candidate_exact_hash in scored_exact_hashes:
            duplicate_reason = "exact duplicate of a previously scored solver"
        elif (
            best_solver_structure_hash
            and candidate_structure_hash == best_solver_structure_hash
            and _is_tiny_solver_delta(best_solver_canonical, candidate_canonical)
        ):
            duplicate_reason = (
                "near-duplicate of incumbent (same structural hash + tiny textual delta)"
            )

        if duplicate_reason is not None:
            score = best_score
            solved_cases, total_cases = _case_counts_from_report_path(best_report_path)
            version_index.record_paths(
                global_v,
                solver_path=solver_path,
                report_path=best_report_path if best_report_path else "",
            )
            _FOOL_LOGGER.line(
                f"iteration {i}: skipped scoring due to duplicate guard: {duplicate_reason}"
            )
            _emit(
                event_callback,
                {
                    "type": "log",
                    "message": (
                        "duplicate guard: skipped Genius submission for iteration "
                        f"{i} ({duplicate_reason}); score remains {score}."
                    ),
                },
            )

            # Distinct label from "neutral" so the model doesn't read
            # "scored == best" as "we're already optimal". Duplicate guard
            # short-circuited the submission — there is NO real evaluation,
            # the displayed score is just the cached incumbent.
            outcome = "duplicate_skipped"
            version_index.record_outcome(
                global_v,
                outcome=outcome,
                score=float(score) if score is not None else None,
                plan_headline=hypothesis,
            )
            recent_history.append(
                RoundOutcome(
                    iteration=i,
                    score=score,
                    hypothesis=hypothesis,
                    outcome=outcome,
                    target_buckets=tuple(harness_result.plan.get("target_buckets", []) or []),
                )
            )
            try:
                durable_memory.record(
                    {
                        "iteration": i,
                        "hypothesis": hypothesis,
                        "target_buckets": list(
                            harness_result.plan.get("target_buckets", []) or []
                        ),
                        "outcome": outcome,
                        "score": float(score) if score is not None else 0.0,
                        "score_delta": 0.0,
                        "reason": f"duplicate_guard: {duplicate_reason}"[:240],
                    }
                )
            except (TypeError, AttributeError):
                pass
            rounds_done += 1
            _emit(
                event_callback,
                {
                    "type": "thought_result",
                    "iteration": i,
                    "outcome": outcome,
                    "score": score,
                    "score_delta": 0.0,
                    "guardrail_flag": "duplicate_skip",
                },
            )
            _emit(
                event_callback,
                {
                    "type": "round_complete",
                    "iteration": i,
                    "score": score,
                    "outcome": outcome,
                    "hypothesis": hypothesis,
                },
            )
            _emit(
                event_callback,
                {
                    "type": "iteration_result",
                    "iteration": i,
                    "score": score,
                    "solved_cases": solved_cases,
                    "total_cases": total_cases,
                    "report_path": str(best_report_path) if best_report_path else "",
                    "solver_path": str(solver_path),
                },
            )
            _run_teacher_review_after_round(i)
            continue

        try:
            submission = submit_solver_to_genius(
                solver_path=solver_path,
                input_dir=Path(input_dir),
                run_dir=run_dir,
                iteration=i,
            )
        except HarnessFailure as exc:
            # Genius wrote a fatal-report (solver too big, py39 incompatible,
            # etc.) — treat as harness_failed, do not promote the solver.
            _FOOL_LOGGER.line(f"iteration {i}: harness_failed (genius): {exc.reason}")
            recent_history.append(
                RoundOutcome(iteration=i, score=None, hypothesis="", outcome="harness_failed", target_buckets=())
            )
            rounds_done += 1
            continue
        score = submission["total_score"]
        report_path = submission["report_path"]
        scored_exact_hashes.add(candidate_exact_hash)
        _solved_v, _total_v = _case_counts_from_report_path(report_path)
        _cases_v = list((submission.get("report") or {}).get("cases", []) or [])
        _bucket_scores = {
            str(c.get("case_name", "?")): float(c.get("score", 0.0)) for c in _cases_v
        }
        _bucket_uncov = {
            str(c.get("case_name", "?")): int(c.get("uncovered_tasks", 0))
            for c in _cases_v
        }
        version_index.record_paths(global_v, report_path=report_path)
        version_index.record_outcome(
            global_v,
            score=float(score) if score is not None else None,
            uncovered=(_total_v - _solved_v) if (_total_v and _solved_v is not None) else None,
            solved_cases=_solved_v,
            total_cases=_total_v,
            plan_headline=str(harness_result.plan.get("hypothesis", "")),
            bucket_scores=_bucket_scores,
            bucket_uncovered=_bucket_uncov,
        )

        prev_best = best_score
        prev_best_report_path_snapshot = best_report_path  # capture before promotion

        # Bucket-aware classification: per-bucket incumbents decide outcome.
        # `score` (global average) is still tracked for back-compat / UI, but
        # the outcome label is driven by target-bucket Δ vs that bucket's own
        # incumbent — so a round that improves scarce by -50 while leaving
        # large within band is `improved`, not `neutral`.
        target_buckets = list(harness_result.plan.get("target_buckets", []) or [])
        bucket_outcome: BucketRoundOutcome = classify_round_bucketed(
            new_scores=_bucket_scores,
            bucket_incumbents=durable_memory.bucket_incumbents.scores(),
            target_buckets=target_buckets,
            band_rel=0.003,
        )
        outcome = bucket_outcome.label

        # Record per-bucket champion replacements (independent of global best).
        for bucket in bucket_outcome.bucket_replacements:
            durable_memory.bucket_incumbents.record(
                bucket=bucket,
                solver_path=Path(solver_path),
                score=_bucket_scores[bucket],
                round_index=i,
                global_v=global_v,
            )

        version_index.record_outcome(global_v, outcome=outcome)
        version_index.record_paths(
            global_v, reflect_path=run_dir / f"reflect_v{i:03d}.json"
        )
        if outcome in ("improved", "baseline"):
            best_score = score
            best_solver_path = solver_path
            best_report_path = report_path
            best_solver_canonical = candidate_canonical
            best_solver_structure_hash = candidate_structure_hash
            (out_root / "solvers").mkdir(parents=True, exist_ok=True)
            (out_root / "reports").mkdir(parents=True, exist_ok=True)
            shutil.copy2(solver_path, out_root / "solvers" / "best_solver.py")
            shutil.copy2(report_path, out_root / "reports" / "best_report.txt")
            try:
                durable_memory.store_best_if_better(
                    score=score,
                    solver_path=solver_path,
                    report_path=report_path,
                    metadata={"bucket_scores": dict(_bucket_scores)},
                )
            except AttributeError:
                pass

        recent_history.append(
            RoundOutcome(
                iteration=i,
                score=score,
                hypothesis=hypothesis,
                outcome=outcome,
                target_buckets=tuple(target_buckets),
            )
        )
        score_delta = None if prev_best is None else float(score - prev_best)
        try:
            durable_memory.record(
                {
                    "iteration": i,
                    "hypothesis": hypothesis,
                    "target_buckets": list(harness_result.plan.get("target_buckets", []) or []),
                    "outcome": outcome,
                    "score": score,
                    "score_delta": score_delta if score_delta is not None else 0.0,
                    "reason": str(harness_result.plan.get("analysis", ""))[:240],
                }
            )
        except (TypeError, AttributeError):
            pass

        try:
            # prev_best_report_path: the incumbent report BEFORE this round began,
            # so the reflector can compute bucket-level deltas against it. Captured
            # before the improved/baseline branch promotes best_report_path.
            prev_best_report_for_reflect: Path | None = (
                Path(prev_best_report_path_snapshot)
                if prev_best_report_path_snapshot is not None
                else None
            )

            bucket_deltas_for_reflect = _bucket_deltas_vs_prev(
                cur=_bucket_scores,
                prev_report_path=prev_best_report_for_reflect,
            )

            reflect_result = reflect_and_write(
                model=model_client,
                memory_notes=memory_notes_store,
                plan=harness_result.plan,
                outcome=outcome,
                score=float(score) if score is not None else None,
                prev_best=float(prev_best) if prev_best is not None else None,
                score_delta=score_delta,
                report_path=Path(report_path) if report_path else None,
                prev_best_report_path=prev_best_report_for_reflect,
                bucket_deltas=bucket_deltas_for_reflect,
                run_id=run_id,
                iteration=i,
                dataset_fp=memory_scope,
                log_path=run_dir / f"reflect_v{i:03d}.json",
            )
            action = reflect_result.get("action") or "?"
            if reflect_result.get("ok") and action == "written":
                _FOOL_LOGGER.line(
                    f"iteration {i}: reflection wrote {reflect_result.get('section')} "
                    f"'{reflect_result.get('title')}' -> {reflect_result.get('path')}"
                )
            elif reflect_result.get("ok") and action == "updated":
                _FOOL_LOGGER.line(
                    f"iteration {i}: reflection updated "
                    f"{reflect_result.get('path')}:{reflect_result.get('inserted_line')} "
                    f"({reflect_result.get('reason')})"
                )
            elif reflect_result.get("ok") and action == "skipped":
                _FOOL_LOGGER.line(
                    f"iteration {i}: reflection skipped intentionally: "
                    f"{reflect_result.get('reason')}"
                )
            else:
                _FOOL_LOGGER.line(
                    f"iteration {i}: reflection failed: {reflect_result.get('reason')}"
                )
        except Exception as exc:  # noqa: BLE001
            _FOOL_LOGGER.line(f"iteration {i}: reflection crashed: {exc}")

        _run_teacher_review_after_round(i)

        # Stagnation decay: when the run plateaus for ≥3 rounds, halve the
        # confidence of try_errors relevant to the current target buckets so
        # they sink in next round's memory_search ranking. This is the
        # automatic counterweight to over-eager try_error writes — old bans
        # decay when they keep blocking exploration without new evidence.
        try:
            if memory_notes_store is not None and _detect_stagnation(recent_history):
                decay_terms = " ".join(
                    [hypothesis or ""]
                    + [str(b) for b in (harness_result.plan.get("target_buckets") or [])]
                ).strip()
                if decay_terms:
                    changes = memory_notes_store.decay_confidence(
                        query=decay_terms,
                        sections=["try_error"],
                        factor=0.5,
                        max_entries=5,
                    )
                    if changes:
                        _FOOL_LOGGER.line(
                            f"iteration {i}: stagnation decay — halved {len(changes)} "
                            f"try_error confidence(s): "
                            + ", ".join(f"{c['path']}:{c['start_line']} {c['old']}→{c['new']}" for c in changes)
                        )
        except Exception as exc:  # noqa: BLE001
            _FOOL_LOGGER.line(f"iteration {i}: stagnation decay crashed: {exc}")

        rounds_done += 1
        target_buckets_decl = [
            str(x) for x in (harness_result.plan.get("target_buckets") or []) if x
        ]
        bucket_deltas_payload: list[dict[str, Any]] = []
        try:
            run_entries = [
                e for e in version_index.for_run(run_id)
                if isinstance(e.get("bucket_scores"), dict) and e["bucket_scores"]
            ]
            run_entries.sort(key=lambda e: int(e.get("iteration", 0)))
            # Baseline = the incumbent (best total_score among rounds < i),
            # not the most recent prior round. Comparing against the
            # previous round hides regressions when the run plateaus, and
            # contradicts the top-level score_delta which already uses the
            # incumbent.
            prior_scored = [
                e for e in run_entries
                if int(e.get("iteration", -1)) < i
                and isinstance(e.get("score"), (int, float))
            ]
            base_buckets: dict[str, float] = {}
            if prior_scored:
                incumbent_entry = min(prior_scored, key=lambda e: float(e["score"]))
                base_buckets = {
                    str(k): float(v)
                    for k, v in (incumbent_entry.get("bucket_scores") or {}).items()
                }
            target_match_keys: set[str] = set()
            if target_buckets_decl:
                from fool.harness.prompt import _match_bucket_keys  # local helper
                keys_sorted = sorted(_bucket_scores.keys())
                for tb in target_buckets_decl:
                    for k in _match_bucket_keys(tb, keys_sorted):
                        target_match_keys.add(k)
            for key in sorted(_bucket_scores.keys()):
                cur = float(_bucket_scores[key])
                base = base_buckets.get(key)
                bucket_deltas_payload.append(
                    {
                        "bucket": key,
                        "score": cur,
                        "base_score": base,
                        "delta": (cur - base) if base is not None else None,
                        "is_target": key in target_match_keys,
                    }
                )
        except Exception:  # noqa: BLE001
            bucket_deltas_payload = []
        _emit(
            event_callback,
            {
                "type": "thought_result",
                "iteration": i,
                "outcome": outcome,
                "score": score,
                "score_delta": score_delta,
                "guardrail_flag": "",
                "bucket_deltas": bucket_deltas_payload,
                "target_buckets": target_buckets_decl,
            },
        )
        _emit(
            event_callback,
            {
                "type": "round_complete",
                "iteration": i,
                "score": score,
                "outcome": outcome,
                "hypothesis": hypothesis,
            },
        )
        _emit(
            event_callback,
            {
                "type": "iteration_result",
                "iteration": i,
                "score": score,
                "solved_cases": int(submission["report"].get("solved_cases", 0)),
                "total_cases": int(submission["report"].get("total_cases", 0)),
                "report_path": str(report_path),
                "solver_path": str(solver_path),
            },
        )

        try:
            memory_notes_store.aggregate_index()
        except Exception as e:
            _FOOL_LOGGER.line(f"aggregate_index failed: {e}")

    try:
        memory_notes_store.aggregate_index()
    except Exception as e:
        _FOOL_LOGGER.line(f"aggregate_index failed: {e}")
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "iterations_completed": rounds_done,
        "best_score": best_score,
        "best_solver_path": str(best_solver_path) if best_solver_path else "",
        "best_report_path": str(best_report_path) if best_report_path else "",
        "recent_history": [outcome.__dict__ for outcome in recent_history],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Fool iterative loop")
    parser.add_argument("--api-type", default="openai")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--scoring", default=FIXED_SCORING_MODE)
    parser.add_argument("--bootstrap-solver-path", default="")
    parser.add_argument(
        "--max-tokens",
        default="8000",
        help="Max output tokens passed to every Fool AI call in a round.",
    )
    parser.add_argument(
        "--effort-level",
        default="low",
        choices=["low", "high"],
        help="Reasoning-effort hint for reasoning-capable models.",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    def cli_event(ev: dict[str, Any]) -> None:
        ev_type = ev.get("type")
        if ev_type == "log":
            print(ev.get("message", ""), flush=True)
        elif ev_type == "status":
            stage = ev.get("stage", "?")
            it = ev.get("iteration", "?")
            print(f"[status] iter={it} stage={stage}", flush=True)
        elif ev_type == "round_complete":
            score = ev.get("score")
            score_text = f"{score:.2f}" if isinstance(score, (int, float)) else "?"
            print(
                f"iter={ev['iteration']} score={score_text} outcome={ev.get('outcome')}",
                flush=True,
            )

    result = run_fool_loop(
        api_type=args.api_type,
        api_key=args.api_key,
        base_url=args.base_url or None,
        model=args.model,
        iterations=args.iterations,
        input_dir=args.input_dir,
        scoring=args.scoring,
        bootstrap_solver_path=args.bootstrap_solver_path or None,
        verbose=not args.quiet,
        max_tokens=args.max_tokens,
        effort_level=args.effort_level,
        event_callback=cli_event,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
