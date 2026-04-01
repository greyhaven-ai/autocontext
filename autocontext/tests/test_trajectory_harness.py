"""Tests for AC-284: multi-seed trajectory test harness for knowledge-heavy domains.

Covers: MultiSeedTrajectoryRunner, TrajectoryReport, PlaybookInspector,
validate_improvement, TrajectoryComparison.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_trajectory(
    scores: list[float],
    task_name: str = "test_task",
) -> Any:
    from autocontext.execution.agent_task_evolution import AgentTaskTrajectory

    return AgentTaskTrajectory(
        task_name=task_name,
        total_generations=len(scores),
        score_history=scores,
        lessons_per_generation=[1] * len(scores),
        cold_start_score=scores[0] if scores else 0.0,
        final_score=scores[-1] if scores else 0.0,
        improvement_delta=round((scores[-1] - scores[0]) if scores else 0.0, 4),
    )


# ===========================================================================
# PlaybookInspector
# ===========================================================================


class TestPlaybookInspector:
    def test_snapshots_at_key_points(self) -> None:
        from autocontext.execution.trajectory_harness import PlaybookInspector

        playbooks_by_gen = {
            0: "",
            1: "Lesson 1: be specific",
            2: "Lesson 1: be specific\nLesson 2: cite sources",
            3: "Lesson 1: be specific\nLesson 2: cite sources\nLesson 3: add examples",
            4: "Full playbook at gen 5",
        }
        inspector = PlaybookInspector(playbooks_by_gen, total_generations=5)
        snapshots = inspector.key_snapshots()

        assert "gen_1" in snapshots
        assert "midpoint" in snapshots
        assert "final" in snapshots
        assert snapshots["gen_1"] == ""

    def test_midpoint_calculation(self) -> None:
        from autocontext.execution.trajectory_harness import PlaybookInspector

        playbooks = {i: f"playbook at gen {i}" for i in range(10)}
        inspector = PlaybookInspector(playbooks, total_generations=10)
        snapshots = inspector.key_snapshots()

        assert snapshots["midpoint"] == "playbook at gen 4"

    def test_growth_summary(self) -> None:
        from autocontext.execution.trajectory_harness import PlaybookInspector

        playbooks = {
            0: "",
            1: "Line 1",
            2: "Line 1\nLine 2\nLine 3",
        }
        inspector = PlaybookInspector(playbooks, total_generations=3)
        summary = inspector.growth_summary()

        assert "gen_1" in summary
        assert "final" in summary


# ===========================================================================
# TrajectoryComparison
# ===========================================================================


class TestTrajectoryComparison:
    def test_construction(self) -> None:
        from autocontext.execution.trajectory_harness import TrajectoryComparison

        comp = TrajectoryComparison(
            task_name="test",
            num_seeds=3,
            num_generations=5,
            mean_cold_start=0.45,
            mean_final=0.78,
            mean_improvement=0.33,
            std_improvement=0.05,
            per_seed_improvements=[0.30, 0.35, 0.34],
            consistent=True,
        )
        assert comp.consistent is True
        assert comp.mean_improvement == 0.33

    def test_roundtrip(self) -> None:
        from autocontext.execution.trajectory_harness import TrajectoryComparison

        comp = TrajectoryComparison(
            task_name="test",
            num_seeds=2,
            num_generations=3,
            mean_cold_start=0.5,
            mean_final=0.7,
            mean_improvement=0.2,
            std_improvement=0.02,
            per_seed_improvements=[0.19, 0.21],
            consistent=True,
        )
        d = comp.to_dict()
        restored = TrajectoryComparison.from_dict(d)
        assert restored.mean_improvement == 0.2
        assert restored.consistent is True

    def test_summary(self) -> None:
        from autocontext.execution.trajectory_harness import TrajectoryComparison

        comp = TrajectoryComparison(
            task_name="clinical_trial",
            num_seeds=5,
            num_generations=10,
            mean_cold_start=0.42,
            mean_final=0.81,
            mean_improvement=0.39,
            std_improvement=0.04,
            per_seed_improvements=[0.38, 0.40, 0.37, 0.41, 0.39],
            consistent=True,
        )
        summary = comp.summary()
        assert "0.42" in summary or "0.4" in summary
        assert "0.81" in summary or "0.8" in summary
        assert "consistent" in summary.lower()


# ===========================================================================
# validate_improvement
# ===========================================================================


class TestValidateImprovement:
    def test_consistent_positive_improvements(self) -> None:
        from autocontext.execution.trajectory_harness import validate_improvement

        improvements = [0.15, 0.18, 0.12, 0.20, 0.16]
        result = validate_improvement(improvements, min_delta=0.05)
        assert result["valid"] is True
        assert result["mean_improvement"] > 0.1

    def test_inconsistent_improvements(self) -> None:
        from autocontext.execution.trajectory_harness import validate_improvement

        improvements = [0.30, -0.05, 0.25, -0.10, 0.20]
        result = validate_improvement(improvements, min_delta=0.05)
        assert result["valid"] is False

    def test_all_below_threshold(self) -> None:
        from autocontext.execution.trajectory_harness import validate_improvement

        improvements = [0.01, 0.02, 0.01, 0.03, 0.01]
        result = validate_improvement(improvements, min_delta=0.05)
        assert result["valid"] is False

    def test_empty_improvements(self) -> None:
        from autocontext.execution.trajectory_harness import validate_improvement

        result = validate_improvement([], min_delta=0.05)
        assert result["valid"] is False

    def test_single_seed(self) -> None:
        from autocontext.execution.trajectory_harness import validate_improvement

        result = validate_improvement([0.25], min_delta=0.05)
        assert result["valid"] is False
        assert "at least 2 seeds" in result["reason"]


# ===========================================================================
# TrajectoryReport
# ===========================================================================


class TestTrajectoryReport:
    def test_construction(self) -> None:
        from autocontext.execution.trajectory_harness import TrajectoryReport

        t1 = _make_trajectory([0.4, 0.55, 0.65])
        t2 = _make_trajectory([0.45, 0.60, 0.70])

        report = TrajectoryReport(
            task_name="test_task",
            trajectories=[t1, t2],
            num_seeds=2,
            num_generations=3,
        )
        assert report.num_seeds == 2
        assert len(report.trajectories) == 2
        assert report.metadata == {}

    def test_mean_scores_per_generation(self) -> None:
        from autocontext.execution.trajectory_harness import TrajectoryReport

        t1 = _make_trajectory([0.4, 0.6, 0.8])
        t2 = _make_trajectory([0.5, 0.7, 0.9])

        report = TrajectoryReport(
            task_name="test",
            trajectories=[t1, t2],
            num_seeds=2,
            num_generations=3,
        )
        means = report.mean_scores_per_generation()
        assert len(means) == 3
        assert abs(means[0] - 0.45) < 0.01
        assert abs(means[1] - 0.65) < 0.01
        assert abs(means[2] - 0.85) < 0.01

    def test_comparison(self) -> None:
        from autocontext.execution.trajectory_harness import TrajectoryReport

        t1 = _make_trajectory([0.4, 0.6, 0.8])
        t2 = _make_trajectory([0.5, 0.65, 0.82])

        report = TrajectoryReport(
            task_name="test",
            trajectories=[t1, t2],
            num_seeds=2,
            num_generations=3,
        )
        comparison = report.compare()
        assert comparison.num_seeds == 2
        assert comparison.mean_improvement > 0


# ===========================================================================
# MultiSeedTrajectoryRunner
# ===========================================================================


class TestMultiSeedTrajectoryRunner:
    def test_runs_multiple_seeds(self) -> None:
        from autocontext.execution.trajectory_harness import MultiSeedTrajectoryRunner

        call_seeds: list[int] = []
        prompt_log: list[str] = []

        def mock_generate(prompt: str, generation: int, seed: int) -> str:
            prompt_log.append(prompt)
            return f"seed={seed} generation={generation}"

        def mock_evaluate(output: str, generation: int, seed: int) -> tuple[float, str, dict[str, float]]:
            call_seeds.append(seed)
            return 0.5 if "Accumulated Lessons" not in output else 0.8, f"Gen {generation} seed {seed}", {}

        runner = MultiSeedTrajectoryRunner(
            task_prompt="Write a report.",
            generate_fn=mock_generate,
            evaluate_fn=mock_evaluate,
            task_name="test_task",
        )
        report = runner.run(num_seeds=3, num_generations=2)

        assert report.num_seeds == 3
        assert len(report.trajectories) == 3
        assert len(set(call_seeds)) >= 2  # Different seeds used
        assert prompt_log

    def test_trajectory_report_has_correct_structure(self) -> None:
        from autocontext.execution.trajectory_harness import MultiSeedTrajectoryRunner

        def mock_generate(prompt: str, generation: int, seed: int) -> str:
            if "Accumulated Lessons" in prompt:
                return f"improved answer for seed {seed}"
            return f"baseline answer for seed {seed}"

        def mock_evaluate(output: str, generation: int, seed: int) -> tuple[float, str, dict[str, float]]:
            if output.startswith("improved answer"):
                return 0.85, "feedback", {}
            return 0.45, "feedback", {}

        runner = MultiSeedTrajectoryRunner(
            task_prompt="Task.",
            generate_fn=mock_generate,
            evaluate_fn=mock_evaluate,
            task_name="multi_seed_test",
        )
        report = runner.run(num_seeds=2, num_generations=5)

        assert report.task_name == "multi_seed_test"
        assert report.num_generations == 5
        for traj in report.trajectories:
            assert len(traj.score_history) == 5
            assert traj.final_score > traj.cold_start_score

    def test_playbook_inspector_available(self) -> None:
        from autocontext.execution.trajectory_harness import MultiSeedTrajectoryRunner

        def mock_generate(prompt: str, generation: int, seed: int) -> str:
            return "baseline" if "Accumulated Lessons" not in prompt else "improved"

        def mock_evaluate(output: str, generation: int, seed: int) -> tuple[float, str, dict[str, float]]:
            score = 0.6 if output == "baseline" else 0.75
            return score, "feedback", {"depth": 0.4}

        runner = MultiSeedTrajectoryRunner(
            task_prompt="Task.",
            generate_fn=mock_generate,
            evaluate_fn=mock_evaluate,
        )
        report = runner.run(num_seeds=2, num_generations=3)

        # Should have playbook data for inspection
        assert report.num_seeds == 2
        assert report.trajectories[0].total_generations == 3
        assert "playbook_inspection" in report.metadata
        assert report.metadata["playbook_inspection"]

    def test_enriched_prompt_drives_improvement_without_generation_cheat(self) -> None:
        from autocontext.execution.trajectory_harness import MultiSeedTrajectoryRunner

        prompts: list[str] = []

        def mock_generate(prompt: str, generation: int, seed: int) -> str:
            prompts.append(prompt)
            return "improved with playbook" if "Accumulated Lessons" in prompt else "first draft"

        def mock_evaluate(output: str, generation: int, seed: int) -> tuple[float, str, dict[str, float]]:
            if output == "improved with playbook":
                return 0.9, "Used lessons well", {"depth": 0.9}
            return 0.4, "Needs more depth", {"depth": 0.4}

        runner = MultiSeedTrajectoryRunner(
            task_prompt="Analyze the protocol risks.",
            generate_fn=mock_generate,
            evaluate_fn=mock_evaluate,
            task_name="clinical_trial",
        )
        report = runner.run(num_seeds=2, num_generations=3)

        assert any("Accumulated Lessons" in prompt for prompt in prompts[1:])
        for trajectory in report.trajectories:
            assert trajectory.cold_start_score == 0.4
            assert trajectory.final_score == 0.9
            assert trajectory.improvement_delta == 0.5
