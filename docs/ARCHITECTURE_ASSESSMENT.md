# Initial Architecture Assessment

## Workspace state

The workspace contained only the uploaded Chunk 1 brief and Replit's Python
3.13 module configuration. It had no application source, package manifest, test
suite, git metadata, frontend, backend, Muteki integration, or MTASA checkout.
There was therefore no compatible existing implementation to preserve or
reconcile.

## Decisions

- Use Python 3.10+ and Pydantic for strict, immutable application contracts.
- Keep upstream Muteki and MTASA under `vendor/` as source snapshots.
- Keep all application models under `app/models/`; downstream code consumes
  those models rather than upstream objects.
- Make the first adapter boundary semantic and async because the inspected
  Muteki event/run lifecycle is async and event-driven.
- Provide deterministic mocks and a three-round fixture so later chunks do not
  need a live Muteki runtime.
- Defer the real Muteki adapter, strategy generation, scoring, orchestration,
  memory engine, and full UI to their assigned chunks.

## Known constraints

The checked-out Muteki source provides an EventBus, durable SessionStore replay,
and an SSE web endpoint. Its live worker path depends on runtime configuration,
LLM credentials, and selectable sandbox/container execution. This environment
has no LLM credential and Docker-backed execution is disabled, so the real
worker lifecycle remains explicitly unverified and is documented in
`docs/UPSTREAM_NOTES.md`.
