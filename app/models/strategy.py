"""High-level strategy contract.

This model intentionally has no target or execution field. The absence is a
security boundary, not an omission: target selection belongs to orchestration.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import Field, model_validator

from ._base import ContractModel


_FORBIDDEN_CONTEXT_KEYS = {
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


def _find_forbidden_context_key(value: Any, path: str = "context") -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).strip().lower()
            current = f"{path}.{key_text}"
            if key_text in _FORBIDDEN_CONTEXT_KEYS:
                return current
            found = _find_forbidden_context_key(nested, current)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found = _find_forbidden_context_key(nested, f"{path}[{index}]")
            if found:
                return found
    return None


class Strategy(ContractModel):
    objective: str = Field(min_length=1)
    priorities: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    revision: int = Field(default=1, ge=1)
    parent_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_revision_lineage(self) -> "Strategy":
        if self.revision == 1 and self.parent_revision is not None:
            raise ValueError("revision 1 cannot have a parent_revision")
        if self.revision > 1 and self.parent_revision != self.revision - 1:
            raise ValueError(
                "revisions must point to the immediately preceding revision"
            )
        forbidden = _find_forbidden_context_key(self.context)
        if forbidden:
            raise ValueError(
                f"strategy contains forbidden target-control or execution field: "
                f"{forbidden}"
            )
        return self
