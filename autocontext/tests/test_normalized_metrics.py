"""Tests for AC-190: Normalized cross-scenario progress and cost-efficiency reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.knowledge.normalized_metrics import (
    CostEfficiency,
    NormalizedProgress,
    RunProgressReport,
    ScenarioNormalizer,
    compute_cost_efficiency,
    compute_normalized_progress,
    generate_run_progress_report,
)

# ---------------------------------------------------------------------------
# NormalizedProgress dataclass
# ---------------------------------------------------------------------------

class TestNormalizedProgress:
    def test_construction_defaults(self) -> None:
        np = NormalizedProgress(raw_score=0.75, normalized_score=0.75, pct_of_ceiling=75.0)
        assert np.raw_score == 0.75
        assert np.normalized_score == 0.75
        assert np.score_floor == 0.0
        assert np.score_ceiling == 1.0
        assert np.pct_of_ceiling == 75.0

    def test_custom_floor_ceiling(self) -> None:
        np = NormalizedProgress(
            raw_score=500,
            normalized_score=0.5,
            score_floor=0,
            score_ceiling=1000,
            pct_of_ceiling=50.0,
        )
        assert np.raw_score == 500
        assert np.normalized_score == 0.5
        assert np.pct_of_ceiling == 50.0

    def test_to_dict(self) -> None:
        np = NormalizedProgress(raw_score=0.8, normalized_score=0.8, pct_of_ceiling=80.0)
        d = np.to_dict()
        assert d["raw_score"] == 0.8
        assert d["normalized_score"] == 0.8
        assert d["pct_of_ceiling"] == 80.0
        assert "score_floor" in d
        assert "score_ceiling" in d

    def test_from_dict_roundtrip(self) -> None:
        original = NormalizedProgress(
            raw_score=0.65,
            normalized_score=0.65,
            score_floor=0.0,
            score_ceiling=1.0,
            pct_of_ceiling=65.0,
        )
        restored = NormalizedProgress.from_dict(original.to_dict())
        assert restored.raw_score == original.raw_score
        assert restored.normalized_score == original.normalized_score
        assert restored.pct_of_ceiling == original.pct_of_ceiling


# ---------------------------------------------------------------------------
# CostEfficiency dataclass
# ---------------------------------------------------------------------------

class TestCostEfficiency:
    def test_construction(self) -> None:
        ce = CostEfficiency(
            total_input_tokens=10000,
            total_output_tokens=5000,
            total_tokens=15000,
            total_cost_usd=0.05,
            tokens_per_advance=5000,
            cost_per_advance=0.0167,
            tokens_per_score_point=30000,
        )
        assert ce.total_tokens == 15000
        assert ce.tokens_per_advance == 5000

    def test_defaults(self) -> None:
        ce = CostEfficiency()
        assert ce.total_input_tokens == 0
        assert ce.total_output_tokens == 0
        assert ce.total_tokens == 0
        assert ce.total_cost_usd == 0.0
        assert ce.tokens_per_advance == 0
        assert ce.cost_per_advance == 0.0
        assert ce.tokens_per_score_point == 0

    def test_to_dict(self) -> None:
        ce = CostEfficiency(total_tokens=1000, total_cost_usd=0.01)
        d = ce.to_dict()
        assert d["total_tokens"] == 1000
        assert d["total_cost_usd"] == 0.01

    def test_from_dict_roundtrip(self) -> None:
        original = CostEfficiency(
            total_input_tokens=5000,
            total_output_tokens=2000,
            total_tokens=7000,
            total_cost_usd=0.03,
            tokens_per_advance=3500,
            cost_per_advance=0.015,
            tokens_per_score_point=14000,
        )
        restored = CostEfficiency.from_dict(original.to_dict())
        assert restored.total_tokens == original.total_tokens
        assert restored.cost_per_advance == original.cost_per_advance


# ---------------------------------------------------------------------------
# ScenarioNormalizer
# ---------------------------------------------------------------------------

class TestScenarioNormalizer:
    def test_default_normalizer_maps_identity(self) -> None:
        """Default normalizer uses floor=0, ceiling=1."""
        normalizer = ScenarioNormalizer()
        result = normalizer.normalize(0.75)
        assert result.normalized_score == pytest.approx(0.75)
        assert result.pct_of_ceiling == pytest.approx(75.0)

    def test_custom_floor_ceiling(self) -> None:
        """Score range [0, 64] maps to [0, 1]."""
        normalizer = ScenarioNormalizer(score_floor=0, score_ceiling=64)
        result = normalizer.normalize(32)
        assert result.normalized_score == pytest.approx(0.5)
        assert result.pct_of_ceiling == pytest.approx(50.0)
        assert result.raw_score == 32

    def test_floor_equals_ceiling(self) -> None:
        """When floor equals ceiling, normalized score should be 0."""
        normalizer = ScenarioNormalizer(score_floor=5, score_ceiling=5)
        result = normalizer.normalize(5)
        assert result.normalized_score == 0.0

    def test_score_below_floor_clamps_to_zero(self) -> None:
        normalizer = ScenarioNormalizer(score_floor=0, score_ceiling=100)
        result = normalizer.normalize(-10)
        assert result.normalized_score == 0.0

    def test_score_above_ceiling_clamps_to_one(self) -> None:
        normalizer = ScenarioNormalizer(score_floor=0, score_ceiling=100)
        result = normalizer.normalize(150)
        assert result.normalized_score == 1.0
        assert result.pct_of_ceiling == 100.0

    def test_negative_floor(self) -> None:
        """Support negative floors (e.g., score range [-1, 1])."""
        normalizer = ScenarioNormalizer(score_floor=-1, score_ceiling=1)
        result = normalizer.normalize(0)
        assert result.normalized_score == pytest.approx(0.5)

    def test_preserves_raw_score(self) -> None:
        normalizer = ScenarioNormalizer(score_floor=0, score_ceiling=10)
        result = normalizer.normalize(7)
        assert result.raw_score == 7


# ---------------------------------------------------------------------------
# compute_normalized_progress (from trajectory rows)
# ---------------------------------------------------------------------------

class TestComputeNormalizedProgress:
    def test_empty_trajectory(self) -> None:
        result = compute_normalized_progress([], normalizer=ScenarioNormalizer())
        assert result.raw_score == 0.0
        assert result.normalized_score == 0.0

    def test_uses_last_best_score(self) -> None:
        trajectory = [
            {"generation_index": 0, "best_score": 0.3, "gate_decision": "advance"},
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance"},
            {"generation_index": 2, "best_score": 0.8, "gate_decision": "advance"},
        ]
        result = compute_normalized_progress(trajectory, normalizer=ScenarioNormalizer())
        assert result.raw_score == pytest.approx(0.8)
        assert result.normalized_score == pytest.approx(0.8)

    def test_custom_normalizer(self) -> None:
        trajectory = [
            {"generation_index": 0, "best_score": 32, "gate_decision": "advance"},
        ]
        normalizer = ScenarioNormalizer(score_floor=0, score_ceiling=64)
        result = compute_normalized_progress(trajectory, normalizer=normalizer)
        assert result.normalized_score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# compute_cost_efficiency (from role metrics and trajectory)
# ---------------------------------------------------------------------------

class TestComputeCostEfficiency:
    def test_empty_inputs(self) -> None:
        result = compute_cost_efficiency(role_metrics=[], trajectory=[], consultation_cost=0.0)
        assert result.total_tokens == 0
        assert result.tokens_per_advance == 0
        assert result.cost_per_advance == 0.0

    def test_basic_computation(self) -> None:
        role_metrics = [
            {"model": "claude-sonnet-4-5-20250929", "input_tokens": 1000, "output_tokens": 500},
            {"model": "claude-sonnet-4-5-20250929", "input_tokens": 2000, "output_tokens": 1000},
            {"model": "claude-sonnet-4-5-20250929", "input_tokens": 1500, "output_tokens": 800},
        ]
        trajectory = [
            {"generation_index": 0, "best_score": 0.3, "delta": 0.3, "gate_decision": "advance"},
            {"generation_index": 1, "best_score": 0.3, "delta": 0.0, "gate_decision": "rollback"},
            {"generation_index": 2, "best_score": 0.5, "delta": 0.2, "gate_decision": "advance"},
        ]
        result = compute_cost_efficiency(role_metrics=role_metrics, trajectory=trajectory)
        assert result.total_input_tokens == 4500
        assert result.total_output_tokens == 2300
        assert result.total_tokens == 6800
        # 2 advances, so tokens_per_advance = 6800 / 2 = 3400
        assert result.tokens_per_advance == 3400
        assert result.total_cost_usd == pytest.approx(0.048)

    def test_no_advances(self) -> None:
        """When no advances, tokens_per_advance should be 0."""
        role_metrics = [{"model": "claude-sonnet-4-5-20250929", "input_tokens": 1000, "output_tokens": 500}]
        trajectory = [
            {"generation_index": 0, "best_score": 0.3, "delta": 0.0, "gate_decision": "rollback"},
        ]
        result = compute_cost_efficiency(role_metrics=role_metrics, trajectory=trajectory)
        assert result.tokens_per_advance == 0

    def test_tokens_per_score_point(self) -> None:
        """Tokens per net score point gained."""
        role_metrics = [
            {"model": "claude-sonnet-4-5-20250929", "input_tokens": 5000, "output_tokens": 2000},
        ]
        trajectory = [
            {"generation_index": 0, "best_score": 0.2, "delta": 0.2, "gate_decision": "advance"},
            {"generation_index": 1, "best_score": 0.7, "delta": 0.5, "gate_decision": "advance"},
        ]
        result = compute_cost_efficiency(role_metrics=role_metrics, trajectory=trajectory)
        # Net score gain = 0.7 (last best_score) - 0.0 (initial) = 0.7
        # But computed from first.best_score and last.best_score delta
        # total_tokens = 7000, net_gain = 0.7 - 0.2 + 0.2 = 0.7
        # Actually first best_score = 0.2 so net = 0.7 - 0 = 0.7 (from trajectory start score)
        assert result.total_tokens == 7000
        # tokens_per_score_point = 7000 / 0.7 = 10000
        assert result.tokens_per_score_point == 10000

    def test_no_score_gain(self) -> None:
        """When no score improvement, tokens_per_score_point = 0."""
        role_metrics = [{"model": "claude-sonnet-4-5-20250929", "input_tokens": 1000, "output_tokens": 500}]
        trajectory = [
            {"generation_index": 0, "best_score": 0.5, "delta": 0.0, "gate_decision": "rollback"},
        ]
        result = compute_cost_efficiency(role_metrics=role_metrics, trajectory=trajectory)
        assert result.tokens_per_score_point == 0

    def test_consultation_cost_included(self) -> None:
        result = compute_cost_efficiency(
            role_metrics=[],
            trajectory=[],
            consultation_cost=0.05,
        )
        assert result.total_cost_usd == pytest.approx(0.05)

    def test_role_cost_and_consultation_cost_are_combined(self) -> None:
        result = compute_cost_efficiency(
            role_metrics=[
                {
                    "model": "claude-sonnet-4-5-20250929",
                    "input_tokens": 1000,
                    "output_tokens": 1000,
                    "latency_ms": 10,
                }
            ],
            trajectory=[
                {"generation_index": 0, "best_score": 0.4, "delta": 0.4, "gate_decision": "advance"},
            ],
            consultation_cost=0.01,
        )
        assert result.total_cost_usd == pytest.approx(0.028)


# ---------------------------------------------------------------------------
# RunProgressReport
# ---------------------------------------------------------------------------

class TestRunProgressReport:
    def test_construction(self) -> None:
        report = RunProgressReport(
            run_id="run_1",
            scenario="grid_ctf",
            total_generations=5,
            advances=3,
            rollbacks=1,
            retries=1,
            progress=NormalizedProgress(raw_score=0.8, normalized_score=0.8, pct_of_ceiling=80.0),
            cost=CostEfficiency(total_tokens=10000),
        )
        assert report.run_id == "run_1"
        assert report.advances == 3

    def test_to_dict(self) -> None:
        report = RunProgressReport(
            run_id="run_1",
            scenario="grid_ctf",
            total_generations=2,
            advances=1,
            rollbacks=1,
            retries=0,
            progress=NormalizedProgress(raw_score=0.5, normalized_score=0.5, pct_of_ceiling=50.0),
            cost=CostEfficiency(total_tokens=5000),
        )
        d = report.to_dict()
        assert d["run_id"] == "run_1"
        assert d["scenario"] == "grid_ctf"
        assert d["progress"]["normalized_score"] == 0.5
        assert d["cost"]["total_tokens"] == 5000

    def test_from_dict_roundtrip(self) -> None:
        original = RunProgressReport(
            run_id="r2",
            scenario="othello",
            total_generations=10,
            advances=6,
            rollbacks=3,
            retries=1,
            progress=NormalizedProgress(raw_score=0.9, normalized_score=0.9, pct_of_ceiling=90.0),
            cost=CostEfficiency(total_tokens=20000, cost_per_advance=0.01),
        )
        restored = RunProgressReport.from_dict(original.to_dict())
        assert restored.run_id == original.run_id
        assert restored.advances == original.advances
        assert restored.progress.normalized_score == original.progress.normalized_score
        assert restored.cost.total_tokens == original.cost.total_tokens

    def test_to_markdown(self) -> None:
        report = RunProgressReport(
            run_id="run_1",
            scenario="grid_ctf",
            total_generations=5,
            advances=3,
            rollbacks=1,
            retries=1,
            progress=NormalizedProgress(
                raw_score=0.8,
                normalized_score=0.8,
                score_floor=0.0,
                score_ceiling=1.0,
                pct_of_ceiling=80.0,
            ),
            cost=CostEfficiency(
                total_input_tokens=8000,
                total_output_tokens=4000,
                total_tokens=12000,
                total_cost_usd=0.04,
                tokens_per_advance=4000,
                cost_per_advance=0.0133,
                tokens_per_score_point=15000,
            ),
        )
        md = report.to_markdown()
        assert "run_1" in md
        assert "grid_ctf" in md
        assert "80.0%" in md
        assert "12,000" in md or "12000" in md
        assert "advance" in md.lower()

    def test_to_markdown_zero_cost(self) -> None:
        """Report with zero cost should still render cleanly."""
        report = RunProgressReport(
            run_id="r2",
            scenario="othello",
            total_generations=0,
            advances=0,
            rollbacks=0,
            retries=0,
            progress=NormalizedProgress(raw_score=0.0, normalized_score=0.0),
            cost=CostEfficiency(),
        )
        md = report.to_markdown()
        assert "othello" in md


# ---------------------------------------------------------------------------
# generate_run_progress_report (integration)
# ---------------------------------------------------------------------------

class TestGenerateRunProgressReport:
    def test_empty_trajectory(self) -> None:
        report = generate_run_progress_report(
            run_id="run_empty",
            scenario="grid_ctf",
            trajectory=[],
            role_metrics=[],
        )
        assert report.total_generations == 0
        assert report.progress.normalized_score == 0.0
        assert report.cost.total_tokens == 0

    def test_basic_report(self) -> None:
        trajectory = [
            {"generation_index": 0, "best_score": 0.3, "delta": 0.3, "gate_decision": "advance"},
            {"generation_index": 1, "best_score": 0.3, "delta": 0.0, "gate_decision": "rollback"},
            {"generation_index": 2, "best_score": 0.6, "delta": 0.3, "gate_decision": "advance"},
        ]
        role_metrics = [
            {"input_tokens": 1000, "output_tokens": 500},
            {"input_tokens": 2000, "output_tokens": 1000},
        ]
        report = generate_run_progress_report(
            run_id="run_basic",
            scenario="grid_ctf",
            trajectory=trajectory,
            role_metrics=role_metrics,
        )
        assert report.total_generations == 3
        assert report.advances == 2
        assert report.rollbacks == 1
        assert report.progress.raw_score == pytest.approx(0.6)
        assert report.cost.total_tokens == 4500

    def test_custom_normalizer(self) -> None:
        trajectory = [
            {"generation_index": 0, "best_score": 32, "delta": 32, "gate_decision": "advance"},
        ]
        normalizer = ScenarioNormalizer(score_floor=0, score_ceiling=64)
        report = generate_run_progress_report(
            run_id="custom",
            scenario="othello",
            trajectory=trajectory,
            role_metrics=[],
            normalizer=normalizer,
        )
        assert report.progress.normalized_score == pytest.approx(0.5)

    def test_consultation_cost_included(self) -> None:
        report = generate_run_progress_report(
            run_id="consult",
            scenario="grid_ctf",
            trajectory=[
                {"generation_index": 0, "best_score": 0.5, "delta": 0.5, "gate_decision": "advance"},
            ],
            role_metrics=[{"model": "claude-sonnet-4-5-20250929", "input_tokens": 1000, "output_tokens": 500}],
            consultation_cost=0.10,
        )
        assert report.cost.total_cost_usd == pytest.approx(0.1105)


# ---------------------------------------------------------------------------
# ArtifactStore integration
# ---------------------------------------------------------------------------

class TestArtifactStoreNormalizedMetrics:
    @pytest.fixture()
    def store(self, tmp_path: Path) -> object:
        from autocontext.storage.artifacts import ArtifactStore

        return ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

    def test_write_and_read_progress_report(self, store: object) -> None:
        from autocontext.storage.artifacts import ArtifactStore

        assert isinstance(store, ArtifactStore)

        report = RunProgressReport(
            run_id="run_1",
            scenario="grid_ctf",
            total_generations=5,
            advances=3,
            rollbacks=1,
            retries=1,
            progress=NormalizedProgress(raw_score=0.8, normalized_score=0.8, pct_of_ceiling=80.0),
            cost=CostEfficiency(total_tokens=10000),
        )
        store.write_progress_report("grid_ctf", "run_1", report)
        restored = store.read_progress_report("grid_ctf", "run_1")
        assert restored is not None
        assert isinstance(restored, RunProgressReport)
        assert restored.run_id == "run_1"
        assert restored.progress.normalized_score == pytest.approx(0.8)

    def test_read_missing_progress_report(self, store: object) -> None:
        from autocontext.storage.artifacts import ArtifactStore

        assert isinstance(store, ArtifactStore)
        result = store.read_progress_report("grid_ctf", "nonexistent")
        assert result is None

    def test_read_latest_progress_reports(self, store: object) -> None:
        from autocontext.storage.artifacts import ArtifactStore

        assert isinstance(store, ArtifactStore)
        for i in range(3):
            report = RunProgressReport(
                run_id=f"run_{i}",
                scenario="grid_ctf",
                total_generations=i + 1,
                advances=i,
                rollbacks=0,
                retries=0,
                progress=NormalizedProgress(
                    raw_score=0.2 * (i + 1),
                    normalized_score=0.2 * (i + 1),
                    pct_of_ceiling=20.0 * (i + 1),
                ),
                cost=CostEfficiency(total_tokens=1000 * (i + 1)),
            )
            store.write_progress_report("grid_ctf", f"run_{i}", report)

        latest = store.read_latest_progress_reports("grid_ctf", max_reports=2)
        assert len(latest) == 2

    def test_progress_report_to_markdown(self, store: object) -> None:
        from autocontext.storage.artifacts import ArtifactStore

        assert isinstance(store, ArtifactStore)
        report = RunProgressReport(
            run_id="run_md",
            scenario="grid_ctf",
            total_generations=3,
            advances=2,
            rollbacks=1,
            retries=0,
            progress=NormalizedProgress(raw_score=0.6, normalized_score=0.6, pct_of_ceiling=60.0),
            cost=CostEfficiency(total_tokens=5000, cost_per_advance=0.01),
        )
        store.write_progress_report("grid_ctf", "run_md", report)
        md = store.read_latest_progress_reports_markdown("grid_ctf", max_reports=1)
        assert "run_md" in md
        assert "60.0%" in md
