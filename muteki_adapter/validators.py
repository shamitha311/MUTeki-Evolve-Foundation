"""Adapter-level fail-closed validation.

This module duplicates and extends the application-level validation from
app.validation. The adapter must re-validate immediately before any Muteki
admission, as required by docs/INTEGRATION_CONTRACT.md:

  "The adapter repeats target and strategy validation immediately before
   upstream admission."

None of these functions silently sanitize invalid inputs. They raise
StrategyValidationError on any violation.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping

from app.models import SandboxTarget, Strategy, TrustedTargetRegistry
from app.validation import StrategyValidationError, approve_strategy

__all__ = [
    "validate_adapter_inputs",
    "assert_no_strategy_target_field",
    "assert_runtime_reference_present",
]

# Keys that must never appear anywhere in a Strategy mapping at any depth.
# Mirrored from app.validation._FORBIDDEN_KEYS to apply adapter-level checks.
_FORBIDDEN_STRATEGY_FIELDS = {
    "target",
    "target_id",
    "target_override",
    "runtime_reference",
    "runtime",
    "shell",
    "command",
    "commands",
    "cmd",
    "exec",
    "execute",
    "docker",
    "host_execution",
    "external_destination",
    "sandbox_escape",
    "worker_command",
}


def _deep_find_key(obj: Any, forbidden: set[str], path: str = "") -> str | None:
    """Return the dotted path of the first forbidden key found, or None."""
    if isinstance(obj, Mapping):
        for key, val in obj.items():
            key_norm = str(key).strip().lower()
            current = f"{path}.{key_norm}" if path else key_norm
            if key_norm in forbidden:
                return current
            found = _deep_find_key(val, forbidden, current)
            if found:
                return found
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            found = _deep_find_key(item, forbidden, f"{path}[{i}]")
            if found:
                return found
    return None


def assert_no_strategy_target_field(strategy: Strategy | Mapping[str, Any]) -> None:
    """Raise StrategyValidationError if the strategy contains any target-control
    or execution field at any nesting depth.

    This is the adapter's own safety gate, applied in addition to the schema-
    level check in Strategy.__init__ and app.validation.validate_strategy.
    """
    # For a fully-constructed Strategy object, the model_validator already
    # ran at construction time. Still check the dict representation because
    # a future code path might pass a raw mapping here.
    if isinstance(strategy, Strategy):
        data: Any = strategy.model_dump()
    else:
        data = strategy

    found = _deep_find_key(data, _FORBIDDEN_STRATEGY_FIELDS)
    if found:
        raise StrategyValidationError(
            f"adapter: strategy contains forbidden target-control or execution "
            f"field: {found}",
            kind="safety",
        )


def assert_runtime_reference_present(target: SandboxTarget) -> None:
    """Raise StrategyValidationError if target.runtime_reference is empty.

    This is a belt-and-suspenders check; SandboxTarget already requires
    min_length=1 on runtime_reference, so this only triggers if the model
    invariant is somehow bypassed.
    """
    if not target.runtime_reference or not target.runtime_reference.strip():
        raise StrategyValidationError(
            f"adapter: target {target.id!r} has an empty runtime_reference; "
            "refusing to start Muteki",
            kind="target",
        )


def validate_adapter_inputs(
    target: SandboxTarget,
    strategy: Strategy,
    registry: TrustedTargetRegistry,
) -> Strategy:
    """Full fail-closed validation immediately before Muteki admission.

    Performs, in order:
    1. Target type check and registry membership
    2. runtime_reference presence
    3. Strategy schema and safety re-validation
    4. Adapter-level strategy safety scan

    Returns the approved Strategy on success.
    Raises StrategyValidationError on any violation.
    No Muteki run is started if this raises.
    """
    # Step 1 & 3: app-level combined target + strategy check
    approved = approve_strategy(target, strategy, registry)

    # Step 2: belt-and-suspenders runtime_reference check
    assert_runtime_reference_present(target)

    # Step 4: adapter-level redundant strategy safety scan (catches any gap
    # between construction-time and admission-time mutations)
    assert_no_strategy_target_field(approved)

    return approved
