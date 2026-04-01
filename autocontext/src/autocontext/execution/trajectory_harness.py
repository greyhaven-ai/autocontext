"""Multi-seed trajectory test harness for knowledge-heavy domains (AC-284).

Runs AgentTaskEvolutionRunner across multiple seeds, captures per-generation
trajectories, inspects playbook growth at key points, and validates that
improvement is consistent rather than one-off.

Key types:
- PlaybookInspector: snapshot playbook at gen 1, midpoint, final
- TrajectoryComparison: cross-seed improvement statistics
- TrajectoryReport: aggregates trajectories with mean-score computation
- MultiSeedTrajectoryRunner: orchestrates runs across seeds
- validate_improvement(): checks improvements are consistent across seeds
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from autocontext.execution.agent_task_evolution import (
    AgentTaskEvolutionRunner,
    AgentTaskGenerationEvaluation,
    AgentTaskTrajectory,
)


class PlaybookInspector:
    """Inspects playbook state at key points in a generation trajectory."""

    def __init__(
        self,
        playbooks_by_gen: dict[int, str],
        total_generations: int,
    ) -> None:
        self._playbooks = playbooks_by_gen
        self._total = total_generations

    def key_snapshots(self) -> dict[str, str]:
        """Return playbook snapshots at gen 1, midpoint, and final."""
        midpoint_gen = max(0, (self._total - 1) // 2)
        final_gen = max(0, self._total - 1)
        return {
            "gen_1": self._playbooks.get(0, ""),
            "midpoint": self._playbooks.get(midpoint_gen, ""),
            "final": self._playbooks.get(final_gen, ""),
        }

    def growth_summary(self) -> dict[str, int]:
        """Return character count of playbook at each key point."""
        snapshots = self.key_snapshots()
        return {key: len(value) for key, value in snapshots.items()}


class TrajectoryComparison(BaseModel):
    """Cross-seed improvement statistics."""

    task_name: str
    num_seeds: int
    num_generations: int
    mean_cold_start: float
    mean_final: float
    mean_improvement: float
    std_improvement: float
    per_seed_improvements: list[float]
    consistent: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"Task: {self.task_name}",
            f"Seeds: {self.num_seeds}, Generations: {self.num_generations}",
            f"Mean cold-start: {self.mean_cold_start:.2f}",
            f"Mean final: {self.mean_final:.2f}",
            f"Mean improvement: +{self.mean_improvement:.2f} (std: {self.std_improvement:.3f})",
            f"Consistent: {'yes' if self.consistent else 'no'}",
            f"Per-seed: {', '.join(f'+{d:.2f}' for d in self.per_seed_improvements)}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrajectoryComparison:
        return cls.model_validate(data)


def validate_improvement(
    improvements: list[float],
    min_delta: float = 0.05,
) -> dict[str, Any]:
    """Check that improvements across seeds are consistent and above threshold.

    Returns dict with valid (bool), mean_improvement, reasons.
    """
    if not improvements:
        return {"valid": False, "mean_improvement": 0.0, "reason": "no seeds"}

    if len(improvements) < 2:
        return {
            "valid": False,
            "mean_improvement": round(improvements[0], 4),
            "reason": "need at least 2 seeds to make a consistency claim",
        }

    mean_imp = statistics.mean(improvements)
    positive_count = sum(1 for d in improvements if d >= min_delta)
    positive_ratio = positive_count / len(improvements)

    if mean_imp < min_delta:
        return {
            "valid": False,
            "mean_improvement": round(mean_imp, 4),
            "reason": f"mean improvement {mean_imp:.4f} below threshold {min_delta}",
        }

    if positive_ratio < 0.7:
        return {
            "valid": False,
            "mean_improvement": round(mean_imp, 4),
            "reason": f"only {positive_ratio:.0%} of seeds show improvement >= {min_delta}",
        }

    return {
        "valid": True,
        "mean_improvement": round(mean_imp, 4),
        "reason": f"{positive_ratio:.0%} of seeds improved by mean +{mean_imp:.4f}",
    }


@dataclass(slots=True)
class TrajectoryReport:
    """Aggregated trajectory data across multiple seeds."""

    task_name: str
    trajectories: list[AgentTaskTrajectory]
    num_seeds: int
    num_generations: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def mean_scores_per_generation(self) -> list[float]:
        """Compute mean score at each generation across seeds."""
        if not self.trajectories:
            return []

        n_gens = min(len(t.score_history) for t in self.trajectories)
        means: list[float] = []
        for gen_idx in range(n_gens):
            scores = [t.score_history[gen_idx] for t in self.trajectories]
            means.append(round(statistics.mean(scores), 4))
        return means

    def compare(self) -> TrajectoryComparison:
        """Compare cold-start vs warmed performance across seeds."""
        improvements = [t.improvement_delta for t in self.trajectories]
        cold_starts = [t.cold_start_score for t in self.trajectories]
        finals = [t.final_score for t in self.trajectories]

        mean_imp = statistics.mean(improvements) if improvements else 0.0
        std_imp = statistics.pstdev(improvements) if len(improvements) > 1 else 0.0
        validation = validate_improvement(improvements)

        return TrajectoryComparison(
            task_name=self.task_name,
            num_seeds=self.num_seeds,
            num_generations=self.num_generations,
            mean_cold_start=round(statistics.mean(cold_starts), 4) if cold_starts else 0.0,
            mean_final=round(statistics.mean(finals), 4) if finals else 0.0,
            mean_improvement=round(mean_imp, 4),
            std_improvement=round(std_imp, 4),
            per_seed_improvements=improvements,
            consistent=validation["valid"],
        )

# Seeded generate function: (enriched_prompt, generation, seed) -> candidate output
SeededGenerateFn = Callable[[str, int, int], str]

# Seeded evaluate function: (output, generation, seed) -> (score, reasoning, dim_scores)
SeededEvaluateFn = Callable[[str, int, int], tuple[float, str, dict[str, float]]]


class MultiSeedTrajectoryRunner:
    """Runs AgentTaskEvolutionRunner across multiple seeds."""

    def __init__(
        self,
        task_prompt: str,
        generate_fn: SeededGenerateFn,
        evaluate_fn: SeededEvaluateFn,
        task_name: str = "agent_task",
        initial_output: str = "",
    ) -> None:
        self._task_prompt = task_prompt
        self._generate_fn = generate_fn
        self._evaluate_fn = evaluate_fn
        self._task_name = task_name
        self._initial_output = initial_output

    @staticmethod
    def _playbooks_by_generation(trajectory: AgentTaskTrajectory) -> dict[int, str]:
        """Reconstruct cumulative playbook text at each generation."""
        lesson_history = trajectory.metadata.get("lesson_history", [])
        playbook = ""
        playbooks_by_gen: dict[int, str] = {}
        for idx, lesson in enumerate(lesson_history):
            if lesson:
                playbook = (playbook + "\n" + lesson).strip() if playbook else lesson
            playbooks_by_gen[idx] = playbook
        return playbooks_by_gen

    def run(
        self,
        num_seeds: int = 5,
        num_generations: int = 10,
        seed_base: int = 42,
    ) -> TrajectoryReport:
        """Run the evolution across multiple seeds and collect trajectories."""
        trajectories: list[AgentTaskTrajectory] = []
        playbook_inspection: dict[str, dict[str, Any]] = {}

        for seed_offset in range(num_seeds):
            seed = seed_base + seed_offset

            def _generate(prompt: str, generation: int, _seed: int = seed) -> str:
                return self._generate_fn(prompt, generation, _seed)

            def _evaluate(
                output: str, generation: int, _seed: int = seed,
            ) -> AgentTaskGenerationEvaluation:
                score, reasoning, dims = self._evaluate_fn(output, generation, _seed)
                return AgentTaskGenerationEvaluation(
                    output=output,
                    score=score,
                    reasoning=reasoning,
                    dimension_scores=dims,
                )

            runner = AgentTaskEvolutionRunner(
                task_prompt=self._task_prompt,
                generate_fn=_generate,
                evaluate_fn=_evaluate,
                initial_output=self._initial_output,
                task_name=self._task_name,
            )
            trajectory = runner.run(num_generations=num_generations)
            trajectories.append(trajectory)
            inspector = PlaybookInspector(
                self._playbooks_by_generation(trajectory),
                trajectory.total_generations,
            )
            playbook_inspection[str(seed)] = {
                "snapshots": inspector.key_snapshots(),
                "growth": inspector.growth_summary(),
            }

        return TrajectoryReport(
            task_name=self._task_name,
            trajectories=trajectories,
            num_seeds=num_seeds,
            num_generations=num_generations,
            metadata={"playbook_inspection": playbook_inspection},
        )
