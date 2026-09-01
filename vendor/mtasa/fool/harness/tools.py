from __future__ import annotations

import json as _json
import re as _re_top
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fool.harness.context import FatalToolError

@dataclass
class ToolContext:
    input_dir: Path
    run_dir: Path
    best_solver_path: Path | None
    best_report_path: Path | None
    last_report_path: Path | None
    bootstrap_solver_path: Path | None
    durable_memory: Any  # fool.memory_store.FoolMemory or None
    dataset_profile_text: str
    memory_notes: Any = None  # fool.memory_notes.MemoryNotesStore or None
    # Harness-side identity for the current round. Plumbed so tools that
    # need run_id / iteration (e.g. memory_write) can fall back to these
    # instead of asking the model to recall and pass them — that was a
    # frequent source of misattributed memory entries.
    run_id: str = ""
    iteration: int = 0
    # Per-dataset fingerprint (16-char sha256 of sorted dataset files).
    # Same value as FoolMemory's scope; recorded in note metadata so future
    # rounds can see which dataset a lesson was learned under. Opaque to the
    # model — never expose in prompts, just plumb through here.
    dataset_fp: str = ""
    # Global version number for this round (allocated from VersionIndex
    # before the round runs). 0 means "not allocated" (legacy / tests).
    global_v: int = 0
    # fool.version_index.VersionIndex (or None) — used by read_version /
    # list_versions to resolve cross-run lookups.
    version_index: Any = None


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str


ToolFn = Callable[[ToolContext, dict[str, Any]], ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    risky: bool
    schema: dict[str, str]
    run: ToolFn
    max_output: int | None = None  # None = uncapped; set only for tools known to spew.
    # Name of a single large-text arg that should be rendered as a raw subtag
    # (e.g. <blocks>...</blocks>) instead of JSON-encoded inside <args>.
    # Set for tools whose payload is multi-line code / SEARCH-REPLACE envelopes
    # where JSON escaping hurts both readability and the LLM's ability to emit
    # the call without escaping mistakes.
    body_field: str | None = None


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


class ToolRegistry:
    # Hard cap on previous-result echo inside the dup-hit error message.
    # Keeps the message actionable without flooding the next prompt.
    _DUP_RESULT_PREVIEW_CHARS = 600

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        # Each entry is (signature, last_content). Only successfully parsed
        # tool calls land here — parse-failure retries in the runner never
        # reach registry.run, so they can't poison dup detection.
        self._call_log: list[tuple[tuple[str, str], str, bool]] = []

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "risky": tool.risky,
                "schema": dict(tool.schema),
            }
            for tool in sorted(self._tools.values(), key=lambda t: t.name)
        ]

    def run(
        self, name: str, context: ToolContext, args: dict[str, Any] | None
    ) -> ToolResult:
        args = args or {}
        # P0 fix: unwrap double-encoded args. LLMs sometimes send
        # {"args": "{\"uuid\": \"...\", ...}"} instead of the bare params.
        # No registered tool takes a single "args" parameter, so it's safe to
        # detect that shape and json.loads it once.
        if (
            isinstance(args, dict)
            and len(args) == 1
            and "args" in args
            and isinstance(args["args"], str)
        ):
            import json as _json
            try:
                inner = _json.loads(args["args"])
                if isinstance(inner, dict):
                    args = inner
            except Exception:
                pass

        tool = self._tools.get(name)
        if tool is None:
            from difflib import get_close_matches as _gcm
            suggestions = _gcm(name, list(self._tools.keys()), n=3, cutoff=0.4)
            hint = (
                f" did you mean: {', '.join(suggestions)}?"
                if suggestions
                else f" available tools: {', '.join(sorted(self._tools.keys()))}"
            )
            return ToolResult(
                ok=False, content=f"error: unknown tool '{name}';{hint}"
            )

        signature = (name, _stable_args(args))
        # Only dedup against the previous call when it *succeeded*. A failed
        # call (e.g. missing required arg, wire-format error) often means the
        # model is about to retry with corrected args — observed in
        # run_20260605_114305 R3 where read_version retries got blocked
        # because args parsing dropped `kind` and the next attempts ate ~5
        # steps bouncing off dedup. Failed-result dedup also bypasses the
        # legitimate "I corrected and want to try again" path.
        if (
            self._call_log
            and self._call_log[-1][0] == signature
            and self._call_log[-1][2] is True
        ):
            preview = _clip(
                self._call_log[-1][1], self._DUP_RESULT_PREVIEW_CHARS
            )
            return ToolResult(
                ok=False,
                content=(
                    f"error: repeated identical call to {name} with same args. "
                    "Last result was:\n"
                    f"---\n{preview}\n---\n"
                    "Re-read it instead of re-calling. "
                    "If you need different info, change args or call a different tool; "
                    "if you're ready, emit <final>."
                ),
            )

        try:
            result = tool.run(context, args)
        except FatalToolError:
            # Infra-level failure — abort the whole loop, do not swallow.
            raise
        except Exception as exc:
            content = f"error: tool {name} failed: {exc}"
            self._call_log.append((signature, content, False))
            return ToolResult(ok=False, content=content)

        content = result.content
        if tool.max_output is not None:
            content = _clip(content, tool.max_output)
        self._call_log.append((signature, content, bool(result.ok)))
        return ToolResult(ok=result.ok, content=content)


def _fuzzy_pick(
    value: str,
    candidates: list[str],
    *,
    auto_cutoff: float = 0.75,
    hint_cutoff: float = 0.5,
) -> tuple[str | None, str]:
    """Resolve a possibly-typo'd value against a closed candidate set.

    Returns (resolved, note):
      - exact match → (value, "")
      - confidently auto-corrected → (other, "[auto-corrected: 'X' -> 'Y']")
      - no confident match → (None, "did you mean: [...]") suggestion hint

    Resolution order: case-insensitive exact → unique prefix → unique
    substring → unique close match (SequenceMatcher ratio ≥ auto_cutoff,
    or top-vs-next gap ≥ 0.1).
    """
    from difflib import SequenceMatcher, get_close_matches

    if not isinstance(value, str) or not value:
        hints = list(candidates[:3])
        return None, f"valid options: {hints}"
    if value in candidates:
        return value, ""
    low = value.lower()
    ci = [c for c in candidates if c.lower() == low]
    if len(ci) == 1:
        return ci[0], f"[auto-corrected: {value!r} -> {ci[0]!r}]"
    prefix = [c for c in candidates if c.lower().startswith(low)]
    if len(prefix) == 1:
        return prefix[0], f"[auto-corrected: {value!r} -> {prefix[0]!r}]"
    substr = [c for c in candidates if low in c.lower()]
    if len(substr) == 1:
        return substr[0], f"[auto-corrected: {value!r} -> {substr[0]!r}]"
    close = get_close_matches(value, candidates, n=2, cutoff=auto_cutoff)
    if len(close) == 1:
        return close[0], f"[auto-corrected: {value!r} -> {close[0]!r}]"
    if len(close) >= 2:
        ratios = sorted(
            ((SequenceMatcher(None, value, c).ratio(), c) for c in close),
            reverse=True,
        )
        if ratios[0][0] - ratios[1][0] >= 0.1:
            best = ratios[0][1]
            return best, f"[auto-corrected: {value!r} -> {best!r}]"
    hints = get_close_matches(value, candidates, n=3, cutoff=hint_cutoff)
    if hints:
        return None, f"did you mean one of: {hints}?"
    return None, f"valid options: {list(candidates)}"


def _stable_args(args: dict[str, Any]) -> str:
    import json

    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False)
    except Exception:
        return repr(sorted(args.items()))


# --- read-only tools ---

from fool.agent_tools.base import ToolContext as _AgentCtx
from fool.agent_tools.analysis_tools import rank_bottlenecks as _agent_rank_bottlenecks
from fool.agent_tools.template_tools import list_strategy_templates as _agent_list_strategy_templates
from fool.agent_tools.template_tools import TEMPLATE_DIR as _STRATEGY_TEMPLATE_DIR

_FOOL_ROOT = Path(__file__).resolve().parents[2]

def _load_report(path: Path | None) -> "dict[str, Any] | None":
    if not path or not path.exists():
        return None
    try:
        from fool.genius_file_client import read_report
        return read_report(path)
    except Exception:
        return None


def _agent_ctx(ctx: ToolContext) -> _AgentCtx:
    return _AgentCtx(
        input_dir=ctx.input_dir,
        run_dir=ctx.run_dir,
        memory_scope="harness",
        dataset_profile=ctx.dataset_profile_text,
        best_report=_load_report(ctx.best_report_path),
        last_report=_load_report(ctx.last_report_path),
    )


_BUCKET_FEATURES_PATH = _FOOL_ROOT / "teacher" / "bucket_features.json"
_BUCKET_FEATURES_CACHE: dict[str, Any] | None = None


def _load_bucket_features() -> dict[str, Any]:
    global _BUCKET_FEATURES_CACHE
    if _BUCKET_FEATURES_CACHE is None:
        try:
            _BUCKET_FEATURES_CACHE = _json.loads(
                _BUCKET_FEATURES_PATH.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            _BUCKET_FEATURES_CACHE = {}
    return _BUCKET_FEATURES_CACHE


_BUCKET_SECTIONS = ("structural", "bundle", "score", "willingness", "joint", "probes", "tags")


# Per-run cache for profile_dataset responses. Key = (run_id, dataset_fp, bucket, section);
# value = (first_iteration, full_content). The dataset is static within a run, so
# any (bucket, section) returns identical content every time — we stub repeat
# calls to save tokens and nudge the model away from redundant lookups. run_id is
# included so a second run in the same Python process (frontend keeps the worker
# module loaded) doesn't see stale "cached at round N" notes from the prior run.
_PROFILE_RESPONSE_CACHE: dict[tuple[str, str, str, str], tuple[int, str]] = {}


def _profile_dataset_clear_cache() -> None:
    """Reset the per-run cache. Called by tests; not exposed to the model."""
    _PROFILE_RESPONSE_CACHE.clear()


def _t_profile_dataset(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    # Legacy 'section' parameter is gone. Field-level slicing replaces it:
    # callers pick a specific field (e.g. 'courier_ratio' or 'score.mean')
    # and the tool slices it across all 10 buckets in one shot, instead of
    # forcing per-bucket section dumps.
    if "section" in args:
        return ToolResult(
            ok=False,
            content=(
                "profile_dataset no longer accepts 'section'. New API: "
                "bucket=<name> dumps that bucket's full profile (~34 fields); "
                "field=<name> slices that field across all 10 buckets "
                "(use 'section.name' if a bare name is ambiguous, e.g. "
                "'score.mean' vs 'willingness.mean'); pass both to get the "
                "bucket dump + the field slice; pass neither for the default "
                "structural cross-bucket slice. Use field='list' to enumerate."
            ),
        )

    bucket = str(args.get("bucket", "")).strip()
    field = str(args.get("field", "")).strip()

    features = _load_bucket_features()
    if not features:
        return ToolResult(ok=False, content=f"bucket_features.json unavailable at {_BUCKET_FEATURES_PATH}")
    buckets = list(features.get("_meta", {}).get("buckets", []))
    if not buckets:
        return ToolResult(ok=False, content="bucket_features.json missing _meta.buckets")

    if bucket in ("list", "all"):
        return ToolResult(ok=True, content=_json.dumps(buckets, ensure_ascii=False))
    if field in ("list", "all"):
        return ToolResult(ok=True, content=_field_listing(features, buckets))

    correction_notes: list[str] = []

    if bucket and bucket not in buckets:
        resolved, note = _fuzzy_pick(bucket, buckets)
        if resolved is None:
            return ToolResult(ok=False, content=f"unknown bucket {bucket!r}. {note}")
        bucket = resolved
        correction_notes.append(note)

    resolved_field: tuple[str, str | None] | None = None
    if field:
        resolved_field, note = _resolve_field(field, features, buckets)
        if resolved_field is None:
            return ToolResult(ok=False, content=f"unknown field {field!r}. {note}")
        if note:
            correction_notes.append(note)

    parts: list[tuple[str, Any]] = []
    if not bucket and not field:
        slice_ = {b: features.get(b, {}).get("structural", {}) for b in buckets}
        parts.append((
            "(default) structural across 10 buckets — pass bucket=<name> for "
            "the full bucket dump, field=<name> for any other slice",
            slice_,
        ))
    else:
        if bucket:
            parts.append((f"bucket={bucket} (all fields)", features.get(bucket, {})))
        if resolved_field:
            section, fname = resolved_field
            if fname is None:
                slice_ = {b: features.get(b, {}).get(section, []) for b in buckets}
                label = f"field={section} across 10 buckets"
            else:
                slice_ = {b: features.get(b, {}).get(section, {}).get(fname) for b in buckets}
                label = f"field={section}.{fname} across 10 buckets"
            parts.append((label, slice_))

    chunks: list[str] = []
    if correction_notes:
        chunks.append("\n".join(correction_notes))
    for label, body in parts:
        chunks.append(f"# {label}\n{_json.dumps(body, ensure_ascii=False, indent=2)}")
    content = "\n\n".join(chunks)

    cache_key = (ctx.run_id or "", ctx.dataset_fp or "", bucket, field)
    cached = _PROFILE_RESPONSE_CACHE.get(cache_key)
    if cached is not None:
        first_iter, cached_content = cached
        if cached_content == content:
            return ToolResult(
                ok=True,
                content=(
                    f"(cached) profile_dataset(bucket={bucket!r}, field={field!r}) "
                    f"was already returned at round {first_iter}; dataset is static "
                    f"within a run so the response is identical ({len(content)}B). "
                    f"Re-read your earlier round if you need the JSON; do not call "
                    f"profile_dataset again with the same args this run."
                ),
            )
    _PROFILE_RESPONSE_CACHE[cache_key] = (ctx.iteration, content)
    return ToolResult(ok=True, content=content)


def _field_index(
    features: dict[str, Any], buckets: list[str]
) -> dict[str, list[tuple[str, str | None]]]:
    """Index {bare_name: [(section, name_or_None), ...]} for one sample bucket.
    All buckets share the same schema. 'tags' (a list section) maps to
    (section, None)."""
    idx: dict[str, list[tuple[str, str | None]]] = {}
    if not buckets:
        return idx
    sample = features.get(buckets[0], {})
    for sec, body in sample.items():
        if isinstance(body, dict):
            for name in body.keys():
                idx.setdefault(name, []).append((sec, name))
        elif sec == "tags":
            idx.setdefault("tags", []).append(("tags", None))
    return idx


def _field_listing(features: dict[str, Any], buckets: list[str]) -> str:
    idx = _field_index(features, buckets)
    flat: list[str] = []
    for pairs in idx.values():
        for sec, name in pairs:
            flat.append(sec if name is None else f"{sec}.{name}")
    return (
        "Available fields (dotted = section.name; bare name OK when unique):\n"
        + _json.dumps(sorted(set(flat)), ensure_ascii=False, indent=2)
    )


def _resolve_field(
    field: str, features: dict[str, Any], buckets: list[str]
) -> tuple[tuple[str, str | None] | None, str]:
    idx = _field_index(features, buckets)
    all_dotted = sorted(
        (sec if name is None else f"{sec}.{name}")
        for pairs in idx.values()
        for sec, name in pairs
    )
    if "." in field:
        section, _, name = field.partition(".")
        sample = features.get(buckets[0], {})
        sec_body = sample.get(section)
        if isinstance(sec_body, dict) and name in sec_body:
            return (section, name), ""
        resolved, note = _fuzzy_pick(field, all_dotted)
        if resolved is None:
            return None, note
        if "." in resolved:
            sec, _, nm = resolved.partition(".")
            return (sec, nm), note
        return (resolved, None), note  # e.g. 'tags'
    if field == "tags":
        return ("tags", None), ""
    pairs = idx.get(field)
    if pairs and len(pairs) == 1:
        return pairs[0], ""
    if pairs and len(pairs) > 1:
        dotted = [(s if n is None else f"{s}.{n}") for s, n in pairs]
        return None, (
            f"ambiguous field {field!r}: matches {dotted}. "
            f"Pass the dotted form (e.g. {dotted[0]!r})."
        )
    resolved, note = _fuzzy_pick(field, all_dotted)
    if resolved is None:
        return None, note
    if "." in resolved:
        sec, _, nm = resolved.partition(".")
        return (sec, nm), note
    return (resolved, None), note


def _t_rank_bottlenecks(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    inner = _agent_rank_bottlenecks(_agent_ctx(ctx), args)
    return ToolResult(ok=inner.ok, content=inner.summary)


def _t_retrieve_guidance(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    query = str(args.get("query", "")).strip()
    target_buckets = _as_str_list(args.get("target_buckets", []))
    case_tags = _as_str_list(args.get("case_tags", [])) + target_buckets
    keywords = _as_str_list(args.get("keywords", []))
    limit = _as_int(args.get("limit", 6), default=6, lo=1, hi=10)
    try:
        if ctx.durable_memory is not None and hasattr(ctx.durable_memory, "retrieve_guidance"):
            body = ctx.durable_memory.retrieve_guidance(
                query_text=query,
                keywords=keywords,
                case_tags=case_tags,
                limit=limit,
            )
        else:
            from fool.memory_store import retrieve_guidance_text

            body = retrieve_guidance_text(
                query_text=query,
                keywords=keywords,
                case_tags=case_tags,
                episodes=None,
                limit=limit,
            )
    except Exception as exc:
        return ToolResult(ok=False, content=f"guidance retrieve failed: {exc}")
    return ToolResult(ok=True, content=str(body) if body else "(guidance empty)")


_UUID_RE = _re_top.compile(r"^[0-9a-f]{32}$")


def _t_memory_write(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.memory_notes is None:
        return ToolResult(ok=False, content="memory_notes not configured")

    # In-loop tool always writes to "lesson"; per-section routing (try_error /
    # key_decision) is done out-of-loop by outcome_reflector and
    # session_compactor via direct MemoryNotesStore.write_note() calls.
    section = "lesson"

    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        return ToolResult(
            ok=False,
            content="memory_write requires non-empty 'title' (<=80 chars).",
        )

    body = args.get("body")
    if not isinstance(body, str) or not body.strip():
        return ToolResult(
            ok=False,
            content=(
                "memory_write requires non-empty 'body'. Write the actual content "
                "future rounds should read — e.g. what was tried, what changed, "
                "which bucket moved, and the score delta."
            ),
        )

    # run_id / iteration are harness state; the model can omit them and we
    # backfill from ctx. Accept explicit overrides for callers that genuinely
    # know what they're doing (e.g. cross-run consolidation tools).
    run_id = args.get("run_id") or ctx.run_id
    if not run_id:
        return ToolResult(
            ok=False,
            content="memory_write: run_id missing and ctx has no run_id configured",
        )

    iteration_raw = args.get("iteration")
    if iteration_raw is None:
        iteration = ctx.iteration
    else:
        try:
            iteration = int(iteration_raw)
        except (TypeError, ValueError):
            return ToolResult(
                ok=False,
                content=(
                    f"memory_write: iteration must be an int, got "
                    f"{iteration_raw!r}. Usually you can just omit this — "
                    "harness fills it in."
                ),
            )

    dataset_fp = args.get("dataset_fp") or None

    try:
        path = ctx.memory_notes.write_note(
            section=section,
            title=title,
            body=body,
            run_id=run_id,
            iteration=iteration,
            dataset_fp=dataset_fp,
        )
        return ToolResult(ok=True, content=f"written to {path}")
    except ValueError as e:
        return ToolResult(ok=False, content=f"memory_write error: {e}")


def _t_memory_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    if ctx.memory_notes is None:
        return ToolResult(ok=False, content="memory_notes not configured")
    query = args.get("query", "")
    results = ctx.memory_notes.search(
        query=query,
        max_results=_as_int(args.get("max_results", 5), default=5, lo=1, hi=20),
        max_snippet_chars=int(args.get("max_snippet_chars", 400)),
    )
    # Partition results so try_errors are visually demoted: the agent should
    # treat them as "局部失败样本" (scope-bound), not as bans. Each entry's
    # scope/falsifies/confidence (when present) is exposed so the agent can
    # judge whether a new hypothesis simply changes the scope.
    lessons: list[dict] = []
    try_errors: list[dict] = []
    others: list[dict] = []
    for r in results:
        sec = r.get("section")
        if sec == "lesson":
            lessons.append(r)
        elif sec == "try_error":
            try_errors.append(r)
        else:
            others.append(r)
    payload: dict[str, Any] = {
        "lessons": lessons,
        "try_errors": try_errors,
    }
    if others:
        payload["other"] = others
    if try_errors:
        payload["try_error_notice"] = (
            "以下 try_errors 是**作用域受限的失败样本**，不是机制级禁区。"
            "若你的新假设把作用域改对（更窄的桶 / 不同的阶段 / 不同参数耦合），"
            "可以在 <intent> 里明确说明绕开方式后继续。confidence < 0.5 的条目"
            "通常已被停滞门控降权，参考价值更弱。"
        )
    if not isinstance(query, str) or not query.strip():
        payload["hint"] = (
            "empty query — memory_search needs keywords to BM25-rank. "
            "Pass query=... (e.g. a hypothesis fragment). If you have no "
            "prior context to recall, skip the memory step and proceed."
        )
    elif not results:
        payload["hint"] = (
            "no matches for this query. Try different keywords, broaden "
            "to a single noun, or skip the memory step if this hypothesis "
            "is genuinely new."
        )
    return ToolResult(ok=True, content=_json.dumps(payload, ensure_ascii=False))


def _t_read_tool_result(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    uid = str(args.get("uuid", "")).strip()
    spill_dir = ctx.run_dir / "tool_results"
    if not uid:
        # No auto-correct: silently guessing "latest spill" caused round 6/7
        # dead-loops where unknown-subtag parsing produced args={} and the
        # fallback returned a stale file the model kept re-reading.
        return ToolResult(
            ok=False,
            content=(
                "uuid required (32 hex chars from a <<<TRUNCATED>>> marker). "
                "Pass via <args>{\"uuid\":\"<32hex>\",\"start_line\":N}</args> — "
                "subtag form like <uuid>...</uuid> is NOT supported."
            ),
        )
    if not _UUID_RE.match(uid):
        return ToolResult(ok=False, content=f"invalid uuid: {uid!r}")
    path = spill_dir / f"{uid}.txt"
    if not path.is_file():
        return ToolResult(
            ok=False,
            content=(
                f"no such tool_result: {uid}\n"
                "Note: read_tool_result only continues THIS round's truncated tool "
                "output — the uuid must come from a real <<<TRUNCATED>>> marker. "
                "To read a prior solver's source, use read_version(v=N) instead."
            ),
        )
    start = max(1, int(args.get("start_line", 1)))
    cap = min(max(1, int(args.get("max_lines", 800))), 2000)
    lines = path.read_text(encoding="utf-8").splitlines()
    end = min(start - 1 + cap, len(lines))
    chunk = "\n".join(lines[start - 1:end])
    if end < len(lines):
        chunk += f"\n<<<TRUNCATED>>> more at start_line={end + 1} (total {len(lines)} lines)"
    return ToolResult(ok=True, content=chunk)


def _t_read_dialog(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    try:
        rnd = int(args["round"])
    except (KeyError, ValueError, TypeError):
        return ToolResult(ok=False, content="round (int) required")
    path = ctx.run_dir / "dialog" / f"round_{rnd:03d}.jsonl"
    if not path.is_file():
        return ToolResult(ok=False, content=f"no dialog file for round {rnd}")
    start = max(1, int(args.get("start_line", 1)))
    cap = min(max(1, int(args.get("max_lines", 400))), 1000)
    lines = path.read_text(encoding="utf-8").splitlines()
    end = min(start - 1 + cap, len(lines))
    chunk = "\n".join(lines[start - 1:end])
    if end < len(lines):
        chunk += f"\n<<<TRUNCATED>>> more at start_line={end + 1} (total {len(lines)} lines)"
    return ToolResult(ok=True, content=chunk)


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _as_int(value: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, lo), hi)


def _t_list_strategy_templates(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    inner = _agent_list_strategy_templates(_agent_ctx(ctx), args)
    return ToolResult(ok=inner.ok, content=inner.summary)


_TEMPLATE_NAME_RE = _re_top.compile(r"^solver_[A-Za-z0-9_]+\.py$")


def _t_read_strategy_template(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    raw_name = str(args.get("name", "")).strip()
    if not raw_name:
        return ToolResult(
            ok=False,
            content=(
                "read_strategy_template requires name='solver_<slug>.py' "
                "(e.g. 'solver_low_regret.py'). Use list_strategy_templates "
                "to discover available names."
            ),
        )

    available = sorted(p.name for p in _STRATEGY_TEMPLATE_DIR.glob("solver_*.py"))
    correction_note = ""
    name = raw_name

    if not _TEMPLATE_NAME_RE.match(name) or name not in available:
        # Try fuzzy match. First normalize: ensure 'solver_' prefix and '.py' suffix.
        candidate = name
        if not candidate.endswith(".py"):
            candidate = candidate + ".py"
        if not candidate.startswith("solver_"):
            candidate = "solver_" + candidate
        resolved, note = _fuzzy_pick(candidate, available)
        if resolved is None:
            # Last attempt: match bare slug against template slugs.
            slugs = [a[len("solver_"):-len(".py")] for a in available]
            bare = raw_name
            if bare.startswith("solver_"):
                bare = bare[len("solver_"):]
            if bare.endswith(".py"):
                bare = bare[:-3]
            resolved2, note2 = _fuzzy_pick(bare, slugs)
            if resolved2 is None:
                return ToolResult(
                    ok=False,
                    content=(
                        f"template not found: {raw_name!r}. {note2} "
                        "Use list_strategy_templates to see available names."
                    ),
                )
            resolved = f"solver_{resolved2}.py"
            note = f"[auto-corrected: {raw_name!r} -> {resolved!r}]"
        name = resolved
        correction_note = note

    path = _STRATEGY_TEMPLATE_DIR / name
    try:
        path_real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return ToolResult(ok=False, content=f"template not found: {name}")
    if path_real.parent != _STRATEGY_TEMPLATE_DIR.resolve():
        return ToolResult(ok=False, content=f"template not found: {name}")
    body = path_real.read_text(encoding="utf-8", errors="replace")
    if correction_note:
        body = f"{correction_note}\n{body}"
    return ToolResult(ok=True, content=body)


_VALID_VERSION_KINDS = ("solver", "report", "plan", "reflect", "harness_full", "buckets")


def _resolve_version_paths(
    ctx: ToolContext, entry: dict[str, Any] | None, iteration: int
) -> dict[str, Path | None]:
    """Return solver/report/harness/reflect paths for a version.

    Prefer paths recorded in the VersionIndex entry (cross-run safe). A
    `None` value means "this kind was not produced for this v" (e.g.
    harness_failed round, Genius rejected solver) — callers should report
    that explicitly instead of silently falling back to current-run
    filenames, which would point at unrelated files when the entry comes
    from another run.

    Only when no entry exists at all (legacy / no index) do we fall back
    to the current run_dir layout — there iteration is implicitly the
    current run's local round number.
    """
    if not entry:
        tag = f"v{iteration:03d}"
        return {
            "solver": ctx.run_dir / f"solver_{tag}.py",
            "report": ctx.run_dir / f"report_{tag}.txt",
            "harness": ctx.run_dir / f"harness_{tag}.json",
            "reflect": ctx.run_dir / f"reflect_{tag}.json",
        }
    paths: dict[str, Path | None] = {}
    for k, key in (
        ("solver", "solver_path"),
        ("report", "report_path"),
        ("harness", "harness_path"),
        ("reflect", "reflect_path"),
    ):
        recorded = entry.get(key) or ""
        paths[k] = Path(recorded) if recorded else None
    return paths


def _t_read_version(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    v_spec = args.get("v")
    kind = args.get("kind")
    correction_note = ""
    if kind not in _VALID_VERSION_KINDS:
        if not isinstance(kind, str) or not kind:
            return ToolResult(
                ok=False,
                content=(
                    f"error: 'kind' is required, must be one of {list(_VALID_VERSION_KINDS)}. "
                    "plan = final plan JSON extracted from the harness transcript; "
                    "harness_full = the entire transcript; reflect = reflector log."
                ),
            )
        resolved, note = _fuzzy_pick(kind, list(_VALID_VERSION_KINDS))
        if resolved is None:
            return ToolResult(
                ok=False,
                content=(
                    f"error: 'kind'={kind!r} unknown — {note}. "
                    f"valid={list(_VALID_VERSION_KINDS)}."
                ),
            )
        kind = resolved
        correction_note = note
    result = _read_version_body(ctx, v_spec, kind)
    if correction_note and result.ok:
        return ToolResult(ok=True, content=f"{correction_note}\n{result.content}")
    return result


def _read_version_body(ctx: ToolContext, v_spec: Any, kind: str) -> ToolResult:
    entry: dict[str, Any] | None = None
    iteration: int

    # B: default missing v to 'latest' rather than failing — that's the most
    # common intent ("show me the last round's X"). The auto-default still
    # routes through the index resolver below, which will error cleanly if
    # no index is plumbed.
    if v_spec is None or (isinstance(v_spec, str) and not v_spec.strip()):
        v_spec = "latest"

    idx = ctx.version_index
    if idx is not None:
        entry = idx.resolve(v_spec, current_run_id=ctx.run_id)
        if entry is None:
            return ToolResult(
                ok=False,
                content=(
                    f"version {v_spec!r} not found in index. Call list_versions "
                    "to see available versions."
                ),
            )
        iteration = int(entry.get("iteration") or 0)
    else:
        # No index plumbed — accept int / 'vNNN' / 'NNN' only, treat as
        # current-run iteration.
        if isinstance(v_spec, int):
            iteration = v_spec
        elif isinstance(v_spec, str):
            digits = v_spec.strip().lower().lstrip("v")
            if not digits.isdigit():
                return ToolResult(
                    ok=False,
                    content="error: no version index available; pass an int iteration",
                )
            iteration = int(digits)
        else:
            return ToolResult(ok=False, content="error: 'v' must be int or str")
        if iteration <= 0:
            return ToolResult(ok=False, content="error: 'v' must be a positive integer")

    paths = _resolve_version_paths(ctx, entry, iteration)
    tag_label = (
        f"v{entry['v']:03d} (run {entry.get('run_id', '?')} iter {iteration})"
        if entry
        else f"v{iteration:03d}"
    )

    if kind == "buckets":
        if not entry:
            return ToolResult(
                ok=False,
                content="error: 'buckets' kind requires the version index (no entry resolved)",
            )
        scores = entry.get("bucket_scores") or {}
        if not scores:
            return ToolResult(
                ok=True,
                content=(
                    f"{tag_label}: no bucket scores recorded "
                    f"(outcome={entry.get('outcome', '?')})"
                ),
            )
        payload = {
            "v": entry.get("v"),
            "iteration": entry.get("iteration"),
            "run_id": entry.get("run_id"),
            "total_score": entry.get("score"),
            "bucket_scores": scores,
            "bucket_uncovered": entry.get("bucket_uncovered") or {},
        }
        return ToolResult(
            ok=True, content=_json.dumps(payload, ensure_ascii=False, indent=2)
        )

    if kind == "solver":
        path = paths["solver"]
    elif kind == "report":
        path = paths["report"]
    elif kind in ("plan", "harness_full"):
        path = paths["harness"]
    else:  # reflect
        path = paths["reflect"]

    if path is None:
        outcome = (entry or {}).get("outcome") or "?"
        return ToolResult(
            ok=False,
            content=(
                f"{tag_label}: no {kind} produced for this version "
                f"(outcome={outcome}). Common causes: harness_failed, "
                "Genius rejected the solver, or duplicate-guard skipped scoring."
            ),
        )
    if not path.exists():
        return ToolResult(ok=False, content=f"{tag_label} {kind} not found at {path}")

    if kind == "plan":
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return ToolResult(ok=False, content=f"error: cannot parse {path.name}: {exc}")
        plan = ((data or {}).get("final") or {}).get("plan")
        if not plan:
            return ToolResult(ok=True, content=f"(no plan recorded for {tag_label})")
        return ToolResult(
            ok=True,
            content=_json.dumps(plan, ensure_ascii=False, indent=2),
        )

    return ToolResult(ok=True, content=path.read_text(encoding="utf-8", errors="replace"))


def _format_version_row(e: dict[str, Any], current_run_id: str) -> str:
    v = e.get("v", "?")
    iter_n = e.get("iteration", "?")
    run_id = e.get("run_id", "")
    is_cur = "*" if run_id == current_run_id else " "
    score = e.get("score")
    score_str = f"{score:.2f}" if isinstance(score, (int, float)) else "  --  "
    solved = e.get("solved_cases")
    total = e.get("total_cases")
    cov = f"{solved}/{total}" if solved is not None and total is not None else "  -  "
    outcome = (e.get("outcome") or "")[:10]
    # Which kinds are actually readable for this v — agent uses these
    # letters to know what read_version(kind=...) will succeed on.
    flags = (
        ("S" if e.get("solver_path") else "-")
        + ("R" if e.get("report_path") else "-")
        + ("P" if e.get("harness_path") else "-")
        + ("r" if e.get("reflect_path") else "-")
    )
    head = (e.get("plan_headline") or "").replace("\n", " ")[:55]
    return (
        f"{is_cur} v{int(v):03d}  it{int(iter_n):02d}  {score_str:>8}  "
        f"{cov:>6}  {flags}  {outcome:<10}  {head}"
    )


def _t_list_versions(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    idx = ctx.version_index
    if idx is None:
        return ToolResult(
            ok=False,
            content="error: version index not available in this harness session",
        )
    scope = str(args.get("scope") or "current_run").strip().lower()
    limit_raw = args.get("limit")
    try:
        limit = int(limit_raw) if limit_raw is not None else 30
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(limit, 200))

    if scope in ("current_run", "current", "run"):
        entries = idx.for_run(ctx.run_id)
        title = f"versions in current run {ctx.run_id!r}"
    elif scope in ("all", "global"):
        entries = idx.all_entries()
        title = "all versions since last 清空全局记忆"
    elif scope in ("best", "top"):
        entries = idx.best(limit)
        title = f"top-{limit} versions by score (lower=better)"
    else:
        return ToolResult(
            ok=False,
            content="error: 'scope' must be current_run | all | best",
        )

    if not entries:
        return ToolResult(ok=True, content=f"({title}: empty)")

    if scope != "best":
        # Newest first for browsing.
        entries = list(reversed(entries))
    entries = entries[:limit]

    header = (
        "  v     iter   score    cov     kinds  outcome     hypothesis\n"
        "  ----  -----  -------  ------  -----  ----------  --------------------"
    )
    rows = [_format_version_row(e, ctx.run_id) for e in entries]
    preamble = [title]
    # Always surface this run's iter→v mapping so the model never confuses
    # vN (global) with "iter N of current run". Costs ~1 line per round.
    if ctx.version_index is not None and ctx.run_id:
        try:
            cur = ctx.version_index.for_run(ctx.run_id)
        except Exception:  # noqa: BLE001
            cur = []
        if cur:
            mapping = ", ".join(
                f"it{int(e.get('iteration', 0)):02d}=v{int(e.get('v', 0)):03d}"
                for e in cur
            )
            preamble.append(
                f"this run's iter↔v map: {mapping}  "
                f"(read_version / restore_draft take vN, not itN)"
            )
    body = "\n".join(preamble + [header] + rows)
    body += (
        "\n('*' = current run; kinds=SRPr → solver/report/plan(harness)/reflect "
        "readable; '-' = not produced. Pass v=<n>/'latest'/'best' to read_version.)"
    )
    return ToolResult(ok=True, content=body)


def _collect_buckets(entries: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for e in entries:
        for k in (e.get("bucket_scores") or {}):
            seen.setdefault(k, None)
    return sorted(seen.keys())


def _t_bucket_scoreboard(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Three modes:

    - no bucket, no v: overview — per-bucket best v + score across scope.
    - bucket=...     : leaderboard for one bucket (top-K by score asc).
    - v=...          : per-bucket table for a single version.
    """
    idx = ctx.version_index
    if idx is None:
        return ToolResult(
            ok=False,
            content="error: version index not available in this harness session",
        )
    scope = str(args.get("scope") or "all").strip().lower()
    bucket = (args.get("bucket") or "").strip() if isinstance(args.get("bucket"), str) else ""
    v_spec = args.get("v")
    try:
        limit = int(args.get("limit") or 8)
    except (TypeError, ValueError):
        limit = 8
    limit = max(1, min(limit, 50))

    # ----- per-version table mode --------------------------------------
    if v_spec is not None:
        entry = idx.resolve(v_spec, current_run_id=ctx.run_id)
        if entry is None:
            return ToolResult(ok=False, content=f"version {v_spec!r} not found in index")
        scores = entry.get("bucket_scores") or {}
        if not scores:
            return ToolResult(
                ok=True,
                content=(
                    f"v{entry['v']:03d}: no bucket scores recorded "
                    f"(outcome={entry.get('outcome', '?')})"
                ),
            )
        uncov = entry.get("bucket_uncovered") or {}
        # Compare to current incumbent (v='best') for delta.
        best_top = idx.best(1)
        best_scores = (best_top[0].get("bucket_scores") if best_top else None) or {}
        is_incumbent = best_top and int(best_top[0].get("v", -1)) == int(entry["v"])
        rows = [
            f"v{entry['v']:03d}  iter={entry.get('iteration')}  "
            f"run={entry.get('run_id', '?')}  total={entry.get('score'):.2f}",
            "  bucket                  score      uncov   Δ vs best",
            "  ----------------------  ---------  ------  ---------",
        ]
        for b in sorted(scores.keys()):
            s = scores.get(b, 0.0)
            u = uncov.get(b, 0)
            bs = best_scores.get(b)
            if is_incumbent or bs is None:
                delta = "    --"
            else:
                d = s - float(bs)
                delta = f"{d:+.2f}"
            rows.append(f"  {b:<22}  {s:>9.2f}  {u:>6}  {delta:>9}")
        return ToolResult(ok=True, content="\n".join(rows))

    # ----- scope selection ---------------------------------------------
    if scope in ("current_run", "current", "run"):
        entries = idx.for_run(ctx.run_id)
        scope_label = f"current run {ctx.run_id!r}"
    else:
        entries = idx.all_entries()
        scope_label = "all versions since last 清空全局记忆"
    entries = [e for e in entries if e.get("bucket_scores")]
    if not entries:
        return ToolResult(ok=True, content=f"(no scored versions in {scope_label})")

    # ----- per-bucket leaderboard mode ---------------------------------
    if bucket:
        ranked = sorted(
            ((e, float(e.get("bucket_scores", {}).get(bucket, float("inf")))) for e in entries),
            key=lambda x: x[1],
        )
        ranked = [(e, s) for e, s in ranked if s != float("inf")]
        if not ranked:
            return ToolResult(
                ok=False,
                content=(
                    f"bucket {bucket!r} not found in any version. "
                    f"Available: {_collect_buckets(entries)}"
                ),
            )
        rows = [
            f"top-{limit} for bucket {bucket!r} in {scope_label} (lower=better)",
            "  rank  v     iter  run                      score      uncov",
            "  ----  ----  ----  -----------------------  ---------  ------",
        ]
        for r, (e, s) in enumerate(ranked[:limit], 1):
            u = (e.get("bucket_uncovered") or {}).get(bucket, 0)
            cur = "*" if e.get("run_id") == ctx.run_id else " "
            rid = (e.get("run_id") or "")[:23]
            rows.append(
                f"  {r:>4}  v{int(e['v']):03d}  it{int(e.get('iteration', 0)):02d} {cur}"
                f" {rid:<23}  {s:>9.2f}  {u:>6}"
            )
        rows.append(
            "(call read_version(v=<n>, kind='solver'|'plan') to inspect a row)"
        )
        return ToolResult(ok=True, content="\n".join(rows))

    # ----- overview mode (default) -------------------------------------
    buckets = _collect_buckets(entries)
    if not buckets:
        return ToolResult(ok=True, content=f"(no bucket scores in {scope_label})")
    best_top = idx.best(1)
    incumbent = best_top[0] if best_top else None
    incumbent_scores = (incumbent.get("bucket_scores") if incumbent else {}) or {}
    rows = [
        f"per-bucket best in {scope_label} (lower=better; incumbent=v"
        f"{int(incumbent['v']):03d} if shown)" if incumbent else
        f"per-bucket best in {scope_label} (lower=better)",
        "  bucket                  best_v  best_score  incumbent  Δ(incumbent − best)",
        "  ----------------------  ------  ----------  ---------  --------------------",
    ]
    floor_sum = 0.0
    floor_n = 0
    incumbent_sum = 0.0
    incumbent_n = 0
    for b in buckets:
        cands = [
            (e, float(e.get("bucket_scores", {}).get(b, float("inf"))))
            for e in entries
            if b in (e.get("bucket_scores") or {})
        ]
        if not cands:
            continue
        cands.sort(key=lambda x: x[1])
        best_e, best_s = cands[0]
        floor_sum += best_s
        floor_n += 1
        inc_s = incumbent_scores.get(b)
        inc_str = f"{inc_s:.2f}" if isinstance(inc_s, (int, float)) else "  --  "
        if isinstance(inc_s, (int, float)):
            incumbent_sum += float(inc_s)
            incumbent_n += 1
        delta_str = (
            f"{(float(inc_s) - best_s):+.2f}"
            if isinstance(inc_s, (int, float))
            else "   --"
        )
        rows.append(
            f"  {b:<22}  v{int(best_e['v']):03d}  {best_s:>10.2f}  {inc_str:>9}  {delta_str:>20}"
        )
    if floor_n:
        avg_floor = floor_sum / floor_n
        rows.append("")
        rows.append(
            f"  桶下界(theoretical floor): Σbest = {floor_sum:.2f} over {floor_n} buckets "
            f"→ avg = {avg_floor:.2f}"
        )
        if incumbent_n == floor_n:
            avg_inc = incumbent_sum / incumbent_n
            rows.append(
                f"  incumbent avg = {avg_inc:.2f}; gap-to-floor = {avg_inc - avg_floor:+.2f} "
                "(每个桶都用各自冠军时的理论最低均分；可作为优化目标参考)"
            )
    rows.append(
        "(use bucket=<name> for the per-bucket leaderboard; v=<n> for one version's table)"
    )
    return ToolResult(ok=True, content="\n".join(rows))


# Per-run cache for read_current_draft. Keyed by run_id; value = (sha, iteration).
# When the draft hasn't changed since the last call, return a short stub instead
# of resending the full ~10KB body. Pass force=true to bypass.
_DRAFT_READ_CACHE: dict[str, tuple[str, int]] = {}


def _draft_read_clear_cache() -> None:
    _DRAFT_READ_CACHE.clear()


def _short_sha(body: str) -> str:
    import hashlib as _hashlib
    return _hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def _t_read_current_draft(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = _draft_path(ctx)
    if not path.exists():
        return ToolResult(
            ok=False,
            content="error: no draft yet; call draft_solver first",
        )
    body = path.read_text(encoding="utf-8")
    sha = _short_sha(body)
    lines = body.splitlines()
    force = bool(args.get("force", False))
    run_key = ctx.run_id or ""
    cached = _DRAFT_READ_CACHE.get(run_key)
    if cached and not force:
        last_sha, last_iter = cached
        if last_sha == sha:
            return ToolResult(
                ok=True,
                content=(
                    f"(unchanged since round {last_iter}; "
                    f"{len(lines)} lines, {len(body.encode('utf-8'))} bytes, sha={sha}). "
                    "Pass force=true to re-read the full body."
                ),
            )
    _DRAFT_READ_CACHE[run_key] = (sha, ctx.iteration)
    if not lines:
        return ToolResult(ok=True, content="(empty draft)")
    width = max(4, len(str(len(lines))))
    numbered = "\n".join(f"L{idx:>{width}}: {line}" for idx, line in enumerate(lines, 1))
    header = (
        f"FULL CONTENT: {len(lines)} lines, {len(body.encode('utf-8'))} bytes, sha={sha} "
        f"(no truncation — L1..L{len(lines)} below is the complete file)."
    )
    return ToolResult(ok=True, content=f"{header}\n{numbered}")


_READ_ONLY_SPECS: list[ToolSpec] = [
    # read_last_report is removed — use read_version(v=-1, kind='report')
    # (or v='best' for the incumbent). The bootstrap baseline is also in
    # the version index, so v=-1 always resolves to the most recent
    # scored attempt in the current run.
    ToolSpec(
        name="profile_dataset",
        description=(
            "查询桶画像。两个可选参数：bucket（指定一个桶）+ field（指定一个字段）。"
            " 无参数 → 默认返回 structural 5 个字段在 10 桶上的横向切片（最常用的入口）。"
            " 只传 bucket=<name> → 返回该桶全部 ~34 个字段（所有 section 嵌套 JSON）。"
            " 只传 field=<name> → 返回该字段在 10 桶上的横向切片 {bucket: value}。"
            " 同时传 bucket + field → 上半段单桶 dump、下半段字段切片。"
            " field 可写裸名（如 'courier_ratio'、'solo_dominates_combo_frac'），"
            "重名时必须用 'section.name' 消歧（如 'score.mean' vs 'willingness.mean'）。"
            "bucket='list' 列 10 桶名；field='list' 列全部字段（带 section 前缀）。"
            "名字允许拼写近似自动纠正。"
            " **本 run 内响应有缓存**：同 (bucket, field) 第二次起返回 '(cached) ...' 短桩——"
            "数据集 run 内不变，不要重复查询同一切片。需要横向对比多个桶的某个字段时，"
            "**务必**用 field=<name> 单次拿全部，禁止逐桶调用 bucket=<x>。"
        ),
        risky=False,
        schema={
            "bucket": "str='' (one of 10 names, or 'list')",
            "field": "str='' (e.g. 'courier_ratio', 'score.mean', 'solo_dominates_combo_frac', or 'list')",
        },
        run=_t_profile_dataset,
    ),
    # ToolSpec(
    #     name="rank_bottlenecks",
    #     description="按各个 case 对评分瓶颈进行排序",
    #     risky=False,
    #     schema={"top_k": "int=4"},
    #     run=_t_rank_bottlenecks,
    # ),
    # retrieve_guidance is intentionally disabled — the BM25 corpus over
    # teacher/skill docs was producing low-signal results that pushed the
    # model toward recycling generic playbook lines. Re-enable only after
    # the underlying corpus is curated.
    # ToolSpec(
    #     name="retrieve_guidance",
    #     description=(
    #         "用 BM25 混合检索 teacher/skill 文档（关键词 + case 标签 + 最近收益排序）。"
    #         "它本质是检索器，不是推理器：请提供可检索关键词。"
    #     ),
    #     risky=False,
    #     schema={
    #         "query": "str",
    #         "target_buckets": "list[str]=[]",
    #         "case_tags": "list[str]=[]",
    #         "keywords": "list[str]=[]",
    #         "limit": "int=6 (1..10)",
    #     },
    #     run=_t_retrieve_guidance,
    #     max_output=12000,
    # ),
    ToolSpec(
        name="list_strategy_templates",
        description="列出可用的策略模板",
        risky=False,
        schema={},
        run=_t_list_strategy_templates,
    ),
    ToolSpec(
        name="read_strategy_template",
        description=(
            "返回 fool/templates/ 下指定策略模板的完整源码。"
            "可选模板（8 个）：solver_beam.py, solver_bitset_lns.py, solver_greedy.py, "
            "solver_loww_regret.py, solver_minimal.py, solver_multi_anchor.py, "
            "solver_output_checker.py, solver_scarce_repair.py。"
            "也接受 bare slug（如 'loww_regret' / 'solver_greedy'），允许拼写近似自动纠正。"
            "示例：read_strategy_template(name='solver_loww_regret.py')。"
        ),
        risky=False,
        schema={"name": "str (required, e.g. 'solver_greedy.py' or bare 'greedy')"},
        run=_t_read_strategy_template,
        max_output=12 * 1024,
    ),
    ToolSpec(
        name="read_version",
        description=(
            "按全局版本号读取历史 solver/report/plan/reflect/harness_full/buckets。"
            "v 接受 int、'latest'、'best'、负数(本 run 倒数)、'v33'/'003' 字符串；"
            "省略 v 默认 'latest'。"
            "kind 必须是六个值之一：solver | report | plan | reflect | harness_full | buckets。"
            " kind=plan 抽 final.plan；harness_full 给整段 transcript；reflect 是 reflector 日志；"
            "buckets 返回该 v 的桶分 JSON（与 bucket_scoreboard 互查）。"
            "先用 list_versions / bucket_scoreboard 找 v。"
        ),
        risky=False,
        schema={
            "v": "int|str='latest' (e.g. 42, 'latest', 'best', -1, 'v33')",
            "kind": "str enum: solver|report|plan|reflect|harness_full|buckets",
        },
        run=_t_read_version,
        max_output=8000,
    ),
    ToolSpec(
        name="bucket_scoreboard",
        description=(
            "按桶查询历史版本得分（lower=better）。三种用法："
            "①不传参=每个桶的最优 v + incumbent 在该桶的差距（横向看 incumbent 在哪些桶有提升空间）；"
            "②bucket=<case_name>=该桶 top-K 版本排行榜；"
            "③v=<n>=指定 v 的所有桶分对照表（与 incumbent 对比）。"
            "默认 scope=all 跨 run；传 scope=current_run 只看本次 run。"
        ),
        risky=False,
        schema={
            "bucket": "str='' (case_name like 'scarce_seed401')",
            "v": "int|str='' (e.g. 42, 'latest', 'best', -1)",
            "scope": "str=all (all|current_run)",
            "limit": "int=8 (1..50)",
        },
        run=_t_bucket_scoreboard,
        max_output=8000,
    ),
    ToolSpec(
        name="list_versions",
        description=(
            "列出可用的历史版本（全局唯一 v + 摘要）。"
            "scope=current_run 默认只看本次 run；scope=all 看自上次清空全局记忆以来全部；"
            "scope=best 按分数排前 K。结果可直接喂给 read_version 的 v 参数。"
        ),
        risky=False,
        schema={
            "scope": "str=current_run (current_run|all|best)",
            "limit": "int=30 (1..200)",
        },
        run=_t_list_versions,
        max_output=8000,
    ),
    ToolSpec(
        name="read_current_draft",
        description=(
            "读取当前本轮 draft.py（带行号）。"
            "**未变化短路**：本 run 内若 draft 自上次调用以来未变（同 sha），"
            "返回 '(unchanged since round N; ... sha=...)' 短桩而不是全文，避免重复 10KB 上下文。"
            "如确需重读全文（如怀疑缓存错位），传 force=true。"
        ),
        risky=False,
        schema={"force": "bool=false (force re-read even if unchanged)"},
        run=_t_read_current_draft,
    ),
    ToolSpec(
        name="memory_write",
        description=(
            "向全局 memory store 追加一条 lesson 笔记（本轮学到的'什么有效/无效'），"
            "供未来轮次/未来 run 复用。示例：memory_write(title='low_w 备份过滤生效', "
            "body='在 low_w 场景对意愿<=0.05 的骑手过滤后行罚分降 8%，"
            "其它桶无回退；下一轮可尝试推广阈值'). "
            "run_id / iteration 一般**留空**——harness 会从当前轮次自动填入。"
        ),
        risky=False,
        schema={
            "title": "str, <=80 chars",
            "body": "str, <=4KB; the actual content future rounds will read",
            "run_id": "str (optional; defaults to harness current run_id)",
            "iteration": "int (optional; defaults to harness current iteration)",
        },
        run=_t_memory_write,
    ),
    ToolSpec(
        name="memory_search",
        description=(
            "BM25-search MEMORY.md + notes/**/*.md. Returns snippet + path+lines. "
            "Snippet defaults to 400 chars per entry; pass max_snippet_chars up to "
            "2000 if you need fuller context — one call is enough, no follow-up read."
        ),
        risky=False,
        schema={
            "query": "str",
            "max_results": "optional int (default 5, max 20)",
            "max_snippet_chars": "optional int (default 400, max 2000)",
        },
        run=_t_memory_search,
        max_output=16 * 1024,
    ),
    ToolSpec(
        name="read_tool_result",
        description=(
            "Continue reading a spilled tool output (seen in <<<TRUNCATED>>> marker). "
            "Example: read_tool_result(uuid=\"04be62b7b3ff473a8b03230039eef8f9\", "
            "start_line=80). Pass uuid as a bare string field — do NOT wrap "
            "params inside another \"args\" string."
        ),
        risky=False,
        schema={
            "uuid": "str, 32-hex (required, from the <<<TRUNCATED>>> marker)",
            "start_line": "int (optional, default 1)",
            "max_lines": "int (optional, default 800, max 2000)",
        },
        run=_t_read_tool_result,
        max_output=64 * 1024,
    ),
    # read_dialog disabled: payloads run ~30KB+ (single jsonl line averages
    # >2KB because tool_result/system content is embedded verbatim) and the
    # tool overlaps with read_version(kind='plan'|'report'|'harness_full').
    # The _t_read_dialog implementation is retained for tests but not
    # registered for LLM use.
]


# --- editor tools ---

DRAFT_FILENAME = "draft.py"
_SNAPSHOTS_DIRNAME = "_snapshots"
import re as _re

_LABEL_RE = _re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _draft_path(ctx: ToolContext) -> Path:
    return ctx.run_dir / DRAFT_FILENAME


def _snapshot_path(ctx: ToolContext, label: str) -> Path:
    return ctx.run_dir / _SNAPSHOTS_DIRNAME / f"{label}.py"


def _t_snapshot_draft(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    label = str(args.get("label", "")).strip()
    if not _LABEL_RE.match(label):
        return ToolResult(
            ok=False,
            content="error: 'label' must match [A-Za-z0-9_-]{1,40}",
        )
    src = _draft_path(ctx)
    if not src.exists():
        return ToolResult(ok=False, content="error: no draft to snapshot; call draft_solver first")
    dst = _snapshot_path(ctx, label)
    dst.parent.mkdir(parents=True, exist_ok=True)
    existed = dst.exists()
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    verb = "overwrote" if existed else "saved"
    return ToolResult(ok=True, content=f"{verb} snapshot {label!r}")


def _t_restore_draft(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    label = str(args.get("label", "")).strip()
    if not _LABEL_RE.match(label):
        return ToolResult(
            ok=False,
            content="error: 'label' must match [A-Za-z0-9_-]{1,40}",
        )
    # New semantics (2026-06-05): for vN labels, prefer the version_index's
    # *submitted solver of round N* so `restore_draft(vN)` matches what
    # `read_version(v=N)` and `list_versions` show. Only fall back to the
    # local pre-patch snapshot when the index has no entry — that fallback
    # is what handles the in-round undo case (restore_draft(vN) during round
    # N itself, before N has been scored and registered).
    src: Path | None = None
    source_kind = "pre-patch snapshot"
    m = _re.fullmatch(r"v(\d{1,3})", label)
    is_v_label = m is not None
    if is_v_label:
        # Normalise zero-padding so v33 ↔ v033 are treated the same.
        label = f"v{int(m.group(1)):03d}"
    if is_v_label and ctx.version_index is not None:
        try:
            entry = ctx.version_index.resolve(label, current_run_id=ctx.run_id)
        except Exception:  # noqa: BLE001
            entry = None
        if entry is not None:
            entry_run_id = str(entry.get("run_id") or "")
            # P0 guard (2026-06-05): vN resolves via the GLOBAL version index,
            # so when the model writes restore_draft(v=1) intending "round 1 of
            # this run", it can silently get a stale solver from a prior run.
            # Refuse the cross-run restore and surface the correct vN for the
            # current run's iter=N (if present), so the model can retry.
            if ctx.run_id and entry_run_id and entry_run_id != ctx.run_id:
                requested_n = int(m.group(1))
                try:
                    cur_entries = ctx.version_index.for_run(ctx.run_id) if hasattr(
                        ctx.version_index, "for_run"
                    ) else []
                except Exception:  # noqa: BLE001
                    cur_entries = []
                same_iter = next(
                    (e for e in cur_entries if int(e.get("iteration", -1)) == requested_n),
                    None,
                )
                # Only refuse when the small vN likely collides with a current-run
                # itN (the typical "v1 means iter 1 of this run" confusion).
                # When there's no such collision the model is plausibly asking
                # for a global v intentionally — let it through.
                if same_iter is not None:
                    cur_v = int(same_iter.get("v", 0))
                    return ToolResult(
                        ok=False,
                        content=(
                            f"error: {label} resolves to prior run {entry_run_id!r} "
                            f"(iter {entry.get('iteration', '?')}, "
                            f"score {entry.get('score', '?')}). Refusing silent "
                            f"cross-run restore because this run also has iter "
                            f"{requested_n} (= v{cur_v:03d}). If you meant 'round "
                            f"{requested_n} of THIS run', use v{cur_v:03d}; if you "
                            f"really want the prior-run version, call it by its "
                            f"current global v explicitly."
                        ),
                    )
            solver_path_str = str(entry.get("solver_path") or "")
            if solver_path_str:
                candidate = Path(solver_path_str)
                if candidate.exists():
                    src = candidate
                    v_num = entry.get("v")
                    try:
                        label = f"v{int(v_num):03d}"
                    except (TypeError, ValueError):
                        pass
                    source_kind = "submitted solver of"
    if src is None:
        local = _snapshot_path(ctx, label)
        if local.exists():
            src = local
    if src is None or not src.exists():
        snaps_dir = ctx.run_dir / _SNAPSHOTS_DIRNAME
        local_snaps = (
            sorted(p.stem for p in snaps_dir.glob("*.py")) if snaps_dir.exists() else []
        )
        recent_indexed: list[str] = []
        if ctx.version_index is not None:
            try:
                recent_indexed = [
                    f"v{int(e.get('v', 0)):03d}"
                    for e in ctx.version_index.all_entries()[-15:]
                ]
            except Exception:  # noqa: BLE001
                recent_indexed = []
        return ToolResult(
            ok=False,
            content=(
                f"error: label {label!r} not found.\n"
                f"  local pre-patch snapshots (this run): {local_snaps}\n"
                f"  recent indexed versions (any run): {recent_indexed}\n"
                "labels are zero-padded to 3 digits, e.g. v033 not v33. "
                "Use list_versions to browse what's available."
            ),
        )
    body = src.read_text(encoding="utf-8")
    dst = _draft_path(ctx)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(body, encoding="utf-8")
    sha = _short_sha(body)
    nbytes = len(body.encode("utf-8"))
    lines = [
        f"restored draft from {source_kind} {label!r} ({nbytes} bytes, sha={sha})"
    ]
    if ctx.best_solver_path is not None and ctx.best_solver_path.exists():
        try:
            incumbent_body = ctx.best_solver_path.read_text(encoding="utf-8")
        except OSError:
            incumbent_body = None
        if incumbent_body is not None:
            if incumbent_body == body:
                lines.append("identical_to_incumbent: yes")
            else:
                inc_sha = _short_sha(incumbent_body)
                lines.append(
                    f"identical_to_incumbent: no (incumbent sha={inc_sha}, "
                    f"{len(incumbent_body.encode('utf-8'))} bytes)"
                )
    else:
        lines.append("identical_to_incumbent: unknown (no incumbent on record)")
    return ToolResult(ok=True, content="\n".join(lines))


def _t_draft_solver(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    code = args.get("code", args.get("content", ""))
    if not isinstance(code, str) or not code.strip():
        return ToolResult(ok=False, content="error: draft_solver requires non-empty 'code'")
    path = _draft_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return ToolResult(ok=True, content=f"wrote {DRAFT_FILENAME} ({len(code)} chars)")


from fool.harness._apply_patch import (
    DRAFT_FILENAME as _APPLY_DRAFT,
    DiffError as _DiffError,
    apply_patch_to_text as _apply_patch_to_text,
)
from fool.harness._block_patch import (
    DRAFT_FILENAME as _BLOCK_DRAFT,
    BlockPatchError as _BlockPatchError,
    apply_blocks_to_text as _apply_blocks_to_text,
)

assert _APPLY_DRAFT == DRAFT_FILENAME, "draft filename mismatch with _apply_patch module"
assert _BLOCK_DRAFT == DRAFT_FILENAME, "draft filename mismatch with _block_patch module"


def _explain_diff_error(
    err: _DiffError, draft_text: str
) -> str:
    """Build a friendly diagnostic for context-match failures.

    The strict matcher in `_apply_patch` raises DiffError without a draft
    slice; this wraps the bare message with a numbered slice around where
    the matcher gave up so the LLM can compare line-by-line.
    """
    base = str(err)
    snippet = err.context_snippet
    if not snippet:
        return base
    draft_lines = draft_text.split("\n")
    needle = next((s for s in snippet if s.strip()), None)
    if needle is None:
        return base
    pos = -1
    for i in range(err.search_start, len(draft_lines)):
        if (
            draft_lines[i] == needle
            or draft_lines[i].rstrip() == needle.rstrip()
            or draft_lines[i].strip() == needle.strip()
        ):
            pos = i
            break
    rows: list[str] = [base]
    if pos == -1:
        rows.append(
            f"first non-blank context line not found in draft from line "
            f"{err.search_start + 1}: {needle!r}"
        )
        # Show the first few lines of the search window so the model can see
        # what draft actually starts with.
        lo = err.search_start
        hi = min(len(draft_lines), lo + 6)
        rows.append(f"draft slice (lines {lo + 1}..{hi}):")
        for i in range(lo, hi):
            rows.append(f"    {i + 1:4d}: {draft_lines[i]!r}")
    else:
        # Walk hunk context alongside draft to find first divergence.
        diverge = None
        for j, h in enumerate(snippet):
            d = draft_lines[pos + j] if pos + j < len(draft_lines) else None
            if d != h:
                diverge = j
                break
        if diverge is None:
            rows.append("(no divergence — matcher ran out of fuzz tolerance)")
        else:
            d_idx = pos + diverge
            rows.append(
                f"first divergence: hunk context[{diverge}] vs draft line {d_idx + 1}:"
            )
            rows.append(f"  hunk wants : {snippet[diverge]!r}")
            rows.append(
                "  draft has  : "
                + (
                    repr(draft_lines[d_idx])
                    if d_idx < len(draft_lines)
                    else "(past EOF)"
                )
            )
            lo = max(0, d_idx - 3)
            hi = min(len(draft_lines), d_idx + 4)
            rows.append(f"draft slice (lines {lo + 1}..{hi}):")
            for i in range(lo, hi):
                marker = " >>" if i == d_idx else "   "
                rows.append(f"{marker} {i + 1:4d}: {draft_lines[i]!r}")
    rows.append(
        "tip: every hunk line must start with '+', '-', or ' ' (single space). "
        "Blank lines in draft must be written as a single ' ' line in the hunk. "
        "Use read_current_draft if unsure of the exact text."
    )
    return "\n".join(rows)


_AUTO_SNAPSHOT_RE = _re.compile(r"^v(\d{3})$")


def _next_auto_snapshot_label(ctx: ToolContext) -> str:
    """Auto-label for an unlabeled snapshot.

    Prefer the current round's global v (allocated by the VersionIndex) so
    snapshots line up 1:1 with read_version / list_versions output. Fall
    back to scanning the snapshots dir when there is no global v (legacy
    paths / tests without a version index).
    """
    if ctx.global_v and ctx.global_v > 0:
        return f"v{ctx.global_v:03d}"
    snaps_dir = ctx.run_dir / _SNAPSHOTS_DIRNAME
    n = 0
    if snaps_dir.exists():
        for p in snaps_dir.glob("v*.py"):
            m = _AUTO_SNAPSHOT_RE.match(p.stem)
            if m:
                n = max(n, int(m.group(1)))
    return f"v{n + 1:03d}"


def _t_apply_patch(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = _draft_path(ctx)
    if not path.exists():
        return ToolResult(
            ok=False,
            content="error: no draft to patch; call draft_solver first",
        )
    envelope = args.get("patch")
    if not isinstance(envelope, str) or not envelope.strip():
        return ToolResult(ok=False, content="error: 'patch' must be a non-empty string")

    original = path.read_text(encoding="utf-8")
    try:
        new_text, fuzz = _apply_patch_to_text(original, envelope)
    except _DiffError as err:
        return ToolResult(ok=False, content=_explain_diff_error(err, original))

    # Auto-snapshot the pre-patch draft so restore_draft(label=vN) undoes this
    # round's edits. _next_auto_snapshot_label returns the same v{global_v}
    # for every call in the same round — so we MUST NOT overwrite, otherwise
    # a multi-patch round would lose the truly-initial state and a later
    # restore would land mid-round. Skip when the snapshot already exists.
    snapshot_label = _next_auto_snapshot_label(ctx)
    snap_dst = _snapshot_path(ctx, snapshot_label)
    snap_dst.parent.mkdir(parents=True, exist_ok=True)
    if not snap_dst.exists():
        snap_dst.write_text(original, encoding="utf-8")

    path.write_text(new_text, encoding="utf-8")

    import difflib as _difflib

    diff = "".join(
        _difflib.unified_diff(
            original.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"{DRAFT_FILENAME} (before)",
            tofile=f"{DRAFT_FILENAME} (after)",
            n=2,
        )
    )
    summary = (
        f"applied patch to {DRAFT_FILENAME}; "
        f"pre-patch draft auto-saved as snapshot {snapshot_label!r} "
        f"(call restore_draft with label='{snapshot_label}' to undo)"
    )
    if fuzz:
        summary += f" (fuzz={fuzz})"
    return ToolResult(ok=True, content=summary + ("\n" + diff if diff else ""))


def _t_block_patch(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    path = _draft_path(ctx)
    if not path.exists():
        return ToolResult(
            ok=False,
            content="error: no draft to patch; call draft_solver first",
        )
    envelope = args.get("blocks")
    if envelope is None:
        # Tolerate callers that pass `patch` (XML body alias) by mistake.
        envelope = args.get("patch")
    if not isinstance(envelope, str) or not envelope.strip():
        return ToolResult(
            ok=False,
            content="error: 'blocks' must be a non-empty string containing "
            "one or more <<<<<<< SEARCH / ======= / >>>>>>> REPLACE sections",
        )

    original = path.read_text(encoding="utf-8")
    try:
        new_text, fuzz = _apply_blocks_to_text(original, envelope)
    except _BlockPatchError as err:
        return ToolResult(ok=False, content=str(err))

    if new_text == original:
        return ToolResult(
            ok=False,
            content="error: blocks matched but produced no change — "
            "SEARCH and REPLACE bodies are identical",
        )

    # See apply_patch above: same-round multi-patch must not overwrite the
    # snapshot, or restore_draft(label=vN) lands mid-round instead of at the
    # round's true initial state.
    snapshot_label = _next_auto_snapshot_label(ctx)
    snap_dst = _snapshot_path(ctx, snapshot_label)
    snap_dst.parent.mkdir(parents=True, exist_ok=True)
    if not snap_dst.exists():
        snap_dst.write_text(original, encoding="utf-8")

    path.write_text(new_text, encoding="utf-8")

    import difflib as _difflib

    new_lines = new_text.splitlines()
    sm = _difflib.SequenceMatcher(
        a=original.splitlines(), b=new_lines, autojunk=False
    )
    added = removed = 0
    first_change_line: int | None = None
    change_spans: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        removed += i2 - i1
        added += j2 - j1
        if first_change_line is None:
            first_change_line = j1 + 1
        change_spans.append((j1, j2))
    line_total_before = original.count("\n") + (0 if original.endswith("\n") else 1)
    line_total_after = new_text.count("\n") + (0 if new_text.endswith("\n") else 1)
    summary = (
        f"applied block_patch to {DRAFT_FILENAME}: "
        f"+{added}/-{removed} lines (file {line_total_before}→{line_total_after}); "
        f"first change at line {first_change_line or '?'}; "
        f"snapshot={snapshot_label!r} (restore_draft to undo)"
    )
    if fuzz:
        summary += f" (fuzz={fuzz})"

    if change_spans:
        previews: list[str] = []
        budget = 800
        truncated = False
        for j1, j2 in change_spans:
            lo = max(0, j1 - 2)
            hi = min(len(new_lines), j2 + 2)
            chunk = "\n".join(f"L{k + 1:>4}: {new_lines[k]}" for k in range(lo, hi))
            if len(chunk) + 1 > budget:
                truncated = True
                break
            previews.append(chunk)
            budget -= len(chunk) + 2
        if previews:
            preview_text = "\n\n".join(previews)
            if truncated:
                preview_text += "\n... (remaining change regions truncated; call read_current_draft if needed)"
            summary += (
                "\n--- post-patch preview (lines around each change) ---\n"
                + preview_text
            )
    return ToolResult(ok=True, content=summary)




import subprocess as _subprocess
import sys as _sys

from genius.solver_executor import DEFAULT_PYTHON_CMD as _PY_CMD

_SMOKE_HARNESS = r"""
import ast
import json
import sys
import importlib.util

draft_path = sys.argv[1]
sample_path = sys.argv[2]
ALLOWED_IMPORTS = {"time", "random", "heapq"}
ALLOWED_TYPING_NAMES = {"List", "Tuple", "Set", "Dict", "Optional", "Iterable"}
EXPECTED_HEADER = "task_id_list\tcourier_id\ttotal_score\twillingness"

with open(draft_path, "r", encoding="utf-8") as fh:
    draft_source = fh.read()

try:
    tree = ast.parse(draft_source)
except SyntaxError as exc:
    print(json.dumps({"ok": False, "msg": f"solver syntax error: {exc}"}))
    sys.exit(0)

solve_defs = 0
for node in tree.body:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name not in ALLOWED_IMPORTS or alias.asname is not None:
                print(json.dumps({"ok": False, "msg": f"unsupported top-level import: import {alias.name}"}))
                sys.exit(0)
    elif isinstance(node, ast.ImportFrom):
        if (
            node.module == "collections"
            and node.level == 0
            and len(node.names) == 1
            and node.names[0].name == "defaultdict"
            and node.names[0].asname is None
        ):
            continue
        if (
            node.module == "typing"
            and node.level == 0
            and all(alias.name in ALLOWED_TYPING_NAMES and alias.asname is None for alias in node.names)
        ):
            continue
        imported = ", ".join(alias.name for alias in node.names)
        prefix = "." * node.level + (node.module or "")
        print(json.dumps({"ok": False, "msg": f"unsupported top-level import: from {prefix} import {imported}"}))
        sys.exit(0)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "solve":
        solve_defs += 1
        if isinstance(node, ast.AsyncFunctionDef):
            print(json.dumps({"ok": False, "msg": "solve must be a normal function"}))
            sys.exit(0)
        args = node.args
        positional = list(getattr(args, "posonlyargs", [])) + list(args.args)
        if (
            len(positional) != 1
            or positional[0].arg != "input_text"
            or args.vararg is not None
            or args.kwarg is not None
            or args.kwonlyargs
        ):
            print(json.dumps({"ok": False, "msg": "solve signature must be solve(input_text)"}))
            sys.exit(0)

if solve_defs == 0:
    print(json.dumps({"ok": False, "msg": "missing top-level solve function"}))
    sys.exit(0)
if solve_defs > 1:
    print(json.dumps({"ok": False, "msg": "multiple top-level solve functions"}))
    sys.exit(0)

spec = importlib.util.spec_from_file_location("draft", draft_path)
module = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(module)
except Exception as exc:
    print(json.dumps({"ok": False, "msg": f"import error: {exc}"}))
    sys.exit(0)

if not hasattr(module, "solve") or not callable(module.solve):
    print(json.dumps({"ok": False, "msg": "missing callable solve()"}))
    sys.exit(0)

with open(sample_path, "r", encoding="utf-8") as fh:
    text = fh.read()

input_lines = [line for line in text.splitlines() if line.strip()]
if not input_lines:
    print(json.dumps({"ok": False, "msg": "input schema failed: empty input"}))
    sys.exit(0)
if input_lines[0].strip() != EXPECTED_HEADER:
    print(json.dumps({"ok": False, "msg": "input schema failed: bad header"}))
    sys.exit(0)
for row_index, raw in enumerate(input_lines[1:], start=2):
    if len(raw.split("\t")) != 4:
        print(json.dumps({"ok": False, "msg": f"input schema failed: row {row_index} must have exactly 4 TAB columns"}))
        sys.exit(0)

# Parse input to build the set of legal task_ids and courier_ids.
legal_task_units = set()
legal_couriers = set()
for raw in input_lines[1:]:  # skip header
    if not raw.strip():
        continue
    cols = raw.split("\t")
    bundle = cols[0].strip()
    if bundle:
        legal_task_units.add(bundle)
    cid = cols[1].strip()
    if cid:
        legal_couriers.add(cid)

try:
    result = module.solve(text)
except Exception as exc:
    print(json.dumps({"ok": False, "msg": f"solve() raised: {exc}"}))
    sys.exit(0)

def _norm_list(value):
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _task_atoms(task_unit):
    return [p.strip() for p in str(task_unit).split(",") if p.strip()]


if not isinstance(result, list):
    print(json.dumps({"ok": False, "msg": "solve() did not return list"}))
    sys.exit(0)
for pair in result:
    if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
        print(json.dumps({"ok": False, "msg": "solve() items must be length-2 list/tuple"}))
        sys.exit(0)
    if not isinstance(pair[0], str):
        print(json.dumps({"ok": False, "msg": "solve() task_id_list must be str"}))
        sys.exit(0)
    courier_field = pair[1]
    if isinstance(courier_field, str):
        pass
    elif isinstance(courier_field, (list, tuple)) and all(isinstance(x, str) for x in courier_field):
        pass
    else:
        print(json.dumps({"ok": False, "msg": "solve() courier field must be str or list/tuple[str]"}))
        sys.exit(0)

# Semantic checks aligned with Genius validation:
# - tasks/couriers may be list/tuple or comma-joined string
# - multi-courier per row allowed (extra notify)
# - no courier may repeat within a row or across rows; no task may repeat across rows
seen_tasks = set()
used_couriers = set()
for raw_tasks, raw_couriers in result:
    task_units = _norm_list(raw_tasks)
    couriers = _norm_list(raw_couriers)
    if not task_units or not couriers:
        print(json.dumps({"ok": False, "msg": "semantic check failed: empty tasks or couriers in row"}))
        sys.exit(0)
    for task_unit in task_units:
        if task_unit not in legal_task_units:
            print(json.dumps({"ok": False, "msg": f"semantic check failed: unknown task_id_list {task_unit!r} (not in input)"}))
            sys.exit(0)
        for tid in _task_atoms(task_unit):
            if tid in seen_tasks:
                print(json.dumps({"ok": False, "msg": f"semantic check failed: duplicate task_id {tid!r}"}))
                sys.exit(0)
            seen_tasks.add(tid)
    row_couriers = set()
    for cid in couriers:
        if cid not in legal_couriers:
            print(json.dumps({"ok": False, "msg": f"semantic check failed: unknown courier_id {cid!r} (not in input)"}))
            sys.exit(0)
        if cid in row_couriers:
            print(json.dumps({"ok": False, "msg": f"semantic check failed: duplicate courier_id {cid!r} within row"}))
            sys.exit(0)
        if cid in used_couriers:
            print(json.dumps({"ok": False, "msg": f"semantic check failed: courier_id {cid!r} reused across rows"}))
            sys.exit(0)
        row_couriers.add(cid)
    used_couriers.update(row_couriers)

print(json.dumps({"ok": True, "msg": f"PASS n={len(result)}"}))
"""


_SMOKE_PREVIEW_CAVEAT = (
    "[caveat] 以上 local_preview 用的是 10-case 离线预览集（mimic_test / sample_10_cases），"
    "**与 Genius 提交时评测的 case 集合不完全重合**——分差可达 ±30%，方向偶尔反向。"
    "请把它当作 sanity check / 方向性信号，**不要**用作 total_score 预测或 <final> 的主要依据。"
    "真正的得分判断依据：历史 Genius 报告 (read_version kind=report|buckets / bucket_scoreboard)"
    " + 跨 bucket 稳定性证据。规则全文见 prefix '本地 smoke 预览 vs Genius 提交评分'。"
)


def _t_smoke_test_solver(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    draft = _draft_path(ctx)
    if not draft.exists():
        return ToolResult(ok=False, content="error: no draft to smoke test")

    samples = sorted(Path(ctx.input_dir).glob("*.txt"))
    if not samples:
        return ToolResult(ok=False, content="error: no *.txt sample in input_dir")
    sample = samples[0]

    harness_path = ctx.run_dir / "_smoke_harness.py"
    harness_path.write_text(_SMOKE_HARNESS, encoding="utf-8")

    try:
        proc = _subprocess.run(
            [_PY_CMD, str(harness_path), str(draft), str(sample)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except _subprocess.TimeoutExpired:
        return ToolResult(ok=False, content="error: smoke test timed out after 30s")

    stdout = proc.stdout.strip()
    if proc.returncode != 0 or not stdout:
        return ToolResult(
            ok=False,
            content=f"error: smoke harness exit={proc.returncode} stderr={proc.stderr.strip()}",
        )
    try:
        import json as _json

        verdict = _json.loads(stdout.splitlines()[-1])
    except Exception:
        return ToolResult(ok=False, content=f"error: malformed verdict: {stdout}")

    shape_ok = bool(verdict.get("ok"))
    shape_msg = str(verdict.get("msg", ""))
    if not shape_ok:
        from fool.harness.smoke_log import append_smoke_log
        append_smoke_log(
            run_dir=ctx.run_dir,
            iteration=ctx.iteration,
            ok=False,
            shape_msg=shape_msg,
        )
        return ToolResult(ok=False, content=f"smoke shape FAIL: {shape_msg}")

    # Shape check passed — run a broader local preview (prefer 10-case mimic)
    # so the model gets an early multi-bucket score signal in the same tool
    # result. Phase 2 is informational only: any failure here is reported
    # in-line but does not flip the smoke verdict.
    preview_block, preview_label, preview_report_path = _smoke_preview_block(ctx)
    body = f"smoke shape PASS: {shape_msg}\n\n{preview_block}\n\n{_SMOKE_PREVIEW_CAVEAT}"

    from fool.harness.smoke_log import append_smoke_log
    append_smoke_log(
        run_dir=ctx.run_dir,
        iteration=ctx.iteration,
        ok=True,
        shape_msg=shape_msg,
        preview_label=preview_label,
        preview_report_path=preview_report_path,
    )
    return ToolResult(ok=True, content=body)


def _smoke_preview_block(ctx: ToolContext) -> tuple[str, str, Path | None]:
    """Build a preview block for smoke_test_solver.

    Preference order:
    1) data/official/mimic_test (10-case online-like set)
    2) data/sample_10_cases (repo baseline 10-case set)
    3) large_seed301 single-case fallback
    """
    for preview_dir, label in (
        (_MIMIC_TEST_DIR, "mimic_10case"),
        (_SAMPLE_10_CASES_DIR, "sample_10_cases"),
    ):
        if _has_cases(preview_dir):
            result = _score_locally_on_dir(
                ctx,
                preview_input_dir=preview_dir,
                preview_report_name=_LOCAL_PREVIEW_MIMIC_NAME,
                preview_label=label,
            )
            return result.content, label, ctx.run_dir / _LOCAL_PREVIEW_MIMIC_NAME
    if _LARGE_SEED301.exists():
        return _t_score_locally(ctx, {}).content, "large_seed301", ctx.run_dir / _LOCAL_PREVIEW_NAME
    return "(local preview skipped: no preview dataset found)", "", None


import tempfile as _tempfile


_LARGE_SEED301 = _FOOL_ROOT / "data" / "official" / "large_seed301.txt"
_MIMIC_TEST_DIR = _FOOL_ROOT / "data" / "official" / "mimic_test"
_SAMPLE_10_CASES_DIR = _FOOL_ROOT / "data" / "sample_10_cases"
_RUN_SUBMISSION = _FOOL_ROOT / "genius" / "run_submission.py"
_LOCAL_PREVIEW_NAME = "_local_preview.txt"
_LOCAL_PREVIEW_MIMIC_NAME = "_local_preview_mimic.txt"


def _has_cases(path: Path) -> bool:
    return path.is_dir() and any(path.glob("*.txt"))


def _score_locally_on_dir(
    ctx: ToolContext,
    *,
    preview_input_dir: Path,
    preview_report_name: str,
    preview_label: str,
) -> ToolResult:
    draft = _draft_path(ctx)
    if not draft.exists():
        return ToolResult(ok=False, content="error: no draft to score; call draft_solver first")
    if not _has_cases(preview_input_dir):
        raise FatalToolError(
            f"score_locally: preview dataset missing or empty at {preview_input_dir}"
        )

    preview_report = ctx.run_dir / preview_report_name
    try:
        proc = _subprocess.run(
            [
                _sys.executable,
                str(_RUN_SUBMISSION),
                "--solver",
                str(draft),
                "--input-dir",
                str(preview_input_dir),
                "--report",
                str(preview_report),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except _subprocess.TimeoutExpired:
        return ToolResult(ok=False, content=f"local preview {preview_label} timed out after 300s")

    if proc.returncode != 0:
        raise FatalToolError(
            f"score_locally: run_submission crashed exit={proc.returncode} "
            f"stderr={proc.stderr.strip()[:800]}"
        )
    if not preview_report.exists():
        raise FatalToolError(
            f"score_locally: run_submission exited 0 but no report at {preview_report}"
        )

    try:
        from fool.genius_file_client import read_report

        report = read_report(preview_report)
    except Exception as exc:
        raise FatalToolError(f"score_locally: cannot parse preview report: {exc}")

    full_report = preview_report.read_text(encoding="utf-8", errors="replace")
    fatal_message = report.get("fatal_message")
    if fatal_message:
        summary_line = f"local_preview {preview_label}: FATAL {fatal_message}"
        return ToolResult(ok=False, content=summary_line + "\n\n" + full_report)

    cases = list(report.get("cases") or [])
    if len(cases) <= 1:
        case = cases[0] if cases else {}
        summary_line = (
            f"local_preview {preview_label}: "
            f"total_score={case.get('score')} uncovered={case.get('uncovered_tasks')} "
            f"covered={case.get('covered')}/{case.get('total_tasks')}"
        )
        return ToolResult(ok=True, content=summary_line + "\n\n" + full_report)

    avg_score = float(report.get("average_score", float("inf")))
    solved_cases = int(report.get("solved_cases", 0))
    total_cases = int(report.get("total_cases", len(cases)))
    uncovered_total = sum(int(case.get("uncovered_tasks", 0)) for case in cases)
    worst_case = max(cases, key=lambda case: float(case.get("score", 0.0)))

    preview_lines = [
        (
            f"local_preview {preview_label}: avg_score={avg_score:.2f} "
            f"solved={solved_cases}/{total_cases} uncovered_total={uncovered_total}"
        ),
        (
            "worst_case="
            f"{worst_case.get('case_name')}({float(worst_case.get('score', 0.0)):.2f})"
        ),
        "per_case:",
    ]
    for case in cases:
        preview_lines.append(
            f"- {case.get('case_name')}: score={case.get('score')} "
            f"covered={case.get('covered')}/{case.get('total_tasks')} "
            f"uncovered={case.get('uncovered_tasks')}"
        )

    return ToolResult(ok=True, content="\n".join(preview_lines) + "\n\n" + full_report)


def _t_score_locally(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    draft = _draft_path(ctx)
    if not draft.exists():
        # LLM-recoverable: it just forgot to draft_solver first this round.
        return ToolResult(ok=False, content="error: no draft to score; call draft_solver first")
    if not _LARGE_SEED301.exists():
        # Infra/env: repo is missing the bundled dataset; iterating more
        # rounds will not fix this — abort the whole loop.
        raise FatalToolError(
            f"score_locally: large_seed301 dataset missing at {_LARGE_SEED301}"
        )

    with _tempfile.TemporaryDirectory() as tmp:
        from shutil import copyfile

        case_dir = Path(tmp)
        copyfile(_LARGE_SEED301, case_dir / "large_seed301.txt")
        return _score_locally_on_dir(
            ctx,
            preview_input_dir=case_dir,
            preview_report_name=_LOCAL_PREVIEW_NAME,
            preview_label="large_seed301",
        )


_EDITOR_SPECS: list[ToolSpec] = [
    # draft_solver and snapshot_draft intentionally NOT registered. Patches are
    # the only edit path, and snapshots are auto-created by apply_patch (one per
    # successful patch, labelled v001/v002/...). Both functions stay defined for
    # tests but are unreachable from the live tool registry.
    ToolSpec(
        name="restore_draft",
        description=(
            "把指定 label 恢复为当前 draft。**`vN` 的语义与 `read_version` / "
            "`list_versions` 完全一致**：若 round N 已完成评分，返回的就是 "
            "round N 最终提交的 solver（incumbent / 历史版本）。若 N 是**当前正在进行**"
            "的轮次（尚未注册到 version_index），则回退到本轮第一次 patch 之前的本地快照"
            "（= 上一轮的最终提交），等价于"
            "『把本轮所有 patch 撤销』。返回包含 bytes / sha / identical_to_incumbent 标记，"
            "**无需再调用 read_current_draft 或 read_version 来核对状态**。"
        ),
        risky=True,
        schema={"label": "str"},
        run=_t_restore_draft,
    ),
    ToolSpec(
        name="block_patch",
        description=(
            "用 SEARCH/REPLACE 块原子地编辑 draft.py：每个块写"
            " '<<<<<<< SEARCH' / 旧片段 / '=======' / 新片段 / '>>>>>>> REPLACE'，"
            "可一次发多块。SEARCH 必须能在 draft 里找到唯一连续切片"
            "（允许整体缩进偏移）；空 SEARCH 表示追加到文件末尾。"
            "返回 unified diff；任一块失败则整批不写盘。"
        ),
        risky=True,
        schema={"blocks": "str (one or more SEARCH/REPLACE blocks)"},
        run=_t_block_patch,
        max_output=8000,
        body_field="blocks",
    ),
    ToolSpec(
        name="smoke_test_solver",
        description=(
            "对当前 draft 做两件事：(1) 在 input_dir 第一个样本上跑 solve()，校验"
            "返回值结构 / 任务-快递员合法性；(2) 形状通过后跑一次本地 10-case "
            "离线预览（mimic_test / sample_10_cases）。**预览集与 Genius 提交评测的 "
            "case 集合不完全重合**，分差可达 ±30%——结果只能当作 sanity check / "
            "方向性信号，不能作为 total_score 预测；详见 prefix '本地 smoke 预览 vs "
            "Genius 提交评分' 一节。返回两段输出 + caveat；ok 标志只反映 (1)。"
        ),
        risky=False,
        schema={},
        run=_t_smoke_test_solver,
    ),
    # score_locally intentionally NOT registered. smoke_test_solver now embeds
    # a local multi-case preview after its shape check, so a single tool call
    # gets the model both signals. _t_score_locally stays defined for tests
    # and as a single-case fallback when multi-case preview sets are missing.
]


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for spec in _READ_ONLY_SPECS:
        registry.register(spec)
    for spec in _EDITOR_SPECS:
        registry.register(spec)
    return registry
