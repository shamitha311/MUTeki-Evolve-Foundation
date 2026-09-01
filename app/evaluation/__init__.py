"""Evaluation / Scoring Engine — Chunk 5.

Public API::

    from app.evaluation import evaluate, EvaluatorConfig

    report: ScoreReport = evaluate(investigation_result)
    report: ScoreReport = evaluate(investigation_result, history=[prev_report])
    report: ScoreReport = evaluate(investigation_result, config=EvaluatorConfig(...))

``evaluate()`` is the canonical entry point. All other sub-modules are
internal implementation details.
"""
from .config import DEFAULT_CONFIG, EvaluatorConfig
from .evaluator import evaluate

__all__ = ["DEFAULT_CONFIG", "EvaluatorConfig", "evaluate"]
