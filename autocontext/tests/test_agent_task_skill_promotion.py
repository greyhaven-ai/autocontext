"""Plateau-triggered promotion of champion slots into executable skills (AC-899)."""

from __future__ import annotations

from autocontext.execution.agent_task_evolution import (
    AgentTaskGenerationEvaluation,
    AgentTaskGenerationState,
    FunctionSlot,
    build_enriched_prompt,
    detect_plateau,
    propose_skill_promotion,
    validate_skill_promotion,
)
from autocontext.knowledge.harness_entries import HarnessEdit, HarnessEntry, SkillReference

SLOT = "def priority(v):\n    return sum(v)"


def _state(score_history: list[float], best_output: str = SLOT) -> AgentTaskGenerationState:
    return AgentTaskGenerationState(
        generation=len(score_history),
        best_output=best_output,
        best_score=score_history[-1] if score_history else 0.0,
        playbook="",
        score_history=score_history,
        lesson_history=[],
    )


class TestDetectPlateau:
    def test_flat_tail_is_plateau(self) -> None:
        assert detect_plateau([0.5, 0.949, 0.949, 0.949], window=3) is True

    def test_rising_tail_is_not(self) -> None:
        assert detect_plateau([0.5, 0.6, 0.7, 0.8], window=3) is False

    def test_short_history_is_not(self) -> None:
        assert detect_plateau([0.9, 0.9], window=3) is False


class TestProposeSkillPromotion:
    def test_no_plateau_returns_none(self) -> None:
        assert propose_skill_promotion(_state([0.1, 0.5, 0.9]), entrypoint="priority") is None

    def test_empty_champion_returns_none(self) -> None:
        state = _state([0.9, 0.9, 0.9], best_output="   ")
        assert propose_skill_promotion(state, entrypoint="priority") is None

    def test_plateau_yields_procedure_edit_with_reference(self) -> None:
        state = _state([0.5, 0.949, 0.949, 0.949])
        edit = propose_skill_promotion(state, entrypoint="priority", call_pattern="priority(vector)")
        assert edit is not None
        assert edit.action == "create" and edit.kind == "procedure"
        assert "priority" in edit.title
        assert edit.reference is not None
        assert edit.reference.source == SLOT
        assert edit.reference.call_pattern == "priority(vector)"
        assert "0.95" in edit.expected_outcome
        assert "plateau" in edit.reason


class TestSkillAssembly:
    def test_assemble_without_skills_unchanged(self) -> None:
        slot_harness = FunctionSlot(harness="print(priority([1]))")
        assert slot_harness.assemble(SLOT) == f"{SLOT}\n\nprint(priority([1]))"

    def test_assemble_places_skills_before_slot(self) -> None:
        helper = "def helper(x):\n    return x * 2"
        slot_harness = FunctionSlot(harness="HARNESS")
        assembled = slot_harness.assemble("SLOT", skills=[helper])
        assert assembled.index(helper) < assembled.index("SLOT") < assembled.index("HARNESS")

    def test_prompt_gains_metadata_only_skill_section(self) -> None:
        entry = HarnessEntry(
            id="harness_s",
            kind="procedure",
            scope="run",
            title="Promoted skill: priority",
            content="c",
            reference=SkillReference(entrypoint="priority", source=SLOT, call_pattern="priority(vector)"),
        )
        prompt = build_enriched_prompt(
            task_prompt="Do the task",
            playbook="",
            generation=2,
            best_output="",
            best_score=0.9,
            skills=[entry],
        )
        assert "Available Skills" in prompt
        assert "priority(vector)" in prompt
        assert "def priority" not in prompt

    def test_prompt_without_skills_byte_identical(self) -> None:
        kwargs = dict(task_prompt="Do the task", playbook="p", generation=1, best_output="b", best_score=0.5)
        assert build_enriched_prompt(**kwargs) == build_enriched_prompt(**kwargs, skills=[])


class TestValidateSkillPromotion:
    def _edit(self) -> HarnessEdit:
        return HarnessEdit(
            action="create",
            kind="procedure",
            title="t",
            content="c",
            reference=SkillReference(entrypoint="priority", source=SLOT),
        )

    def test_reproducing_score_validates(self) -> None:
        captured: list[str] = []

        def evaluate(program: str) -> AgentTaskGenerationEvaluation:
            captured.append(program)
            return AgentTaskGenerationEvaluation(output=program, score=0.949, reasoning="ok")

        slot_harness = FunctionSlot(harness="HARNESS")
        assert validate_skill_promotion(self._edit(), slot_harness, evaluate, best_score=0.949) is True
        assert captured == [slot_harness.assemble(SLOT)]

    def test_lower_score_fails_validation(self) -> None:
        def evaluate(program: str) -> AgentTaskGenerationEvaluation:
            return AgentTaskGenerationEvaluation(output=program, score=0.5, reasoning="regressed")

        assert validate_skill_promotion(self._edit(), FunctionSlot(harness="H"), evaluate, best_score=0.949) is False

    def test_missing_reference_fails(self) -> None:
        edit = HarnessEdit(action="create", kind="procedure", title="t", content="c")

        def evaluate(program: str) -> AgentTaskGenerationEvaluation:
            raise AssertionError("must not evaluate without a reference")

        assert validate_skill_promotion(edit, FunctionSlot(harness="H"), evaluate, best_score=0.9) is False
