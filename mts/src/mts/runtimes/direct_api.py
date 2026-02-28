"""Direct API runtime — uses an LLMProvider for generation/revision."""

from __future__ import annotations

from mts.providers.base import LLMProvider
from mts.runtimes.base import AgentOutput, AgentRuntime


class DirectAPIRuntime(AgentRuntime):
    """Agent runtime that calls an LLM provider directly.

    This is the simplest runtime — equivalent to what the experiment
    scripts and SimpleAgentTask do today.
    """

    def __init__(
        self,
        provider: LLMProvider,
        model: str | None = None,
    ) -> None:
        self._provider = provider
        self._model = model

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> AgentOutput:
        sys_prompt = system or "You are a skilled writer and analyst. Complete the task precisely."
        result = self._provider.complete(
            system_prompt=sys_prompt,
            user_prompt=prompt,
            model=self._model,
        )
        return AgentOutput(
            text=result.text,
            cost_usd=result.cost_usd,
            model=result.model,
        )

    def revise(
        self,
        prompt: str,
        previous_output: str,
        feedback: str,
        system: str | None = None,
    ) -> AgentOutput:
        revision_prompt = (
            f"Revise the following output based on the judge's feedback.\n\n"
            f"## Original Output\n{previous_output}\n\n"
            f"## Judge Feedback\n{feedback}\n\n"
            f"## Original Task\n{prompt}\n\n"
            "Produce an improved version:"
        )
        sys_prompt = system or "You are revising content based on expert feedback. Improve the output."
        result = self._provider.complete(
            system_prompt=sys_prompt,
            user_prompt=revision_prompt,
            model=self._model,
        )
        return AgentOutput(
            text=result.text,
            cost_usd=result.cost_usd,
            model=result.model,
        )
