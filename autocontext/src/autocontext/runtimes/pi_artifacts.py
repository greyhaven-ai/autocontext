"""Pi session artifact contract — maps Pi outputs into autocontext artifacts.

Defines PiExecutionTrace for structured persistence and replay of Pi
CLI/RPC sessions within the generation directory layout.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PiExecutionTrace(BaseModel):
    """Structured record of a single Pi execution."""

    session_id: str = ""
    branch_id: str = ""
    prompt_context: str = ""
    raw_output: str = ""
    normalized_output: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    cost_usd: float = 0.0
    model: str = "pi"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiExecutionTrace:
        return cls.model_validate(data)
