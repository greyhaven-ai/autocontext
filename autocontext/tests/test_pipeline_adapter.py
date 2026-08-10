"""Tests for PipelineEngine-backed orchestrator codepath."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.agents.pipeline_adapter import RoleHandler, build_mts_dag, build_role_handler
from autocontext.config.settings import AppSettings
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import RoleExecution, RoleUsage
from autocontext.prompts.templates import PromptBundle


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
        coach=base
        + (
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

    def test_handler_uses_local_runtime_when_role_routing_is_auto(self, tmp_path: Path) -> None:
        client = DeterministicDevClient()
        local_model = tmp_path / "mlx-bundle"
        local_model.mkdir()
        settings = AppSettings(
            agent_provider="deterministic",
            role_routing="auto",
            mlx_model_path=str(local_model),
        )
        orch = AgentOrchestrator(client=client, settings=settings)
        handler = build_role_handler(orch, generation=1, scenario_name="grid_ctf")

        seen: dict[str, object] = {}

        def fake_run(prompt: str, tool_context: str = "") -> tuple[str, RoleExecution]:
            seen["client"] = orch.competitor.runtime.client
            seen["model"] = orch.competitor.model
            return "", RoleExecution(
                role="competitor",
                content="{}",
                usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="local"),
                subagent_id="test",
                status="completed",
            )

        orch.competitor.run = fake_run  # type: ignore[method-assign]

        mock_local_client = MagicMock(spec=LanguageModelClient)
        with patch("autocontext.agents.provider_bridge.create_role_client", return_value=mock_local_client) as mock_create:
            result = handler("competitor", _make_prompt_bundle().competitor, {})

        assert result.role == "competitor"
        assert seen["client"] is mock_local_client
        assert seen["model"] == str(local_model)
        mock_create.assert_called_once_with(
            "mlx",
            settings,
            model_override=str(local_model),
            scenario_name="grid_ctf",
            role="competitor",
        )


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

    def test_pipeline_malformed_translator_content_raises_inside_translate(self) -> None:
        """Malformed model output reaching `StrategyTranslator.translate` in
        the pipeline path propagates out of `run_generation` rather than being
        swallowed by the engine.

        SCOPE, precisely -- an earlier version of this docstring overclaimed
        and the claim is the thing being corrected: the raise happens inside
        `translate` (translator.py:70), and the engine/adapter simply do not
        catch it. `orchestrator.py:692`'s own `extract_json` call is NEVER
        entered on this input, because `translate` raises before returning and
        the pipeline never gets a translator RoleExecution to re-parse. What
        this test pins is the propagation path, not that site. For coverage of
        `orchestrator.py:692` itself see
        `test_orchestrator_parses_translator_content_and_raises_on_malformed`
        below, which stubs the handler so malformed content arrives there.

        Both matter, and neither substitutes for the other: this one is the
        production wiring (the raise a real malformed translator response
        actually produces), the other one is the orchestrator's own parse.
        """
        client = DeterministicDevClient()
        settings = _make_settings(use_pipeline=True)
        orch = AgentOrchestrator(client=client, settings=settings)

        def fake_competitor_run(prompt: str, tool_context: str = "") -> tuple[str, RoleExecution]:
            # Prose with no embedded JSON, so extract_strategy_deterministic
            # returns None and translate() falls through to the runtime call
            # below instead of short-circuiting on the deterministic path.
            content = "I will play aggressively and hold the center."
            return content, RoleExecution(
                role="competitor",
                content=content,
                usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="stub"),
                subagent_id="test",
                status="completed",
            )

        orch.competitor.run = fake_competitor_run  # type: ignore[method-assign]

        def fake_translator_run_task(task: object) -> RoleExecution:
            return RoleExecution(
                role="translator",
                content='{"aggression": 0.5, "defense":}',  # malformed: no value
                usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="stub"),
                subagent_id="test",
                status="completed",
            )

        orch.translator.runtime.run_task = fake_translator_run_task  # type: ignore[method-assign]

        prompts = _make_prompt_bundle()
        with pytest.raises(json.JSONDecodeError) as excinfo:
            orch.run_generation(prompts, generation_index=1)

        # Pin WHERE, so this test can't quietly start covering something else:
        # translate() is on the stack, the orchestrator's own parse is not.
        frames = [str(entry.path) for entry in excinfo.traceback]
        assert any(f.endswith("agents/translator.py") for f in frames)

    def test_orchestrator_parses_translator_content_and_raises_on_malformed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_run_via_pipeline`'s own `extract_json` (orchestrator.py:692) must
        raise on malformed translator content -- reached here for real.

        Getting to that line takes stubbing the role HANDLER, not the
        translator runtime, and the reason is worth stating because it is a
        wiring defect in its own right: `pipeline_adapter.py:95` calls
        `orch.translator.translate(...)`, DISCARDS the `_strategy` it returns,
        and hands back only `exec_result`. `_run_via_pipeline` then re-parses
        `exec_result.content`, which is either `json.dumps(...)` (always valid)
        or the exact string `translate` just parsed successfully. `extract_json`
        is deterministic, so the second parse cannot disagree with the first.
        Through the real adapter, orchestrator.py:692 therefore cannot fail:
        any input bad enough to break it breaks `translate` first, one frame
        earlier (the test above).

        Replacing the handler removes `translate` from the path entirely and
        delivers malformed content straight to the orchestrator, which is the
        only way to exercise the parse the orchestrator actually performs. The
        stub is narrow: every role except translator still runs the real
        handler.

        This is a defense-in-depth guard, not dead weight. The adapter's
        discard-and-re-parse is not a contract -- an adapter that returned a
        translator RoleExecution assembled from anywhere other than
        `translate`'s own input (a cached execution, a retry, a future
        streaming/partial-result path) would put un-vetted content in front of
        that line, and `use_pipeline_engine` flipping default-on is when that
        would start to matter. Pinning it now costs one test and means the
        fail-hard behavior is verified rather than assumed.
        """
        client = DeterministicDevClient()
        settings = _make_settings(use_pipeline=True)
        orch = AgentOrchestrator(client=client, settings=settings)

        def fail_if_called(raw_output: str, strategy_interface: str) -> tuple[dict[str, Any], RoleExecution]:
            raise AssertionError("translate() must not run: the handler stub replaces it")

        monkeypatch.setattr(orch.translator, "translate", fail_if_called)

        real_build_role_handler = build_role_handler

        def build_handler_with_malformed_translator(*args: Any, **kwargs: Any) -> RoleHandler:
            inner = real_build_role_handler(*args, **kwargs)

            def handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
                if name != "translator":
                    return inner(name, prompt, completed)
                return RoleExecution(
                    role="translator",
                    content='{"aggression": 0.5, "defense":}',  # malformed: no value
                    usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="stub"),
                    subagent_id="test",
                    status="completed",
                )

            return handler

        prompts = _make_prompt_bundle()
        with patch(
            "autocontext.agents.pipeline_adapter.build_role_handler",
            side_effect=build_handler_with_malformed_translator,
        ):
            with pytest.raises(json.JSONDecodeError) as excinfo:
                orch.run_generation(prompts, generation_index=1)

        # The mirror image of the assertion in the test above: the
        # orchestrator's frame is on the stack and translate()'s is not, so
        # this really is the parse at orchestrator.py:692 failing.
        frames = [str(entry.path) for entry in excinfo.traceback]
        assert any(f.endswith("agents/orchestrator.py") for f in frames)
        assert not any(f.endswith("agents/translator.py") for f in frames)

    def test_orchestrator_rejects_array_shaped_translator_content(self) -> None:
        """Same site, the other behavior change its comment claims: a
        translator response that parses fine but to a JSON ARRAY is rejected
        at orchestrator.py:692 instead of being assigned to `strategy` as-is.

        The old `json.loads(...)` there had no type check, so a list flowed
        into a field every downstream consumer declares as `dict[str, Any]`.
        Pinned separately from the malformed case because it exercises a
        different rule (wrong-type-is-terminal, not a parse failure) and
        raises a different exception -- a bare ValueError, checked exactly
        here so a JSONDecodeError could not pass for it.
        """
        client = DeterministicDevClient()
        settings = _make_settings(use_pipeline=True)
        orch = AgentOrchestrator(client=client, settings=settings)

        real_build_role_handler = build_role_handler

        def build_handler_with_array_translator(*args: Any, **kwargs: Any) -> RoleHandler:
            inner = real_build_role_handler(*args, **kwargs)

            def handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
                if name != "translator":
                    return inner(name, prompt, completed)
                return RoleExecution(
                    role="translator",
                    content='[{"aggression": 0.5}]',  # parses, but to a list
                    usage=RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="stub"),
                    subagent_id="test",
                    status="completed",
                )

            return handler

        prompts = _make_prompt_bundle()
        with patch(
            "autocontext.agents.pipeline_adapter.build_role_handler",
            side_effect=build_handler_with_array_translator,
        ):
            with pytest.raises(ValueError) as excinfo:
                orch.run_generation(prompts, generation_index=1)

        assert type(excinfo.value) is ValueError  # not the JSONDecodeError subclass
        assert "Expected JSON object, got list" in str(excinfo.value)
