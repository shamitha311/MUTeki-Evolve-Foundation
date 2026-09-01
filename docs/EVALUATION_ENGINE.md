# MUTeki-Evolve Evaluation Engine

Chunk 5 — Evaluation / Scoring Engine.

## 1. Responsibility

The Evaluation Engine receives a normalized `InvestigationResult` produced by
the Muteki Adapter and returns a `ScoreReport` that:

- measures investigation progress (0–100, not percentage solved),
- determines the solved condition,
- explains why the score was assigned,
- detects stagnation from prior history.

The evaluator is **read-only**. It does not execute commands, call Muteki,
select targets, modify runtime references, or generate strategies.

---

## 2. Architecture Position

```text
Muteki Adapter
    ↓
InvestigationResult
    ↓
app.evaluation.evaluate()    ← Chunk 5
    ↓
ScoreReport
    ↓
Strategy Memory / Strategy Evolution Engine
```

The evaluator depends on: `app.models.InvestigationResult`, `app.models.ScoreReport`,
`app.models.Evidence`.

It does **not** depend on: Muteki internals, MTASA, Docker, shell, any adapter,
any strategy engine.

---

## 3. Input Contract

```python
InvestigationResult(
    run_id: str,
    solved: bool,                   # verified success flag
    evidence: list[Evidence],       # normalized evidence items
    evidence_summary: str,          # human-readable summary
    progress_signals: list[str],    # investigation progress keywords
    elapsed_seconds: float,
    event_summary: list[str],
    error: str | None,              # error or timeout description
)

Evidence(
    type: str,
    summary: str,
    confidence: float,              # 0.0 – 1.0
    source_event: int | None,
)
```

The evaluator handles gracefully:
- empty evidence, empty signals, empty event_summary
- error / timeout results (via `InvestigationResult.error`)
- invalid confidence values (clamped by `validators.normalize_result`)
- malformed evidence items (silently dropped)

---

## 4. Output Contract

```python
ScoreReport(
    progress_score: float,     # 0.0 – 100.0 (investigation progress)
    solved: bool,              # True only when InvestigationResult.solved is True
    progress_level: str,       # human-readable level (see Section 6)
    reasons: list[str],        # factual explanation of the score
    stagnated: bool,           # True if prior history shows no meaningful progress
)
```

---

## 5. Scoring Model

The score is **deterministic** and **evidence-based**. The same inputs always
produce the same output. No randomness, no language generation.

```
score = signal_base
      + evidence_contribution
      + diversity_bonus
      (clamped to [0.0, 100.0])
```

### Signal base score (0 – 70)

`InvestigationResult.progress_signals` are classified into tiers:

| Tier | Keywords (substring match) | Base score |
|---|---|---|
| Verified | "verified success", "verified", "success", "verification", "resolved" | 70 |
| Strong | "strong evidence", "strong", "correlated", "correlation", "hypothesis", "validated" | 50 |
| Reconnaissance | "reconnaissance", "surface", "recon", "observation", "initial" | 18 |
| Generic | any non-empty signal not matching above | 5 |
| None | no signals | 0 |

The **maximum** across all signals is taken. Multiple signals of the same tier
do not stack.

### Evidence contribution (0 – 30)

For each **unique** evidence item (after deduplication):

```
item_contribution = confidence × type_weight
```

**Type weights:**

| Evidence type | Weight |
|---|---|
| verified_success, verified success | 1.0 |
| verified condition, resolution signal | 0.9 |
| correlation, correlated trace, hypothesis_test | 0.8 |
| authentication, authorization | 0.75 |
| input validation | 0.7 |
| reconnaissance | 0.6 |
| surface_map, surface map | 0.55 |
| observation, fact, unknown | 0.5 |

Total evidence contribution = `sum(item_contributions) × 25`, capped at **30**.

### Diversity bonus (0 – 5)

`min(unique_evidence_types × 3, 5)`

Multiple evidence types indicate broader investigation coverage.

### Hard override: solved

If `InvestigationResult.solved is True`, `progress_score = 100.0` immediately,
regardless of signals or evidence.

### Configurable weights

`EvaluatorConfig` exposes all weights and thresholds:

| Parameter | Default | Description |
|---|---|---|
| `evidence_per_item_scale` | 25.0 | Multiplier for per-item weighted contribution |
| `max_evidence_contribution` | 30.0 | Cap on total evidence contribution |
| `diversity_per_type` | 3.0 | Bonus per unique evidence type |
| `max_diversity_bonus` | 5.0 | Cap on diversity bonus |
| `no_progress_window` | 2 | History window for stagnation detection |
| `meaningful_progress_delta` | 5.0 | Minimum score improvement for "real" progress |

---

## 6. Progress Levels

> **STOP CONDITION 5 compliant**: These strings match the existing UI display values
> in `artifacts/muteki-evolve/src/lib/replay.ts`. Do not rename or reorder
> without updating the UI contract.

| Score range | solved | Progress level |
|---|---|---|
| exactly 0 | False | `started` |
| 1 – 35 | False | `reconnaissance` |
| 36 – 59 | False | `partial evidence` |
| 60 – 84 | False | `strong evidence` |
| 85 – 99 | False | `validated` |
| any | True | `verified success` |

The existing UI fixture uses `"reconnaissance"` (score 28), `"strong evidence"` (score 72),
and `"verified success"` (score 100). These are all produced by the above mapping.

---

## 7. Solved Semantics

`solved = True` **only** when `InvestigationResult.solved is True`.

The evaluator **never** infers solved from:
- high `progress_score`
- high evidence confidence
- many evidence items
- verified-sounding evidence types
- a combination of the above

This is intentional and enforced. `determine_solved()` in `scorer.py` is the
canonical location of this rule.

---

## 8. Evidence Weighting

Evidence is weighted by two factors:
1. **confidence** (0.0 – 1.0, from the InvestigationResult contract)
2. **type weight** (static lookup table in `evidence_analyzer.py`)

10 weak duplicate observations score **lower** than 1 strong correlated finding.

---

## 9. Duplicate Evidence Handling

Deduplication is applied before scoring. Two evidence items are considered
duplicates if their normalized `(type, summary)` key is identical after
whitespace collapse and case folding. The **first occurrence** is kept;
subsequent duplicates are dropped and counted.

Duplicate count is included in `ScoreReport.reasons` when non-zero.

No semantic similarity or embeddings are used. Deduplication is
purely string-based and deterministic.

---

## 10. Stagnation Detection

Stagnation is detected from **prior history** (`Sequence[ScoreReport]`), not
from the current result.

Stagnated when ALL of the following hold over the last `no_progress_window`
(default 2) reports:
- No report has `solved=True`
- Score delta (max − min) < `meaningful_progress_delta` (default 5.0)
- All reports share the same `progress_level`

OR if all recent reports already had `stagnated=True`.

A single unsuccessful iteration is **never** marked stagnated (requires
at least `no_progress_window` items in history).

Solved iterations are **never** stagnated.

---

## 11. Timeout Behavior

When `InvestigationResult.error` is non-None:
- `solved` remains False
- `progress_score` reflects whatever evidence was collected before the error
- An empty timeout (no evidence, no signals) scores 0
- The error string is included in `reasons`
- `stagnated` is not automatically set to True

---

## 12. Malformed Result Behavior

`validators.normalize_result()` pre-processes the result before evaluation:
- Invalid confidence values (NaN, ±∞, out of range) are clamped to [0.0, 1.0]
- Irrecoverably malformed evidence items are silently dropped
- All other fields are passed through unchanged

The evaluator does not raise on malformed input.

---

## 13. Configuration

```python
from app.evaluation import EvaluatorConfig, evaluate

# Default
report = evaluate(result)

# Custom
config = EvaluatorConfig(
    evidence_per_item_scale=20.0,
    max_evidence_contribution=25.0,
    no_progress_window=3,
    meaningful_progress_delta=8.0,
)
report = evaluate(result, config=config)
```

---

## 14. Deterministic Behavior

The same `(InvestigationResult, history, EvaluatorConfig)` always produces the
same `ScoreReport`. No randomness, uncontrolled ordering, or time-based values
are used. This is verified by test case 28 (`TestDeterministicOutput`).

---

## 15. Example

### Input

```python
InvestigationResult(
    run_id="run-example",
    solved=False,
    evidence=[
        Evidence(type="correlation", summary="Leading hypothesis corroborated.", confidence=0.82)
    ],
    evidence_summary="Strong evidence, but success condition not verified.",
    progress_signals=["strong evidence"],
    elapsed_seconds=2.0,
    event_summary=["Hypothesis corroborated."],
)
```

### Score calculation

```
signal_base          = 50.0   ("strong evidence" → STRONG tier)
evidence_contribution= 0.82 × 0.8 × 25 = 16.4  (min(16.4, 30) = 16.4)
diversity_bonus      = min(1 × 3, 5) = 3.0
───────────────────────────────────────
progress_score       = 50.0 + 16.4 + 3.0 = 69.4
progress_level       = "strong evidence"  (60 ≤ 69.4 ≤ 84)
solved               = False
```

### Output

```python
ScoreReport(
    progress_score=69.4,
    solved=False,
    progress_level="strong evidence",
    reasons=[
        "Progress signals observed: strong evidence.",
        "1 unique evidence item(s) contributed to the score.",
        "At least one evidence item has high confidence (≥ 0.75).",
        "Evidence summary: Strong evidence, but success condition not verified.",
        "Strong evidence supports a leading hypothesis; verification is still required.",
        "Success condition has not been verified.",
    ],
    stagnated=False,
)
```

---

## 16. STOP CONDITIONS Resolution

### STOP CONDITION 1 (missing docs / models)
- `docs/INTEGRATION_CONTRACT.md` ✅ present and complete
- `docs/UPSTREAM_NOTES.md` ✅ present
- `app/models/` ✅ present with `InvestigationResult`, `Evidence`, `ScoreReport`
- `docs/MUTEKI_ADAPTER.md` — not present, but `muteki_adapter/` code exists and the mock is well-documented. **Not a hard stop** per Section 0.1 (models are present).
- No models were recreated from scratch. All existing contracts were used.

### STOP CONDITION 2 (incomplete docs)
- `docs/INTEGRATION_CONTRACT.md` is complete for fields this chunk needs.
- `docs/UPSTREAM_NOTES.md`: real Muteki execution is "not yet verified" but Section 0 clarification explicitly states this does not block Chunk 5. The evaluator accepts any well-formed `InvestigationResult` — mock or real.
- **No provisional contracts** affect this chunk's implementation.

### STOP CONDITION 3 (Contract Conflicts affecting solved semantics)
- `docs/UPSTREAM_NOTES.md` has a "Contract Conflicts" section with 3 items.
- None of them touch `InvestigationResult.solved` or `ScoreReport` semantics.
- **No impact on this chunk.**

### STOP CONDITION 4 (real model fields differ from illustrative examples)
- Real `InvestigationResult` fields: `run_id`, `solved`, `evidence`, `evidence_summary`, `progress_signals`, `elapsed_seconds`, `event_summary`, `error`.
- Real `ScoreReport` fields: `progress_score`, `solved`, `progress_level`, `reasons`, `stagnated`.
- All code uses actual field names from `app/models/`. ✅

### STOP CONDITION 5 (fixed progress_level enum from earlier chunk)
- **Triggered.** The UI (`artifacts/muteki-evolve/src/lib/replay.ts`) already uses the strings `"reconnaissance"`, `"strong evidence"`, `"verified success"`.
- The `orchestration/mock_scenario.py` fixture uses the same strings plus an `"awaiting replay"` UI-only state.
- **Resolution:** The evaluator's `determine_progress_level()` uses these exact strings. The full level set added (`"started"`, `"partial evidence"`, `"validated"`) fills gaps not covered by the fixture but does not conflict with existing UI values.

---

## 17. File Structure

```
app/
  evaluation/
    __init__.py          Public API: evaluate(), EvaluatorConfig
    config.py            EvaluatorConfig (weights/thresholds)
    evidence_analyzer.py Deduplication, type weights, EvidenceAnalysis
    scorer.py            Score calculation, level determination, reasons
    stagnation.py        Stagnation detection from ScoreReport history
    validators.py        Input normalization (malformed confidence etc.)
    evaluator.py         Top-level evaluate() orchestrator

tests/
  evaluation/
    __init__.py
    test_evaluator.py    97 tests covering all Section 36 cases + three-round fixture

docs/
  EVALUATION_ENGINE.md  This document
```

---

## 18. Security Requirements

The evaluator is a read-only analysis component. It:

- ✅ does NOT execute commands
- ✅ does NOT call Muteki
- ✅ does NOT start Docker
- ✅ does NOT access the host
- ✅ does NOT select targets
- ✅ does NOT modify targets
- ✅ does NOT modify `runtime_reference`
- ✅ does NOT generate strategies
- ✅ does NOT bypass validation

It only analyzes application-level `InvestigationResult` data.
