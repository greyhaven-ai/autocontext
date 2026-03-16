"""Multi-generation support for AgentTask scenarios (AC-281).

Provides cross-generation learning for AgentTask scenarios that use
judge-based evaluation, enabling playbook accumulation, lesson carry-forward,
and trajectory reporting comparable to ScenarioInterface + GenerationRunner.

Key types:
- AgentTaskGenerationState: mutable cross-generation state
- accumulate_lessons(): extracts structured lessons from judge feedback
- build_enriched_prompt(): enriches task prompt with playbook context
- AgentTaskTrajectory: trajectory report with cold-vs-warm comparison
- ScenarioFamilyGuide: when-to-use guidance for choosing scenario families
- AgentTaskEvolutionRunner: multi-generation runner with lesson accumulation
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from autocontext.scenarios.agent_task import AgentTaskResult


@dataclass(slots=True)
class AgentTaskGenerationState:
    """Cross-generation state for an agent task evolution run."""

    generation: int
    best_output: str
    best_score: float
    playbook: str
    score_history: list[float]
    lesson_history: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "best_output": self.best_output,
            "best_score": self.best_score,
            "playbook": self.playbook,
            "score_history": self.score_history,
            "lesson_history": self.lesson_history,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentTaskGenerationState:
        return cls(
            generation=data.get("generation", 0),
            best_output=data.get("best_output", ""),
            best_score=data.get("best_score", 0.0),
            playbook=data.get("playbook", ""),
            score_history=data.get("score_history", []),
            lesson_history=data.get("lesson_history", []),
            metadata=data.get("metadata", {}),
        )


def accumulate_lessons(
    judge_result: AgentTaskResult,
    generation: int,
) -> str:
    """Extract a structured lesson from judge feedback for the playbook.

    Returns a single lesson string summarizing what was learned.
    """
    parts: list[str] = [f"Generation {generation} (score: {judge_result.score:.2f}):"]

    if judge_result.reasoning:
        parts.append(f"  Feedback: {judge_result.reasoning}")

    weak_dims = {
        dim: score
        for dim, score in judge_result.dimension_scores.items()
        if score < 0.7
    }
    if weak_dims:
        dim_strs = [f"{dim} ({score:.2f})" for dim, score in sorted(weak_dims.items(), key=lambda x: x[1])]
        parts.append(f"  Weak dimensions: {', '.join(dim_strs)}")

    strong_dims = {
        dim: score
        for dim, score in judge_result.dimension_scores.items()
        if score >= 0.8
    }
    if strong_dims:
        dim_strs = [f"{dim} ({score:.2f})" for dim, score in sorted(strong_dims.items(), key=lambda x: -x[1])]
        parts.append(f"  Strong dimensions: {', '.join(dim_strs)}")

    if not judge_result.reasoning and not weak_dims:
        parts.append(f"  Score: {judge_result.score:.2f}")

    return "\n".join(parts)


def build_enriched_prompt(
    *,
    task_prompt: str,
    playbook: str,
    generation: int,
    best_output: str,
    best_score: float,
) -> str:
    """Enrich a task prompt with cross-generation context.

    Injects playbook lessons, best previous output, and generation info
    into the task prompt to guide later generations.
    """
    sections: list[str] = [task_prompt]

    if playbook:
        sections.append(
            f"\n\n## Accumulated Lessons (Generation {generation})\n"
            f"Previous best score: {best_score:.2f}\n\n"
            f"{playbook}"
        )

    if best_output and generation > 0:
        sections.append(
            f"\n\n## Best Previous Output (score {best_score:.2f})\n"
            f"{best_output}"
        )

    if playbook or (best_output and generation > 0):
        sections.append(
            "\n\nUse the accumulated lessons and previous best output as context. "
            "Produce an improved version that addresses the identified weaknesses."
        )

    return "\n".join(sections)


@dataclass(slots=True)
class AgentTaskTrajectory:
    """Trajectory report for a multi-generation agent task run."""

    task_name: str
    total_generations: int
    score_history: list[float]
    lessons_per_generation: list[int]
    cold_start_score: float
    final_score: float
    improvement_delta: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def cold_vs_warm_summary(self) -> str:
        """Human-readable comparison of cold-start vs warmed performance."""
        lines = [
            f"Task: {self.task_name}",
            f"Generations: {self.total_generations}",
            f"Cold-start score: {self.cold_start_score:.2f}",
            f"Final score: {self.final_score:.2f}",
            f"Improvement: +{self.improvement_delta:.2f}",
        ]
        if len(self.score_history) >= 2:
            lines.append(f"Trajectory: {' → '.join(f'{s:.2f}' for s in self.score_history)}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_name": self.task_name,
            "total_generations": self.total_generations,
            "score_history": self.score_history,
            "lessons_per_generation": self.lessons_per_generation,
            "cold_start_score": self.cold_start_score,
            "final_score": self.final_score,
            "improvement_delta": self.improvement_delta,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentTaskTrajectory:
        return cls(
            task_name=data.get("task_name", ""),
            total_generations=data.get("total_generations", 0),
            score_history=data.get("score_history", []),
            lessons_per_generation=data.get("lessons_per_generation", []),
            cold_start_score=data.get("cold_start_score", 0.0),
            final_score=data.get("final_score", 0.0),
            improvement_delta=data.get("improvement_delta", 0.0),
            metadata=data.get("metadata", {}),
        )


class ScenarioFamilyGuide:
    """When-to-use guidance for choosing between scenario families."""

    def __init__(self) -> None:
        self.families: dict[str, dict[str, str]] = {
            "agent_task": {
                "when_to_use": (
                    "Open-ended rubric-driven tasks evaluated by LLM judge. "
                    "Best for writing, analysis, code review, and creative tasks "
                    "where output quality is subjective and dimension-scored."
                ),
                "multi_gen": "Yes — via AgentTaskEvolutionRunner with playbook accumulation.",
            },
            "simulation": {
                "when_to_use": (
                    "Richly stateful scenarios with world state, entities, resources, "
                    "and multi-step transitions. Best for orchestration, resource "
                    "management, and planning tasks."
                ),
                "multi_gen": "Yes — via GenerationRunner with ScenarioInterface adapter.",
            },
            "negotiation": {
                "when_to_use": (
                    "Multi-party interaction scenarios with offers, counteroffers, "
                    "and agreement dynamics. Best for bargaining, diplomacy, and "
                    "contract negotiation tasks."
                ),
                "multi_gen": "Yes — via GenerationRunner.",
            },
            "schema_evolution": {
                "when_to_use": (
                    "Tasks involving schema changes, migrations, and backward "
                    "compatibility. Best for database evolution, API versioning, "
                    "and configuration management."
                ),
                "multi_gen": "Yes — via GenerationRunner.",
            },
            "game": {
                "when_to_use": (
                    "Tournament-scored competitive scenarios with match execution. "
                    "Best for grid_ctf, othello, and other game-like environments."
                ),
                "multi_gen": "Yes — via GenerationRunner (native).",
            },
        }

    def to_markdown(self) -> str:
        lines = ["# Scenario Family Guide\n"]
        for family, info in self.families.items():
            lines.append(f"## {family}")
            lines.append(f"**When to use:** {info['when_to_use']}")
            lines.append(f"**Multi-generation:** {info['multi_gen']}\n")
        return "\n".join(lines)


# Evaluate function type: (output, generation) -> (score, reasoning, dimension_scores)
EvaluateFn = Callable[[str, int], tuple[float, str, dict[str, float]]]


class AgentTaskEvolutionRunner:
    """Multi-generation runner for AgentTask scenarios with lesson accumulation."""

    def __init__(
        self,
        task_prompt: str,
        evaluate_fn: EvaluateFn,
        initial_output: str = "",
        task_name: str = "agent_task",
    ) -> None:
        self._task_prompt = task_prompt
        self._evaluate_fn = evaluate_fn
        self._initial_output = initial_output
        self._task_name = task_name

    def run_generation(
        self,
        state: AgentTaskGenerationState,
    ) -> AgentTaskGenerationState:
        """Run one generation: evaluate, accumulate lessons, advance state."""
        score, reasoning, dim_scores = self._evaluate_fn(
            state.best_output, state.generation,
        )

        judge_result = AgentTaskResult(
            score=score,
            reasoning=reasoning,
            dimension_scores=dim_scores,
        )

        lesson = accumulate_lessons(judge_result, state.generation)

        new_playbook = state.playbook
        if lesson:
            new_playbook = (state.playbook + "\n" + lesson).strip() if state.playbook else lesson

        return AgentTaskGenerationState(
            generation=state.generation + 1,
            best_output=state.best_output,
            best_score=score,
            playbook=new_playbook,
            score_history=[*state.score_history, score],
            lesson_history=[*state.lesson_history, lesson],
        )

    def run(self, num_generations: int = 10) -> AgentTaskTrajectory:
        """Run multiple generations and return trajectory report."""
        state = AgentTaskGenerationState(
            generation=0,
            best_output=self._initial_output,
            best_score=0.0,
            playbook="",
            score_history=[],
            lesson_history=[],
        )

        for _ in range(num_generations):
            state = self.run_generation(state)

        return AgentTaskTrajectory(
            task_name=self._task_name,
            total_generations=num_generations,
            score_history=state.score_history,
            lessons_per_generation=[1] * num_generations,
            cold_start_score=state.score_history[0] if state.score_history else 0.0,
            final_score=state.score_history[-1] if state.score_history else 0.0,
            improvement_delta=round(
                (state.score_history[-1] - state.score_history[0]) if state.score_history else 0.0, 4
            ),
        )
