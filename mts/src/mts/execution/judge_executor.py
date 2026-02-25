from __future__ import annotations

from mts.execution.judge import LLMJudge
from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class JudgeExecutor:
    """Executes evaluation by delegating to an AgentTaskInterface and/or LLMJudge."""

    def __init__(self, task: AgentTaskInterface, judge: LLMJudge) -> None:
        self.task = task
        self.judge = judge

    def execute(self, agent_output: str, state: dict) -> AgentTaskResult:
        """Evaluate agent output using the task's evaluate_output method."""
        return self.task.evaluate_output(agent_output, state)
