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
    # ERP-67 structural role isolation: when set, `prompt` is the untrusted user
    # turn and `system` is the trusted system turn, delivered via
    # `generate_multiturn` so role-capable backends keep untrusted content out of
    # the system prompt. Empty (the default, and every call site today) → the
    # single-prompt `generate` path, byte-identical to prior behaviour.
    system: str = ""


class SubagentRuntime:
    """Lightweight subagent runtime abstraction over configured LLM provider."""

    def __init__(self, client: LanguageModelClient) -> None:
        self.client = client

    def run_task(self, task: SubagentTask) -> RoleExecution:
        if task.system:
            response = self.client.generate_multiturn(
                model=task.model,
                system=task.system,
                messages=[{"role": "user", "content": task.prompt}],
                max_tokens=task.max_tokens,
                temperature=task.temperature,
                role=task.role,
            )
        else:
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
