# Muteki-Evolve: Implementation Plan
## v4 — Hackathon-Optimized, Parallel-Team Architecture

---

# 0. Project Goal

Build a software-only, sandboxed autonomous security-testing system by combining:

- **Muteki** as the autonomous security investigation/execution swarm.
- **An MTASA-inspired strategy evolution loop** for iterative strategy improvement.

The core loop is:

```text
Trusted SandboxTarget
        ↓
Initial Strategy
        ↓
Strategy Schema Validation
        ↓
Safety / Target Validation
        ↓
Muteki Run
        ↓
Investigation Events
        ↓
Evidence Normalization
        ↓
InvestigationResult
        ↓
Progress Score + Solved Flag
        ↓
Strategy Memory
        ↓
Teacher / Reviewer
        ↓
Improved Strategy
        ↓
Muteki investigates again
        ↓
...
```

The system must operate **only against explicitly registered, isolated hackathon targets**.

## Important Architectural Decisions

### 1. Muteki owns execution

Do **not** make MTASA execute individual shell commands through a custom Muteki worker API.

Instead:

```text
Strategy Engine
      ↓
High-level strategy
      ↓
Muteki run
      ↓
Muteki Coordinator
      ↓
Muteki Workers
      ↓
Investigation events / evidence
```

Muteki remains responsible for concrete investigation actions, worker lifecycle, tool execution, and its existing isolation model.

### 2. MTASA is a design pattern, not a delivery-solver dependency

We are adapting the useful MTASA pattern:

```text
Generate → Execute → Evaluate → Remember → Improve
```

Do **not** port MTASA's delivery-routing solver, Python 3.6 execution environment, delivery constraints, or delivery-specific regression system.

The Strategy Evolution Engine is our project-owned implementation inspired by MTASA's Fool/Teacher/memory iteration.

### 3. The project owns the UI

Do **not** merge Muteki's and MTASA's frontends.

Build one thin project-owned UI that consumes normalized application-level events/results.

Muteki's existing event/run architecture may be used internally by the adapter, but the UI must not depend directly on Muteki internals.

### 4. Target identity is trusted infrastructure

`SandboxTarget` is **our application's trusted target abstraction**.

It is not assumed to be a native Muteki `challenge_id` API.

The generated strategy must never choose, modify, or override the target.

---

# 1. Team / Chunk Structure

## Shared chunks — everyone

### Chunk 1 — Setup + Repository Understanding + Base Architecture

Everyone works together.

Purpose:

- get both upstream projects running
- inspect their actual interfaces
- verify the real Muteki run/event lifecycle
- verify the reusable MTASA iteration boundaries
- establish the common project structure
- freeze interfaces

### Chunk 2 — UI / Demo Console

Everyone works together after Chunk 1.

Purpose:

- create the judge-facing dashboard
- use mock data
- establish the UI contract
- make the final demo visible from the beginning

These two chunks create the common foundation used by all later chunks.

---

## Independent chunks

After Chunk 1's contracts are frozen:

### Chunk 3 — Strategy Evolution Engine

Owner: Member 1

Purpose:

- Strategy generation
- memory
- Teacher/review logic
- strategy deduplication
- stagnation handling

### Chunk 4 — Muteki Integration Adapter

Owner: Member 2

Purpose:

- strategy injection into an actual Muteki run
- event/result collection
- normalized InvestigationResult
- sandbox validation

### Chunk 5 — Evaluation / Scoring Engine

Owner: Member 3

Purpose:

- convert investigation outcomes into objective scores
- progress classification
- efficiency/stagnation signals

### Chunk 6 — End-to-End Orchestration + Final Integration

Owner: Member 4 / integration lead

Purpose:

- connect Chunks 3–5
- run the closed loop
- final tests
- sandbox/guardrail audit
- demo hardening

Chunks 3–5 must be independently runnable against mocks.

---

# CHUNK 1 — SETUP + BASE ARCHITECTURE

## Everyone

### Objective

Get both upstream repositories running independently, understand the **actual current implementations**, and establish stable interfaces that allow Members 1–3 to work independently.

Do **not** build the complete integration yet.

---

## Master Prompt for AI Agent

```text
You are responsible for Chunk 1 of the Muteki-Evolve hackathon project.

Your job is to establish a clean, reproducible foundation for a four-person team.

PROJECT GOAL:

Combine Muteki's autonomous security investigation/execution swarm with an MTASA-inspired iterative strategy-learning loop.

CORE LOOP:

Strategy
→ Muteki investigation
→ InvestigationResult
→ ScoreReport
→ Memory
→ Improved Strategy
→ Muteki investigation again

IMPORTANT ARCHITECTURAL RULES:

1. Do NOT invent a direct "execute arbitrary command on Muteki worker" API.

2. Do NOT make MTASA send shell commands to Muteki workers.

3. MTASA is being used as architectural inspiration for:
   Generate → Execute → Evaluate → Remember → Improve.

4. Muteki remains responsible for:
   - coordinator
   - worker lifecycle
   - tool execution
   - workspace
   - blackboard
   - isolation
   - investigation execution

5. Our Strategy Engine only produces HIGH-LEVEL investigation strategy.

6. Our integration layer must discover the actual current Muteki mechanism for:
   - starting a run
   - supplying operator/task guidance
   - observing events
   - obtaining completion/results

Do not guess these mechanisms.

SETUP:

1. Clone both upstream repositories into:

   vendor/
     muteki/
     mtasa/

2. Do not modify upstream source files.

3. Run Muteki standalone according to its current documentation.

Verify:
   - core starts
   - command deck starts
   - one worker can start
   - one safe local/sandboxed investigation can run
   - actual run lifecycle is understood
   - actual event stream is understood
   - actual result/success representation is understood

4. Run MTASA standalone according to its current documentation.

Verify:
   - its iteration loop starts
   - its sample can run
   - Fool behavior is understood
   - Teacher behavior is understood
   - memory behavior is understood
   - Genius/evaluation behavior is understood
   - delivery-specific components are identified

5. Explicitly document which MTASA components are:
   REUSABLE:
      - iteration concept
      - Fool/Teacher reasoning pattern
      - memory concept
      - experiment history

   NOT PART OF FINAL SYSTEM:
      - delivery solver
      - delivery constraints
      - Python 3.6 runtime
      - delivery-specific Genius harness
      - delivery regression
      - delivery-specific frontend

6. Create project-owned directories:

   app/
     models/
     strategy/
     muteki_adapter/
     evaluation/
     orchestration/
     ui/

   tests/
   docs/

7. Create docs/UPSTREAM_NOTES.md.

It must contain:

MUTeki:
   - actual entrypoints
   - actual run lifecycle
   - actual coordinator behavior
   - actual worker behavior
   - actual blackboard/event behavior
   - actual event subscription/retrieval mechanism
   - actual operator/directive/task injection mechanism
   - actual completion/result mechanism
   - isolation mechanism

MTASA:
   - actual Fool lifecycle
   - actual Teacher lifecycle
   - actual memory lifecycle
   - actual evaluation lifecycle
   - reusable pieces
   - delivery-specific pieces

8. Create docs/INTEGRATION_CONTRACT.md.

Freeze the following semantic interfaces.

SandboxTarget:

   {
     id: string,
     name: string,
     description: string,
     runtime_reference: string
   }

The runtime_reference is trusted infrastructure data.
It must never be generated or modified by the Strategy Engine.

Strategy:

   {
     objective: string,
     priorities: list[string],
     constraints: list[string],
     context: dict,
     revision: int,
     parent_revision: optional[int]
   }

Strategy validation pipeline:

   Generated Strategy
          ↓
   Schema Validation
          ↓
   Safety / Target-Control Validation
          ↓
   Approved Strategy
          ↓
   Muteki Adapter

Validation must happen BEFORE the strategy reaches Muteki.
The Muteki adapter must still fail closed as a second safety boundary.

InvestigationEvent:

   {
     sequence: int,
     timestamp: string,
     type: string,
     run_id: string,
     worker_id: optional[string],
     summary: string
   }

Evidence:

   {
     type: string,
     summary: string,
     confidence: float,
     source_event: optional[int]
   }

InvestigationResult:

   {
     run_id: string,
     solved: bool,
     evidence: list[Evidence],
     evidence_summary: string,
     progress_signals: list[string],
     elapsed_seconds: float,
     event_summary: list[string],
     error: optional[string]
   }

ScoreReport:

   {
     progress_score: float,
     solved: bool,
     progress_level: string,
     reasons: list[string],
     stagnated: bool
   }

IMPORTANT SEMANTIC RULE:

progress_score is a 0–100 measure of investigation progress.
It does NOT mean "percent solved".

solved is an independent boolean representing whether the target's
verified success condition has been achieved.

9. Define the adapter semantics:

   run_strategy(
       target: SandboxTarget,
       strategy: Strategy
   ) -> InvestigationResult

IMPORTANT:

SandboxTarget is selected by trusted orchestration code.
The Strategy object cannot contain a target override.

10. Define event streaming semantics separately:

   subscribe_events(run_id)
       -> stream[InvestigationEvent]

The exact implementation may be polling, SSE, or another mechanism depending on what the actual Muteki source supports.

Do not invent an implementation before inspecting Muteki.

11. Create mock implementations of:
   - Strategy
   - Muteki adapter
   - InvestigationResult
   - InvestigationEvent
   - ScoreReport

12. Create a fake three-round scenario so Chunks 3–5 can be developed without real Muteki.

Example:

Round 1:
   progress = reconnaissance

Round 2:
   progress = strong evidence

Round 3:
   solved = true

13. Define target guardrails:

   - only trusted SandboxTarget values are accepted
   - Strategy cannot select the target
   - Strategy cannot modify runtime_reference
   - no host execution
   - no alternate Docker execution path
   - no arbitrary target supplied by an LLM
   - all actual investigation happens through Muteki's existing execution/isolation architecture

14. Create docs/SETUP.md containing exact commands.

15. Do not modify either upstream repository.

16. Do not merge existing frontends.

17. Before finishing this chunk, run a 60-second architecture review with the team:
   - confirm actual Muteki strategy-injection mechanism
   - confirm actual Muteki event mechanism
   - confirm actual Muteki completion/result mechanism
   - confirm MTASA reusable boundaries
   - freeze the contract

The most important output is not code.
It is a VERIFIED integration contract based on the actual repositories.
```

---

## Deliverables

- `vendor/muteki/`
- `vendor/mtasa/`
- `app/` skeleton
- `tests/` skeleton
- `docs/UPSTREAM_NOTES.md`
- `docs/INTEGRATION_CONTRACT.md`
- `docs/SETUP.md`
- working standalone Muteki
- working standalone MTASA
- mock interfaces
- fake three-round scenario
- license notes

---

## Merge Checks

Before starting independent chunks:

- [ ] Both upstream projects run.
- [ ] Vendor source is untouched.
- [ ] Actual Muteki run lifecycle verified from source.
- [ ] Actual Muteki event mechanism verified from source.
- [ ] Actual Muteki strategy/task/directive injection mechanism verified from source.
- [ ] Actual Muteki completion/result mechanism verified.
- [ ] Actual evidence extraction mechanism verified.
- [ ] No fake command-execution API exists.
- [ ] `SandboxTarget` is explicitly our application abstraction.
- [ ] Strategy cannot control target selection.
- [ ] Strategy validation occurs before Muteki sees the strategy.
- [ ] A real Strategy A run has been demonstrated.
- [ ] A real Strategy B run has been demonstrated.
- [ ] Strategy A and B produce observable behavioral differences.
- [ ] The hard integration gate has passed.
- [ ] Chunks 3–5 can run using mocks.
- [ ] Everyone uses the same base commit.
- [ ] Integration contract is frozen.

---

# CHUNK 2 — UI / DEMO CONSOLE

## Everyone

### Objective

Build a thin project-owned judge-facing dashboard.

Do not merge Muteki's frontend with MTASA's frontend.

The UI must be usable with mock data before real integration exists.

---

## Master Prompt for AI Agent

```text
You are responsible for Chunk 2.

Build the project-owned UI for Muteki-Evolve.

IMPORTANT:

Do NOT:
- merge Muteki's frontend
- merge MTASA's frontend
- import Muteki frontend components
- import MTASA frontend components
- expose Muteki internals directly to the UI

Build a thin dashboard consuming only project-owned application contracts.

The UI must display:

1. Sandbox target name
2. Overall objective
3. Current strategy
4. Strategy revision
5. Parent revision
6. Current iteration
7. Muteki run status
8. Live/near-live investigation events
9. Evidence/progress
10. Current score
11. Score history
12. Strategy evolution
13. Final solved/unsolved state
14. Errors/timeouts

Create components:

- TargetCard
- ObjectiveCard
- StrategyCard
- InvestigationTimeline
- EvidencePanel
- ScorePanel
- ScoreHistory
- StrategyHistory
- SystemStatus

Use mock data initially.

Implement a local replay mode containing at least:

Round 1:
   Strategy A
   reconnaissance
   score 20

Round 2:
   Strategy B
   strong evidence
   score 60

Round 3:
   Strategy C
   solved
   score 100

The UI must handle:

- no active run
- Muteki unavailable
- evaluator failure
- incomplete evidence
- timeout
- finished run
- empty event stream

The UI must never:
- select arbitrary targets
- modify SandboxTarget.runtime_reference
- execute commands
- make security decisions

Keep the frontend minimal and reliable.

The final UI should be optimized for a 2–3 minute judge demo rather than being a full security operations platform.

Create docs/UI_CONTRACT.md.
```

---

## Deliverables

- project-owned dashboard
- replay/demo mode
- live-data interface
- investigation timeline
- score history
- strategy history
- evidence panel
- error states
- responsive layout
- `docs/UI_CONTRACT.md`

---

## Merge Checks

- [ ] UI works without Muteki.
- [ ] UI works without MTASA.
- [ ] UI works entirely with mock data.
- [ ] UI imports only project-owned models/contracts.
- [ ] UI does not import Muteki internals.
- [ ] UI does not import MTASA internals.
- [ ] No arbitrary target selection.
- [ ] Three-round replay works.
- [ ] UI handles Muteki failure gracefully.
- [ ] UI is ready before individual chunks are merged.

---

# CHUNK 3 — STRATEGY EVOLUTION ENGINE

## Individual Owner

### Objective

Implement the MTASA-inspired iterative strategy-learning layer.

The engine produces **high-level strategies**, not shell commands.

---

## Master Prompt for AI Agent

```text
You are responsible for Chunk 3.

Implement the Strategy Evolution Engine against docs/INTEGRATION_CONTRACT.md.

Do NOT depend on a real Muteki installation.

Use mock InvestigationResult and ScoreReport objects.

IMPORTANT:

We are adapting the MTASA iterative pattern, not porting its delivery solver.

Core loop:

previous strategy
→ investigation result
→ score
→ memory
→ next strategy

Implement:

1. Strategy representation.

2. Initial strategy generation.

3. Next-strategy generation based on:
   - objective
   - context
   - previous strategy
   - previous score
   - evidence summary
   - previous strategy history

4. Minimal strategy memory.

Start with a simple local history:

   iteration
   strategy
   score
   result_summary
   successful_directions
   failed_directions

Do NOT spend significant time porting MTASA's full memory implementation.

Only reuse MTASA memory code if it can be integrated cleanly in under 30 minutes.

5. Teacher/review behavior.

If multiple iterations stagnate:
   - summarize what has failed
   - identify unexplored directions
   - produce a revised strategy

6. Deduplication.

Prevent nearly identical strategies from being repeatedly generated.

7. Revision tracking.

Every strategy contains:
   - revision
   - parent_revision

8. Strategy validation.

Reject strategies containing:
   - target override
   - arbitrary destination
   - host execution instructions
   - sandbox escape instructions

9. Strategy generation must remain high-level.

Example acceptable strategy:

   {
     "objective": "...",
     "priorities": ["authentication", "authorization"],
     "constraints": ["stay within sandbox"],
     "context": {...}
   }

Do NOT generate a list of shell commands.

10. Deterministic mock mode.

Given a fixed seed and fixed history, output must be reproducible.

11. Tests:

   - initial strategy
   - strategy after progress
   - strategy after failure
   - stagnation review
   - duplicate strategy
   - revision tracking
   - invalid target-control content
   - deterministic mock behavior

Do not:
- call Muteki
- execute commands
- implement scoring
- modify UI
- modify upstream MTASA
- modify upstream Muteki
```

---

## Deliverables

- `app/strategy/`
- strategy model
- strategy generator
- strategy memory
- Teacher/review logic
- stagnation handling
- strategy validation
- unit tests
- sample strategy history
- `docs/STRATEGY_ENGINE.md`

---

## Merge Checks

- [ ] Runs without Muteki.
- [ ] Runs without UI.
- [ ] Uses only frozen contracts.
- [ ] Produces high-level strategies.
- [ ] Never executes commands.
- [ ] Cannot select target.
- [ ] Strategy changes based on previous results.
- [ ] Repeated failure causes diversification.
- [ ] Revision/parent revision are correct.
- [ ] UI can render its output directly.

---

# CHUNK 4 — MUTeki INTEGRATION ADAPTER

## Individual Owner

### Objective

Connect the Strategy Engine to the **actual Muteki architecture**.

This chunk must not recreate Muteki's worker system.

---

## Master Prompt for AI Agent

```text
You are responsible for Chunk 4.

Implement the Muteki Integration Adapter against docs/INTEGRATION_CONTRACT.md.

CRITICAL:

Do NOT:
- create an arbitrary shell execution API
- create a second Docker executor
- bypass Muteki's coordinator
- bypass Muteki's worker lifecycle
- modify Muteki source
- invent a challenge_id API
- allow an LLM-generated strategy to choose the target

The adapter's responsibility is:

Trusted SandboxTarget
        +
PRE-VALIDATED High-level Strategy
        ↓
Actual Muteki run/task/directive mechanism
        ↓
Muteki autonomous investigation
        ↓
Actual Muteki event/result mechanism
        ↓
Normalized InvestigationResult

FIRST:

Use docs/UPSTREAM_NOTES.md to identify the actual Muteki mechanisms discovered in Chunk 1.

If the mechanism documented there is inconsistent with the current source:
STOP and correct the documentation before implementing.

Implement:

run_strategy(
    target: SandboxTarget,
    strategy: Strategy
) -> InvestigationResult

The target must be selected by trusted orchestration code.

The strategy must not be allowed to:
- change target
- change runtime_reference
- specify arbitrary external hosts
- bypass sandbox restrictions

Implement event normalization:

subscribe_events(run_id)
    -> stream[InvestigationEvent]

Use the actual Muteki event mechanism discovered in Chunk 1.

Normalize:
- run ID
- worker ID where available
- event sequence
- timestamps
- useful event summaries
- completion state
- evidence
- elapsed time
- errors

Create a fake adapter for independent development.

Add tests for:

- successful sandbox run
- unsuccessful run
- Muteki unavailable
- malformed result
- invalid target
- strategy target override
- timeout
- event-stream interruption

The adapter must fail closed.

If target validation fails:
    DO NOT EXECUTE.

If Muteki cannot be reached:
    return a controlled error.

Do not leak Muteki-specific objects outside this module.

Do not modify UI.
Do not implement scoring.
Do not implement strategy generation.
```

---

## Deliverables

- `app/muteki_adapter/`
- actual Muteki adapter
- mock adapter
- event normalization
- result normalization
- target validation
- timeout handling
- tests
- `docs/MUTEKI_ADAPTER.md`

---

## Merge Checks

- [ ] Uses actual Muteki run mechanism.
- [ ] Uses actual Muteki strategy/task/directive mechanism.
- [ ] Uses actual Muteki event mechanism.
- [ ] No fake command execution endpoint.
- [ ] No second worker manager.
- [ ] No second Docker execution system.
- [ ] Target comes only from trusted orchestration.
- [ ] Invalid target fails before execution.
- [ ] Real sandbox run succeeds.
- [ ] Event stream is observable.
- [ ] InvestigationResult contains enough information for scoring.
- [ ] UI can consume normalized events/results.
- [ ] Muteki-specific internals remain inside adapter.

---

# CHUNK 5 — EVALUATION / SCORING ENGINE

## Individual Owner

### Objective

Turn investigation outcomes into a simple, meaningful learning signal.

The evaluator must reward actual progress rather than endless reconnaissance.

---

## Master Prompt for AI Agent

```text
You are responsible for Chunk 5.

Implement the Evaluation / Scoring Engine against docs/INTEGRATION_CONTRACT.md.

Input:

InvestigationResult

Output:

ScoreReport

Do NOT build a complicated mathematical scoring framework.

Use a simple 0–100 progress scale.

Default progression:

0   = no meaningful progress
20  = useful target/surface understanding
40  = meaningful attack-surface discovery
60  = strong verified evidence / vulnerability hypothesis
80  = issue reproduced/validated in sandbox
100 = target objective solved / verified success condition

Adapt the exact mapping to the REAL signals available from Muteki, as documented in Chunk 1.

IMPORTANT SEMANTIC DISTINCTION:

progress_score = how much useful investigation progress was made.
solved = whether the target's verified success condition was achieved.

Never interpret progress_score as "percent solved".

Important:

If the target uses a verified success condition/flag,
treat that as the strongest solved signal.

Evidence should be retained in normalized form where possible:
   - evidence type
   - concise summary
   - confidence
   - source event

The evaluator may apply a small efficiency penalty, but:

- do not reward command volume
- do not reward random enumeration indefinitely
- do not score based on raw exploit payload contents
- do not make time more important than genuine progress
- preserve a useful gradient between failure and success

Implement:

1. score(result)

2. progress classification

3. human-readable score reasons

4. solved detection

5. stagnation signal

6. score history comparison

Tests:

- zero progress
- reconnaissance progress
- strong evidence
- reproduced issue
- solved
- malformed result
- repeated no-progress result
- timeout

Do not:
- call Muteki
- generate strategies
- modify UI
- execute commands

If adapting any MTASA stagnation concept, normalize it to this project's score model instead of importing delivery-specific assumptions.
```

---

## Deliverables

- `app/evaluation/`
- ScoreReport implementation
- score function
- progress classifier
- stagnation detection
- tests
- `docs/EVALUATION.md`

---

## Merge Checks

- [ ] Works with mock InvestigationResult.
- [ ] No Muteki dependency.
- [ ] Score is always 0–100.
- [ ] Solved state is clearly defined.
- [ ] Reconnaissance cannot dominate the score.
- [ ] Repeated useless activity does not inflate score.
- [ ] Strategy Engine can consume ScoreReport.
- [ ] UI can display ScoreReport.
- [ ] No raw exploit payload storage is required.

---

# CHUNK 6 — END-TO-END ORCHESTRATION + FINAL INTEGRATION

## Individual Owner / Integration Lead

### Objective

Merge Chunks 3–5 and prove the autonomous closed-loop system.

Do not redesign the architecture here.

---

## Master Prompt for AI Agent

```text
You are responsible for Chunk 6.

Integrate the completed Chunks 3, 4 and 5 using docs/INTEGRATION_CONTRACT.md.

Do not redesign the architecture.

FINAL LOOP:

1. Load a trusted SandboxTarget.

2. Generate initial Strategy.

3. Start a Muteki run using the actual Muteki integration mechanism.

4. Muteki autonomously investigates the sandbox.

5. Collect InvestigationEvents.

6. Produce InvestigationResult.

7. Evaluate it using the Evaluation Engine.

8. Store result + score in Strategy Memory.

9. Generate the next Strategy.

10. Repeat.

Start with exactly 3 iterations for the first end-to-end demo.

Verify:

- strategies actually differ when outcomes differ
- strategies pass schema + safety validation before Muteki
- Muteki receives the validated strategy guidance
- Muteki actually executes the investigation
- events are real
- evidence is normalized
- result is real
- progress_score changes based on real progress
- solved remains a separate boolean
- memory influences later strategy
- stagnation triggers strategic diversification/review
- solved state is detected correctly

Do NOT require every iteration to improve.
Do NOT hard-code a 20 → 60 → 100 progression.
Do NOT make the demo depend on reaching solved=true.

A valid demonstration may look like:

   Round 1 → progress_score 24, solved=false
   Round 2 → progress_score 58, solved=false
   Round 3 → progress_score 86, solved=false

if the system visibly learned and changed strategy.

A successful run may of course reach solved=true, but
strategy adaptation is the primary demonstration of the system.

FINAL GUARDRAIL AUDIT:

- SandboxTarget comes only from trusted orchestration
- Strategy cannot override target
- no host execution
- no arbitrary external target
- no alternate Docker execution system
- no secret exposure to generated strategy
- Muteki isolation remains intact
- timeout stops the run safely
- invalid target stops before execution

FAILURE TESTS:

- Muteki unavailable
- evaluator failure
- invalid SandboxTarget
- malformed Strategy
- timeout
- empty evidence
- event stream interruption
- repeated failed strategies

Do not add major features.

Do not rewrite upstream projects.

Do not add additional workers/models unless the existing configuration cannot run.

Do not add additional challenge types unless the selected demo target is unusable.

Create docs/DEMO_SCRIPT.md containing:
- exact setup
- exact launch command
- exact target
- expected strategy evolution
- expected score progression
- fallback replay mode
- recovery instructions
```

---

## Deliverables

- fully integrated pipeline
- end-to-end tests
- sandbox guardrail audit
- `docs/DEMO_SCRIPT.md`
- reproducible launch command
- saved demo/replay data
- final architecture diagram
- final 3-round working demonstration

---

# FINAL MERGE CHECKLIST

## Architecture

- [ ] Muteki remains responsible for autonomous execution.
- [ ] MTASA-derived logic is used only as an iterative strategy-learning pattern.
- [ ] No direct arbitrary-command bridge exists.
- [ ] No fake Muteki API exists.
- [ ] SandboxTarget is explicitly a project-owned trusted abstraction.
- [ ] Strategy, InvestigationEvent, InvestigationResult and ScoreReport contracts are stable.
- [ ] Vendor repositories remain untouched.
- [ ] Muteki-specific internals are isolated inside the adapter.

---

## Learning Loop

- [ ] Round 1 produces a strategy.
- [ ] Strategy schema validation runs.
- [ ] Safety/target validation runs before Muteki.
- [ ] Muteki executes it autonomously.
- [ ] Real events are received.
- [ ] Evidence is normalized.
- [ ] InvestigationResult is generated.
- [ ] progress_score is calculated.
- [ ] solved is calculated independently.
- [ ] Memory records the outcome.
- [ ] Round 2 receives information from Round 1.
- [ ] Strategy changes when appropriate.
- [ ] Stagnation triggers diversification/review.
- [ ] Round 3 can reach a different result.
- [ ] The loop can run unattended.

---

## Security / Sandbox

- [ ] Only registered SandboxTargets can run.
- [ ] Generated Strategy cannot select a target.
- [ ] Generated Strategy cannot alter runtime_reference.
- [ ] No host execution path exists.
- [ ] No second Docker execution system exists.
- [ ] No arbitrary external target can be introduced.
- [ ] Timeouts terminate safely.
- [ ] Secrets are not exposed to generated strategies.
- [ ] Demo is fully isolated and authorized.

---

## UI

- [ ] Judge sees the objective.
- [ ] Judge sees current strategy.
- [ ] Judge sees strategy revision.
- [ ] Judge sees Muteki activity.
- [ ] Judge sees evidence/progress.
- [ ] Judge sees score progression.
- [ ] Judge sees strategy evolution.
- [ ] Judge sees final result.
- [ ] UI survives backend failure.
- [ ] Replay mode works without live services.

---

# DEMO FLOW

The ideal live demonstration should fit into approximately 2–3 minutes.

```text
1. Select registered sandbox target.

2. Show objective.

3. Show initial Strategy #1.

4. Start system.

5. Muteki swarm investigates.

6. Live events appear in UI.

7. Evidence appears.

8. Score updates.

9. Strategy Engine analyzes result.

10. Strategy #2 appears.

11. Muteki investigates again.

12. Score changes.

13. Strategy #3 appears.

14. Final result appears.
```

The judge-facing message:

> **"We don't tell the system how to solve the security challenge. We give it a bounded objective. It investigates autonomously, measures progress, remembers what happened, changes its strategy, and investigates again."**

---

# TIME-BUDGET RULES

This is a 13-hour hackathon.

## Hard cuts

Do NOT spend significant time on:

- merging Muteki and MTASA frontends
- preserving MTASA delivery-routing functionality
- supporting every LLM provider
- supporting every Muteki worker type
- rewriting Muteki's worker system
- creating a new worker execution layer
- creating a sophisticated memory database
- designing a complicated mathematical score
- building many challenge containers
- rewriting upstream projects
- adding features after the end-to-end loop works

## Priority order

### P0 — Must work

1. **Real Muteki run**
2. **Real strategy injection**
3. **Real evidence/result extraction**
4. **Trusted sandbox enforcement**

### P1 — Differentiator

5. **Strategy validation before execution**
6. **Progress scoring + independent solved flag**
7. **Strategy memory**
8. **Strategy evolution**
9. **3-round autonomous loop**

### P2 — Presentation

10. **Live event UI**
11. **Strategy/score visualization**
12. **Replay mode**
13. **Polish**

## Emergency fallback

If real-time event streaming becomes unstable:

- keep the real Muteki run
- collect final events/results
- use near-live polling
- retain replay mode

If strategy evolution becomes unstable:

- use deterministic strategy templates driven by score/progress
- keep the architecture intact
- demonstrate the loop

If memory becomes unstable:

- use simple JSON/local history

If the UI becomes unstable:

- use replay mode for presentation
- preserve the real backend demo separately

The **closed-loop architecture is more important than sophisticated implementation details.**

---

# DEFINITION OF DONE

The system is successful when it can run:

```text
Trusted SandboxTarget
        ↓
Initial Strategy
        ↓
Real Muteki autonomous investigation
        ↓
Real InvestigationEvents
        ↓
InvestigationResult
        ↓
Objective Score
        ↓
Strategy Memory
        ↓
New Strategy
        ↓
Real Muteki investigation again
```

for at least **3 iterations without manual intervention**, with:

- strategy validation before execution
- visible strategy evolution
- meaningful progress_score signals
- independent solved boolean
- real Muteki execution
- real investigation events
- normalized evidence
- safe sandboxing
- replayable demo data

The system does not need to solve every demo target.
It must demonstrate that later strategies are informed by earlier evidence and scores.

The project is **not** considered complete merely because:

- both repositories run
- the UI looks good
- an agent finds a vulnerability once
- the Strategy Engine generates different text
- the score changes artificially

The differentiating feature is:

> **A bounded autonomous security system that learns which investigation strategies make progress and uses that experience to guide subsequent Muteki investigations.**

---

# FINAL TEAM WORKFLOW

```text
             EVERYONE
                │
        ┌───────┴───────┐
        │               │
     CHUNK 1          CHUNK 2
     Setup            UI
        │               │
        └───────┬───────┘
                │
        FREEZE CONTRACTS
                │
     ┌──────────┼──────────┐
     ↓          ↓          ↓
 CHUNK 3     CHUNK 4    CHUNK 5
 Strategy     Muteki     Scoring
 Evolution    Adapter    Engine
     │          │          │
     └──────────┼──────────┘
                ↓
             CHUNK 6
          Integration
                ↓
          3-Round Demo
                ↓
             SUBMIT
```

**This structure is intentionally optimized so that the first two chunks establish the shared truth, while Chunks 3–5 can then be developed independently and merged through stable contracts.**


## Final End-to-End Architecture

```text
Trusted SandboxTarget
        ↓
Initial Strategy
        ↓
Schema + Safety Validation
        ↓
Muteki Run
        ↓
Real Investigation Events
        ↓
Evidence Normalization
        ↓
InvestigationResult
        ↓
Progress Score + Solved Flag
        ↓
Strategy Memory
        ↓
Teacher / Reviewer
        ↓
Improved Strategy
        ↓
Muteki Run
        ↓
...
```
