"""Base agent runtime interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True)
class AgentOutput:
    """Output from an agent runtime."""

    text: str
    structured: dict | None = None
    cost_usd: float | None = None
    model: str | None = None
    session_id: str | None = None
    metadata: dict = field(default_factory=dict)


class AgentRuntime(ABC):
    """Abstract base for agent runtimes.

    MTS uses runtimes to generate and revise content. The runtime
    could be a direct API call, a Claude Code CLI invocation,
    or any other agent framework.
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> AgentOutput:
        """Generate initial output for a task.

        Args:
            prompt: The task prompt / user instruction.
            system: Optional system prompt.
            schema: Optional JSON schema for structured output.

        Returns:
            AgentOutput with the generated text and metadata.
        """
        ...

    @abstractmethod
    def revise(
        self,
        prompt: str,
        previous_output: str,
        feedback: str,
        system: str | None = None,
    ) -> AgentOutput:
        """Revise output based on judge feedback.

        Args:
            prompt: The original task prompt.
            previous_output: The output being revised.
            feedback: Judge reasoning / feedback.
            system: Optional system prompt.

        Returns:
            AgentOutput with the revised text and metadata.
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable runtime name."""
        return self.__class__.__name__
