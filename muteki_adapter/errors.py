"""Structured adapter error types for the MUTeki-Evolve adapter boundary.

These are the only error types the rest of the application sees from the
adapter layer. No raw Muteki exceptions or stack traces are surfaced.
"""

from __future__ import annotations

from app.validation import StrategyValidationError  # re-export for callers

__all__ = [
    "MutekiAdapterError",
    "MutekiUnavailableError",
    "MutekiRunCreationError",
    "MutekiRunFailedError",
    "MutekiTimeoutError",
    "MutekiEventStreamError",
    "MutekiMalformedResultError",
    "StrategyValidationError",
]


class MutekiAdapterError(RuntimeError):
    """Base class for all adapter-layer errors.

    Callers can catch this to handle any adapter failure uniformly, or catch
    subclasses to handle specific failure modes.
    """

    def __init__(self, message: str, *, kind: str = "adapter_error") -> None:
        super().__init__(message)
        self.kind = kind


class MutekiUnavailableError(MutekiAdapterError):
    """Raised when the Muteki runtime cannot be reached or imported.

    The UI should display: "Muteki is currently unavailable."
    This is never silently swallowed — the run is never pretended to succeed.
    """

    def __init__(self, message: str = "Muteki runtime is unavailable") -> None:
        super().__init__(message, kind="muteki_unavailable")


class MutekiRunCreationError(MutekiAdapterError):
    """Raised when a Muteki run handle cannot be created or started."""

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="run_creation_failed")


class MutekiRunFailedError(MutekiAdapterError):
    """Raised when a Muteki run terminates with an internal failure.

    Note: a run that ends without a flag is NOT a MutekiRunFailedError — that
    is a normal (unsolved) InvestigationResult. This error is only for
    cases where Muteki itself failed to run.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="run_failed")


class MutekiTimeoutError(MutekiAdapterError):
    """Raised when the adapter-level timeout is exceeded before RUN_FINISHED.

    The adapter never marks a timed-out run as solved. Collected events and
    evidence are preserved in the returned InvestigationResult.
    """

    def __init__(self, message: str = "investigation timed out") -> None:
        super().__init__(message, kind="investigation_timeout")


class MutekiEventStreamError(MutekiAdapterError):
    """Raised when the Muteki event bus disconnects unexpectedly.

    Partial events collected before the disconnect are preserved.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="event_stream_error")


class MutekiMalformedResultError(MutekiAdapterError):
    """Raised when the RUN_FINISHED payload is malformed or unreadable.

    The adapter never invents a result to replace a missing one.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="malformed_result")
