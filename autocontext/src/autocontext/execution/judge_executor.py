from __future__ import annotations

from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


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
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        """Evaluate agent output using the task's evaluate_output method."""
        # Run context preparation if the task supports it
        prepared_state = self.task.prepare_context(dict(state))
        context_errors = self.task.validate_context(prepared_state)
        if context_errors:
            return AgentTaskResult(
                score=0.0,
                reasoning=f"Context validation failed: {'; '.join(context_errors)}",
                dimension_scores={},
            )

        return self.task.evaluate_output(
            agent_output,
            prepared_state,
            reference_context=reference_context,
            required_concepts=required_concepts,
            calibration_examples=calibration_examples,
            pinned_dimensions=pinned_dimensions,
        )
