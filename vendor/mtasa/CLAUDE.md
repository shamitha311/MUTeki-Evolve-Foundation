# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

MTASA (Meituan Test-set Adaptive Solver Agent) is a four-module local system that iteratively writes and judges pure-Python solver scripts for a courier–task assignment problem. There is no external framework: the frontend is a stdlib `http.server`, the LLM client is custom, and the judge is a deterministic local Python runner.

## Running

```bash
# Start frontend + control panel (auto-picks a free port starting at 7860)
python run_local.py

# Genius judge alone (validates + scores a solver against a dataset)
python genius/genius_judge.py \
  --solver fool/templates/solver_greedy.py \
  --input-dir data/sample_10_cases \
  --scoring official_like_latest \
  --report out/reports/report.txt

# Fool loop alone (LLM-driven iterative solver generation)
python fool/fool_loop.py \
  --api-type openai --api-key "$OPENAI_API_KEY" --model gpt-4.1-mini \
  --iterations 10 --input-dir data/sample_10_cases --scoring official_like_latest
```

Tests:

```bash
python -m pytest genius/tests
```

The frontend persists a sanitized snapshot of the active config to `out/runtime_config.json` (api_key is stripped); `config.example.json` is the template.
The frontend detects supported API keys in `~/.zshrc` (for example `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, and `DEEPSEEK_API_KEY`) without sourcing
that shell file or returning secret values to the browser. Manual API entry remains available.

## Hard runtime constraints (enforced by Genius — do not bypass)

- Solver runs under `python3.6` by default (`genius_python_cmd`). Code generated for solvers must be Python 3.6 compatible — no PEP 585 generic subscripts (`list[...]`, `tuple[...]`), no `int.bit_count()`, no walrus operator, no `from __future__ import annotations`.
- **`from typing import ...` and `import typing` are forbidden in solver files** (any position). The online sandbox has been observed to fail 10/10 cases with case-uniform penalty scores when typing is imported, even though `typing` is stdlib. Generic annotations are therefore disallowed entirely — use bare builtins (`list`, `tuple`, `set`, `dict`) as annotations or omit them. The `genius/smoke.py` static gate enforces this alongside the entrypoint signature.
- Solver file ≤ 100KB; per-case wall time ≤ 10s on the online judge. Local Genius runs ~2.5× slower (python3.6 + local hardware), so the default `max_case_seconds` is **25s** (`DEFAULT_CASE_TIMEOUT_SEC` in `genius/solver_executor.py`), configurable in the frontend's Genius panel. A solver finishing within 25s locally is expected to fit the 10s online budget.
- **`BUDGET_SEC` protocol constant**: every solver must declare a module-level `BUDGET_SEC = 10.0` (literal `10.0`, smoke-enforced) and self-limit against it (`deadline = time.monotonic() + BUDGET_SEC - 0.5`). Before each local run, `genius/solver_executor.materialize_local_solver` writes a tmp copy with the line rewritten to the configured local wall budget (`max_case_seconds`); the original file submitted online still reads `10.0`. Smoke's contract gate rejects any other literal (`9.5`, `10`, `9.0`, …) and requires at least one `time.monotonic()` / `time.time()` / `time.perf_counter()` call somewhere in the file.
- Solvers must be **pure Python stdlib only** — no external libs, no CP-SAT/OR-tools. This rule is currently enforced only by the prompt in `fool/harness/prompt.py` (`_HARD_CONSTRAINTS`); there is no separate skill file for it anymore.
- Solver entrypoint signature is fixed: `def solve(input_text: str) -> list:` — return annotation must be the bare name `list`; smoke rejects `List[Tuple[str, str]]` and other subscripted forms. The return value shape is `[(task_id_list_str, [courier_id, ...]), ...]` — each row a `(str, list-of-str)` pair.
- Scoring mode is fixed to `official_like_latest` (`FIXED_SCORING_MODE` in `genius/scoring_functions.py`). Do not add mode switches.
- Input is **TAB-delimited, 4 columns**: `task_id_list`, `courier_id`, `total_score`, `willingness`. `task_id_list` may contain commas (merged bundle) — commas are not CSV separators. Do not invent extra fields.

## Architecture

Four modules, each is a directory at the repo root. Fool and frontend submit solver `.py` files through the independent `genius/run_submission.py` process and consume Genius-written TXT reports under `out/`; Fool does not import the Genius scoring implementation.

- **teacher/** — Static knowledge base (Markdown). Two access paths:
  - **Inlined into the cached system prefix** (`fool/harness/prompt.py`): only `teacher/DATA_STRATEGY_PLAYBOOK.md`. Editing this file changes every round's prompt — treat it as code, not docs.
  - **Retrievable on demand via the `retrieve_guidance` tool** (BM25 corpus registered in `fool/memory_store.py`): `teacher/MTASA_BOTTLENECK_OPTIMIZATION_GUIDE_CN.md` plus other bucket/feature references. Lives outside the prefix to keep the cached system message small; the forced-exploration gate makes the model call `retrieve_guidance` when stagnation triggers fire.
  - Other files in this directory (`FOOL_AGENT_PROMPT.md`, `README.md`, anything under `teacher/skills/`) are **not** loaded by the current harness — they are stale artifacts from the pre-refactor pipeline.
- **genius/** — Deterministic judge. `genius_judge.py` orchestrates: `validation.py` checks solver file, `solver_executor.py` runs it as a subprocess per case under `python3.6`, `official_like.py` + `scoring_functions.py` compute the recursive score/willingness backup aggregation with uncovered-task penalty, and `report_writer.py` emits the standard TXT report. The scoring is "case-table driven, aligned with autosolver evaluator behavior"; merge-bundle behavior is still partially reverse-engineered (see README "Current Scoring Reverse-Engineering Handoff").
- **fool/** — LLM-driven iteration loop. `fool_loop.py` drives N rounds; each round calls `fool/harness/run_round` (`fool/harness/runner.py`), which runs a tool-calling conversation with the LLM until the model emits `<final>`. The system prefix is built in `fool/harness/prompt.py` (identity + hard constraints + tool specs + output rules + cached `DATA_STRATEGY_PLAYBOOK.md`); per-round user header includes recent history and, for rounds N>1, a `Prior round vNNN` block with the previous round's `final.plan` and report head. Tools live in `fool/harness/tools.py`. After `<final>`, Fool writes the solver, submits it to Genius via `fool/genius_file_client.py`, classifies the outcome, and updates `fool/memory_store.py`. `fool/judge_fitter.py` exists in the tree but is not wired into the current loop. No silent local fallback — if the LLM is not reachable, the run is rejected.
- **Fool token budget** — `max_tokens` is the per-call output budget passed as
  `max_new_tokens` to every harness LLM step within a round (every tool call and
  the final emission share the same ceiling). Do not add ad hoc `2000`/`3000`
  planning budgets; API probe budgets are separate because they are only
  connectivity checks.
- **fool memory** — Two coexisting stores, both file-based, BM25 only (no embeddings/vector store):
  - **Per-dataset (`fool/memory_store.py` → `FoolMemory`)**: scored episodes + compact
    strategy index under `out/memory/runs/<dataset_fingerprint>/` (`episodes.jsonl`,
    `strategy_index.json`, `best_*`, `session_summaries.jsonl`). Retrieval filtered to
    same dataset; blocks unproductive repeated hypotheses.
  - **Global markdown notes (`fool/memory_notes.py` → `MemoryNotesStore`)**: cross-dataset
    knowledge under `out/memory/` — `MEMORY.md` is an auto-aggregated index rebuilt
    after **every iteration** (and at run end) with one line per note in the form
    `- [Title](relative/path.md) — description`. Knowledge bodies are **one file
    per note** under `notes/<section>_<slug>.md`, each with a YAML frontmatter
    (`name`, `title`, `description`,
    `metadata.{type,run_id,iteration,ts,confidence}`). Sections:
    `preference`, `lesson`, `try_error`, `key_decision`. Exposed
    to the agent via 5 tools in `fool/harness/tools.py`:
    `memory_search`, `memory_get`, `memory_write` (LLM-driven), plus `read_tool_result`
    and `read_dialog` for continuing truncated outputs. `fool/harness/prompt.py` contains a
    "Memory Protocol" section that mandates `memory_search` before new hypotheses and
    `memory_write` after outcomes.
  - **Per-round transcript & spill** — `fool/harness/dialog_writer.py` streams every
    message to `out/runs/<run_id>/dialog/round_NNN.jsonl`. Large tool outputs spill to
    `out/runs/<run_id>/tool_results/<uuid>.txt` with `<<<TRUNCATED>>>` markers; the
    agent calls `read_tool_result` to continue reading.
  - **Compaction (`fool/harness/session_compactor.py`)** runs before each LLM call:
    >80k-token transcripts get the older turns summarized by the same LLM into a
    structured Chinese plan; the `## 关键决策` section is auto-routed into the global
    notes as per-decision `notes/key_decision_*.md` files via
    `MemoryNotesStore.write_note()`. Compact summary still
    appended to per-dataset `session_summaries.jsonl` for BM25 retrieval next round.
  - Frontend has two purge buttons: 重置 (clears per-dataset `<fp>/` + `MEMORY.md`,
    keeps `notes/`) and 清空全局记忆 (also wipes `notes/` — destructive).
- **frontend/** — Single-page UI (`index.html` + `app.js` + `styles.css`) served by a stdlib `http.server` in `server.py`. Holds `STATE` in-process behind `STATE_LOCK`; the Fool run executes on a background thread driven by `STOP_EVENT`. The UI polls for status/logs/reports.

### Run artifacts

Each Fool round writes to `out/runs/<run_id>/`:
- `solver_v<NNN>.py` — the submitted solver for that round.
- `report_v<NNN>.txt` — Genius-written report.
- `harness_v<NNN>.json` — full harness transcript (system/user/assistant/tool turns plus the `<final>` plan), written by `fool/harness/session.py`.
- `draft.py` — the working draft the harness edits across steps; overwritten each round.
- `fool.log` — appended status/log lines for the whole run.

`teacher_review_v<NNN>.md` and `notes_v<NNN>.md` listed in older docs are **not** produced by the current harness; only `fool/scripts/summarize_runs.py` (an offline tool) still globs the latter.

Best-so-far is mirrored to `out/solvers/best_solver.py` and `out/reports/best_report.txt`. The frontend's **归档并清空 out** button (`/api/purge_global_notes` in `frontend/server.py`) zips the whole `out/` to `out_backups/out_<timestamp>.zip` and wipes it; there is no narrower per-run reset.

**Per-bucket incumbents** (`fool/bucket_incumbents.py`): each bucket's current champion solver lives at `out/memory/runs/<fp>/buckets/<bucket>/champion.py` with a `meta.json` recording `{score, round, global_v}`. Outcome classification is driven by per-bucket Δ via `fool/bucket_classify.py:classify_round_bucketed`, not by the global average — a round that improves the target bucket relative to *its own* incumbent (0.3% band) is labeled `improved` even if the global average stays flat. The scoreboard's **桶下界** column is the sum of all bucket champions' scores (the theoretical lower bound the average could reach if each case ran its bucket's champion).

## Iteration policy (Fool)

`skill.md` is the authoritative contract for Fool's per-round behavior. Key rules to preserve when editing Fool or its prompts:

- One isolated hypothesis per round; prefer minimal diffs over incumbent rather than full rewrites.
- Planning prompts should be Chinese-first, evidence-based, and progressive across
  rounds: state what the last result taught, what to keep, what to drop, and the
  single mechanism being tested next.
- Catastrophic regressions (zero coverage on all cases, large uncovered jump, large score spike) must be flagged, **not silently replaced with incumbent** — the failed attempt is kept as a negative learning sample.
- Never repeat a regressed hypothesis verbatim within 3 rounds.
- Ten benchmark buckets (`tiny_seed42`, `small_seed100`, `medium_seed201–203`, `large_seed301–302`, `low_willingness_seed501`, `scarce_couriers_seed401`, `high_noise_seed601`) — changes targeting one bucket must justify why others remain stable.

## Notes for editing

- README's `cd /Users/Lithos/Documents/GitHub/MTASA` path is stale; the actual working dir is `/Users/zhuym/Documents/101camp/MTASA`.
- `out/runtime_config.json` is tracked but rewritten every run with `api_key` blanked — expect it to show up dirty in `git status`.
- `requirements.md` (root) is the long-form product spec; consult it before changing module contracts.
