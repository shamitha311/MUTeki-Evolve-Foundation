"""Tests for muteki_adapter.translator — Strategy → Muteki Challenge translation.

Key invariant verified in every test:
  target.runtime_reference → Challenge.target (ALWAYS from the trusted target)
  strategy content → Challenge.description (never used as target address)
"""

from __future__ import annotations

import pytest

from app.models import SandboxTarget, Strategy
from muteki_adapter.translator import (
    SAFE_CATEGORIES,
    _safe_category,
    translate_strategy_to_challenge,
)

# Import guard: translator imports muteki.models.solve_graph.Challenge.
# If Muteki is not on the path, all translator tests are skipped.
try:
    from muteki.models.solve_graph import Challenge  # type: ignore[import]
    MUTEKI_AVAILABLE = True
except ImportError:
    MUTEKI_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not MUTEKI_AVAILABLE,
    reason="vendor/muteki must be on sys.path; run from the project root with the vendor path configured",
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _target(
    runtime_reference: str = "mock://target-1",
    name: str = "Test Sandbox",
) -> SandboxTarget:
    return SandboxTarget(
        id="t1",
        name=name,
        description="A trusted test target",
        runtime_reference=runtime_reference,
    )


def _strategy(**kwargs) -> Strategy:
    defaults = {"objective": "Investigate the web service"}
    defaults.update(kwargs)
    return Strategy(**defaults)


def _translate(target=None, strategy=None, run_id="test-run-1") -> Challenge:
    return translate_strategy_to_challenge(
        target or _target(),
        strategy or _strategy(),
        run_id,
    )


# ── Challenge.id == run_id ────────────────────────────────────────────────────

def test_challenge_id_equals_run_id():
    """Challenge.id must be exactly the run_id passed to the translator."""
    ch = _translate(run_id="my-unique-run-abc")
    assert ch.id == "my-unique-run-abc"


def test_challenge_id_is_unique_per_call():
    """Different run_ids produce different Challenge.id values."""
    ch1 = _translate(run_id="run-001")
    ch2 = _translate(run_id="run-002")
    assert ch1.id != ch2.id


# ── runtime_reference → Challenge.target (from trusted target ONLY) ──────────

def test_runtime_reference_maps_to_challenge_target():
    """Challenge.target comes from target.runtime_reference, not from strategy."""
    target = _target(runtime_reference="mock://specific-sandbox")
    ch = _translate(target=target)
    assert ch.target == "mock://specific-sandbox"


def test_strategy_content_cannot_override_challenge_target():
    """Strategy objective content does not affect Challenge.target."""
    target = _target(runtime_reference="mock://real-target")
    # Even if strategy mentions an address in the objective, it goes to description only
    strategy = _strategy(
        objective="Investigate mock://evil-address to check for injection"
    )
    ch = _translate(target=target, strategy=strategy)
    # target must come from the trusted SandboxTarget
    assert ch.target == "mock://real-target"
    # objective text goes to description, not target
    assert "mock://evil-address" in ch.description or "mock://evil-address" not in ch.target


def test_different_targets_produce_different_challenge_targets():
    """Different trusted targets produce different Challenge.target values."""
    ch1 = _translate(target=_target("mock://alpha"), run_id="r1")
    ch2 = _translate(target=_target("mock://beta"), run_id="r2")
    assert ch1.target == "mock://alpha"
    assert ch2.target == "mock://beta"


# ── strategy.objective → Challenge.description ────────────────────────────────

def test_strategy_objective_appears_in_description():
    """Strategy.objective is represented in the Challenge description."""
    strategy = _strategy(objective="Probe the web service for SQL injection")
    ch = _translate(strategy=strategy)
    assert "Probe the web service for SQL injection" in ch.description


def test_strategy_priorities_appear_in_description():
    """Strategy priorities contribute to the Challenge description."""
    strategy = _strategy(
        objective="Test authentication",
        priorities=["enumerate endpoints", "test cookies"],
    )
    ch = _translate(strategy=strategy)
    assert "enumerate endpoints" in ch.description


def test_strategy_constraints_appear_in_description():
    """Strategy constraints contribute to the Challenge description."""
    strategy = _strategy(
        objective="Audit the API",
        constraints=["read-only only", "no modifications"],
    )
    ch = _translate(strategy=strategy)
    assert "read-only only" in ch.description


def test_description_length_bounded():
    """Very long strategy content is truncated to protect Muteki."""
    strategy = _strategy(objective="x" * 10_000)
    ch = _translate(strategy=strategy)
    assert len(ch.description) <= 4100  # max + small overhead


# ── Category whitelist ────────────────────────────────────────────────────────

def test_valid_category_from_context_is_used():
    """A whitelisted category from strategy.context passes through."""
    for cat in SAFE_CATEGORIES:
        strategy = _strategy(context={"category": cat})
        ch = _translate(strategy=strategy)
        assert ch.category == cat


def test_unknown_category_defaults_to_misc():
    """An unknown category value defaults to 'misc'."""
    strategy = _strategy(context={"category": "pwned-my-category"})
    ch = _translate(strategy=strategy)
    assert ch.category == "misc"


def test_missing_category_defaults_to_misc():
    """A missing category context key defaults to 'misc'."""
    strategy = _strategy()
    ch = _translate(strategy=strategy)
    assert ch.category == "misc"


def test_none_category_defaults_to_misc():
    """An explicit None category defaults to 'misc'."""
    strategy = _strategy(context={"category": None})
    ch = _translate(strategy=strategy)
    assert ch.category == "misc"


def test_category_is_case_normalized():
    """Category is lowercased before checking against whitelist."""
    strategy = _strategy(context={"category": "WEB"})
    ch = _translate(strategy=strategy)
    assert ch.category == "web"


# ── _safe_category unit tests ─────────────────────────────────────────────────

def test_safe_category_all_valid():
    for cat in SAFE_CATEGORIES:
        assert _safe_category({"category": cat}) == cat


def test_safe_category_invalid_returns_misc():
    assert _safe_category({"category": "sandbox_escape"}) == "misc"
    assert _safe_category({"category": ""}) == "misc"
    assert _safe_category({}) == "misc"
    assert _safe_category({"category": 42}) == "misc"


# ── Challenge.name ────────────────────────────────────────────────────────────

def test_challenge_name_contains_target_name():
    """Challenge.name includes the target name for traceability."""
    target = _target(name="Production Web App")
    ch = _translate(target=target)
    assert "Production Web App" in ch.name


def test_challenge_name_length_bounded():
    """Challenge name is bounded in length."""
    target = _target(name="A" * 300)
    ch = _translate(target=target)
    assert len(ch.name) <= 200
