"""Adapter protocol.

The real implementation is intentionally deferred to Chunk 4. This protocol
describes semantics only and exposes no upstream Muteki objects.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from app.models import InvestigationEvent, InvestigationResult, SandboxTarget, Strategy


class MutekiAdapter(Protocol):
    async def run_strategy(
        self, target: SandboxTarget, strategy: Strategy
    ) -> InvestigationResult:
        """Execute one approved high-level strategy through Muteki."""

    def subscribe_events(self, run_id: str) -> AsyncIterator[InvestigationEvent]:
        """Yield normalized events for a run in sequence order."""
