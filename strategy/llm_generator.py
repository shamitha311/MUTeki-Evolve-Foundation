"""LLM-backed Strategy Generation with fallback to deterministic rules.

Supports OpenAI, DeepSeek, Anthropic, Gemini, or local Ollama endpoints.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from app.models import InvestigationResult, ScoreReport, Strategy
from app.validation import validate_strategy
from strategy.generator import StrategyEngine
from strategy.memory import StrategyMemory


class LLMStrategyEngine(StrategyEngine):
    """Strategy Evolution Engine supporting LLM reasoning with fail-closed safety."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        seed: int | None = None,
    ) -> None:
        super().__init__(seed=seed)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
        self.model = os.environ.get("LLM_MODEL", model)

    def _query_llm(self, prompt: str) -> dict[str, Any] | None:
        if not self.api_key:
            return None

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an autonomous cybersecurity strategy advisor. "
                        "Given prior investigation evidence and progress, output a JSON object with: "
                        "{'priorities': ['<priority1>', '<priority2>'], 'reasoning': '<brief>'}. "
                        "Do not include commands, target URLs, or code."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }

        try:
            req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode("utf-8"))
            with urllib.request.urlopen(req, timeout=15) as res:
                body = json.loads(res.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                return json.loads(content)
        except Exception:
            return None

    def generate_next_strategy(
        self,
        previous_strategy: Strategy,
        investigation_result: InvestigationResult,
        score_report: ScoreReport,
        history: StrategyMemory | None = None,
    ) -> Strategy:
        """Generate next strategy, augmenting with LLM reasoning if configured."""
        # Try LLM generation if credentials exist
        if self.api_key:
            prompt = (
                f"Objective: {previous_strategy.objective}\n"
                f"Previous Priorities: {list(previous_strategy.priorities)}\n"
                f"Evidence: {investigation_result.evidence_summary}\n"
                f"Signals: {investigation_result.progress_signals}\n"
                f"Progress Score: {score_report.progress_score}\n"
                f"Propose next strategic priorities (e.g. reconnaissance, surface discovery, authentication, verification)."
            )
            llm_result = self._query_llm(prompt)
            if llm_result and "priorities" in llm_result:
                priorities = tuple(str(p).strip() for p in llm_result["priorities"][:3])
                strategy = Strategy(
                    objective=previous_strategy.objective,
                    priorities=priorities or ("surface discovery", "evidence correlation"),
                    constraints=previous_strategy.constraints,
                    context={"llm_reasoning": llm_result.get("reasoning", "LLM-guided investigation step")},
                    revision=previous_strategy.revision + 1,
                    parent_revision=previous_strategy.revision,
                )
                try:
                    return validate_strategy(strategy)
                except Exception:
                    pass

        # Fallback to standard deterministic rule engine
        return super().generate_next_strategy(
            previous_strategy=previous_strategy,
            investigation_result=investigation_result,
            score_report=score_report,
            history=history,
        )
