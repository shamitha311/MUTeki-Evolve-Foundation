# Fool Module

Fool is the iterative solver-learning module.

Loop summary:

1. Read teacher checklist.
2. Generate or revise solver candidate.
3. Submit candidate to genius.
4. Read score report and teacher guidance.
5. Fit lightweight surrogate of Genius scoring behavior from previous rounds.
6. Keep all attempts, flag guardrail hits, and continue learning.

## Per-round harness

Each iteration runs `fool.harness.run_round`. The LLM drives the round by
calling tools (`read_teacher_checklist`, `read_current_draft`,
`profile_dataset`, `memory_search`,
`list_strategy_templates`, `apply_patch`,
`snapshot_draft`, `restore_draft`, `smoke_test_solver`, ...) until it emits
`<final><plan>{...}</plan></final>`.
The outer loop only submits the resulting solver to Genius and updates the
best pointer.

Runtime policy:

- Fool requires live AI connection and refuses to run if API is not connected.
- No silent fallback to incumbent solver during iteration scoring.
- If generation output is malformed, Fool coerces it into a runnable minimal candidate so the trial is still learnable.

Learning curriculum (ignorant model -> strong model):

1. Stage A (coverage first): reduce uncovered on worst cases.
2. Stage B (stability): avoid zero-coverage and large score spikes.
3. Stage C (refinement): perform minimal, target-case-specific scoring improvements.

Two entry methods:

- CLI: `python fool/fool_loop.py ...`
- Python API: `from fool.fool_loop import run_fool_loop`

Artifacts per run are written to `out/runs/<run_id>/`.

Template policy:

- `fool/templates/*.py` are runnable baseline strategies, not empty placeholders.
- They provide deterministic pure-Python starting points for greedy, beam, multi-anchor, LNS-style local search, low_w regret selection, and scarce repair.
