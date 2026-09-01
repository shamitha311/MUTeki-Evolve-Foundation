from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _skip_smoke_by_default(monkeypatch):
    """Bypass the Genius smoke gate for all tests by default.

    Most tests use trivially hardcoded solvers that can't satisfy the
    adversarial smoke cases under `genius/smoke_cases/`. Tests that
    specifically exercise the smoke gate must do `monkeypatch.delenv(...)`
    themselves to re-enable it.
    """
    monkeypatch.setenv("GENIUS_SKIP_SMOKE", "1")
