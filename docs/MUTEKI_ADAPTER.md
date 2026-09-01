# MUTeki Integration Adapter

> **Chunk 4 Deliverable** — `muteki_adapter/`
>
> Architecture reference: [INTEGRATION_CONTRACT.md](INTEGRATION_CONTRACT.md) §3–4

---

## Purpose

The Muteki Integration Adapter is the **only code that touches Muteki internals**.

Its responsibilities are:

1. Accept a trusted `SandboxTarget` and a pre-validated `Strategy` from the orchestration layer
2. Translate the strategy into a Muteki `Challenge`
3. Create and start a Muteki `RunManager` run
4. Collect real `InvestigationEvent`s from the Muteki `EventBus`
5. Wait for `RUN_FINISHED` (or timeout)
6. Normalize all events and the terminal payload into a project-owned `InvestigationResult`

No Muteki types or exceptions are permitted to leak outside this module.

---

## Module Structure

```
muteki_adapter/
  adapter.py          — RealMutekiAdapter (main class)
  live_probe.py       — LiveNetworkAdapter (real HTTP/HTTPS prober)
  mock.py             — MockMutekiAdapter (deterministic test fixture)
  protocol.py         — MutekiAdapter protocol definition
  config.py           — AdapterConfig + load_config()
  errors.py           — Typed error classes
  event_normalizer.py — Muteki Event → InvestigationEvent
  result_normalizer.py— Collected events → InvestigationResult
  translator.py       — Strategy → Muteki Challenge
  validators.py       — Pre-execution validation (second safety boundary)
```

---

## Integration Modes

The adapter supports three operating modes, selected via `MUTEKI_MODE` or `AdapterConfig`:

### `mock_bridge` (default)

Uses Muteki's own `examples/mock_solver.run_mock_solve()` as the driver.

- Exercises the **real** `RunManager → EventBus → SessionStore` pipeline
- No Docker or LLM credentials required
- All 241 tests use this mode by default

```python
MUTEKI_MODE=mock_bridge python -m pytest
```

### `live_probe`

Uses `LiveNetworkAdapter` — performs real HTTP/HTTPS requests against the target URL.

- Fetches response headers, status codes, page metadata
- Probes security endpoints concurrently (`/robots.txt`, `/admin`, `/login`, etc.)
- Analyzes cookie flags, CSP/HSTS/X-Frame-Options headers
- No Docker required — works against any live web target

### `real`

Attempts to use Muteki's real Swarm coordinator.

- Requires Docker and LLM credentials (`ANTHROPIC_API_KEY`, etc.)
- Fails closed with `MutekiUnavailableError` if dependencies are unavailable
- See `docs/MUTEKI_INTEGRATION_LIMITATIONS.md` for environment constraints

---

## Security Boundary

The adapter is a **fail-closed** double boundary:

```
app/validation.py (First boundary)
        ↓  approve_strategy()
muteki_adapter/validators.py (Second boundary)
        ↓  validate_adapter_inputs()
Muteki Run
```

The adapter **refuses to execute** if:

- The `SandboxTarget` is not present in `TrustedTargetRegistry`
- The `Strategy` contains any forbidden key (`target`, `shell`, `command`, `docker`, `sandbox_escape`, etc.)
- The strategy tries to override `runtime_reference`

This validation runs **before any Muteki run is created**.

---

## Core API

### `RealMutekiAdapter`

```python
from muteki_adapter import RealMutekiAdapter
from muteki_adapter.config import AdapterConfig

adapter = RealMutekiAdapter(
    registry=trusted_registry,
    config=AdapterConfig(mode="mock_bridge", timeout_seconds=300.0)
)

# Execute one strategy — returns InvestigationResult
result = await adapter.run_strategy(target, strategy)

# Stream normalized events for a run
async for event in adapter.subscribe_events(run_id):
    print(event.type, event.summary)
```

### `run_strategy(target, strategy) → InvestigationResult`

**Steps (source-verified from `adapter.py`):**

1. `validate_adapter_inputs(target, strategy, registry)` — fail-closed, raises `StrategyValidationError` on any violation
2. `translate_strategy_to_challenge(target, strategy, run_id)` — builds Muteki `Challenge`
3. `mgr.create(run_id)` / `mgr.start(run_id, driver)` — creates and starts the Muteki run
4. `bus.subscribe(last_event_id=0)` — subscribes to live event stream
5. Wait for `is_run_terminal(event)` or `timeout_seconds` deadline
6. `normalize_result(events, finished_event, elapsed, error)` — returns `InvestigationResult`

> **IMPORTANT:** `solved=True` is only returned when Muteki's `RUN_FINISHED` payload verifies it. The adapter never invents a solved state.

### `subscribe_events(run_id) → AsyncIterator[InvestigationEvent]`

Replays persisted events from `SessionStore`, then streams live from `EventBus`. Events are normalized via `event_normalizer.normalize_event()`.

---

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `MUTEKI_MODE` | `mock_bridge` | `mock_bridge`, `live_probe`, or `real` |
| `MUTEKI_TIMEOUT_SECONDS` | `300.0` | Max seconds to wait for `RUN_FINISHED` |
| `MUTEKI_EVENT_TIMEOUT_SECONDS` | `30.0` | Max idle time between events (polling paths) |
| `MUTEKI_SESSIONS_ROOT` | `sessions` | Muteki `RunManager` sessions directory |

---

## Error Types

All errors inherit from `MutekiAdapterError`. The application never sees raw Muteki exceptions.

| Error | `kind` | Meaning |
|---|---|---|
| `StrategyValidationError` | `safety` / `target` / `schema` | Validation rejected before run creation |
| `MutekiUnavailableError` | `muteki_unavailable` | Muteki runtime cannot be reached/imported |
| `MutekiRunCreationError` | `run_creation_failed` | `RunManager.create()` or `start()` failed |
| `MutekiTimeoutError` | `investigation_timeout` | Run exceeded `MUTEKI_TIMEOUT_SECONDS` |
| `MutekiEventStreamError` | `event_stream_error` | `EventBus.subscribe()` disconnected |
| `MutekiMalformedResultError` | `malformed_result` | `RUN_FINISHED` payload is unreadable |

---

## Event Normalization

Muteki's internal `Event` type (from `vendor/muteki/muteki/core/events.py`) is translated to the project-owned `InvestigationEvent` contract:

```
Muteki Event {event_type, seq, ts, run_id, solver_id, payload}
        ↓  normalize_event()
InvestigationEvent {sequence, timestamp, type, run_id, worker_id, summary}
```

**Key rules (source-verified):**

- `run.finished` is the **only** terminal event — `WORKER_FINISHED` is NOT run completion
- `sequence` uses Muteki's `seq` if non-zero, else the adapter's monotonic counter
- `worker_id` maps from `solver_id` (may be `None`)
- Events that carry no application-level information return `None` (not raised)
- `None` input returns `None` (malformed events never crash the event loop)

---

## Live Probe Adapter

`LiveNetworkAdapter` (`live_probe.py`) provides real HTTP/HTTPS investigation without Muteki:

```python
from muteki_adapter.live_probe import LiveNetworkAdapter

adapter = LiveNetworkAdapter(registry=trusted_registry, run_id="probe-001", timeout=8.0)
result = await adapter.run_strategy(target, strategy)
```

**Capabilities:**
- Real HTTP GET with realistic browser headers
- HTML title + `<meta name="generator">` extraction (WordPress, Drupal, etc.)
- Concurrent endpoint probing via `asyncio.gather`
- Cookie flag inspection (`HttpOnly`, `Secure`, `SameSite`)
- Security header audit (CSP, HSTS, X-Frame-Options, Referrer-Policy)
- Form and input parameter discovery

---

## Testing

```bash
# Run all adapter tests
python -m pytest tests/muteki_adapter/ -v

# Test files
tests/muteki_adapter/test_adapter.py          # RealMutekiAdapter contract tests
tests/muteki_adapter/test_event_normalizer.py # normalize_event edge cases
tests/muteki_adapter/test_result_normalizer.py# normalize_result contract
tests/muteki_adapter/test_translator.py       # Strategy → Challenge translation
tests/muteki_adapter/test_validators.py       # Pre-execution validation
```

---

## What the Adapter Does NOT Do

| Forbidden | Reason |
|---|---|
| Create shell commands | Strategy Engine produces high-level direction only |
| Create a second Docker runner | Muteki owns worker lifecycle |
| Bypass `TrustedTargetRegistry` | Security boundary must not be bypassable |
| Expose Muteki types to application | Contract isolation |
| Invent a solved result | `solved=True` comes only from Muteki's `RUN_FINISHED` payload |
| Swallow errors silently | Fail-closed — all failures raise typed exceptions |

---

## Related Docs

- [INTEGRATION_CONTRACT.md](INTEGRATION_CONTRACT.md) — canonical interface definitions
- [UPSTREAM_NOTES.md](UPSTREAM_NOTES.md) — source-verified Muteki internals
- [ORCHESTRATION.md](ORCHESTRATION.md) — how the adapter fits into the closed loop
- [EVALUATION_ENGINE.md](EVALUATION_ENGINE.md) — how `InvestigationResult` feeds scoring
