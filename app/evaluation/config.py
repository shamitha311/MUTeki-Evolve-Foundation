"""Configurable weights and thresholds for the Evaluation Engine (Chunk 5).

All defaults are documented here. Pass a custom EvaluatorConfig to evaluate()
to override any value without changing application code.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluatorConfig:
    """Scoring weights and detection thresholds.

    Attributes:
        evidence_per_item_scale:
            Scale factor multiplied by (confidence × type_weight) for each
            unique evidence item. Controls how much individual evidence items
            contribute relative to signal-based scores.

        max_evidence_contribution:
            Hard cap on total evidence contribution regardless of item count.
            Prevents many low-quality items from dominating the score.

        diversity_per_type:
            Bonus per unique evidence type. Broader investigations (multiple
            evidence types) score slightly higher than narrow ones.

        max_diversity_bonus:
            Hard cap on the diversity bonus.

        no_progress_window:
            Number of prior ScoreReports inspected for stagnation detection.
            Must be at least 2.

        meaningful_progress_delta:
            Minimum score improvement (across the window) considered real
            progress. Score changes below this threshold may indicate stagnation.
    """

    # Evidence contribution
    evidence_per_item_scale: float = 25.0
    max_evidence_contribution: float = 30.0

    # Diversity bonus
    diversity_per_type: float = 3.0
    max_diversity_bonus: float = 5.0

    # Stagnation detection
    no_progress_window: int = 2
    meaningful_progress_delta: float = 5.0


DEFAULT_CONFIG = EvaluatorConfig()
