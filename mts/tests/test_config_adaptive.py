from __future__ import annotations

import json
from pathlib import Path

import pytest

from mts.config.settings import AppSettings, load_settings
from mts.config.tuning_bounds import TUNING_PARAMS, architect_bounds, protocol_bounds
from mts.knowledge.tuning import (
    TUNING_BOUNDS,
    TuningConfig,
    compute_meta_parameter_stats,
    format_meta_stats,
    parse_tuning_proposal,
    validate_tuning_bounds,
)
from mts.storage.artifacts import ArtifactStore

# ---------------------------------------------------------------------------
# TestConfigAdaptiveSettings
# ---------------------------------------------------------------------------


class TestConfigAdaptiveSettings:
    def test_config_adaptive_enabled_defaults_false(self) -> None:
        settings = AppSettings()
        assert settings.config_adaptive_enabled is False

    def test_load_settings_reads_config_adaptive_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MTS_CONFIG_ADAPTIVE_ENABLED", "true")
        monkeypatch.setenv("MTS_AGENT_PROVIDER", "deterministic")
        settings = load_settings()
        assert settings.config_adaptive_enabled is True


class TestHarnessInheritanceSetting:
    def test_default_is_true(self) -> None:
        settings = AppSettings()
        assert settings.harness_inheritance_enabled is True

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MTS_HARNESS_INHERITANCE_ENABLED", "false")
        monkeypatch.setenv("MTS_AGENT_PROVIDER", "deterministic")
        settings = load_settings()
        assert settings.harness_inheritance_enabled is False

    def test_env_var_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MTS_HARNESS_INHERITANCE_ENABLED", "true")
        monkeypatch.setenv("MTS_AGENT_PROVIDER", "deterministic")
        settings = load_settings()
        assert settings.harness_inheritance_enabled is True


# ---------------------------------------------------------------------------
# TestTuningConfig
# ---------------------------------------------------------------------------


class TestTuningConfig:
    def test_tuning_config_to_json(self) -> None:
        cfg = TuningConfig(
            version=2,
            parameters={"matches_per_generation": 5},
            recommended_by="architect",
            reasoning="More matches improve signal quality.",
        )
        raw = cfg.to_json()
        data = json.loads(raw)
        assert data["version"] == 2
        assert data["parameters"]["matches_per_generation"] == 5
        assert data["recommended_by"] == "architect"
        assert "signal quality" in data["reasoning"]

    def test_tuning_config_from_json(self) -> None:
        raw = json.dumps({
            "version": 3,
            "parameters": {"backpressure_min_delta": 0.01, "rlm_max_turns": 10},
            "recommended_by": "coach",
            "reasoning": "Tighter delta improves gating.",
        })
        cfg = TuningConfig.from_json(raw)
        assert cfg.version == 3
        assert cfg.parameters["backpressure_min_delta"] == 0.01
        assert cfg.parameters["rlm_max_turns"] == 10
        assert cfg.recommended_by == "coach"

    def test_tuning_config_roundtrip(self) -> None:
        original = TuningConfig(
            version=4,
            parameters={"matches_per_generation": 7, "probe_matches": 2},
            recommended_by="test",
            reasoning="roundtrip check",
        )
        restored = TuningConfig.from_json(original.to_json())
        assert restored.version == original.version
        assert restored.parameters == original.parameters
        assert restored.recommended_by == original.recommended_by
        assert restored.reasoning == original.reasoning


# ---------------------------------------------------------------------------
# TestValidateTuningBounds
# ---------------------------------------------------------------------------


class TestValidateTuningBounds:
    def test_valid_params_accepted(self) -> None:
        raw = {
            "matches_per_generation": 5,
            "backpressure_min_delta": 0.02,
            "rlm_max_turns": 30,
        }
        result = validate_tuning_bounds(raw)
        assert result["matches_per_generation"] == 5
        assert result["backpressure_min_delta"] == 0.02
        assert result["rlm_max_turns"] == 30

    def test_unknown_keys_dropped(self) -> None:
        raw = {
            "matches_per_generation": 5,
            "unknown_param": 42,
            "another_bad": "hello",
        }
        result = validate_tuning_bounds(raw)
        assert "matches_per_generation" in result
        assert "unknown_param" not in result
        assert "another_bad" not in result

    def test_out_of_range_dropped(self) -> None:
        raw = {
            "matches_per_generation": 99,  # max is 10
            "backpressure_min_delta": -1.0,  # min is 0.0
            "rlm_max_turns": 10,  # valid
        }
        result = validate_tuning_bounds(raw)
        assert "matches_per_generation" not in result
        assert "backpressure_min_delta" not in result
        assert result["rlm_max_turns"] == 10


# ---------------------------------------------------------------------------
# TestComputeMetaStats
# ---------------------------------------------------------------------------


class TestComputeMetaStats:
    def test_compute_stats_with_data(self) -> None:
        trajectory = [
            {"gate_decision": "advance", "delta": 0.05},
            {"gate_decision": "retry", "delta": -0.01},
            {"gate_decision": "advance", "delta": 0.03},
            {"gate_decision": "retry", "delta": 0.0},
        ]
        stats = compute_meta_parameter_stats(trajectory)
        assert stats["retry_rate"] == pytest.approx(0.5)
        assert stats["avg_delta"] == pytest.approx(0.0175)
        assert stats["total_generations"] == 4.0

    def test_compute_stats_empty(self) -> None:
        stats = compute_meta_parameter_stats([])
        assert stats["retry_rate"] == 0.0
        assert stats["avg_delta"] == 0.0
        assert stats["total_generations"] == 0.0


# ---------------------------------------------------------------------------
# TestParseTuningProposal
# ---------------------------------------------------------------------------


class TestParseTuningProposal:
    def test_parse_proposal_from_architect(self) -> None:
        output = (
            "Here is my analysis of the run performance.\n\n"
            "<!-- TUNING_PROPOSAL_START -->\n"
            '{"matches_per_generation": 5, "rlm_max_turns": 15, "reasoning": "more signal"}\n'
            "<!-- TUNING_PROPOSAL_END -->\n\n"
            "End of output."
        )
        cfg = parse_tuning_proposal(output)
        assert cfg is not None
        assert cfg.parameters["matches_per_generation"] == 5
        assert cfg.parameters["rlm_max_turns"] == 15
        assert cfg.reasoning == "more signal"

    def test_parse_proposal_no_markers(self) -> None:
        output = "Just some regular architect output with no tuning proposal."
        result = parse_tuning_proposal(output)
        assert result is None


# ---------------------------------------------------------------------------
# TestArtifactStoreTuning
# ---------------------------------------------------------------------------


class TestArtifactStoreTuning:
    def test_read_tuning_empty(self, tmp_path: Path) -> None:
        store = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        result = store.read_tuning("grid_ctf")
        assert result == ""

    def test_write_read_tuning_roundtrip(self, tmp_path: Path) -> None:
        store = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        content = json.dumps({"version": 1, "parameters": {"matches_per_generation": 4}})
        store.write_tuning("grid_ctf", content)
        result = store.read_tuning("grid_ctf")
        assert result == content


# ---------------------------------------------------------------------------
# TestFormatMetaStats
# ---------------------------------------------------------------------------


class TestFormatMetaStats:
    def test_format_meta_stats(self) -> None:
        stats = {
            "retry_rate": 0.25,
            "avg_delta": 0.0123,
            "rlm_utilization": 0.0,
            "total_generations": 8.0,
        }
        output = format_meta_stats(stats)
        assert "## Meta-Parameter Analysis" in output
        assert "Retry rate: 25%" in output
        assert "Average gate delta: 0.0123" in output
        assert "RLM utilization: 0%" in output
        assert "last 8 gens" in output


# ---------------------------------------------------------------------------
# TestCanonicalTuningBounds
# ---------------------------------------------------------------------------


class TestCanonicalTuningBounds:
    """Verify both tiers derive from the single canonical source."""

    def test_tuning_bounds_matches_architect_bounds(self) -> None:
        assert TUNING_BOUNDS == architect_bounds()

    def test_protocol_and_architect_share_same_keys(self) -> None:
        assert set(architect_bounds().keys()) == set(protocol_bounds().keys())

    def test_architect_bounds_within_protocol_bounds(self) -> None:
        """Architect bounds should be equal or tighter than protocol bounds."""
        for key, param in TUNING_PARAMS.items():
            assert param.architect_min >= param.protocol_min, (
                f"{key}: architect_min ({param.architect_min}) < protocol_min ({param.protocol_min})"
            )
            assert param.architect_max <= param.protocol_max, (
                f"{key}: architect_max ({param.architect_max}) > protocol_max ({param.protocol_max})"
            )

    def test_all_tuning_params_have_valid_ranges(self) -> None:
        for key, param in TUNING_PARAMS.items():
            assert param.architect_min <= param.architect_max, f"{key}: architect min > max"
            assert param.protocol_min <= param.protocol_max, f"{key}: protocol min > max"

    def test_architect_every_n_gens_in_protocol_bounds(self) -> None:
        """architect_every_n_gens was missing from protocol — now present."""
        pb = protocol_bounds()
        assert "architect_every_n_gens" in pb
