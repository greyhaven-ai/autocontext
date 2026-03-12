"""Tests for stagnation detection and fresh start."""

from __future__ import annotations

from unittest.mock import MagicMock

from autocontext.config.settings import AppSettings
from autocontext.knowledge.fresh_start import execute_fresh_start
from autocontext.knowledge.stagnation import StagnationDetector, StagnationReport
from autocontext.loop.stage_types import GenerationContext
from autocontext.loop.stages import stage_stagnation_check
from autocontext.storage.artifacts import ArtifactStore

# ---------------------------------------------------------------------------
# StagnationDetector tests
# ---------------------------------------------------------------------------


class TestStagnationDetector:
    def test_consecutive_rollbacks_at_threshold_triggers(self) -> None:
        detector = StagnationDetector(rollback_threshold=3)
        report = detector.detect(
            gate_history=["advance", "rollback", "rollback", "rollback"],
            score_history=[0.5, 0.4, 0.4, 0.4],
        )
        assert report.is_stagnated is True
        assert report.trigger == "consecutive_rollbacks"
        assert "3 consecutive rollbacks" in report.detail

    def test_consecutive_rollbacks_below_threshold(self) -> None:
        detector = StagnationDetector(rollback_threshold=5)
        report = detector.detect(
            gate_history=["rollback", "rollback", "rollback"],
            score_history=[0.4, 0.4, 0.4],
        )
        assert report.is_stagnated is False
        assert report.trigger == "none"

    def test_score_plateau_triggers(self) -> None:
        detector = StagnationDetector(plateau_window=3, plateau_epsilon=0.01)
        report = detector.detect(
            gate_history=["advance", "advance", "advance"],
            score_history=[0.5, 0.5, 0.5],
        )
        assert report.is_stagnated is True
        assert report.trigger == "score_plateau"
        assert "variance" in report.detail

    def test_score_plateau_high_variance_no_trigger(self) -> None:
        detector = StagnationDetector(plateau_window=3, plateau_epsilon=0.001)
        report = detector.detect(
            gate_history=["advance", "advance", "advance"],
            score_history=[0.3, 0.5, 0.7],
        )
        assert report.is_stagnated is False

    def test_insufficient_history_no_stagnation(self) -> None:
        detector = StagnationDetector(plateau_window=5, rollback_threshold=5)
        report = detector.detect(
            gate_history=["advance"],
            score_history=[0.5],
        )
        assert report.is_stagnated is False

    def test_interleaved_advance_rollback_resets_count(self) -> None:
        detector = StagnationDetector(rollback_threshold=3, plateau_window=10)
        report = detector.detect(
            gate_history=["rollback", "rollback", "advance", "rollback", "rollback"],
            score_history=[0.2, 0.3, 0.8, 0.1, 0.6],
        )
        assert report.is_stagnated is False

    def test_empty_history_no_stagnation(self) -> None:
        detector = StagnationDetector()
        report = detector.detect(gate_history=[], score_history=[])
        assert report.is_stagnated is False
        assert report.trigger == "none"

    def test_exact_epsilon_boundary_no_trigger(self) -> None:
        """Variance exactly equal to epsilon should NOT trigger (strictly less than)."""
        detector = StagnationDetector(plateau_window=2, plateau_epsilon=0.01)
        # Two scores: 0.0 and 0.2 => mean=0.1, var = ((0.0-0.1)^2+(0.2-0.1)^2)/2 = 0.01
        report = detector.detect(
            gate_history=["advance", "advance"],
            score_history=[0.0, 0.2],
        )
        assert report.is_stagnated is False


class TestStagnationReportNoStagnation:
    def test_static_factory(self) -> None:
        report = StagnationReport.no_stagnation()
        assert report.is_stagnated is False
        assert report.trigger == "none"
        assert report.detail == ""


# ---------------------------------------------------------------------------
# execute_fresh_start tests
# ---------------------------------------------------------------------------


class TestExecuteFreshStart:
    def test_archives_playbook_and_writes_distilled(self, tmp_path: object) -> None:
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",  # type: ignore[operator]
            knowledge_root=tmp_path / "knowledge",  # type: ignore[operator]
            skills_root=tmp_path / "skills",  # type: ignore[operator]
            claude_skills_path=tmp_path / ".claude" / "skills",  # type: ignore[operator]
        )
        scenario = "test_scenario"
        # Seed a playbook
        artifacts.write_playbook(scenario, "Original playbook content")
        hint = execute_fresh_start(
            artifacts=artifacts,
            scenario_name=scenario,
            current_strategy={"param_a": 1, "param_b": 2},
            lessons=["lesson one", "lesson two"],
            top_n=5,
        )
        # Playbook should now be the distilled version
        playbook = artifacts.read_playbook(scenario)
        assert "Fresh Start Playbook" in playbook
        assert "lesson one" in playbook
        assert "lesson two" in playbook
        assert "param_a" in playbook
        assert isinstance(hint, str)

    def test_clears_hints(self, tmp_path: object) -> None:
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",  # type: ignore[operator]
            knowledge_root=tmp_path / "knowledge",  # type: ignore[operator]
            skills_root=tmp_path / "skills",  # type: ignore[operator]
            claude_skills_path=tmp_path / ".claude" / "skills",  # type: ignore[operator]
        )
        scenario = "test_scenario"
        artifacts.write_playbook(scenario, "playbook")
        artifacts.write_hints(scenario, "some hints")
        execute_fresh_start(
            artifacts=artifacts,
            scenario_name=scenario,
            current_strategy={},
            lessons=[],
        )
        hints = artifacts.read_hints(scenario)
        assert hints.strip() == ""

    def test_retains_top_n_lessons(self, tmp_path: object) -> None:
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",  # type: ignore[operator]
            knowledge_root=tmp_path / "knowledge",  # type: ignore[operator]
            skills_root=tmp_path / "skills",  # type: ignore[operator]
            claude_skills_path=tmp_path / ".claude" / "skills",  # type: ignore[operator]
        )
        scenario = "test_scenario"
        artifacts.write_playbook(scenario, "playbook")
        lessons = [f"lesson {i}" for i in range(10)]
        execute_fresh_start(
            artifacts=artifacts,
            scenario_name=scenario,
            current_strategy={},
            lessons=lessons,
            top_n=3,
        )
        playbook = artifacts.read_playbook(scenario)
        assert "lesson 0" in playbook
        assert "lesson 2" in playbook
        assert "lesson 3" not in playbook

    def test_returns_fresh_start_hint(self, tmp_path: object) -> None:
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",  # type: ignore[operator]
            knowledge_root=tmp_path / "knowledge",  # type: ignore[operator]
            skills_root=tmp_path / "skills",  # type: ignore[operator]
            claude_skills_path=tmp_path / ".claude" / "skills",  # type: ignore[operator]
        )
        scenario = "test_scenario"
        artifacts.write_playbook(scenario, "playbook")
        hint = execute_fresh_start(
            artifacts=artifacts,
            scenario_name=scenario,
            current_strategy={},
            lessons=[],
        )
        assert "FRESH START" in hint
        assert "fundamentally different" in hint

    def test_handles_empty_lessons(self, tmp_path: object) -> None:
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",  # type: ignore[operator]
            knowledge_root=tmp_path / "knowledge",  # type: ignore[operator]
            skills_root=tmp_path / "skills",  # type: ignore[operator]
            claude_skills_path=tmp_path / ".claude" / "skills",  # type: ignore[operator]
        )
        scenario = "test_scenario"
        artifacts.write_playbook(scenario, "playbook")
        execute_fresh_start(
            artifacts=artifacts,
            scenario_name=scenario,
            current_strategy={},
            lessons=[],
        )
        playbook = artifacts.read_playbook(scenario)
        assert "No prior lessons" in playbook


# ---------------------------------------------------------------------------
# stage_stagnation_check tests
# ---------------------------------------------------------------------------


def _make_ctx(
    tmp_path: object,
    *,
    stagnation_reset_enabled: bool = True,
    ablation_no_feedback: bool = False,
    gate_history: list[str] | None = None,
    score_history: list[float] | None = None,
    rollback_threshold: int = 3,
    plateau_window: int = 5,
    plateau_epsilon: float = 0.01,
) -> GenerationContext:
    """Build a minimal GenerationContext for stage testing."""
    settings = AppSettings(
        stagnation_reset_enabled=stagnation_reset_enabled,
        ablation_no_feedback=ablation_no_feedback,
        stagnation_rollback_threshold=rollback_threshold,
        stagnation_plateau_window=plateau_window,
        stagnation_plateau_epsilon=plateau_epsilon,
        knowledge_root=tmp_path / "knowledge",  # type: ignore[operator]
        skills_root=tmp_path / "skills",  # type: ignore[operator]
        runs_root=tmp_path / "runs",  # type: ignore[operator]
        claude_skills_path=tmp_path / ".claude" / "skills",  # type: ignore[operator]
    )
    scenario = MagicMock()
    return GenerationContext(
        run_id="test_run",
        scenario_name="test_scenario",
        scenario=scenario,
        generation=5,
        settings=settings,
        previous_best=0.5,
        challenger_elo=1500.0,
        score_history=score_history if score_history is not None else [],
        gate_decision_history=gate_history if gate_history is not None else [],
        coach_competitor_hints="original hints",
        replay_narrative="",
        current_strategy={"param": 42},
    )


class TestStageStagnationCheck:
    def test_noop_when_disabled(self, tmp_path: object) -> None:
        ctx = _make_ctx(tmp_path, stagnation_reset_enabled=False)
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",  # type: ignore[operator]
            knowledge_root=tmp_path / "knowledge",  # type: ignore[operator]
            skills_root=tmp_path / "skills",  # type: ignore[operator]
            claude_skills_path=tmp_path / ".claude" / "skills",  # type: ignore[operator]
        )
        events = MagicMock()
        result = stage_stagnation_check(ctx, artifacts=artifacts, events=events)
        assert result.fresh_start_triggered is False
        assert result.coach_competitor_hints == "original hints"
        events.emit.assert_not_called()

    def test_noop_on_ablation(self, tmp_path: object) -> None:
        ctx = _make_ctx(tmp_path, ablation_no_feedback=True)
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",  # type: ignore[operator]
            knowledge_root=tmp_path / "knowledge",  # type: ignore[operator]
            skills_root=tmp_path / "skills",  # type: ignore[operator]
            claude_skills_path=tmp_path / ".claude" / "skills",  # type: ignore[operator]
        )
        events = MagicMock()
        result = stage_stagnation_check(ctx, artifacts=artifacts, events=events)
        assert result.fresh_start_triggered is False
        events.emit.assert_not_called()

    def test_triggers_on_consecutive_rollbacks(self, tmp_path: object) -> None:
        ctx = _make_ctx(
            tmp_path,
            gate_history=["rollback", "rollback", "rollback"],
            score_history=[0.4, 0.4, 0.4],
            rollback_threshold=3,
        )
        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",  # type: ignore[operator]
            knowledge_root=tmp_path / "knowledge",  # type: ignore[operator]
            skills_root=tmp_path / "skills",  # type: ignore[operator]
            claude_skills_path=tmp_path / ".claude" / "skills",  # type: ignore[operator]
        )
        # Seed a playbook so write_playbook can archive it
        artifacts.write_playbook("test_scenario", "old playbook")
        events = MagicMock()
        result = stage_stagnation_check(ctx, artifacts=artifacts, events=events)
        assert result.fresh_start_triggered is True
        assert "FRESH START" in result.coach_competitor_hints
        events.emit.assert_called_once()
        call_args = events.emit.call_args
        assert call_args[0][0] == "fresh_start"
        assert call_args[0][1]["trigger"] == "consecutive_rollbacks"
