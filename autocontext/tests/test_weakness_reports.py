"""Tests for AC-196: Weakness reports and targeted probe scenario generation (Phase 1).

Verifies:
1. Weakness and WeaknessReport dataclass construction and serialization.
2. WeaknessAnalyzer detects score regressions, validation failures, match variance,
   stagnation risk, and dead-end patterns.
3. WeaknessReport markdown rendering for operator visibility.
4. Integration with ArtifactStore for persistence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. Weakness dataclass
# ---------------------------------------------------------------------------


class TestWeakness:
    def test_construction(self) -> None:
        from autocontext.knowledge.weakness import Weakness

        w = Weakness(
            category="score_regression",
            severity="high",
            affected_generations=[3, 5, 7],
            description="Score dropped below previous best in 3 generations",
            evidence={"delta_avg": -0.05, "worst_delta": -0.12},
        )
        assert w.category == "score_regression"
        assert w.severity == "high"
        assert w.affected_generations == [3, 5, 7]
        assert w.evidence["worst_delta"] == -0.12
        assert w.frequency == 0

    def test_construction_with_frequency(self) -> None:
        from autocontext.knowledge.weakness import Weakness

        w = Weakness(
            category="validation_failure",
            severity="medium",
            affected_generations=[2, 4],
            description="Validation errors in 2 of 5 generations",
            evidence={},
            frequency=2,
        )
        assert w.frequency == 2

    def test_to_dict_from_dict_roundtrip(self) -> None:
        from autocontext.knowledge.weakness import Weakness

        w = Weakness(
            category="match_variance",
            severity="low",
            affected_generations=[1, 2, 3],
            description="High score variance across matches",
            evidence={"std_dev": 0.15},
            frequency=3,
        )
        d = w.to_dict()
        assert isinstance(d, dict)
        restored = Weakness.from_dict(d)
        assert restored.category == w.category
        assert restored.severity == w.severity
        assert restored.affected_generations == w.affected_generations
        assert restored.evidence == w.evidence
        assert restored.frequency == w.frequency


# ---------------------------------------------------------------------------
# 2. WeaknessReport
# ---------------------------------------------------------------------------


class TestWeaknessReport:
    def test_construction_empty(self) -> None:
        from autocontext.knowledge.weakness import WeaknessReport

        report = WeaknessReport(
            run_id="test_run",
            scenario="grid_ctf",
            total_generations=5,
            weaknesses=[],
        )
        assert report.weaknesses == []
        assert report.total_generations == 5

    def test_construction_with_weaknesses(self) -> None:
        from autocontext.knowledge.weakness import Weakness, WeaknessReport

        weaknesses = [
            Weakness(
                category="score_regression",
                severity="high",
                affected_generations=[3],
                description="Score regression",
                evidence={},
            ),
            Weakness(
                category="validation_failure",
                severity="medium",
                affected_generations=[1, 4],
                description="Validation errors",
                evidence={},
                frequency=2,
            ),
        ]
        report = WeaknessReport(
            run_id="test_run",
            scenario="grid_ctf",
            total_generations=5,
            weaknesses=weaknesses,
        )
        assert len(report.weaknesses) == 2

    def test_to_markdown(self) -> None:
        from autocontext.knowledge.weakness import Weakness, WeaknessReport

        weaknesses = [
            Weakness(
                category="score_regression",
                severity="high",
                affected_generations=[3, 5],
                description="Recurring score drops after gen 2",
                evidence={"delta_avg": -0.05},
                frequency=2,
            ),
        ]
        report = WeaknessReport(
            run_id="test_run",
            scenario="grid_ctf",
            total_generations=5,
            weaknesses=weaknesses,
        )
        md = report.to_markdown()
        assert "# Weakness Report" in md
        assert "grid_ctf" in md
        assert "score_regression" in md
        assert "high" in md.lower()
        assert "Recurring score drops" in md

    def test_to_markdown_empty(self) -> None:
        from autocontext.knowledge.weakness import WeaknessReport

        report = WeaknessReport(
            run_id="test_run",
            scenario="grid_ctf",
            total_generations=5,
            weaknesses=[],
        )
        md = report.to_markdown()
        assert "no weakness" in md.lower() or "No weaknesses" in md

    def test_to_dict_from_dict_roundtrip(self) -> None:
        from autocontext.knowledge.weakness import Weakness, WeaknessReport

        report = WeaknessReport(
            run_id="test_run",
            scenario="grid_ctf",
            total_generations=5,
            weaknesses=[
                Weakness(
                    category="dead_end_pattern",
                    severity="medium",
                    affected_generations=[2, 4],
                    description="Repeated dead-end strategies",
                    evidence={"count": 2},
                ),
            ],
        )
        d = report.to_dict()
        restored = WeaknessReport.from_dict(d)
        assert restored.run_id == report.run_id
        assert restored.scenario == report.scenario
        assert restored.total_generations == report.total_generations
        assert len(restored.weaknesses) == 1
        assert restored.weaknesses[0].category == "dead_end_pattern"

    def test_high_severity_count(self) -> None:
        from autocontext.knowledge.weakness import Weakness, WeaknessReport

        report = WeaknessReport(
            run_id="r",
            scenario="s",
            total_generations=5,
            weaknesses=[
                Weakness(category="a", severity="high", affected_generations=[], description="", evidence={}),
                Weakness(category="b", severity="low", affected_generations=[], description="", evidence={}),
                Weakness(category="c", severity="high", affected_generations=[], description="", evidence={}),
            ],
        )
        assert report.high_severity_count == 2


# ---------------------------------------------------------------------------
# 3. WeaknessAnalyzer — score regression detection
# ---------------------------------------------------------------------------


class TestWeaknessAnalyzerScoreRegression:
    def test_detects_score_regression(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance", "delta": 0.0},
            {"generation_index": 2, "best_score": 0.6, "gate_decision": "advance", "delta": 0.1},
            {"generation_index": 3, "best_score": 0.4, "gate_decision": "rollback", "delta": -0.2},
            {"generation_index": 4, "best_score": 0.55, "gate_decision": "advance", "delta": 0.15},
            {"generation_index": 5, "best_score": 0.3, "gate_decision": "rollback", "delta": -0.25},
        ]
        report = analyzer.analyze(run_id="test", scenario="grid_ctf", trajectory=trajectory)
        regression = [w for w in report.weaknesses if w.category == "score_regression"]
        assert len(regression) == 1
        assert 3 in regression[0].affected_generations
        assert 5 in regression[0].affected_generations

    def test_no_regression_when_all_advance(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance", "delta": 0.0},
            {"generation_index": 2, "best_score": 0.6, "gate_decision": "advance", "delta": 0.1},
            {"generation_index": 3, "best_score": 0.7, "gate_decision": "advance", "delta": 0.1},
        ]
        report = analyzer.analyze(run_id="test", scenario="grid_ctf", trajectory=trajectory)
        regression = [w for w in report.weaknesses if w.category == "score_regression"]
        assert len(regression) == 0


# ---------------------------------------------------------------------------
# 4. WeaknessAnalyzer — validation failure detection
# ---------------------------------------------------------------------------


class TestWeaknessAnalyzerValidation:
    def test_detects_validation_failures(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance", "delta": 0.0},
            {"generation_index": 2, "best_score": 0.6, "gate_decision": "advance", "delta": 0.1},
        ]
        match_data = [
            {"generation_index": 1, "score": 0.5, "passed_validation": True, "validation_errors": "[]"},
            {"generation_index": 1, "score": 0.4, "passed_validation": False, "validation_errors": '["missing field X"]'},
            {"generation_index": 2, "score": 0.6, "passed_validation": True, "validation_errors": "[]"},
            {"generation_index": 2, "score": 0.3, "passed_validation": False, "validation_errors": '["missing field X"]'},
        ]
        report = analyzer.analyze(
            run_id="test", scenario="grid_ctf", trajectory=trajectory, match_data=match_data,
        )
        val_failures = [w for w in report.weaknesses if w.category == "validation_failure"]
        assert len(val_failures) == 1
        assert val_failures[0].frequency >= 2

    def test_no_validation_weakness_when_all_pass(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance", "delta": 0.0},
        ]
        match_data = [
            {"generation_index": 1, "score": 0.5, "passed_validation": True, "validation_errors": "[]"},
            {"generation_index": 1, "score": 0.6, "passed_validation": True, "validation_errors": "[]"},
        ]
        report = analyzer.analyze(
            run_id="test", scenario="grid_ctf", trajectory=trajectory, match_data=match_data,
        )
        val_failures = [w for w in report.weaknesses if w.category == "validation_failure"]
        assert len(val_failures) == 0


# ---------------------------------------------------------------------------
# 5. WeaknessAnalyzer — match variance detection
# ---------------------------------------------------------------------------


class TestWeaknessAnalyzerMatchVariance:
    def test_detects_high_match_variance(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.9, "gate_decision": "advance", "delta": 0.0},
        ]
        # Very high variance: scores 0.1 and 0.9 in the same generation
        match_data = [
            {"generation_index": 1, "score": 0.1, "passed_validation": True, "validation_errors": "[]"},
            {"generation_index": 1, "score": 0.9, "passed_validation": True, "validation_errors": "[]"},
        ]
        report = analyzer.analyze(
            run_id="test", scenario="grid_ctf", trajectory=trajectory, match_data=match_data,
        )
        variance = [w for w in report.weaknesses if w.category == "match_variance"]
        assert len(variance) == 1

    def test_no_variance_weakness_when_consistent(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance", "delta": 0.0},
        ]
        match_data = [
            {"generation_index": 1, "score": 0.50, "passed_validation": True, "validation_errors": "[]"},
            {"generation_index": 1, "score": 0.51, "passed_validation": True, "validation_errors": "[]"},
            {"generation_index": 1, "score": 0.49, "passed_validation": True, "validation_errors": "[]"},
        ]
        report = analyzer.analyze(
            run_id="test", scenario="grid_ctf", trajectory=trajectory, match_data=match_data,
        )
        variance = [w for w in report.weaknesses if w.category == "match_variance"]
        assert len(variance) == 0


# ---------------------------------------------------------------------------
# 6. WeaknessAnalyzer — stagnation risk detection
# ---------------------------------------------------------------------------


class TestWeaknessAnalyzerStagnation:
    def test_detects_stagnation_risk(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": i, "best_score": 0.5, "gate_decision": "rollback", "delta": -0.01}
            for i in range(1, 6)
        ]
        report = analyzer.analyze(run_id="test", scenario="grid_ctf", trajectory=trajectory)
        stagnation = [w for w in report.weaknesses if w.category == "stagnation_risk"]
        assert len(stagnation) == 1
        assert stagnation[0].severity == "high"

    def test_no_stagnation_when_advancing(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance", "delta": 0.1},
            {"generation_index": 2, "best_score": 0.6, "gate_decision": "advance", "delta": 0.1},
            {"generation_index": 3, "best_score": 0.7, "gate_decision": "advance", "delta": 0.1},
        ]
        report = analyzer.analyze(run_id="test", scenario="grid_ctf", trajectory=trajectory)
        stagnation = [w for w in report.weaknesses if w.category == "stagnation_risk"]
        assert len(stagnation) == 0


# ---------------------------------------------------------------------------
# 7. WeaknessAnalyzer — dead-end pattern detection
# ---------------------------------------------------------------------------


class TestWeaknessAnalyzerDeadEnds:
    def test_detects_dead_end_pattern(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance", "delta": 0.0},
            {"generation_index": 2, "best_score": 0.4, "gate_decision": "rollback", "delta": -0.1},
            {"generation_index": 3, "best_score": 0.6, "gate_decision": "advance", "delta": 0.1},
            {"generation_index": 4, "best_score": 0.3, "gate_decision": "rollback", "delta": -0.3},
            {"generation_index": 5, "best_score": 0.7, "gate_decision": "advance", "delta": 0.1},
            {"generation_index": 6, "best_score": 0.35, "gate_decision": "rollback", "delta": -0.35},
        ]
        report = analyzer.analyze(run_id="test", scenario="grid_ctf", trajectory=trajectory)
        dead_ends = [w for w in report.weaknesses if w.category == "dead_end_pattern"]
        assert len(dead_ends) == 1
        assert dead_ends[0].frequency >= 3

    def test_no_dead_ends_when_no_rollbacks(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance", "delta": 0.1},
            {"generation_index": 2, "best_score": 0.6, "gate_decision": "advance", "delta": 0.1},
        ]
        report = analyzer.analyze(run_id="test", scenario="grid_ctf", trajectory=trajectory)
        dead_ends = [w for w in report.weaknesses if w.category == "dead_end_pattern"]
        assert len(dead_ends) == 0


# ---------------------------------------------------------------------------
# 8. WeaknessAnalyzer — empty / minimal input
# ---------------------------------------------------------------------------


class TestWeaknessAnalyzerEdgeCases:
    def test_empty_trajectory(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        report = analyzer.analyze(run_id="test", scenario="grid_ctf", trajectory=[])
        assert report.weaknesses == []
        assert report.total_generations == 0

    def test_single_generation(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": 1, "best_score": 0.5, "gate_decision": "advance", "delta": 0.0},
        ]
        report = analyzer.analyze(run_id="test", scenario="grid_ctf", trajectory=trajectory)
        # Should not crash; may or may not have weaknesses
        assert isinstance(report.weaknesses, list)

    def test_analyze_returns_correct_metadata(self) -> None:
        from autocontext.knowledge.weakness import WeaknessAnalyzer

        analyzer = WeaknessAnalyzer()
        trajectory = [
            {"generation_index": i, "best_score": 0.5, "gate_decision": "advance", "delta": 0.0}
            for i in range(1, 4)
        ]
        report = analyzer.analyze(run_id="run_42", scenario="othello", trajectory=trajectory)
        assert report.run_id == "run_42"
        assert report.scenario == "othello"
        assert report.total_generations == 3


# ---------------------------------------------------------------------------
# 9. ArtifactStore integration — persist and read weakness reports
# ---------------------------------------------------------------------------


class TestArtifactStoreWeaknessIntegration:
    @pytest.fixture()
    def artifact_store(self, tmp_path: Path):
        from autocontext.storage.artifacts import ArtifactStore

        return ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

    def test_persist_weakness_report(self, artifact_store) -> None:
        from autocontext.knowledge.weakness import Weakness, WeaknessReport

        report = WeaknessReport(
            run_id="test_run",
            scenario="grid_ctf",
            total_generations=5,
            weaknesses=[
                Weakness(
                    category="score_regression",
                    severity="high",
                    affected_generations=[3],
                    description="Score dropped",
                    evidence={"delta": -0.1},
                ),
            ],
        )
        artifact_store.write_weakness_report("grid_ctf", "test_run", report)

    def test_read_weakness_report(self, artifact_store) -> None:
        from autocontext.knowledge.weakness import Weakness, WeaknessReport

        report = WeaknessReport(
            run_id="test_run",
            scenario="grid_ctf",
            total_generations=5,
            weaknesses=[
                Weakness(
                    category="score_regression",
                    severity="high",
                    affected_generations=[3],
                    description="Score dropped",
                    evidence={"delta": -0.1},
                ),
            ],
        )
        artifact_store.write_weakness_report("grid_ctf", "test_run", report)
        restored = artifact_store.read_weakness_report("grid_ctf", "test_run")
        assert restored is not None
        assert restored.run_id == "test_run"
        assert len(restored.weaknesses) == 1

    def test_read_missing_report_returns_none(self, artifact_store) -> None:
        result = artifact_store.read_weakness_report("grid_ctf", "nonexistent")
        assert result is None

    def test_read_latest_weakness_reports(self, artifact_store) -> None:
        from autocontext.knowledge.weakness import WeaknessReport

        for i in range(3):
            report = WeaknessReport(
                run_id=f"run_{i}",
                scenario="grid_ctf",
                total_generations=5,
                weaknesses=[],
            )
            artifact_store.write_weakness_report("grid_ctf", f"run_{i}", report)

        latest = artifact_store.read_latest_weakness_reports("grid_ctf", max_reports=2)
        assert len(latest) == 2

    def test_read_latest_weakness_reports_markdown(self, artifact_store) -> None:
        from autocontext.knowledge.weakness import Weakness, WeaknessReport

        report = WeaknessReport(
            run_id="run_md",
            scenario="grid_ctf",
            total_generations=4,
            weaknesses=[
                Weakness(
                    category="dead_end_pattern",
                    severity="high",
                    affected_generations=[2, 4],
                    description="Repeated rollbacks detected",
                    evidence={"rollback_ratio": 0.5},
                ),
            ],
        )
        artifact_store.write_weakness_report("grid_ctf", "run_md", report)

        markdown = artifact_store.read_latest_weakness_reports_markdown("grid_ctf")
        assert "# Weakness Report: run_md" in markdown
        assert "dead_end_pattern" in markdown
