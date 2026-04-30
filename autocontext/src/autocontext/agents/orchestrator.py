from __future__ import annotations

import json as _json
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autocontext.agents.analyst import AnalystRunner
from autocontext.agents.architect import ArchitectRunner, parse_architect_harness_specs, parse_architect_tool_specs
from autocontext.agents.coach import CoachRunner, parse_coach_sections
from autocontext.agents.competitor import CompetitorRunner
from autocontext.agents.curator import KnowledgeCurator
from autocontext.agents.llm_client import LanguageModelClient, build_client_from_settings
from autocontext.agents.model_router import ModelRouter, TierConfig
from autocontext.agents.parsers import parse_analyst_output, parse_architect_output, parse_coach_output, parse_competitor_output
from autocontext.agents.role_router import ProviderClass, RoleRouter, RoutingContext
from autocontext.agents.role_runtime_overrides import apply_role_overrides, settings_for_budgeted_role_call
from autocontext.agents.skeptic import SkepticAgent
from autocontext.agents.subagent_runtime import SubagentRuntime
from autocontext.agents.translator import StrategyTranslator
from autocontext.agents.trial_summary import build_trial_summary as _build_trial_summary
from autocontext.agents.types import AgentOutputs, RoleExecution
from autocontext.config.settings import AppSettings
from autocontext.execution.harness_coverage import HarnessCoverage
from autocontext.extensions import HookBus, wrap_language_model_client
from autocontext.harness.orchestration.dag import RoleDAG
from autocontext.harness.orchestration.types import RoleSpec
from autocontext.prompts.templates import PromptBundle

if TYPE_CHECKING:
    from autocontext.agents.role_router import ProviderConfig

logger = logging.getLogger(__name__)

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
        from autocontext.harness.repl.monty_worker import MontyReplWorker

        if use_constraints:
            from autocontext.rlm.prompts import (
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
        from autocontext.rlm.prompts import (
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
    from autocontext.rlm.repl_worker import ReplWorker

    if use_constraints:
        from autocontext.rlm.prompts import (
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
    from autocontext.rlm.prompts import ANALYST_RLM_SYSTEM, ARCHITECT_RLM_SYSTEM, COMPETITOR_RLM_SYSTEM

    return _RlmBackendConfig(
        worker_cls=ReplWorker,
        competitor_tpl=COMPETITOR_RLM_SYSTEM,
        analyst_tpl=ANALYST_RLM_SYSTEM,
        architect_tpl=ARCHITECT_RLM_SYSTEM,
    )


def apply_dag_changes(dag: RoleDAG, changes: Sequence[dict[str, Any]]) -> tuple[int, int]:
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
        hook_bus: HookBus | None = None,
    ) -> None:
        self.hook_bus = hook_bus
        self.client = wrap_language_model_client(client, hook_bus)
        self.settings = settings
        self._artifacts = artifacts
        self._harness_coverage_cache: dict[str, HarnessCoverage | None] = {}
        self._routed_clients: dict[tuple[str, str | None, str | None, str | None], LanguageModelClient] = {}
        self._disposable_client_ids: set[int] = set()
        runtime = SubagentRuntime(client=self.client)
        self.competitor = CompetitorRunner(runtime, settings.model_competitor)
        self.translator = StrategyTranslator(runtime, settings.model_translator)
        self.analyst = AnalystRunner(runtime, settings.model_analyst)
        self.coach = CoachRunner(runtime, settings.model_coach)
        self.architect = ArchitectRunner(runtime, settings.model_architect)
        self.curator: KnowledgeCurator | None = None
        if settings.curator_enabled:
            self.curator = KnowledgeCurator(runtime, settings.model_curator)
        self.skeptic: SkepticAgent | None = None
        if settings.skeptic_enabled:
            self.skeptic = SkepticAgent(runtime, settings.model_skeptic)
        self._role_clients: dict[str, LanguageModelClient] = {}
        self._active_generation_deadline: float | None = None
        self._role_router = RoleRouter(settings)

        self._model_router = ModelRouter(
            TierConfig(
                enabled=settings.tier_routing_enabled,
                tier_haiku_model=settings.tier_haiku_model,
                tier_sonnet_model=settings.tier_sonnet_model,
                tier_opus_model=settings.tier_opus_model,
                competitor_haiku_max_gen=settings.tier_competitor_haiku_max_gen,
                harness_aware_tiering_enabled=settings.tier_harness_aware_enabled,
                harness_coverage_demotion_threshold=settings.tier_harness_coverage_demotion_threshold,
            )
        )

        self._rlm_loader = None
        if settings.rlm_enabled and settings.agent_provider != "agent_sdk":
            if artifacts is None or sqlite is None:
                raise ValueError("RLM mode requires artifacts and sqlite stores")
            from autocontext.rlm.context_loader import ContextLoader

            self._rlm_loader = ContextLoader(artifacts, sqlite)

    def _wrap_client(self, client: LanguageModelClient, *, provider_name: str = "") -> LanguageModelClient:
        return wrap_language_model_client(client, self.hook_bus, provider_name=provider_name)

    def _mark_disposable_client(self, client: LanguageModelClient) -> LanguageModelClient:
        self._disposable_client_ids.add(id(client))
        return client

    def _close_disposable_client(self, client: LanguageModelClient) -> None:
        if id(client) not in self._disposable_client_ids:
            return
        self._disposable_client_ids.discard(id(client))
        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:
            logger.debug("failed to close disposable role runtime client", exc_info=True)

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings,
        artifacts: Any | None = None,
        sqlite: Any | None = None,
        hook_bus: HookBus | None = None,
    ) -> AgentOrchestrator:
        client: LanguageModelClient = build_client_from_settings(settings)

        orch = cls(client=client, settings=settings, artifacts=artifacts, sqlite=sqlite, hook_bus=hook_bus)

        # Apply per-role provider overrides (AC-184)
        apply_role_overrides(orch, settings)

        return orch

    def _client_for_role(self, role: str) -> LanguageModelClient:
        return self._role_clients.get(role, self.client)

    def _configured_role_provider(self, role: str) -> str:
        from autocontext.agents.provider_bridge import configured_role_provider

        return configured_role_provider(role, self.settings)

    def _available_local_models(self, scenario_name: str = "", runtime_type: str = "provider") -> list[str]:
        model_path = self.settings.mlx_model_path.strip()
        if not model_path:
            if not scenario_name:
                return []
            try:
                from autocontext.providers.scenario_routing import (
                    ScenarioRoutingContext,
                    resolve_provider_for_context,
                )
                from autocontext.training.model_registry import ModelRegistry

                decision = resolve_provider_for_context(
                    ScenarioRoutingContext(
                        scenario=scenario_name,
                        backend="mlx",
                        runtime_type=runtime_type,
                    ),
                    ModelRegistry(self.settings.knowledge_root),
                    fallback_provider="",
                    fallback_model="",
                )
            except Exception:
                logger.debug("agents.orchestrator: caught Exception", exc_info=True)
                return []
            if decision.fallback_used or decision.provider_type != "mlx":
                return []
            candidate_path = decision.model.strip()
            return [candidate_path] if candidate_path and Path(candidate_path).exists() else []
        return [model_path] if Path(model_path).exists() else []

    def _scenario_bound_runtime_client(
        self,
        provider_type: str,
        role: str,
        *,
        scenario_name: str,
    ) -> LanguageModelClient | None:
        if provider_type not in {"pi", "pi-rpc"} or not scenario_name:
            return None

        from autocontext.agents.provider_bridge import create_role_client

        call_settings, is_budgeted = settings_for_budgeted_role_call(
            self.settings,
            provider_type,
            role,
            self._active_generation_deadline,
        )
        key = (provider_type.lower(), None, scenario_name, role)
        if not is_budgeted:
            cached = self._routed_clients.get(key)
            if cached is not None:
                return cached

        client = create_role_client(
            provider_type,
            call_settings,
            scenario_name=scenario_name,
            role=role,
        )
        if client is not None:
            client = self._wrap_client(client, provider_name=f"{provider_type}:{role}")
            if is_budgeted:
                self._mark_disposable_client(client)
            else:
                self._routed_clients[key] = client
        return client

    def _scenario_bound_override_client(
        self,
        role: str,
        *,
        scenario_name: str,
    ) -> LanguageModelClient | None:
        explicit_provider = self._configured_role_provider(role)
        return self._scenario_bound_runtime_client(
            explicit_provider,
            role,
            scenario_name=scenario_name,
        )

    def _scenario_bound_default_client(
        self,
        role: str,
        *,
        scenario_name: str,
    ) -> LanguageModelClient | None:
        return self._scenario_bound_runtime_client(
            self.settings.agent_provider.lower().strip(),
            role,
            scenario_name=scenario_name,
        )

    def _resolve_role_provider_config(
        self,
        role: str,
        *,
        generation: int,
        retry_count: int = 0,
        is_plateau: bool = False,
        scenario_name: str = "",
    ) -> ProviderConfig | None:
        if self.settings.role_routing != "auto":
            return None
        context = RoutingContext(
            generation=generation,
            retry_count=retry_count,
            is_plateau=is_plateau,
            available_local_models=self._available_local_models(
                scenario_name=scenario_name,
                runtime_type="provider",
            ),
            scenario_name=scenario_name,
        )
        return self._role_router.route(role, context=context)

    def _client_for_provider_config(
        self,
        role: str,
        config: ProviderConfig,
        *,
        scenario_name: str = "",
    ) -> LanguageModelClient:
        default_provider = self.settings.agent_provider.lower()
        default_scenario_client = self._scenario_bound_default_client(role, scenario_name=scenario_name)
        openai_like_default = default_provider in ("openai", "openai-compatible", "ollama", "vllm")
        if (
            config.provider_type == self.settings.agent_provider
            and config.provider_class != ProviderClass.LOCAL
            and not self._configured_role_provider(role)
            and (not openai_like_default or config.model in (None, "", self.settings.agent_default_model))
        ):
            return default_scenario_client or self._client_for_role(role)

        explicit_provider = self._configured_role_provider(role)
        if explicit_provider and explicit_provider == config.provider_type.lower():
            explicit_client = self._role_clients.get(role)
            if explicit_provider in {"pi", "pi-rpc"}:
                scenario_client = self._scenario_bound_override_client(role, scenario_name=scenario_name)
                if scenario_client is not None:
                    return scenario_client
            if explicit_client is not None and (
                config.provider_class != ProviderClass.LOCAL or config.model == self.settings.mlx_model_path
            ):
                return explicit_client

        if (
            config.provider_type == self.settings.agent_provider
            and config.provider_class == ProviderClass.LOCAL
            and config.model == self.settings.mlx_model_path
            and not explicit_provider
        ):
            return default_scenario_client or self._client_for_role(role)

        from autocontext.agents.provider_bridge import create_role_client

        call_settings, is_budgeted = settings_for_budgeted_role_call(
            self.settings,
            config.provider_type,
            role,
            self._active_generation_deadline,
        )
        key = (config.provider_type.lower(), config.model, scenario_name or None, role)
        if not is_budgeted:
            cached = self._routed_clients.get(key)
            if cached is not None:
                return cached
        client = create_role_client(
            config.provider_type,
            call_settings,
            model_override=config.model,
            scenario_name=scenario_name,
            role=role,
        )
        if client is None:
            return self._client_for_role(role)
        client = self._wrap_client(client, provider_name=f"{config.provider_type}:{role}")
        if is_budgeted:
            self._mark_disposable_client(client)
        else:
            self._routed_clients[key] = client
        return client

    def _resolve_role_execution(
        self,
        role: str,
        *,
        generation: int,
        retry_count: int = 0,
        is_plateau: bool = False,
        scenario_name: str = "",
    ) -> tuple[LanguageModelClient, str | None]:
        client = (
            self._scenario_bound_override_client(role, scenario_name=scenario_name)
            or self._scenario_bound_default_client(role, scenario_name=scenario_name)
            or self._client_for_role(role)
        )
        model = self.resolve_model(
            role,
            generation=generation,
            retry_count=retry_count,
            is_plateau=is_plateau,
            scenario_name=scenario_name,
        )
        provider_config = self._resolve_role_provider_config(
            role,
            generation=generation,
            retry_count=retry_count,
            is_plateau=is_plateau,
            scenario_name=scenario_name,
        )
        if provider_config is None:
            return client, model
        client = self._client_for_provider_config(role, provider_config, scenario_name=scenario_name)
        if provider_config.provider_class == ProviderClass.LOCAL:
            return client, provider_config.model
        return client, model or provider_config.model

    def resolve_role_execution(
        self,
        role: str,
        *,
        generation: int,
        retry_count: int = 0,
        is_plateau: bool = False,
        scenario_name: str = "",
        generation_deadline: float | None = None,
    ) -> tuple[LanguageModelClient, str | None]:
        """Resolve the effective client and model for a role execution.

        This is the stable public wrapper for non-runner pipeline stages that need
        to respect per-role overrides and automatic routing decisions.
        """
        previous_deadline = self._active_generation_deadline
        self._active_generation_deadline = generation_deadline
        try:
            return self._resolve_role_execution(
                role,
                generation=generation,
                retry_count=retry_count,
                is_plateau=is_plateau,
                scenario_name=scenario_name,
            )
        finally:
            self._active_generation_deadline = previous_deadline

    @contextmanager
    def _use_role_runtime(
        self,
        role: str,
        runner: Any,
        *,
        generation: int,
        retry_count: int = 0,
        is_plateau: bool = False,
        scenario_name: str = "",
        generation_deadline: float | None = None,
    ) -> Any:
        original_client = runner.runtime.client
        original_model = runner.model
        previous_deadline = self._active_generation_deadline
        self._active_generation_deadline = generation_deadline
        client: LanguageModelClient | None = None
        try:
            client, model = self._resolve_role_execution(
                role,
                generation=generation,
                retry_count=retry_count,
                is_plateau=is_plateau,
                scenario_name=scenario_name,
            )
        finally:
            self._active_generation_deadline = previous_deadline
        runner.runtime.client = client
        if model is not None:
            runner.model = model
        try:
            yield model
        finally:
            runner.runtime.client = original_client
            runner.model = original_model
            if client is not None:
                self._close_disposable_client(client)

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
        generation_deadline: float | None = None,
    ) -> AgentOutputs:
        # Feature-gated pipeline codepath (skips RLM path when active)
        if self.settings.use_pipeline_engine and not (self.settings.rlm_enabled and self._rlm_loader is not None):
            return self._run_via_pipeline(
                prompts,
                generation_index,
                scenario_name,
                tool_context,
                strategy_interface,
                on_role_event,
                generation_deadline,
            )

        def _notify(role: str, status: str) -> None:
            if on_role_event:
                on_role_event(role, status)

        # --- Competitor phase ---
        competitor_model = (
            self.resolve_model(
                "competitor",
                generation=generation_index,
                scenario_name=scenario_name,
            )
            or self.competitor.model
        )
        use_competitor_rlm = (
            self.settings.rlm_enabled
            and self.settings.rlm_competitor_enabled
            and self._rlm_loader is not None
            and self.settings.agent_provider != "agent_sdk"
        )

        if use_competitor_rlm:
            _notify("competitor", "started")
            raw_text, competitor_exec = self._run_rlm_competitor(
                run_id,
                scenario_name,
                generation_index,
                model=competitor_model,
                strategy_interface=strategy_interface,
                scenario_rules=scenario_rules,
                current_strategy=current_strategy,
            )
            _notify("competitor", "completed")
        else:
            _notify("competitor", "started")
            competitor_prompt = prompts.competitor
            if self.settings.code_strategies_enabled:
                from autocontext.prompts.templates import code_strategy_competitor_suffix

                competitor_prompt += code_strategy_competitor_suffix(strategy_interface)
            with self._use_role_runtime(
                "competitor",
                self.competitor,
                generation=generation_index,
                scenario_name=scenario_name,
                generation_deadline=generation_deadline,
            ):
                raw_text, competitor_exec = self.competitor.run(competitor_prompt, tool_context=tool_context)
            _notify("competitor", "completed")

        _notify("translator", "started")
        with self._use_role_runtime(
            "translator",
            self.translator,
            generation=generation_index,
            scenario_name=scenario_name,
            generation_deadline=generation_deadline,
        ):
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
                run_id,
                scenario_name,
                generation_index,
                strategy,
                architect_prompt,
                scenario_rules=scenario_rules,
            )
            _notify("analyst", "completed")
            _notify("architect", "completed")
            _notify("coach", "started")
            enriched_coach_prompt = self._enrich_coach_prompt(prompts.coach, analyst_exec.content)
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self._use_role_runtime(
                    "coach",
                    self.coach,
                    generation=generation_index,
                    scenario_name=scenario_name,
                    generation_deadline=generation_deadline,
                ):
                    coach_future = pool.submit(self.coach.run, enriched_coach_prompt)
                    coach_exec = coach_future.result()
            _notify("coach", "completed")
        else:
            # Analyst runs first; its output enriches the coach prompt
            _notify("analyst", "started")
            with self._use_role_runtime(
                "analyst",
                self.analyst,
                generation=generation_index,
                scenario_name=scenario_name,
                generation_deadline=generation_deadline,
            ):
                analyst_exec = self.analyst.run(prompts.analyst)
            _notify("analyst", "completed")
            enriched_coach_prompt = self._enrich_coach_prompt(prompts.coach, analyst_exec.content)
            _notify("coach", "started")
            _notify("architect", "started")
            with (
                self._use_role_runtime(
                    "coach",
                    self.coach,
                    generation=generation_index,
                    scenario_name=scenario_name,
                    generation_deadline=generation_deadline,
                ),
                self._use_role_runtime(
                    "architect",
                    self.architect,
                    generation=generation_index,
                    scenario_name=scenario_name,
                    generation_deadline=generation_deadline,
                ),
            ):
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
            raw_text,
            strategy,
            is_code_strategy=self.settings.code_strategies_enabled,
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
        scenario_name: str,
        tool_context: str,
        strategy_interface: str,
        on_role_event: Callable[[str, str], None] | None,
        generation_deadline: float | None = None,
    ) -> AgentOutputs:
        """Execute the 5-role generation via PipelineEngine."""
        from autocontext.agents.pipeline_adapter import build_mts_dag, build_role_handler
        from autocontext.harness.orchestration.engine import PipelineEngine

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

        handler = build_role_handler(
            self,
            generation=generation_index,
            scenario_name=scenario_name,
            tool_context=tool_context,
            strategy_interface=strategy_interface,
            generation_deadline=generation_deadline,
        )
        engine = PipelineEngine(dag, handler, max_workers=2)
        results = engine.execute(prompt_map, on_role_event=on_role_event)

        # Extract strategy from translator result
        from autocontext.harness.core.output_parser import strip_json_fences

        try:
            strategy = _json.loads(strip_json_fences(results["translator"].content))
        except (_json.JSONDecodeError, TypeError):
            strategy = {}

        tools = parse_architect_tool_specs(results["architect"].content)
        harness_specs = parse_architect_harness_specs(results["architect"].content)
        coach_playbook, coach_lessons, coach_hints = parse_coach_sections(results["coach"].content)

        competitor_typed = parse_competitor_output(
            results["competitor"].content,
            strategy,
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
            role_executions=[results[r] for r in ["competitor", "translator", "analyst", "coach", "architect"]],
            competitor_output=competitor_typed,
            analyst_output=analyst_typed,
            coach_output=coach_typed,
            architect_output=architect_typed,
        )

    def resolve_model(
        self,
        role: str,
        *,
        generation: int,
        retry_count: int = 0,
        is_plateau: bool = False,
        scenario_name: str = "",
        harness_coverage: HarnessCoverage | None = None,
    ) -> str | None:
        """Return the model to use for a role, or None to use the default."""
        if harness_coverage is None and role == "competitor":
            harness_coverage = self._get_harness_coverage(scenario_name)
        return self._model_router.select(
            role,
            generation=generation,
            retry_count=retry_count,
            is_plateau=is_plateau,
            harness_coverage=harness_coverage,
        )

    def _get_harness_coverage(self, scenario_name: str) -> HarnessCoverage | None:
        """Load and cache harness coverage for a scenario when routing needs it."""
        if not self.settings.tier_harness_aware_enabled or not scenario_name or self._artifacts is None:
            return None
        if scenario_name in self._harness_coverage_cache:
            return self._harness_coverage_cache[scenario_name]

        from autocontext.execution.harness_coverage import HarnessCoverageAnalyzer
        from autocontext.execution.harness_loader import HarnessLoader

        harness_dir = self._artifacts.harness_dir(scenario_name)
        loader = HarnessLoader(harness_dir, timeout_seconds=self.settings.harness_timeout_seconds)
        loader.load()
        if not loader.loaded_names:
            self._harness_coverage_cache[scenario_name] = None
            return None

        # Until we persist historical harness accuracy, treat loaded scenario
        # harnesses as trusted executable constraints for routing purposes.
        coverage = HarnessCoverageAnalyzer().analyze(loader, validation_accuracy=1.0)
        self._harness_coverage_cache[scenario_name] = coverage
        return coverage

    def _enrich_coach_prompt(self, base_prompt: str, analyst_content: str) -> str:
        return base_prompt + f"\n\n--- Analyst findings (this generation) ---\n{analyst_content}\n"

    def _run_single_rlm_session(
        self,
        role: str,
        model: str,
        system_tpl: str,
        context: Any,
        worker_cls: type,
        *,
        client: LanguageModelClient | None = None,
    ) -> tuple[RoleExecution, list[Any]]:
        """Build and run a single RLM REPL session for the given role.

        Returns (role_execution, execution_history) where execution_history
        is a list of ExecutionRecord from the session.
        """
        from autocontext.rlm.session import RlmSession, make_llm_batch

        settings = self.settings
        ns = dict(context.variables)
        role_client = client or self._client_for_role(role)
        ns["llm_batch"] = make_llm_batch(role_client, settings.rlm_sub_model)
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
            client=role_client,
            worker=worker,
            role=role,
            model=model,
            system_prompt=system_prompt,
            max_turns=settings.rlm_max_turns,
        )
        role_exec = session.run()
        return role_exec, session.execution_history

    def _run_rlm_competitor(
        self,
        run_id: str,
        scenario_name: str,
        generation_index: int,
        *,
        model: str | None = None,
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
        competitor_client, resolved_model = self._resolve_role_execution(
            "competitor",
            generation=generation_index,
            scenario_name=scenario_name,
        )
        if hasattr(competitor_client, "reset_rlm_turns"):
            competitor_client.reset_rlm_turns()

        competitor_ctx = self._rlm_loader.load_for_competitor(
            run_id,
            scenario_name,
            generation_index,
            strategy_interface=strategy_interface,
            scenario_rules=scenario_rules,
            current_strategy=current_strategy,
        )
        resolved_model = model or resolved_model or self.settings.model_competitor
        competitor_exec, exec_history = self._run_single_rlm_session(
            role="competitor",
            model=resolved_model,
            system_tpl=backend.competitor_tpl,
            context=competitor_ctx,
            worker_cls=backend.worker_cls,
            client=competitor_client,
        )

        # Store RLM trial summary for experiment log
        if exec_history:
            summary = _build_trial_summary(generation_index, exec_history, competitor_exec)
            try:
                self._rlm_loader.sqlite.append_agent_output(
                    run_id,
                    generation_index,
                    "competitor_rlm_trials",
                    summary,
                )
            except Exception:
                logger.debug("failed to store RLM trial summary", exc_info=True)

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
        analyst_client, analyst_model = self._resolve_role_execution(
            "analyst",
            generation=generation_index,
            scenario_name=scenario_name,
        )
        if hasattr(analyst_client, "reset_rlm_turns"):
            analyst_client.reset_rlm_turns()

        # --- Analyst ---
        analyst_ctx = self._rlm_loader.load_for_analyst(
            run_id,
            scenario_name,
            generation_index,
            scenario_rules=scenario_rules,
            current_strategy=strategy,
        )
        analyst_exec, _ = self._run_single_rlm_session(
            role="analyst",
            model=analyst_model or self.settings.model_analyst,
            system_tpl=backend.analyst_tpl,
            context=analyst_ctx,
            worker_cls=backend.worker_cls,
            client=analyst_client,
        )

        # Reset turn counter between roles for deterministic client
        architect_client, architect_model = self._resolve_role_execution(
            "architect",
            generation=generation_index,
            scenario_name=scenario_name,
        )
        if hasattr(architect_client, "reset_rlm_turns"):
            architect_client.reset_rlm_turns()

        # --- Architect ---
        architect_ctx = self._rlm_loader.load_for_architect(
            run_id,
            scenario_name,
            generation_index,
            scenario_rules=scenario_rules,
        )
        architect_exec, _ = self._run_single_rlm_session(
            role="architect",
            model=architect_model or self.settings.model_architect,
            system_tpl=backend.architect_tpl,
            context=architect_ctx,
            worker_cls=backend.worker_cls,
            client=architect_client,
        )

        return analyst_exec, architect_exec
