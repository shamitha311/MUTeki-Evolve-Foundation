# MUTeki-Evolve Integration Contract

This is the project-owned contract for Chunks 2–6. It is deliberately
independent from Muteki and MTASA internals.

## Ownership and flow

```text
Trusted SandboxTarget
  -> Strategy
  -> schema + safety validation
  -> Muteki adapter
  -> normalized InvestigationEvent / InvestigationResult
  -> ScoreReport
  -> strategy memory and evolution
```

- **Orchestration** owns trusted target selection and the closed loop.
- **Strategy Engine** creates high-level strategies only. It does not execute,
  select targets, or change runtime infrastructure.
- **Muteki Adapter** communicates with upstream Muteki and normalizes events and
  results. It does not generate strategies or score results.
- **Evaluation** scores `InvestigationResult`. It does not execute Muteki.
- **UI** consumes project-owned contracts only and never imports Muteki internals.

## Models

### SandboxTarget

```json
{
  "id": "string",
  "name": "string",
  "description": "string",
  "runtime_reference": "string"
}
```

`SandboxTarget` is infrastructure-owned and must come from
`TrustedTargetRegistry`. `runtime_reference` is never LLM-generated and never
modifiable by a Strategy.

### Strategy

```json
{
  "objective": "string",
  "priorities": ["string"],
  "constraints": ["string"],
  "context": {},
  "revision": 1,
  "parent_revision": null
}
```

Strategy is high-level intent. It must not contain target selection,
`runtime_reference`, shell/command/exec content, Docker instructions, host
execution, external destinations, or sandbox escape instructions. Unknown
fields are rejected. Revision 1 has no parent; later revisions point to the
immediately preceding revision.

### InvestigationEvent

```json
{
  "sequence": 1,
  "timestamp": "ISO-8601 string",
  "type": "string",
  "run_id": "string",
  "worker_id": "string or null",
  "summary": "string"
}
```

This is a normalized projection. It is not a mirror of a raw Muteki event.
`sequence` starts at 1 and is monotonic within a run.

### Evidence

```json
{
  "type": "string",
  "summary": "string",
  "confidence": 0.0,
  "source_event": null
}
```

`confidence` is bounded from 0.0 through 1.0. `source_event`, when present,
refers to a normalized event sequence.

### InvestigationResult

```json
{
  "run_id": "string",
  "solved": false,
  "evidence": [],
  "evidence_summary": "string",
  "progress_signals": [],
  "elapsed_seconds": 0.0,
  "event_summary": [],
  "error": null
}
```

`solved` is the verified success boolean. It is independent from
`ScoreReport.progress_score`.

### ScoreReport

```json
{
  "progress_score": 0.0,
  "solved": false,
  "progress_level": "string",
  "reasons": [],
  "stagnated": false
}
```

`progress_score` is a 0–100 progress measure, not a percentage-solved claim.
Scoring semantics belong to Chunk 5.

## Validation pipeline

1. Orchestration resolves a `SandboxTarget` from the trusted registry.
2. Generated Strategy undergoes schema validation.
3. Generated Strategy undergoes fail-closed target-control validation.
4. Only the approved Strategy reaches the adapter.
5. The adapter repeats target and strategy validation immediately before
   upstream admission.

Invalid strategies are rejected with a controlled `StrategyValidationError`;
they are not silently sanitized.

## Adapter interface

```python
async def run_strategy(
    target: SandboxTarget,
    strategy: Strategy,
) -> InvestigationResult

def subscribe_events(
    run_id: str,
) -> AsyncIterator[InvestigationEvent]
```

The target is selected by trusted orchestration code. The Strategy cannot
override it. The real Chunk 4 adapter must map this semantic interface to
Muteki's run/driver/coordinator/worker lifecycle and must not add arbitrary
command, host execution, alternate Docker, or second sandbox paths.

Events may be delivered through polling, SSE, WebSocket, or another transport;
the normalized stream must preserve run identity and sequence order.

## Error and timeout behavior

- Schema, safety, and target failures are controlled validation errors before
  upstream execution.
- Upstream execution failures are represented in `InvestigationResult.error`
  and do not become `solved=True`.
- A timeout must terminate/close the adapter-owned run according to Muteki's
  lifecycle, return a result with a non-empty error, and never claim success.
- A partial event stream may be returned before an error; consumers must use
  `run_id` and sequence values rather than raw Muteki object identity.
- The exact timeout value is an orchestration policy decision for a later chunk;
  this contract does not invent one.

## Mock behavior

`MockMutekiAdapter` is deterministic and returns only project-owned models. The
three-round fixture is:

| Round | Strategy | Result | Example level | Example score |
|---|---|---|---|---:|
| 1 | A | reconnaissance, unsolved | reconnaissance | 28 |
| 2 | B | strong evidence, unsolved | strong evidence | 72 |
| 3 | C | verified success, solved | verified success | 100 |

These are demo fixtures, not real Muteki results and not a scoring
implementation. Chunks 3–5 can run against them without Muteki.
