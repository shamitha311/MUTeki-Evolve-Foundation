# Strategy Evolution Engine

Chunk 3 adds a deterministic, application-owned Strategy Evolution Engine. It
learns from completed investigation results and score reports, records a small
history, asks a lightweight reviewer for direction, and emits the next
high-level `Strategy`.

The engine is intentionally independent from Muteki, MTASA, Docker, shell
commands, targets, and the frontend.

## Contract status and stop conditions

The required `docs/INTEGRATION_CONTRACT.md` and `app/models/` were present, so
the hard stop in Section 0 of the Chunk 3 brief was not triggered. The engine
uses the actual project-owned fields:

- `Strategy.objective`, `priorities`, `constraints`, `context`, `revision`, and
  `parent_revision`
- `InvestigationResult.evidence`, `evidence_summary`, `progress_signals`,
  `solved`, and `error`
- `ScoreReport.progress_score`, `progress_level`, `reasons`, `solved`, and
  `stagnated`

The upstream notes contain expected contract conflicts: Muteki's real driver
and event shapes must be normalized by Chunk 4, and the generic evidence model
is intentionally narrower than Muteki's graph/artifact provenance. Those
conflicts do not change this chunk because the engine consumes only the
project-owned normalized contracts. The real upstream execution and isolation
claims remain unverified as documented in `docs/UPSTREAM_NOTES.md`.

The existing conventions were unambiguous: Python, Pydantic contracts, and
pytest. No duplicate models were created.

## Strategy model

The engine emits the existing frozen `app.models.Strategy` model. A strategy
contains high-level intent only:

```text
objective
priorities
constraints
context
revision
parent_revision
```

It has no target, runtime reference, command, worker, Docker, or host
execution field. Target selection remains owned by trusted orchestration.

## Initial strategy generation

`generate_initial_strategy(...)` accepts an objective, priorities, constraints,
and optional trusted context. It creates revision 1 with no parent and sends
the result through the existing fail-closed validation boundary.

The generator does not create commands or infer a target. Context is copied
only as high-level application context and is rejected if it contains forbidden
target-control or execution keys.

## Next strategy generation

`generate_next_strategy(...)` accepts:

1. the previous Strategy;
2. a normalized InvestigationResult;
3. a ScoreReport supplied by the evaluation boundary;
4. prior StrategyMemory history.

The engine does not calculate a score or decide whether a run is solved. It
uses the supplied score and result to choose a direction. It adds a bounded
review record to the next strategy context so later chunks can see why the
strategy changed.

The rules are deterministic and intentionally small:

- useful reconnaissance tends to move to evidence correlation and hypothesis
  testing;
- strong evidence or a high supplied score tends to move to verification;
- authentication and authorization signals keep the next strategy in those
  high-level areas;
- errors and non-progress reduce emphasis on the failed priorities;
- stagnation selects an unexplored catalog direction rather than repeating the
  same priorities.

The catalog is a rule-based default, not an execution plan:
reconnaissance, surface discovery, evidence collection, evidence correlation,
hypothesis testing, authentication, session handling, authorization, input
validation, deeper surface analysis, verification, and clear success evidence.

## Memory

`StrategyMemory` is an append-only in-memory history. It supports:

- recording an iteration;
- retrieving complete history;
- retrieving the latest strategy and result;
- retrieving score history;
- collecting successful and failed directions;
- identifying repeated failures;
- detecting stagnation over a small configurable window.

Each `StrategyMemoryRecord` stores the strategy, normalized result, supplied
score report, iteration number, result summary, successful directions, and
failed directions. Direction outcomes are derived from result text, evidence,
signals, errors, and score movement. This is observation and bookkeeping, not
scoring.

No database or JSON persistence was added because the current project
architecture has no persistence requirement for this chunk.

## Teacher / Reviewer

`review_history(...)` returns a structured `Review` with:

- summary;
- successful directions;
- failed directions;
- unexplored directions;
- recommendation;
- stagnated flag.

The reviewer compares recent outcomes and priorities. It does not execute,
select targets, call Muteki, or modify any contract. Its recommendation is
input to the generator and is also included in the next Strategy context for
auditable lineage.

## Stagnation and diversification

The default stagnation window is two iterations and is configurable. The
engine recognizes:

- explicitly stagnated score reports;
- repeated errors;
- repeated low-change scores with the same result summary;
- repeated priority sets without meaningful score movement.

Solved iterations are never marked stagnated. When stagnation is detected, the
reviewer lists an unexplored direction and the generator changes priorities
while preserving the objective, constraints, and immediate parent revision.
If there is no safe, non-duplicate alternative, generation fails closed with
`StrategyDiversificationRequired` instead of returning the same strategy
forever.

## Deduplication

`strategy_fingerprint(...)` normalizes semantic fields:

- case and punctuation in objective, priorities, and constraints;
- order of priorities and constraints;
- recursively normalized context with sorted mapping keys.

Revision and parent lineage are not identity fields, so a new revision with
the same meaning is still considered a duplicate. No embeddings or
uncontrolled randomness are used.

## Revision tracking

Initial strategies use:

```text
revision = 1
parent_revision = null
```

Every generated next strategy uses:

```text
revision = previous.revision + 1
parent_revision = previous.revision
```

The existing Pydantic model rejects broken lineage.

## Validation and safety

Every generated strategy passes through `app.validation.validate_strategy`.
This preserves the existing fail-closed boundary and rejects target
selection, runtime reference modification, arbitrary destinations, host
execution, shell/command content, Docker instructions, and sandbox escape
content. Invalid generated content is rejected rather than silently
sanitized.

The engine never receives a `SandboxTarget` and never produces one.

## Deterministic and mock mode

`StrategyEngine(seed=...)` exposes a seed configuration point for a future
generator that may need seeded selection. The current rule-based generator
does not use randomness, so identical inputs produce identical outputs for
every seed.

The tests run the engine against the existing Chunk 1 three-round
`MockMutekiAdapter` without importing Muteki internals, Docker, shell
commands, or the frontend. The adapter supplies the fixture outcomes; the
engine reacts to them and preserves revision lineage:

```text
Strategy #1
  reconnaissance + evidence collection
  -> useful reconnaissance result
  -> move to evidence correlation + hypothesis testing

Strategy #2
  evidence correlation + hypothesis testing
  -> strong supplied evidence / higher supplied score
  -> move to verification + clear success evidence

Strategy #3
  verification + clear success evidence
  -> verified success result
```

The example scores belong to the fixture and are not embedded in the engine.

## Extension point for a future LLM generator

`StrategyEngine` is the application-level boundary. A future LLM-backed
implementation can satisfy the same initial/next strategy inputs and return
the same project-owned `Strategy`, then reuse the existing validation,
deduplication, memory, and reviewer boundaries. No provider is selected or
integrated in Chunk 3.

## Verification

The dedicated tests cover initial and next generation, meaningful progress,
failure, strong evidence, memory insertion and retrieval, successful and
failed direction tracking, reviewer output, stagnation, diversification,
semantic deduplication, revision lineage, safety rejection, deterministic
generation, history influence, and the three-round mock loop.

The engine has no frontend changes and no upstream source changes.