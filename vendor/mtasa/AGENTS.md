# Repository Guidelines

## Project Structure & Module Organization

MTASA is a local, four-module Python system:

- `teacher/`: strategy references. Active docs are `DATA_STRATEGY_PLAYBOOK.md`, `DATASET_FEATURE.md`, and `README.md`. `DATA_STRATEGY_PLAYBOOK.md` is loaded into Fool's harness prompt each round.
- `genius/`: deterministic judge (validation, subprocess execution, fixed `official_like_latest` scoring, report generation).
- `fool/`: iterative LLM loop, per-round harness tooling, and durable memory (`memory_store.py`), plus runnable templates in `fool/templates/`. `fool/judge_fitter.py` exists but is not wired into the current loop.
- `frontend/`: standard-library `http.server` and vanilla HTML/CSS/JS control panel.

Datasets live in `data/`; run artifacts, logs, and reports live in `out/`.

## Build, Test, and Development Commands

Run commands from the repository root:

```bash
python run_local.py
python -m pytest genius/tests
python fool/fool_loop.py --api-type openai --api-key "$OPENAI_API_KEY" --model gpt-4.1-mini --iterations 10 --input-dir data/sample_10_cases --scoring official_like_latest
python genius/genius_judge.py --solver fool/templates/solver_greedy.py --input-dir data/sample_10_cases --scoring official_like_latest --report out/reports/report.txt
```

`python run_local.py` launches the local UI on port `7860` or the next free port. `pytest` exercises judge and harness behavior in `genius/tests/`. The Genius CLI evaluates one solver against a dataset. Solver execution defaults to `python3.9`; install it before validating candidate solvers.

## Coding Style, Runtime Constraints, and Naming

Use Python type hints, `from __future__ import annotations` where modules already follow that pattern, four-space indentation, and standard-library-first solutions. Name modules and functions in `snake_case`, classes in `PascalCase`, and tests as `test_<behavior>.py` with `test_<expected_result>()` functions.

Solver-side hard constraints to preserve:

- Python 3.9 compatible, pure stdlib only, deterministic behavior.
- Solver top-level imports are restricted to `import time`, `import random`, `import heapq`, and `from collections import defaultdict`; do not use `from __future__ import ...` in solver submissions.
- Entrypoint signature is fixed: `solve(input_text: str) -> list[tuple[str, str]]`.
- Input is TAB-delimited with exactly 4 columns: `task_id_list`, `courier_id`, `total_score`, `willingness`.
- `task_id_list` may contain commas for merged bundles (commas are not CSV separators).
- Output must not reuse any `task_id` or `courier_id` across rows; backup couriers also count as used couriers.
- Fixed scoring mode is `official_like_latest` (do not add mode switches).
- Solver file size must be <= 100 KB; local per-case timeout is 30s (aligned to online 10s budget).

No formatter or linter is configured; keep edits consistent with adjacent code.

## Testing Guidelines

Add focused `pytest` coverage for changes to parsing, validation, score computation, report output, or runtime failure handling. Use `tmp_path` for temporary solver and case files (for example, `genius/tests/test_genius_basic.py`).

For solver-strategy changes:

- Run the Genius CLI on `data/sample_10_cases`.
- Review uncovered tasks, invalid rows, and score regressions across all cases (not just one bucket).

## Commit & Pull Request Guidelines

Existing commits are short and informal (`genius fix`, `legacy ver2.0`). For new work, use a concise imperative subject that identifies the component, such as `genius: reject duplicate output rows`. Pull requests should describe behavioral changes, list verification commands, link relevant issues when available, and include UI screenshots only for `frontend/` changes. Do not commit API keys, `.env*` files, or generated artifacts under `out/`.

## Authoritative References

- [`requirements.md`](requirements.md): product-level constraints and module contracts.
- [`skill.md`](skill.md): Fool iteration contract (single-hypothesis rounds, guardrails, bucket-aware progression).
- [`fool/README.md`](fool/README.md): Fool loop and harness behavior.
- [`genius/README.md`](genius/README.md): judge constraints, runtime limits, and scoring policy.
- [`teacher/README.md`](teacher/README.md): teacher module scope.
- [`teacher/DATA_STRATEGY_PLAYBOOK.md`](teacher/DATA_STRATEGY_PLAYBOOK.md): strategy playbook loaded into harness prompts.
