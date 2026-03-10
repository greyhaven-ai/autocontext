"""Tests for AR-1 Experiment Log Injection (MTS-97 through MTS-101).

Covers:
- MTS-97: experiment_log_enabled setting in AppSettings
- MTS-98: build_experiment_log() method on ScoreTrajectoryBuilder
- MTS-99: experiment_log parameter in build_prompt_bundle()
"""

from __future__ import annotations

from unittest.mock import MagicMock

from mts.config.settings import AppSettings, load_settings
from mts.knowledge.trajectory import ScoreTrajectoryBuilder
from mts.scenarios.base import Observation

# ── MTS-97: AppSettings.experiment_log_enabled ──────────────────────────


class TestExperimentLogSetting:
    def test_experiment_log_enabled_defaults_false(self) -> None:
        settings = AppSettings()
        assert settings.experiment_log_enabled is False

    def test_experiment_log_enabled_field_exists(self) -> None:
        """The field should be declared on AppSettings with correct type."""
        info = AppSettings.model_fields["experiment_log_enabled"]
        assert info.annotation is bool
        assert info.default is False

    def test_load_settings_reads_experiment_log_env(self, monkeypatch: object) -> None:
        """load_settings() should honour MTS_EXPERIMENT_LOG_ENABLED env var."""
        mp = monkeypatch  # type: ignore[assignment]
        mp.setenv("MTS_EXPERIMENT_LOG_ENABLED", "true")
        settings = load_settings()
        assert settings.experiment_log_enabled is True


# ── MTS-98: ScoreTrajectoryBuilder.build_experiment_log ─────────────────


def _make_builder(
    trajectory_rows: list[dict[str, object]] | None = None,
    strategy_rows: list[dict[str, object]] | None = None,
) -> ScoreTrajectoryBuilder:
    """Create a ScoreTrajectoryBuilder with a mocked SQLiteStore."""
    mock_sqlite = MagicMock()
    mock_sqlite.get_generation_trajectory.return_value = trajectory_rows or []
    mock_sqlite.get_strategy_score_history.return_value = strategy_rows or []
    return ScoreTrajectoryBuilder(sqlite=mock_sqlite)


class TestBuildExperimentLog:
    def test_build_experiment_log_empty_run(self) -> None:
        """Returns empty string when no trajectory data exists."""
        builder = _make_builder()
        result = builder.build_experiment_log("nonexistent_run")
        assert result == ""

    def test_build_experiment_log_with_data(self) -> None:
        """Produces a markdown table with the expected columns."""
        trajectory = [
            {
                "generation_index": 1,
                "mean_score": 0.5000,
                "best_score": 0.6000,
                "elo": 1010.0,
                "gate_decision": "advance",
                "delta": 0.1000,
            },
            {
                "generation_index": 2,
                "mean_score": 0.6500,
                "best_score": 0.7500,
                "elo": 1030.0,
                "gate_decision": "advance",
                "delta": 0.1500,
            },
        ]
        strategies = [
            {
                "generation_index": 1,
                "content": '{"aggression": 0.8, "defense": 0.3}',
                "best_score": 0.6000,
                "gate_decision": "advance",
            },
            {
                "generation_index": 2,
                "content": '{"aggression": 0.6, "defense": 0.5}',
                "best_score": 0.7500,
                "gate_decision": "advance",
            },
        ]
        builder = _make_builder(trajectory_rows=trajectory, strategy_rows=strategies)
        result = builder.build_experiment_log("run_1")

        # Header present
        assert "## Experiment Log" in result
        assert "| Gen | Strategy Summary | Score | Delta | Gate | Approach |" in result

        # Row data present
        assert "| 1 " in result
        assert "| 2 " in result
        assert "advance" in result
        assert "json" in result  # strategy starts with '{' -> json approach

    def test_build_experiment_log_strategy_truncation(self) -> None:
        """Strategy text longer than 80 chars is truncated with ellipsis."""
        long_strategy = '{"param_' + "x" * 200 + '": 1}'
        trajectory = [
            {
                "generation_index": 1,
                "mean_score": 0.5,
                "best_score": 0.6,
                "elo": 1000.0,
                "gate_decision": "advance",
                "delta": 0.1,
            },
        ]
        strategies = [
            {
                "generation_index": 1,
                "content": long_strategy,
                "best_score": 0.6,
                "gate_decision": "advance",
            },
        ]
        builder = _make_builder(trajectory_rows=trajectory, strategy_rows=strategies)
        result = builder.build_experiment_log("run_1")

        # The strategy text in the output should be truncated
        lines = result.split("\n")
        data_lines = [line for line in lines if line.startswith("| 1 ")]
        assert len(data_lines) == 1
        # Full long strategy should NOT appear
        assert long_strategy not in result
        # Truncated indicator
        assert "..." in result

    def test_build_experiment_log_approach_detection_json(self) -> None:
        """Strategy starting with '{' should be detected as 'json' approach."""
        trajectory = [
            {
                "generation_index": 1,
                "mean_score": 0.5,
                "best_score": 0.6,
                "elo": 1000.0,
                "gate_decision": "advance",
                "delta": 0.1,
            },
        ]
        strategies = [
            {
                "generation_index": 1,
                "content": '{"aggression": 0.8}',
                "best_score": 0.6,
                "gate_decision": "advance",
            },
        ]
        builder = _make_builder(trajectory_rows=trajectory, strategy_rows=strategies)
        result = builder.build_experiment_log("run_1")
        assert "| json |" in result

    def test_build_experiment_log_approach_detection_code(self) -> None:
        """Strategy containing 'def ' or 'result =' should be detected as 'code'."""
        trajectory = [
            {
                "generation_index": 1,
                "mean_score": 0.5,
                "best_score": 0.6,
                "elo": 1000.0,
                "gate_decision": "advance",
                "delta": 0.1,
            },
            {
                "generation_index": 2,
                "mean_score": 0.6,
                "best_score": 0.7,
                "elo": 1020.0,
                "gate_decision": "advance",
                "delta": 0.1,
            },
        ]
        strategies = [
            {
                "generation_index": 1,
                "content": "def compute(state):\n    result = {'a': 1}\n    return result",
                "best_score": 0.6,
                "gate_decision": "advance",
            },
            {
                "generation_index": 2,
                "content": "result = {'aggression': 0.9}",
                "best_score": 0.7,
                "gate_decision": "advance",
            },
        ]
        builder = _make_builder(trajectory_rows=trajectory, strategy_rows=strategies)
        result = builder.build_experiment_log("run_1")
        # Both rows should have 'code' approach
        assert result.count("| code |") == 2

    def test_build_experiment_log_approach_detection_text(self) -> None:
        """Strategy that is neither JSON nor code should be detected as 'text'."""
        trajectory = [
            {
                "generation_index": 1,
                "mean_score": 0.5,
                "best_score": 0.6,
                "elo": 1000.0,
                "gate_decision": "advance",
                "delta": 0.1,
            },
        ]
        strategies = [
            {
                "generation_index": 1,
                "content": "Use a balanced approach with moderate aggression",
                "best_score": 0.6,
                "gate_decision": "advance",
            },
        ]
        builder = _make_builder(trajectory_rows=trajectory, strategy_rows=strategies)
        result = builder.build_experiment_log("run_1")
        assert "| text |" in result


# ── MTS-99: build_prompt_bundle experiment_log parameter ────────────────


def _obs() -> Observation:
    return Observation(narrative="test", state={"key": "value"}, constraints=["c1"])


class TestPromptBundleExperimentLog:
    def test_prompt_bundle_includes_experiment_log(self) -> None:
        """When experiment_log is provided, it should appear in all prompts."""
        from mts.prompts.templates import build_prompt_bundle

        log_text = "| Gen | Strategy Summary | Score | Delta | Gate | Approach |\n| 1 | strat | 0.6 | +0.1 | advance | json |"
        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=_obs(),
            current_playbook="playbook",
            available_tools="tools",
            experiment_log=log_text,
        )
        assert "Experiment log:" in bundle.competitor
        assert log_text in bundle.competitor
        assert "Experiment log:" in bundle.analyst
        assert "Experiment log:" in bundle.coach
        assert "Experiment log:" in bundle.architect

    def test_prompt_bundle_empty_experiment_log_omitted(self) -> None:
        """When experiment_log is empty, the 'Experiment log:' header should not appear."""
        from mts.prompts.templates import build_prompt_bundle

        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=_obs(),
            current_playbook="playbook",
            available_tools="tools",
            experiment_log="",
        )
        assert "Experiment log:" not in bundle.competitor
        assert "Experiment log:" not in bundle.analyst

    def test_prompt_bundle_experiment_log_default_empty(self) -> None:
        """Default value for experiment_log should be empty, omitting the block."""
        from mts.prompts.templates import build_prompt_bundle

        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=_obs(),
            current_playbook="playbook",
            available_tools="tools",
        )
        assert "Experiment log:" not in bundle.competitor
