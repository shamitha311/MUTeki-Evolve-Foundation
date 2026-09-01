# System Flow

## Startup

1. User starts `python run_local.py`.
2. Frontend (`frontend/server.py`) binds 7860 or next free port.
3. User configures API + dataset path in browser; clicks 开始运行.

## Per-run init (`fool/fool_loop.py:run_fool_loop`)

4. Create `out/runs/<run_id>/` with `dialog/`, `tool_results/` subdirs.
5. Construct two stores:
   - `FoolMemory(scope=<dataset_fingerprint>)` — per-dataset BM25 over episodes.
   - `MemoryNotesStore(root=out/memory/)` — global markdown notes.
6. Determine baseline `best_score` from `FoolMemory` (or score the bootstrap solver if none).

## Per round (`fool/harness/runner.run_round`)

7. Build `system_prefix` (identity + hard constraints + tool specs + **Memory Protocol** + playbook).
8. Build round header. If `MEMORY.md` exists, embed its first 80 lines as `[Memory Index Head]`.
9. Open `DialogWriter(round_NNN.jsonl)`; log seed messages.
10. Construct `SessionCompactor(memory_notes=..., run_id=..., iteration=i)` (80k token threshold).
11. **Inner loop** (until `<final>` or `max_steps`):
    a. `compactor.maybe_compact(messages)` — spill large tool outputs to `tool_results/<uuid>.txt`; if over threshold, LLM-summarise and route `## 关键决策` into `notes/key_decisions.md`.
    b. `model.complete(messages)` → assistant turn.
    c. Parse: tool call OR `<final>` OR retry.
    d. On tool call: `registry.run(name, ctx, args)`. Tool may read/write `MemoryNotesStore` (via `memory_search` / `memory_get` / `memory_write`) or run-local files (`read_tool_result` / `read_dialog`).
    e. Append assistant + tool-result messages; mirror to `DialogWriter`.

## Post round

12. Fool writes `solver_v<NNN>.py`; submits to Genius via `fool/genius_file_client.py`.
13. Genius runs solver in subprocess, writes `report_v<NNN>.txt`.
14. Fool classifies outcome (improved / regressed / harness_failed); updates `FoolMemory.episodes.jsonl`.
15. If improved, mirror solver/report to `out/solvers/best_solver.py` + `out/reports/best_report.txt`.

## Post run

16. `MemoryNotesStore.aggregate_index()` rebuilds `MEMORY.md` as a top-N digest over `notes/*.md` (pure Python, no LLM).
17. Frontend polls reflect final state; logs visible.

## Persistence summary

| File | Lifetime | Writer | Reader |
|---|---|---|---|
| `out/memory/runs/<fp>/episodes.jsonl` | cross-run, per-dataset | `FoolMemory.record` | `retrieve_guidance` tool |
| `out/memory/runs/<fp>/session_summaries.jsonl` | cross-run, per-dataset | compactor callback | `retrieve_guidance` tool |
| `out/memory/notes/*.md` | cross-run, global | `memory_write` tool + compactor routing | `memory_search` / `memory_get` tools |
| `out/memory/MEMORY.md` | rebuilt every run end | `aggregate_index` | round-header `[Memory Index Head]` |
| `out/runs/<run_id>/dialog/round_NNN.jsonl` | per-run | `DialogWriter` | `read_dialog` tool |
| `out/runs/<run_id>/tool_results/<uuid>.txt` | per-run | `compact_tool_results` spill | `read_tool_result` tool |
