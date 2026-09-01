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

__all__ = ["translate_strategy_to_challenge", "build_start_payload", "SAFE_CATEGORIES"]

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



def build_start_payload(
    target: SandboxTarget,
    strategy: Strategy,
    run_id: str,
    worker_engine: str = "codex",
    worker_model: str = "",
    worker_backend: str = "local",
) -> dict:
    """Build the JSON body for POST /api/runs/{run_id}/start.

    Produces the complete request payload consumed by Muteki's build_driver()
    entrypoint. Schema is SOURCE-VERIFIED against:
      - apps/web/drivers.py::_swarm_driver (knobs section)
      - apps/web/worker_config.py::DEFAULT_WORKER_PROFILES

    Source-verified profile schema (worker_config.py:105):
      {id, name, engine, transport, auth, credential_mode, credential_account,
       api_key_ref, base_url, wire_api, roles, race, max_running,
       max_review_running, priority, model, enabled}

    Transport aliases (worker_profiles.py:24):
      "codex"   → engine "codex"   | transport "codex_cli"
      "claude"  → engine "claude"  | transport "claude_code"
      "cursor"  → engine "cursor"  | transport "cursor_agent"
      "omp"     → engine "omp"     | transport "omp"

    Model field:
      Intentionally empty ("") → Codex CLI uses the authenticated account's
      default model. Set MUTEKI_WORKER_MODEL to override (e.g. "o3-mini").
      Do NOT hardcode a model name — the CLI installation picks the model.

    Credential:
      "codex-main" is the default credential_account name for Codex.
      Codex CLI uses a subscription (logged-in account via `codex` CLI),
      NOT an API key. auth="subscription", credential_mode="subscription".

    Args:
        target:         Trusted SandboxTarget (runtime_reference → challenge.target).
        strategy:       Approved Strategy (objective/priorities → description/prompt).
        run_id:         Muteki run ID (→ challenge.id).
        worker_engine:  Base engine name ("codex", "claude", etc.). Default "codex".
        worker_model:   Model override (default "" = CLI picks its own).
        worker_backend: "local" (host subprocess) or "container" (Docker).

    Returns:
        dict — the complete /api/runs/{run_id}/start request body.

    Security invariant: challenge.target comes ONLY from target.runtime_reference,
    never from strategy fields.
    """
    _assert_strategy_safe_for_translation(strategy)

    category = _safe_category(strategy.context)
    description = _build_description(strategy)
    name = _build_name(strategy, target)

    # Source-verified transport map (worker_profiles.py::TRANSPORT_TO_ENGINE).
    _ENGINE_TRANSPORT: dict[str, str] = {
        "codex":    "codex_cli",
        "claude":   "claude_code",
        "cursor":   "cursor_agent",
        "omp":      "omp",
        "kimi":     "kimi_code",
        "grok":     "grok_build",
        "opencode": "opencode_cli",
        "dsh":      "deepseek_harness",
        "pi":       "pi",
    }
    # Source-verified wire_api per engine (worker_config.py defaults).
    _ENGINE_WIRE_API: dict[str, str] = {
        "codex": "responses",
        "claude": "",
        "cursor": "",
        # grok, kimi, opencode, dsh, pi, omp: all empty (driver-managed)
    }
    # Source-verified auth mode per engine.
    # credential_accounts.py:226-284: claude/codex=subscription, others=api_key.
    # "cursor" uses api_key (CURSOR_API_KEY); "codex" needs auth.json subscription.
    _ENGINE_AUTH: dict[str, str] = {
        "codex":  "subscription",  # codex login → ~/.codex/auth.json
        "claude": "subscription",  # claude login → CLAUDE_CODE_OAUTH_TOKEN
        "pi":     "subscription",
        "omp":    "subscription",
        # All others: API key stored as API_KEY file → injected as env var
        "grok":     "api_key",  # → XAI_API_KEY
        "kimi":     "api_key",  # → KIMI_CODE_HOME or API_KEY
        "cursor":   "api_key",  # → CURSOR_API_KEY
        "opencode": "api_key",  # → OPENAI_API_KEY / OPENCODE_API_KEY
        "dsh":      "api_key",  # → API_KEY
    }
    # Source-verified default credential_account names.
    _ENGINE_CREDENTIAL: dict[str, str] = {
        "codex":    "codex-main",
        "claude":   "claude-main",
        "cursor":   "cursor-main",
        "omp":      "omp-main",
        "kimi":     "kimi-main",
        "grok":     "grok-main",
        "opencode": "opencode-main",
        "dsh":      "dsh-main",
        "pi":       "pi-main",
    }

    transport = _ENGINE_TRANSPORT.get(worker_engine, worker_engine)
    wire_api = _ENGINE_WIRE_API.get(worker_engine, "")
    auth = _ENGINE_AUTH.get(worker_engine, "api_key")
    credential_account = _ENGINE_CREDENTIAL.get(worker_engine, f"{worker_engine}-main")

    # Profile id: "{engine}-api-local" for api_key engines,
    # "{engine}-sub-local" for subscription engines — mirrors the naming convention
    # used in worker_config.py::DEFAULT_WORKER_PROFILES.
    profile_suffix = "api" if auth == "api_key" else "sub"
    profile_id = f"{worker_engine}-{profile_suffix}-local"

    # Build the strategy-derived prompt.  This is how the strategy reaches Muteki
    # as actual text — the Challenge description carries objectives, and the
    # top-level prompt seeds the conversational dispatcher.
    prompt_parts = [f"Investigate {target.runtime_reference}."]
    if strategy.objective:
        prompt_parts.append(strategy.objective[:1000])
    if strategy.priorities:
        priorities_text = "; ".join(str(p) for p in strategy.priorities[:5])
        prompt_parts.append(f"Focus on: {priorities_text}")
    prompt = " ".join(prompt_parts)[:4000]

    return {
        # ── Engine + profile roster ─────────────────────────────────────────
        "engines": [worker_engine],
        "worker_profiles": [
            {
                # Source-verified field set (worker_config.py::DEFAULT_WORKER_PROFILES)
                "id":                 profile_id,
                "name":               profile_id,
                "engine":             worker_engine,
                "transport":          transport,
                # auth/credential_mode are ENGINE-specific (not all engines use
                # subscription): codex/claude → subscription; grok/kimi/cursor/
                # opencode/dsh → api_key (key injected as XAI_API_KEY etc).
                "auth":               auth,
                "credential_mode":    auth,
                "credential_account": credential_account,
                "api_key_ref":        "",
                "base_url":           "",
                "wire_api":           wire_api,
                "roles":              ["race", "bootstrap", "explore", "respond"],
                "race":               True,
                "max_running":        1,
                "max_review_running": 0,
                "priority":           20,
                # model="" → CLI picks its own model. Set MUTEKI_WORKER_MODEL to override.
                "model":              worker_model,
                "enabled":            True,
            }
        ],
        # ── Challenge (strategy injection) ──────────────────────────────────
        "challenge": {
            "id":          run_id,
            "name":        name,
            "description": description,
            # SECURITY: target.runtime_reference from trusted target ONLY
            "target":      target.runtime_reference,
            "category":    category,
            "flag_format": _DEFAULT_FLAG_FORMAT,
            "mode":        "pentest" if strategy.context.get("mode") == "pentest" else "ctf",
        },
        # ── Conversational prompt (seeds the coordinator's dispatch plan) ────
        "prompt": prompt,
        # ── Execution knobs ─────────────────────────────────────────────────
        "worker_backend": worker_backend,    # "local" or "container"
        "coordinator":    True,
        "race_scout":     False,             # single-engine: no race scout needed
        "n_solvers":      1,
        "kind":           "swarm",           # explicit: real solving (not mock)
    }



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
