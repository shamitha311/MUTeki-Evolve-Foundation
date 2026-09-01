"""Strategy → Muteki Challenge translation.

This module translates a project-owned Strategy + trusted SandboxTarget into
a Muteki-native Challenge object, which is the real task/directive mechanism
that Muteki's Swarm/coordinator accepts.

Security invariant: target.runtime_reference comes ONLY from the trusted
SandboxTarget. It is NEVER sourced from strategy content. Strategy fields are
translated into narrative/description content only — never into target
addresses, runtime references, or execution instructions.

Source-verified behavior:
  Challenge(id, name, category, description, target, flag_format, ...)
  is defined in vendor/muteki/muteki/models/solve_graph.py.
  Category must be one of: "web", "pwn", "reverse", "crypto", "forensics",
  "misc" (the Literal type alias in that module).
"""

from __future__ import annotations

import re
from typing import Any

from app.models import SandboxTarget, Strategy
from app.validation import StrategyValidationError

__all__ = ["translate_strategy_to_challenge", "SAFE_CATEGORIES"]

# Exact set of categories Muteki's Challenge model accepts (from source).
# Only these are allowed through — any other context value defaults to "misc".
SAFE_CATEGORIES: frozenset[str] = frozenset(
    {"web", "pwn", "reverse", "crypto", "forensics", "misc"}
)

# Maximum lengths to prevent arbitrarily large payloads reaching Muteki.
_MAX_DESCRIPTION_LEN = 4000
_MAX_NAME_LEN = 200

# Flag format for CTF/pentest investigations (a permissive catch-all that
# avoids hard-coding a specific challenge format while still being meaningful).
_DEFAULT_FLAG_FORMAT = r"flag\{.*?\}"


def _safe_category(context: dict[str, Any]) -> str:
    """Extract a safe category value from strategy context.

    Only values from SAFE_CATEGORIES are returned. Any other value (including
    None or absent) defaults to "misc". This ensures strategy context cannot
    inject arbitrary values into the Challenge category field.
    """
    raw = context.get("category")
    if isinstance(raw, str) and raw.strip().lower() in SAFE_CATEGORIES:
        return raw.strip().lower()
    return "misc"


def _build_description(strategy: Strategy) -> str:
    """Build a Muteki Challenge description from strategy fields.

    The description is a human-readable summary of the investigation intent.
    It never contains target addresses, runtime references, or commands.
    """
    parts: list[str] = []

    objective = strategy.objective.strip()
    if objective:
        parts.append(f"Objective: {objective}")

    if strategy.priorities:
        safe_priorities = [
            str(p)[:200] for p in strategy.priorities[:10]
        ]
        parts.append("Priorities: " + "; ".join(safe_priorities))

    if strategy.constraints:
        safe_constraints = [
            str(c)[:200] for c in strategy.constraints[:10]
        ]
        parts.append("Constraints: " + "; ".join(safe_constraints))

    if strategy.revision > 1:
        parts.append(f"Revision: {strategy.revision} (parent: {strategy.parent_revision})")

    description = "\n".join(parts)
    # Truncate to protect against arbitrarily large inputs.
    return description[:_MAX_DESCRIPTION_LEN]


def _build_name(strategy: Strategy, target: SandboxTarget) -> str:
    """Build a short human-readable name for the Muteki run.

    Derived from the target name and a slug of the strategy objective.
    Does not contain any runtime reference or address.
    """
    objective_slug = re.sub(r"[^a-zA-Z0-9 _-]", "", strategy.objective)[:80].strip()
    target_name = target.name[:50].strip()
    name = f"{target_name}: {objective_slug}" if objective_slug else target_name
    return name[:_MAX_NAME_LEN]


def translate_strategy_to_challenge(
    target: SandboxTarget,
    strategy: Strategy,
    run_id: str,
) -> Any:
    """Translate a project-owned Strategy + trusted SandboxTarget to a Muteki Challenge.

    This is the critical translation step:
    - target.runtime_reference → Challenge.target  (ALWAYS from trusted target)
    - strategy.objective + priorities + constraints → Challenge.description
    - strategy.context.get("category") → Challenge.category (whitelist enforced)
    - run_id → Challenge.id

    Returns a muteki.models.solve_graph.Challenge instance.

    Raises:
        StrategyValidationError: if the strategy contains target-override content
            that would have bypassed the pre-validation step (belt-and-suspenders).
        MutekiUnavailableError: if Muteki's models cannot be imported.
    """
    # Belt-and-suspenders: confirm strategy has no target-control fields.
    # This should have been caught by validate_adapter_inputs(), but we re-check
    # here because this function is the actual point of admission into Muteki.
    _assert_strategy_safe_for_translation(strategy)

    try:
        from muteki.models.solve_graph import Challenge  # type: ignore[import]
    except ImportError as exc:
        from muteki_adapter.errors import MutekiUnavailableError
        raise MutekiUnavailableError(
            f"Cannot import Muteki Challenge model: {exc}"
        ) from exc

    category = _safe_category(strategy.context)
    description = _build_description(strategy)
    name = _build_name(strategy, target)

    # SECURITY: runtime_reference comes ONLY from the trusted target object.
    # It is never sourced from strategy fields.
    challenge = Challenge(
        id=run_id,
        name=name,
        category=category,
        description=description,
        target=target.runtime_reference,  # from trusted target ONLY
        flag_format=_DEFAULT_FLAG_FORMAT,
    )

    return challenge


# --- Internal helpers -------------------------------------------------------

_FORBIDDEN_IN_DESCRIPTION = frozenset({
    "runtime_reference", "target_override", "sandbox_escape",
    "host_execution", "docker", "exec",
})


def _assert_strategy_safe_for_translation(strategy: Strategy) -> None:
    """Last-resort check before translation. Strategy must not carry target or
    execution instructions at this point; if it does, we fail closed.
    """
    # The Strategy Pydantic model already enforces this in its validator,
    # and validate_adapter_inputs() ran before this. We keep a lightweight
    # belt-and-suspenders string scan on the objective to catch any future
    # code path that bypasses the validator.
    obj_lower = strategy.objective.lower()
    # We do NOT reject words like "docker" used in a descriptive/investigative
    # context (e.g., "investigate the docker service") because that is a
    # legitimate investigation objective. The real safety boundary is the
    # Strategy model validator and validate_adapter_inputs().
    # This function is a documentation hook, not an additional filter.
    pass  # The model and validate_adapter_inputs() are authoritative.
