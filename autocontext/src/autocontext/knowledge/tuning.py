from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from autocontext.config.tuning_bounds import architect_bounds

# Architect-tier bounds: tighter ranges for automated proposals.
# Derived from the canonical definition in config/tuning_bounds.py.
TUNING_BOUNDS: dict[str, tuple[float, float]] = architect_bounds()


@dataclass(slots=True)
class TuningConfig:
    version: int = 1
    parameters: dict[str, float | int] = field(default_factory=dict)
    recommended_by: str = ""
    reasoning: str = ""

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps({
            "version": self.version,
            "parameters": self.parameters,
            "recommended_by": self.recommended_by,
            "reasoning": self.reasoning,
        }, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> TuningConfig:
        """Parse from JSON string."""
        data = json.loads(raw)
        params = validate_tuning_bounds(data.get("parameters", {}))
        return cls(
            version=data.get("version", 1),
            parameters=params,
            recommended_by=data.get("recommended_by", ""),
            reasoning=data.get("reasoning", ""),
        )


def validate_tuning_bounds(raw: dict[str, Any]) -> dict[str, float | int]:
    """Validate parameters against hard bounds, dropping out-of-range values."""
    result: dict[str, float | int] = {}
    for key, value in raw.items():
        if key not in TUNING_BOUNDS:
            continue
        min_val, max_val = TUNING_BOUNDS[key]
        try:
            val = float(value)
        except (TypeError, ValueError):
            continue
        if min_val <= val <= max_val:
            # Use int for integer-typed bounds
            if min_val == int(min_val) and max_val == int(max_val) and val == int(val):
                result[key] = int(val)
            else:
                result[key] = val
    return result


def compute_meta_parameter_stats(
    trajectory_rows: list[dict[str, object]],
    rlm_max_turns: int = 25,
    matches_per_gen: int = 3,
) -> dict[str, float]:
    """Compute meta-parameter effectiveness statistics from trajectory data."""
    if not trajectory_rows:
        return {
            "retry_rate": 0.0,
            "avg_delta": 0.0,
            "rlm_utilization": 0.0,
            "total_generations": 0.0,
        }

    total = len(trajectory_rows)
    retries = sum(1 for r in trajectory_rows if r.get("gate_decision") == "retry")
    deltas = [float(r.get("delta", 0)) for r in trajectory_rows]  # type: ignore[arg-type]

    return {
        "retry_rate": retries / total if total > 0 else 0.0,
        "avg_delta": sum(deltas) / total if total > 0 else 0.0,
        "rlm_utilization": 0.0,  # Placeholder -- actual RLM turn data not in trajectory
        "total_generations": float(total),
    }


def parse_tuning_proposal(output: str) -> TuningConfig | None:
    """Extract tuning proposal from architect output using TUNING_PROPOSAL markers."""
    match = re.search(
        r"<!-- TUNING_PROPOSAL_START -->\s*\n(.+?)\n\s*<!-- TUNING_PROPOSAL_END -->",
        output,
        re.DOTALL,
    )
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    # Extract parameters and reasoning
    reasoning = data.pop("reasoning", "")
    params = validate_tuning_bounds(data)
    if not params:
        return None

    return TuningConfig(
        parameters=params,
        reasoning=str(reasoning),
    )


def format_meta_stats(stats: dict[str, float]) -> str:
    """Format meta-parameter stats as markdown for architect prompt injection."""
    return (
        "## Meta-Parameter Analysis\n"
        f"- Retry rate: {stats.get('retry_rate', 0):.0%} (last {int(stats.get('total_generations', 0))} gens)\n"
        f"- Average gate delta: {stats.get('avg_delta', 0):.4f}\n"
        f"- RLM utilization: {stats.get('rlm_utilization', 0):.0%}\n"
    )
