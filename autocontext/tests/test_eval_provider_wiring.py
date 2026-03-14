"""Tests for AC-241: Fix generated agent-task eval provider wiring.

Ensures generated agent-task scenarios auto-wire the provider into
evaluate_output instead of using a broken llm_fn placeholder.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
from autocontext.scenarios.custom.agent_task_validator import validate_execution, validate_syntax

# A minimal valid spec for testing.
SAMPLE_SPEC = AgentTaskSpec(
    task_prompt="Write a haiku about testing.",
    judge_rubric="Evaluate haiku quality: 5-7-5 syllable structure.",
)


# ---------------------------------------------------------------------------
# Codegen: generated code must not contain placeholder llm_fn
# ---------------------------------------------------------------------------

class TestCodegenNoPlaceholder:
    def test_generated_code_does_not_contain_llm_fn_placeholder(self) -> None:
        """The broken 'llm_fn must be injected at runtime' pattern must be gone."""
        source = generate_agent_task_class(SAMPLE_SPEC, name="haiku_task")
        assert "llm_fn must be injected at runtime" not in source

    def test_generated_code_uses_provider(self) -> None:
        """Generated evaluate_output should use get_provider / load_settings."""
        source = generate_agent_task_class(SAMPLE_SPEC, name="haiku_task")
        assert "get_provider" in source
        assert "load_settings" in source

    def test_generated_code_passes_provider_to_judge(self) -> None:
        """LLMJudge should receive provider=, not llm_fn=."""
        source = generate_agent_task_class(SAMPLE_SPEC, name="haiku_task")
        assert "provider=provider" in source
        assert "llm_fn=" not in source

    def test_generated_code_resolves_model_from_settings_when_empty(self) -> None:
        """Generated evaluate_output should fall back to runtime judge model resolution."""
        source = generate_agent_task_class(SAMPLE_SPEC, name="haiku_task")
        assert "settings.judge_model" in source
        assert "provider.default_model()" in source

    def test_generated_code_syntax_valid(self) -> None:
        source = generate_agent_task_class(SAMPLE_SPEC, name="haiku_task")
        errors = validate_syntax(source)
        assert errors == [], f"Syntax errors: {errors}"

    def test_generated_code_execution_valid(self) -> None:
        source = generate_agent_task_class(SAMPLE_SPEC, name="haiku_task")
        errors = validate_execution(source)
        assert errors == [], f"Execution errors: {errors}"


# ---------------------------------------------------------------------------
# Generated evaluate_output calls provider correctly
# ---------------------------------------------------------------------------

class TestGeneratedEvaluateOutput:
    def _build_instance(self, spec: AgentTaskSpec | None = None, name: str = "test_task") -> object:
        """Generate, compile, and instantiate a generated agent task."""
        source = generate_agent_task_class(spec or SAMPLE_SPEC, name=name)
        ns: dict = {}
        exec(compile(source, "<test>", "exec"), ns)  # noqa: S102
        cls_name = name.split("_")
        pascal = "".join(p.capitalize() for p in cls_name) + "AgentTask"
        return ns[pascal]()

    def test_evaluate_output_calls_provider(self) -> None:
        """evaluate_output should call get_provider and pass it to LLMJudge."""
        instance = self._build_instance()

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.score = 0.8
        mock_result.reasoning = "Good"
        mock_result.dimension_scores = {}
        mock_result.internal_retries = 0

        with (
            patch(
                "autocontext.config.load_settings",
                return_value=MagicMock(),
            ) as mock_load,
            patch(
                "autocontext.providers.registry.get_provider",
                return_value=mock_provider,
            ) as mock_get,
            patch(
                "autocontext.execution.judge.LLMJudge.evaluate",
                return_value=mock_result,
            ),
        ):
            result = instance.evaluate_output("test output", {})
            mock_load.assert_called_once()
            mock_get.assert_called_once()
            assert result.score == 0.8

    def test_evaluate_output_no_not_implemented_error(self) -> None:
        """evaluate_output must not raise NotImplementedError."""
        instance = self._build_instance()

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.score = 0.5
        mock_result.reasoning = "OK"
        mock_result.dimension_scores = {}
        mock_result.internal_retries = 0

        with (
            patch("autocontext.config.load_settings", return_value=MagicMock()),
            patch("autocontext.providers.registry.get_provider", return_value=mock_provider),
            patch("autocontext.execution.judge.LLMJudge.evaluate", return_value=mock_result),
        ):
            # This should NOT raise NotImplementedError
            result = instance.evaluate_output("some output", {})
            assert result.score == 0.5

    def test_evaluate_output_uses_runtime_judge_model_when_spec_model_empty(self) -> None:
        """Empty judge_model should fall back to configured settings judge model."""
        instance = self._build_instance()

        mock_provider = MagicMock()
        mock_provider.default_model.return_value = "provider-fallback-model"
        mock_result = MagicMock()
        mock_result.score = 0.6
        mock_result.reasoning = "OK"
        mock_result.dimension_scores = {}
        mock_result.internal_retries = 0

        with (
            patch("autocontext.config.load_settings", return_value=MagicMock(judge_model="runtime-judge-model")),
            patch("autocontext.providers.registry.get_provider", return_value=mock_provider),
            patch("autocontext.execution.judge.LLMJudge.evaluate", return_value=mock_result),
            patch("autocontext.execution.judge.LLMJudge.__init__", return_value=None) as mock_init,
        ):
            result = instance.evaluate_output("some output", {})
            assert result.score == 0.6
            assert mock_init.call_args.kwargs["model"] == "runtime-judge-model"

    def test_evaluate_output_passes_reference_context(self) -> None:
        """Reference context should be forwarded to the judge."""
        spec = AgentTaskSpec(
            task_prompt="Write about RLMs.",
            judge_rubric="Check accuracy",
            reference_context="RLM = Recursive Language Model",
            required_concepts=["context folding"],
        )
        instance = self._build_instance(spec, name="rlm_task")

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.score = 0.9
        mock_result.reasoning = "Accurate"
        mock_result.dimension_scores = {}
        mock_result.internal_retries = 0

        with (
            patch("autocontext.config.load_settings", return_value=MagicMock()),
            patch("autocontext.providers.registry.get_provider", return_value=mock_provider),
            patch("autocontext.execution.judge.LLMJudge.evaluate", return_value=mock_result) as mock_eval,
        ):
            instance.evaluate_output(
                "test output", {},
                reference_context="Custom ref",
                required_concepts=["custom concept"],
            )
            # Verify judge.evaluate was called with the passed-in context
            call_kwargs = mock_eval.call_args
            assert call_kwargs.kwargs.get("reference_context") == "Custom ref"
            assert call_kwargs.kwargs.get("required_concepts") == ["custom concept"]

    def test_evaluate_output_falls_back_to_class_defaults(self) -> None:
        """When no ref context is passed, fall back to class defaults."""
        spec = AgentTaskSpec(
            task_prompt="Write about RLMs.",
            judge_rubric="Check accuracy",
            reference_context="Default ref context",
            required_concepts=["default concept"],
        )
        instance = self._build_instance(spec, name="default_task")

        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.score = 0.7
        mock_result.reasoning = "OK"
        mock_result.dimension_scores = {}
        mock_result.internal_retries = 0

        with (
            patch("autocontext.config.load_settings", return_value=MagicMock()),
            patch("autocontext.providers.registry.get_provider", return_value=mock_provider),
            patch("autocontext.execution.judge.LLMJudge.evaluate", return_value=mock_result) as mock_eval,
        ):
            instance.evaluate_output("test output", {})
            call_kwargs = mock_eval.call_args
            assert call_kwargs.kwargs.get("reference_context") == "Default ref context"
            assert call_kwargs.kwargs.get("required_concepts") == ["default concept"]


# ---------------------------------------------------------------------------
# Validator catches placeholder pattern
# ---------------------------------------------------------------------------

class TestValidatorCatchesPlaceholder:
    def test_validator_rejects_llm_fn_placeholder(self) -> None:
        """validate_execution should fail by exercising the broken eval path."""
        # Hand-craft source with the old broken pattern
        broken_source = '''\
from __future__ import annotations
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.execution.judge import LLMJudge

class BrokenAgentTask(AgentTaskInterface):
    name = "broken"
    _task_prompt = "test"
    _rubric = "test"
    _judge_model = "test-model"

    def get_task_prompt(self, state: dict) -> str:
        return self._task_prompt

    def evaluate_output(self, output: str, state: dict, **kwargs) -> AgentTaskResult:
        def llm_fn(system: str, user: str) -> str:
            raise NotImplementedError("llm_fn must be injected at runtime")
        judge = LLMJudge(model=self._judge_model, rubric=self._rubric, llm_fn=llm_fn)
        result = judge.evaluate(self._task_prompt, output)
        return AgentTaskResult(score=result.score, reasoning=result.reasoning)

    def get_rubric(self) -> str:
        return self._rubric

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return self._task_prompt
'''
        errors = validate_execution(broken_source)
        assert any("evaluate_output()" in e or "llm_fn" in e for e in errors), (
            f"Expected validation error about llm_fn placeholder, got: {errors}"
        )


# ---------------------------------------------------------------------------
# Revise_output comment is cleaned up too
# ---------------------------------------------------------------------------

class TestReviseOutputCleaned:
    def test_revise_output_no_llm_fn_comment(self) -> None:
        """The revise_output method should not reference llm_fn in comments."""
        source = generate_agent_task_class(SAMPLE_SPEC, name="clean_task")
        # The old comment "llm_fn must be injected at runtime" in revise_output
        # should be removed or rewritten
        assert source.count("llm_fn") == 0
