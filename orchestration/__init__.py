"""Closed-loop orchestration package and trusted-target ownership."""

from .orchestrator import Orchestrator, OrchestrationError, TargetNotTrustedError
from .registry import get_default_target_registry
from .types import (
    InvestigationRunState,
    IterationRecord,
    RunStatus,
    TerminationReason,
)

__all__ = [
    "InvestigationRunState",
    "IterationRecord",
    "OrchestrationError",
    "Orchestrator",
    "RunStatus",
    "TargetNotTrustedError",
    "TerminationReason",
    "get_default_target_registry",
]
