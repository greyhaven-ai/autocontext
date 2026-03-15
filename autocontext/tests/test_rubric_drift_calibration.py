"""Tests for AC-259 + AC-260: rubric-drift monitoring and human calibration workflow.

AC-259: RubricSnapshot, DriftThresholds, DriftWarning, RubricDriftMonitor, DriftStore
AC-260: CalibrationSample, CalibrationOutcome, CalibrationRound, SpotCheckSampler, CalibrationStore
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ===========================================================================
# Shared helpers
# ===========================================================================


def _make_drift_facets() -> list[Any]:
    """Build facets that exhibit drift signals for testing."""
    from autocontext.analytics.facets import (
        DelightSignal,
        FrictionSignal,
        RunFacet,
    )

    return [
        # Older runs: moderate scores, some friction
        RunFacet(
            run_id="drift-1",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="deterministic",
            executor_mode="local",
            total_generations=5,
            advances=3, retries=1, rollbacks=1,
            best_score=0.55, best_elo=1050.0,
            total_duration_seconds=60.0,
            total_tokens=30000, total_cost_usd=0.15,
            tool_invocations=5, validation_failures=2,
            consultation_count=0, consultation_cost_usd=0.0,
            friction_signals=[
                FrictionSignal(
                    signal_type="validation_failure", severity="medium",
                    generation_index=2, description="Parse failure",
                    evidence=["ev-1"],
                ),
            ],
            delight_signals=[],
            events=[], metadata={"release": "v1.0.0"},
            created_at="2026-03-01T12:00:00Z",
        ),
        RunFacet(
            run_id="drift-2",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="deterministic",
            executor_mode="local",
            total_generations=4,
            advances=2, retries=2, rollbacks=0,
            best_score=0.60, best_elo=1070.0,
            total_duration_seconds=50.0,
            total_tokens=25000, total_cost_usd=0.12,
            tool_invocations=4, validation_failures=1,
            consultation_count=0, consultation_cost_usd=0.0,
            friction_signals=[],
            delight_signals=[
                DelightSignal(
                    signal_type="strong_improvement", generation_index=2,
                    description="Big jump", evidence=["ev-2"],
                ),
            ],
            events=[], metadata={"release": "v1.0.0"},
            created_at="2026-03-02T12:00:00Z",
        ),
        # Newer runs: suspiciously high scores, near-perfect, many revision jumps
        RunFacet(
            run_id="drift-3",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="anthropic",
            executor_mode="local",
            total_generations=3,
            advances=3, retries=0, rollbacks=0,
            best_score=0.97, best_elo=1400.0,
            total_duration_seconds=30.0,
            total_tokens=15000, total_cost_usd=0.08,
            tool_invocations=3, validation_failures=0,
            consultation_count=0, consultation_cost_usd=0.0,
            friction_signals=[],
            delight_signals=[
                DelightSignal(
                    signal_type="strong_improvement", generation_index=1,
                    description="Huge jump", evidence=["ev-3"],
                ),
                DelightSignal(
                    signal_type="fast_advance", generation_index=2,
                    description="Quick advance", evidence=["ev-4"],
                ),
            ],
            events=[], metadata={"release": "v1.1.0"},
            created_at="2026-03-10T12:00:00Z",
        ),
        RunFacet(
            run_id="drift-4",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="anthropic",
            executor_mode="local",
            total_generations=3,
            advances=3, retries=0, rollbacks=0,
            best_score=0.98, best_elo=1420.0,
            total_duration_seconds=25.0,
            total_tokens=14000, total_cost_usd=0.07,
            tool_invocations=2, validation_failures=0,
            consultation_count=0, consultation_cost_usd=0.0,
            friction_signals=[],
            delight_signals=[
                DelightSignal(
                    signal_type="strong_improvement", generation_index=1,
                    description="Huge jump", evidence=["ev-5"],
                ),
            ],
            events=[], metadata={"release": "v1.1.0"},
            created_at="2026-03-11T12:00:00Z",
        ),
        RunFacet(
            run_id="drift-5",
            scenario="othello",
            scenario_family="game",
            agent_provider="anthropic",
            executor_mode="local",
            total_generations=4,
            advances=4, retries=0, rollbacks=0,
            best_score=0.96, best_elo=1380.0,
            total_duration_seconds=35.0,
            total_tokens=16000, total_cost_usd=0.09,
            tool_invocations=4, validation_failures=0,
            consultation_count=0, consultation_cost_usd=0.0,
            friction_signals=[],
            delight_signals=[
                DelightSignal(
                    signal_type="fast_advance", generation_index=1,
                    description="Clean run", evidence=["ev-6"],
                ),
            ],
            events=[], metadata={"release": "v1.1.0"},
            created_at="2026-03-12T12:00:00Z",
        ),
    ]


def _make_healthy_facets() -> list[Any]:
    """Build facets with no drift signals — healthy scoring distribution."""
    from autocontext.analytics.facets import (
        FrictionSignal,
        RunFacet,
    )

    return [
        RunFacet(
            run_id=f"healthy-{i}",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="deterministic",
            executor_mode="local",
            total_generations=5,
            advances=3, retries=1, rollbacks=1,
            best_score=0.5 + i * 0.05,  # 0.50, 0.55, 0.60, 0.65
            best_elo=1000.0 + i * 20,
            total_duration_seconds=50.0,
            total_tokens=20000, total_cost_usd=0.10,
            tool_invocations=4, validation_failures=1,
            consultation_count=0, consultation_cost_usd=0.0,
            friction_signals=[
                FrictionSignal(
                    signal_type="validation_failure", severity="low",
                    generation_index=2, description="minor",
                    evidence=[f"ev-h-{i}"],
                ),
            ],
            delight_signals=[],
            events=[], metadata={},
            created_at=f"2026-03-{10 + i:02d}T12:00:00Z",
        )
        for i in range(4)
    ]


# ===========================================================================
# AC-259: RubricSnapshot data model
# ===========================================================================


class TestRubricSnapshot:
    def test_construction(self) -> None:
        from autocontext.analytics.rubric_drift import RubricSnapshot

        snap = RubricSnapshot(
            snapshot_id="snap-1",
            created_at="2026-03-14T12:00:00Z",
            window_start="2026-03-01T00:00:00Z",
            window_end="2026-03-14T00:00:00Z",
            run_count=5,
            mean_score=0.81,
            median_score=0.96,
            stddev_score=0.19,
            min_score=0.55,
            max_score=0.98,
            score_inflation_rate=0.2,
            perfect_score_rate=0.6,
            revision_jump_rate=0.4,
            retry_rate=0.2,
            rollback_rate=0.07,
            release="v1.1.0",
            scenario_family="game",
            agent_provider="anthropic",
        )
        assert snap.snapshot_id == "snap-1"
        assert snap.mean_score == 0.81
        assert snap.perfect_score_rate == 0.6

    def test_roundtrip(self) -> None:
        from autocontext.analytics.rubric_drift import RubricSnapshot

        snap = RubricSnapshot(
            snapshot_id="snap-2",
            created_at="2026-03-14T12:00:00Z",
            window_start="2026-03-01T00:00:00Z",
            window_end="2026-03-14T00:00:00Z",
            run_count=3,
            mean_score=0.7,
            median_score=0.7,
            stddev_score=0.1,
            min_score=0.6,
            max_score=0.8,
            score_inflation_rate=0.05,
            perfect_score_rate=0.0,
            revision_jump_rate=0.1,
            retry_rate=0.1,
            rollback_rate=0.1,
            release="",
            scenario_family="",
            agent_provider="",
        )
        d = snap.to_dict()
        restored = RubricSnapshot.from_dict(d)
        assert restored.snapshot_id == snap.snapshot_id
        assert restored.mean_score == snap.mean_score
        assert restored.stddev_score == snap.stddev_score


# ===========================================================================
# AC-259: DriftThresholds data model
# ===========================================================================


class TestDriftThresholds:
    def test_defaults(self) -> None:
        from autocontext.analytics.rubric_drift import DriftThresholds

        t = DriftThresholds()
        assert t.max_score_inflation == 0.15
        assert t.max_perfect_rate == 0.5
        assert t.max_revision_jump_rate == 0.4
        assert t.min_stddev == 0.05
        assert t.max_retry_rate == 0.5
        assert t.max_rollback_rate == 0.3

    def test_custom(self) -> None:
        from autocontext.analytics.rubric_drift import DriftThresholds

        t = DriftThresholds(
            max_score_inflation=0.3,
            max_perfect_rate=0.8,
            min_stddev=0.01,
        )
        assert t.max_score_inflation == 0.3
        assert t.max_perfect_rate == 0.8


# ===========================================================================
# AC-259: DriftWarning data model
# ===========================================================================


class TestDriftWarning:
    def test_construction(self) -> None:
        from autocontext.analytics.rubric_drift import DriftWarning

        w = DriftWarning(
            warning_id="warn-1",
            created_at="2026-03-14T12:00:00Z",
            warning_type="score_inflation",
            severity="high",
            description="Scores trending upward suspiciously",
            snapshot_id="snap-1",
            metric_name="score_inflation_rate",
            metric_value=0.25,
            threshold_value=0.15,
            affected_scenarios=["grid_ctf"],
            affected_providers=["anthropic"],
            affected_releases=["v1.1.0"],
        )
        assert w.warning_type == "score_inflation"
        assert w.severity == "high"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.rubric_drift import DriftWarning

        w = DriftWarning(
            warning_id="warn-2",
            created_at="2026-03-14T12:00:00Z",
            warning_type="score_compression",
            severity="medium",
            description="Score variance too low",
            snapshot_id="snap-2",
            metric_name="stddev_score",
            metric_value=0.02,
            threshold_value=0.05,
            affected_scenarios=[],
            affected_providers=[],
            affected_releases=[],
        )
        d = w.to_dict()
        restored = DriftWarning.from_dict(d)
        assert restored.warning_id == w.warning_id
        assert restored.warning_type == w.warning_type
        assert restored.metric_value == w.metric_value


# ===========================================================================
# AC-259: RubricDriftMonitor
# ===========================================================================


class TestRubricDriftMonitor:
    def test_compute_snapshot_basic(self) -> None:
        from autocontext.analytics.rubric_drift import RubricDriftMonitor

        facets = _make_drift_facets()
        monitor = RubricDriftMonitor()
        snap = monitor.compute_snapshot(facets)

        assert snap.run_count == 5
        assert snap.mean_score > 0
        assert snap.min_score <= snap.max_score
        assert snap.window_start <= snap.window_end
        assert snap.metadata["scenarios"] == ["grid_ctf", "othello"]

    def test_detect_score_inflation(self) -> None:
        """Drift facets have rising scores over time — should detect inflation."""
        from autocontext.analytics.rubric_drift import (
            DriftThresholds,
            RubricDriftMonitor,
        )

        facets = _make_drift_facets()
        monitor = RubricDriftMonitor(thresholds=DriftThresholds(max_score_inflation=0.1))
        snap = monitor.compute_snapshot(facets)
        warnings = monitor.detect_drift(snap)

        inflation_warnings = [w for w in warnings if w.warning_type == "score_inflation"]
        assert len(inflation_warnings) > 0

    def test_detect_perfect_rate(self) -> None:
        """Drift facets have 3/5 runs near-perfect — should detect high perfect rate."""
        from autocontext.analytics.rubric_drift import (
            DriftThresholds,
            RubricDriftMonitor,
        )

        facets = _make_drift_facets()
        monitor = RubricDriftMonitor(thresholds=DriftThresholds(max_perfect_rate=0.4))
        snap = monitor.compute_snapshot(facets)
        warnings = monitor.detect_drift(snap)

        perfect_warnings = [w for w in warnings if w.warning_type == "perfect_rate_high"]
        assert len(perfect_warnings) > 0

    def test_detect_score_compression(self) -> None:
        """When all scores are nearly identical, stddev is low — should detect compression."""
        from autocontext.analytics.facets import RunFacet
        from autocontext.analytics.rubric_drift import (
            DriftThresholds,
            RubricDriftMonitor,
        )

        # All scores very close: 0.70, 0.71, 0.70, 0.71
        compressed_facets = [
            RunFacet(
                run_id=f"comp-{i}",
                scenario="grid_ctf", scenario_family="game",
                agent_provider="deterministic", executor_mode="local",
                total_generations=3, advances=2, retries=1, rollbacks=0,
                best_score=0.70 + (i % 2) * 0.01,
                best_elo=1100.0,
                total_duration_seconds=30.0,
                total_tokens=15000, total_cost_usd=0.05,
                tool_invocations=2, validation_failures=0,
                consultation_count=0, consultation_cost_usd=0.0,
                friction_signals=[], delight_signals=[],
                events=[], metadata={},
                created_at=f"2026-03-{10 + i:02d}T12:00:00Z",
            )
            for i in range(4)
        ]

        monitor = RubricDriftMonitor(thresholds=DriftThresholds(min_stddev=0.05))
        snap = monitor.compute_snapshot(compressed_facets)
        warnings = monitor.detect_drift(snap)

        compression_warnings = [w for w in warnings if w.warning_type == "score_compression"]
        assert len(compression_warnings) > 0
        assert snap.stddev_score < 0.05

    def test_detect_revision_jump_rate(self) -> None:
        """Drift facets have many strong_improvement signals — should detect high jump rate."""
        from autocontext.analytics.rubric_drift import (
            DriftThresholds,
            RubricDriftMonitor,
        )

        facets = _make_drift_facets()
        monitor = RubricDriftMonitor(thresholds=DriftThresholds(max_revision_jump_rate=0.1))
        snap = monitor.compute_snapshot(facets)
        warnings = monitor.detect_drift(snap)

        jump_warnings = [w for w in warnings if w.warning_type == "revision_jump_rate_high"]
        assert len(jump_warnings) > 0

    def test_no_drift_on_healthy_facets(self) -> None:
        """Healthy facets should produce no warnings with default thresholds."""
        from autocontext.analytics.rubric_drift import RubricDriftMonitor

        facets = _make_healthy_facets()
        monitor = RubricDriftMonitor()
        snap = monitor.compute_snapshot(facets)
        warnings = monitor.detect_drift(snap)

        assert len(warnings) == 0

    def test_analyze_combines_snapshot_and_warnings(self) -> None:
        from autocontext.analytics.rubric_drift import (
            DriftThresholds,
            RubricDriftMonitor,
        )

        facets = _make_drift_facets()
        monitor = RubricDriftMonitor(thresholds=DriftThresholds(max_perfect_rate=0.4))
        snap, warnings = monitor.analyze(facets)

        assert snap.run_count == 5
        assert len(warnings) > 0

    def test_warnings_carry_snapshot_scenarios(self) -> None:
        from autocontext.analytics.rubric_drift import (
            DriftThresholds,
            RubricDriftMonitor,
        )

        facets = _make_drift_facets()
        monitor = RubricDriftMonitor(thresholds=DriftThresholds(max_perfect_rate=0.4))
        snapshot, warnings = monitor.analyze(facets)

        assert warnings
        assert any(warning.affected_scenarios for warning in warnings)
        assert warnings[0].affected_scenarios == ["grid_ctf", "othello"]

    def test_empty_facets(self) -> None:
        from autocontext.analytics.rubric_drift import RubricDriftMonitor

        monitor = RubricDriftMonitor()
        snap = monitor.compute_snapshot([])

        assert snap.run_count == 0
        assert snap.mean_score == 0.0

    def test_baseline_comparison(self) -> None:
        """Detect inflation by comparing current snapshot to a baseline."""
        from autocontext.analytics.rubric_drift import (
            DriftThresholds,
            RubricDriftMonitor,
            RubricSnapshot,
        )

        facets = _make_drift_facets()
        monitor = RubricDriftMonitor(thresholds=DriftThresholds(max_score_inflation=0.1))
        current = monitor.compute_snapshot(facets)

        # Baseline with lower mean
        baseline = RubricSnapshot(
            snapshot_id="baseline",
            created_at="2026-03-01T00:00:00Z",
            window_start="2026-02-01T00:00:00Z",
            window_end="2026-03-01T00:00:00Z",
            run_count=10,
            mean_score=0.55,
            median_score=0.55,
            stddev_score=0.1,
            min_score=0.4,
            max_score=0.7,
            score_inflation_rate=0.0,
            perfect_score_rate=0.0,
            revision_jump_rate=0.1,
            retry_rate=0.2,
            rollback_rate=0.1,
            release="v1.0.0",
            scenario_family="game",
            agent_provider="deterministic",
        )

        warnings = monitor.detect_drift(current, baseline=baseline)
        inflation = [w for w in warnings if w.warning_type == "score_inflation"]
        assert len(inflation) > 0


# ===========================================================================
# AC-259: DriftStore
# ===========================================================================


class TestDriftStore:
    def test_persist_and_load_snapshot(self, tmp_path: Path) -> None:
        from autocontext.analytics.rubric_drift import DriftStore, RubricSnapshot

        store = DriftStore(tmp_path)
        snap = RubricSnapshot(
            snapshot_id="snap-persist",
            created_at="2026-03-14T12:00:00Z",
            window_start="2026-03-01T00:00:00Z",
            window_end="2026-03-14T00:00:00Z",
            run_count=5,
            mean_score=0.8, median_score=0.8, stddev_score=0.1,
            min_score=0.6, max_score=0.98,
            score_inflation_rate=0.1, perfect_score_rate=0.4,
            revision_jump_rate=0.2, retry_rate=0.1, rollback_rate=0.05,
            release="", scenario_family="", agent_provider="",
        )
        path = store.persist_snapshot(snap)
        assert path.exists()

        loaded = store.load_snapshot("snap-persist")
        assert loaded is not None
        assert loaded.run_count == 5

    def test_load_missing_snapshot(self, tmp_path: Path) -> None:
        from autocontext.analytics.rubric_drift import DriftStore

        store = DriftStore(tmp_path)
        assert store.load_snapshot("nonexistent") is None

    def test_persist_and_load_warning(self, tmp_path: Path) -> None:
        from autocontext.analytics.rubric_drift import DriftStore, DriftWarning

        store = DriftStore(tmp_path)
        w = DriftWarning(
            warning_id="warn-persist",
            created_at="2026-03-14T12:00:00Z",
            warning_type="score_inflation",
            severity="high",
            description="test",
            snapshot_id="snap-1",
            metric_name="score_inflation_rate",
            metric_value=0.25,
            threshold_value=0.15,
            affected_scenarios=["grid_ctf"],
            affected_providers=["anthropic"],
            affected_releases=["v1.1.0"],
        )
        path = store.persist_warning(w)
        assert path.exists()

        loaded = store.load_warning("warn-persist")
        assert loaded is not None
        assert loaded.warning_type == "score_inflation"

    def test_load_missing_warning(self, tmp_path: Path) -> None:
        from autocontext.analytics.rubric_drift import DriftStore

        store = DriftStore(tmp_path)
        assert store.load_warning("nonexistent") is None

    def test_list_snapshots(self, tmp_path: Path) -> None:
        from autocontext.analytics.rubric_drift import DriftStore, RubricSnapshot

        store = DriftStore(tmp_path)
        for i in range(3):
            store.persist_snapshot(RubricSnapshot(
                snapshot_id=f"snap-{i}",
                created_at="2026-03-14T12:00:00Z",
                window_start="2026-03-01T00:00:00Z",
                window_end="2026-03-14T00:00:00Z",
                run_count=i, mean_score=0.5, median_score=0.5,
                stddev_score=0.1, min_score=0.4, max_score=0.6,
                score_inflation_rate=0.0, perfect_score_rate=0.0,
                revision_jump_rate=0.0, retry_rate=0.0, rollback_rate=0.0,
                release="", scenario_family="", agent_provider="",
            ))
        assert len(store.list_snapshots()) == 3

    def test_list_warnings(self, tmp_path: Path) -> None:
        from autocontext.analytics.rubric_drift import DriftStore, DriftWarning

        store = DriftStore(tmp_path)
        for i in range(2):
            store.persist_warning(DriftWarning(
                warning_id=f"warn-{i}",
                created_at="2026-03-14T12:00:00Z",
                warning_type="score_compression",
                severity="medium",
                description="test",
                snapshot_id="snap-1",
                metric_name="stddev_score",
                metric_value=0.02,
                threshold_value=0.05,
                affected_scenarios=[], affected_providers=[],
                affected_releases=[],
            ))
        assert len(store.list_warnings()) == 2


# ===========================================================================
# AC-260: CalibrationSample data model
# ===========================================================================


class TestCalibrationSample:
    def test_construction(self) -> None:
        from autocontext.analytics.calibration import CalibrationSample

        sample = CalibrationSample(
            sample_id="sample-1",
            run_id="drift-3",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="anthropic",
            generation_index=1,
            risk_score=0.85,
            risk_reasons=["near_perfect", "large_score_jump"],
            best_score=0.97,
            score_delta=0.35,
            playbook_mutation_size=0,
            created_at="2026-03-14T12:00:00Z",
        )
        assert sample.sample_id == "sample-1"
        assert sample.risk_score == 0.85
        assert "near_perfect" in sample.risk_reasons

    def test_roundtrip(self) -> None:
        from autocontext.analytics.calibration import CalibrationSample

        sample = CalibrationSample(
            sample_id="sample-2",
            run_id="drift-4",
            scenario="grid_ctf",
            scenario_family="game",
            agent_provider="anthropic",
            generation_index=0,
            risk_score=0.5,
            risk_reasons=["near_perfect"],
            best_score=0.98,
            score_delta=0.0,
            playbook_mutation_size=0,
            created_at="2026-03-14T12:00:00Z",
        )
        d = sample.to_dict()
        restored = CalibrationSample.from_dict(d)
        assert restored.sample_id == sample.sample_id
        assert restored.risk_score == sample.risk_score


# ===========================================================================
# AC-260: CalibrationOutcome data model
# ===========================================================================


class TestCalibrationOutcome:
    def test_construction(self) -> None:
        from autocontext.analytics.calibration import CalibrationOutcome

        outcome = CalibrationOutcome(
            outcome_id="outcome-1",
            sample_id="sample-1",
            decision="reject",
            reviewer="human-reviewer",
            notes="Rubric is overfit to style",
            rubric_quality="overfit",
            playbook_quality="good",
            recommended_action="rollback_rubric",
            created_at="2026-03-14T13:00:00Z",
        )
        assert outcome.decision == "reject"
        assert outcome.rubric_quality == "overfit"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.calibration import CalibrationOutcome

        outcome = CalibrationOutcome(
            outcome_id="outcome-2",
            sample_id="sample-2",
            decision="approve",
            reviewer="reviewer-2",
            notes="Looks good",
            rubric_quality="good",
            playbook_quality="good",
            recommended_action="none",
            created_at="2026-03-14T13:00:00Z",
        )
        d = outcome.to_dict()
        restored = CalibrationOutcome.from_dict(d)
        assert restored.outcome_id == outcome.outcome_id
        assert restored.decision == outcome.decision


# ===========================================================================
# AC-260: CalibrationRound data model
# ===========================================================================


class TestCalibrationRound:
    def test_construction(self) -> None:
        from autocontext.analytics.calibration import CalibrationRound

        rnd = CalibrationRound(
            round_id="round-1",
            created_at="2026-03-14T12:00:00Z",
            samples=[],
            outcomes=[],
            status="pending",
            summary="",
        )
        assert rnd.round_id == "round-1"
        assert rnd.status == "pending"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.calibration import (
            CalibrationOutcome,
            CalibrationRound,
            CalibrationSample,
        )

        rnd = CalibrationRound(
            round_id="round-2",
            created_at="2026-03-14T12:00:00Z",
            samples=[
                CalibrationSample(
                    sample_id="s1", run_id="r1", scenario="grid_ctf",
                    scenario_family="game", agent_provider="anthropic",
                    generation_index=1, risk_score=0.8,
                    risk_reasons=["near_perfect"],
                    best_score=0.97, score_delta=0.3,
                    playbook_mutation_size=0,
                    created_at="2026-03-14T12:00:00Z",
                ),
            ],
            outcomes=[
                CalibrationOutcome(
                    outcome_id="o1", sample_id="s1", decision="approve",
                    reviewer="tester", notes="ok",
                    rubric_quality="good", playbook_quality="good",
                    recommended_action="none",
                    created_at="2026-03-14T13:00:00Z",
                ),
            ],
            status="completed",
            summary="All clear",
        )
        d = rnd.to_dict()
        restored = CalibrationRound.from_dict(d)
        assert restored.round_id == rnd.round_id
        assert len(restored.samples) == 1
        assert len(restored.outcomes) == 1
        assert restored.status == "completed"


# ===========================================================================
# AC-260: SpotCheckSampler
# ===========================================================================


class TestSpotCheckSampler:
    def test_sample_high_risk(self) -> None:
        """Drift facets with near-perfect scores should be sampled first."""
        from autocontext.analytics.calibration import SpotCheckSampler

        facets = _make_drift_facets()
        sampler = SpotCheckSampler(max_samples=3)
        samples = sampler.sample(facets)

        assert len(samples) <= 3
        assert len(samples) > 0
        # Highest risk samples should be the near-perfect runs
        assert samples[0].risk_score >= samples[-1].risk_score
        high_score_run_ids = {"drift-3", "drift-4", "drift-5"}
        assert samples[0].run_id in high_score_run_ids

    def test_sample_with_drift_warnings(self) -> None:
        """Drift warnings should boost risk scores for affected runs."""
        from autocontext.analytics.calibration import SpotCheckSampler
        from autocontext.analytics.rubric_drift import DriftWarning

        facets = _make_drift_facets()
        warnings = [
            DriftWarning(
                warning_id="w1",
                created_at="2026-03-14T12:00:00Z",
                warning_type="score_inflation",
                severity="high",
                description="Inflation detected",
                snapshot_id="snap-1",
                metric_name="score_inflation_rate",
                metric_value=0.25,
                threshold_value=0.15,
                affected_scenarios=["grid_ctf"],
                affected_providers=["anthropic"],
                affected_releases=["v1.1.0"],
            ),
        ]
        sampler = SpotCheckSampler(max_samples=5)
        samples = sampler.sample(facets, drift_warnings=warnings)

        assert len(samples) > 0
        # Anthropic grid_ctf runs in v1.1.0 should have boosted risk
        anthropic_samples = [s for s in samples if s.agent_provider == "anthropic"]
        assert len(anthropic_samples) > 0
        assert any("drift_warning_match" in sample.risk_reasons for sample in anthropic_samples)

    def test_sample_max_limit(self) -> None:
        from autocontext.analytics.calibration import SpotCheckSampler

        facets = _make_drift_facets()
        sampler = SpotCheckSampler(max_samples=2)
        samples = sampler.sample(facets)

        assert len(samples) <= 2

    def test_empty_facets(self) -> None:
        from autocontext.analytics.calibration import SpotCheckSampler

        sampler = SpotCheckSampler()
        samples = sampler.sample([])

        assert len(samples) == 0


# ===========================================================================
# AC-260: CalibrationStore
# ===========================================================================


class TestCalibrationStore:
    def test_persist_and_load_round(self, tmp_path: Path) -> None:
        from autocontext.analytics.calibration import (
            CalibrationRound,
            CalibrationStore,
        )

        store = CalibrationStore(tmp_path)
        rnd = CalibrationRound(
            round_id="round-persist",
            created_at="2026-03-14T12:00:00Z",
            samples=[], outcomes=[],
            status="pending", summary="",
        )
        path = store.persist_round(rnd)
        assert path.exists()

        loaded = store.load_round("round-persist")
        assert loaded is not None
        assert loaded.status == "pending"

    def test_load_missing_round(self, tmp_path: Path) -> None:
        from autocontext.analytics.calibration import CalibrationStore

        store = CalibrationStore(tmp_path)
        assert store.load_round("nonexistent") is None

    def test_persist_and_load_outcome(self, tmp_path: Path) -> None:
        from autocontext.analytics.calibration import (
            CalibrationOutcome,
            CalibrationStore,
        )

        store = CalibrationStore(tmp_path)
        outcome = CalibrationOutcome(
            outcome_id="outcome-persist",
            sample_id="s1",
            decision="needs_adjustment",
            reviewer="tester",
            notes="Playbook has bloat",
            rubric_quality="good",
            playbook_quality="bloated",
            recommended_action="investigate",
            created_at="2026-03-14T13:00:00Z",
        )
        path = store.persist_outcome(outcome)
        assert path.exists()

        loaded = store.load_outcome("outcome-persist")
        assert loaded is not None
        assert loaded.decision == "needs_adjustment"

    def test_load_missing_outcome(self, tmp_path: Path) -> None:
        from autocontext.analytics.calibration import CalibrationStore

        store = CalibrationStore(tmp_path)
        assert store.load_outcome("nonexistent") is None

    def test_list_rounds(self, tmp_path: Path) -> None:
        from autocontext.analytics.calibration import (
            CalibrationRound,
            CalibrationStore,
        )

        store = CalibrationStore(tmp_path)
        for i in range(3):
            store.persist_round(CalibrationRound(
                round_id=f"round-{i}",
                created_at="2026-03-14T12:00:00Z",
                samples=[], outcomes=[],
                status="completed", summary="",
            ))
        assert len(store.list_rounds()) == 3

    def test_list_outcomes(self, tmp_path: Path) -> None:
        from autocontext.analytics.calibration import (
            CalibrationOutcome,
            CalibrationStore,
        )

        store = CalibrationStore(tmp_path)
        for i in range(2):
            store.persist_outcome(CalibrationOutcome(
                outcome_id=f"outcome-{i}",
                sample_id=f"s{i}",
                decision="approve",
                reviewer="tester",
                notes="",
                rubric_quality="good",
                playbook_quality="good",
                recommended_action="none",
                created_at="2026-03-14T13:00:00Z",
            ))
        assert len(store.list_outcomes()) == 2
