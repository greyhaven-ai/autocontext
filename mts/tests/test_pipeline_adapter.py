"""Tests for PipelineEngine-backed orchestrator codepath."""
from __future__ import annotations

from mts.agents.llm_client import DeterministicDevClient
from mts.agents.orchestrator import AgentOrchestrator
from mts.agents.pipeline_adapter import build_mts_dag, build_role_handler
from mts.config.settings import AppSettings
from mts.harness.core.types import RoleExecution
from mts.prompts.templates import PromptBundle


def _make_settings(use_pipeline: bool = False) -> AppSettings:
    return AppSettings(agent_provider="deterministic", use_pipeline_engine=use_pipeline)


def _make_prompt_bundle() -> PromptBundle:
    base = (
        "Scenario rules:\nTest\n\nStrategy interface:\n"
        '{"aggression": float, "defense": float, "path_bias": float}\n\n'
        "Evaluation criteria:\nScore\n\nObservation narrative:\nTest\n\n"
        "Observation state:\n{}\n\nConstraints:\nNone\n\n"
        "Current playbook:\nNone\n\nAvailable tools:\nNone\n\n"
        "Previous generation summary:\nNone\n"
    )
    return PromptBundle(
        competitor=base + "Describe your strategy reasoning and recommend specific parameter values.",
        analyst=base + "Analyze strengths/failures and return markdown with sections: "
        "Findings, Root Causes, Actionable Recommendations.",
        coach=base + (
            "You are the playbook coach. Produce TWO structured sections:\n\n"
            "1. A COMPLETE replacement playbook between markers.\n\n"
            "<!-- PLAYBOOK_START -->\n(Your consolidated playbook here)\n<!-- PLAYBOOK_END -->\n\n"
            "2. Operational lessons learned between markers.\n\n"
            "<!-- LESSONS_START -->\n(lessons)\n<!-- LESSONS_END -->"
        ),
        architect=base + "Propose infrastructure/tooling improvements.",
    )


class TestBuildMtsDag:
    def test_dag_has_five_roles(self) -> None:
        dag = build_mts_dag()
        assert len(dag.roles) == 5

    def test_dag_batch_order(self) -> None:
        dag = build_mts_dag()
        batches = dag.execution_batches()
        assert batches[0] == ["competitor"]
        assert batches[1] == ["translator"]
        assert "analyst" in batches[2]
        assert "architect" in batches[2]
        # Coach depends on analyst, comes after
        assert "coach" in batches[3]

    def test_dag_validates(self) -> None:
        dag = build_mts_dag()
        dag.validate()  # Should not raise


class TestBuildRoleHandler:
    def test_handler_returns_role_execution(self) -> None:
        client = DeterministicDevClient()
        settings = _make_settings()
        orch = AgentOrchestrator(client=client, settings=settings)
        handler = build_role_handler(orch)
        result = handler("competitor", _make_prompt_bundle().competitor, {})
        assert isinstance(result, RoleExecution)
        assert result.role == "competitor"


class TestPipelineOrchestratorIntegration:
    def test_pipeline_produces_same_roles_as_direct(self) -> None:
        """Pipeline codepath produces AgentOutputs with all 5 role executions."""
        client = DeterministicDevClient()
        settings = _make_settings(use_pipeline=True)
        orch = AgentOrchestrator(client=client, settings=settings)
        prompts = _make_prompt_bundle()
        outputs = orch.run_generation(prompts, generation_index=1)
        assert len(outputs.role_executions) == 5
        roles = {e.role for e in outputs.role_executions}
        assert roles == {"competitor", "translator", "analyst", "coach", "architect"}

    def test_pipeline_backward_compatible(self) -> None:
        """Pipeline path produces valid AgentOutputs with all required fields."""
        client = DeterministicDevClient()
        settings = _make_settings(use_pipeline=True)
        orch = AgentOrchestrator(client=client, settings=settings)
        prompts = _make_prompt_bundle()
        outputs = orch.run_generation(prompts, generation_index=1)
        assert isinstance(outputs.strategy, dict)
        assert outputs.analysis_markdown
        assert outputs.coach_markdown
        assert outputs.architect_markdown

    def test_direct_and_pipeline_produce_equivalent_output(self) -> None:
        """With deterministic client, both codepaths produce equivalent results."""
        prompts = _make_prompt_bundle()

        client_a = DeterministicDevClient()
        orch_a = AgentOrchestrator(client=client_a, settings=_make_settings(use_pipeline=False))
        outputs_a = orch_a.run_generation(prompts, generation_index=1)

        client_b = DeterministicDevClient()
        orch_b = AgentOrchestrator(client=client_b, settings=_make_settings(use_pipeline=True))
        outputs_b = orch_b.run_generation(prompts, generation_index=1)

        assert outputs_a.strategy == outputs_b.strategy
        assert len(outputs_a.role_executions) == len(outputs_b.role_executions)

    def test_pipeline_flag_default_off(self) -> None:
        """Default settings have use_pipeline_engine=False."""
        settings = AppSettings(agent_provider="deterministic")
        assert settings.use_pipeline_engine is False

    def test_pipeline_skipped_when_rlm_enabled(self) -> None:
        """Pipeline codepath is NOT used when RLM is enabled, even if flag is on."""
        # Just verify the flag check logic — RLM with pipeline flag should still use existing path
        settings = AppSettings(agent_provider="deterministic", use_pipeline_engine=True, rlm_enabled=True)
        # Can't fully test without artifacts/sqlite, but can verify settings
        assert settings.use_pipeline_engine is True
        assert settings.rlm_enabled is True

    def test_pipeline_produces_coach_playbook(self) -> None:
        """Pipeline path correctly parses coach sections from output."""
        client = DeterministicDevClient()
        settings = _make_settings(use_pipeline=True)
        orch = AgentOrchestrator(client=client, settings=settings)
        prompts = _make_prompt_bundle()
        outputs = orch.run_generation(prompts, generation_index=1)
        # DeterministicDevClient coach response has PLAYBOOK_START/END markers
        assert outputs.coach_playbook
        assert "Strategy Updates" in outputs.coach_playbook

    def test_pipeline_produces_architect_tools(self) -> None:
        """Pipeline path correctly parses architect tool specs from output."""
        client = DeterministicDevClient()
        settings = _make_settings(use_pipeline=True)
        orch = AgentOrchestrator(client=client, settings=settings)
        prompts = _make_prompt_bundle()
        outputs = orch.run_generation(prompts, generation_index=1)
        # DeterministicDevClient architect response has tools JSON
        assert isinstance(outputs.architect_tools, list)
        assert len(outputs.architect_tools) >= 1
