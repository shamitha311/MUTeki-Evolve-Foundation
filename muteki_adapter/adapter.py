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

  "real":
    Passes a real swarm driver to RunManager. Requires Docker and LLM credentials.
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
import logging
import time
import uuid
from collections.abc import AsyncIterator

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
from muteki_adapter.translator import translate_strategy_to_challenge
from muteki_adapter.validators import validate_adapter_inputs

LOG = logging.getLogger(__name__)

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


async def _real_swarm_driver(run: "Any", challenge: "Any") -> None:
    """Real Muteki swarm driver.

    Starts a real Swarm coordinator with the provided Challenge.
    Requires Docker (for container workers) and LLM credentials.

    Source reference: vendor/muteki/muteki/swarm/swarm.py,
                      vendor/muteki/apps/web/drivers.py

    This driver is defined but NOT verified runnable in environments without
    Docker and LLM credentials (see docs/MUTEKI_INTEGRATION_LIMITATIONS.md).
    """
    try:
        from apps.web.drivers import build_driver  # type: ignore[import]
    except ImportError:
        pass

    try:
        from muteki.swarm.swarm import Swarm  # type: ignore[import]
    except ImportError as exc:
        raise MutekiUnavailableError(
            f"Cannot import Muteki Swarm coordinator: {exc}"
        ) from exc

    # The Swarm coordinator requires a full worker lineup and sandbox. In real
    # mode the adapter defers to the web driver infrastructure rather than
    # constructing a Swarm directly, since the web driver has the complete
    # worker-config / sandbox-manager wiring.
    #
    # LIMITATION: In this environment, real mode cannot be end-to-end verified.
    # The adapter falls back to raising MutekiUnavailableError with a clear
    # diagnostic message. See docs/MUTEKI_INTEGRATION_LIMITATIONS.md.
    raise MutekiUnavailableError(
        "RealMutekiAdapter real mode requires Docker and LLM credentials. "
        "Set MUTEKI_MODE=mock_bridge for environments without these dependencies. "
        "See docs/MUTEKI_INTEGRATION_LIMITATIONS.md for details."
    )


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
        try:
            from apps.web.run_manager import RunManager  # type: ignore[import]
        except ImportError as exc:
            raise MutekiUnavailableError(
                f"Cannot import Muteki RunManager: {exc}"
            ) from exc
        try:
            return RunManager(sessions_root=self._config.sessions_root)
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
                await _real_swarm_driver(run_obj, challenge)

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
