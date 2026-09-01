# Fool Strategy Modules (Proven Patterns)

This note maps local template files to proven solver modules from the ProjectMTAS low-score line.

Primary evidence sources:
- ProjectMTAS `autosolver/NOcpsat/(722.79T1)logic_lns_v4_lloww+normal_large_noise_isolated_stricter.py`
- ProjectMTAS `autosolver/context/VERSION_HISTORY.md`
- MTASA local validator/scorer contract (`genius/validation.py`, `genius/official_like.py`)

## Contract First (must keep)
- Input parse: TAB-delimited with header `task_id_list\tcourier_id\ttotal_score\twillingness`.
- Entrypoint signature: `def solve(input_text: str) -> list:` — bare `list` return annotation only. The online sandbox rejects `from typing import ...` and `import typing`, so do not use `List[...]` / `Tuple[...]` annotations anywhere in the solver file. Drop annotations on internal helpers or use bare `list`/`tuple`/`set`/`dict`.
- Return shape: `[(task_id_list_str, [courier_id, ...]), ...]` — each row is a 2-tuple of (str, list-of-str).
- No duplicated tasks across rows.
- No duplicated courier IDs across primary/backup chains.
- Keep deterministic behavior.

## Template Modules
- `solver_minimal.py`
  - Safe fallback module.
  - Real parser + dedup + deterministic legal greedy assignment.
  - Use when generation quality is unstable.

- `solver_greedy.py`
  - Baseline Formula-A-like greedy ordering.
  - Includes two anchors (density-first and visible-first) and picks the better one.
  - Use for low-risk incremental tuning.

- `solver_multi_anchor.py`
  - Multi-anchor race: formula-density, visible-first, rarity-aware.
  - Use when medium/large cases need robust selection without heavy search.

- `solver_beam.py`
  - Task-driven beam search with per-task candidate pruning.
  - Objective keeps uncovered penalty explicit.
  - Use for large/high-noise targeted exploration.

- `solver_bitset_lns.py`
  - Bitset ruin-repair local search (deterministic seed).
  - Useful when greedy plateaus and local structure changes are needed.

- `solver_loww_regret.py`
  - Low-willingness regret ordering + guarded backup insertion.
  - Backup is strictly capped and only added on strong expected gain.

- `solver_scarce_repair.py`
  - Scarce mode: multi-task-first candidate ordering + uncovered-task hole fill.
  - Coverage-first strategy for courier-scarce buckets.

- `solver_output_checker.py`
  - Contract and duplication guard helper.
  - Use after any generation/refactor step.

## Proven Do/Do-not Summary
- Do:
  - Keep parser and output contract stable.
  - Use strict case routing and guarded candidate acceptance.
  - Prefer small isolated edits over global objective rewrites.

- Do not:
  - Re-enable broad backup behavior globally.
  - Use fake token extraction (regex-only task/courier mining).
  - Replace objective stack with unverified full rewrites in one step.
