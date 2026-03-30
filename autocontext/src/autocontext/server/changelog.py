from __future__ import annotations

import json
import logging
from typing import Any, cast

from autocontext.agents.coach import parse_coach_sections
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


def _extract_tool_names(architect_content: str) -> list[str]:
    """Extract tool names from architect output (expects JSON list of {name, code} dicts)."""
    try:
        specs = json.loads(architect_content)
        if isinstance(specs, list):
            return [s["name"] for s in specs if isinstance(s, dict) and "name" in s]
    except (json.JSONDecodeError, TypeError, KeyError):
        logger.debug("server.changelog: suppressed json.JSONDecodeError), TypeError, KeyError", exc_info=True)
    return []


def build_changelog(
    run_id: str,
    sqlite: SQLiteStore,
    artifacts: ArtifactStore,
) -> dict[str, Any]:
    """Build "what changed" between consecutive generations."""
    generations = sqlite.get_generation_metrics(run_id)
    if not generations:
        return {"run_id": run_id, "generations": []}

    # Sort by generation_index
    generations.sort(key=lambda g: g["generation_index"])

    # Get architect outputs for tool detection
    architect_outputs = sqlite.get_agent_outputs_by_role(run_id, "architect")
    architect_by_gen: dict[int, list[str]] = {}
    for ao in architect_outputs:
        gen_idx = cast(int, ao["generation_index"])
        if gen_idx not in architect_by_gen:
            architect_by_gen[gen_idx] = []
        architect_by_gen[gen_idx].append(str(ao["content"]))

    coach_outputs = sqlite.get_agent_outputs_by_role(run_id, "coach")
    playbook_changed_gens: set[int] = set()
    for coach_output in coach_outputs:
        gen_idx = cast(int, coach_output["generation_index"])
        playbook, _, _ = parse_coach_sections(str(coach_output["content"]))
        if playbook.strip():
            playbook_changed_gens.add(gen_idx)

    result_gens: list[dict[str, Any]] = []
    for i, gen in enumerate(generations):
        if i == 0:
            prev_best_score = 0.0
            prev_elo = 1000.0
        else:
            prev_best_score = generations[i - 1]["best_score"]
            prev_elo = generations[i - 1]["elo"]

        gen_idx = gen["generation_index"]

        # Detect new tools from architect outputs
        new_tools: list[str] = []
        for content in architect_by_gen.get(gen_idx, []):
            new_tools.extend(_extract_tool_names(content))

        entry: dict[str, Any] = {
            "generation": gen_idx,
            "score_delta": round(gen["best_score"] - prev_best_score, 6),
            "elo_delta": round(gen["elo"] - prev_elo, 6),
            "gate_decision": gen["gate_decision"],
            "new_tools": new_tools,
            "playbook_changed": gen_idx in playbook_changed_gens,
            "duration_seconds": gen.get("duration_seconds"),
        }
        result_gens.append(entry)

    return {"run_id": run_id, "generations": result_gens}
