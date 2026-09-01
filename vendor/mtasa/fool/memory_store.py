from __future__ import annotations

import json
import math
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fool.bucket_incumbents import BucketIncumbents


ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = ROOT / "out" / "memory"
RUNS_SUBDIR = "runs"
EPISODES_NAME = "episodes.jsonl"
PLANS_NAME = "plans.jsonl"
TOOL_TRACES_NAME = "tool_traces.jsonl"
RUN_LESSONS_NAME = "run_lessons.jsonl"
RUN_LESSONS_SUMMARY_NAME = "RUN_LESSONS.md"
INDEX_NAME = "strategy_index.json"
SUMMARY_NAME = "MEMORY.md"
SESSION_SUMMARIES_NAME = "session_summaries.jsonl"
BEST_SOLVER_NAME = "best_solver.py"
BEST_REPORT_NAME = "best_report.txt"
BEST_META_NAME = "best_meta.json"

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
_CASE_TAG_ALIASES = {
    "scarce": "scarce",
    "scarce_couriers": "scarce",
    "scarce_couriers_seed401": "scarce",
    "seed401": "scarce",
    "low_w": "low_willingness",
    "loww": "low_willingness",
    "low_willingness": "low_willingness",
    "low_willingness_seed501": "low_willingness",
    "seed501": "low_willingness",
    "small": "small",
    "small_seed100": "small",
    "seed100": "small",
    "medium": "medium",
    "medium_seed201": "medium",
    "medium_seed202": "medium",
    "medium_seed203": "medium",
    "seed201": "medium",
    "seed202": "medium",
    "seed203": "medium",
    "large": "large",
    "large_seed301": "large",
    "large_seed302": "large",
    "seed301": "large",
    "seed302": "large",
    "high_noise": "high_noise",
    "high_noise_seed601": "high_noise",
    "seed601": "high_noise",
    "tiny": "tiny",
    "tiny_seed42": "tiny",
    "seed42": "tiny",
}
_GUIDANCE_SOURCES = [
    ROOT / "teacher" / "DATA_STRATEGY_PLAYBOOK.md",
    ROOT / "teacher" / "MTASA_BOTTLENECK_OPTIMIZATION_GUIDE_CN.md",
    ROOT / "teacher" / "DATASET_FEATURE.md",
    ROOT / "skill.md",
]

_GUIDANCE_STEEL_STAMP = [
    "Guidance steel-stamp (hard rule, must obey):",
    "- Teacher guidance in bottleneck/plateau rounds must output forward optimization actions; do not use rollback/restore incumbent as the final strategy.",
    "- If rollback is mentioned, treat it only as temporary safety fallback; still propose at least one verifiable forward edit (mechanism + target bucket + smoke check).",
    "- Submitting incumbent-equivalent no-change code is forbidden as a bottleneck response.",
]


def _token_list(text: str) -> list[str]:
    return [
        token.lower()
        for token in _TOKEN_RE.findall(text)
        if len(token) > 1
    ]


def _tokens(text: str) -> set[str]:
    return set(_token_list(text))


def _normalize_case_tag(raw_tag: str) -> str:
    tag = str(raw_tag or "").strip().lower().replace("-", "_")
    if not tag:
        return ""
    direct = _CASE_TAG_ALIASES.get(tag)
    if direct:
        return direct
    if "scarce" in tag or "401" in tag:
        return "scarce"
    if "low_w" in tag or "loww" in tag or "501" in tag:
        return "low_willingness"
    if "high_noise" in tag or "601" in tag:
        return "high_noise"
    if "small" in tag or "100" in tag:
        return "small"
    if "medium" in tag or tag.endswith(("201", "202", "203")):
        return "medium"
    if "large" in tag or tag.endswith(("301", "302")):
        return "large"
    if "tiny" in tag or "42" in tag:
        return "tiny"
    return ""


def _collect_case_tags(values: list[str]) -> set[str]:
    tags: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        normalized = _normalize_case_tag(text)
        if normalized:
            tags.add(normalized)
        for part in re.split(r"[,;|/\s]+", text):
            normalized_part = _normalize_case_tag(part)
            if normalized_part:
                tags.add(normalized_part)
    return tags


def _build_bm25_stats(token_docs: list[list[str]]) -> tuple[dict[str, int], float]:
    doc_freq: dict[str, int] = defaultdict(int)
    total_len = 0
    for tokens in token_docs:
        total_len += len(tokens)
        for token in set(tokens):
            doc_freq[token] += 1
    avgdl = (total_len / len(token_docs)) if token_docs else 1.0
    return doc_freq, max(avgdl, 1.0)


def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    *,
    doc_freq: dict[str, int],
    total_docs: int,
    avgdl: float,
    k1: float = 1.2,
    b: float = 0.75,
) -> float:
    if not query_tokens or not doc_tokens or total_docs <= 0:
        return 0.0
    tf = Counter(doc_tokens)
    dl = max(len(doc_tokens), 1)
    total = 0.0
    for token in query_tokens:
        freq = tf.get(token, 0)
        df = doc_freq.get(token, 0)
        if freq <= 0 or df <= 0:
            continue
        idf = math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))
        denom = freq + k1 * (1.0 - b + b * dl / avgdl)
        total += idf * (freq * (k1 + 1.0)) / max(denom, 1e-9)
    return total


def _recent_gain_by_tag(episodes: list[dict[str, Any]], max_recent: int = 80) -> dict[str, float]:
    if not episodes:
        return {}
    tail = episodes[-max_recent:]
    total = len(tail)
    gains: dict[str, float] = defaultdict(float)
    for idx, item in enumerate(tail):
        recency = (idx + 1) / max(total, 1)
        outcome = str(item.get("outcome", ""))
        delta = float(item.get("score_delta", 0.0) or 0.0)
        if outcome == "improved":
            gain = min(max(-delta, 0.0), 800.0) / 120.0
        elif outcome in {"rollback", "regressed"}:
            gain = -min(max(delta, 0.0), 800.0) / 200.0
        else:
            gain = 0.0
        if gain == 0.0:
            continue
        tags = _collect_case_tags(
            [str(x) for x in item.get("target_buckets", [])]
            + [
                str(item.get("hypothesis", "")),
                str(item.get("reason", "")),
            ]
        )
        if not tags:
            tags = {"all"}
        for tag in tags:
            gains[tag] += gain * recency
    return dict(gains)


def _chunk_markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = "preamble"
    body_lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            body = "\n".join(body_lines).strip()
            if body:
                sections.append((heading, body))
            heading = line.lstrip("#").strip() or "untitled"
            body_lines = []
            continue
        body_lines.append(line)
    tail = "\n".join(body_lines).strip()
    if tail:
        sections.append((heading, tail))
    return sections


def _guidance_chunks() -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    for path in _GUIDANCE_SOURCES:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for heading, body in _chunk_markdown_sections(text):
            compact = " ".join(line.strip() for line in body.splitlines() if line.strip())
            if not compact:
                continue
            chunks.append(
                {
                    "source": str(path.relative_to(ROOT)),
                    "heading": heading,
                    "body": compact,
                }
            )
    return chunks


def retrieve_guidance_text(
    *,
    query_text: str,
    keywords: list[str] | None = None,
    case_tags: list[str] | None = None,
    episodes: list[dict[str, Any]] | None = None,
    limit: int = 6,
) -> str:
    chunks = _guidance_chunks()
    if not chunks:
        return "No teacher/skill guidance documents found."

    keyword_list = [str(x) for x in (keywords or []) if str(x).strip()]
    tag_values = [str(x) for x in (case_tags or []) if str(x).strip()]
    requested_tags = _collect_case_tags(tag_values + keyword_list + [query_text])
    query_tokens = _token_list(" ".join([query_text] + keyword_list + tag_values))
    token_docs = [
        _token_list(f"{item['heading']} {item['body']} {item['source']}") for item in chunks
    ]
    doc_freq, avgdl = _build_bm25_stats(token_docs)
    total_docs = len(token_docs)
    gains = _recent_gain_by_tag(episodes or [])

    ranked: list[tuple[float, int, dict[str, str], set[str], float, float]] = []
    for idx, item in enumerate(chunks):
        tokens = token_docs[idx]
        chunk_tags = _collect_case_tags([item["heading"], item["body"], item["source"]])
        overlap = len(requested_tags & chunk_tags)
        bm25 = _bm25_score(
            query_tokens,
            tokens,
            doc_freq=doc_freq,
            total_docs=total_docs,
            avgdl=avgdl,
        )
        recent_bonus = sum(max(gains.get(tag, 0.0), 0.0) for tag in (requested_tags & chunk_tags))
        score = bm25 * 1.8 + overlap * 2.3 + recent_bonus * 1.2
        ranked.append((score, idx, item, chunk_tags, bm25, recent_bonus))

    selected = sorted(ranked, key=lambda row: (row[0], row[1]), reverse=True)[: max(limit, 1)]
    lines = [
        "Teacher/skill guidance retrieved via BM25-hybrid search (keyword + case-tag + recent-gain ranking):",
        "- query={q}; keywords={k}; case_tags={t}".format(
            q=query_text or "(empty)",
            k=keyword_list or [],
            t=sorted(requested_tags) or [],
        ),
        "",
        *_GUIDANCE_STEEL_STAMP,
    ]
    for rank, (score, _idx, item, chunk_tags, bm25, recent_bonus) in enumerate(selected, start=1):
        snippet = item["body"][:260]
        if len(item["body"]) > 260:
            snippet += "..."
        lines.append(
            "[{rank}] source={source} section={section} score={score:.3f} bm25={bm25:.3f} recent_gain={recent:.3f} tags={tags}".format(
                rank=rank,
                source=item["source"],
                section=item["heading"][:80],
                score=score,
                bm25=bm25,
                recent=recent_bonus,
                tags=sorted(chunk_tags)[:6],
            )
        )
        lines.append(f"    snippet: {snippet}")
    return "\n".join(lines)


def _memory_key(hypothesis: str, target_buckets: list[str]) -> str:
    normalized = " ".join(sorted(_tokens(hypothesis)))
    buckets = ",".join(sorted(str(bucket) for bucket in target_buckets))
    return f"{buckets}|{normalized}"[:360]


class FoolMemory:
    """Persistent, scored memory for stateless Fool API calls."""

    def __init__(self, memory_dir: Path | None = None, scope: str | None = None) -> None:
        self.memory_dir = memory_dir or (MEMORY_DIR / RUNS_SUBDIR / (scope or "default"))
        self.episodes_path = self.memory_dir / EPISODES_NAME
        self.plans_path = self.memory_dir / PLANS_NAME
        self.tool_traces_path = self.memory_dir / TOOL_TRACES_NAME
        self.run_lessons_path = self.memory_dir / RUN_LESSONS_NAME
        self.run_lessons_summary_path = self.memory_dir / RUN_LESSONS_SUMMARY_NAME
        self.session_summaries_path = self.memory_dir / SESSION_SUMMARIES_NAME
        self.index_path = self.memory_dir / INDEX_NAME
        self.summary_path = self.memory_dir / SUMMARY_NAME
        self.best_solver_path = self.memory_dir / BEST_SOLVER_NAME
        self.best_report_path = self.memory_dir / BEST_REPORT_NAME
        self.best_meta_path = self.memory_dir / BEST_META_NAME
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._bucket_incumbents: BucketIncumbents | None = None

    @property
    def bucket_incumbents(self) -> BucketIncumbents:
        """Per-bucket champion store. Seeds from the legacy global best on
        first access if no buckets/ directory exists yet but a legacy
        best_solver.py + bucket_scores in best_meta.json are present."""
        if self._bucket_incumbents is None:
            store = BucketIncumbents(self.memory_dir / "buckets")
            if not store.scores() and self.best_solver_path.exists() and self.best_meta_path.exists():
                try:
                    meta = json.loads(self.best_meta_path.read_text(encoding="utf-8"))
                    bs = meta.get("bucket_scores") or {}
                    if isinstance(bs, dict) and bs:
                        store.seed_from_legacy(
                            solver_path=self.best_solver_path,
                            bucket_scores={k: float(v) for k, v in bs.items()},
                        )
                except (OSError, ValueError):
                    pass
            self._bucket_incumbents = store
        return self._bucket_incumbents

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    def _read_episodes(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.episodes_path)

    def _read_index(self) -> dict[str, dict[str, Any]]:
        if not self.index_path.exists():
            return {}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def retrieve(
        self,
        *,
        target_buckets: list[str],
        strategy_lane: str,
        query_text: str,
        keywords: list[str] | None = None,
        case_tags: list[str] | None = None,
        limit: int = 6,
    ) -> str:
        episodes = self._read_episodes()
        if not episodes:
            return "No durable memory recorded yet."
        keyword_list = [str(x) for x in (keywords or []) if str(x).strip()]
        tag_values = [str(x) for x in (case_tags or []) if str(x).strip()]
        query_tokens = _token_list(
            " ".join(target_buckets + [strategy_lane, query_text] + keyword_list + tag_values)
        )
        requested_tags = _collect_case_tags(target_buckets + tag_values + [query_text] + keyword_list)
        episode_texts: list[str] = []
        for item in episodes:
            episode_texts.append(
                " ".join(
                    [
                        str(item.get("hypothesis", "")),
                        str(item.get("reason", "")),
                        str(item.get("outcome", "")),
                        " ".join(str(x) for x in item.get("target_buckets", [])),
                    ]
                )
            )
        token_docs = [_token_list(text) for text in episode_texts]
        doc_freq, avgdl = _build_bm25_stats(token_docs)
        total_docs = len(token_docs)
        recent_gain_map = _recent_gain_by_tag(episodes)
        ranked: list[tuple[float, int, dict[str, Any], float, int, float]] = []
        for pos, item in enumerate(episodes):
            item_tags = _collect_case_tags(
                [str(x) for x in item.get("target_buckets", [])]
                + [
                    str(item.get("hypothesis", "")),
                    str(item.get("reason", "")),
                ]
            )
            overlap = len(requested_tags & item_tags) if requested_tags else 0
            bm25 = _bm25_score(
                query_tokens,
                token_docs[pos],
                doc_freq=doc_freq,
                total_docs=total_docs,
                avgdl=avgdl,
            )
            outcome = str(item.get("outcome", ""))
            outcome_weight = {
                "improved": 2.2,
                "rollback": 1.0,
                "regressed": 0.8,
                "neutral": 0.4,
                "baseline": 0.2,
            }.get(outcome, 0.0)
            recency = (pos + 1) / max(len(episodes), 1)
            delta = float(item.get("score_delta", 0.0) or 0.0)
            if outcome == "improved":
                delta_gain = min(max(-delta, 0.0), 900.0) / 130.0
            elif outcome in {"rollback", "regressed"}:
                delta_gain = -min(max(delta, 0.0), 900.0) / 220.0
            else:
                delta_gain = 0.0
            recent_gain = sum(max(recent_gain_map.get(tag, 0.0), 0.0) for tag in (requested_tags & item_tags))
            score = bm25 * 2.0 + overlap * 2.5 + recent_gain * 1.2 + delta_gain + outcome_weight + recency * 0.4
            ranked.append((score, pos, item, bm25, overlap, recent_gain))
        selected = sorted(ranked, key=lambda row: (row[0], row[1]), reverse=True)[:limit]
        lines = [
            "Retrieved durable experiment memory via BM25-hybrid ranking (keyword + case-tag + recent-gain):",
            "- query={q}; strategy_lane={lane}; keywords={k}; case_tags={t}".format(
                q=query_text or "(empty)",
                lane=strategy_lane,
                k=keyword_list or [],
                t=sorted(requested_tags) or [],
            ),
            "- do not repeat failed edits without a new mechanism.",
        ]
        for _, _, item, bm25, overlap, recent_gain in selected:
            rejection_text = ""
            rejections = item.get("generation_rejections", [])
            if isinstance(rejections, list) and rejections:
                reasons = [str(r.get("reason", ""))[:90] for r in rejections if isinstance(r, dict)]
                if reasons:
                    rejection_text = "; generation_rejections=" + " | ".join(reasons[:3])
            lines.append(
                "- iter={iteration} outcome={outcome} delta={delta:+.2f} buckets={buckets}; "
                "bm25={bm25:.3f} tag_overlap={overlap} recent_gain={recent_gain:.3f}; "
                "hypothesis={hypothesis}; reason={reason}{rejections}".format(
                    iteration=item.get("iteration", "?"),
                    outcome=item.get("outcome", "?"),
                    delta=float(item.get("score_delta", 0.0)),
                    buckets=",".join(str(x) for x in item.get("target_buckets", [])) or "all",
                    bm25=bm25,
                    overlap=overlap,
                    recent_gain=recent_gain,
                    hypothesis=str(item.get("hypothesis", ""))[:180],
                    reason=str(item.get("reason", ""))[:150],
                    rejections=rejection_text,
                )
            )
        # Append the 3 most recent session summaries matching any requested tag
        # (lightweight surface-only; BM25 ranking is still episode-driven).
        session_records = self._read_jsonl(self.session_summaries_path)
        if session_records:
            matched: list[dict[str, Any]] = []
            for rec in reversed(session_records):
                rec_tags = _collect_case_tags(
                    [str(x) for x in rec.get("target_buckets", [])]
                )
                if not requested_tags or (rec_tags & requested_tags) or not rec_tags:
                    matched.append(rec)
                if len(matched) >= 3:
                    break
            if matched:
                lines.append("")
                lines.append("Prior session summaries (most recent first):")
                for rec in matched:
                    summary_head = str(rec.get("summary", ""))[:600]
                    lines.append(
                        "- run={run} iter={iter} buckets={buckets}: {summary}".format(
                            run=rec.get("run_id", "?"),
                            iter=rec.get("iteration", "?"),
                            buckets=",".join(str(x) for x in rec.get("target_buckets", [])) or "all",
                            summary=summary_head,
                        )
                    )
        return "\n".join(lines)

    def retrieve_guidance(
        self,
        *,
        query_text: str,
        keywords: list[str] | None = None,
        case_tags: list[str] | None = None,
        limit: int = 6,
    ) -> str:
        return retrieve_guidance_text(
            query_text=query_text,
            keywords=keywords,
            case_tags=case_tags,
            episodes=self._read_episodes(),
            limit=limit,
        )

    def retrieve_plans(self, *, target_buckets: list[str], limit: int = 4) -> str:
        plans = self._read_jsonl(self.plans_path)
        if not plans:
            return "No stored brain plans yet."
        bucket_set = set(str(x) for x in target_buckets)
        ranked: list[tuple[float, int, dict[str, Any]]] = []
        for pos, item in enumerate(plans):
            item_buckets = set(str(x) for x in item.get("target_buckets", []))
            overlap = len(bucket_set & item_buckets)
            recency = (pos + 1) / max(len(plans), 1)
            ranked.append((overlap * 4.0 + recency, pos, item))
        selected = sorted(ranked, key=lambda row: (row[0], row[1]), reverse=True)[:limit]
        lines = ["Stored brain plans (use as continuity; do not repeat rejected ideas):"]
        for _, _, item in selected:
            lines.append(
                "- iter={iteration} lane={lane} buckets={buckets}; hypothesis={hypothesis}; plan={plan}".format(
                    iteration=item.get("iteration", "?"),
                    lane=str(item.get("strategy_lane", ""))[:60],
                    buckets=",".join(str(x) for x in item.get("target_buckets", [])) or "all",
                    hypothesis=str(item.get("hypothesis", ""))[:180],
                    plan="; ".join(str(x) for x in item.get("edit_plan", []))[:220],
                )
            )
        return "\n".join(lines)

    def retrieve_run_lessons(self, *, target_buckets: list[str], limit: int = 5) -> str:
        lessons = self._read_jsonl(self.run_lessons_path)
        if not lessons:
            return "No run-level lessons recorded yet."
        bucket_set = set(str(x) for x in target_buckets)
        ranked: list[tuple[float, int, dict[str, Any]]] = []
        for pos, item in enumerate(lessons):
            bottlenecks = set(str(x) for x in item.get("bottlenecks", []))
            winners = set(str(x) for x in item.get("winning_cases", []))
            overlap = len(bucket_set & (bottlenecks | winners))
            recency = (pos + 1) / max(len(lessons), 1)
            improvement = abs(float(item.get("total_delta", 0.0)))
            ranked.append((overlap * 5.0 + min(improvement, 800.0) / 200.0 + recency, pos, item))
        selected = sorted(ranked, key=lambda row: (row[0], row[1]), reverse=True)[:limit]
        lines = ["Run-level lessons from saved histories (treat as hard-earned guidance):"]
        for _, _, item in selected:
            lines.append(
                "- run={run_id} best={best_score:.2f} delta={delta:+.2f}; bottlenecks={bottlenecks}; "
                "worked={worked}; avoid={avoid}; next={next_steps}".format(
                    run_id=str(item.get("run_id", "?")),
                    best_score=float(item.get("best_score", 0.0) or 0.0),
                    delta=float(item.get("total_delta", 0.0) or 0.0),
                    bottlenecks=",".join(str(x) for x in item.get("bottlenecks", []))[:140] or "unknown",
                    worked="; ".join(str(x) for x in item.get("worked", []))[:220] or "unknown",
                    avoid="; ".join(str(x) for x in item.get("avoid", []))[:220] or "unknown",
                    next_steps="; ".join(str(x) for x in item.get("next_steps", []))[:220] or "unknown",
                )
            )
        return "\n".join(lines)

    def blocked_hypotheses(self, max_return: int = 6) -> list[str]:
        index = self._read_index()
        failed = [
            item
            for item in index.values()
            if int(item.get("failed_count", 0)) > 0
            and int(item.get("improved_count", 0)) == 0
        ]
        failed.sort(key=lambda item: str(item.get("last_seen", "")), reverse=True)
        return [str(item.get("hypothesis", "")) for item in failed[:max_return]]

    def record_plan(self, record: dict[str, Any]) -> None:
        normalized = dict(record)
        normalized["recorded_at"] = datetime.now().isoformat(timespec="seconds")
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        with self.plans_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(normalized, ensure_ascii=False) + "\n")

    def record_tool_trace(self, record: dict[str, Any]) -> None:
        normalized = dict(record)
        normalized["recorded_at"] = datetime.now().isoformat(timespec="seconds")
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        with self.tool_traces_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(normalized, ensure_ascii=False) + "\n")

    def record_run_lesson(self, record: dict[str, Any]) -> None:
        normalized = dict(record)
        normalized["recorded_at"] = datetime.now().isoformat(timespec="seconds")
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            item
            for item in self._read_jsonl(self.run_lessons_path)
            if str(item.get("run_id", "")) != str(normalized.get("run_id", ""))
        ]
        rows.append(normalized)
        with self.run_lessons_path.open("w", encoding="utf-8") as stream:
            for item in rows:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
        self._write_run_lessons_summary(rows)

    def record_session_summary(
        self,
        *,
        run_id: str,
        iteration: int,
        target_buckets: list[str],
        summary_text: str,
    ) -> None:
        """Append one harness-transcript summary to session_summaries.jsonl.

        Used by SessionCompactor as its summary_callback: every time the
        harness compresses a long transcript, the LLM-produced structured
        summary lands here, then becomes BM25-retrievable via retrieve().
        """
        if not summary_text or not summary_text.strip():
            return
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "run_id": str(run_id),
            "iteration": int(iteration),
            "target_buckets": [str(x) for x in (target_buckets or [])],
            "summary": summary_text.strip(),
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self.session_summaries_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def stored_best_score(self) -> float | None:
        if not self.best_meta_path.exists():
            return None
        try:
            data = json.loads(self.best_meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        try:
            return float(data.get("score"))
        except (TypeError, ValueError):
            return None

    def store_best_if_better(
        self,
        *,
        score: float,
        solver_path: Path,
        report_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        current = self.stored_best_score()
        if current is not None and score >= current:
            return False
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(solver_path, self.best_solver_path)
        shutil.copyfile(report_path, self.best_report_path)
        payload = dict(metadata or {})
        payload["score"] = float(score)
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        payload["solver_path"] = str(self.best_solver_path)
        payload["report_path"] = str(self.best_report_path)
        self.best_meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def record(self, record: dict[str, Any]) -> None:
        normalized = dict(record)
        normalized["recorded_at"] = datetime.now().isoformat(timespec="seconds")
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        with self.episodes_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(normalized, ensure_ascii=False) + "\n")

        index = self._read_index()
        key = _memory_key(
            str(normalized.get("hypothesis", "")),
            [str(x) for x in normalized.get("target_buckets", [])],
        )
        current = index.get(
            key,
            {
                "hypothesis": str(normalized.get("hypothesis", "")),
                "target_buckets": list(normalized.get("target_buckets", [])),
                "attempt_count": 0,
                "improved_count": 0,
                "failed_count": 0,
                "best_delta": None,
            },
        )
        current["attempt_count"] = int(current["attempt_count"]) + 1
        outcome = str(normalized.get("outcome", ""))
        if outcome == "improved":
            current["improved_count"] = int(current["improved_count"]) + 1
        if outcome in {"rollback", "regressed"}:
            current["failed_count"] = int(current["failed_count"]) + 1
        delta = float(normalized.get("score_delta", 0.0))
        previous_best = current.get("best_delta")
        current["best_delta"] = delta if previous_best is None else min(float(previous_best), delta)
        current["last_outcome"] = outcome
        current["last_reason"] = str(normalized.get("reason", ""))[:240]
        current["last_seen"] = normalized["recorded_at"]
        index[key] = current
        self.index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_summary(index)

    def _write_summary(self, index: dict[str, dict[str, Any]]) -> None:
        entries = sorted(
            index.values(),
            key=lambda item: (
                int(item.get("improved_count", 0)) > 0,
                -float(item.get("best_delta", 0.0) or 0.0),
                str(item.get("last_seen", "")),
            ),
            reverse=True,
        )
        lines = ["# Fool Long-Term Memory", "", "评分验证后的策略摘要；原始证据保存在 `episodes.jsonl`。", ""]
        for item in entries[:20]:
            lines.append(
                "- outcome={}; attempts={}; improved={}; failed={}; best_delta={:+.2f}; hypothesis={}".format(
                    item.get("last_outcome", "?"),
                    int(item.get("attempt_count", 0)),
                    int(item.get("improved_count", 0)),
                    int(item.get("failed_count", 0)),
                    float(item.get("best_delta", 0.0) or 0.0),
                    str(item.get("hypothesis", ""))[:180],
                )
            )
        self.summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_run_lessons_summary(self, lessons: list[dict[str, Any]]) -> None:
        lines = [
            "# Fool Run Lessons",
            "",
            "自动从完整 runs 中提炼的经验；用于压缩历史、指导后续 Agent 规划。",
            "",
        ]
        for item in lessons[-12:]:
            lines.append(
                "## {run_id}".format(run_id=str(item.get("run_id", "?")))
            )
            lines.append(
                "- best_score={best:.2f}; baseline_score={baseline:.2f}; total_delta={delta:+.2f}".format(
                    best=float(item.get("best_score", 0.0) or 0.0),
                    baseline=float(item.get("baseline_score", 0.0) or 0.0),
                    delta=float(item.get("total_delta", 0.0) or 0.0),
                )
            )
            lines.append("- bottlenecks: " + (", ".join(str(x) for x in item.get("bottlenecks", [])) or "unknown"))
            lines.append("- worked: " + ("; ".join(str(x) for x in item.get("worked", [])) or "unknown"))
            lines.append("- avoid: " + ("; ".join(str(x) for x in item.get("avoid", [])) or "unknown"))
            lines.append("- next: " + ("; ".join(str(x) for x in item.get("next_steps", [])) or "unknown"))
            lines.append("")
        self.run_lessons_summary_path.write_text("\n".join(lines), encoding="utf-8")
