from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True)
class AgentTaskResult:
    """Result of evaluating an agent's output on a task."""

    score: float  # 0.0 to 1.0
    reasoning: str
    dimension_scores: dict[str, float] = field(default_factory=dict)


class AgentTaskInterface(ABC):
    """Abstract interface for agent task scenarios."""

    @abstractmethod
    def get_task_prompt(self, state: dict) -> str:
        """Return the task prompt for the agent."""

    @abstractmethod
    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
    ) -> AgentTaskResult:
        """Evaluate the agent's output against the task criteria."""

    @abstractmethod
    def get_rubric(self) -> str:
        """Return the evaluation rubric."""

    @abstractmethod
    def initial_state(self, seed: int | None = None) -> dict:
        """Return the initial state for this task."""

    @abstractmethod
    def describe_task(self) -> str:
        """Return a human-readable description of the task."""
