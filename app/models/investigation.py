"""Normalized investigation events, evidence, and results."""

from datetime import datetime

from pydantic import Field, field_validator

from ._base import ContractModel


def _validate_iso_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    candidate = value.strip()
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    return candidate


class InvestigationEvent(ContractModel):
    sequence: int = Field(ge=1)
    timestamp: str
    type: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    worker_id: str | None = None
    summary: str = Field(min_length=1)

    _timestamp = field_validator("timestamp")(_validate_iso_timestamp)


class Evidence(ContractModel):
    type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    source_event: int | None = Field(default=None, ge=1)


class InvestigationResult(ContractModel):
    run_id: str = Field(min_length=1)
    solved: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    evidence_summary: str = ""
    progress_signals: list[str] = Field(default_factory=list)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    event_summary: list[str] = Field(default_factory=list)
    error: str | None = None

    @field_validator("error")
    @classmethod
    def validate_error(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("error must be non-empty when provided")
        return value
