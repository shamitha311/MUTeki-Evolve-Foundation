# MUTeki-Evolve — End-to-End Orchestration

## 1. Overview and Architecture

The **Orchestrator** is the central glue component of **MUTeki-Evolve**. It coordinates the complete autonomous investigation loop:

$$\text{GENERATE} \rightarrow \text{VALIDATE} \rightarrow \text{EXECUTE} \rightarrow \text{OBSERVE} \rightarrow \text{EVALUATE} \rightarrow \text{REMEMBER} \rightarrow \text{IMPROVE} \rightarrow \text{REPEAT}$$

The Orchestrator maintains strict isolation between intelligence (Strategy Engine), execution (Muteki Adapter), measurement (Evaluation Engine), and history (Strategy Memory).

```text
                ┌───────────────────┐
                │   Trusted Target  │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │   ORCHESTRATOR    │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ STRATEGY ENGINE   │
                └─────────┬─────────┘
                          │
                     Strategy
                          │
                          ▼
                ┌───────────────────┐
                │    VALIDATOR      │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │  MUTEKI ADAPTER   │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │      MUTEKI       │
                │ Coordinator/Worker│
                └─────────┬─────────┘
                          │
                     Events/Result
                          │
                          ▼
                ┌───────────────────┐
                │ INVESTIGATION     │
                │     RESULT        │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ EVALUATION ENGINE │
                └─────────┬─────────┘
                          │
                     ScoreReport
                          │
                          ▼
                ┌───────────────────┐
                │  STRATEGY MEMORY  │
                └─────────┬─────────┘
                          │
                          ▼
                ┌───────────────────┐
                │ TEACHER / REVIEW  │
                └─────────┬─────────┘
                          │
                   Improved Strategy
                          │
                          └───────────────┐
                                          │
                                          ▼
                                     NEXT ITERATION
```

---

## 2. Inherited & Resolved STOP CONDITIONS

### Inherited Limitations (Chunk 4 / Upstream STOP CONDITION 2)

As documented in `docs/UPSTREAM_NOTES.md` and `docs/INTEGRATION_CONTRACT.md`:
1. **Docker is disabled in this environment** (`REPLIT_DISABLE_DOCKER`).
2. **No live LLM credential** is configured for Muteki.
3. **No authorized external target sandbox** is available.

**Inheritance Logic for Chunk 6**:
- Chunk 6 inherits these environment limitations from Chunk 4.
- The REAL mode code path (`mode="real"`) is fully constructed, wired, and validated by construction.
- Live execution in REAL mode remains **unverified** in this environment due to the inherited constraints.
- Section 48's MOCK mode end-to-end test serves as the primary verified execution path for tests and demonstrations.
- The system honestly reports `mode="real"` status without faking live execution.

### Resolved STOP CONDITIONS Summary (Chunks 1–5)

- **Chunk 1**: Source snapshots verified; vendor repositories kept 100% clean.
- **Chunk 2**: UI contract established with normalized investigation events.
- **Chunk 3**: Strategy Evolution Engine built with rule-based initial/next generation, deterministic memory, and reviewer lineage.
- **Chunk 4**: Muteki Adapter contract and normalization defined; mock adapter implemented with 3-round fixture.
- **Chunk 5**: Evaluation Engine built with evidence weighting, signal scoring, and `progress_level` alignment (`"reconnaissance"`, `"strong evidence"`, `"verified success"`).

---

## 3. Run and Iteration Lifecycle

### Run Status (`RunStatus`)

| Status | Description |
|---|---|
| `CREATED` | Evolution run initialized, target resolved. |
| `RUNNING` | Active iteration executing through Muteki Adapter. |
| `EVALUATING` | `InvestigationResult` being evaluated by Evaluation Engine. |
| `IMPROVING` | Strategy Engine generating improved strategy for next iteration. |
| `SOLVED` | Verified success achieved (`ScoreReport.solved == True`). Terminated. |
| `MAX_ITERATIONS_REACHED` | Iteration budget exhausted (`iteration >= MAX_ITERATIONS`). Terminated. |
| `FAILED` | Run terminated due to validation error, execution crash, or strategy failure. |
| `TIMED_OUT` | Execution exceeded maximum allowed duration. |
| `CANCELLED` | Run cancelled safely by user request. |

### Iteration State (`IterationRecord`)

Each iteration produces an immutable `IterationRecord` containing:
- `iteration`: Iteration sequence number (1-indexed).
- `strategy`: Approved `Strategy` executed in this iteration.
- `result`: `InvestigationResult` produced by the adapter.
- `score`: `ScoreReport` emitted by the Evaluation Engine.
- `started_at` / `completed_at`: ISO-8601 timestamps.

---

## 4. Security Boundaries & Target Control

1. **Trusted Target Boundary**: Targets must be resolved from `TrustedTargetRegistry` via `target_id`. Arbitrary target URLs, raw IP addresses, or client-supplied `runtime_reference` strings are strictly rejected.
2. **Target Immutability**: Once an investigation run begins, `target.id`, `target.name`, `target.description`, and `target.runtime_reference` are frozen. If any component attempts to mutate target metadata, the iteration aborts immediately with a validation failure.
3. **Fail-Closed Strategy Validation**: Before EVERY adapter invocation, the strategy is validated via `app.validation.approve_strategy`. Strategies containing shell commands, host execution paths, Docker instructions, or sandbox escape attempts are rejected.
4. **No LLM Target Control**: The LLM / Strategy Engine generates high-level investigation intent only (`objective`, `priorities`, `constraints`). It has no mechanism to select or modify targets.

---

## 5. Execution Modes (MOCK vs REAL)

- **`MODE=mock`**: Uses `MockMutekiAdapter` backed by deterministic test fixtures. Exercises the complete loop (Generate $\rightarrow$ Validate $\rightarrow$ Execute $\rightarrow$ Observe $\rightarrow$ Evaluate $\rightarrow$ Memory $\rightarrow$ Next Strategy). Fully verified in all environments.
- **`MODE=real`**: Wires the production `MutekiAdapter` to upstream Muteki drivers. Uses real event streams and evidence graphs when supported by environment credentials and container access.

---

## 6. API and Data Flow

### Key Endpoints

- `POST /runs`: Create and launch an evolution run (`target_id`, `objective`, `max_iterations`, `mode`).
- `GET /runs/{id}`: Retrieve current orchestration state (`InvestigationRunState`).
- `GET /runs/{id}/history`: Retrieve complete strategy and evaluation history.
- `GET /runs/{id}/events`: Retrieve normalized stream of `InvestigationEvent`s.
- `POST /runs/{id}/cancel`: Request graceful run cancellation.

---

## 7. Principle of Thin Orchestration

The Orchestrator does not duplicate intelligence, measurement, or execution:
- **Intelligence**: Strategy Evolution Engine (`strategy/`).
- **Execution**: Muteki Adapter (`muteki_adapter/`).
- **Measurement**: Evaluation Engine (`app/evaluation/`).
- **Memory**: Strategy Memory (`strategy/memory.py`).
- **Coordination**: Orchestrator (`orchestration/`).
