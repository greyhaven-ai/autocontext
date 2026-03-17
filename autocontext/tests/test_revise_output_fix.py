"""Tests for AC-280: generated revise_output actually revises instead of no-op.

Covers: build_revision_prompt (pure function), generated revise_output template,
ImprovementLoop unchanged_output prevention.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ===========================================================================
# build_revision_prompt — pure function tests
# ===========================================================================


class TestBuildRevisionPrompt:
    def test_includes_original_output(self) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.agent_task_revision import build_revision_prompt

        result = AgentTaskResult(
            score=0.65, reasoning="Good structure but lacks depth",
            dimension_scores={"accuracy": 0.8, "depth": 0.4},
        )
        prompt = build_revision_prompt(
            original_output="A brief analysis of the topic.",
            judge_result=result,
            task_prompt="Write a deep analysis.",
        )
        assert "A brief analysis of the topic." in prompt

    def test_includes_judge_reasoning(self) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.agent_task_revision import build_revision_prompt

        result = AgentTaskResult(
            score=0.5, reasoning="Missing concrete examples",
            dimension_scores={},
        )
        prompt = build_revision_prompt(
            original_output="Some output",
            judge_result=result,
            task_prompt="Write with examples.",
        )
        assert "Missing concrete examples" in prompt

    def test_includes_weak_dimensions(self) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.agent_task_revision import build_revision_prompt

        result = AgentTaskResult(
            score=0.6,
            reasoning="Weak on depth and evidence",
            dimension_scores={"clarity": 0.9, "depth": 0.3, "evidence": 0.4},
        )
        prompt = build_revision_prompt(
            original_output="output",
            judge_result=result,
            task_prompt="Task prompt.",
        )
        # Should mention weak dimensions (< 0.7)
        assert "depth" in prompt.lower()
        assert "evidence" in prompt.lower()

    def test_includes_revision_prompt_when_provided(self) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.agent_task_revision import build_revision_prompt

        result = AgentTaskResult(score=0.7, reasoning="ok", dimension_scores={})
        prompt = build_revision_prompt(
            original_output="output",
            judge_result=result,
            task_prompt="Task.",
            revision_prompt="Focus on improving citations and evidence.",
        )
        assert "improving citations and evidence" in prompt
        assert "## Original Task" in prompt
        assert "Task." in prompt

    def test_includes_task_prompt_when_no_revision_prompt(self) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.agent_task_revision import build_revision_prompt

        result = AgentTaskResult(score=0.5, reasoning="weak", dimension_scores={})
        prompt = build_revision_prompt(
            original_output="output",
            judge_result=result,
            task_prompt="Analyze drug interactions for safety.",
        )
        assert "drug interactions" in prompt.lower()

    def test_includes_score(self) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.agent_task_revision import build_revision_prompt

        result = AgentTaskResult(score=0.42, reasoning="needs work", dimension_scores={})
        prompt = build_revision_prompt(
            original_output="output",
            judge_result=result,
            task_prompt="Task.",
        )
        assert "0.42" in prompt

    def test_no_weak_dimensions_when_all_high(self) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.agent_task_revision import build_revision_prompt

        result = AgentTaskResult(
            score=0.85,
            reasoning="Good overall",
            dimension_scores={"clarity": 0.9, "depth": 0.85, "evidence": 0.8},
        )
        prompt = build_revision_prompt(
            original_output="output",
            judge_result=result,
            task_prompt="Task.",
        )
        # No "## Weak Dimensions" section should appear when all scores >= 0.7
        assert "## Weak Dimensions" not in prompt


# ===========================================================================
# Generated revise_output should NOT be a no-op
# ===========================================================================


class TestGeneratedReviseOutputTemplate:
    def test_generated_code_uses_shared_runtime_helper(self) -> None:
        """The generated revise_output method should call the shared runtime helper."""
        from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

        spec = AgentTaskSpec(
            task_prompt="Write an essay.",
            judge_rubric="Evaluate essay quality.",
            max_rounds=3,
            revision_prompt="Improve depth and examples.",
        )
        source = generate_agent_task_class(spec, "essay_task")

        assert "revise_generated_output" in source

    def test_generated_code_single_round_still_noop(self) -> None:
        """When max_rounds=1 and no revision_prompt, revise_output should still no-op."""
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

        spec = AgentTaskSpec(
            task_prompt="Write a haiku.",
            judge_rubric="Evaluate haiku.",
            max_rounds=1,
            revision_prompt=None,
        )
        source = generate_agent_task_class(spec, "haiku_task")
        ns: dict[str, object] = {}
        exec(compile(source, "<test>", "exec"), ns)  # noqa: S102
        cls = ns["HaikuTaskAgentTask"]
        task = cls()
        revised = task.revise_output("original", AgentTaskResult(score=0.4, reasoning="weak"), {})
        assert revised == "original"

    def test_generated_revise_output_imports_revision_module(self) -> None:
        """Generated code should import build_revision_prompt."""
        from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

        spec = AgentTaskSpec(
            task_prompt="Analyze data.",
            judge_rubric="Evaluate analysis.",
            max_rounds=5,
        )
        source = generate_agent_task_class(spec, "analysis_task")
        assert "agent_task_revision" in source or "revise_generated_output" in source


class TestLegacyGeneratedTaskUpgrade:
    def test_registry_patches_legacy_generated_agent_task(self, tmp_path: Path) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.registry import load_all_custom_scenarios

        scenario_dir = tmp_path / "_custom_scenarios" / "legacy_task"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "scenario_type.txt").write_text("agent_task", encoding="utf-8")
        (scenario_dir / "agent_task.py").write_text(
            """
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class LegacyTaskAgentTask(AgentTaskInterface):
    name = "legacy_task"
    _revision_prompt = "Improve the answer."
    _max_rounds = 3
    _judge_model = "test-model"
    _rubric = "test rubric"

    def get_task_prompt(self, state: dict) -> str:
        return "Do the task."

    def evaluate_output(self, output: str, state: dict, **kwargs) -> AgentTaskResult:
        return AgentTaskResult(score=0.5, reasoning="needs work")

    def get_rubric(self) -> str:
        return self._rubric

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "legacy"

    def revise_output(self, output: str, judge_result: AgentTaskResult, state: dict) -> str:
        if not self._revision_prompt and self._max_rounds <= 1:
            return output
        # Default revision: return original (llm_fn must be injected at runtime)
        return output
""",
            encoding="utf-8",
        )

        loaded = load_all_custom_scenarios(tmp_path)
        cls = loaded["legacy_task"]
        task = cls()

        with patch(
            "autocontext.scenarios.custom.agent_task_revision.revise_generated_output",
            return_value="revised output",
        ) as mock_reviser:
            revised = task.revise_output(
                "original",
                AgentTaskResult(score=0.4, reasoning="weak"),
                {},
            )

        assert revised == "revised output"
        mock_reviser.assert_called_once()

    def test_registry_preserves_custom_revise_output(self, tmp_path: Path) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult
        from autocontext.scenarios.custom.registry import load_all_custom_scenarios

        scenario_dir = tmp_path / "_custom_scenarios" / "custom_task"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "scenario_type.txt").write_text("agent_task", encoding="utf-8")
        (scenario_dir / "agent_task.py").write_text(
            """
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class CustomTaskAgentTask(AgentTaskInterface):
    name = "custom_task"

    def get_task_prompt(self, state: dict) -> str:
        return "Do the custom task."

    def evaluate_output(self, output: str, state: dict, **kwargs) -> AgentTaskResult:
        return AgentTaskResult(score=0.5, reasoning="ok")

    def get_rubric(self) -> str:
        return "test rubric"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "custom"

    def revise_output(self, output: str, judge_result: AgentTaskResult, state: dict) -> str:
        return output + " manual"
""",
            encoding="utf-8",
        )

        loaded = load_all_custom_scenarios(tmp_path)
        task = loaded["custom_task"]()

        with patch(
            "autocontext.scenarios.custom.agent_task_revision.revise_generated_output",
            return_value="unexpected",
        ) as mock_reviser:
            revised = task.revise_output(
                "original",
                AgentTaskResult(score=0.4, reasoning="weak"),
                {},
            )

        assert revised == "original manual"
        mock_reviser.assert_not_called()


# ===========================================================================
# AC-310: Legacy evaluate_output with llm_fn placeholder should be patched
# ===========================================================================


class TestPatchLegacyEvaluateOutput:
    def test_legacy_evaluate_source_is_detected(self) -> None:
        """AC-310: Source containing 'llm_fn must be injected at runtime'
        should be detected as needing the evaluate_output patch."""
        from autocontext.scenarios.custom.agent_task_revision import (
            _LEGACY_EVALUATE_MARKER,
        )

        legacy_source = (
            'def evaluate_output(self, output, state, **kwargs):\n'
            '    def llm_fn(system, user):\n'
            '        raise NotImplementedError("llm_fn must be injected at runtime")\n'
            '    judge = LLMJudge(model=self._judge_model, rubric=self._rubric, llm_fn=llm_fn)\n'
        )
        assert _LEGACY_EVALUATE_MARKER in legacy_source

    def test_patch_replaces_evaluate_output(self, tmp_path: Path) -> None:
        """AC-310: patch_legacy_generated_evaluate_output should replace
        the evaluate_output method on legacy classes."""
        from unittest.mock import MagicMock, patch

        from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
        from autocontext.scenarios.custom.agent_task_revision import (
            patch_legacy_generated_evaluate_output,
        )

        class _LegacyTask(AgentTaskInterface):
            name = "legacy_eval"
            _task_prompt = "Do the task."
            _rubric = "test rubric"
            _judge_model = ""

            def get_task_prompt(self, state: dict) -> str:
                return self._task_prompt

            def evaluate_output(self, output: str, state: dict, **kwargs: object) -> AgentTaskResult:
                raise NotImplementedError("llm_fn must be injected at runtime")

            def get_rubric(self) -> str:
                return self._rubric

            def initial_state(self, seed: int | None = None) -> dict:
                return {}

            def describe_task(self) -> str:
                return "legacy"

        # Write a source file containing the marker
        source_path = tmp_path / "agent_task.py"
        source_path.write_text(
            'raise NotImplementedError("llm_fn must be injected at runtime")',
            encoding="utf-8",
        )

        patched_cls = patch_legacy_generated_evaluate_output(_LegacyTask, source_path)

        mock_result = MagicMock()
        mock_result.score = 0.82
        mock_result.reasoning = "patched evaluation"
        mock_result.dimension_scores = {}
        mock_result.internal_retries = 0

        mock_settings = MagicMock()
        mock_settings.judge_model = "configured-model"
        mock_provider = MagicMock()
        mock_provider.default_model.return_value = "default-model"

        task = patched_cls()
        with (
            patch("autocontext.config.load_settings", return_value=mock_settings),
            patch("autocontext.providers.registry.get_provider", return_value=mock_provider),
            patch("autocontext.execution.judge.LLMJudge.evaluate", return_value=mock_result),
        ):
            result = task.evaluate_output("test output", {})

        # Should NOT raise "llm_fn must be injected at runtime"
        assert result.score == 0.82

    def test_non_legacy_source_is_not_patched(self, tmp_path: Path) -> None:
        """Non-legacy scenarios should keep their original evaluate_output."""
        from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
        from autocontext.scenarios.custom.agent_task_revision import (
            patch_legacy_generated_evaluate_output,
        )

        class _ModernTask(AgentTaskInterface):
            name = "modern"
            _task_prompt = "Task."
            _rubric = "Rubric."

            def get_task_prompt(self, state: dict) -> str:
                return self._task_prompt

            def evaluate_output(self, output: str, state: dict, **kwargs: object) -> AgentTaskResult:
                return AgentTaskResult(score=0.99, reasoning="modern")

            def get_rubric(self) -> str:
                return self._rubric

            def initial_state(self, seed: int | None = None) -> dict:
                return {}

            def describe_task(self) -> str:
                return "modern"

        source_path = tmp_path / "agent_task.py"
        source_path.write_text("# modern code, no llm_fn placeholder", encoding="utf-8")

        patched = patch_legacy_generated_evaluate_output(_ModernTask, source_path)
        task = patched()
        result = task.evaluate_output("test", {})
        assert result.score == 0.99  # original method preserved
