"""Domain-agnostic subagent runtime and task definitions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import RoleExecution


@dataclass(slots=True)
class SubagentTask:
    role: str
    model: str
    prompt: str
    max_tokens: int
    temperature: float


class SubagentRuntime:
    """Lightweight subagent runtime abstraction over configured LLM provider."""

    def __init__(self, client: LanguageModelClient) -> None:
        self.client = client

    def run_task(self, task: SubagentTask) -> RoleExecution:
        response = self.client.generate(
            model=task.model,
            prompt=task.prompt,
            max_tokens=task.max_tokens,
            temperature=task.temperature,
            role=task.role,
        )
        return RoleExecution(
            role=task.role,
            content=response.text.strip(),
            usage=response.usage,
            subagent_id=f"{task.role}-{uuid.uuid4().hex[:10]}",
            status="completed",
            metadata=dict(response.metadata),
        )
