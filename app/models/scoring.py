"""Evaluation output contract; scoring itself belongs to a later chunk."""

from pydantic import Field

from ._base import ContractModel


class ScoreReport(ContractModel):
    progress_score: float = Field(ge=0.0, le=100.0)
    solved: bool = False
    progress_level: str = Field(min_length=1)
    reasons: list[str] = Field(default_factory=list)
    stagnated: bool = False
