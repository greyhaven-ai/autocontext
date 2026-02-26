from __future__ import annotations

import re
import textwrap

from mts.scenarios.custom.agent_task_spec import AgentTaskSpec


def _class_name(name: str) -> str:
    parts = name.split("_")
    return "".join(p.capitalize() for p in parts) + "AgentTask"


def _safe_identifier(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def generate_agent_task_class(spec: AgentTaskSpec, name: str = "custom_agent_task") -> str:
    """Generate Python source for an AgentTaskInterface subclass from spec.

    Args:
        spec: The agent task specification.
        name: Snake-case name for the generated class.

    Returns:
        Python source code string.
    """
    cls_name = _class_name(name)
    safe_name = _safe_identifier(name)

    task_prompt_repr = repr(spec.task_prompt)
    rubric_repr = repr(spec.judge_rubric)

    source = textwrap.dedent(f'''\
        from __future__ import annotations

        from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
        from mts.execution.judge import LLMJudge


        class {cls_name}(AgentTaskInterface):
            """Generated agent task: {safe_name}."""

            name = "{safe_name}"
            _task_prompt = {task_prompt_repr}
            _rubric = {rubric_repr}
            _output_format = "{spec.output_format}"
            _judge_model = "{spec.judge_model}"

            def get_task_prompt(self, state: dict) -> str:
                return self._task_prompt

            def evaluate_output(self, output: str, state: dict) -> AgentTaskResult:
                def llm_fn(system: str, user: str) -> str:
                    raise NotImplementedError("llm_fn must be injected at runtime")

                judge = LLMJudge(
                    model=self._judge_model,
                    rubric=self._rubric,
                    llm_fn=llm_fn,
                )
                result = judge.evaluate(self._task_prompt, output)
                return AgentTaskResult(
                    score=result.score,
                    reasoning=result.reasoning,
                    dimension_scores=result.dimension_scores,
                )

            def get_rubric(self) -> str:
                return self._rubric

            def initial_state(self) -> dict:
                return {{"task_name": "{safe_name}", "output_format": "{spec.output_format}"}}

            def describe_task(self) -> str:
                return self._task_prompt
    ''')
    return source
