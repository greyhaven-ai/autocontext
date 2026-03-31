"""Tests for Pydantic migration of analytics dataclasses (AC-489, AC-481).

Verifies that migrated types use Pydantic BaseModel with model_dump/model_validate
instead of manual to_dict/from_dict.
"""

from __future__ import annotations

import json

from autocontext.analytics.calibration import (
    CalibrationOutcome,
    CalibrationRound,
    CalibrationSample,
)


class TestCalibrationSamplePydantic:
    def test_model_dump_roundtrip(self) -> None:
        sample = CalibrationSample(
            sample_id="s1",
            run_id="r1",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="anthropic",
            generation_index=3,
            risk_score=0.8,
            risk_reasons=["large_score_jump"],
            best_score=0.9,
            score_delta=0.2,
            playbook_mutation_size=5,
            created_at="2025-01-01T00:00:00Z",
        )
        data = sample.model_dump()
        assert isinstance(data, dict)
        assert data["sample_id"] == "s1"
        assert data["risk_reasons"] == ["large_score_jump"]

        restored = CalibrationSample.model_validate(data)
        assert restored.sample_id == sample.sample_id
        assert restored.risk_score == sample.risk_score

    def test_json_serialization(self) -> None:
        sample = CalibrationSample(
            sample_id="s2",
            run_id="r2",
            scenario="othello",
            scenario_family="game",
            agent_provider="openai",
            generation_index=1,
            risk_score=0.5,
            risk_reasons=[],
            best_score=0.6,
            score_delta=0.1,
            playbook_mutation_size=2,
            created_at="2025-01-01T00:00:00Z",
        )
        json_str = sample.model_dump_json()
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["sample_id"] == "s2"


class TestCalibrationOutcomePydantic:
    def test_model_dump(self) -> None:
        outcome = CalibrationOutcome(
            outcome_id="o1",
            sample_id="s1",
            decision="approve",
            reviewer="human",
            notes="looks good",
            rubric_quality="good",
            playbook_quality="good",
            recommended_action="none",
            created_at="2025-01-01T00:00:00Z",
        )
        data = outcome.model_dump()
        assert data["decision"] == "approve"
        restored = CalibrationOutcome.model_validate(data)
        assert restored == outcome


class TestCalibrationRoundPydantic:
    def test_nested_model_dump(self) -> None:
        rnd = CalibrationRound(
            round_id="rnd1",
            created_at="2025-01-01T00:00:00Z",
            samples=[
                CalibrationSample(
                    sample_id="s1", run_id="r1", scenario="grid_ctf",
                    scenario_family="game", agent_provider="anthropic",
                    generation_index=0, risk_score=0.5, risk_reasons=[],
                    best_score=0.5, score_delta=0.0, playbook_mutation_size=0,
                    created_at="2025-01-01T00:00:00Z",
                ),
            ],
            outcomes=[],
            status="pending",
            summary="",
        )
        data = rnd.model_dump()
        assert len(data["samples"]) == 1
        assert data["samples"][0]["sample_id"] == "s1"

        restored = CalibrationRound.model_validate(data)
        assert len(restored.samples) == 1
        assert restored.samples[0].sample_id == "s1"

    def test_backward_compat_to_dict(self) -> None:
        """to_dict should still work as alias for model_dump."""
        rnd = CalibrationRound(
            round_id="rnd1", created_at="now", samples=[], outcomes=[],
            status="pending", summary="",
        )
        assert rnd.to_dict() == rnd.model_dump()
