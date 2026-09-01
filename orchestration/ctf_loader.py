"""CTF target loader — registers pre-approved real-world assessment targets.

This module provides helpers to register sanctioned CTF and pentest targets
into the TrustedTargetRegistry. Targets registered here have been explicitly
approved for autonomous investigation.

Security invariant: ONLY targets defined in this module (or registry.py) may
be registered as trusted. External code must never construct a SandboxTarget
with an arbitrary runtime_reference and inject it into the registry.

Approved target list (as of this version):
  - testphp.vulnweb.com  — Acunetix intentionally vulnerable demo application.
    This is a public, legal-to-scan demo target operated by Acunetix.
    Reference: https://www.acunetix.com/acunetix-website-security-scanner/

Usage:
    from orchestration.ctf_loader import load_ctf_targets
    from orchestration.registry import get_default_target_registry

    registry = get_default_target_registry()
    load_ctf_targets(registry)           # adds CTF targets to existing registry
    target = registry.resolve("vulnweb-testphp")

CLI:
    python -m orchestration.ctf_loader   # prints registered targets and exits
"""

from __future__ import annotations

from app.models import SandboxTarget, TrustedTargetRegistry

__all__ = ["load_ctf_targets", "CTF_TARGETS"]


# ---------------------------------------------------------------------------
# Approved target definitions
# ---------------------------------------------------------------------------

#: testphp.vulnweb.com — Acunetix intentionally vulnerable demo app.
#: This is a permanent, public demo target; scanning it is explicitly legal.
_VULNWEB_TESTPHP = SandboxTarget(
    id="vulnweb-testphp",
    name="testphp.vulnweb.com Assessment",
    description=(
        "Acunetix intentionally vulnerable PHP application at testphp.vulnweb.com. "
        "Provides a realistic attack surface for web vulnerability assessment: "
        "SQL injection, XSS, authentication issues, and more. "
        "Legal to scan — operated by Acunetix as a public demo target."
    ),
    runtime_reference="http://testphp.vulnweb.com",
)

#: All pre-approved CTF / pentest sandbox targets exported by this module.
CTF_TARGETS: tuple[SandboxTarget, ...] = (_VULNWEB_TESTPHP,)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_ctf_targets(registry: TrustedTargetRegistry) -> list[str]:
    """Register all pre-approved CTF/pentest targets into *registry*.

    Idempotent: targets already registered are silently skipped (the registry
    raises ValueError only on conflicting re-registration, which cannot happen
    here since all definitions are module-level constants).

    Args:
        registry: A TrustedTargetRegistry instance to populate.

    Returns:
        List of target IDs that were newly registered.
    """
    registered: list[str] = []
    for target in CTF_TARGETS:
        try:
            registry.register(target)
            registered.append(target.id)
        except ValueError:
            # Already registered with the same object — idempotent, skip.
            pass
    return registered


# ---------------------------------------------------------------------------
# __main__ entrypoint — run as: python -m orchestration.ctf_loader
# ---------------------------------------------------------------------------

def _main() -> None:
    """Print registered CTF targets and exit (for manual verification)."""
    from orchestration.registry import get_default_target_registry

    registry = get_default_target_registry()
    newly = load_ctf_targets(registry)

    print(f"\nCTF Target Loader — {len(CTF_TARGETS)} target(s) defined\n")
    for target in CTF_TARGETS:
        status = "NEW" if target.id in newly else "already registered"
        print(f"  [{status:18s}] id={target.id!r}")
        print(f"                      name={target.name!r}")
        print(f"                      ref={target.runtime_reference!r}")
        print()
    print("Registry contains", len(registry), "trusted target(s) total.")


if __name__ == "__main__":
    _main()
