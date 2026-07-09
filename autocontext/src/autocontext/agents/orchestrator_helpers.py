"""Orchestrator generation-phase helpers (AC-864).

Free functions extracted from AgentOrchestrator.run_generation, mirroring the
loop/stage_helpers/ extraction pattern: each takes the orchestrator instance
as its first argument plus the same explicit parameters run_generation used
to thread through the call. Kept in a separate module because
agents/orchestrator.py sits at its grandfathered module-size cap.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from autocontext.agents.architect import parse_architect_harness_specs, parse_architect_tool_specs
from autocontext.agents.coach import parse_coach_sections
from autocontext.agents.parsers import parse_analyst_output, parse_architect_output, parse_coach_output, parse_competitor_output
from autocontext.agents.role_isolation import resolve_role_turn
from autocontext.agents.types import AgentOutputs, RoleExecution

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.prompts.templates import PromptBundle, PromptPartsBundle, RolePromptParts

NotifyFn = Callable[[str, str], None]


def _direct_turn(
    orchestrator: AgentOrchestrator,
    rp: RolePromptParts | None,
    runner: object,
    flat_prompt: str,
    *,
    suffix: str = "",
) -> tuple[str, str]:
    """Resolve (user_prompt, system) for a direct-path role.

    Returns the legacy ``flat_prompt`` with no system turn unless structural
    isolation is enabled and parts are available, in which case it defers to
    ``resolve_role_turn`` (which itself falls back to flat for unsafe splits or
    incapable clients). Must be called inside the role's ``_use_role_runtime``
    scope so the resolved client's capability is what's checked.
    """
    if not orchestrator.settings.structural_role_isolation or rp is None:
        return flat_prompt, ""
    client = getattr(getattr(runner, "runtime", None), "client", None)
    return resolve_role_turn(rp, client, suffix=suffix)


def _run_competitor_phase(
    orchestrator: AgentOrchestrator,
    prompts: PromptBundle,
    generation_index: int,
    tool_context: str,
    run_id: str,
    scenario_name: str,
    strategy_interface: str,
    scenario_rules: str,
    current_strategy: dict[str, Any] | None,
    generation_deadline: float | None,
    notify: NotifyFn,
    parts: PromptPartsBundle | None = None,
) -> tuple[str, RoleExecution]:
    """Run the Competitor role, choosing RLM vs direct execution."""
    settings = orchestrator.settings
    competitor_model = (
        orchestrator.resolve_model(
            "competitor",
            generation=generation_index,
            scenario_name=scenario_name,
        )
        or orchestrator.competitor.model
    )
    use_competitor_rlm = (
        settings.rlm_enabled
        and settings.rlm_competitor_enabled
        and orchestrator._rlm_loader is not None
        and settings.agent_provider != "agent_sdk"
    )

    if use_competitor_rlm:
        notify("competitor", "started")
        raw_text, competitor_exec = orchestrator._run_rlm_competitor(
            run_id,
            scenario_name,
            generation_index,
            model=competitor_model,
            strategy_interface=strategy_interface,
            scenario_rules=scenario_rules,
            current_strategy=current_strategy,
        )
        notify("competitor", "completed")
    else:
        notify("competitor", "started")
        competitor_prompt = prompts.competitor
        code_suffix = ""
        if settings.code_strategies_enabled:
            from autocontext.prompts.templates import code_strategy_competitor_suffix

            code_suffix = code_strategy_competitor_suffix(strategy_interface)
            competitor_prompt += code_suffix
        with orchestrator._use_role_runtime(
            "competitor",
            orchestrator.competitor,
            generation=generation_index,
            scenario_name=scenario_name,
            generation_deadline=generation_deadline,
        ):
            user_prompt, system = _direct_turn(
                orchestrator,
                parts.competitor if parts else None,
                orchestrator.competitor,
                competitor_prompt,
                suffix=code_suffix,
            )
            if system:
                raw_text, competitor_exec = orchestrator.competitor.run(user_prompt, tool_context=tool_context, system=system)
            else:
                raw_text, competitor_exec = orchestrator.competitor.run(user_prompt, tool_context=tool_context)
        notify("competitor", "completed")

    return raw_text, competitor_exec


def _run_translator_phase(
    orchestrator: AgentOrchestrator,
    raw_text: str,
    strategy_interface: str,
    generation_index: int,
    scenario_name: str,
    generation_deadline: float | None,
    notify: NotifyFn,
) -> tuple[dict[str, Any], RoleExecution]:
    """Run the Translator role against the Competitor's raw output."""
    settings = orchestrator.settings
    notify("translator", "started")
    with orchestrator._use_role_runtime(
        "translator",
        orchestrator.translator,
        generation=generation_index,
        scenario_name=scenario_name,
        generation_deadline=generation_deadline,
    ):
        if settings.code_strategies_enabled:
            strategy, translator_exec = orchestrator.translator.translate_code(raw_text)
        else:
            strategy, translator_exec = orchestrator.translator.translate(raw_text, strategy_interface)
    notify("translator", "completed")
    return strategy, translator_exec


def _run_analyst_coach_architect(
    orchestrator: AgentOrchestrator,
    prompts: PromptBundle,
    run_id: str,
    scenario_name: str,
    generation_index: int,
    strategy: dict[str, Any],
    architect_prompt: str,
    scenario_rules: str,
    generation_deadline: float | None,
    notify: NotifyFn,
    parts: PromptPartsBundle | None = None,
    architect_cadence: str = "",
) -> tuple[RoleExecution, RoleExecution, RoleExecution]:
    """Run Analyst, Coach, and Architect, choosing RLM vs threaded execution.

    Returns (analyst_exec, coach_exec, architect_exec).
    """
    settings = orchestrator.settings

    def _coach_turn(base_prompt: str, analyst_content: str) -> tuple[str, str]:
        # Resolve the coach turn, then enrich the (untrusted or flat) user base
        # with the analyst's findings — enrichment always rides the user turn.
        base, system = _direct_turn(orchestrator, parts.coach if parts else None, orchestrator.coach, base_prompt)
        return orchestrator._enrich_coach_prompt(base, analyst_content), system

    if settings.rlm_enabled and orchestrator._rlm_loader is not None and settings.agent_provider != "agent_sdk":
        notify("analyst", "started")
        notify("architect", "started")
        analyst_exec, architect_exec = orchestrator._run_rlm_roles(
            run_id,
            scenario_name,
            generation_index,
            strategy,
            architect_prompt,
            scenario_rules=scenario_rules,
        )
        notify("analyst", "completed")
        notify("architect", "completed")
        notify("coach", "started")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with orchestrator._use_role_runtime(
                "coach",
                orchestrator.coach,
                generation=generation_index,
                scenario_name=scenario_name,
                generation_deadline=generation_deadline,
            ):
                enriched_coach_prompt, coach_system = _coach_turn(prompts.coach, analyst_exec.content)
                coach_kwargs = {"system": coach_system} if coach_system else {}
                coach_future = pool.submit(orchestrator.coach.run, enriched_coach_prompt, **coach_kwargs)
                coach_exec = coach_future.result()
        notify("coach", "completed")
    else:
        # Analyst runs first; its output enriches the coach prompt
        notify("analyst", "started")
        with orchestrator._use_role_runtime(
            "analyst",
            orchestrator.analyst,
            generation=generation_index,
            scenario_name=scenario_name,
            generation_deadline=generation_deadline,
        ):
            analyst_user, analyst_system = _direct_turn(
                orchestrator, parts.analyst if parts else None, orchestrator.analyst, prompts.analyst
            )
            if analyst_system:
                analyst_exec = orchestrator.analyst.run(analyst_user, system=analyst_system)
            else:
                analyst_exec = orchestrator.analyst.run(analyst_user)
        notify("analyst", "completed")
        notify("coach", "started")
        notify("architect", "started")
        with (
            orchestrator._use_role_runtime(
                "coach",
                orchestrator.coach,
                generation=generation_index,
                scenario_name=scenario_name,
                generation_deadline=generation_deadline,
            ),
            orchestrator._use_role_runtime(
                "architect",
                orchestrator.architect,
                generation=generation_index,
                scenario_name=scenario_name,
                generation_deadline=generation_deadline,
            ),
        ):
            enriched_coach_prompt, coach_system = _coach_turn(prompts.coach, analyst_exec.content)
            architect_user, architect_system = _direct_turn(
                orchestrator,
                parts.architect if parts else None,
                orchestrator.architect,
                architect_prompt,
                suffix=architect_cadence,
            )
            coach_kwargs = {"system": coach_system} if coach_system else {}
            architect_kwargs = {"system": architect_system} if architect_system else {}
            with ThreadPoolExecutor(max_workers=2) as pool:
                coach_future = pool.submit(orchestrator.coach.run, enriched_coach_prompt, **coach_kwargs)
                architect_future = pool.submit(orchestrator.architect.run, architect_user, **architect_kwargs)
                coach_exec = coach_future.result()
                notify("coach", "completed")
                architect_exec = architect_future.result()
                notify("architect", "completed")

    return analyst_exec, coach_exec, architect_exec


def _assemble_agent_outputs(
    orchestrator: AgentOrchestrator,
    raw_text: str,
    strategy: dict[str, Any],
    competitor_exec: RoleExecution,
    translator_exec: RoleExecution,
    analyst_exec: RoleExecution,
    coach_exec: RoleExecution,
    architect_exec: RoleExecution,
) -> AgentOutputs:
    """Parse role outputs and assemble the AgentOutputs for this generation."""
    tools = parse_architect_tool_specs(architect_exec.content)
    harness_specs = parse_architect_harness_specs(architect_exec.content)
    coach_playbook, coach_lessons, coach_hints = parse_coach_sections(coach_exec.content)

    competitor_typed = parse_competitor_output(
        raw_text,
        strategy,
        is_code_strategy=orchestrator.settings.code_strategies_enabled,
    )
    analyst_typed = parse_analyst_output(analyst_exec.content)
    coach_typed = parse_coach_output(coach_exec.content)
    architect_typed = parse_architect_output(architect_exec.content)

    return AgentOutputs(
        strategy=strategy,
        analysis_markdown=analyst_exec.content,
        coach_markdown=coach_exec.content,
        coach_playbook=coach_playbook,
        coach_lessons=coach_lessons,
        coach_competitor_hints=coach_hints,
        architect_markdown=architect_exec.content,
        architect_tools=tools,
        architect_harness_specs=harness_specs,
        role_executions=[competitor_exec, translator_exec, analyst_exec, coach_exec, architect_exec],
        competitor_output=competitor_typed,
        analyst_output=analyst_typed,
        coach_output=coach_typed,
        architect_output=architect_typed,
    )
