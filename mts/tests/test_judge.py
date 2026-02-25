from __future__ import annotations

from mts.execution.judge import JudgeResult, LLMJudge
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

    def test_build_judge_prompt(self) -> None:
        judge = LLMJudge(model="test", rubric="My rubric", llm_fn=make_mock_llm())
        prompt = judge._build_judge_prompt("task", "output")
        assert "My rubric" in prompt
        assert "task" in prompt
        assert "output" in prompt


class TestParseJudgeResponse:
    def test_valid(self) -> None:
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        score, reasoning, dims = judge._parse_judge_response(VALID_JUDGE_RESPONSE)
        assert score == 0.85
        assert reasoning == "Good output"
        assert dims == {"clarity": 0.9, "accuracy": 0.8}

    def test_missing_markers(self) -> None:
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        score, reasoning, dims = judge._parse_judge_response("No markers here")
        assert score == 0.0
        assert "missing" in reasoning.lower()
        assert dims == {}

    def test_invalid_json(self) -> None:
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        resp = "<!-- JUDGE_RESULT_START -->{bad json<!-- JUDGE_RESULT_END -->"
        score, reasoning, dims = judge._parse_judge_response(resp)
        assert score == 0.0
        assert "invalid" in reasoning.lower()

    def test_score_clamping(self) -> None:
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        resp = '<!-- JUDGE_RESULT_START -->{"score": 1.5, "reasoning": "ok", "dimensions": {"x": -0.5}}<!-- JUDGE_RESULT_END -->'
        score, reasoning, dims = judge._parse_judge_response(resp)
        assert score == 1.0
        assert dims["x"] == 0.0


class ConcreteTask(AgentTaskInterface):
    def get_task_prompt(self, state: dict) -> str:
        return "Do something"

    def evaluate_output(self, output: str, state: dict) -> AgentTaskResult:
        return AgentTaskResult(score=0.9, reasoning="Great", dimension_scores={"quality": 0.9})

    def get_rubric(self) -> str:
        return "Be great"

    def initial_state(self) -> dict:
        return {}

    def describe_task(self) -> str:
        return "A task"


class TestJudgeExecutor:
    def test_execute(self) -> None:
        task = ConcreteTask()
        judge = LLMJudge(model="t", rubric="r", llm_fn=make_mock_llm())
        executor = JudgeExecutor(task=task, judge=judge)
        result = executor.execute("my output", {})
        assert result.score == 0.9
        assert result.reasoning == "Great"
