"""Tests for function-slot evolution mode (AC-776).

The evolved unit is a small slot inside a fixed harness, not a whole
program. The runner carries only the slot (so enriched prompts stay
small), but evaluates the assembled harness+slot. This fixes the
whole-program-bloat failure mode where best_output ballooned the prompt.
"""

from __future__ import annotations


class TestFunctionSlot:
    def test_assemble_prepends_slot_to_harness(self) -> None:
        from autocontext.execution.agent_task_evolution import FunctionSlot

        slot = FunctionSlot(harness="def build():\n    return priority")
        assembled = slot.assemble("def priority(v):\n    return 1.0")
        assert "def priority(v):" in assembled
        assert "def build():" in assembled
        # slot is prepended so the harness can reference it
        assert assembled.index("def priority(v):") < assembled.index("def build():")


class TestRunnerSlotMode:
    def test_carries_slot_not_assembled_program(self) -> None:
        from autocontext.execution.agent_task_evolution import (
            AgentTaskEvolutionRunner,
            AgentTaskGenerationEvaluation,
            FunctionSlot,
        )

        big_harness = "def build():\n    return priority\n" + "# pad\n" * 2000
        slot = FunctionSlot(harness=big_harness)
        eval_inputs: list[str] = []

        def gen(prompt: str, g: int) -> str:
            return "def priority(v):\n    return 2.0"

        def ev(output: str, g: int) -> AgentTaskGenerationEvaluation:
            eval_inputs.append(output)
            return AgentTaskGenerationEvaluation(output=output, score=0.5, reasoning="ok")

        runner = AgentTaskEvolutionRunner(
            task_prompt="t",
            generate_fn=gen,
            evaluate_fn=ev,
            initial_output="def priority(v):\n    return 0.0",
            slot=slot,
        )
        trajectory, state = runner.run_with_state(2)

        # evaluate_fn received the ASSEMBLED program (harness present)
        assert any("def build():" in inp for inp in eval_inputs)
        # but the carried best_output is only the SLOT (no harness bloat)
        assert "def build():" not in state.best_output
        assert "def priority(v):" in state.best_output

    def test_best_previous_output_section_carries_slot_not_assembled(self) -> None:
        """The enriched prompt's "Best Previous Output" carries the small
        slot, never the assembled harness+slot — the anti-bloat guarantee."""
        from autocontext.execution.agent_task_evolution import (
            AgentTaskEvolutionRunner,
            AgentTaskGenerationEvaluation,
            FunctionSlot,
        )

        slot = FunctionSlot(harness="def build():\n    return priority  # HARNESS_MARKER")
        seen_prompts: list[str] = []

        def gen(prompt: str, g: int) -> str:
            seen_prompts.append(prompt)
            return "def priority(v):\n    return 1.0  # SLOT_MARKER"

        def ev(output: str, g: int) -> AgentTaskGenerationEvaluation:
            return AgentTaskGenerationEvaluation(output=output, score=0.5, reasoning="ok")

        runner = AgentTaskEvolutionRunner(
            task_prompt="t",
            generate_fn=gen,
            evaluate_fn=ev,
            initial_output="def priority(v):\n    return 0.0",
            slot=slot,
        )
        runner.run_with_state(2)
        assert seen_prompts, "generate_fn should have been called on gen 2"
        for p in seen_prompts:
            if "## Best Previous Output" in p:
                best_section = p.split("## Best Previous Output")[1]
                # carries a slot (a priority fn), never the assembled harness
                assert "def priority(v):" in best_section
                assert "HARNESS_MARKER" not in best_section


class TestEnrichedPromptHarness:
    def test_includes_harness_section_when_provided(self) -> None:
        from autocontext.execution.agent_task_evolution import build_enriched_prompt

        out = build_enriched_prompt(
            task_prompt="t",
            playbook="",
            generation=1,
            best_output="",
            best_score=0.0,
            harness="HARNESS_CODE_HERE",
        )
        assert "HARNESS_CODE_HERE" in out
        assert "Fixed Harness" in out

    def test_no_harness_section_when_absent(self) -> None:
        from autocontext.execution.agent_task_evolution import build_enriched_prompt

        out = build_enriched_prompt(
            task_prompt="t",
            playbook="",
            generation=1,
            best_output="",
            best_score=0.0,
        )
        assert "Fixed Harness" not in out
