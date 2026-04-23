"""Multi-generation support for AgentTask scenarios (AC-281)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from autocontext.knowledge.compaction import compact_prompt_component
from autocontext.scenarios.agent_task import AgentTaskResult


class AgentTaskGenerationState(BaseModel):
    """Cross-generation state for an agent task evolution run."""

    generation: int
    best_output: str
    best_score: float
    playbook: str
    score_history: list[float]
    lesson_history: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentTaskGenerationState:
        return cls.model_validate(data)


@dataclass(slots=True)
class AgentTaskGenerationEvaluation:
    """Evaluation result for one cross-generation candidate."""

    output: str
    score: float
    reasoning: str
    dimension_scores: dict[str, float] = field(default_factory=dict)
    round_count: int = 1
    met_threshold: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def accumulate_lessons(
    judge_result: AgentTaskResult,
    generation: int,
) -> str:
    """Extract a structured lesson from judge feedback for the playbook."""
    parts: list[str] = [f"Generation {generation} (score: {judge_result.score:.2f}):"]

    if judge_result.reasoning:
        parts.append(f"  Feedback: {judge_result.reasoning}")

    weak_dims = {
        dim: score
        for dim, score in judge_result.dimension_scores.items()
        if score < 0.7
    }
    if weak_dims:
        dim_strs = [
            f"{dim} ({score:.2f})"
            for dim, score in sorted(weak_dims.items(), key=lambda x: x[1])
        ]
        parts.append(f"  Weak dimensions: {', '.join(dim_strs)}")

    strong_dims = {
        dim: score
        for dim, score in judge_result.dimension_scores.items()
        if score >= 0.8
    }
    if strong_dims:
        dim_strs = [
            f"{dim} ({score:.2f})"
            for dim, score in sorted(strong_dims.items(), key=lambda x: -x[1])
        ]
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
    """Enrich a task prompt with cross-generation context."""
    playbook = compact_prompt_component("agent_task_playbook", playbook)
    best_output = compact_prompt_component("agent_task_best_output", best_output)
    sections: list[str] = [task_prompt]

    if playbook:
        sections.append(
            f"\n\n## Accumulated Lessons (Generation {generation})\n"
            f"Previous best score: {best_score:.2f}\n\n"
            f"{playbook}"
        )

    if best_output:
        sections.append(
            f"\n\n## Best Previous Output (score {best_score:.2f})\n"
            f"{best_output}"
        )

    if playbook or best_output:
        sections.append(
            "\n\nUse the accumulated lessons and previous best output as context. "
            "Produce an improved version that addresses the identified weaknesses."
        )

    return "\n".join(sections)


class AgentTaskTrajectory(BaseModel):
    """Trajectory report for a multi-generation agent task run."""

    task_name: str
    total_generations: int
    score_history: list[float]
    lessons_per_generation: list[int]
    cold_start_score: float
    final_score: float
    improvement_delta: float
    metadata: dict[str, Any] = Field(default_factory=dict)

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
            lines.append(
                f"Trajectory: {' → '.join(f'{score:.2f}' for score in self.score_history)}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentTaskTrajectory:
        return cls.model_validate(data)


class ScenarioFamilyGuide:
    """When-to-use guidance for choosing between scenario families."""

    def __init__(self) -> None:
        self.families: dict[str, dict[str, str]] = {
            "agent_task": {
                "when_to_use": (
                    "Open-ended rubric-driven tasks evaluated by an LLM judge. "
                    "Best for writing, analysis, code review, and other subjective "
                    "tasks where quality is dimension-scored."
                ),
                "multi_gen": "Yes — via AgentTaskEvolutionRunner with playbook carry-forward.",
            },
            "simulation": {
                "when_to_use": (
                    "Richly stateful scenarios with world state, entities, resources, "
                    "and multi-step transitions. Best for orchestration, planning, "
                    "and resource-management tasks."
                ),
                "multi_gen": "Yes — via GenerationRunner with ScenarioInterface.",
            },
            "negotiation": {
                "when_to_use": (
                    "Multi-party interaction scenarios with offers, counteroffers, "
                    "and agreement dynamics. Best for bargaining and diplomacy."
                ),
                "multi_gen": "Yes — via GenerationRunner.",
            },
            "schema_evolution": {
                "when_to_use": (
                    "Tasks involving schema changes, migrations, and backward "
                    "compatibility. Best for data and API evolution."
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


GenerateFn = Callable[[str, int], str]
EvaluateFn = Callable[[str, int], AgentTaskGenerationEvaluation]


class AgentTaskEvolutionRunner:
    """Multi-generation runner for AgentTask scenarios with lesson accumulation."""

    def __init__(
        self,
        task_prompt: str,
        generate_fn: GenerateFn,
        evaluate_fn: EvaluateFn,
        initial_output: str = "",
        task_name: str = "agent_task",
    ) -> None:
        self._task_prompt = task_prompt
        self._generate_fn = generate_fn
        self._evaluate_fn = evaluate_fn
        self._initial_output = initial_output
        self._task_name = task_name

    def run_generation(
        self,
        state: AgentTaskGenerationState,
    ) -> AgentTaskGenerationState:
        """Run one generation: generate, evaluate, accumulate lessons, advance state."""
        prompt = build_enriched_prompt(
            task_prompt=self._task_prompt,
            playbook=state.playbook,
            generation=state.generation + 1,
            best_output=state.best_output,
            best_score=state.best_score,
        )

        if state.generation == 0 and self._initial_output:
            candidate_output = self._initial_output
        else:
            candidate_output = self._generate_fn(prompt, state.generation).strip()
            if not candidate_output:
                candidate_output = state.best_output

        evaluation = self._evaluate_fn(candidate_output, state.generation)
        evaluated_output = evaluation.output.strip() or candidate_output

        judge_result = AgentTaskResult(
            score=evaluation.score,
            reasoning=evaluation.reasoning,
            dimension_scores=evaluation.dimension_scores,
        )

        lesson = accumulate_lessons(judge_result, state.generation + 1)
        new_playbook = state.playbook
        if lesson:
            new_playbook = (
                (state.playbook + "\n" + lesson).strip() if state.playbook else lesson
            )

        new_best_output = state.best_output
        new_best_score = state.best_score
        if not state.best_output or evaluation.score >= state.best_score:
            new_best_output = evaluated_output
            new_best_score = evaluation.score

        metadata = dict(state.metadata)
        generation_prompts = list(metadata.get("generation_prompts", []))
        generation_outputs = list(metadata.get("generation_outputs", []))
        generation_round_counts = list(metadata.get("generation_round_counts", []))
        met_threshold_history = list(metadata.get("met_threshold_history", []))

        generation_prompts.append(prompt)
        generation_outputs.append(evaluated_output)
        generation_round_counts.append(evaluation.round_count)
        met_threshold_history.append(evaluation.met_threshold)

        metadata["generation_prompts"] = generation_prompts
        metadata["generation_outputs"] = generation_outputs
        metadata["generation_round_counts"] = generation_round_counts
        metadata["met_threshold_history"] = met_threshold_history

        return AgentTaskGenerationState(
            generation=state.generation + 1,
            best_output=new_best_output,
            best_score=new_best_score,
            playbook=new_playbook,
            score_history=[*state.score_history, evaluation.score],
            lesson_history=[*state.lesson_history, lesson],
            metadata=metadata,
        )

    def run_with_state(
        self,
        num_generations: int = 10,
    ) -> tuple[AgentTaskTrajectory, AgentTaskGenerationState]:
        """Run multiple generations and return both trajectory and final state."""
        state = AgentTaskGenerationState(
            generation=0,
            best_output="",
            best_score=0.0,
            playbook="",
            score_history=[],
            lesson_history=[],
            metadata={},
        )

        for _ in range(num_generations):
            state = self.run_generation(state)

        trajectory = AgentTaskTrajectory(
            task_name=self._task_name,
            total_generations=num_generations,
            score_history=state.score_history,
            lessons_per_generation=[1 if lesson else 0 for lesson in state.lesson_history],
            cold_start_score=state.score_history[0] if state.score_history else 0.0,
            final_score=state.score_history[-1] if state.score_history else 0.0,
            improvement_delta=round(
                (state.score_history[-1] - state.score_history[0])
                if state.score_history
                else 0.0,
                4,
            ),
            metadata={
                "best_output": state.best_output,
                "best_score": state.best_score,
                "playbook": state.playbook,
                "lesson_history": state.lesson_history,
                **state.metadata,
            },
        )
        return trajectory, state

    def run(self, num_generations: int = 10) -> AgentTaskTrajectory:
        """Run multiple generations and return a trajectory report."""
        trajectory, _ = self.run_with_state(num_generations)
        return trajectory
