"""Tests for probe integration in GenerationPipeline (MTS-26)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from mts.loop.generation_pipeline import GenerationPipeline


def test_pipeline_calls_probe_when_enabled() -> None:
    """Pipeline calls stage_probe between agent generation and tournament."""
    pipeline = GenerationPipeline(
        orchestrator=MagicMock(),
        supervisor=MagicMock(),
        gate=MagicMock(),
        artifacts=MagicMock(),
        sqlite=MagicMock(),
        trajectory_builder=MagicMock(),
        events=MagicMock(),
        curator=None,
    )

    mock_ctx = MagicMock()
    mock_ctx.generation = 2  # Skip startup verification
    mock_ctx.settings.probe_matches = 1
    mock_ctx.settings.coherence_check_enabled = False

    with (
        patch("mts.loop.generation_pipeline.stage_knowledge_setup", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_agent_generation", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_probe", return_value=mock_ctx) as mock_probe,
        patch("mts.loop.generation_pipeline.stage_tournament", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_stagnation_check", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_curator_gate", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_persistence", return_value=mock_ctx),
    ):
        pipeline.run_generation(mock_ctx)

    mock_probe.assert_called_once()


def test_pipeline_skips_probe_when_disabled() -> None:
    """Pipeline still calls stage_probe (it returns immediately when probe_matches=0)."""
    pipeline = GenerationPipeline(
        orchestrator=MagicMock(),
        supervisor=MagicMock(),
        gate=MagicMock(),
        artifacts=MagicMock(),
        sqlite=MagicMock(),
        trajectory_builder=MagicMock(),
        events=MagicMock(),
        curator=None,
    )

    mock_ctx = MagicMock()
    mock_ctx.generation = 2
    mock_ctx.settings.probe_matches = 0
    mock_ctx.settings.coherence_check_enabled = False

    with (
        patch("mts.loop.generation_pipeline.stage_knowledge_setup", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_agent_generation", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_probe", return_value=mock_ctx) as mock_probe,
        patch("mts.loop.generation_pipeline.stage_tournament", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_stagnation_check", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_curator_gate", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_persistence", return_value=mock_ctx),
    ):
        pipeline.run_generation(mock_ctx)

    # stage_probe is called but returns immediately (no-op when probe_matches=0)
    mock_probe.assert_called_once()
