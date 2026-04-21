from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autocontext.agents.types import RoleExecution, RoleUsage


def analyst_has_fresh_match_data(variables: Mapping[str, Any]) -> bool:
    return bool(
        variables.get("replays")
        or variables.get("metrics_history")
        or variables.get("match_scores")
    )


def build_analyst_no_data_execution(model: str) -> RoleExecution:
    return RoleExecution(
        role="analyst",
        content=(
            "## Findings\n"
            "- No fresh match data is available for this run yet; replays, metrics history, "
            "and match scores are empty.\n\n"
            "## Root Causes\n"
            "- The analyst RLM pass was skipped because there is no new scored evidence "
            "to analyze for this generation.\n"
            "- Only carry-forward guidance from the playbook, prior analyses, and "
            "operational lessons is available.\n\n"
            "## Actionable Recommendations\n"
            "- Freeze parameter deltas at `0.00` and reuse the currently validated "
            "baseline until fresh replay-backed data arrives.\n"
            "- Collect new scored runs before making additional strategic claims or "
            "branching changes."
        ),
        usage=RoleUsage(
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            model=model,
        ),
        subagent_id="analyst-no-data",
        status="completed",
    )


def build_architect_cadence_skip_execution(model: str) -> RoleExecution:
    return RoleExecution(
        role="architect",
        content=(
            "## Observed Bottlenecks\n"
            "- Architect cadence skip: no major intervention this generation.\n\n"
            "## Tool Proposals\n"
            "- None; return minimal status and an empty tools array.\n\n"
            "## Impact Hypothesis\n"
            "- Reduces live runtime overhead on off-cadence generations while preserving existing tool behavior.\n\n"
            "```json\n"
            '{"tools": []}\n'
            "```"
        ),
        usage=RoleUsage(
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            model=model,
        ),
        subagent_id="architect-skip",
        status="completed",
    )
