# MUTeki-Evolve UI Contract

The judge-facing dashboard consumes a small, explicit view-model boundary in
`artifacts/muteki-evolve/src/lib/replay.ts`. It mirrors the project-owned
normalized contracts without importing Python models, orchestration code,
Muteki internals, MTASA internals, or a transport client.

## Stop-condition resolution

- Chunk 1 contract documentation exists at `docs/INTEGRATION_CONTRACT.md`.
- The project-owned models exist under `app/models/`.
- The actual compatibility modules are `strategy/` and `muteki_adapter/` at the
  repository root, not `app/strategy/` and `app/muteki_adapter/` as named in the
  Chunk 2 brief. The UI does not depend on either path directly.
- The adapter's live return/event behavior remains provisional and unverified,
  so this chunk uses the verified normalized mock fixture only.
- The existing workspace had no judge-facing React application. A new
  project-owned React/Vite artifact was created at `artifacts/muteki-evolve/`.

## Data source boundary

`demoScenario` is a deterministic, three-round local replay:

- target: `trusted-demo-target`
- run: `mock-c1`
- strategy revisions: 1 → 2 → 3
- progress: 0 (idle) → 28 → 72 → 100
- solved: only revision 3

The `SandboxTarget`, `Strategy`, `InvestigationEvent`, `Evidence`,
`InvestigationResult`, and `ScoreReport` view-models use the actual Chunk 1
field names. The UI renders normalized application data and does not expose
raw Muteki event objects, worker classes, blackboard state, or MTASA classes.

## Components

The dashboard composes `TargetCard`, `ObjectiveCard`, `StrategyCard`,
`InvestigationTimeline`, `EvidencePanel`, `ScorePanel`, `ScoreHistory`,
`StrategyHistory`, and `SystemStatus`. `FailureStates` and `SafeStateBanner`
provide explicit, local previews for failure-safe presentation.

## Replay states

- `IDLE`: no events or evidence admitted; score is 0 and unsolved.
- `RUNNING`: the current round is visible and Auto Play may advance after 3.2s.
- `PAUSED`: the current round remains visible and can resume.
- `COMPLETED`: revision 3 remains visible, solved is true, and Auto Play stops.

Start, pause, next round, reset, and auto play are local state transitions only.
Reset returns to `IDLE` without mutating the fixture.

## Failure-safe presentation

The safe-state preview controls render:

- `Muteki is currently unavailable.`
- `Evaluation unavailable.`
- `Evidence incomplete.`
- `Investigation timed out.`
- `No investigation events received yet.`

Unavailable and evaluator states preserve the current normalized investigation
view where possible. Incomplete evidence removes evidence from the view rather
than inventing confidence. Empty event streams render an explicit empty state.
No safe-state preview changes the target, executes anything, or makes a
security decision.

## Security boundary

The target card is display-only. It exposes no target creation, host/runtime
editing, command entry, or security decision controls. Strategy display is
high-level intent and shows only revision lineage and safe context.

The replay is always visibly labeled `DEMO REPLAY` / `MOCK REPLAY`; it is never
represented as a live Muteki execution.