"""Tests for muteki_adapter.validators — the adapter-level fail-closed gate.

These tests verify that:
  1. Valid inputs pass through
  2. Invalid/untrusted targets are rejected
  3. Strategy safety violations are caught
  4. runtime_reference overrides are rejected
  5. Nested forbidden fields are caught
  6. All validation errors have the correct kind
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import SandboxTarget, Strategy, TrustedTargetRegistry
from app.validation import StrategyValidationError
from muteki_adapter.validators import (
    assert_no_strategy_target_field,
    assert_runtime_reference_present,
    validate_adapter_inputs,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _target(
    runtime_reference: str = "mock://sandbox-1",
    name: str = "Test Sandbox",
) -> SandboxTarget:
    return SandboxTarget(
        id="t1",
        name=name,
        description="A trusted test target",
        runtime_reference=runtime_reference,
    )


def _trusted_registry(target: SandboxTarget | None = None) -> TrustedTargetRegistry:
    t = target or _target()
    return TrustedTargetRegistry({t.id: t})


def _strategy(**kwargs) -> Strategy:
    defaults = {"objective": "Understand the sandbox"}
    defaults.update(kwargs)
    return Strategy(**defaults)


# ── validate_adapter_inputs ───────────────────────────────────────────────────

def test_valid_target_and_strategy_pass():
    """A trusted target + safe strategy must be accepted."""
    target = _target()
    strategy = _strategy()
    registry = _trusted_registry(target)
    result = validate_adapter_inputs(target, strategy, registry)
    assert result.objective == "Understand the sandbox"


def test_strategy_returned_on_success():
    """The approved Strategy is returned (caller uses it)."""
    target = _target()
    strategy = _strategy(priorities=["recon"], revision=2, parent_revision=1)
    registry = _trusted_registry(target)
    result = validate_adapter_inputs(target, strategy, registry)
    assert result.revision == 2
    assert result.priorities == ["recon"]


def test_untrusted_target_rejected():
    """A target whose runtime_reference was tampered must be rejected."""
    target = _target()
    registry = _trusted_registry(target)
    tampered = target.model_copy(update={"runtime_reference": "mock://evil"})
    with pytest.raises(StrategyValidationError) as exc_info:
        validate_adapter_inputs(tampered, _strategy(), registry)
    assert exc_info.value.kind == "target"


def test_unknown_target_rejected():
    """A target whose id is not in the registry must be rejected."""
    target = _target()
    other_target = SandboxTarget(
        id="other", name="Other", description="x",
        runtime_reference="mock://other",
    )
    registry = _trusted_registry(target)  # only contains "t1"
    with pytest.raises(StrategyValidationError) as exc_info:
        validate_adapter_inputs(other_target, _strategy(), registry)
    assert exc_info.value.kind == "target"


def test_empty_registry_rejects_any_target():
    """An empty registry should reject any target."""
    target = _target()
    registry = TrustedTargetRegistry({})
    with pytest.raises(StrategyValidationError) as exc_info:
        validate_adapter_inputs(target, _strategy(), registry)
    assert exc_info.value.kind == "target"


# ── assert_no_strategy_target_field ──────────────────────────────────────────

def test_strategy_with_runtime_reference_in_context_rejected():
    """A strategy carrying runtime_reference in context must be rejected."""
    # The Strategy model validator should catch this, but test our layer too.
    with pytest.raises((StrategyValidationError, Exception)):
        # Strategy.__init__ validator should reject this at construction time
        Strategy(objective="x", context={"runtime_reference": "mock://override"})


def test_strategy_with_target_field_in_context_rejected_by_app_validation():
    """Strategy carrying 'target' field in context must be rejected."""
    with pytest.raises(StrategyValidationError) as exc_info:
        from app.validation import validate_strategy
        validate_strategy({"objective": "x", "target": "other"})
    assert exc_info.value.kind == "safety"


def test_assert_no_strategy_target_field_passes_safe_strategy():
    """assert_no_strategy_target_field passes for a clean strategy."""
    strategy = _strategy(
        objective="Probe the web service for injection",
        priorities=["recon", "observe"],
        constraints=["stay scoped"],
    )
    # Should not raise
    assert_no_strategy_target_field(strategy)


def test_assert_no_strategy_target_field_with_dict_target_raises():
    """assert_no_strategy_target_field raises if a raw dict carries 'target'."""
    with pytest.raises(StrategyValidationError) as exc_info:
        assert_no_strategy_target_field({"objective": "x", "target": "evil"})
    assert exc_info.value.kind == "safety"


def test_assert_no_strategy_target_field_nested_forbidden_key():
    """A nested forbidden key (deep dict) is detected."""
    with pytest.raises(StrategyValidationError) as exc_info:
        assert_no_strategy_target_field({
            "objective": "x",
            "context": {"env": {"runtime_reference": "override"}}
        })
    assert exc_info.value.kind == "safety"


def test_assert_no_strategy_target_field_nested_in_list():
    """A forbidden key nested inside a list element is detected."""
    with pytest.raises(StrategyValidationError) as exc_info:
        assert_no_strategy_target_field({
            "objective": "x",
            "priorities": [{"docker": "run evil"}],
        })
    assert exc_info.value.kind == "safety"


# ── assert_runtime_reference_present ─────────────────────────────────────────

def test_runtime_reference_present_passes():
    """A target with a valid runtime_reference passes the check."""
    assert_runtime_reference_present(_target("mock://valid"))


def test_runtime_reference_whitespace_only_rejected():
    """A runtime_reference of only whitespace is rejected."""
    # SandboxTarget enforces min_length=1, so we must bypass model validation
    # to test our belt-and-suspenders check.
    target = _target("mock://valid")
    object.__setattr__(target, "runtime_reference", "   ")
    with pytest.raises(StrategyValidationError) as exc_info:
        assert_runtime_reference_present(target)
    assert exc_info.value.kind == "target"


def test_runtime_reference_empty_rejected():
    """An empty runtime_reference is rejected at the adapter boundary."""
    target = _target("mock://valid")
    object.__setattr__(target, "runtime_reference", "")
    with pytest.raises(StrategyValidationError) as exc_info:
        assert_runtime_reference_present(target)
    assert exc_info.value.kind == "target"


# ── Strategy model-level safety ───────────────────────────────────────────────

def test_strategy_model_rejects_command_in_context():
    """Strategy model validator rejects execution commands in context."""
    with pytest.raises(ValidationError):
        Strategy(objective="observe", context={"command": "rm -rf /"})


def test_strategy_model_rejects_nested_exec():
    """Strategy model validator rejects nested exec in context."""
    with pytest.raises(ValidationError):
        Strategy(objective="observe", context={"nested": {"exec": "bad"}})


def test_strategy_model_allows_safe_context():
    """Strategy model allows benign context entries."""
    strategy = Strategy(
        objective="observe the web service",
        context={"category": "web", "notes": "check JS endpoints"},
    )
    assert strategy.context["category"] == "web"


def test_validate_adapter_inputs_full_rejection_on_strategy_safety():
    """Full validate_adapter_inputs rejects a strategy with forbidden fields."""
    target = _target()
    registry = _trusted_registry(target)
    with pytest.raises(StrategyValidationError) as exc_info:
        from app.validation import validate_strategy
        bad_strategy = validate_strategy({"objective": "x", "runtime_reference": "hack"})
    assert exc_info.value.kind == "safety"


def test_strategy_with_priorities_and_constraints_passes():
    """Strategy with multiple safe priorities and constraints is accepted."""
    target = _target()
    strategy = _strategy(
        objective="Identify exposed services and assess authentication",
        priorities=["enumerate ports", "test authentication", "probe APIs"],
        constraints=["do not modify files", "read-only access only"],
        revision=3,
        parent_revision=2,
    )
    registry = _trusted_registry(target)
    result = validate_adapter_inputs(target, strategy, registry)
    assert len(result.priorities) == 3
    assert len(result.constraints) == 2
    assert result.revision == 3
