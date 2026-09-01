# Upstream Notes

This document records source inspection and verification for Chunk 1. Claims
about upstream behavior below are based on the checked-out source snapshots, not
on the application contracts.

## Snapshot provenance

| Upstream | Source | Checked-out revision |
|---|---|---|
| Muteki | https://github.com/FishCodeTech/muteki | `d1093c1` (shallow clone) |
| MTASA | https://github.com/Nerolithos/MTASA | `d09a2f6` (shallow clone) |

The workspace initially had no git repository, remotes, manifests, source files,
or documentation referring to either project. The URLs were discovered through
public repository lookup and are now recorded here for reproducibility.

The upstream directories are vendor snapshots. No files inside their working
trees were edited by Chunk 1.

## Environment

Observed on 2026-09-01:

- Python `3.13.11` is available.
- `uv 0.9.24` is available.
- Docker CLI `27.5.1` is available, but the environment exposes
  `REPLIT_DISABLE_DOCKER`; container-backed execution was not treated as
  verified.
- `python3.6` and `python3.9` are not available.
- No Muteki LLM credential is configured.
- `tiktoken` and `pytest` were not installed in the base interpreter.

## Muteki

### Actual entrypoints and startup commands

Source inspection found:

- `run.sh tui` launches `uv run python -m apps.tui`.
- `run.sh web --backend-only` launches the FastAPI command deck through
  `uvicorn apps.web.server:create_app --factory`.
- `run.sh web` can also start the Next.js UI after the backend.
- `muteki.cli` delegates `web` and `tui` to `run.sh`.
- `apps.tui.__main__` has a no-credential mock path and a `--swarm` real path.
- `examples/mock_solver.py` is a deterministic in-process event-stream demo.

### Actual run lifecycle

The web route `POST /api/runs/{run_id}/start` builds a driver, creates or
retrieves a `Run`, and calls `RunManager.start`. The driver constructs the
upstream `Swarm`; `Swarm.run()` owns coordinator/worker execution. The web
server projects run metadata and closes the run bus after runtime cleanup.

The actual high-level input is a Muteki `Challenge` containing fields such as
id, description, category, target, and attachments. This is intentionally not
copied into the project-owned Strategy contract. Chunk 4 must map an approved
application strategy to the real Muteki driver/challenge/directive mechanism
without allowing generated strategy content to choose a target.

### Coordinator, workers, workspace, and isolation

Source inspection shows a coordinator loop and worker engines under
`vendor/muteki/muteki/swarm/` and `vendor/muteki/muteki/solver/`. The web driver
creates a `SandboxManager`, `ArtifactStore`, and `Swarm`; worker execution is
delegated to Muteki's configured CLI/container machinery. The web API also
scopes uploads and workspaces under the run.

The exact production isolation behavior depends on the selected executor and
runtime configuration. **Not yet verified — blocked by: this environment has
Docker disabled, no LLM credential, and no authorized external target.**

### Events and evidence

Muteki has a typed `EventType` enum and `Event` model in
`muteki/core/events.py`. The event bus in `muteki/core/event_bus.py`:

- assigns monotonic sequence numbers;
- fans events out to multiple subscribers;
- keeps a bounded in-memory replay ring;
- persists through `SessionStore` sinks;
- supports replay/resume semantics.

The web command deck exposes `GET /api/runs/{run_id}/events` as an SSE stream
with `Last-Event-ID` replay. Relevant upstream event types include
`run.preparing`, `run.started`, `worker.status`, `worker.finished`,
`reasoning.delta`, `tool.start`, `tool.args`, `tool.result`,
`terminal.output`, `solvegraph.delta`, `insight.event`,
`blackboard.delta`, and `run.finished`.

Upstream evidence is represented in typed event payloads and the solve/shared
graph. `insight.event` distinguishes facts, dead ends, and flags; shared graph
payloads include fact, verified, confidence, actor, artifact, and verifier
fields. This is the source shape Chunk 4 must normalize, not an application
contract.

### Completion and result

`run.finished` is the run-level terminal event. `worker.finished` is explicitly
worker-level and must not be interpreted as whole-run completion. The upstream
mock emits a solved flag and a flag-found insight; live runs project solved/flag
state through the run manager and swarm outcome.

### Safe verification performed

The following command succeeded:

```bash
uv run --project vendor/muteki python -m examples.mock_solver
```

Observed result: the deterministic Muteki mock produced
`flag{mock_encoding_solved}`, persisted 42 events, and replayed ordered events
ending in `run.finished`. This verifies the in-process event bus, mock run
lifecycle, persistence/replay, and completion representation only.

The real web app source imports successfully, but a live swarm was not started.
**Not yet verified — blocked by: no LLM credential and Docker-backed worker
execution disabled.** No arbitrary or external target was used.

The backend health endpoint was verified without starting a swarm:

```bash
cd vendor/muteki
uv run python -m uvicorn apps.web.server:app \
  --host 127.0.0.1 --port 18080
curl http://127.0.0.1:18080/api/health
```

It returned `{"status":"ok","version":"0.3.2"}` while the bounded local server
was running. The server was then stopped.

## MTASA

### Actual entrypoints and lifecycle

`run_local.py` starts the local frontend on port 7860 or the next free port.
`fool/fool_loop.py:run_fool_loop` owns the iterative Fool loop:

1. initialize per-dataset and global memory;
2. build a round prompt and run the harness;
3. write a candidate solver;
4. submit it to `genius/genius_judge.py`;
5. read the report and classify the outcome;
6. update memory and keep the best result.

Genius loads datasets, executes a solver subprocess, validates output, scores with
the fixed `official_like_latest` mode, and writes a report. The Teacher supplies
static playbook/checklist guidance and periodic review signals; it does not score
or directly produce the final solver. Memory persists episodes, summaries, and
Markdown notes, with BM25-style retrieval and mechanical index aggregation.

### Reusable concepts

- Generate → Execute → Evaluate → Remember → Improve.
- A Fool/Teacher separation between exploratory changes and slower guidance.
- Persistent experiment history and explicit outcome classification.
- Strategy improvement based on evidence from previous rounds.

### Delivery-specific concepts excluded

The final MUTeki-Evolve system does not port MTASA's delivery solver, delivery
routing, delivery constraints, Python 3.6 solver runtime, Genius delivery
regression system, or delivery-specific frontend. The application will create
its own Strategy Evolution Engine in Chunk 3 and its own Evaluation Engine in
Chunk 5.

### Verification

`docs/INSTALL.md` confirms that MTASA requires Python 3.10+ for the main code and
Python 3.6 for Genius solver subprocesses. This environment lacks both
`python3.6` and `tiktoken`; therefore the documented Genius end-to-end
subprocess path and full MTASA test suite are **Not yet verified — blocked by:
required Python 3.6 runtime and missing optional dependency**.

The frontend launch command was attempted with a bounded timeout, but it was not
counted as a successful long-running verification because the process did not
produce a usable readiness signal before the timeout.

## Contract Conflicts

1. The plan's conceptual `run_strategy(target, strategy)` maps to Muteki's real
   driver/swarm lifecycle rather than a native upstream function with that exact
   name. The application contract is retained as a semantic adapter boundary;
   Chunk 4 must implement the mapping.
2. The plan's `subscribe_events(run_id)` maps cleanly at the semantic level to
   Muteki's EventBus plus durable SessionStore replay and the web SSE endpoint,
   but the event fields are richer and Muteki-specific. Chunk 4 must normalize
   them into `InvestigationEvent`.
3. The plan's generic `Evidence` model is intentionally narrower than Muteki's
   graph/artifact provenance. This is expected normalization, not a reason to
   expose raw upstream objects.

No upstream source was modified.
