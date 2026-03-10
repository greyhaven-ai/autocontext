from __future__ import annotations

import json as _json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from mts.agents.analyst import AnalystRunner
from mts.agents.architect import ArchitectRunner, parse_architect_harness_specs, parse_architect_tool_specs
from mts.agents.coach import CoachRunner, parse_coach_sections
from mts.agents.competitor import CompetitorRunner
from mts.agents.curator import KnowledgeCurator
from mts.agents.llm_client import AnthropicClient, DeterministicDevClient, LanguageModelClient
from mts.agents.model_router import ModelRouter, TierConfig
from mts.agents.parsers import parse_analyst_output, parse_architect_output, parse_coach_output, parse_competitor_output
from mts.agents.subagent_runtime import SubagentRuntime
from mts.agents.translator import StrategyTranslator
from mts.agents.types import AgentOutputs, RoleExecution
from mts.config.settings import AppSettings
from mts.harness.orchestration.dag import RoleDAG
from mts.harness.orchestration.types import RoleSpec
from mts.prompts.templates import PromptBundle

LOGGER = logging.getLogger(__name__)

_ARCHITECT_CADENCE_SKIP = "\n\nArchitect cadence note: no major intervention; return minimal status + empty tools array."


@dataclass(frozen=True, slots=True)
class _RlmBackendConfig:
    """Resolved RLM worker class and per-role prompt templates."""

    worker_cls: type
    competitor_tpl: str
    analyst_tpl: str
    architect_tpl: str


def _resolve_rlm_backend(settings: AppSettings) -> _RlmBackendConfig:
    """Select worker class and prompt templates based on backend + constraint mode."""
    use_constraints = settings.constraint_prompts_enabled
    if settings.rlm_backend == "monty":
        from mts.harness.repl.monty_worker import MontyReplWorker

        if use_constraints:
            from mts.rlm.prompts import (
                ANALYST_MONTY_RLM_SYSTEM_CONSTRAINED,
                ARCHITECT_MONTY_RLM_SYSTEM_CONSTRAINED,
                COMPETITOR_MONTY_RLM_SYSTEM_CONSTRAINED,
            )
            return _RlmBackendConfig(
                worker_cls=MontyReplWorker,
                competitor_tpl=COMPETITOR_MONTY_RLM_SYSTEM_CONSTRAINED,
                analyst_tpl=ANALYST_MONTY_RLM_SYSTEM_CONSTRAINED,
                architect_tpl=ARCHITECT_MONTY_RLM_SYSTEM_CONSTRAINED,
            )
        from mts.rlm.prompts import (
            ANALYST_MONTY_RLM_SYSTEM,
            ARCHITECT_MONTY_RLM_SYSTEM,
            COMPETITOR_MONTY_RLM_SYSTEM,
        )
        return _RlmBackendConfig(
            worker_cls=MontyReplWorker,
            competitor_tpl=COMPETITOR_MONTY_RLM_SYSTEM,
            analyst_tpl=ANALYST_MONTY_RLM_SYSTEM,
            architect_tpl=ARCHITECT_MONTY_RLM_SYSTEM,
        )
    # Default: exec backend
    from mts.rlm.repl_worker import ReplWorker

    if use_constraints:
        from mts.rlm.prompts import (
            ANALYST_RLM_SYSTEM_CONSTRAINED,
            ARCHITECT_RLM_SYSTEM_CONSTRAINED,
            COMPETITOR_RLM_SYSTEM_CONSTRAINED,
        )
        return _RlmBackendConfig(
            worker_cls=ReplWorker,
            competitor_tpl=COMPETITOR_RLM_SYSTEM_CONSTRAINED,
            analyst_tpl=ANALYST_RLM_SYSTEM_CONSTRAINED,
            architect_tpl=ARCHITECT_RLM_SYSTEM_CONSTRAINED,
        )
    from mts.rlm.prompts import ANALYST_RLM_SYSTEM, ARCHITECT_RLM_SYSTEM, COMPETITOR_RLM_SYSTEM

    return _RlmBackendConfig(
        worker_cls=ReplWorker,
        competitor_tpl=COMPETITOR_RLM_SYSTEM,
        analyst_tpl=ANALYST_RLM_SYSTEM,
        architect_tpl=ARCHITECT_RLM_SYSTEM,
    )


def apply_dag_changes(dag: RoleDAG, changes: list[dict[str, Any]]) -> tuple[int, int]:
    """Apply a list of DAG change directives. Returns (applied, skipped) counts."""
    applied = 0
    skipped = 0
    for change in changes:
        action = change.get("action")
        name = change.get("name", "")
        try:
            if action == "add_role":
                deps = tuple(change.get("depends_on", []))
                dag.add_role(RoleSpec(name=name, depends_on=deps))
                applied += 1
            elif action == "remove_role":
                dag.remove_role(name)
                applied += 1
            else:
                skipped += 1
        except ValueError:
            skipped += 1
    return applied, skipped


class AgentOrchestrator:
    """Runs competitor/analyst/coach/architect role sequence."""

    def __init__(
        self,
        client: LanguageModelClient,
        settings: AppSettings,
        artifacts: Any | None = None,
        sqlite: Any | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        runtime = SubagentRuntime(client=client)
        self.competitor = CompetitorRunner(runtime, settings.model_competitor)
        self.translator = StrategyTranslator(runtime, settings.model_translator)
        self.analyst = AnalystRunner(runtime, settings.model_analyst)
        self.coach = CoachRunner(runtime, settings.model_coach)
        self.architect = ArchitectRunner(runtime, settings.model_architect)
        self.curator: KnowledgeCurator | None = None
        if settings.curator_enabled:
            self.curator = KnowledgeCurator(runtime, settings.model_curator)

        self._model_router = ModelRouter(TierConfig(
            enabled=settings.tier_routing_enabled,
            tier_haiku_model=settings.tier_haiku_model,
            tier_sonnet_model=settings.tier_sonnet_model,
            tier_opus_model=settings.tier_opus_model,
            competitor_haiku_max_gen=settings.tier_competitor_haiku_max_gen,
        ))

        self._rlm_loader = None
        if settings.rlm_enabled and settings.agent_provider != "agent_sdk":
            if artifacts is None or sqlite is None:
                raise ValueError("RLM mode requires artifacts and sqlite stores")
            from mts.rlm.context_loader import ContextLoader

            self._rlm_loader = ContextLoader(artifacts, sqlite)

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        artifacts: Any | None = None,
        sqlite: Any | None = None,
    ) -> AgentOrchestrator:
        if settings.agent_provider == "anthropic":
            if not settings.anthropic_api_key:
                raise ValueError("MTS_ANTHROPIC_API_KEY is required when MTS_AGENT_PROVIDER=anthropic")
            client: LanguageModelClient = AnthropicClient(api_key=settings.anthropic_api_key)
        elif settings.agent_provider == "deterministic":
            client = DeterministicDevClient()
        elif settings.agent_provider == "agent_sdk":
            from mts.agents.agent_sdk_client import AgentSdkClient, AgentSdkConfig

            sdk_config = AgentSdkConfig(connect_mcp_server=settings.agent_sdk_connect_mcp)
            client = AgentSdkClient(config=sdk_config)
        else:
            raise ValueError(f"unsupported agent provider: {settings.agent_provider}")
        return cls(client=client, settings=settings, artifacts=artifacts, sqlite=sqlite)

    def run_generation(
        self,
        prompts: PromptBundle,
        generation_index: int,
        tool_context: str = "",
        run_id: str = "",
        scenario_name: str = "",
        strategy_interface: str = "",
        on_role_event: Callable[[str, str], None] | None = None,
        scenario_rules: str = "",
        current_strategy: dict[str, Any] | None = None,
    ) -> AgentOutputs:
        # Feature-gated pipeline codepath (skips RLM path when active)
        if self.settings.use_pipeline_engine and not (
            self.settings.rlm_enabled and self._rlm_loader is not None
        ):
            return self._run_via_pipeline(
                prompts, generation_index, tool_context, strategy_interface, on_role_event,
            )

        def _notify(role: str, status: str) -> None:
            if on_role_event:
                on_role_event(role, status)

        # --- Competitor phase ---
        use_competitor_rlm = (
            self.settings.rlm_enabled
            and self.settings.rlm_competitor_enabled
            and self._rlm_loader is not None
            and self.settings.agent_provider != "agent_sdk"
        )

        if use_competitor_rlm:
            _notify("competitor", "started")
            raw_text, competitor_exec = self._run_rlm_competitor(
                run_id, scenario_name, generation_index,
                strategy_interface=strategy_interface,
                scenario_rules=scenario_rules,
                current_strategy=current_strategy,
            )
            _notify("competitor", "completed")
        else:
            _notify("competitor", "started")
            competitor_prompt = prompts.competitor
            if self.settings.code_strategies_enabled:
                from mts.prompts.templates import code_strategy_competitor_suffix
                competitor_prompt += code_strategy_competitor_suffix(strategy_interface)
            raw_text, competitor_exec = self.competitor.run(competitor_prompt, tool_context=tool_context)
            _notify("competitor", "completed")

        _notify("translator", "started")
        if self.settings.code_strategies_enabled:
            strategy, translator_exec = self.translator.translate_code(raw_text)
        else:
            strategy, translator_exec = self.translator.translate(raw_text, strategy_interface)
        _notify("translator", "completed")
        architect_prompt = prompts.architect
        if generation_index % self.settings.architect_every_n_gens != 0:
            architect_prompt += _ARCHITECT_CADENCE_SKIP

        if self.settings.rlm_enabled and self._rlm_loader is not None and self.settings.agent_provider != "agent_sdk":
            _notify("analyst", "started")
            _notify("architect", "started")
            analyst_exec, architect_exec = self._run_rlm_roles(
                run_id, scenario_name, generation_index, strategy, architect_prompt,
                scenario_rules=scenario_rules,
            )
            _notify("analyst", "completed")
            _notify("architect", "completed")
            _notify("coach", "started")
            enriched_coach_prompt = self._enrich_coach_prompt(prompts.coach, analyst_exec.content)
            with ThreadPoolExecutor(max_workers=1) as pool:
                coach_future = pool.submit(self.coach.run, enriched_coach_prompt)
                coach_exec = coach_future.result()
            _notify("coach", "completed")
        else:
            # Analyst runs first; its output enriches the coach prompt
            _notify("analyst", "started")
            analyst_exec = self.analyst.run(prompts.analyst)
            _notify("analyst", "completed")
            enriched_coach_prompt = self._enrich_coach_prompt(prompts.coach, analyst_exec.content)
            _notify("coach", "started")
            _notify("architect", "started")
            with ThreadPoolExecutor(max_workers=2) as pool:
                coach_future = pool.submit(self.coach.run, enriched_coach_prompt)
                architect_future = pool.submit(self.architect.run, architect_prompt)
                coach_exec = coach_future.result()
                _notify("coach", "completed")
                architect_exec = architect_future.result()
                _notify("architect", "completed")

        tools = parse_architect_tool_specs(architect_exec.content)
        harness_specs = parse_architect_harness_specs(architect_exec.content)
        coach_playbook, coach_lessons, coach_hints = parse_coach_sections(coach_exec.content)

        # Parse typed contracts
        competitor_typed = parse_competitor_output(
            raw_text, strategy, is_code_strategy=self.settings.code_strategies_enabled,
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

    def _run_via_pipeline(
        self,
        prompts: PromptBundle,
        generation_index: int,
        tool_context: str,
        strategy_interface: str,
        on_role_event: Callable[[str, str], None] | None,
    ) -> AgentOutputs:
        """Execute the 5-role generation via PipelineEngine."""
        from mts.agents.pipeline_adapter import build_mts_dag, build_role_handler
        from mts.harness.orchestration.engine import PipelineEngine

        dag = build_mts_dag()

        architect_prompt = prompts.architect
        if generation_index % self.settings.architect_every_n_gens != 0:
            architect_prompt += _ARCHITECT_CADENCE_SKIP

        prompt_map = {
            "competitor": prompts.competitor,
            "translator": "",  # translator uses competitor output, not a prompt
            "analyst": prompts.analyst,
            "architect": architect_prompt,
            "coach": prompts.coach,
        }

        handler = build_role_handler(self, tool_context=tool_context, strategy_interface=strategy_interface)
        engine = PipelineEngine(dag, handler, max_workers=2)
        results = engine.execute(prompt_map, on_role_event=on_role_event)

        # Extract strategy from translator result
        from mts.harness.core.output_parser import strip_json_fences

        try:
            strategy = _json.loads(strip_json_fences(results["translator"].content))
        except (_json.JSONDecodeError, TypeError):
            strategy = {}

        tools = parse_architect_tool_specs(results["architect"].content)
        harness_specs = parse_architect_harness_specs(results["architect"].content)
        coach_playbook, coach_lessons, coach_hints = parse_coach_sections(results["coach"].content)

        competitor_typed = parse_competitor_output(
            results["competitor"].content, strategy,
            is_code_strategy=self.settings.code_strategies_enabled,
        )
        analyst_typed = parse_analyst_output(results["analyst"].content)
        coach_typed = parse_coach_output(results["coach"].content)
        architect_typed = parse_architect_output(results["architect"].content)

        return AgentOutputs(
            strategy=strategy,
            analysis_markdown=results["analyst"].content,
            coach_markdown=results["coach"].content,
            coach_playbook=coach_playbook,
            coach_lessons=coach_lessons,
            coach_competitor_hints=coach_hints,
            architect_markdown=results["architect"].content,
            architect_tools=tools,
            architect_harness_specs=harness_specs,
            role_executions=[
                results[r] for r in ["competitor", "translator", "analyst", "coach", "architect"]
            ],
            competitor_output=competitor_typed,
            analyst_output=analyst_typed,
            coach_output=coach_typed,
            architect_output=architect_typed,
        )

    def resolve_model(self, role: str, *, generation: int, retry_count: int = 0, is_plateau: bool = False) -> str | None:
        """Return the model to use for a role, or None to use the default."""
        return self._model_router.select(role, generation=generation, retry_count=retry_count, is_plateau=is_plateau)

    def _enrich_coach_prompt(self, base_prompt: str, analyst_content: str) -> str:
        return base_prompt + f"\n\n--- Analyst findings (this generation) ---\n{analyst_content}\n"

    def _run_single_rlm_session(
        self,
        role: str,
        model: str,
        system_tpl: str,
        context: Any,
        worker_cls: type,
    ) -> RoleExecution:
        """Build and run a single RLM REPL session for the given role."""
        from mts.rlm.session import RlmSession, make_llm_batch

        settings = self.settings
        ns = dict(context.variables)
        ns["llm_batch"] = make_llm_batch(self.client, settings.rlm_sub_model)
        worker = worker_cls(
            namespace=ns,
            max_stdout_chars=settings.rlm_max_stdout_chars,
            timeout_seconds=settings.rlm_code_timeout_seconds,
        )
        system_prompt = system_tpl.format(
            max_stdout_chars=settings.rlm_max_stdout_chars,
            max_turns=settings.rlm_max_turns,
            variable_summary=context.summary,
        )
        session = RlmSession(
            client=self.client,
            worker=worker,
            role=role,
            model=model,
            system_prompt=system_prompt,
            max_turns=settings.rlm_max_turns,
        )
        return session.run()

    def _run_rlm_competitor(
        self,
        run_id: str,
        scenario_name: str,
        generation_index: int,
        *,
        strategy_interface: str = "",
        scenario_rules: str = "",
        current_strategy: dict[str, Any] | None = None,
    ) -> tuple[str, RoleExecution]:
        """Run the Competitor via an RLM REPL session.

        Returns (raw_text, competitor_exec) matching the CompetitorRunner.run() contract.
        The raw_text is the answer content (expected to be a JSON strategy string).
        """
        if self._rlm_loader is None:
            raise RuntimeError("RLM loader not initialized")

        backend = _resolve_rlm_backend(self.settings)

        # Reset deterministic client turn counter if applicable
        if hasattr(self.client, "reset_rlm_turns"):
            self.client.reset_rlm_turns()

        competitor_ctx = self._rlm_loader.load_for_competitor(
            run_id, scenario_name, generation_index,
            strategy_interface=strategy_interface,
            scenario_rules=scenario_rules,
            current_strategy=current_strategy,
        )
        competitor_exec = self._run_single_rlm_session(
            role="competitor",
            model=self.settings.model_competitor,
            system_tpl=backend.competitor_tpl,
            context=competitor_ctx,
            worker_cls=backend.worker_cls,
        )
        raw_text = competitor_exec.content
        return raw_text, competitor_exec

    def _run_rlm_roles(
        self,
        run_id: str,
        scenario_name: str,
        generation_index: int,
        strategy: dict[str, Any],
        architect_prompt: str,
        *,
        scenario_rules: str = "",
    ) -> tuple[RoleExecution, RoleExecution]:
        """Run Analyst and Architect via RLM sessions."""
        if self._rlm_loader is None:
            raise RuntimeError("RLM loader not initialized")

        backend = _resolve_rlm_backend(self.settings)

        # Reset deterministic client turn counter if applicable
        if hasattr(self.client, "reset_rlm_turns"):
            self.client.reset_rlm_turns()

        # --- Analyst ---
        analyst_ctx = self._rlm_loader.load_for_analyst(
            run_id, scenario_name, generation_index,
            scenario_rules=scenario_rules,
            current_strategy=strategy,
        )
        analyst_exec = self._run_single_rlm_session(
            role="analyst",
            model=self.settings.model_analyst,
            system_tpl=backend.analyst_tpl,
            context=analyst_ctx,
            worker_cls=backend.worker_cls,
        )

        # Reset turn counter between roles for deterministic client
        if hasattr(self.client, "reset_rlm_turns"):
            self.client.reset_rlm_turns()

        # --- Architect ---
        architect_ctx = self._rlm_loader.load_for_architect(
            run_id, scenario_name, generation_index,
            scenario_rules=scenario_rules,
        )
        architect_exec = self._run_single_rlm_session(
            role="architect",
            model=self.settings.model_architect,
            system_tpl=backend.architect_tpl,
            context=architect_ctx,
            worker_cls=backend.worker_cls,
        )

        return analyst_exec, architect_exec
