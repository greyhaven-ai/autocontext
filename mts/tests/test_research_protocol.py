"""Tests for AR-3 Research Protocol feature."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mts.config.settings import AppSettings, load_settings
from mts.scenarios.base import Observation
from mts.storage.artifacts import ArtifactStore

# ── TestProtocolSettings ───────────────────────────────────────────────


class TestProtocolSettings:
    def test_protocol_enabled_defaults_false(self) -> None:
        settings = AppSettings()
        assert settings.protocol_enabled is False

    def test_load_settings_reads_protocol_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MTS_PROTOCOL_ENABLED", "true")
        settings = load_settings()
        assert settings.protocol_enabled is True


# ── TestResearchProtocol ───────────────────────────────────────────────


class TestResearchProtocol:
    def test_default_protocol(self) -> None:
        from mts.knowledge.protocol import default_protocol

        p = default_protocol()
        assert p.exploration_mode == "linear"
        assert p.current_focus == ""
        assert p.constraints == []
        assert p.tuning_overrides == {}

    def test_protocol_to_markdown(self) -> None:
        from mts.knowledge.protocol import ResearchProtocol

        p = ResearchProtocol(
            exploration_mode="rapid",
            current_focus="Optimize defense parameters",
            constraints=["Do not exceed aggression 0.9", "Keep defense above 0.3"],
            tuning_overrides={"backpressure_min_delta": 0.01, "matches_per_generation": 5},
        )
        md = p.to_markdown()
        assert "## Exploration Mode" in md
        assert "rapid" in md
        assert "## Current Focus" in md
        assert "Optimize defense parameters" in md
        assert "## Constraints" in md
        assert "- Do not exceed aggression 0.9" in md
        assert "- Keep defense above 0.3" in md
        assert "## Tuning Overrides" in md
        assert '"backpressure_min_delta": 0.01' in md
        assert '"matches_per_generation": 5' in md

    def test_protocol_roundtrip(self) -> None:
        from mts.knowledge.protocol import ResearchProtocol, parse_research_protocol

        original = ResearchProtocol(
            exploration_mode="tree",
            current_focus="Explore flanking strategies",
            constraints=["Avoid brute force", "Limit resource usage"],
            tuning_overrides={"rlm_max_turns": 10},
        )
        md = original.to_markdown()
        restored = parse_research_protocol(md)
        assert restored.exploration_mode == original.exploration_mode
        assert restored.current_focus == original.current_focus
        assert restored.constraints == original.constraints
        assert restored.tuning_overrides == original.tuning_overrides


# ── TestParseProtocol ──────────────────────────────────────────────────


class TestParseProtocol:
    def test_parse_exploration_mode(self) -> None:
        from mts.knowledge.protocol import parse_research_protocol

        md = "## Exploration Mode\nrapid\n\n## Current Focus\n(none)\n"
        p = parse_research_protocol(md)
        assert p.exploration_mode == "rapid"

    def test_parse_constraints(self) -> None:
        from mts.knowledge.protocol import parse_research_protocol

        md = (
            "## Exploration Mode\nlinear\n\n"
            "## Current Focus\n(none)\n\n"
            "## Constraints\n"
            "- No high aggression\n"
            "- Keep defense balanced\n"
            "- Avoid risky openings\n\n"
            "## Tuning Overrides\n(none)\n"
        )
        p = parse_research_protocol(md)
        assert len(p.constraints) == 3
        assert "No high aggression" in p.constraints
        assert "Keep defense balanced" in p.constraints
        assert "Avoid risky openings" in p.constraints

    def test_parse_tuning_overrides(self) -> None:
        from mts.knowledge.protocol import parse_research_protocol

        overrides = {"backpressure_min_delta": 0.05, "matches_per_generation": 7}
        md = (
            "## Exploration Mode\nlinear\n\n"
            "## Current Focus\n(none)\n\n"
            "## Constraints\n(none)\n\n"
            "## Tuning Overrides\n"
            "```json\n"
            f"{json.dumps(overrides, indent=2)}\n"
            "```\n"
        )
        p = parse_research_protocol(md)
        assert p.tuning_overrides["backpressure_min_delta"] == pytest.approx(0.05)
        assert p.tuning_overrides["matches_per_generation"] == 7

    def test_parse_empty_protocol(self) -> None:
        from mts.knowledge.protocol import parse_research_protocol

        p = parse_research_protocol("")
        assert p.exploration_mode == "linear"
        assert p.current_focus == ""
        assert p.constraints == []
        assert p.tuning_overrides == {}


# ── TestValidateTuningOverrides ────────────────────────────────────────


class TestValidateTuningOverrides:
    def test_valid_overrides_accepted(self) -> None:
        from mts.knowledge.protocol import validate_tuning_overrides

        raw: dict[str, object] = {
            "backpressure_min_delta": 0.5,
            "matches_per_generation": 10,
            "rlm_max_turns": 25,
            "probe_matches": 3,
        }
        result = validate_tuning_overrides(raw)
        assert result["backpressure_min_delta"] == pytest.approx(0.5)
        assert result["matches_per_generation"] == 10
        assert result["rlm_max_turns"] == 25
        assert result["probe_matches"] == 3

    def test_unknown_keys_filtered(self) -> None:
        from mts.knowledge.protocol import validate_tuning_overrides

        raw: dict[str, object] = {
            "backpressure_min_delta": 0.5,
            "unknown_key": 42,
            "another_bad_key": "hello",
        }
        result = validate_tuning_overrides(raw)
        assert "backpressure_min_delta" in result
        assert "unknown_key" not in result
        assert "another_bad_key" not in result

    def test_out_of_range_filtered(self) -> None:
        from mts.knowledge.protocol import validate_tuning_overrides

        raw: dict[str, object] = {
            "backpressure_min_delta": 1.5,  # max is 1.0
            "matches_per_generation": 0,  # min is 1
            "rlm_max_turns": 100,  # max is 50
            "probe_matches": -1,  # min is 0
        }
        result = validate_tuning_overrides(raw)
        assert result == {}


# ── TestArchitectProtocolParsing ───────────────────────────────────────


class TestArchitectProtocolParsing:
    def test_parse_protocol_from_architect_output(self) -> None:
        from mts.knowledge.protocol import parse_protocol_from_architect

        output = (
            "Some architect commentary here.\n\n"
            "<!-- PROTOCOL_START -->\n"
            "## Exploration Mode\nrapid\n\n"
            "## Current Focus\nOptimize flanking\n\n"
            "## Constraints\n- Stay defensive\n\n"
            "## Tuning Overrides\n(none)\n"
            "<!-- PROTOCOL_END -->\n\n"
            "More commentary."
        )
        protocol = parse_protocol_from_architect(output)
        assert protocol is not None
        assert protocol.exploration_mode == "rapid"
        assert protocol.current_focus == "Optimize flanking"
        assert protocol.constraints == ["Stay defensive"]

    def test_parse_protocol_from_architect_no_markers(self) -> None:
        from mts.knowledge.protocol import parse_protocol_from_architect

        output = "Just regular architect output with no protocol markers."
        result = parse_protocol_from_architect(output)
        assert result is None


# ── TestArtifactStoreProtocol ──────────────────────────────────────────


class TestArtifactStoreProtocol:
    def _make_store(self, tmp_path: Path) -> ArtifactStore:
        return ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

    def test_read_protocol_empty(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        assert store.read_research_protocol("test_scenario") == ""

    def test_write_read_protocol_roundtrip(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        content = "## Exploration Mode\nrapid\n\n## Current Focus\nTest focus\n"
        store.write_research_protocol("test_scenario", content)
        result = store.read_research_protocol("test_scenario")
        assert result == content


# ── TestPromptBundleProtocol ───────────────────────────────────────────


class TestPromptBundleProtocol:
    def _obs(self) -> Observation:
        return Observation(narrative="test", state={}, constraints=[])

    def test_prompt_bundle_includes_protocol(self) -> None:
        import mts.agents  # noqa: F401
        from mts.prompts.templates import build_prompt_bundle

        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=self._obs(),
            current_playbook="playbook",
            available_tools="tools",
            research_protocol="## Current Focus\nTest focus",
        )
        assert "Research protocol" in bundle.competitor
        assert "Test focus" in bundle.competitor
        assert "Research protocol" in bundle.analyst
        assert "Research protocol" in bundle.architect

    def test_prompt_bundle_empty_protocol_omitted(self) -> None:
        import mts.agents  # noqa: F401
        from mts.prompts.templates import build_prompt_bundle

        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=self._obs(),
            current_playbook="playbook",
            available_tools="tools",
            research_protocol="",
        )
        assert "Research protocol" not in bundle.competitor
        assert "Research protocol" not in bundle.analyst
