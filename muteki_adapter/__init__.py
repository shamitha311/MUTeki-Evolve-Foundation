"""Semantic boundary between MUTeki-Evolve and upstream Muteki.

Public surface:
  MutekiAdapter     — Protocol (structural type check only)
  RealMutekiAdapter — Real integration adapter (Chunk 4)
  Error types       — Structured failure types for callers
  AdapterConfig     — Configuration dataclass
  load_config       — Load configuration from environment
"""

from .protocol import MutekiAdapter
from .adapter import RealMutekiAdapter
from .config import AdapterConfig, load_config
from .errors import (
    MutekiAdapterError,
    MutekiEventStreamError,
    MutekiMalformedResultError,
    MutekiRunCreationError,
    MutekiRunFailedError,
    MutekiTimeoutError,
    MutekiUnavailableError,
    StrategyValidationError,
)

__all__ = [
    "MutekiAdapter",
    "RealMutekiAdapter",
    "AdapterConfig",
    "load_config",
    "MutekiAdapterError",
    "MutekiEventStreamError",
    "MutekiMalformedResultError",
    "MutekiRunCreationError",
    "MutekiRunFailedError",
    "MutekiTimeoutError",
    "MutekiUnavailableError",
    "StrategyValidationError",
]
