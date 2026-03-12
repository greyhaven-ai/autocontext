"""Tests for probe integration in GenerationPipeline (MTS-26)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from autocontext.loop.generation_pipeline import GenerationPipeline


def _configure_pipeline_settings(mock_ctx: MagicMock, *, probe_matches: int) -> None:
    mock_ctx.settings.probe_matches = probe_matches
    mock_ctx.settings.coherence_check_enabled = False
    mock_ctx.settings.generation_time_budget_seconds = 0
    mock_ctx.settings.harness_validators_enabled = False
    mock_ctx.settings.policy_refinement_enabled = False
    mock_ctx.settings.exploration_mode = "linear"


def _make_pipeline() -> GenerationPipeline:
    orchestrator = MagicMock()
    orchestrator.resolve_role_execution.return_value = (MagicMock(), "")
    return GenerationPipeline(
        orchestrator=orchestrator,
        supervisor=MagicMock(),
        gate=MagicMock(),
        artifacts=MagicMock(),
        sqlite=MagicMock(),
        trajectory_builder=MagicMock(),
        events=MagicMock(),
        curator=None,
    )


def test_pipeline_calls_probe_when_enabled() -> None:
    """Pipeline calls stage_probe between agent generation and tournament."""
    pipeline = _make_pipeline()

    mock_ctx = MagicMock()
    mock_ctx.generation = 2  # Skip startup verification
    _configure_pipeline_settings(mock_ctx, probe_matches=1)

    with (
        patch("autocontext.loop.generation_pipeline.stage_knowledge_setup", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_agent_generation", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_staged_validation", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_prevalidation", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_probe", return_value=mock_ctx) as mock_probe,
        patch("autocontext.loop.generation_pipeline.stage_policy_refinement", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_tournament", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_stagnation_check", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_curator_gate", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_persistence", return_value=mock_ctx),
    ):
        pipeline.run_generation(mock_ctx)

    mock_probe.assert_called_once()


def test_pipeline_skips_probe_when_disabled() -> None:
    """Pipeline still calls stage_probe (it returns immediately when probe_matches=0)."""
    pipeline = _make_pipeline()

    mock_ctx = MagicMock()
    mock_ctx.generation = 2
    _configure_pipeline_settings(mock_ctx, probe_matches=0)

    with (
        patch("autocontext.loop.generation_pipeline.stage_knowledge_setup", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_agent_generation", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_staged_validation", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_prevalidation", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_probe", return_value=mock_ctx) as mock_probe,
        patch("autocontext.loop.generation_pipeline.stage_policy_refinement", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_tournament", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_stagnation_check", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_curator_gate", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_persistence", return_value=mock_ctx),
    ):
        pipeline.run_generation(mock_ctx)

    # stage_probe is called but returns immediately (no-op when probe_matches=0)
    mock_probe.assert_called_once()


def test_pipeline_continues_after_staged_validation_retry_signal() -> None:
    """A staged-validation retry signal should not short-circuit the rest of the pipeline."""
    pipeline = _make_pipeline()

    mock_ctx = MagicMock()
    mock_ctx.generation = 2
    _configure_pipeline_settings(mock_ctx, probe_matches=1)
    mock_ctx.gate_decision = "retry"
    mock_ctx.staged_validation_results = [{"stage": "contract", "status": "failed"}]

    with (
        patch("autocontext.loop.generation_pipeline.stage_knowledge_setup", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_agent_generation", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_staged_validation", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_prevalidation", return_value=mock_ctx) as mock_prevalidation,
        patch("autocontext.loop.generation_pipeline.stage_probe", return_value=mock_ctx) as mock_probe,
        patch("autocontext.loop.generation_pipeline.stage_policy_refinement", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_tournament", return_value=mock_ctx) as mock_tournament,
        patch("autocontext.loop.generation_pipeline.stage_stagnation_check", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_curator_gate", return_value=mock_ctx),
        patch("autocontext.loop.generation_pipeline.stage_persistence", return_value=mock_ctx),
    ):
        pipeline.run_generation(mock_ctx)

    mock_prevalidation.assert_called_once()
    mock_probe.assert_called_once()
    mock_tournament.assert_called_once()
