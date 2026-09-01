"""Adapter configuration read from the environment.

Only variables with a real corresponding Muteki requirement are defined here.
No variable is invented without purpose.

Environment variables:
  MUTEKI_TIMEOUT_SECONDS
      How long the adapter will wait for RUN_FINISHED before triggering a
      timeout. Default: 300.0 seconds.

  MUTEKI_EVENT_TIMEOUT_SECONDS
      How long the adapter will wait between events before treating the
      connection as stalled (only used in polling paths). Default: 30.0 s.

  MUTEKI_MODE
      "real"        — attempt to drive a real Muteki Swarm (requires Docker
                      and LLM credentials; fails closed if unavailable).
      "mock_bridge" — drive the real Muteki EventBus + SessionStore using
                      Muteki's own run_mock_solve() demo script as the driver.
                      No Docker or LLM credential required.
      Default: "mock_bridge" (safe for environments without credentials).

  MUTEKI_SESSIONS_ROOT
      Directory where Muteki's RunManager stores session JSONL files. Mirrors
      the upstream MUTEKI_SESSIONS_ROOT environment variable.
      Default: "sessions".
"""

from __future__ import annotations

import os

__all__ = ["AdapterConfig", "load_config"]


class AdapterConfig:
    """Immutable configuration snapshot for one adapter instance."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 300.0,
        event_timeout_seconds: float = 30.0,
        mode: str = "mock_bridge",
        sessions_root: str = "sessions",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if event_timeout_seconds <= 0:
            raise ValueError("event_timeout_seconds must be positive")
        if mode not in ("real", "mock_bridge"):
            raise ValueError(f"mode must be 'real' or 'mock_bridge', got: {mode!r}")
        self.timeout_seconds = float(timeout_seconds)
        self.event_timeout_seconds = float(event_timeout_seconds)
        self.mode = mode
        self.sessions_root = str(sessions_root) or "sessions"

    def __repr__(self) -> str:
        return (
            f"AdapterConfig(mode={self.mode!r}, "
            f"timeout_seconds={self.timeout_seconds}, "
            f"sessions_root={self.sessions_root!r})"
        )


def _env_float(name: str, default: float) -> float:
    try:
        value = os.environ.get(name)
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return str(value) if value not in (None, "") else default


def load_config() -> AdapterConfig:
    """Read adapter configuration from the environment.

    Callers may also construct AdapterConfig directly to override values
    in tests without touching the environment.
    """
    return AdapterConfig(
        timeout_seconds=_env_float("MUTEKI_TIMEOUT_SECONDS", 300.0),
        event_timeout_seconds=_env_float("MUTEKI_EVENT_TIMEOUT_SECONDS", 30.0),
        mode=_env_str("MUTEKI_MODE", "mock_bridge"),
        sessions_root=_env_str("MUTEKI_SESSIONS_ROOT", "sessions"),
    )
