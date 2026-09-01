"""Pydantic configuration shared by application-owned models."""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Immutable, strict models prevent silent contract drift."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )
