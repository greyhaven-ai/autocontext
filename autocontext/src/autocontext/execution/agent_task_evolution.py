"""Multi-generation support for AgentTask scenarios (AC-281)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import accumulate
from typing import Any

from pydantic import BaseModel, Field

from autocontext.execution.interpreter_workspace import InterpreterWorkspace
from autocontext.knowledge.compaction import compact_prompt_component
from autocontext.knowledge.harness_entries import HarnessEdit, HarnessEntry, SkillReference
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
class LessonSignal:
    """Structured, evaluator-provided guidance for the next generation.

    Domain-aware lesson accumulation: a deterministic evaluator usually
    knows *why* a candidate plateaued and *what* to try next (the size
    delta, which constraints bind, an explicit hint). Carrying that here
    lets ``accumulate_lessons`` write actionable playbook entries instead
    of only score + dimension scores.
    """

    hint: str = ""
    plateau: bool = False
    metrics: dict[str, float] = field(default_factory=dict)


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
    lesson_signal: LessonSignal | None = None


@dataclass(frozen=True, slots=True)
class FunctionSlot:
    """A fixed code harness with a small evolved slot (AC-776).

    Function-slot evolution mode keeps the evolved unit small: the runner
    carries only the slot in state and in the enriched prompt (so prompts
    stay compact), while evaluation runs the assembled ``harness`` + slot.
    This avoids the whole-program-bloat failure mode where carrying a large
    generated artifact in ``best_output`` ballooned every prompt.

    Convention: the slot is *prepended* to the harness, so the harness can
    reference names the slot defines (e.g. a ``priority`` function the
    greedy skeleton calls).
    """

    harness: str

    def assemble(self, slot: str, *, skills: Sequence[str] = ()) -> str:
        """Return the full runnable program: skills, then slot, then harness.

        Skill sources come first so the slot may call them; behavior without
        skills is unchanged.
        """
        if skills:
            return "\n\n".join([*skills, slot, self.harness])
        return f"{slot}\n\n{self.harness}"


def accumulate_lessons(
    judge_result: AgentTaskResult,
    generation: int,
    signal: LessonSignal | None = None,
) -> str:
    """Extract a structured lesson from judge feedback for the playbook.

    When the evaluator supplies a :class:`LessonSignal`, its actionable
    guidance (hint, plateau flag, metrics) is rendered alongside the score
    and dimension scores so the playbook carries move-level direction.
    """
    parts: list[str] = [f"Generation {generation} (score: {judge_result.score:.2f}):"]

    if judge_result.reasoning:
        parts.append(f"  Feedback: {judge_result.reasoning}")

    weak_dims = {dim: score for dim, score in judge_result.dimension_scores.items() if score < 0.7}
    if weak_dims:
        dim_strs = [f"{dim} ({score:.2f})" for dim, score in sorted(weak_dims.items(), key=lambda x: x[1])]
        parts.append(f"  Weak dimensions: {', '.join(dim_strs)}")

    strong_dims = {dim: score for dim, score in judge_result.dimension_scores.items() if score >= 0.8}
    if strong_dims:
        dim_strs = [f"{dim} ({score:.2f})" for dim, score in sorted(strong_dims.items(), key=lambda x: -x[1])]
        parts.append(f"  Strong dimensions: {', '.join(dim_strs)}")

    if not judge_result.reasoning and not weak_dims:
        parts.append(f"  Score: {judge_result.score:.2f}")

    if signal is not None:
        if signal.hint:
            parts.append(f"  Hint: {signal.hint}")
        if signal.plateau:
            parts.append(
                "  Plateau detected — a structurally different approach is needed; "
                "incremental tweaks are not advancing the score."
            )
        if signal.metrics:
            metric_strs = [f"{k}={v:g}" for k, v in sorted(signal.metrics.items())]
            parts.append(f"  Metrics: {', '.join(metric_strs)}")

    return "\n".join(parts)


def lesson_edit(
    judge_result: AgentTaskResult,
    generation: int,
    signal: LessonSignal | None = None,
) -> HarnessEdit:
    """Map a generation lesson onto a typed harness edit (AC-898).

    Actionable signals (hint or plateau) become policy entries carrying a
    falsifiable expected outcome; plain judge feedback becomes a fact.
    """
    actionable = signal is not None and bool(signal.hint or signal.plateau)
    expected_outcome = ""
    if signal is not None and signal.hint:
        expected_outcome = f"Applying the hint should raise the best score above {judge_result.score:.2f}."
    elif signal is not None and signal.plateau:
        expected_outcome = "A structurally different approach should break the plateau."
    return HarnessEdit(
        action="create",
        kind="policy" if actionable else "fact",
        title=f"Generation {generation} lesson (score {judge_result.score:.2f})",
        content=accumulate_lessons(judge_result, generation, signal=signal),
        expected_outcome=expected_outcome,
        reason=f"lesson accumulated at generation {generation}",
    )


def detect_plateau(score_history: Sequence[float], *, window: int = 3, epsilon: float = 1e-6) -> bool:
    """Flat RUNNING-BEST tail over the last ``window`` generations.

    ``score_history`` carries per-generation candidate scores, which can
    oscillate below a stuck best; the plateau that matters for promotion is
    the running maximum going flat.
    """
    window = max(1, window)
    if len(score_history) < window:
        return False
    running_best = list(accumulate(score_history, max))
    tail = running_best[-window:]
    return max(tail) - min(tail) <= epsilon


def propose_skill_promotion(
    state: AgentTaskGenerationState,
    *,
    entrypoint: str,
    call_pattern: str = "",
    window: int = 3,
    epsilon: float = 1e-6,
) -> HarnessEdit | None:
    """Propose freezing the champion slot as a named executable skill (AC-899).

    Precondition: the run must be in function-slot mode, where
    ``state.best_output`` holds only the evolved slot. In whole-program mode
    ``best_output`` is the entire program and must not be promoted as a skill.

    Fires only on a plateau with a non-empty champion: the search has stopped
    improving, so the best procedure so far is worth reusing deterministically
    instead of regenerating. Returns a create edit (proposal); the caller
    decides whether to apply it and at what scope.
    """
    if not detect_plateau(state.score_history, window=window, epsilon=epsilon):
        return None
    if not state.best_output.strip():
        return None
    reference = SkillReference(
        entrypoint=entrypoint,
        source=state.best_output,
        call_pattern=call_pattern or f"{entrypoint}(...)",
    )
    return HarnessEdit(
        action="create",
        kind="procedure",
        title=f"Promoted skill: {entrypoint}",
        content=(
            f"Champion slot frozen at generation {state.generation} "
            f"(score {state.best_score:.2f}). Call `{reference.call_pattern}`; do not reimplement."
        ),
        expected_outcome=f"Assembling the harness with this skill reproduces score {state.best_score:.2f}.",
        reason=f"plateau over {window} generations at score {state.best_score:.2f}",
        reference=reference,
    )


def build_enriched_prompt(
    *,
    task_prompt: str,
    playbook: str,
    generation: int,
    best_output: str,
    best_score: float,
    harness: str = "",
    skills: Sequence[HarnessEntry] = (),
    workspace_summary: str = "",
) -> str:
    """Enrich a task prompt with cross-generation context.

    In function-slot mode (``harness`` provided), the fixed harness is shown
    once as stable context so the model knows the contract it writes the slot
    against. The evolved slot itself is carried via ``best_output``.

    ``workspace_summary`` describes persistent interpreter variables by name
    (AC-901); contents stay in the workspace, never in the prompt.
    """
    playbook = compact_prompt_component("agent_task_playbook", playbook)
    best_output = compact_prompt_component("agent_task_best_output", best_output)
    sections: list[str] = [task_prompt]

    if harness:
        sections.append(f"\n\n## Fixed Harness (do not modify; you write only the slot)\n{harness}")

    if playbook:
        sections.append(
            f"\n\n## Accumulated Lessons (Generation {generation})\nPrevious best score: {best_score:.2f}\n\n{playbook}"
        )

    if best_output:
        sections.append(f"\n\n## Best Previous Output (score {best_score:.2f})\n{best_output}")

    skill_lines = [
        "- `{}`: {}".format(
            (entry.reference.call_pattern or entry.reference.entrypoint).replace("\n", " "),
            entry.title.replace("\n", " "),
        )
        for entry in skills
        if entry.reference is not None
    ]
    if skill_lines:
        sections.append("\n\n## Available Skills (call them; do not reimplement)\n" + "\n".join(skill_lines))

    if workspace_summary:
        sections.append(
            "\n\n## Workspace (persistent interpreter variables; reference them by name, "
            "contents are not inlined)\n" + workspace_summary
        )

    if playbook or best_output:
        sections.append(
            "\n\nUse the accumulated lessons and previous best output as context. "
            "Produce an improved version that addresses the identified weaknesses."
        )

    return "\n".join(sections)


def validate_skill_promotion(
    edit: HarnessEdit,
    slot_harness: FunctionSlot,
    evaluate: Callable[[str], AgentTaskGenerationEvaluation],
    *,
    best_score: float,
    skills: Sequence[str] = (),
    epsilon: float = 1e-9,
) -> bool:
    """A promotion must reproduce the score it was distilled from (AC-899).

    ``skills`` are previously promoted skill sources the candidate may call;
    validation must assemble the same program shape evaluation used.
    """
    if edit.reference is None:
        return False
    assembled = slot_harness.assemble(edit.reference.source, skills=skills)
    evaluation = evaluate(assembled)
    return evaluation.score >= best_score - epsilon


def migrate_states(
    states: list[AgentTaskGenerationState],
) -> list[AgentTaskGenerationState]:
    """Island migration: seed lagging islands with the global champion.

    Each island below the best score adopts the champion's best output and
    score (so winners propagate), but keeps its own playbook so accumulated
    lessons stay diverse. The champion island (and any tied) are unchanged.
    """
    if not states:
        return states
    champion = max(states, key=lambda s: s.best_score)
    migrated: list[AgentTaskGenerationState] = []
    for s in states:
        if s.best_score < champion.best_score:
            migrated.append(
                s.model_copy(
                    update={
                        "best_output": champion.best_output,
                        "best_score": champion.best_score,
                    }
                )
            )
        else:
            migrated.append(s)
    return migrated


def migrate_workspaces(
    states: list[AgentTaskGenerationState],
    migrated: list[AgentTaskGenerationState],
    workspaces: Sequence[InterpreterWorkspace],
) -> None:
    """Carry the champion's workspace into islands that adopted its output.

    ``states``/``migrated`` are the before/after of :func:`migrate_states`;
    an island whose best score rose during migration adopted the champion's
    output, so it also adopts a deep copy of the champion's variables. The
    champion (and any tied island) keeps its own workspace untouched.
    """
    if not states or not workspaces:
        return
    if len(workspaces) != len(states):
        raise ValueError(f"expected {len(states)} workspaces, got {len(workspaces)}")
    champion_index = max(range(len(states)), key=lambda i: states[i].best_score)
    snapshot = workspaces[champion_index].snapshot()
    for i, (before, after) in enumerate(zip(states, migrated, strict=True)):
        if after.best_score > before.best_score:
            workspaces[i].restore(snapshot)


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
            lines.append(f"Trajectory: {' → '.join(f'{score:.2f}' for score in self.score_history)}")
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
                    "Tasks involving schema changes, migrations, and backward compatibility. Best for data and API evolution."
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
WorkspaceEvaluateFn = Callable[[str, int, InterpreterWorkspace], AgentTaskGenerationEvaluation]
PromotionFn = Callable[[AgentTaskGenerationState, AgentTaskGenerationEvaluation], bool]


class AgentTaskEvolutionRunner:
    """Multi-generation runner for AgentTask scenarios with lesson accumulation."""

    def __init__(
        self,
        task_prompt: str,
        generate_fn: GenerateFn,
        evaluate_fn: EvaluateFn,
        initial_output: str = "",
        task_name: str = "agent_task",
        slot: FunctionSlot | None = None,
        workspace_factory: Callable[[], InterpreterWorkspace] | None = None,
        workspace_evaluate_fn: WorkspaceEvaluateFn | None = None,
        promotion_fn: PromotionFn | None = None,
    ) -> None:
        if workspace_evaluate_fn is not None and workspace_factory is None:
            raise ValueError("workspace_evaluate_fn requires a workspace_factory")
        self._task_prompt = task_prompt
        self._generate_fn = generate_fn
        self._evaluate_fn = evaluate_fn
        self._initial_output = initial_output
        self._task_name = task_name
        self._slot = slot
        self._workspace_factory = workspace_factory
        self._workspace_evaluate_fn = workspace_evaluate_fn
        self._promotion_fn = promotion_fn

    def run_generation(
        self,
        state: AgentTaskGenerationState,
        workspace: InterpreterWorkspace | None = None,
    ) -> AgentTaskGenerationState:
        """Run one generation: generate, evaluate, accumulate lessons, advance state.

        When a ``workspace`` is provided its variable listing (names, never
        contents) is rendered into the prompt, and evaluation goes through
        ``workspace_evaluate_fn`` when one was configured (AC-901).
        """
        prompt = build_enriched_prompt(
            task_prompt=self._task_prompt,
            playbook=state.playbook,
            generation=state.generation + 1,
            best_output=state.best_output,
            best_score=state.best_score,
            harness=self._slot.harness if self._slot else "",
            workspace_summary=workspace.render_markdown() if workspace is not None else "",
        )

        if state.generation == 0 and self._initial_output:
            candidate_output = self._initial_output
        else:
            candidate_output = self._generate_fn(prompt, state.generation).strip()
            if not candidate_output:
                candidate_output = state.best_output

        if self._slot is not None:
            # Function-slot mode: evaluate the assembled harness+slot, but
            # carry only the small slot forward (no whole-program bloat).
            program = self._slot.assemble(candidate_output)
        else:
            program = candidate_output

        if workspace is not None and self._workspace_evaluate_fn is not None:
            evaluation = self._workspace_evaluate_fn(program, state.generation, workspace)
        else:
            evaluation = self._evaluate_fn(program, state.generation)

        if self._slot is not None:
            evaluated_output = candidate_output
        else:
            evaluated_output = evaluation.output.strip() or candidate_output

        judge_result = AgentTaskResult(
            score=evaluation.score,
            reasoning=evaluation.reasoning,
            dimension_scores=evaluation.dimension_scores,
        )

        lesson = accumulate_lessons(judge_result, state.generation + 1, signal=evaluation.lesson_signal)
        new_playbook = state.playbook
        if lesson:
            new_playbook = (state.playbook + "\n" + lesson).strip() if state.playbook else lesson

        new_best_output = state.best_output
        new_best_score = state.best_score
        should_promote = (
            self._promotion_fn(state, evaluation)
            if self._promotion_fn is not None
            else not state.best_output or evaluation.score >= state.best_score
        )
        if should_promote:
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

        if workspace is not None:
            workspace_variables = list(metadata.get("workspace_variables", []))
            workspace_variables.append([f"{v.name}:{v.type_name}" for v in workspace.variables()])
            metadata["workspace_variables"] = workspace_variables

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

        workspace = self._workspace_factory() if self._workspace_factory is not None else None
        try:
            for _ in range(num_generations):
                state = self.run_generation(state, workspace=workspace)
        finally:
            if workspace is not None:
                workspace.close()

        trajectory = AgentTaskTrajectory(
            task_name=self._task_name,
            total_generations=num_generations,
            score_history=state.score_history,
            lessons_per_generation=[1 if lesson else 0 for lesson in state.lesson_history],
            cold_start_score=state.score_history[0] if state.score_history else 0.0,
            final_score=state.score_history[-1] if state.score_history else 0.0,
            improvement_delta=round(
                (state.score_history[-1] - state.score_history[0]) if state.score_history else 0.0,
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

    def run_islands(
        self,
        num_islands: int = 4,
        num_generations: int = 10,
        migrate_every: int = 0,
    ) -> AgentTaskTrajectory:
        """Run ``num_islands`` parallel lineages, optionally migrating the
        global champion into laggards every ``migrate_every`` generations.

        Islands preserve diversity (each keeps its own playbook and lineage)
        while migration shares winners — the population analogue of the
        single-lineage :meth:`run`. ``migrate_every=0`` disables migration
        (pure parallel islands).
        """
        if num_islands < 1:
            raise ValueError(f"num_islands must be >= 1, got {num_islands}")
        states = [
            AgentTaskGenerationState(
                generation=0,
                best_output="",
                best_score=0.0,
                playbook="",
                score_history=[],
                lesson_history=[],
                metadata={},
            )
            for _ in range(num_islands)
        ]

        # Created inside the try so a factory failure mid-list still closes
        # the workspaces already created.
        created_workspaces: list[InterpreterWorkspace] = []
        workspaces: list[InterpreterWorkspace] | None = None

        best_per_gen: list[float] = []
        try:
            if self._workspace_factory is not None:
                for _ in range(num_islands):
                    created_workspaces.append(self._workspace_factory())
                workspaces = created_workspaces
            for gen in range(num_generations):
                if workspaces is None:
                    states = [self.run_generation(s) for s in states]
                else:
                    states = [self.run_generation(s, workspace=ws) for s, ws in zip(states, workspaces, strict=True)]
                best_per_gen.append(max(s.best_score for s in states))
                if migrate_every and (gen + 1) % migrate_every == 0:
                    migrated = migrate_states(states)
                    if workspaces is not None:
                        migrate_workspaces(states, migrated, workspaces)
                    states = migrated
        finally:
            for ws in created_workspaces:
                ws.close()

        champion = max(states, key=lambda s: s.best_score)
        return AgentTaskTrajectory(
            task_name=self._task_name,
            total_generations=num_generations,
            score_history=best_per_gen,
            lessons_per_generation=[num_islands] * len(best_per_gen),
            cold_start_score=best_per_gen[0] if best_per_gen else 0.0,
            final_score=best_per_gen[-1] if best_per_gen else 0.0,
            improvement_delta=round((best_per_gen[-1] - best_per_gen[0]) if best_per_gen else 0.0, 4),
            metadata={
                "best_output": champion.best_output,
                "best_score": champion.best_score,
                "num_islands": num_islands,
                "playbook": champion.playbook,
            },
        )
