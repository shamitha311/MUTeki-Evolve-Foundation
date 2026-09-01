# Fool Memory Design

Fool calls a stateless LLM API many times per round, so continuity must be explicit.
The implemented memory has **two coexisting stores** plus **per-round transcript files**,
all file-based with BM25 retrieval — no embeddings, no vector store.

Inspired by three patterns:

- **Reflexion** — language feedback from scored trials reused next attempt: <https://arxiv.org/abs/2303.11366>
- **MemGPT** — bounded active context + durable external storage: <https://arxiv.org/abs/2310.08560>
- **ReMe / CoPaw** — `Memory.md` backbone + categorised `notes/*.md` + file-system spill: <https://github.com/agentscope-ai/ReMe/blob/main/docs/copaw_context_design.md>

---

## Storage layout

### Per-dataset learning (`fool/memory_store.py` → `FoolMemory`)

```
out/memory/runs/<dataset_fingerprint>/
├── episodes.jsonl              ← scored attempts (hypothesis, score, outcome)
├── strategy_index.json         ← consolidated repeated-hypothesis tracker
├── session_summaries.jsonl     ← LLM-written round summaries (BM25-indexed)
├── best_solver.py              ← incumbent for this dataset
├── best_report.txt
└── best_meta.json
```

Fingerprint derives from input filenames + content hash so different datasets cannot contaminate one another.

### Global cross-dataset knowledge (`fool/memory_notes.py` → `MemoryNotesStore`)

```
out/memory/
├── MEMORY.md                   ← auto-aggregated index, rebuilt at end-of-run
└── notes/
    ├── preferences.md          ← user/system preferences (hard rules)
    ├── lessons.md              ← validated wins, what works
    ├── try_errors.md           ← validated failures, what to avoid
    ├── key_decisions.md        ← important architecture/algorithm choices
    └── datasets/
        └── <fingerprint>.md    ← dataset-specific facts
```

`MEMORY.md` is index-only (paths + line ranges); knowledge bodies live in `notes/**/*.md`. This mirrors ReMe's `MemorySearch` → `MemoryGet` two-tier pattern.

### Per-run transcript & spill (`out/runs/<run_id>/`)

```
dialog/round_NNN.jsonl          ← every LLM message turn, streamed by DialogWriter
tool_results/<uuid>.txt         ← large tool outputs spilled with <<<TRUNCATED>>> markers
harness_v<NNN>.json             ← full structured transcript per round (debug aid)
solver_v<NNN>.py / report_v<NNN>.txt
```

---

## The 5 LLM-facing memory tools

Registered in `fool/harness/tools.py`; exposed via `ToolContext.memory_notes`.

| Tool | Purpose |
|---|---|
| `memory_search(query, sections?, max_results?)` | BM25 over `notes/**/*.md`; returns `[{path, start_line, end_line, score, snippet}]` |
| `memory_get(path, offset, limit)` | Safe line-range read of a `notes/*.md` file (`.md`-only whitelist, symlink-refused, containment-guarded) |
| `memory_write(section, title, body, run_id, iteration, dataset_fp?)` | Append a note to `notes/<section>.md` with auto-stamped `<!-- run_id=... iteration=... ts=ISO8601 -->` header |
| `read_tool_result(uuid, start_line?, max_lines?)` | Continue reading a spilled tool output after seeing `<<<TRUNCATED>>>` |
| `read_dialog(round, start_line?, max_lines?)` | Read a prior round's `dialog/round_NNN.jsonl` |

In addition, the legacy `retrieve_guidance` tool searches `episodes.jsonl` / `session_summaries.jsonl` for past-run outcomes and remains active.

## Memory Protocol (prompt-enforced)

`fool/harness/prompt.py` embeds a Chinese-first "Memory Protocol" section in every system prompt that mandates:

1. **Before** proposing a new hypothesis: call `memory_search` (global memory recall); when the forced-exploration gate fires, also call `list_strategy_templates` + at least one `read_strategy_template(name=...)`. If search results cite paths, `memory_get` the exact lines — don't guess from snippets.
2. **After** an outcome is confirmed:
   - failed → `memory_write(section="try_error", ...)` with evidence
   - new constraint → `memory_write(section="preference", ...)`
   - won → `memory_write(section="lesson" or "key_decision", ...)`
3. On `<<<TRUNCATED>>>` markers: `read_tool_result(uuid, start_line=N)` to continue; do not conclude from fragments.

## Compaction hook (`fool/harness/session_compactor.py`)

Runs before every LLM call:

1. **Tool-result truncation** — old user messages spilled to `tool_results/<uuid>.txt` (3 KB cap); newest kept at 100 KB.
2. **Token check** — `tiktoken` total; if < 80k threshold, no further action.
3. **Split** — pinned head (system + round header) + recent tail kept; middle batched for compaction.
4. **LLM summary** — `summarizer.complete()` produces a 6-section Chinese plan (目标 / 约束和偏好 / 进展 / 关键决策 / 下一步 / 关键上下文).
5. **Routing** — `## 关键决策` extracted → auto-written to `notes/key_decisions.md` via `MemoryNotesStore.write_note()`.
6. **Callback** — full summary also appended to per-dataset `session_summaries.jsonl` for BM25.

## End-of-run aggregation

`fool_loop.py` calls `MemoryNotesStore.aggregate_index()` at run end. Pure Python (no LLM): scans `notes/*.md`, picks top-N most recent entries per section (by `ts=` in HTML comment), writes `MEMORY.md` with sections like:

```markdown
# MTASA Memory Index
> Last aggregated: 2026-06-01T00:00:00Z

## Active Preferences (top 5)
- stdlib only → notes/preferences.md:5-8
...

## Recent Lessons (top 5)
- willingness sort wins on scarce_couriers → notes/lessons.md:45-58
...
```

The next run reads `MEMORY.md`'s first 80 lines into the round-1 user header as `[Memory Index Head]` so the LLM sees a curated digest immediately.

## Frontend purge buttons (`frontend/`)

| Button | Action |
|---|---|
| 重置 (existing) | `_purge_run_memory()` — wipes per-dataset `<fp>/` dirs + `MEMORY.md`. **Preserves `notes/`** (cross-run wisdom). |
| 清空全局记忆 (new) | `_purge_global_notes()` — destructive: wipes `notes/` + `MEMORY.md`. Confirm dialog. |

## What's deliberately NOT done

- No embeddings, no vector DB — BM25 only.
- No daily `YYYY-MM-DD.md` logs — MTASA's unit is a run, not a day; `aggregate_index` runs at end-of-run.
- `MEMORY.md` is rebuilt mechanically, never LLM-summarised — keeps it fast and idempotent.

## Disclosure

The UI displays the model's structured planning summary (`重点分析`, `原因`, `本轮假设`) and scored outcome. Hidden chain-of-thought is not exposed; displayed reasoning is explicit output requested for review and approval.
