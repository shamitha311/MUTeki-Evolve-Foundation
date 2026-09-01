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
      "real"        — attempt to drive a real Muteki Swarm over HTTP (requires
                      MUTEKI_BACKEND, Docker, and LLM credentials; fails closed
                      if unavailable).
      "mock_bridge" — drive the real Muteki EventBus + SessionStore using
                      Muteki's own run_mock_solve() demo script as the driver.
                      No Docker or LLM credential required.
      Default: "mock_bridge" (safe for environments without credentials).

  MUTEKI_BACKEND
      Base URL of the Muteki web API (e.g. http://127.0.0.1:8000).
      Used in "real" mode to POST /api/runs and stream SSE events.
      Leave unset when running in-process (mock_bridge).
      Default: "" (empty — in-process RunManager is used).

  MUTEKI_WORKER_ENGINE
      Engine identifier for the worker profile sent to Muteki.
      Maps to the "engine" field in the worker_profiles list.
      Default: "grok" (uses XAI_API_KEY — no subscription required).
      Alternatives: "codex" (Codex CLI subscription), "claude" (Claude CLI subscription),
        "kimi", "opencode", "dsh" (all accept API_KEY).

  XAI_API_KEY
      xAI API key for the Grok engine worker. Required when MUTEKI_WORKER_ENGINE=grok
      in real mode. Set in environment or .env file. The Grok CLI reads this
      automatically from its environment — no `grok login` needed when using an API key.
      Get yours at: https://console.x.ai
      Default: "" (must be provided for real mode with grok).

  MUTEKI_WORKER_MODEL
      Model override passed to the worker profile's "model" field.
      Leave empty (default) — the CLI picks its own model from the authenticated
      account. Set to e.g. "grok-3" or "grok-3-mini" to force a specific model.
      Default: "" (empty — CLI picks its own).

  MUTEKI_WORKER_BACKEND
      Execution backend for the worker: "local" (host subprocess) or
      "container" (Docker). Mirrors MUTEKI_WORKER_BACKEND upstream.
      Default: "local".

  MUTEKI_SESSIONS_ROOT
      Directory where Muteki's RunManager stores session JSONL files. Mirrors
      the upstream MUTEKI_SESSIONS_ROOT environment variable.
      Default: "sessions".
"""

from __future__ import annotations

import os
from pathlib import Path


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
        backend_url: str = "",
        worker_engine: str = "codex",
        worker_model: str = "",
        worker_backend: str = "local",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if event_timeout_seconds <= 0:
            raise ValueError("event_timeout_seconds must be positive")
        if mode not in ("real", "mock_bridge"):
            raise ValueError(f"mode must be 'real' or 'mock_bridge', got: {mode!r}")
        if worker_backend not in ("local", "container"):
            raise ValueError(f"worker_backend must be 'local' or 'container', got: {worker_backend!r}")
        self.timeout_seconds = float(timeout_seconds)
        self.event_timeout_seconds = float(event_timeout_seconds)
        self.mode = mode
        self.sessions_root = str(sessions_root) or "sessions"
        # Strip trailing slash for consistent URL construction.
        self.backend_url = str(backend_url).rstrip("/") if backend_url else ""
        self.worker_engine = str(worker_engine) if worker_engine else "grok"
        # model="" → CLI picks its own model from authenticated account.
        self.worker_model = str(worker_model) if worker_model else ""
        self.worker_backend = str(worker_backend) if worker_backend else "local"

    @property
    def http_mode(self) -> bool:
        """True when a remote Muteki backend URL is configured."""
        return bool(self.backend_url)

    def __repr__(self) -> str:
        return (
            f"AdapterConfig(mode={self.mode!r}, "
            f"timeout_seconds={self.timeout_seconds}, "
            f"sessions_root={self.sessions_root!r}, "
            f"backend_url={self.backend_url!r}, "
            f"worker_engine={self.worker_engine!r}, "
            f"worker_model={self.worker_model!r}, "
            f"worker_backend={self.worker_backend!r})"
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
    # Auto-configure MUTEKI_GROK_BIN if unset, pointing to bundled bridge binary
    if not os.environ.get("MUTEKI_GROK_BIN"):
        project_root = Path(__file__).resolve().parent.parent
        bin_ext = ".cmd" if os.name == "nt" else ""
        bundled_bin = project_root / "bin" / f"grok{bin_ext}"
        if bundled_bin.exists():
            os.environ["MUTEKI_GROK_BIN"] = str(bundled_bin)

    return AdapterConfig(
        timeout_seconds=_env_float("MUTEKI_TIMEOUT_SECONDS", 300.0),
        event_timeout_seconds=_env_float("MUTEKI_EVENT_TIMEOUT_SECONDS", 30.0),
        mode=_env_str("MUTEKI_MODE", "mock_bridge"),
        sessions_root=_env_str("MUTEKI_SESSIONS_ROOT", "sessions"),
        backend_url=_env_str("MUTEKI_BACKEND", ""),
        worker_engine=_env_str("MUTEKI_WORKER_ENGINE", "grok"),
        worker_model=_env_str("MUTEKI_WORKER_MODEL", ""),
        worker_backend=_env_str("MUTEKI_WORKER_BACKEND", "local"),
    )

