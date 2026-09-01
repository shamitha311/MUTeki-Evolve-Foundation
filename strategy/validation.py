"""Compatibility import for the strategy validation boundary."""

from app.validation import StrategyValidationError, approve_strategy, validate_strategy

__all__ = ["StrategyValidationError", "approve_strategy", "validate_strategy"]
