"""Fail-closed validation pipeline owned by the application."""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.models import SandboxTarget, Strategy, TrustedTargetRegistry


class StrategyValidationError(ValueError):
    """Controlled error returned when a generated strategy is not admissible."""

    def __init__(self, message: str, *, kind: str = "schema") -> None:
        super().__init__(message)
        self.kind = kind


_FORBIDDEN_KEYS = {
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


def _find_forbidden_key(value: Any, path: str = "strategy") -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).strip().lower()
            current = f"{path}.{key_text}"
            if key_text in _FORBIDDEN_KEYS:
                return current
            found = _find_forbidden_key(nested, current)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found = _find_forbidden_key(nested, f"{path}[{index}]")
            if found:
                return found
    return None


def validate_strategy(raw_strategy: Strategy | Mapping[str, Any]) -> Strategy:
    """Validate schema and reject target-control/execution behavior.

    Generated content is rejected, never silently sanitized. This is the first
    boundary; the Muteki adapter must call it again before execution.
    """

    if isinstance(raw_strategy, Strategy):
        return raw_strategy
    if not isinstance(raw_strategy, Mapping):
        raise StrategyValidationError(
            "strategy must be a mapping or Strategy instance", kind="schema"
        )
    forbidden = _find_forbidden_key(raw_strategy)
    if forbidden:
        raise StrategyValidationError(
            f"strategy contains forbidden target-control or execution field: {forbidden}",
            kind="safety",
        )
    try:
        return Strategy.model_validate(dict(raw_strategy))
    except ValidationError as exc:
        raise StrategyValidationError(
            f"strategy schema validation failed: {exc.errors()}",
            kind="schema",
        ) from exc


def validate_target(
    target: SandboxTarget, registry: TrustedTargetRegistry
) -> SandboxTarget:
    if not isinstance(target, SandboxTarget):
        raise StrategyValidationError(
            "target must be a project-owned SandboxTarget", kind="target"
        )
    if not registry.contains(target):
        raise StrategyValidationError(
            f"target is not present in the trusted target registry: {target.id}",
            kind="target",
        )
    return target


def approve_strategy(
    target: SandboxTarget,
    raw_strategy: Strategy | Mapping[str, Any],
    registry: TrustedTargetRegistry,
) -> Strategy:
    """Run target validation and strategy validation before adapter admission."""

    validate_target(target, registry)
    return validate_strategy(raw_strategy)
