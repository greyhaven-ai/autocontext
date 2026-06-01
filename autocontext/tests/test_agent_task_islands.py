"""Tests for population/island mode in agent-task evolution.

Single-lineage evolution re-derives the same local optimum each
generation. Island mode runs K parallel lineages and periodically
migrates the global champion into lagging islands — sharing winners
while preserving per-island playbook diversity.
"""

from __future__ import annotations


def _state(best_output: str, best_score: float):
    from autocontext.execution.agent_task_evolution import AgentTaskGenerationState

    return AgentTaskGenerationState(
        generation=1,
        best_output=best_output,
        best_score=best_score,
        playbook=f"playbook-for-{best_output}",
        score_history=[best_score],
        lesson_history=["l"],
    )


class TestMigrateStates:
    def test_champion_copied_into_laggards(self) -> None:
        from autocontext.execution.agent_task_evolution import migrate_states

        states = [_state("champ", 0.9), _state("weak", 0.4), _state("mid", 0.6)]
        migrated = migrate_states(states)
        # every island now holds the champion's best output/score
        assert all(s.best_score == 0.9 for s in migrated)
        assert all(s.best_output == "champ" for s in migrated)

    def test_preserves_per_island_playbook(self) -> None:
        from autocontext.execution.agent_task_evolution import migrate_states

        states = [_state("champ", 0.9), _state("weak", 0.4)]
        migrated = migrate_states(states)
        # migration shares the champion artifact but NOT the playbook
        # (diversity in accumulated lessons is preserved)
        assert migrated[1].playbook == "playbook-for-weak"


class TestRunIslands:
    def test_returns_trajectory_with_global_best(self) -> None:
        from autocontext.execution.agent_task_evolution import (
            AgentTaskEvolutionRunner,
            AgentTaskGenerationEvaluation,
        )

        counter = {"n": 0}

        def gen(prompt: str, g: int) -> str:
            counter["n"] += 1
            return f"cand{counter['n']}"

        def ev(output: str, g: int) -> AgentTaskGenerationEvaluation:
            # score grows with the candidate index parsed from the name
            idx = int(output.replace("cand", "")) if output.startswith("cand") else 0
            return AgentTaskGenerationEvaluation(output=output, score=min(1.0, 0.1 * idx), reasoning="ok")

        runner = AgentTaskEvolutionRunner(task_prompt="t", generate_fn=gen, evaluate_fn=ev)
        traj = runner.run_islands(num_islands=3, num_generations=2)

        assert traj.total_generations == 2
        assert len(traj.score_history) == 2
        # final reported best equals the max best_score any island reached
        assert traj.metadata["num_islands"] == 3
        assert traj.metadata["best_score"] == max(traj.score_history)
        # score_history is the per-generation best across islands (non-decreasing)
        assert traj.score_history == sorted(traj.score_history)

    def test_rejects_zero_islands(self) -> None:
        import pytest

        from autocontext.execution.agent_task_evolution import (
            AgentTaskEvolutionRunner,
            AgentTaskGenerationEvaluation,
        )

        def gen(prompt: str, g: int) -> str:
            return "x"

        def ev(output: str, g: int) -> AgentTaskGenerationEvaluation:
            return AgentTaskGenerationEvaluation(output=output, score=0.5, reasoning="ok")

        runner = AgentTaskEvolutionRunner(task_prompt="t", generate_fn=gen, evaluate_fn=ev)
        with pytest.raises(ValueError, match="num_islands must be >= 1"):
            runner.run_islands(num_islands=0, num_generations=2)

    def test_migration_runs_without_error(self) -> None:
        from autocontext.execution.agent_task_evolution import (
            AgentTaskEvolutionRunner,
            AgentTaskGenerationEvaluation,
        )

        def gen(prompt: str, g: int) -> str:
            return "x"

        def ev(output: str, g: int) -> AgentTaskGenerationEvaluation:
            return AgentTaskGenerationEvaluation(output=output, score=0.5, reasoning="ok")

        runner = AgentTaskEvolutionRunner(task_prompt="t", generate_fn=gen, evaluate_fn=ev)
        traj = runner.run_islands(num_islands=2, num_generations=4, migrate_every=2)
        assert traj.total_generations == 4
