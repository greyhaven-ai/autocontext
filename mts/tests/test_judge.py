from __future__ import annotations

from mts.execution.judge import JudgeResult, LLMJudge, _detect_generated_dimensions
from mts.execution.judge_executor import JudgeExecutor
from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult

VALID_JUDGE_RESPONSE = """
Here is my evaluation:
<!-- JUDGE_RESULT_START -->
{"score": 0.85, "reasoning": "Good output", "dimensions": {"clarity": 0.9, "accuracy": 0.8}}
<!-- JUDGE_RESULT_END -->
"""


def make_mock_llm(response: str = VALID_JUDGE_RESPONSE):
    def mock_llm(system: str, user: str) -> str:
        return response
    return mock_llm


class TestLLMJudge:
    def test_evaluate_valid_response(self) -> None:
        judge = LLMJudge(model="test", rubric="Be good", llm_fn=make_mock_llm())
        result = judge.evaluate("Do task", "My output")
        assert isinstance(result, JudgeResult)
        assert result.score == 0.85
        assert "Good output" in result.reasoning
        assert result.dimension_scores["clarity"] == 0.9
        assert result.dimension_scores["accuracy"] == 0.8
        assert len(result.raw_responses) == 1
        assert result.parse_method == "markers"
        assert result.internal_retries == 0

    def test_multi_sample_averaging(self) -> None:
        responses = [
            '<!-- JUDGE_RESULT_START -->{"score": 0.8, "reasoning": "R1", "dimensions": {"x": 0.6}}<!-- JUDGE_RESULT_END -->',
            '<!-- JUDGE_RESULT_START -->{"score": 0.6, "reasoning": "R2", "dimensions": {"x": 0.4}}<!-- JUDGE_RESULT_END -->',
        ]
        call_count = 0

        def multi_llm(system: str, user: str) -> str:
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        judge = LLMJudge(model="test", rubric="R", llm_fn=multi_llm, samples=2)
        result = judge.evaluate("T", "O")
        assert abs(result.score - 0.7) < 1e-9
        assert abs(result.dimension_scores["x"] - 0.5) < 1e-9
        assert "R1" in result.reasoning
        assert "R2" in result.reasoning
        assert len(result.raw_responses) == 2
        assert result.internal_retries == 0

    def test_build_judge_prompt(self) -> None:
        judge = LLMJudge(model="test", rubric="My rubric", llm_fn=make_mock_llm())
        prompt = judge._build_judge_prompt("task", "output")
        assert "My rubric" in prompt
        assert "task" in prompt
        assert "output" in prompt


    def test_evaluate_with_reference_context(self) -> None:
        resp = (
            '<!-- JUDGE_RESULT_START -->{"score": 0.7, "reasoning": "Factually accurate", '
            '"dimensions": {"clarity": 0.8, "factual_accuracy": 0.6}}<!-- JUDGE_RESULT_END -->'
        )
        judge = LLMJudge(model="test", rubric="Be good", llm_fn=make_mock_llm(resp))
        result = judge.evaluate("Do task", "My output", reference_context="RLM means recursive language model")
        assert result.dimension_scores["factual_accuracy"] == 0.6

    def test_evaluate_with_reference_context_adds_factual_accuracy_default(self) -> None:
        # When reference context provided but judge doesn't return factual_accuracy
        resp = (
            '<!-- JUDGE_RESULT_START -->{"score": 0.75, "reasoning": "ok", '
            '"dimensions": {"clarity": 0.8}}<!-- JUDGE_RESULT_END -->'
        )
        judge = LLMJudge(model="test", rubric="Be good", llm_fn=make_mock_llm(resp))
        result = judge.evaluate("Do task", "My output", reference_context="Some context")
        assert "factual_accuracy" in result.dimension_scores
        assert result.dimension_scores["factual_accuracy"] == 0.75  # defaults to overall score

    def test_evaluate_without_reference_context_no_factual_accuracy(self) -> None:
        resp = (
            '<!-- JUDGE_RESULT_START -->{"score": 0.8, "reasoning": "ok", '
            '"dimensions": {"clarity": 0.9}}<!-- JUDGE_RESULT_END -->'
        )
        judge = LLMJudge(model="test", rubric="Be good", llm_fn=make_mock_llm(resp))
        result = judge.evaluate("Do task", "My output")
        assert "factual_accuracy" not in result.dimension_scores

    def test_build_judge_prompt_with_reference_context(self) -> None:
        judge = LLMJudge(model="test", rubric="My rubric", llm_fn=make_mock_llm())
        prompt = judge._build_judge_prompt("task", "output", reference_context="Domain knowledge here")
        assert "Reference Context" in prompt
        assert "Domain knowledge here" in prompt

    def test_build_judge_prompt_with_required_concepts(self) -> None:
        judge = LLMJudge(model="test", rubric="My rubric", llm_fn=make_mock_llm())
        prompt = judge._build_judge_prompt("task", "output", required_concepts=["concept1", "concept2"])
        assert "Required Concepts" in prompt
        assert "concept1" in prompt
        assert "concept2" in prompt

    def test_internal_retries_tracked(self) -> None:
        """Internal retries are tracked when first parse attempt fails."""
        call_count = 0

        def retry_llm(system: str, user: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "no structured output here"
            return '{"score": 0.7, "reasoning": "OK"}'

        judge = LLMJudge(model="t", rubric="r", llm_fn=retry_llm)
        result = judge.evaluate("t", "o")
        assert result.score == 0.7
        assert result.internal_retries == 1
        assert call_count == 2

    def test_parse_method_plaintext(self) -> None:
        """Parse method is 'plaintext' for plain text score extraction."""
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm("The agent scored well. Score: 0.8"))
        result = judge.evaluate("t", "o")
        assert result.parse_method == "plaintext"
        assert result.score == 0.8


class TestDetectGeneratedDimensions:
    def test_empty_keys(self) -> None:
        assert _detect_generated_dimensions([], "any rubric") is False

    def test_keys_match_rubric(self) -> None:
        assert _detect_generated_dimensions(
            ["code_quality", "test_coverage"],
            "Evaluate code quality and test coverage",
        ) is False

    def test_keys_not_in_rubric(self) -> None:
        assert _detect_generated_dimensions(
            ["originality", "flair"],
            "Evaluate clarity and accuracy",
        ) is True

    def test_case_insensitive(self) -> None:
        assert _detect_generated_dimensions(
            ["Code_Quality"],
            "Check code quality carefully",
        ) is False

    def test_underscore_compound_rubric_term_exact_match(self) -> None:
        assert _detect_generated_dimensions(
            ["technical_accuracy", "clarity", "completeness"],
            "Evaluate on three dimensions: technical_accuracy, clarity, completeness",
        ) is False

    def test_underscore_compound_rubric_term_inline(self) -> None:
        assert _detect_generated_dimensions(
            ["code_quality"],
            "Score the code_quality of the submission",
        ) is False


class TestDimensionsWereGenerated:
    def test_generated_true_when_dims_not_in_rubric(self) -> None:
        resp = (
            '<!-- JUDGE_RESULT_START -->{"score": 0.8, "reasoning": "ok", '
            '"dimensions": {"originality": 0.9, "flair": 0.7}}<!-- JUDGE_RESULT_END -->'
        )
        judge = LLMJudge(model="test", rubric="Evaluate clarity and accuracy", llm_fn=make_mock_llm(resp))
        result = judge.evaluate("Write something", "Hello")
        assert result.dimensions_were_generated is True

    def test_generated_false_when_dims_match_rubric(self) -> None:
        resp = (
            '<!-- JUDGE_RESULT_START -->{"score": 0.8, "reasoning": "ok", '
            '"dimensions": {"clarity": 0.9, "accuracy": 0.7}}<!-- JUDGE_RESULT_END -->'
        )
        judge = LLMJudge(model="test", rubric="Evaluate clarity and accuracy", llm_fn=make_mock_llm(resp))
        result = judge.evaluate("Write something", "Hello")
        assert result.dimensions_were_generated is False


class TestParseJudgeResponse:
    def test_valid(self) -> None:
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        score, reasoning, dims, parse_method = judge._parse_judge_response(VALID_JUDGE_RESPONSE)
        assert score == 0.85
        assert reasoning == "Good output"
        assert dims == {"clarity": 0.9, "accuracy": 0.8}
        assert parse_method == "markers"

    def test_missing_markers_no_score(self) -> None:
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        score, reasoning, dims, parse_method = judge._parse_judge_response("No markers here and no score either")
        assert score == 0.0
        assert "no parseable score" in reasoning.lower()
        assert dims == {}
        assert parse_method == "none"

    def test_missing_markers_with_plaintext_score(self) -> None:
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        score, reasoning, dims, parse_method = judge._parse_judge_response("Overall score: 0.75")
        assert score == 0.75
        assert "[plaintext parse]" not in reasoning
        assert parse_method == "plaintext"

    def test_invalid_json_in_markers(self) -> None:
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        resp = "<!-- JUDGE_RESULT_START -->{bad json<!-- JUDGE_RESULT_END -->"
        score, reasoning, dims, parse_method = judge._parse_judge_response(resp)
        # Falls through to other strategies; no score in "bad json" text
        assert score == 0.0

    def test_score_clamping(self) -> None:
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        resp = '<!-- JUDGE_RESULT_START -->{"score": 1.5, "reasoning": "ok", "dimensions": {"x": -0.5}}<!-- JUDGE_RESULT_END -->'
        score, reasoning, dims, parse_method = judge._parse_judge_response(resp)
        assert score == 1.0
        assert dims["x"] == 0.0

    def test_markers_tried_first(self) -> None:
        """Marker strategy is tried first when markers are present."""
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        resp = (
            '<!-- JUDGE_RESULT_START -->{"score": 0.9, "reasoning": "markers"}'
            '<!-- JUDGE_RESULT_END -->'
        )
        score, reasoning, dims, parse_method = judge._parse_judge_response(resp)
        assert score == 0.9
        assert parse_method == "markers"

    def test_reasoning_clean_no_prefix(self) -> None:
        """Reasoning should not contain parse method prefixes."""
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        resp = 'Some text {"score": 0.8, "reasoning": "Good work"} more text'
        score, reasoning, dims, parse_method = judge._parse_judge_response(resp)
        assert reasoning == "Good work"
        assert "[raw_json parse]" not in reasoning
        assert "[code_block parse]" not in reasoning


class ConcreteTask(AgentTaskInterface):
    def get_task_prompt(self, state: dict) -> str:
        return "Do something"

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        **kwargs: object,
    ) -> AgentTaskResult:
        return AgentTaskResult(score=0.9, reasoning="Great", dimension_scores={"quality": 0.9})

    def get_rubric(self) -> str:
        return "Be great"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "A task"


class TestJudgeExecutor:
    def test_execute(self) -> None:
        task = ConcreteTask()
        executor = JudgeExecutor(task=task)
        result = executor.execute("my output", {})
        assert result.score == 0.9
        assert result.reasoning == "Great"
