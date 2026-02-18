"""Tests for GenerationContext and StageResult pipeline types."""

from __future__ import annotations

from unittest.mock import MagicMock

from mts.config.settings import AppSettings
from mts.loop.stage_types import GenerationContext, StageResult


def _make_context(**overrides: object) -> GenerationContext:
    """Build a GenerationContext with sensible defaults, overridable per-test."""
    defaults: dict[str, object] = {
        "run_id": "test_run_001",
        "scenario_name": "grid_ctf",
        "scenario": MagicMock(),
        "generation": 1,
        "settings": AppSettings(),
        "previous_best": 0.0,
        "challenger_elo": 1000.0,
        "score_history": [],
        "gate_decision_history": [],
        "coach_competitor_hints": "",
        "replay_narrative": "",
    }
    defaults.update(overrides)
    return GenerationContext(**defaults)  # type: ignore[arg-type]


# ---------- TestGenerationContext ----------


class TestGenerationContext:
    def test_construction_with_required_fields(self) -> None:
        """All required fields are set and accessible after construction."""
        scenario = MagicMock()
        settings = AppSettings()
        ctx = GenerationContext(
            run_id="run_42",
            scenario_name="othello",
            scenario=scenario,
            generation=3,
            settings=settings,
            previous_best=0.65,
            challenger_elo=1050.0,
            score_history=[0.5, 0.6],
            gate_decision_history=["advance"],
            coach_competitor_hints="try aggression=0.7",
            replay_narrative="Player captured flag at step 4",
        )
        assert ctx.run_id == "run_42"
        assert ctx.scenario_name == "othello"
        assert ctx.scenario is scenario
        assert ctx.generation == 3
        assert ctx.settings is settings
        assert ctx.previous_best == 0.65
        assert ctx.challenger_elo == 1050.0
        assert ctx.score_history == [0.5, 0.6]
        assert ctx.gate_decision_history == ["advance"]
        assert ctx.coach_competitor_hints == "try aggression=0.7"
        assert ctx.replay_narrative == "Player captured flag at step 4"

    def test_optional_fields_default_none(self) -> None:
        """Stage output fields default to None, empty string, zero, or empty containers."""
        ctx = _make_context()
        assert ctx.prompts is None
        assert ctx.outputs is None
        assert ctx.tournament is None
        assert ctx.gate_decision == ""
        assert ctx.gate_delta == 0.0
        assert ctx.current_strategy == {}
        assert ctx.created_tools == []
        assert ctx.strategy_interface == ""
        assert ctx.tool_context == ""

    def test_mutable_fields_independent(self) -> None:
        """Two independently constructed contexts do not share list or dict instances."""
        ctx_a = _make_context()
        ctx_b = _make_context()

        # Mutate ctx_a's mutable defaults
        ctx_a.current_strategy["aggression"] = 0.9
        ctx_a.created_tools.append("recon_tool.py")

        # ctx_b must remain unaffected
        assert ctx_b.current_strategy == {}
        assert ctx_b.created_tools == []

        # Also check the required-field lists passed via factory
        ctx_c = _make_context(score_history=[0.5])
        ctx_d = _make_context(score_history=[0.5])
        ctx_c.score_history.append(0.8)
        assert ctx_d.score_history == [0.5]


# ---------- TestStageResult ----------


class TestStageResult:
    def test_success_construction(self) -> None:
        """A successful StageResult has stage name, success=True, and no error."""
        result = StageResult(stage="prompt_assembly", success=True)
        assert result.stage == "prompt_assembly"
        assert result.success is True
        assert result.error is None

    def test_failure_with_error(self) -> None:
        """A failed StageResult carries an error message."""
        result = StageResult(stage="tournament", success=False, error="Timeout after 30s")
        assert result.stage == "tournament"
        assert result.success is False
        assert result.error == "Timeout after 30s"
