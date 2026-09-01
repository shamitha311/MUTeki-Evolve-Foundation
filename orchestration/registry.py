"""Default trusted target registry pre-populated with pre-approved targets."""

from app.models import SandboxTarget, TrustedTargetRegistry

_DEMO_TARGET = SandboxTarget(
    id="trusted-demo-target",
    name="Trusted demo sandbox",
    description="A deterministic local fixture target for contract tests.",
    runtime_reference="mock://trusted-demo-target",
)


def get_default_target_registry() -> TrustedTargetRegistry:
    """Return a TrustedTargetRegistry containing pre-approved trusted sandbox targets.

    Includes the fixture demo target (for contract tests) and all pre-approved
    CTF / pentest sandbox targets defined in orchestration.ctf_loader.

    Arbitrary client runtime_references or untrusted target IDs are rejected.
    """
    from orchestration.ctf_loader import load_ctf_targets  # local import avoids circular

    registry = TrustedTargetRegistry()
    registry.register(_DEMO_TARGET)
    load_ctf_targets(registry)
    return registry
