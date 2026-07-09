"""Adapter building a harness PipelineEngine from autocontext orchestrator components."""

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
    """Build the standard autocontext 5-role DAG.

    competitor -> translator -> analyst -> coach
                             -> architect (parallel with analyst; coach depends on analyst)
    """
    return RoleDAG(
        [
            RoleSpec(name="competitor"),
            RoleSpec(name="translator", depends_on=("competitor",)),
            RoleSpec(name="analyst", depends_on=("translator",)),
            RoleSpec(name="architect", depends_on=("translator",)),
            RoleSpec(name="coach", depends_on=("analyst",)),
        ]
    )


def build_role_handler(
    orch: AgentOrchestrator,
    generation: int = 1,
    scenario_name: str = "",
    tool_context: str = "",
    strategy_interface: str = "",
    generation_deadline: float | None = None,
    system_map: dict[str, str] | None = None,
    flat_map: dict[str, str] | None = None,
) -> RoleHandler:
    """Build a RoleHandler callable that delegates to the orchestrator's role runners.

    ERP-67 Stage 2b — structural role isolation. ``system_map`` maps a role to a
    trusted system prompt and ``flat_map`` to that role's exact legacy flat
    prompt. When a role is in ``system_map`` AND its resolved client supports
    real message roles, ``prompt`` (the untrusted user turn) is sent with the
    system turn. Otherwise the role falls back to its flat prompt with no system
    turn — byte-identical to legacy — so single-prompt / runtime-bridge backends
    are never silently reordered. Absent maps → legacy path, no ``system`` kwarg.
    """
    systems = system_map or {}
    flats = flat_map or {}

    def _resolve_turn(name: str, runner: object, prompt: str) -> tuple[str, str]:
        """Return (user_prompt, system) for a role, honouring client capability."""
        system = systems.get(name, "")
        if not system:
            return prompt, ""
        client = getattr(getattr(runner, "runtime", None), "client", None)
        if getattr(client, "supports_structural_isolation", False):
            return prompt, system  # capable: untrusted user turn + system turn
        return flats.get(name, prompt), ""  # incapable: exact legacy flat prompt

    def handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
        if name == "competitor":
            with orch._use_role_runtime(
                "competitor",
                orch.competitor,
                generation=generation,
                scenario_name=scenario_name,
                generation_deadline=generation_deadline,
            ):
                user_prompt, system = _resolve_turn("competitor", orch.competitor, prompt)
                # Pass `system` only when isolating, so legacy runner signatures
                # (and test fakes) that predate the kwarg keep working.
                if system:
                    _raw_text, exec_result = orch.competitor.run(user_prompt, tool_context=tool_context, system=system)
                else:
                    _raw_text, exec_result = orch.competitor.run(user_prompt, tool_context=tool_context)
                return exec_result
        elif name == "translator":
            competitor_exec = completed.get("competitor")
            raw_text = competitor_exec.content if competitor_exec else ""
            with orch._use_role_runtime(
                "translator",
                orch.translator,
                generation=generation,
                scenario_name=scenario_name,
                generation_deadline=generation_deadline,
            ):
                _strategy, exec_result = orch.translator.translate(raw_text, strategy_interface)
                return exec_result
        elif name == "analyst":
            with orch._use_role_runtime(
                "analyst",
                orch.analyst,
                generation=generation,
                scenario_name=scenario_name,
                generation_deadline=generation_deadline,
            ):
                user_prompt, system = _resolve_turn("analyst", orch.analyst, prompt)
                if system:
                    return orch.analyst.run(user_prompt, system=system)
                return orch.analyst.run(user_prompt)
        elif name == "architect":
            with orch._use_role_runtime(
                "architect",
                orch.architect,
                generation=generation,
                scenario_name=scenario_name,
                generation_deadline=generation_deadline,
            ):
                user_prompt, system = _resolve_turn("architect", orch.architect, prompt)
                if system:
                    return orch.architect.run(user_prompt, system=system)
                return orch.architect.run(user_prompt)
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
                generation_deadline=generation_deadline,
            ):
                return orch.coach.run(enriched)
        else:
            raise ValueError(f"Unknown role: {name}")

    return handler
