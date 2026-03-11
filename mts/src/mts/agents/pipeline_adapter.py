"""Adapter building a harness PipelineEngine from MTS orchestrator components."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mts.harness.core.types import RoleExecution
from mts.harness.orchestration.dag import RoleDAG
from mts.harness.orchestration.types import RoleSpec

if TYPE_CHECKING:
    from mts.agents.orchestrator import AgentOrchestrator

RoleHandler = Callable[[str, str, dict[str, RoleExecution]], RoleExecution]


def build_mts_dag() -> RoleDAG:
    """Build the standard MTS 5-role DAG.

    competitor -> translator -> analyst -> coach
                             -> architect (parallel with analyst; coach depends on analyst)
    """
    return RoleDAG([
        RoleSpec(name="competitor"),
        RoleSpec(name="translator", depends_on=("competitor",)),
        RoleSpec(name="analyst", depends_on=("translator",)),
        RoleSpec(name="architect", depends_on=("translator",)),
        RoleSpec(name="coach", depends_on=("analyst",)),
    ])


def build_role_handler(
    orch: AgentOrchestrator,
    generation: int = 1,
    scenario_name: str = "",
    tool_context: str = "",
    strategy_interface: str = "",
) -> RoleHandler:
    """Build a RoleHandler callable that delegates to the orchestrator's role runners."""

    def handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
        if name == "competitor":
            model = orch.resolve_model("competitor", generation=generation, scenario_name=scenario_name)
            original_model = orch.competitor.model
            if model is not None:
                orch.competitor.model = model
            try:
                _raw_text, exec_result = orch.competitor.run(prompt, tool_context=tool_context)
                return exec_result
            finally:
                orch.competitor.model = original_model
        elif name == "translator":
            competitor_exec = completed.get("competitor")
            raw_text = competitor_exec.content if competitor_exec else ""
            _strategy, exec_result = orch.translator.translate(raw_text, strategy_interface)
            return exec_result
        elif name == "analyst":
            model = orch.resolve_model("analyst", generation=generation)
            original_model = orch.analyst.model
            if model is not None:
                orch.analyst.model = model
            try:
                return orch.analyst.run(prompt)
            finally:
                orch.analyst.model = original_model
        elif name == "architect":
            model = orch.resolve_model("architect", generation=generation)
            original_model = orch.architect.model
            if model is not None:
                orch.architect.model = model
            try:
                return orch.architect.run(prompt)
            finally:
                orch.architect.model = original_model
        elif name == "coach":
            analyst_exec = completed.get("analyst")
            enriched = prompt
            if analyst_exec:
                enriched = orch._enrich_coach_prompt(prompt, analyst_exec.content)
            model = orch.resolve_model("coach", generation=generation)
            original_model = orch.coach.model
            if model is not None:
                orch.coach.model = model
            try:
                return orch.coach.run(enriched)
            finally:
                orch.coach.model = original_model
        else:
            raise ValueError(f"Unknown role: {name}")

    return handler
