"""Tests for probe-based strategy refinement (MTS-26)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from autocontext.loop.stage_probe import stage_probe


def _make_ctx(probe_matches: int = 0) -> MagicMock:
    ctx = MagicMock()
    ctx.settings.probe_matches = probe_matches
    ctx.settings.seed_base = 1000
    ctx.settings.code_strategies_enabled = False
    ctx.run_id = "run_1"
    ctx.generation = 1
    ctx.challenger_elo = 1000.0
    ctx.current_strategy = {"move": "up"}
    ctx.prompts.competitor = "compete"
    ctx.tool_context = ""
    ctx.strategy_interface = '{"move": "str"}'
    ctx.probe_refinement_applied = False
    return ctx


def test_probe_disabled_returns_unchanged() -> None:
    """When probe_matches=0, stage_probe is a no-op."""
    ctx = _make_ctx(probe_matches=0)
    result = stage_probe(ctx, agents=MagicMock(), events=MagicMock(), supervisor=MagicMock())
    assert result.probe_refinement_applied is False


def test_probe_runs_single_match_and_refines() -> None:
    """When probe_matches=1, runs 1 match and calls competitor for refinement."""
    ctx = _make_ctx(probe_matches=1)
    ctx.current_strategy = {"move": "up"}

    mock_agents = MagicMock()
    mock_agents.competitor.run.return_value = ('{"move": "down"}', MagicMock())
    mock_agents.translator.translate.return_value = ({"move": "down"}, MagicMock())

    mock_events = MagicMock()

    mock_eval_result = MagicMock()
    mock_eval_result.best_score = 0.3
    mock_exec_output = MagicMock()
    mock_exec_output.result.replay = {}
    mock_exec_output.result.score = 0.3
    mock_eval_result.results = [MagicMock(score=0.3, metadata={"execution_output": mock_exec_output})]

    ctx.scenario.replay_to_narrative.return_value = "narrative"
    ctx.scenario.validate_actions.return_value = (True, "")
    ctx.scenario.initial_state.return_value = {"seed": 1}

    with patch("autocontext.loop.stage_probe.EvaluationRunner") as mock_runner_cls:
        mock_runner_cls.return_value.run.return_value = mock_eval_result
        with patch("autocontext.loop.stage_probe.ScenarioEvaluator"):
            result = stage_probe(ctx, agents=mock_agents, events=mock_events, supervisor=MagicMock())

    assert result.probe_refinement_applied is True
    assert result.current_strategy == {"move": "down"}
    mock_events.emit.assert_any_call("probe_started", {"run_id": "run_1", "generation": 1, "probe_matches": 1})


def test_probe_keeps_original_on_failure() -> None:
    """If competitor refinement fails, keep original strategy."""
    ctx = _make_ctx(probe_matches=1)

    mock_agents = MagicMock()
    mock_agents.competitor.run.side_effect = RuntimeError("LLM error")

    mock_eval_result = MagicMock()
    mock_eval_result.best_score = 0.3
    mock_exec_output = MagicMock()
    mock_exec_output.result.replay = {}
    mock_eval_result.results = [MagicMock(score=0.3, metadata={"execution_output": mock_exec_output})]

    ctx.scenario.replay_to_narrative.return_value = "narrative"

    with patch("autocontext.loop.stage_probe.EvaluationRunner") as mock_runner_cls:
        mock_runner_cls.return_value.run.return_value = mock_eval_result
        with patch("autocontext.loop.stage_probe.ScenarioEvaluator"):
            result = stage_probe(ctx, agents=mock_agents, events=MagicMock(), supervisor=MagicMock())

    assert result.current_strategy == {"move": "up"}
    assert result.probe_refinement_applied is False
