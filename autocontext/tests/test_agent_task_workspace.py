"""Workspace integration for the agent task evolution runner (AC-901)."""

from __future__ import annotations

import pytest

from autocontext.execution.agent_task_evolution import (
    AgentTaskEvolutionRunner,
    AgentTaskGenerationEvaluation,
    AgentTaskGenerationState,
    FunctionSlot,
    build_enriched_prompt,
    migrate_states,
    migrate_workspaces,
)
from autocontext.execution.interpreter_workspace import InterpreterWorkspace


def _evaluation(score: float, output: str = "out") -> AgentTaskGenerationEvaluation:
    return AgentTaskGenerationEvaluation(output=output, score=score, reasoning="r")


def _state() -> AgentTaskGenerationState:
    return AgentTaskGenerationState(
        generation=0,
        best_output="",
        best_score=0.0,
        playbook="",
        score_history=[],
        lesson_history=[],
        metadata={},
    )


class TrackingWorkspaceFactory:
    """Factory that records every workspace it creates."""

    def __init__(self, seed: dict | None = None) -> None:
        self.created: list[InterpreterWorkspace] = []
        self._seed = seed

    def __call__(self) -> InterpreterWorkspace:
        ws = InterpreterWorkspace(seed=dict(self._seed) if self._seed else None)
        self.created.append(ws)
        return ws


def test_prompt_without_workspace_summary_is_unchanged() -> None:
    baseline = build_enriched_prompt(
        task_prompt="Task",
        playbook="notes",
        generation=2,
        best_output="prev",
        best_score=0.5,
    )
    explicit = build_enriched_prompt(
        task_prompt="Task",
        playbook="notes",
        generation=2,
        best_output="prev",
        best_score=0.5,
        workspace_summary="",
    )
    assert explicit == baseline
    assert "## Workspace" not in baseline


def test_prompt_with_workspace_summary_adds_section() -> None:
    prompt = build_enriched_prompt(
        task_prompt="Task",
        playbook="",
        generation=1,
        best_output="",
        best_score=0.0,
        workspace_summary="- pool (list, size 3): [1, 2, 3]",
    )
    assert "## Workspace (persistent interpreter variables; reference them by name, contents are not inlined)" in prompt
    assert "- pool (list, size 3): [1, 2, 3]" in prompt


def test_workspace_evaluate_fn_requires_factory() -> None:
    with pytest.raises(ValueError, match="workspace_factory"):
        AgentTaskEvolutionRunner(
            task_prompt="Task",
            generate_fn=lambda prompt, gen: "code",
            evaluate_fn=lambda output, gen: _evaluation(0.5),
            workspace_evaluate_fn=lambda output, gen, ws: _evaluation(0.5),
        )


def test_run_generation_uses_workspace_evaluate_fn_with_assembled_program() -> None:
    seen: list[tuple[str, int, InterpreterWorkspace]] = []

    def workspace_evaluate(output: str, gen: int, ws: InterpreterWorkspace) -> AgentTaskGenerationEvaluation:
        seen.append((output, gen, ws))
        return _evaluation(0.7, output="slot_code")

    factory = TrackingWorkspaceFactory()
    runner = AgentTaskEvolutionRunner(
        task_prompt="Task",
        generate_fn=lambda prompt, gen: "slot_code",
        evaluate_fn=lambda output, gen: _evaluation(0.1),
        slot=FunctionSlot(harness="harness_code"),
        workspace_factory=factory,
        workspace_evaluate_fn=workspace_evaluate,
    )
    ws = factory()
    state = runner.run_generation(_state(), workspace=ws)
    assert len(seen) == 1
    program, gen, seen_ws = seen[0]
    assert program == "slot_code\n\nharness_code"
    assert gen == 0
    assert seen_ws is ws
    # Slot mode still carries only the slot forward.
    assert state.best_output == "slot_code"


def test_run_generation_without_workspace_falls_back_to_evaluate_fn() -> None:
    calls: list[str] = []
    runner = AgentTaskEvolutionRunner(
        task_prompt="Task",
        generate_fn=lambda prompt, gen: "code",
        evaluate_fn=lambda output, gen: (calls.append(output), _evaluation(0.4))[1],
        workspace_factory=TrackingWorkspaceFactory(),
        workspace_evaluate_fn=lambda output, gen, ws: _evaluation(0.9),
    )
    state = runner.run_generation(_state())
    assert calls == ["code"]
    assert state.best_score == 0.4


def test_run_generation_renders_workspace_into_prompt_and_metadata() -> None:
    ws = InterpreterWorkspace(seed={"pool": [1, 2, 3]})
    runner = AgentTaskEvolutionRunner(
        task_prompt="Task",
        generate_fn=lambda prompt, gen: "code",
        evaluate_fn=lambda output, gen: _evaluation(0.4),
    )
    state = runner.run_generation(_state(), workspace=ws)
    prompt = state.metadata["generation_prompts"][0]
    assert "## Workspace" in prompt
    assert "- pool (list, size 3): [1, 2, 3]" in prompt
    assert state.metadata["workspace_variables"] == [["pool:list"]]


def test_run_with_state_persists_workspace_and_closes_it() -> None:
    factory = TrackingWorkspaceFactory(seed={"pool": [1, 2, 3]})

    def workspace_evaluate(output: str, gen: int, ws: InterpreterWorkspace) -> AgentTaskGenerationEvaluation:
        ws.run(f"progress_{gen} = {gen}")
        return _evaluation(0.5 + gen / 10, output=output)

    runner = AgentTaskEvolutionRunner(
        task_prompt="Task",
        generate_fn=lambda prompt, gen: "code",
        evaluate_fn=lambda output, gen: _evaluation(0.0),
        workspace_factory=factory,
        workspace_evaluate_fn=workspace_evaluate,
    )
    _, state = runner.run_with_state(num_generations=2)

    assert len(factory.created) == 1
    ws = factory.created[0]
    # Variables written in generation 0 appear in generation 1's prompt.
    second_prompt = state.metadata["generation_prompts"][1]
    assert "progress_0" in second_prompt
    # Deterministic teardown after the run.
    with pytest.raises(RuntimeError, match="closed"):
        ws.run("1")


def test_run_with_state_closes_workspace_on_evaluate_exception() -> None:
    factory = TrackingWorkspaceFactory()

    def exploding_evaluate(output: str, gen: int, ws: InterpreterWorkspace) -> AgentTaskGenerationEvaluation:
        raise RuntimeError("boom")

    runner = AgentTaskEvolutionRunner(
        task_prompt="Task",
        generate_fn=lambda prompt, gen: "code",
        evaluate_fn=lambda output, gen: _evaluation(0.0),
        workspace_factory=factory,
        workspace_evaluate_fn=exploding_evaluate,
    )
    with pytest.raises(RuntimeError, match="boom"):
        runner.run_with_state(num_generations=1)
    assert len(factory.created) == 1
    with pytest.raises(RuntimeError, match="closed"):
        factory.created[0].run("1")


def test_run_islands_one_workspace_per_island_all_closed() -> None:
    factory = TrackingWorkspaceFactory()
    runner = AgentTaskEvolutionRunner(
        task_prompt="Task",
        generate_fn=lambda prompt, gen: "code",
        evaluate_fn=lambda output, gen: _evaluation(0.0),
        workspace_factory=factory,
        workspace_evaluate_fn=lambda output, gen, ws: _evaluation(0.5),
    )
    runner.run_islands(num_islands=3, num_generations=2)
    assert len(factory.created) == 3
    for ws in factory.created:
        with pytest.raises(RuntimeError, match="closed"):
            ws.run("1")


def test_migrate_workspaces_adopts_champion_state_in_laggards_only() -> None:
    champion_ws = InterpreterWorkspace(seed={"data": [1, 2, 3]})
    laggard_ws = InterpreterWorkspace(seed={"data": [9]})
    tied_ws = InterpreterWorkspace(seed={"data": [7]})

    def state_with(score: float) -> AgentTaskGenerationState:
        return AgentTaskGenerationState(
            generation=1,
            best_output=f"out_{score}",
            best_score=score,
            playbook="",
            score_history=[score],
            lesson_history=[],
            metadata={},
        )

    states = [state_with(0.9), state_with(0.2), state_with(0.9)]
    migrated = migrate_states(states)
    migrate_workspaces(states, migrated, [champion_ws, laggard_ws, tied_ws])

    adopted = laggard_ws.run("data")
    assert "[1, 2, 3]" in adopted.stdout
    untouched = tied_ws.run("data")
    assert "[7]" in untouched.stdout
    # Deep independence: mutating the laggard's copy leaves the champion intact.
    laggard_ws.run("data[0] = 99")
    original = champion_ws.run("data")
    assert "[1, 2, 3]" in original.stdout


def test_run_islands_migration_carries_workspace_variables() -> None:
    factory = TrackingWorkspaceFactory()
    scores = {0: [0.9, 0.9], 1: [0.1, 0.1]}
    observed: dict[int, str] = {}

    def workspace_evaluate(output: str, gen: int, ws: InterpreterWorkspace) -> AgentTaskGenerationEvaluation:
        island = factory.created.index(ws)
        if gen == 0:
            ws.run(f"marker = {island}")
        else:
            observed[island] = ws.run("marker").stdout.strip()
        return _evaluation(scores[island][gen])

    runner = AgentTaskEvolutionRunner(
        task_prompt="Task",
        generate_fn=lambda prompt, gen: "code",
        evaluate_fn=lambda output, gen: _evaluation(0.0),
        workspace_factory=factory,
        workspace_evaluate_fn=workspace_evaluate,
    )
    runner.run_islands(num_islands=2, num_generations=2, migrate_every=1)
    # After the first migration the laggard island's workspace carries the
    # champion's marker (island 0), observed during its second generation.
    assert observed[0] == "0"
    assert observed[1] == "0"
