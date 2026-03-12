"""Adapter building a harness PipelineEngine from AutoContext orchestrator components."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from autocontext.harness.core.types import RoleExecution
from autocontext.harness.orchestration.dag import RoleDAG
from autocontext.harness.orchestration.types import RoleSpec

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator

RoleHandler = Callable[[str, str, dict[str, RoleExecution]], RoleExecution]


def build_mts_dag() -> RoleDAG:
    """Build the standard AutoContext 5-role DAG.

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
            with orch._use_role_runtime(
                "competitor",
                orch.competitor,
                generation=generation,
                scenario_name=scenario_name,
            ):
                _raw_text, exec_result = orch.competitor.run(prompt, tool_context=tool_context)
                return exec_result
        elif name == "translator":
            competitor_exec = completed.get("competitor")
            raw_text = competitor_exec.content if competitor_exec else ""
            with orch._use_role_runtime(
                "translator",
                orch.translator,
                generation=generation,
                scenario_name=scenario_name,
            ):
                _strategy, exec_result = orch.translator.translate(raw_text, strategy_interface)
                return exec_result
        elif name == "analyst":
            with orch._use_role_runtime(
                "analyst",
                orch.analyst,
                generation=generation,
                scenario_name=scenario_name,
            ):
                return orch.analyst.run(prompt)
        elif name == "architect":
            with orch._use_role_runtime(
                "architect",
                orch.architect,
                generation=generation,
                scenario_name=scenario_name,
            ):
                return orch.architect.run(prompt)
        elif name == "coach":
            analyst_exec = completed.get("analyst")
            enriched = prompt
            if analyst_exec:
                enriched = orch._enrich_coach_prompt(prompt, analyst_exec.content)
            with orch._use_role_runtime(
                "coach",
                orch.coach,
                generation=generation,
                scenario_name=scenario_name,
            ):
                return orch.coach.run(enriched)
        else:
            raise ValueError(f"Unknown role: {name}")

    return handler
