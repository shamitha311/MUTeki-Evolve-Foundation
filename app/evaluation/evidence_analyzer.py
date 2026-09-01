"""Evidence quality analysis and deduplication for the Evaluation Engine.

This module is read-only: it analyzes InvestigationResult evidence and returns
structured metrics used by the scorer. It does not execute anything, modify any
model, call Muteki, or generate strategies.

Deduplication identity is based on normalized (type, summary) — no embeddings
or semantic similarity are used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.models import Evidence


# ---------------------------------------------------------------------------
# Evidence type quality multipliers
# Higher weight = more score contribution per unit confidence.
# Unknown types default to a conservative 0.5.
# ---------------------------------------------------------------------------
_TYPE_WEIGHTS: dict[str, float] = {
    "verified_success": 1.0,
    "verified success": 1.0,
    "verified condition": 0.9,
    "resolution signal": 0.9,
    "correlation": 0.8,
    "correlated trace": 0.8,
    "hypothesis_test": 0.8,
    "hypothesis test": 0.8,
    "authentication": 0.75,
    "authorization": 0.75,
    "input validation": 0.7,
    "reconnaissance": 0.6,
    "surface_map": 0.55,
    "surface map": 0.55,
    "observation": 0.5,
    "fact": 0.5,
}

_DEFAULT_TYPE_WEIGHT = 0.5


def _normalize_key(value: str) -> str:
    """Normalize whitespace and case for stable comparison."""
    return re.sub(r"\s+", " ", value.strip().lower())


def _type_weight(evidence_type: str) -> float:
    """Return the quality multiplier for an evidence type.

    Falls back to partial substring matching, then to the default.
    This is deterministic — no randomness or ordering sensitivity.
    """
    normalized = _normalize_key(evidence_type)
    # Exact match first
    if normalized in _TYPE_WEIGHTS:
        return _TYPE_WEIGHTS[normalized]
    # Partial match: check if any known key is contained in the type string
    for known, weight in sorted(_TYPE_WEIGHTS.items(), key=lambda kv: -kv[1]):
        if known in normalized:
            return weight
    return _DEFAULT_TYPE_WEIGHT


def deduplicate_evidence(evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """Return only the first occurrence of each (normalized type, normalized summary) pair.

    Duplicate evidence does not earn additional credit. The order of the first
    occurrences is preserved. No semantic similarity is used — deduplication is
    purely string-based after normalization.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[Evidence] = []
    for item in evidence:
        key = (_normalize_key(item.type), _normalize_key(item.summary))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


@dataclass(frozen=True)
class EvidenceAnalysis:
    """Structured metrics derived from the evidence list.

    Attributes:
        unique_evidence:      Deduplicated evidence items (first occurrences).
        unique_types:         Normalized set of distinct evidence type strings.
        weighted_contribution:Sum of (confidence × type_weight) for unique items.
        has_verified_success: True if any unique item has a verified/success type.
        has_high_confidence:  True if any unique item has confidence ≥ 0.75.
        duplicate_count:      Number of items removed during deduplication.
    """

    unique_evidence: tuple[Evidence, ...]
    unique_types: frozenset[str]
    weighted_contribution: float
    has_verified_success: bool
    has_high_confidence: bool
    duplicate_count: int


def analyze_evidence(evidence: Sequence[Evidence]) -> EvidenceAnalysis:
    """Deduplicate and analyze evidence; return scoring-relevant metrics.

    Deterministic: identical evidence produces identical EvidenceAnalysis.
    """
    unique = deduplicate_evidence(evidence)
    duplicate_count = len(evidence) - len(unique)

    weighted_contribution = sum(
        item.confidence * _type_weight(item.type) for item in unique
    )
    unique_types = frozenset(_normalize_key(item.type) for item in unique)

    has_verified_success = any(
        "verified" in _normalize_key(item.type)
        or "success" in _normalize_key(item.type)
        for item in unique
    )
    has_high_confidence = any(item.confidence >= 0.75 for item in unique)

    return EvidenceAnalysis(
        unique_evidence=unique,
        unique_types=unique_types,
        weighted_contribution=weighted_contribution,
        has_verified_success=has_verified_success,
        has_high_confidence=has_high_confidence,
        duplicate_count=duplicate_count,
    )
