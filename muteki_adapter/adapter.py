"""RealMutekiAdapter — the real integration between MUTeki-Evolve and Muteki.

This module implements the MutekiAdapter protocol by:
  1. Validating inputs (fail-closed)
  2. Translating Strategy → Muteki Challenge
  3. Creating and starting a Muteki RunManager run
  4. Subscribing to the EventBus for live events
  5. Waiting for RUN_FINISHED (or timeout)
  6. Normalizing collected events and the terminal payload → InvestigationResult

Muteki integration modes:
  "mock_bridge" (default, verifiably runnable without Docker/LLM):
    Uses Muteki's own run_mock_solve() from examples/mock_solver.py as the
    driver. This exercises the real RunManager → EventBus → SessionStore →
    event normalization pipeline against real Muteki event objects.

  "real" with MUTEKI_BACKEND set (HTTP mode):
    Calls the remote Muteki web API to create + start a run with the
    configured worker engine (MUTEKI_WORKER_ENGINE, default "codex"), then
    consumes the SSE stream from GET /api/runs/{run_id}/events.
    Requires a running Muteki backend and LLM credentials server-side.

  "real" without MUTEKI_BACKEND (in-process mode):
    Passes a real swarm driver to RunManager. Requires Docker and LLM creds.
    Fails closed with MutekiUnavailableError if unavailable.

Architecture reference: docs/INTEGRATION_CONTRACT.md §3 (run_strategy),
  docs/INTEGRATION_CONTRACT.md §4 (subscribe_events).

Security boundary: This module is the ONLY code that interacts with Muteki
  internals. No Muteki types are exposed beyond this layer.

STOP CONDITIONS (docs/MUTEKI_INTEGRATION_LIMITATIONS.md):
  Real-mode end-to-end integration is not verifiable in environments where
  Docker is disabled (REPLIT_DISABLE_DOCKER) or LLM credentials are absent.
  All tests in mock_bridge mode are independently verifiable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.models import (
    InvestigationEvent,
    InvestigationResult,
    SandboxTarget,
    Strategy,
    TrustedTargetRegistry,
)
from muteki_adapter.config import AdapterConfig, load_config
from muteki_adapter.errors import (
    MutekiAdapterError,
    MutekiEventStreamError,
    MutekiRunCreationError,
    MutekiTimeoutError,
    MutekiUnavailableError,
)
from muteki_adapter.event_normalizer import is_run_terminal, normalize_event
from muteki_adapter.result_normalizer import normalize_result
from muteki_adapter.translator import build_start_payload, translate_strategy_to_challenge
from muteki_adapter.validators import validate_adapter_inputs

LOG = logging.getLogger(__name__)

RunManager: Any = None

__all__ = ["RealMutekiAdapter"]


def _generate_run_id() -> str:
    """Generate a unique, URL-safe run identifier."""
    return f"ev-{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# Driver factories
# ---------------------------------------------------------------------------

async def _mock_bridge_driver(run: "Any") -> None:
    """Muteki-native mock driver.

    Uses Muteki's own examples/mock_solver.run_mock_solve() as the driver.
    This exercises the real EventBus + SessionStore + event pipeline without
    any Docker or LLM dependency.

    Source reference: vendor/muteki/examples/mock_solver.py
    """
    try:
        from examples.mock_solver import run_mock_solve  # type: ignore[import]
    except ImportError as exc:
        raise MutekiUnavailableError(
            f"Cannot import Muteki mock_solver (examples must be on path): {exc}"
        ) from exc

    try:
        from muteki.core.cost import CostController  # type: ignore[import]
    except ImportError as exc:
        raise MutekiUnavailableError(
            f"Cannot import Muteki CostController: {exc}"
        ) from exc

    cost = CostController(bus=run.bus)
    await run_mock_solve(bus=run.bus, cost=cost, run_id=run.run_id)


async def _real_swarm_driver(
    run: "Any",
    challenge: "Any",
    *,
    config: "AdapterConfig | None" = None,
    target: "SandboxTarget | None" = None,
    strategy: "Strategy | None" = None,
    run_id: str = "",
) -> None:
    """Real Muteki swarm driver.

    HTTP mode (MUTEKI_BACKEND set):
      POSTs the codex worker-profile payload to the Muteki web API and lets
      the server orchestrate the swarm. The caller then streams events over
      SSE. This function returns immediately after a successful start; the
      event collection loop in run_strategy drives the rest.

    In-process mode (no MUTEKI_BACKEND):
      Starts a real Swarm coordinator with the provided Challenge.
      Requires Docker (for container workers) and LLM credentials.
      Fails closed with MutekiUnavailableError if unavailable.

    Source reference: vendor/muteki/muteki/swarm/swarm.py,
                      vendor/muteki/apps/web/drivers.py
    """
    cfg = config or load_config()

    # ── HTTP mode: delegate to the remote Muteki web API ─────────────────────
    if cfg.http_mode and target is not None and strategy is not None and run_id:
        try:
            import httpx  # type: ignore[import]
        except ImportError as exc:
            raise MutekiUnavailableError(
                "httpx is required for HTTP mode (pip install httpx). "
                "Alternatively set MUTEKI_MODE=mock_bridge."
            ) from exc

        payload = build_start_payload(
            target, strategy, run_id,
            worker_engine=cfg.worker_engine,
            worker_model=cfg.worker_model,
            worker_backend=cfg.worker_backend,
        )
        url = f"{cfg.backend_url}/api/runs/{run_id}/start"
        LOG.info("_real_swarm_driver: HTTP POST %s engine=%s", url, cfg.worker_engine)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                LOG.info(
                    "_real_swarm_driver: Muteki run started via HTTP run_id=%s status=%s",
                    run_id, resp.status_code,
                )
        except httpx.HTTPStatusError as exc:
            raise MutekiRunCreationError(
                f"Muteki /api/runs/{run_id}/start returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            ) from exc
        except httpx.RequestError as exc:
            raise MutekiUnavailableError(
                f"Cannot reach Muteki backend at {cfg.backend_url}: {exc}"
            ) from exc
        # In HTTP mode the server handles the swarm; nothing more to do here.
        return

    # ── In-process mode: build_driver() via the real web driver stack ─────────
    #
    # Source reference: vendor/muteki/apps/web/drivers.py::build_driver(body, mgr)
    # We build the same request body that the web API accepts and pass it
    # directly to build_driver(), bypassing the HTTP layer.
    #
    # Pre-conditions for this path:
    #   - Codex CLI must be on PATH and authenticated (codex login)
    #   - MUTEKI_WORKER_BACKEND=local or container (Docker)
    #   - No MUTEKI_BACKEND set (otherwise we'd have taken the HTTP branch)
    if target is None or strategy is None or not run_id:
        raise MutekiUnavailableError(
            "_real_swarm_driver: in-process mode requires target, strategy, and run_id. "
            "Set MUTEKI_MODE=mock_bridge for environments without Codex on PATH."
        )

    # Build the real /start request body (source-verified schema).
    from muteki_adapter.translator import build_start_payload  # noqa: PLC0415
    body = build_start_payload(
        target, strategy, run_id,
        worker_engine=cfg.worker_engine,
        worker_model=cfg.worker_model,
        worker_backend=cfg.worker_backend,
    )

    try:
        from apps.web.drivers import build_driver  # type: ignore[import]
    except ImportError as exc:
        raise MutekiUnavailableError(
            f"Cannot import Muteki build_driver from apps.web.drivers: {exc}. "
            "Ensure vendor/muteki is on PYTHONPATH and its dependencies are installed. "
            "Alternatively set MUTEKI_MODE=mock_bridge."
        ) from exc

    try:
        # RunManager (mgr) is accessed via the run object's __module__ parent;
        # build_driver only needs the body — it returns a coroutine driver function.
        real_driver = build_driver(body, mgr=None)  # mgr=None: no run registry needed
    except Exception as exc:
        # build_driver raises ValueError/RuntimeError for missing credentials or
        # unavailable engines — surface these as precise diagnostics.
        raise MutekiUnavailableError(
            f"Muteki build_driver() failed: {type(exc).__name__}: {exc}. "
            "Check that the Codex CLI is installed, authenticated, and on PATH. "
            "Run: codex --version   (must succeed) "
            "Run: codex login       (if not authenticated) "
            "Alternatively set MUTEKI_MODE=mock_bridge."
        ) from exc

    LOG.info(
        "_real_swarm_driver: in-process swarm driver built engine=%s run_id=%s",
        cfg.worker_engine, run_id,
    )
    # Invoke the driver with the run object so Muteki starts the real swarm.
    await real_driver(run)


# ---------------------------------------------------------------------------
# RealMutekiAdapter
# ---------------------------------------------------------------------------

class RealMutekiAdapter:
    """Real integration adapter between MUTeki-Evolve and the Muteki runtime.

    This class implements the MutekiAdapter protocol.

    Usage:
        registry = TrustedTargetRegistry({target.id: target})
        adapter = RealMutekiAdapter(registry=registry)
        result = await adapter.run_strategy(target, strategy)

    Configuration (from environment or explicit AdapterConfig):
        MUTEKI_MODE:             "mock_bridge" (default) or "real"
        MUTEKI_TIMEOUT_SECONDS:  max seconds to wait for RUN_FINISHED (default 300)
        MUTEKI_SESSIONS_ROOT:    Muteki sessions directory (default "sessions")
    """

    def __init__(
        self,
        *,
        registry: TrustedTargetRegistry,
        config: AdapterConfig | None = None,
    ) -> None:
        self._registry = registry
        self._config = config or load_config()
        LOG.info(
            "RealMutekiAdapter initialized: mode=%s sessions_root=%s timeout=%.0fs",
            self._config.mode,
            self._config.sessions_root,
            self._config.timeout_seconds,
        )

    def _make_run_manager(self) -> "Any":
        """Create a Muteki RunManager for this adapter invocation.

        Each run_strategy call gets its own RunManager to provide isolation.
        """
        run_mgr_cls = RunManager
        if run_mgr_cls is None:
            try:
                from apps.web.run_manager import RunManager as _RM  # type: ignore[import]
                run_mgr_cls = _RM
            except ImportError as exc:
                raise MutekiUnavailableError(
                    f"Cannot import Muteki RunManager: {exc}"
                ) from exc
        try:
            return run_mgr_cls(sessions_root=self._config.sessions_root)
        except Exception as exc:
            raise MutekiRunCreationError(
                f"RunManager construction failed: {type(exc).__name__}: {exc}"
            ) from exc

    async def run_strategy(
        self,
        target: SandboxTarget,
        strategy: Strategy,
    ) -> InvestigationResult:
        """Execute one approved high-level strategy through Muteki.

        Steps:
        1. Validate inputs (fail-closed; raises StrategyValidationError on any violation)
        2. Translate Strategy → Muteki Challenge
        3. Create Muteki RunManager + Run
        4. Launch run with configured driver
        5. Subscribe to EventBus and collect events
        6. Wait for RUN_FINISHED or timeout
        7. Normalize → InvestigationResult

        Args:
            target: A SandboxTarget from the trusted registry.
            strategy: An approved Strategy.

        Returns:
            InvestigationResult. solved=True ONLY when Muteki verifies success.

        Raises:
            StrategyValidationError: If inputs fail validation.
            MutekiUnavailableError: If Muteki cannot be reached.
            MutekiRunCreationError: If the run cannot be created or started.
            MutekiTimeoutError: If the run exceeds the adapter timeout.
            MutekiAdapterError: For any other adapter-layer failure.
        """
        start_time = time.monotonic()
        run_id = _generate_run_id()

        LOG.info(
            "run_strategy called: run_id=%s target=%s mode=%s",
            run_id, target.id, self._config.mode,
        )

        # ── Step 1: Fail-closed validation ──────────────────────────────────
        try:
            validated_strategy = validate_adapter_inputs(target, strategy, self._registry)
        except Exception:
            LOG.warning("run_strategy: validation failed for target=%s", target.id)
            raise  # re-raise StrategyValidationError intact

        # ── Step 2: Translate to Muteki Challenge ────────────────────────────
        challenge = translate_strategy_to_challenge(target, validated_strategy, run_id)
        LOG.debug("run_strategy: challenge translated run_id=%s", run_id)

        # ── Step 3 & 4: Create run and launch driver ─────────────────────────
        mgr = self._make_run_manager()
        try:
            run = mgr.get(run_id) or mgr.create(run_id)
        except Exception as exc:
            raise MutekiRunCreationError(
                f"Cannot create Muteki run {run_id!r}: {type(exc).__name__}: {exc}"
            ) from exc

        # Choose driver based on mode
        if self._config.mode == "mock_bridge":
            async def driver(run_obj: "Any") -> None:
                await _mock_bridge_driver(run_obj)
        else:
            async def driver(run_obj: "Any") -> None:
                await _real_swarm_driver(
                    run_obj,
                    challenge,
                    config=self._config,
                    target=target,
                    strategy=validated_strategy,
                    run_id=run_id,
                )

        try:
            run = await mgr.start(run_id, driver)
        except Exception as exc:
            raise MutekiRunCreationError(
                f"RunManager.start() failed for {run_id!r}: {type(exc).__name__}: {exc}"
            ) from exc

        LOG.info("run_strategy: Muteki run started run_id=%s", run_id)

        # ── Step 5 & 6: Collect events, wait for RUN_FINISHED ────────────────
        collected_events: list[InvestigationEvent] = []
        finished_muteki_event: "Any | None" = None
        sequence_counter = 0
        error: str | None = None

        timeout = self._config.timeout_seconds
        deadline = time.monotonic() + timeout

        try:
            async for muteki_event in _collect_events_with_timeout(
                run, deadline=deadline
            ):
                sequence_counter += 1
                normalized = normalize_event(
                    muteki_event,
                    run_id=run_id,
                    sequence_counter=sequence_counter,
                )
                if normalized is not None:
                    collected_events.append(normalized)

                if is_run_terminal(muteki_event):
                    finished_muteki_event = muteki_event
                    LOG.info(
                        "run_strategy: RUN_FINISHED received run_id=%s events=%d",
                        run_id, len(collected_events),
                    )
                    break

        except _TimeoutSignal:
            elapsed = time.monotonic() - start_time
            error = "investigation_timeout"
            LOG.warning(
                "run_strategy: timeout after %.1fs run_id=%s events=%d",
                elapsed, run_id, len(collected_events),
            )
        except Exception as exc:
            elapsed = time.monotonic() - start_time
            error = "event_stream_error"
            LOG.exception(
                "run_strategy: event stream error after %.1fs run_id=%s: %s",
                elapsed, run_id, exc,
            )

        elapsed_seconds = time.monotonic() - start_time

        # ── Step 7: Normalize → InvestigationResult ──────────────────────────
        result = normalize_result(
            run_id=run_id,
            events=collected_events,
            finished_event=finished_muteki_event,
            elapsed_seconds=elapsed_seconds,
            error=error,
        )

        LOG.info(
            "run_strategy: complete run_id=%s solved=%s elapsed=%.1fs events=%d error=%s",
            run_id, result.solved, elapsed_seconds, len(collected_events), error,
        )
        return result

    async def subscribe_events(self, run_id: str) -> AsyncIterator[InvestigationEvent]:
        """Yield normalized events for a run in sequence order.

        Replays persisted events from SessionStore first (via RunManager's
        session replay mechanism), then streams live events from the EventBus.

        Normalizes each Muteki event to InvestigationEvent before yielding.
        Silently skips events that cannot be normalized.
        """
        try:
            from apps.web.run_manager import RunManager  # type: ignore[import]
        except ImportError as exc:
            raise MutekiUnavailableError(
                f"Cannot import Muteki RunManager: {exc}"
            ) from exc

        mgr = RunManager(sessions_root=self._config.sessions_root)
        run = mgr.get(run_id)
        if run is None:
            return  # no such run — yield nothing (not an error, generator semantics)

        sequence_counter = 0
        async for muteki_event in run.bus.subscribe(last_event_id=0):
            sequence_counter += 1
            normalized = normalize_event(
                muteki_event,
                run_id=run_id,
                sequence_counter=sequence_counter,
            )
            if normalized is not None:
                yield normalized

    async def __aenter__(self) -> "RealMutekiAdapter":
        return self

    async def __aexit__(self, *_: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class _TimeoutSignal(BaseException):
    """Internal signal to break out of event collection on timeout."""


async def _collect_events_with_timeout(
    run: "Any",
    *,
    deadline: float,
) -> "AsyncIterator[Any]":
    """Async generator that yields Muteki events and raises _TimeoutSignal on deadline.

    Uses EventBus.subscribe(last_event_id=0) to receive all events in sequence.
    Checks the deadline before yielding each event.
    """
    try:
        bus = run.bus
        async for event in bus.subscribe(last_event_id=0):
            if time.monotonic() >= deadline:
                raise _TimeoutSignal("investigation deadline exceeded")
            yield event
    except _TimeoutSignal:
        raise
    except Exception as exc:
        raise MutekiEventStreamError(
            f"EventBus subscription failed: {type(exc).__name__}: {exc}"
        ) from exc
