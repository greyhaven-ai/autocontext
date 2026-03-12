"""Tests for StrategyTranslator agent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autocontext.agents.translator import StrategyTranslator
from autocontext.agents.types import RoleExecution, RoleUsage


def _make_runtime(response_text: str) -> MagicMock:
    """Create a mock SubagentRuntime that returns *response_text*."""
    runtime = MagicMock()
    runtime.run_task.return_value = RoleExecution(
        role="translator",
        content=response_text,
        usage=RoleUsage(input_tokens=50, output_tokens=20, latency_ms=100, model="test"),
        subagent_id="translator-abc123",
        status="completed",
    )
    return runtime


OTHELLO_INTERFACE = (
    "Return JSON object with `mobility_weight`, `corner_weight`, and `stability_weight` "
    "as floats in [0,1]."
)

GRID_CTF_INTERFACE = (
    "Return JSON object with `aggression`, `defense`, and `path_bias` as floats in [0,1]."
)


class TestStrategyTranslator:
    def test_translate_extracts_json_from_narrative(self) -> None:
        """Translator returns correct dict when LLM outputs valid JSON."""
        raw_json = '{"mobility_weight": 0.3, "corner_weight": 0.5, "stability_weight": 0.2}'
        runtime = _make_runtime(raw_json)
        translator = StrategyTranslator(runtime, model="test-model")

        result, execution = translator.translate(
            raw_output="I recommend high corner pressure with moderate mobility.",
            strategy_interface=OTHELLO_INTERFACE,
        )

        assert result == {"mobility_weight": 0.3, "corner_weight": 0.5, "stability_weight": 0.2}
        assert isinstance(execution, RoleExecution)

    def test_translate_maps_abbreviated_keys(self) -> None:
        """Translator maps abbreviated keys to canonical names via prompt instruction."""
        # The translator LLM is told to map keys — so its output should already be canonical.
        raw_json = '{"mobility_weight": 0.25, "corner_weight": 0.6, "stability_weight": 0.15}'
        runtime = _make_runtime(raw_json)
        translator = StrategyTranslator(runtime, model="test-model")

        result, _ = translator.translate(
            raw_output="mobility 0.25, corner 0.60, stability 0.15",
            strategy_interface=OTHELLO_INTERFACE,
        )

        assert "mobility_weight" in result
        assert result["mobility_weight"] == 0.25

    def test_translate_passthrough_valid_json(self) -> None:
        """When translator returns clean JSON, it passes through correctly."""
        raw_json = '{"aggression": 0.58, "defense": 0.57, "path_bias": 0.54}'
        runtime = _make_runtime(raw_json)
        translator = StrategyTranslator(runtime, model="test-model")

        result, _ = translator.translate(
            raw_output=raw_json,
            strategy_interface=GRID_CTF_INTERFACE,
        )

        assert result == {"aggression": 0.58, "defense": 0.57, "path_bias": 0.54}

    def test_translate_raises_on_unparseable(self) -> None:
        """Translator raises ValueError when LLM returns non-JSON."""
        runtime = _make_runtime("I cannot produce a strategy for this scenario.")
        translator = StrategyTranslator(runtime, model="test-model")

        with pytest.raises(ValueError):
            translator.translate(
                raw_output="some nonsense",
                strategy_interface=OTHELLO_INTERFACE,
            )

    def test_translate_tracks_usage(self) -> None:
        """RoleExecution has role='translator' and tracks usage."""
        raw_json = '{"mobility_weight": 0.3, "corner_weight": 0.5, "stability_weight": 0.2}'
        runtime = _make_runtime(raw_json)
        translator = StrategyTranslator(runtime, model="test-model")

        _, execution = translator.translate(
            raw_output="anything",
            strategy_interface=OTHELLO_INTERFACE,
        )

        assert execution.role == "translator"
        assert execution.usage.input_tokens == 50
        assert execution.usage.output_tokens == 20
        assert execution.status == "completed"

    def test_translate_prompt_contains_interface_and_output(self) -> None:
        """Verify the prompt sent to the LLM includes both the interface and raw output."""
        raw_json = '{"mobility_weight": 0.3, "corner_weight": 0.5, "stability_weight": 0.2}'
        runtime = _make_runtime(raw_json)
        translator = StrategyTranslator(runtime, model="test-model")

        translator.translate(
            raw_output="I think we should go aggressive",
            strategy_interface=OTHELLO_INTERFACE,
        )

        call_args = runtime.run_task.call_args[0][0]
        assert "I think we should go aggressive" in call_args.prompt
        assert "mobility_weight" in call_args.prompt
        assert call_args.role == "translator"
        assert call_args.temperature == 0.0
        assert call_args.max_tokens == 200

    def test_translate_strips_markdown_fences(self) -> None:
        """Translator strips markdown code fences wrapping JSON."""
        fenced = '```json\n{"mobility_weight": 0.4, "corner_weight": 0.35, "stability_weight": 0.25}\n```'
        runtime = _make_runtime(fenced)
        translator = StrategyTranslator(runtime, model="test-model")

        result, _ = translator.translate(
            raw_output="some narrative",
            strategy_interface=OTHELLO_INTERFACE,
        )

        assert result == {"mobility_weight": 0.4, "corner_weight": 0.35, "stability_weight": 0.25}

    def test_translate_strips_plain_fences(self) -> None:
        """Translator strips plain ``` fences (no language tag)."""
        fenced = '```\n{"aggression": 0.6, "defense": 0.5, "path_bias": 0.4}\n```'
        runtime = _make_runtime(fenced)
        translator = StrategyTranslator(runtime, model="test-model")

        result, _ = translator.translate(
            raw_output="some narrative",
            strategy_interface=GRID_CTF_INTERFACE,
        )

        assert result == {"aggression": 0.6, "defense": 0.5, "path_bias": 0.4}
