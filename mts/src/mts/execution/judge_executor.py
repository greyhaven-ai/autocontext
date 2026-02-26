from __future__ import annotations

from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class JudgeExecutor:
    """Executes evaluation by delegating to an AgentTaskInterface."""

    def __init__(self, task: AgentTaskInterface) -> None:
        self.task = task

    def execute(
        self,
        agent_output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
    ) -> AgentTaskResult:
        """Evaluate agent output using the task's evaluate_output method."""
        return self.task.evaluate_output(
            agent_output,
            state,
            reference_context=reference_context,
            required_concepts=required_concepts,
        )
