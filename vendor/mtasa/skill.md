---
name: mtasa-fool-skill-guide
description: Fool iteration guidance for MTASA schema, bucket-aware optimization, and anti-regression learning.
---

# MTASA Fool Skill Guide

This guide is consumed by Fool during iterative solver generation.

## 1) Exact Data Schema

Input rows are TAB-delimited with exactly 4 columns:

1. task_id_list
2. courier_id
3. total_score
4. willingness

Important:
- task_id_list may contain commas because it can be a merged bundle.
- Commas do not mean CSV columns.
- Do not infer or invent fields such as visible, distance, task, courier.

## 2) Output Contract

Function signature must be:

solve(input_text: str) -> list[tuple[str, str]]

Output rows must be deterministic and stable.
Do not emit invalid or placeholder couriers.

## 3) Objective Reality

Official-like objective is sensitive to:
- uncovered tasks (large fixed penalty)
- willingness-weighted recursive row score
- row structure (merge and backup behavior)

Therefore:
- Coverage protection is mandatory.
- Do not chase local row score while dropping many tasks.
- Avoid changes that increase uncovered tasks unless strongly justified.

## 4) Ten Bucket Awareness

Always reason with these benchmark buckets:
- tiny_seed42
- small_seed100
- medium_seed201
- medium_seed202
- medium_seed203
- large_seed301
- large_seed302
- low_willingness_seed501
- scarce_couriers_seed401
- high_noise_seed601

If changing one bucket, state why non-target buckets should remain stable.

## 5) Iteration Policy

Per round, do exactly one isolated hypothesis:
- small parser-safe logic change
- expected impact on specific buckets
- explicit rollback condition

Prefer revising incumbent solver over full rewrites.

Direction continuity rule:
- If the previous round produced a verified improvement on target bucket A
	(negative score delta and no catastrophic guardrail hit), keep the same
	mechanism family for at least the next 1-2 rounds.
- Do not abandon a working direction immediately just because another bucket
	currently has a larger absolute penalty.

Early-round priority rule:
- In early rounds, prioritize large/medium/small/high_noise for broad,
	stable gains and parser/output safety.
- Treat scarce_couriers and low_willingness as hard buckets: enter them after
	baseline stability is established, or when broad buckets are no longer the
	main source of improvement.

## 6) Regression Guardrails

Treat as catastrophic and flag:
- all cases zero coverage
- large uncovered jump vs incumbent
- large average score spike vs incumbent

No-fallback rule for training signal quality:
- Keep catastrophic attempts as negative learning samples.
- Do not erase attempts by replacing them with incumbent results.

## 7) Frequent Failure Modes to Avoid

- Parsing as CSV instead of TAB table.
- Converting willingness to integer and losing ranking signal.
- Greedy rules that enforce disjointness too rigidly and collapse coverage.
- Random broad rewrites without per-case diagnosis.
- Ignoring incumbent strengths (especially scarce and low_w buckets).

## 8) Minimum Thinking Checklist Before Output

1. Parser still matches exact 4-column schema?
2. Coverage likely preserved on large/medium/scarce?
3. Any change that can create zero-coverage behavior?
4. Is this a minimal delta over incumbent?
5. Is rollback condition explicit?

## 9) Process-Trace Template (Imitate Human Solver Thinking)

Before generating code, write a compact internal plan with this structure:

1. Hypothesis: one sentence only.
2. Target buckets: which 1-2 buckets are being optimized.
3. Non-target protection: why other buckets should not regress.
4. Edit plan: 2-4 minimal code edits over incumbent.
5. Safety checks: parser/output invariants that must stay unchanged.
6. Stop/rollback trigger: exact condition for rejecting this round.

After judging each round, summarize:

1. Outcome label: improved / neutral / regressed / rollback.
2. Key evidence: score delta + uncovered delta.
3. Reuse decision: keep this hypothesis family or ban it.

Never repeat a regressed hypothesis verbatim in the next 3 rounds.

## 10) Core Direction Lanes (Not Random Trial)

Use one lane per round, selected by evidence:

1. coverage_recovery:
	- Trigger: recent zero-coverage or uncovered spike rollback.
	- Direction: prioritize reducing uncovered on worst cases first.
2. directed_explore:
	- Trigger: repeated neutral rounds (same score plateau).
	- Direction: switch to a different heuristic family, still parser-safe.
3. incremental_refine:
	- Trigger: no recent catastrophic failures, normal operation.
	- Direction: one minimal-diff optimization over incumbent.

Lane switching discipline:
- When a lane is improving on its target bucket, prefer parameter tightening
	or small structural refinement before switching lanes.
- Switch lanes only with explicit evidence (plateau, regression, or coverage
	risk), and explain why the new lane is more promising than continuing the
	current one.

The lane and target cases must be written in thought trace before code generation.

## 12) Mandatory Pre-Submit Gate (Hard Rule)

Before every full benchmark submission, run a mandatory large_seed301 smoke test.

Rules:
- If large_seed301 smoke test fails, do not submit to full benchmark.
- Fix solver and retest large_seed301 until it passes or retry budget is exhausted.
- Treat each test opportunity as scarce; avoid random rewrites and preserve useful incumbent structure.

This rule is non-optional and overrides speed-oriented shortcuts.

## 11) Learning Ladder For an Ignorant Fool

Assume Fool starts with no domain intuition.

1. Lesson 1 (survival): never output patterns that cause zero coverage.
2. Lesson 2 (structure): keep parser/output invariants fully stable.
3. Lesson 3 (diagnosis): read worst-case uncovered/score deltas and pick one target family.
4. Lesson 4 (controlled edits): perform one heuristic-family change, not full rewrites.
5. Lesson 5 (retention): store failed hypothesis text and avoid repeating it in next rounds.
