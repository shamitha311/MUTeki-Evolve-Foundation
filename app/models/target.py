"""Trusted investigation target contracts."""

from collections.abc import Mapping

from pydantic import Field, field_validator

from ._base import ContractModel


class SandboxTarget(ContractModel):
    """An infrastructure-owned target; strategies never carry one."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    runtime_reference: str = Field(min_length=1)


class TrustedTargetRegistry:
    """Small explicit allow-list used by orchestration and adapter boundaries."""

    def __init__(self, targets: Mapping[str, SandboxTarget] | None = None) -> None:
        self._targets: dict[str, SandboxTarget] = {}
        for target in (targets or {}).values():
            self.register(target)

    def register(self, target: SandboxTarget) -> None:
        if not isinstance(target, SandboxTarget):
            raise TypeError("only SandboxTarget instances may enter the registry")
        if target.id in self._targets and self._targets[target.id] != target:
            raise ValueError(f"trusted target id already registered: {target.id}")
        self._targets[target.id] = target

    def resolve(self, target_id: str) -> SandboxTarget:
        try:
            return self._targets[target_id]
        except KeyError as exc:
            raise KeyError(f"target is not trusted: {target_id}") from exc

    def contains(self, target: SandboxTarget) -> bool:
        return self._targets.get(target.id) == target

    def __len__(self) -> int:
        return len(self._targets)
