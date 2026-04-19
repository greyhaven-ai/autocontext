"""AC-574 — end-to-end: ScenarioCreator.create() retries on intent drift."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator
from autocontext.scenarios.custom.agent_task_designer import SPEC_END, SPEC_START
from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

_VALID_TEXT_SPEC = AgentTaskSpec(
    task_prompt="Write a haiku about distributed systems.",
    judge_rubric="Score syllable accuracy, relevance, imagery 0-1 each.",
    output_format="free_text",
    judge_model="",
)

_INVALID_CODE_SPEC = AgentTaskSpec(
    task_prompt="Implement a Python function that writes a haiku.",
    judge_rubric="Score code quality, tests, documentation 0-1 each.",
    output_format="code",
    judge_model="",
)


def _spec_response(spec: AgentTaskSpec) -> str:
    data = {
        "task_prompt": spec.task_prompt,
        "judge_rubric": spec.judge_rubric,
        "output_format": spec.output_format,
        "judge_model": spec.judge_model,
    }
    return f"prefix\n{SPEC_START}\n{json.dumps(data)}\n{SPEC_END}\nsuffix"


def _scripted_llm_fn(responses: list[str]) -> Callable[[str, str], str]:
    calls: list[tuple[str, str]] = []

    def fn(system: str, user: str) -> str:
        if not responses:
            raise AssertionError("llm_fn called more times than responses available")
        calls.append((system, user))
        return responses.pop(0)

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


class TestAgentTaskCreatorRetry:
    def test_creator_retries_on_intent_drift_and_succeeds(
        self,
        tmp_path: Path,
    ) -> None:
        """ScenarioCreator.create() must use the retry-capable designer.

        First LLM call returns code-format spec for a text description
        (triggers validate_intent drift). Second call returns valid spec.
        Creator must succeed, not raise.
        """
        llm_fn = _scripted_llm_fn([
            _spec_response(_INVALID_CODE_SPEC),
            _spec_response(_VALID_TEXT_SPEC),
        ])

        creator = AgentTaskCreator(llm_fn=llm_fn, knowledge_root=tmp_path)

        scenario_instance = creator.create("Write a haiku about distributed systems.")

        # Proves the retry happened at the creator layer.
        assert len(llm_fn.calls) == 2  # type: ignore[attr-defined]
        # Proves creator returned a live instance (second attempt's spec was
        # codegenned, loaded, and instantiated successfully).
        assert scenario_instance is not None
        # A persisted agent_task.py exists for the returned scenario.
        persisted_files = list((tmp_path / "_custom_scenarios").rglob("agent_task.py"))
        assert len(persisted_files) == 1, (
            f"expected one persisted agent_task.py, got {persisted_files}"
        )

    def test_creator_retries_on_unparseable_designer_response(
        self,
        tmp_path: Path,
    ) -> None:
        """Malformed first responses should still get a second design attempt."""
        llm_fn = _scripted_llm_fn([
            "not delimited json",
            _spec_response(_VALID_TEXT_SPEC),
        ])

        creator = AgentTaskCreator(llm_fn=llm_fn, knowledge_root=tmp_path)

        scenario_instance = creator.create("Write a haiku about distributed systems.")

        assert len(llm_fn.calls) == 2  # type: ignore[attr-defined]
        assert scenario_instance is not None
        persisted_files = list((tmp_path / "_custom_scenarios").rglob("agent_task.py"))
        assert len(persisted_files) == 1
