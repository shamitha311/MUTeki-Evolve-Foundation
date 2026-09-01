from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from fool.harness.model_client import ModelClient
from fool.harness.parser import parse_model_output
from fool.memory_notes import MAX_BODY_BYTES, MAX_TITLE_LEN, SECTION_FILES, MemoryNotesStore

logger = logging.getLogger(__name__)

_REPORT_HEAD_LINES = 30
# 700 was too tight in production: ~40% of rounds emitted Chinese bodies that
# got cut mid-args-JSON, dropping the capture. 1800 fits every healthy
# reflection observed without much overhead.
_DEFAULT_MAX_NEW_TOKENS = 1800
_PRIOR_NOTES_MAX = 3
_PRIOR_SNIPPET_CHARS = 220

_VALID_SECTIONS = set(SECTION_FILES.keys())

# Closing </skip> optional: production runs showed the model occasionally
# truncating after a period inside the skip body. The opening tag + non-empty
# body is enough signal that the model intended to skip.
_SKIP_RE = re.compile(
    r"<skip>(.*?)(?:</skip>|\Z)", re.DOTALL | re.IGNORECASE
)
_UPDATE_RE = re.compile(
    r'<update\s+path="([^"]+)"\s+line=(\d+)\s*>(.*?)</update>',
    re.DOTALL | re.IGNORECASE,
)
_MAX_EVIDENCE_CHARS = 500


_SYSTEM_PROMPT = """你是 Fool 迭代循环的"事后复盘记忆员"。本轮已经评分完毕，outcome 已知。
你的任务：根据本轮的 plan、outcome、Genius 桶级 Δ、以及与本轮假设相近的历史 notes，
emit 一个 **write / update / skip**，**三选一**，不要任何其它文本。

============= 内部思考顺序（必走，输出前自检）=============

在 emit 之前，按以下顺序在心里走一遍，**任何一步通不过就直接 SKIP**：

(i) **先读 Bucket-level Δ 表**：把"哪些桶变好 / 哪些变差 / 哪些不变"列清楚。
    - 没有任何桶 |Δ| ≥ 1.0 → 这是噪声轮，**必须 SKIP**（不写 try_error，也不写 lesson）。
(ii) **判机制级 vs 作用域级失败**：
    - 若 regressed 的桶**就是本轮 target_buckets**（机制在它想改的地方就失败了）→ 这是机制级失败，可写 try_error。
    - 若 regressed 的桶是 target_buckets **以外的桶**（机制在目标桶上没机会，被非目标桶拖垮）→ 这是**作用域错配**，**必须 SKIP**。把作用域改对再试，不能给机制本身判死刑。
    - 若 regressed 来自"参数没调好 / 阈值越界 / 与现有路径耦合"等可修复因素 → **必须 SKIP**。
(iii) **判是否已经被历史 notes 覆盖**：相近 notes 里已有同机制条目 → 走 UPDATE 或 SKIP，绝不重复 WRITE。

============= 三路决策 =============

(1) **WRITE 新 lesson/try_error**：通过上面三步且没有相近条目时才能写。
    - section="lesson"：outcome=improved/baseline，或 neutral 但确认了一个新正向机制
    - section="try_error"：**机制级**失败（target_buckets 内部退化），且失败原因不能归因于作用域/参数/耦合
    - 仅在做出"影响后续多轮的明确架构选择"时（罕见）用 section="key_decision"

(2) **UPDATE 现有条目**：本轮机制和某一条历史 notes **核心一致，但新增证据**
    （例如：同公式 + 新桶覆盖到、同方向 + 更大 delta、同方向 + 新反例边界）。
    - 不要为"同机制重复验证"专门 write 一条新 lesson —— 那只是噪音
    - 用 `<update path="..." line=N>一行新证据</update>`，证据 ≤500 字符，
      内含量化数字 + 简短机制说明
    - path 必须**严格等于**"Similar prior notes"块里出现过的某个 path
    - line 必须等于该条目展示的起始行号（即 `[<path>:<start>-<end>]` 里的 start）

(3) **SKIP**（默认且最常见的出口）：
    - 没有桶 |Δ| ≥ 1.0（噪声轮）
    - 作用域错配 / 参数没调好 / 耦合导致的失败
    - outcome=neutral 且 plan 里没有可量化的机制变化
    - 纯回滚 / 提交 incumbent / submit baseline
    - 本轮机制和历史 notes 完全重复（连证据都没有新增）

**默认就该 SKIP。WRITE 是少数情况。重复写或泛化写比不写糟得多。**

============= WRITE 的硬约束（违反任意一条→自动拒绝）=============

**title**（≤ 80 字符）：
- 必须含**作用域限定词**（如"在 scarce 桶 / 在全局排序 / 在 backup 阶段"），不允许"X 不可行"这种泛化结论。
- 反例（拒绝）："候选骑手数升序不可行"
- 正例：    "全局排序键引入候选数权重时非稀缺桶遭受惩罚"

**body**（≤ 4KB）必须包含以下结构化字段，缺一拒绝。**字段名不要加 `#` 前缀**
（避免和 markdown 标题混淆，否则会破坏检索）：

```
# === 事实（直接来自本轮报告，不允许加解释）===
scope: <作用域，比如：全局主派排序 / 仅 scarce 桶 / backup 阶段 / 排序键二级>
bucket_delta: <逐桶 Δ，至少包含 target 桶与最大反例桶；只允许写数值与方向，不写"因为/所以">

# === 判断（你的推断，可能是错的；后续轮要敢于推翻）===
falsifies: [推断] <被本轮证伪的"具体作用域下的机制"——不是泛化结论>
mechanism: [推断] <一句话讲为什么会失败/成功>
confidence: <low|medium|high>  ← 你对上面两条推断的把握
```

**事实 vs 判断的硬规则**（违反就拒绝）：
1. `falsifies:` 和 `mechanism:` 的内容**必须**以 `[推断]` 开头，提醒未来的读者这是解读、不是定论。
2. 不允许把推断混进 `scope:` / `bucket_delta:`。后两条必须是可以从 Genius 报告直接核对的客观条目。
3. `confidence:` 必须是 `low` / `medium` / `high` 三者之一；如果你只看到 1 次现象、或退化幅度接近噪声、或没做对照实验，必须写 `low`。`high` 仅在跨多个桶、多轮稳定复现时使用。
4. 正文里如果还有进一步分析，请用 `事实：…` / `推断：…` 段落分行，不要混写。

其余文字（量化数字、复盘思路）放在结构化字段后面正文里。

UPDATE：
- 单行证据 ≤500 字符，内含量化数字（哪个桶、delta 多少）
- 不要复述旧 lesson 的机制，只写**新增的那一点**
- run_id 和 iteration 由 harness 自动写入，不需要手动加

============= 合法输出格式（三选一，emit 一次）=============

  <tool name="memory_write"><args>{"section":"try_error","title":"…作用域 + 机制…","body":"scope: …\\nbucket_delta: …\\nfalsifies: [推断] …\\nmechanism: [推断] …\\nconfidence: low\\n\\n正文"}</args></tool>

  <update path="/abs/path/to/notes/lessons.md" line=15>scarce 桶也确认 -8（n=39/40）：w³ 在低意愿池也生效。</update>

  <skip>scope_mismatch：本轮 target=scarce 但退化集中在 large/low_w，作用域错配，不写</skip>

不要 <intent>、不要 markdown 代码块包裹、不要解释、不要前后加文字。
"""


_NOISE_DELTA_THRESHOLD = 1.0  # |Δ| < this on every bucket → noise round → must SKIP


def _format_bucket_delta_table(
    bucket_deltas: list[dict] | None,
    target_buckets: list[str],
) -> str:
    """Render a Markdown-ish table of per-bucket Δ. Returns "" if no data.

    Each row in bucket_deltas is expected to contain {bucket, prev, cur, delta}.
    Target buckets are marked with [T]; entries with |Δ|≥threshold get an arrow.
    """
    if not bucket_deltas:
        return ""
    tgt = {t.strip() for t in target_buckets if t and isinstance(t, str)}
    rows: list[str] = []
    max_abs = 0.0
    target_bad: list[tuple[str, float]] = []
    nontarget_bad: list[tuple[str, float]] = []
    for d in bucket_deltas:
        name = str(d.get("bucket", ""))
        if not name:
            continue
        prev = d.get("prev")
        cur = d.get("cur")
        delta = d.get("delta")
        if delta is None:
            continue
        try:
            delta = float(delta)
        except (TypeError, ValueError):
            continue
        max_abs = max(max_abs, abs(delta))
        arrow = " ·" if abs(delta) < _NOISE_DELTA_THRESHOLD else (" ↑(worse)" if delta > 0 else " ↓(better)")
        flag = " [T]" if name in tgt else ""
        prev_s = f"{float(prev):.2f}" if prev is not None else "?"
        cur_s = f"{float(cur):.2f}" if cur is not None else "?"
        rows.append(f"  - {name}{flag}: {prev_s} → {cur_s}  Δ={delta:+.2f}{arrow}")
        # Track regressions to surface scope mismatch
        if delta >= _NOISE_DELTA_THRESHOLD:
            if name in tgt:
                target_bad.append((name, delta))
            else:
                nontarget_bad.append((name, delta))
    if not rows:
        return ""
    header_bits = [
        f"## Bucket-level Δ (cur vs prev incumbent; threshold |Δ|≥{_NOISE_DELTA_THRESHOLD})",
        f"- max |Δ| = {max_abs:.2f}"
        + ("  → noise round (建议 SKIP)" if max_abs < _NOISE_DELTA_THRESHOLD else ""),
    ]
    if target_bad and not nontarget_bad:
        header_bits.append("- 退化集中在 target_buckets 内 → 候选 try_error 写入（机制级失败）")
    elif nontarget_bad and not target_bad:
        header_bits.append(
            "- 退化集中在 target_buckets **以外** → scope_mismatch，按 SKIP 处理"
        )
    elif target_bad and nontarget_bad:
        header_bits.append(
            "- target 与非 target 都退化 → 大概率作用域错配，写 try_error 前需说明"
            "为什么 target 桶失败不是作用域问题"
        )
    return "\n".join(header_bits + ["```"] + rows + ["```"])


def _build_user_prompt(
    *,
    iteration: int,
    hypothesis: str,
    analysis: str,
    target_buckets: list[str],
    edit_plan: list[str],
    outcome: str,
    score: float | None,
    prev_best: float | None,
    score_delta: float | None,
    report_head: str,
    prev_report_head: str | None,
    prior_notes: list[dict],
    bucket_deltas: list[dict] | None = None,
) -> str:
    parts = [f"## Round {iteration} outcome"]
    parts.append(f"- outcome: {outcome}")
    if score is not None:
        parts.append(f"- score: {score}")
    if prev_best is not None:
        parts.append(f"- prev_best: {prev_best}")
    if score_delta is not None:
        parts.append(f"- score_delta: {score_delta:+.4f}")
    parts.append("")
    parts.append("## Round plan")
    parts.append(f"- hypothesis: {hypothesis}")
    if analysis:
        parts.append(f"- analysis: {analysis}")
    if target_buckets:
        parts.append(f"- target_buckets: {', '.join(target_buckets)}")
    if edit_plan:
        parts.append("- edit_plan:")
        for step in edit_plan:
            parts.append(f"  - {step}")
    parts.append("")
    delta_block = _format_bucket_delta_table(bucket_deltas, target_buckets)
    if delta_block:
        parts.append(delta_block)
        parts.append("")
    parts.append("## Genius report head (this round)")
    parts.append("```")
    parts.append(report_head.strip())
    parts.append("```")
    if prev_report_head:
        parts.append("")
        parts.append("## Prev best report head (raw, for reference)")
        parts.append("```")
        parts.append(prev_report_head.strip())
        parts.append("```")
    parts.append("")
    parts.append("## Similar prior notes (BM25 top hits — check before writing)")
    if prior_notes:
        for n in prior_notes:
            path = n.get("path", "")
            sl = n.get("start_line", "?")
            el = n.get("end_line", "?")
            snippet = (n.get("snippet") or "").strip().replace("\n", " ")[:_PRIOR_SNIPPET_CHARS]
            parts.append(f"- [{path}:{sl}-{el}] {snippet}")
    else:
        parts.append("- (none)")
    parts.append("")
    parts.append("现在决定：emit memory_write 还是 <skip>。")
    return "\n".join(parts)


def _read_report_head(report_path: Path | None) -> str:
    if report_path is None:
        return "(report unavailable)"
    try:
        text = report_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(report unreadable)"
    lines = text.splitlines()[:_REPORT_HEAD_LINES]
    return "\n".join(lines)


def _search_similar(
    memory_notes: MemoryNotesStore, hypothesis: str, analysis: str
) -> list[dict]:
    query = f"{hypothesis} {analysis}".strip()
    if not query:
        return []
    try:
        return memory_notes.search(
            query, sections=["lesson", "try_error"], max_results=_PRIOR_NOTES_MAX
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("outcome_reflector: prior-note search failed: %s", exc)
        return []


def _extract_skip(raw: str) -> str | None:
    m = _SKIP_RE.search(raw)
    if not m:
        return None
    return m.group(1).strip()[:200] or "(no reason given)"


def _extract_update(raw: str) -> tuple[str, int, str] | None:
    """Return (path, line, evidence) if the response is an <update> tag."""
    m = _UPDATE_RE.search(raw)
    if not m:
        return None
    path = m.group(1).strip()
    try:
        line = int(m.group(2))
    except ValueError:
        return None
    evidence = m.group(3).strip()
    if not evidence:
        return None
    if len(evidence) > _MAX_EVIDENCE_CHARS:
        evidence = evidence[:_MAX_EVIDENCE_CHARS]
    return path, line, evidence


def _is_known_anchor(
    path: str, line: int, prior_notes: list[dict]
) -> bool:
    """The model can only update entries that were shown in the prompt — this
    blocks arbitrary-file writes and hallucinated line numbers."""
    for n in prior_notes:
        if str(n.get("path")) == path and int(n.get("start_line", -1)) == line:
            return True
    return False


_REQUIRED_BODY_FIELDS_FOR_LEARNING = (
    "scope:", "falsifies:", "bucket_delta:", "mechanism:", "confidence:",
)
_JUDGMENT_FIELDS = ("falsifies:", "mechanism:")  # must carry [推断] tag
_FACT_FIELDS = ("scope:", "bucket_delta:")  # must NOT contain [推断]
_VALID_CONFIDENCES = ("low", "medium", "high")
_BANNED_TITLE_PHRASES = ("不可行", "无效", "全面无效", "完全没用")


def _line_after(body: str, field: str) -> str:
    """Return the inline content after a `field:` marker (rest of that line)."""
    import re as _re
    m = _re.search(rf"^{_re.escape(field)}\s*(.*)$", body, _re.MULTILINE)
    return (m.group(1).strip() if m else "")


def _coerce_args(args: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(args, dict):
        return None, "args not a dict"
    section = args.get("section")
    title = args.get("title")
    body = args.get("body")
    if not (isinstance(section, str) and isinstance(title, str) and isinstance(body, str)):
        return None, "section/title/body must all be strings"
    if section not in _VALID_SECTIONS:
        return None, f"invalid section: {section!r}"
    if not title.strip() or not body.strip():
        return None, "title or body is empty"
    if len(title) > MAX_TITLE_LEN:
        title = title[:MAX_TITLE_LEN]
    # B: title must have a scope qualifier and must not be a sweeping verdict
    if section in {"lesson", "try_error"}:
        low = title.strip()
        for phrase in _BANNED_TITLE_PHRASES:
            if low.endswith(phrase) or low == phrase:
                return None, (
                    f"title ends with sweeping verdict {phrase!r}; "
                    "add scope qualifier (e.g. '在 X 阶段/在 Y 桶上')"
                )
        # B: body must declare scope/falsifies/bucket_delta/mechanism/confidence
        missing = [f for f in _REQUIRED_BODY_FIELDS_FOR_LEARNING if f not in body]
        if missing:
            return None, f"body missing required fields: {missing}"
        # B+: separate facts from judgments. Judgments must be tagged [推断];
        # facts must not be (catches the model burying a hypothesis inside
        # what should be a directly-observable line).
        for field in _JUDGMENT_FIELDS:
            line = _line_after(body, field)
            if not line.startswith("[推断]"):
                return None, (
                    f"field '{field}' must start with '[推断]' so future readers "
                    "know it is interpretation, not observation"
                )
        for field in _FACT_FIELDS:
            line = _line_after(body, field)
            if "[推断]" in line:
                return None, (
                    f"field '{field}' is a fact field — remove the '[推断]' tag and "
                    "move any interpretation into 'mechanism:' / 'falsifies:'"
                )
        conf_line = _line_after(body, "confidence:").lower().split()[:1]
        conf = conf_line[0] if conf_line else ""
        if conf not in _VALID_CONFIDENCES:
            return None, (
                f"confidence must be one of {_VALID_CONFIDENCES}; got {conf!r}"
            )
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        b = body.encode("utf-8")[: MAX_BODY_BYTES - 16]
        body = b.decode("utf-8", errors="ignore") + "\n[truncated]"
    return {
        "section": section,
        "title": title.strip(),
        "body": body,
    }, ""


def reflect_and_write(
    *,
    model: ModelClient,
    memory_notes: MemoryNotesStore | None,
    plan: dict[str, Any],
    outcome: str,
    score: float | None,
    prev_best: float | None,
    score_delta: float | None,
    report_path: Path | None,
    prev_best_report_path: Path | None = None,
    bucket_deltas: list[dict] | None = None,
    run_id: str,
    iteration: int,
    dataset_fp: str,
    log_path: Path | None = None,
    max_tokens: int = _DEFAULT_MAX_NEW_TOKENS,
) -> dict[str, Any]:
    """Post-outcome reflection: one LLM sub-call dedicated to writing memory.

    Outcomes:
      - ok=True, action="written": memory_write succeeded
      - ok=True, action="skipped": model emitted <skip> (no-write is a valid outcome)
      - ok=False: parse / write failure or unexpected response
    """
    result: dict[str, Any] = {
        "ok": False,
        "action": None,
        "reason": "",
        "section": None,
        "title": None,
        "path": None,
        "inserted_line": None,
        "raw_response": "",
        "prior_notes_seen": 0,
    }
    if memory_notes is None:
        result["reason"] = "memory_notes not configured"
        return result
    if outcome == "harness_failed":
        result["reason"] = "skipped: harness_failed"
        result["action"] = "skipped"
        result["ok"] = True
        return result

    hypothesis = str(plan.get("hypothesis", "")).strip()
    analysis = str(plan.get("analysis", "")).strip()
    target_buckets = list(plan.get("target_buckets", []) or [])
    edit_plan = list(plan.get("edit_plan", []) or [])
    report_head = _read_report_head(report_path)
    prev_report_head: str | None = None
    if prev_best_report_path is not None:
        prev_report_head = _read_report_head(prev_best_report_path)

    prior_notes = _search_similar(memory_notes, hypothesis, analysis)
    result["prior_notes_seen"] = len(prior_notes)

    user_prompt = _build_user_prompt(
        iteration=iteration,
        hypothesis=hypothesis,
        analysis=analysis,
        target_buckets=target_buckets,
        edit_plan=edit_plan,
        outcome=outcome,
        score=score,
        prev_best=prev_best,
        score_delta=score_delta,
        report_head=report_head,
        prev_report_head=prev_report_head,
        prior_notes=prior_notes,
        bucket_deltas=bucket_deltas,
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        raw = model.complete(messages, max_tokens)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"model.complete failed: {exc}"
        _write_log(log_path, messages, "", result)
        return result
    result["raw_response"] = raw

    # Check for <skip> first — it's a legitimate "no write needed" outcome.
    skip_reason = _extract_skip(raw)
    if skip_reason is not None:
        result["ok"] = True
        result["action"] = "skipped"
        result["reason"] = f"model skipped: {skip_reason}"
        _write_log(log_path, messages, raw, result)
        return result

    # Check for <update> — append-only evidence to an existing entry.
    update_tuple = _extract_update(raw)
    if update_tuple is not None:
        upd_path, upd_line, evidence = update_tuple
        if not _is_known_anchor(upd_path, upd_line, prior_notes):
            result["reason"] = (
                f"update rejected: path/line {upd_path}:{upd_line} was not in "
                "Similar prior notes — model may only update entries shown in the prompt"
            )
            _write_log(log_path, messages, raw, result)
            return result
        try:
            inserted_line = memory_notes.append_evidence(
                path=Path(upd_path),
                anchor_line=upd_line,
                evidence=evidence,
                run_id=run_id,
                iteration=iteration,
            )
        except Exception as exc:  # noqa: BLE001
            result["reason"] = f"append_evidence failed: {exc}"
            _write_log(log_path, messages, raw, result)
            return result
        result["ok"] = True
        result["action"] = "updated"
        result["reason"] = f"appended evidence to {upd_path}:{upd_line}"
        result["path"] = upd_path
        result["inserted_line"] = inserted_line
        _write_log(log_path, messages, raw, result)
        return result

    kind, payload = parse_model_output(raw)
    if kind != "tool" or not isinstance(payload, dict) or payload.get("name") != "memory_write":
        result["reason"] = f"unexpected response kind={kind!r}"
        _write_log(log_path, messages, raw, result)
        return result

    args, coerce_reason = _coerce_args(payload.get("args"))
    if args is None:
        result["reason"] = f"args rejected: {coerce_reason}"
        _write_log(log_path, messages, raw, result)
        return result

    section = args["section"]

    # try_error entries start at confidence=0.7 so they shape future
    # exploration without acting as hard bans; lessons stay at 1.0.
    initial_confidence = 0.7 if section == "try_error" else 1.0
    try:
        path = memory_notes.write_note(
            section=section,
            title=args["title"],
            body=args["body"],
            run_id=run_id,
            iteration=iteration,
            dataset_fp=dataset_fp or None,
            confidence=initial_confidence,
        )
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"write_note failed: {exc}"
        _write_log(log_path, messages, raw, result)
        return result

    result["ok"] = True
    result["action"] = "written"
    result["reason"] = "written"
    result["section"] = section
    result["title"] = args["title"]
    result["path"] = str(path)
    _write_log(log_path, messages, raw, result)
    return result


def _write_log(
    log_path: Path | None,
    messages: list[dict[str, str]],
    raw: str,
    result: dict[str, Any],
) -> None:
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(
                {
                    "messages": messages,
                    "response": raw,
                    "result": result,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("outcome_reflector: failed to write log %s: %s", log_path, exc)
