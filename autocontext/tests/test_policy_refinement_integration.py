"""Tests for AC-156: PolicyRefinementLoop integration into the generation pipeline."""

from __future__ import annotations

import json
import textwrap
from unittest.mock import MagicMock

import pytest

from autocontext.config.settings import AppSettings
from autocontext.execution.policy_refinement import PolicyRefinementResult
from autocontext.scenarios.grid_ctf import GridCtfScenario

# ── Helpers ──────────────────────────────────────────────────────────────────

_GOOD_GRID_CTF_POLICY = textwrap.dedent("""\
    def choose_action(state):
        return {"aggression": 0.7, "defense": 0.5, "path_bias": 0.8}
""")


def _make_settings(**overrides: object) -> AppSettings:
    defaults = {
        "agent_provider": "deterministic",
        "code_strategies_enabled": True,
        "policy_refinement_enabled": True,
    }
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


# ── Settings ─────────────────────────────────────────────────────────────────


class TestPolicyRefinementSettings:
    def test_defaults_exist(self) -> None:
        s = AppSettings(agent_provider="deterministic")
        assert s.policy_refinement_enabled is False
        assert s.policy_refinement_max_iterations == 50
        assert s.policy_refinement_matches_per_iteration == 5
        assert s.policy_refinement_convergence_window == 5
        assert abs(s.policy_refinement_convergence_epsilon - 0.01) < 1e-9
        assert s.policy_refinement_model == ""
        assert abs(s.policy_refinement_timeout_per_match - 5.0) < 1e-9

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.config.settings import load_settings

        monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
        monkeypatch.setenv("AUTOCONTEXT_POLICY_REFINEMENT_ENABLED", "true")
        monkeypatch.setenv("AUTOCONTEXT_POLICY_REFINEMENT_MAX_ITERATIONS", "10")
        s = load_settings()
        assert s.policy_refinement_enabled is True
        assert s.policy_refinement_max_iterations == 10


# ── GenerationContext field ──────────────────────────────────────────────────


class TestGenerationContextField:
    def test_policy_refinement_result_default_none(self) -> None:
        from autocontext.loop.stage_types import GenerationContext

        ctx = GenerationContext(
            run_id="r1",
            scenario_name="grid_ctf",
            scenario=GridCtfScenario(),
            generation=1,
            settings=_make_settings(),
            previous_best=0.0,
            challenger_elo=1500.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
        )
        assert ctx.policy_refinement_result is None


# ── Stage skip conditions ────────────────────────────────────────────────────


class TestStageSkipConditions:
    def _make_ctx(self, **overrides: object):
        from autocontext.loop.stage_types import GenerationContext

        defaults = dict(
            run_id="r1",
            scenario_name="grid_ctf",
            scenario=GridCtfScenario(),
            generation=1,
            settings=_make_settings(),
            previous_best=0.0,
            challenger_elo=1500.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
            current_strategy={"__code__": _GOOD_GRID_CTF_POLICY},
        )
        defaults.update(overrides)
        return GenerationContext(**defaults)  # type: ignore[arg-type]

    def test_skips_when_disabled(self) -> None:
        from autocontext.loop.stages import stage_policy_refinement

        ctx = self._make_ctx(settings=_make_settings(policy_refinement_enabled=False))
        events = MagicMock()
        client = MagicMock()
        sqlite = MagicMock()
        result = stage_policy_refinement(ctx, client=client, model="model-a", events=events, sqlite=sqlite)
        assert result.policy_refinement_result is None
        events.emit.assert_not_called()

    def test_skips_when_not_code_strategy(self) -> None:
        from autocontext.loop.stages import stage_policy_refinement

        ctx = self._make_ctx(
            settings=_make_settings(code_strategies_enabled=False),
            current_strategy={"aggression": 0.5},
        )
        events = MagicMock()
        client = MagicMock()
        sqlite = MagicMock()
        result = stage_policy_refinement(ctx, client=client, model="model-a", events=events, sqlite=sqlite)
        assert result.policy_refinement_result is None
        events.emit.assert_not_called()

    def test_skips_for_agent_task_scenario(self) -> None:
        from autocontext.loop.stages import stage_policy_refinement

        # Agent tasks don't have execute_match
        mock_scenario = MagicMock(spec=[])
        ctx = self._make_ctx(scenario=mock_scenario)
        events = MagicMock()
        client = MagicMock()
        sqlite = MagicMock()
        result = stage_policy_refinement(ctx, client=client, model="model-a", events=events, sqlite=sqlite)
        assert result.policy_refinement_result is None
        events.emit.assert_not_called()


# ── Stage execution ──────────────────────────────────────────────────────────


class TestStageExecution:
    def _make_ctx(self, **overrides: object):
        from autocontext.loop.stage_types import GenerationContext

        defaults = dict(
            run_id="r1",
            scenario_name="grid_ctf",
            scenario=GridCtfScenario(),
            generation=1,
            settings=_make_settings(
                policy_refinement_max_iterations=2,
                policy_refinement_matches_per_iteration=2,
            ),
            previous_best=0.0,
            challenger_elo=1500.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
            current_strategy={"__code__": _GOOD_GRID_CTF_POLICY},
        )
        defaults.update(overrides)
        return GenerationContext(**defaults)  # type: ignore[arg-type]

    def _make_deterministic_client(self) -> MagicMock:
        """Create a mock LanguageModelClient that returns a good policy."""
        client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = _GOOD_GRID_CTF_POLICY
        client.generate.return_value = mock_response
        return client

    def test_refines_code_strategy(self) -> None:
        from autocontext.loop.stages import stage_policy_refinement

        ctx = self._make_ctx()
        events = MagicMock()
        client = self._make_deterministic_client()
        sqlite = MagicMock()

        result = stage_policy_refinement(ctx, client=client, model="resolved-model", events=events, sqlite=sqlite)

        assert result.policy_refinement_result is not None
        assert isinstance(result.policy_refinement_result, PolicyRefinementResult)
        assert result.policy_refinement_result.best_heuristic > 0.0
        assert "__code__" in result.current_strategy
        client.generate.assert_called()
        assert client.generate.call_args.kwargs["model"] == "resolved-model"
        sqlite.append_agent_output.assert_called_once_with(
            "r1",
            1,
            "competitor",
            json.dumps(result.current_strategy, sort_keys=True),
        )

    def test_emits_started_and_completed_events(self) -> None:
        from autocontext.loop.stages import stage_policy_refinement

        ctx = self._make_ctx()
        events = MagicMock()
        client = self._make_deterministic_client()
        sqlite = MagicMock()

        stage_policy_refinement(ctx, client=client, model="resolved-model", events=events, sqlite=sqlite)

        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "policy_refinement_started" in event_names
        assert "policy_refinement_completed" in event_names

    def test_fallback_on_error(self) -> None:
        from autocontext.loop.stages import stage_policy_refinement

        original_code = _GOOD_GRID_CTF_POLICY
        ctx = self._make_ctx(current_strategy={"__code__": original_code})
        events = MagicMock()
        # Client that raises on generate
        client = MagicMock()
        client.generate.side_effect = RuntimeError("LLM down")
        sqlite = MagicMock()

        # Should not raise — fallback to original
        result = stage_policy_refinement(ctx, client=client, model="resolved-model", events=events, sqlite=sqlite)

        assert result.current_strategy["__code__"] == original_code
        assert result.policy_refinement_result is None
        sqlite.append_agent_output.assert_not_called()
        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "policy_refinement_failed" in event_names


# ── _ClientAsProvider bridge ─────────────────────────────────────────────────


class TestClientAsProviderBridge:
    def test_delegates_to_client(self) -> None:
        from autocontext.loop.stages import _ClientAsProvider
        from autocontext.providers.base import CompletionResult

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "generated text"
        mock_client.generate.return_value = mock_response

        provider = _ClientAsProvider(mock_client, model="test-model")
        result = provider.complete("system prompt", "user prompt")

        assert isinstance(result, CompletionResult)
        assert result.text == "generated text"
        mock_client.generate.assert_called_once()

    def test_default_model(self) -> None:
        from autocontext.loop.stages import _ClientAsProvider

        mock_client = MagicMock()
        provider = _ClientAsProvider(mock_client, model="my-model")
        assert provider.default_model() == "my-model"
