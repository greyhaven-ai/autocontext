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
    tool_context: str = "",
    strategy_interface: str = "",
) -> RoleHandler:
    """Build a RoleHandler callable that delegates to the orchestrator's role runners."""

    def handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
        if name == "competitor":
            _raw_text, exec_result = orch.competitor.run(prompt, tool_context=tool_context)
            return exec_result
        elif name == "translator":
            competitor_exec = completed.get("competitor")
            raw_text = competitor_exec.content if competitor_exec else ""
            _strategy, exec_result = orch.translator.translate(raw_text, strategy_interface)
            return exec_result
        elif name == "analyst":
            return orch.analyst.run(prompt)
        elif name == "architect":
            return orch.architect.run(prompt)
        elif name == "coach":
            analyst_exec = completed.get("analyst")
            enriched = prompt
            if analyst_exec:
                enriched = orch._enrich_coach_prompt(prompt, analyst_exec.content)
            return orch.coach.run(enriched)
        else:
            raise ValueError(f"Unknown role: {name}")

    return handler
