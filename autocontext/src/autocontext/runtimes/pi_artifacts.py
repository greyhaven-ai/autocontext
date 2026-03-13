"""Pi session artifact contract — maps Pi outputs into AutoContext artifacts.

Defines PiExecutionTrace for structured persistence and replay of Pi
CLI/RPC sessions within the generation directory layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PiExecutionTrace:
    """Structured record of a single Pi execution."""

    session_id: str
    branch_id: str = ""
    prompt_context: str = ""
    raw_output: str = ""
    normalized_output: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    cost_usd: float = 0.0
    model: str = "pi"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "branch_id": self.branch_id,
            "prompt_context": self.prompt_context,
            "raw_output": self.raw_output,
            "normalized_output": self.normalized_output,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "cost_usd": self.cost_usd,
            "model": self.model,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PiExecutionTrace:
        return cls(
            session_id=data.get("session_id", ""),
            branch_id=data.get("branch_id", ""),
            prompt_context=data.get("prompt_context", ""),
            raw_output=data.get("raw_output", ""),
            normalized_output=data.get("normalized_output", ""),
            exit_code=data.get("exit_code", 0),
            duration_ms=data.get("duration_ms", 0),
            cost_usd=data.get("cost_usd", 0.0),
            model=data.get("model", "pi"),
            metadata=data.get("metadata", {}),
        )
