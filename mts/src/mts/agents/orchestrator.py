from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from mts.agents.analyst import AnalystRunner
from mts.agents.architect import ArchitectRunner, parse_architect_tool_specs
from mts.agents.coach import CoachRunner, parse_coach_sections
from mts.agents.competitor import CompetitorRunner
from mts.agents.curator import KnowledgeCurator
from mts.agents.llm_client import AnthropicClient, DeterministicDevClient, LanguageModelClient
from mts.agents.subagent_runtime import SubagentRuntime
from mts.agents.translator import StrategyTranslator
from mts.agents.types import AgentOutputs, RoleExecution
from mts.config.settings import AppSettings
from mts.prompts.templates import PromptBundle

LOGGER = logging.getLogger(__name__)


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
    ) -> AgentOutputs:
        raw_text, competitor_exec = self.competitor.run(prompts.competitor, tool_context=tool_context)
        strategy, translator_exec = self.translator.translate(raw_text, strategy_interface)
        architect_prompt = prompts.architect
        if generation_index % self.settings.architect_every_n_gens != 0:
            architect_prompt += "\n\nArchitect cadence note: no major intervention; return minimal status + empty tools array."

        if self.settings.rlm_enabled and self._rlm_loader is not None and self.settings.agent_provider != "agent_sdk":
            analyst_exec, architect_exec = self._run_rlm_roles(
                run_id, scenario_name, generation_index, strategy, architect_prompt,
            )
            enriched_coach_prompt = self._enrich_coach_prompt(prompts.coach, analyst_exec.content)
            with ThreadPoolExecutor(max_workers=1) as pool:
                coach_future = pool.submit(self.coach.run, enriched_coach_prompt)
                coach_exec = coach_future.result()
        else:
            # Analyst runs first; its output enriches the coach prompt
            analyst_exec = self.analyst.run(prompts.analyst)
            enriched_coach_prompt = self._enrich_coach_prompt(prompts.coach, analyst_exec.content)
            with ThreadPoolExecutor(max_workers=2) as pool:
                coach_future = pool.submit(self.coach.run, enriched_coach_prompt)
                architect_future = pool.submit(self.architect.run, architect_prompt)
                coach_exec = coach_future.result()
                architect_exec = architect_future.result()

        tools = parse_architect_tool_specs(architect_exec.content)
        coach_playbook, coach_lessons, coach_hints = parse_coach_sections(coach_exec.content)
        return AgentOutputs(
            strategy=strategy,
            analysis_markdown=analyst_exec.content,
            coach_markdown=coach_exec.content,
            coach_playbook=coach_playbook,
            coach_lessons=coach_lessons,
            coach_competitor_hints=coach_hints,
            architect_markdown=architect_exec.content,
            architect_tools=tools,
            role_executions=[competitor_exec, translator_exec, analyst_exec, coach_exec, architect_exec],
        )

    def _enrich_coach_prompt(self, base_prompt: str, analyst_content: str) -> str:
        return base_prompt + f"\n\n--- Analyst findings (this generation) ---\n{analyst_content}\n"

    def _run_rlm_roles(
        self,
        run_id: str,
        scenario_name: str,
        generation_index: int,
        strategy: dict[str, Any],
        architect_prompt: str,
    ) -> tuple[RoleExecution, RoleExecution]:
        """Run Analyst and Architect via RLM sessions."""
        from mts.rlm.prompts import ANALYST_RLM_SYSTEM, ARCHITECT_RLM_SYSTEM
        from mts.rlm.repl_worker import ReplWorker
        from mts.rlm.session import RlmSession, make_llm_batch

        assert self._rlm_loader is not None
        settings = self.settings

        # Reset deterministic client turn counter if applicable
        if hasattr(self.client, "reset_rlm_turns"):
            self.client.reset_rlm_turns()

        # --- Analyst ---
        analyst_ctx = self._rlm_loader.load_for_analyst(
            run_id, scenario_name, generation_index,
            current_strategy=strategy,
        )
        analyst_ns = dict(analyst_ctx.variables)
        analyst_ns["llm_batch"] = make_llm_batch(self.client, settings.rlm_sub_model)
        analyst_worker = ReplWorker(
            namespace=analyst_ns,
            max_stdout_chars=settings.rlm_max_stdout_chars,
            timeout_seconds=settings.rlm_code_timeout_seconds,
        )
        analyst_system = ANALYST_RLM_SYSTEM.format(
            max_stdout_chars=settings.rlm_max_stdout_chars,
            max_turns=settings.rlm_max_turns,
            variable_summary=analyst_ctx.summary,
        )
        analyst_session = RlmSession(
            client=self.client,
            worker=analyst_worker,
            role="analyst",
            model=settings.model_analyst,
            system_prompt=analyst_system,
            max_turns=settings.rlm_max_turns,
        )
        analyst_exec = analyst_session.run()

        # Reset turn counter between roles for deterministic client
        if hasattr(self.client, "reset_rlm_turns"):
            self.client.reset_rlm_turns()

        # --- Architect ---
        architect_ctx = self._rlm_loader.load_for_architect(
            run_id, scenario_name, generation_index,
        )
        architect_ns = dict(architect_ctx.variables)
        architect_ns["llm_batch"] = make_llm_batch(self.client, settings.rlm_sub_model)
        architect_worker = ReplWorker(
            namespace=architect_ns,
            max_stdout_chars=settings.rlm_max_stdout_chars,
            timeout_seconds=settings.rlm_code_timeout_seconds,
        )
        architect_system = ARCHITECT_RLM_SYSTEM.format(
            max_stdout_chars=settings.rlm_max_stdout_chars,
            max_turns=settings.rlm_max_turns,
            variable_summary=architect_ctx.summary,
        )
        architect_session = RlmSession(
            client=self.client,
            worker=architect_worker,
            role="architect",
            model=settings.model_architect,
            system_prompt=architect_system,
            max_turns=settings.rlm_max_turns,
        )
        architect_exec = architect_session.run()

        return analyst_exec, architect_exec
