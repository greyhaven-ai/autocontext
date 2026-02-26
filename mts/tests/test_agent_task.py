from __future__ import annotations

from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from mts.scenarios.custom.agent_task_spec import AgentTaskSpec


class ConcreteAgentTask(AgentTaskInterface):
    def get_task_prompt(self, state: dict) -> str:
        return "Write a haiku about testing."

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
    ) -> AgentTaskResult:
        score = 0.8 if "test" in output.lower() else 0.3
        return AgentTaskResult(score=score, reasoning="Evaluated", dimension_scores={"relevance": score})

    def get_rubric(self) -> str:
        return "Must be a haiku about testing."

    def initial_state(self, seed: int | None = None) -> dict:
        return {"topic": "testing"}

    def describe_task(self) -> str:
        return "Write a haiku about testing."


class TestAgentTaskInterface:
    def test_subclass_and_use(self) -> None:
        task = ConcreteAgentTask()
        state = task.initial_state()
        assert state == {"topic": "testing"}
        prompt = task.get_task_prompt(state)
        assert "haiku" in prompt
        result = task.evaluate_output("A test in the night", state)
        assert result.score == 0.8
        assert result.reasoning == "Evaluated"
        assert result.dimension_scores == {"relevance": 0.8}

    def test_describe_and_rubric(self) -> None:
        task = ConcreteAgentTask()
        assert "haiku" in task.describe_task()
        assert "haiku" in task.get_rubric()


class TestAgentTaskResult:
    def test_creation(self) -> None:
        r = AgentTaskResult(score=0.5, reasoning="ok", dimension_scores={"a": 0.5})
        assert r.score == 0.5
        assert r.reasoning == "ok"
        assert r.dimension_scores == {"a": 0.5}

    def test_default_dimensions(self) -> None:
        r = AgentTaskResult(score=1.0, reasoning="perfect")
        assert r.dimension_scores == {}


class TestAgentTaskSpec:
    def test_creation(self) -> None:
        spec = AgentTaskSpec(task_prompt="Do X", judge_rubric="Evaluate X")
        assert spec.task_prompt == "Do X"
        assert spec.output_format == "free_text"
        assert spec.judge_model == "claude-sonnet-4-20250514"
        assert spec.difficulty_tiers is None

    def test_reference_context_fields(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Write about RLMs",
            judge_rubric="Accuracy",
            reference_context="RLM = Recursive Language Model",
            reference_sources=["https://example.com/rlm"],
            required_concepts=["context folding", "sub-LLM delegation"],
        )
        assert spec.reference_context == "RLM = Recursive Language Model"
        assert spec.reference_sources == ["https://example.com/rlm"]
        assert spec.required_concepts == ["context folding", "sub-LLM delegation"]

    def test_reference_context_defaults_none(self) -> None:
        spec = AgentTaskSpec(task_prompt="Do X", judge_rubric="Evaluate X")
        assert spec.reference_context is None
        assert spec.reference_sources is None
        assert spec.required_concepts is None

    def test_with_options(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Code Y",
            judge_rubric="Check Y",
            output_format="code",
            judge_model="custom-model",
            difficulty_tiers=[{"level": 1, "description": "easy"}],
        )
        assert spec.output_format == "code"
        assert spec.judge_model == "custom-model"
        assert len(spec.difficulty_tiers) == 1
