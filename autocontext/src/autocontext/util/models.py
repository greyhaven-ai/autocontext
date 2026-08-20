"""Shared model bases for durable public artifacts."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Reject unknown/coerced fields and expose the repository JSON codec."""

    model_config = ConfigDict(extra="forbid", strict=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls.model_validate(data)


__all__ = ["StrictModel"]
