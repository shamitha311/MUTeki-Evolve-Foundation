"""Periodic out-of-loop teacher review.

After each Genius-scored round, `fool_loop.py` invokes
`maybe_run_review(...)`. When the trigger conditions fire and we haven't
already reviewed at the current iteration, this module:

  1. Assembles a structured snapshot with early-round context, recent
     outcomes, the current best solver, the bottleneck optimization guide,
     per-bucket Δ matrix, smoke-vs-Genius divergence, agent behavior
     counters, and block_patch summary.
  2. Calls `judge_model` (or the main model) one-shot to produce a strict
     JSON verdict: {observation, exhausted_directions, next_candidates}.
  3. Persists the verdict to `<run_dir>/teacher_reviews.jsonl` (dedup) and
     appends a key_decision note to the global memory store.

The verdict is rendered via `format_review_block(...)` and injected at the
top of the next round's `round_header` by the caller (read-only —
**suggestion**, not a soft constraint).

Failure mode: any exception (LLM unreachable, JSON parse error) is caught
and logged at the call site as "skipped"; the loop continues normally.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fool.harness.model_client import ModelClient
from fool.harness.smoke_log import read_smoke_log


_REVIEW_LOG_NAME = "teacher_reviews.jsonl"
_HISTORY_WINDOW = 5
_PLATEAU_TOLERANCE = 0.01
_PERIODIC_EVERY = 5
_NO_PROGRESS_WINDOW = 2
_GUIDE_REL_PATH = Path("teacher") / "MTASA_BOTTLENECK_OPTIMIZATION_GUIDE_CN.md"


# ---------- public API ----------------------------------------------------


def should_trigger(
    *,
    iteration: int,
    recent_history: list,  # list[RoundOutcome]-like, with .score attribute
    last_reviewed_at: int,
) -> str | None:
    """Decide whether teacher_review should fire after `iteration`.

    Returns a short trigger label or None. Dedupes by `last_reviewed_at`:
    won't fire again until at least one more round has been scored.
    """
    if iteration <= 0:
        return None
    if iteration <= last_reviewed_at:
        return None

    # Trigger A: periodic every N rounds.
    if iteration % _PERIODIC_EVERY == 0:
        return f"periodic_every_{_PERIODIC_EVERY}"

    # Trigger B: two consecutive completed rounds without progress. This is
    # intentionally checked after the periodic trigger so round 5/10 logs keep
    # the clearer periodic label.
    if _has_consecutive_no_progress(recent_history, window=_NO_PROGRESS_WINDOW):
        return f"stagnation_{_NO_PROGRESS_WINDOW}round"

    # Trigger C: average score plateau over the last 4 rounds (2-vs-2).
    scored = [
        float(getattr(r, "score", 0.0))
        for r in recent_history
        if getattr(r, "score", None) is not None
    ]
    if len(scored) >= 4:
        last2 = sum(scored[-2:]) / 2.0
        prev2 = sum(scored[-4:-2]) / 2.0
        if abs(last2 - prev2) < _PLATEAU_TOLERANCE:
            return "score_plateau_2v2"
    return None


def _has_consecutive_no_progress(recent_history: list, *, window: int) -> bool:
    completed = [
        r for r in recent_history
        if getattr(r, "score", None) is not None
    ]
    if len(completed) < window:
        return False
    tail = completed[-window:]
    if all(str(getattr(r, "outcome", "") or "") in {"neutral", "regressed", "duplicate_skipped"} for r in tail):
        return True
    scores = [float(getattr(r, "score")) for r in tail]
    return all(scores[idx] >= scores[idx - 1] - _PLATEAU_TOLERANCE for idx in range(1, len(scores)))


def maybe_run_review(
    *,
    run_dir: Path,
    iteration: int,
    recent_history: list,
    version_index: Any,
    run_id: str,
    judge_model: ModelClient | None,
    main_model: ModelClient,
    memory_notes: Any = None,
    max_tokens: int = 1500,
    force_trigger: str | None = None,
) -> dict[str, Any]:
    """If the trigger fires, run one teacher review.

    Always returns a dict with a `_status` field:
        - "ok"            → full verdict payload (plus `_trigger`, `_iteration`,
                            `_ts`, `_run_id`)
        - "no_trigger"    → neither periodic nor plateau fired, or already
                            reviewed at this iteration
        - "build_failed"  → `_build_review_input` raised (with `_detail`)
        - "model_error"   → `client.complete` raised (with `_detail`)
        - "parse_failed"  → model returned text that wasn't valid verdict JSON
                            (with `_detail` = first 200 chars of raw)

    On `model_error`/`parse_failed` the raw response head is also appended to
    `<run_dir>/teacher_review_debug.log` for later inspection. Never raises.
    """
    last_reviewed = _last_reviewed_iter(run_dir)
    if force_trigger:
        trigger = None if iteration <= last_reviewed else force_trigger
    else:
        trigger = should_trigger(
            iteration=iteration,
            recent_history=recent_history,
            last_reviewed_at=last_reviewed,
        )
    if trigger is None:
        return {"_status": "no_trigger"}

    try:
        payload_text = _build_review_input(
            run_dir=run_dir,
            iteration=iteration,
            run_id=run_id,
            version_index=version_index,
            window=_HISTORY_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001
        return {"_status": "build_failed", "_detail": f"{type(exc).__name__}: {exc}"}

    prompt_msgs = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": payload_text},
    ]
    client = judge_model or main_model
    try:
        raw = client.complete(prompt_msgs, max_tokens)
    except Exception as exc:  # noqa: BLE001 — LLM unreachable
        _append_debug(run_dir, iteration, trigger, "model_error",
                      f"{type(exc).__name__}: {exc}")
        return {"_status": "model_error", "_detail": f"{type(exc).__name__}: {exc}"}

    verdict = _parse_verdict(raw)
    if verdict is None:
        head = str(raw or "").strip()[:200]
        _append_debug(run_dir, iteration, trigger, "parse_failed", head)
        return {"_status": "parse_failed", "_detail": head}

    verdict["_status"] = "ok"
    verdict["_trigger"] = trigger
    verdict["_iteration"] = iteration
    verdict["_ts"] = datetime.now().isoformat(timespec="seconds")
    verdict["_run_id"] = run_id

    _persist(run_dir=run_dir, verdict=verdict)

    # Append to global key_decisions memory note so the digest survives
    # beyond this run.
    if memory_notes is not None:
        try:
            memory_notes.write_note(
                section="key_decision",
                title=f"teacher_review @ iter {iteration} ({trigger})",
                body=format_review_block(verdict),
                run_id=run_id,
                iteration=iteration,
            )
        except Exception:  # noqa: BLE001
            pass

    return verdict


def format_review_block(verdict: dict[str, Any]) -> str:
    """Render a verdict to a compact Chinese block for round_header injection."""
    obs = str(verdict.get("observation", "")).strip()
    exhausted = verdict.get("exhausted_directions") or []
    candidates = verdict.get("next_candidates") or []

    lines = [
        f"[Teacher Review] trigger={verdict.get('_trigger', '?')} "
        f"@ iter={verdict.get('_iteration', '?')}"
    ]
    if obs:
        lines.append(f"观察: {obs}")
    if exhausted:
        ex_txt = "; ".join(str(x) for x in exhausted[:3])
        lines.append(f"已饱和方向: {ex_txt}")
    if candidates:
        lines.append("建议候选方向:")
        for c in candidates[:3]:
            if isinstance(c, dict):
                mech = str(c.get("mechanism", "")).strip()
                rat = str(c.get("rationale", "")).strip()
                buckets = c.get("target_buckets") or []
                bkt = ",".join(str(b) for b in buckets) if buckets else "?"
                lines.append(f"  - [{mech}] → 桶({bkt}): {rat}")
            else:
                lines.append(f"  - {c}")
    return "\n".join(lines)


# ---------- internal helpers ---------------------------------------------


_SYSTEM_PROMPT = """你是一个独立的策略复盘 AI。你**不**修改代码，只读迭代记录、
当前最优 solver 代码和 teacher/MTASA_BOTTLENECK_OPTIMIZATION_GUIDE_CN.md，
生成下一轮开头要插入的明确建议。建议必须能直接指导下一轮 first intent / edit_plan，
风格接近 teacher/teacher_v008_790_plan.md，但更短、更聚焦。
然后用严格 JSON 回答：

{
  "observation": "<≤120字的总体观察：陷入什么模式 / 进展卡在哪>",
  "exhausted_directions": ["<已经反复尝试且证伪的方向 1-3 条，每条 ≤60 字>"],
  "next_candidates": [
    {
      "mechanism": "<sort_anchor|backup_aug|scarce_coverage|classify_threshold|scoring_refine|combo_activation|chain_reopt|other>",
      "rationale": "<下一轮应该怎么做、为什么有希望，≤120 字>",
      "target_buckets": ["<bucket name 1>", "..."]
    }
    /* 总共 2-3 条 */
  ]
}

只输出 JSON，不要任何解释文字、Markdown 围栏、或前后空行。mechanism 必须从给定 8 类里选一个。
"""


def _last_reviewed_iter(run_dir: Path) -> int:
    log_path = run_dir / _REVIEW_LOG_NAME
    if not log_path.is_file():
        return 0
    last = 0
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                it = int(obj.get("_iteration", 0))
                if it > last:
                    last = it
    except OSError:
        return 0
    return last


def _persist(*, run_dir: Path, verdict: dict[str, Any]) -> None:
    log_path = run_dir / _REVIEW_LOG_NAME
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(verdict, ensure_ascii=False) + "\n")
    except OSError:
        pass


_DEBUG_LOG_NAME = "teacher_review_debug.log"


def _append_debug(run_dir: Path, iteration: int, trigger: str, status: str, detail: str) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        line = f"[{ts}] iter={iteration} trigger={trigger} status={status} detail={detail!r}\n"
        with (run_dir / _DEBUG_LOG_NAME).open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _parse_verdict(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    # Strip ```json fences if model added them.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Locate the outermost JSON object.
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


# ---------- data assembly ------------------------------------------------


def _build_review_input(
    *,
    run_dir: Path,
    iteration: int,
    run_id: str,
    version_index: Any,
    window: int,
) -> str:
    """Assemble enhanced review input. Layout:

        # First 5 Round Overview           ← early basic situation
        # First 2 Round Details            ← detailed early plans and edits
        # Round Stream (last N)            ← per-round id/score/outcome/hypo/analysis
        # Current Best Solver              ← current best code.py snapshot
        # Bottleneck Optimization Guide    ← teacher guide excerpt
        # Block Patch Summary              ← (a) line ranges + keywords
        # Bucket Δ Matrix                  ← (b) N × 10 deltas vs incumbent
        # Smoke vs Genius Divergence       ← (c) per-round
        # Agent Behavior Counters          ← (d) memory_search / retrieve / ...
    """
    entries = _recent_entries(version_index, run_id, iteration, window)
    all_entries = _run_entries(version_index, run_id, iteration)

    overview_lines = ["# First 5 Round Overview"]
    first_five = all_entries[:5]
    if first_five:
        for e in first_five:
            score = e.get("score")
            score_txt = f"{float(score):.2f}" if isinstance(score, (int, float)) else "?"
            overview_lines.append(
                f"- iter={e.get('iteration')} v{e.get('v', '?')} score={score_txt} "
                f"outcome={e.get('outcome', '?')} hypothesis={str(e.get('plan_headline', ''))[:160]}"
            )
    else:
        overview_lines.append("(no scored early entries yet)")

    detail_lines = ["", "# First 2 Round Details"]
    first_two = all_entries[:2]
    if first_two:
        for e in first_two:
            it = int(e.get("iteration", 0))
            plan = _load_plan(run_dir, it) or {}
            detail_lines.append(f"## iter={it} v{e.get('v', '?')} outcome={e.get('outcome', '?')}")
            detail_lines.append(f"hypothesis: {str(plan.get('hypothesis', e.get('plan_headline', '')))[:400]}")
            detail_lines.append(f"analysis: {str(plan.get('analysis', ''))[:900]}")
            edit_plan = plan.get("edit_plan") or []
            if edit_plan:
                detail_lines.append("edit_plan: " + "; ".join(str(x) for x in edit_plan)[:700])
            detail_lines.append("patch: " + _block_patch_summary(run_dir, it))
    else:
        detail_lines.append("(no first-round details yet)")

    # --- Section 1: Round stream
    stream_lines = ["", "# Recent Round Stream (most recent first)"]
    for e in entries[::-1]:
        plan = _load_plan(run_dir, int(e.get("iteration", 0)))
        hyp = str(plan.get("hypothesis", ""))[:120] if plan else ""
        analysis = str(plan.get("analysis", ""))[:400] if plan else ""
        target_buckets = ",".join(str(x) for x in (plan.get("target_buckets") or [])) if plan else ""
        edit_plan = "; ".join(str(x) for x in (plan.get("edit_plan") or []))[:300] if plan else ""
        score = e.get("score")
        score_txt = f"{float(score):.2f}" if isinstance(score, (int, float)) else "?"
        stream_lines.append(
            f"- v{e.get('v', '?')} iter={e.get('iteration')} score={score_txt} "
            f"outcome={e.get('outcome', '?')} target=[{target_buckets}]"
        )
        if hyp:
            stream_lines.append(f"    hypothesis: {hyp}")
        if analysis:
            stream_lines.append(f"    analysis: {analysis}")
        if edit_plan:
            stream_lines.append(f"    edit_plan: {edit_plan}")

    best_lines = ["", "# Current Best Solver Code"]
    best_snapshot = _best_solver_snapshot(version_index, run_id, iteration, run_dir)
    best_lines.extend(best_snapshot)

    guide_lines = ["", "# Bottleneck Optimization Guide (required reference)"]
    guide_lines.append(_read_repo_text(_GUIDE_REL_PATH, limit=18000))

    # --- Section 2: block_patch summary
    patch_lines = ["", "# Block Patch Summary"]
    for e in entries[::-1]:
        it = int(e.get("iteration", 0))
        summary = _block_patch_summary(run_dir, it)
        patch_lines.append(f"- iter={it}: {summary}")

    # --- Section 3: bucket Δ matrix
    delta_lines = ["", "# Bucket Δ Matrix (vs incumbent before this round)"]
    bucket_names = _collect_bucket_names(entries)
    if bucket_names:
        header = "iter | " + " | ".join(b[:8] for b in bucket_names)
        delta_lines.append(header)
        prev_bucket: dict[str, float] = {}
        # Walk oldest → newest so each delta uses the running incumbent.
        for e in entries:
            cur = e.get("bucket_scores") or {}
            row = [str(e.get("iteration", "?")).rjust(4)]
            for b in bucket_names:
                cur_v = cur.get(b)
                prev_v = prev_bucket.get(b)
                if isinstance(cur_v, (int, float)) and isinstance(prev_v, (int, float)):
                    row.append(f"{cur_v - prev_v:+.1f}")
                elif isinstance(cur_v, (int, float)):
                    row.append(f"{cur_v:.0f}*")
                else:
                    row.append("--")
            # Update running incumbent only with this round's scores
            for b, v in cur.items():
                if isinstance(v, (int, float)):
                    prev_bucket[b] = float(v)
            delta_lines.append(" | ".join(row))
    else:
        delta_lines.append("(no bucket_scores in version_index entries)")

    # --- Section 4: smoke vs Genius divergence
    smoke_lines = ["", "# Smoke (local_preview) vs Genius (Δavg)"]
    smoke_entries = read_smoke_log(run_dir)
    by_iter: dict[int, dict[str, Any]] = {}
    for s in smoke_entries:
        it = int(s.get("iteration", 0))
        # keep the last smoke per iter (most representative final-call)
        by_iter[it] = s
    for e in entries:
        it = int(e.get("iteration", 0))
        s = by_iter.get(it)
        genius_avg = e.get("score")
        if s and isinstance(s.get("preview_avg"), (int, float)) and isinstance(genius_avg, (int, float)):
            div = float(genius_avg) - float(s["preview_avg"])
            smoke_lines.append(
                f"- iter={it} label={s.get('preview_label', '?')} "
                f"preview_avg={float(s['preview_avg']):.2f} genius_avg={float(genius_avg):.2f} "
                f"Δ(genius-preview)={div:+.2f}"
            )
        elif s:
            smoke_lines.append(f"- iter={it} smoke ok={s.get('ok')} (no preview_avg)")
        else:
            smoke_lines.append(f"- iter={it} (no smoke entry)")

    # --- Section 5: agent behavior counters
    counter_lines = ["", "# Agent Behavior Counters (last N rounds)"]
    counters = _agent_behavior_counters(run_dir, entries)
    if counters:
        for name, total in sorted(counters.items()):
            counter_lines.append(f"- {name}: {total}")
    else:
        counter_lines.append("(no harness transcripts available)")

    return "\n".join(
        overview_lines
        + detail_lines
        + stream_lines
        + best_lines
        + guide_lines
        + patch_lines
        + delta_lines
        + smoke_lines
        + counter_lines
    )


def _recent_entries(version_index: Any, run_id: str, iteration: int, window: int) -> list[dict]:
    return _run_entries(version_index, run_id, iteration)[-window:]


def _run_entries(version_index: Any, run_id: str, iteration: int) -> list[dict]:
    if version_index is None:
        return []
    try:
        run_entries = list(version_index.for_run(run_id))
    except Exception:  # noqa: BLE001
        return []
    run_entries = [
        e for e in run_entries
        if isinstance(e.get("iteration"), int) and 0 < int(e["iteration"]) <= iteration
    ]
    run_entries.sort(key=lambda e: int(e["iteration"]))
    return run_entries


def _best_solver_snapshot(
    version_index: Any,
    run_id: str,
    iteration: int,
    run_dir: Path,
    *,
    limit: int = 32000,
) -> list[str]:
    candidates: list[dict[str, Any]] = []
    if version_index is not None:
        try:
            candidates.extend(list(version_index.best(5)))
        except Exception:  # noqa: BLE001
            pass
        try:
            run_entries = _run_entries(version_index, run_id, iteration)
            scored = [e for e in run_entries if e.get("score") is not None and e.get("solver_path")]
            scored.sort(key=lambda e: float(e.get("score", 0.0)))
            candidates.extend(scored[:3])
        except Exception:  # noqa: BLE001
            pass
    fallback = run_dir.parent / "solvers" / "best_solver.py"
    if fallback.is_file():
        candidates.append({"solver_path": str(fallback), "score": "durable_best"})

    seen: set[str] = set()
    for entry in candidates:
        path_text = str(entry.get("solver_path", "") or "")
        if not path_text or path_text in seen:
            continue
        seen.add(path_text)
        path = Path(path_text)
        if not path.is_file():
            continue
        code = _read_file(path, limit=limit)
        return [
            f"path: {path}",
            f"score: {entry.get('score', '?')} v{entry.get('v', '?')} run={entry.get('run_id', '?')} iter={entry.get('iteration', '?')}",
            "```python",
            code,
            "```",
        ]
    return ["(no current best solver file found)"]


def _read_repo_text(rel_path: Path, *, limit: int) -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return _read_file(repo_root / rel_path, limit=limit)


def _read_file(path: Path, *, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(unavailable: {path}: {exc})"
    if len(text) > limit:
        return text[:limit] + f"\n... (truncated at {limit} chars)"
    return text


def _load_plan(run_dir: Path, iteration: int) -> dict | None:
    p = run_dir / f"harness_v{iteration:03d}.json"
    if not p.is_file():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return ((obj or {}).get("final") or {}).get("plan") or {}


def _block_patch_summary(run_dir: Path, iteration: int) -> str:
    """Summarize this round's block_patch tool calls. Returns one short line
    with line-range hints + the most distinctive keywords from the patches.
    """
    p = run_dir / f"harness_v{iteration:03d}.json"
    if not p.is_file():
        return "(no harness transcript)"
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "(unparsable harness)"
    turns = (obj or {}).get("transcript") or []
    patches: list[dict] = []
    for t in turns:
        if not isinstance(t, dict):
            continue
        if t.get("role") != "tool":
            continue
        if str(t.get("name", "")) != "block_patch":
            continue
        args = t.get("args") or {}
        patches.append(args)
    if not patches:
        return "no block_patch this round"
    keywords: set[str] = set()
    bodies: list[str] = []
    for a in patches:
        body = str(a.get("blocks", "") or a.get("body", "") or "")
        bodies.append(body)
        for tok in re.findall(r"[A-Za-z_][A-Za-z_0-9]{4,}", body):
            keywords.add(tok)
            if len(keywords) > 25:
                break
        if len(keywords) > 25:
            break
    kw_top = ", ".join(sorted(keywords)[:8]) if keywords else "(none)"
    return f"{len(patches)} block_patch call(s); keywords: {kw_top}"


def _collect_bucket_names(entries: list[dict]) -> list[str]:
    seen: set[str] = set()
    for e in entries:
        bs = e.get("bucket_scores") or {}
        for k in bs:
            seen.add(str(k))
    return sorted(seen)


def _agent_behavior_counters(run_dir: Path, entries: list[dict]) -> dict[str, int]:
    interesting = {
        "memory_search",
        "retrieve_guidance",
        "list_strategy_templates",
        "read_strategy_template",
        "memory_write",
        "block_patch",
        "smoke_test_solver",
    }
    counts: dict[str, int] = {n: 0 for n in interesting}
    for e in entries:
        it = int(e.get("iteration", 0))
        p = run_dir / f"harness_v{it:03d}.json"
        if not p.is_file():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for t in (obj or {}).get("transcript") or []:
            if not isinstance(t, dict):
                continue
            if t.get("role") != "tool":
                continue
            name = str(t.get("name", ""))
            if name in counts:
                counts[name] += 1
    return counts
