"""Domain-agnostic subagent runtime and task definitions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import RoleExecution
from autocontext.providers.base import OutputSchema


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
    # AC-913: when set, the role asks the backend to constrain generation to
    # this schema. Backends that cannot honor it still answer, and the
    # RoleExecution records constrained=False -- the request is never mistaken
    # for enforcement.
    output_schema: OutputSchema | None = None


class SubagentRuntime:
    """Lightweight subagent runtime abstraction over configured LLM provider."""

    def __init__(self, client: LanguageModelClient, *, constrained_output: bool = True) -> None:
        self.client = client
        # AC-931: gated here rather than at each role runner so a role added
        # later cannot forget the switch. Off means the schema never leaves this
        # method, which is what the test asserts -- the request itself, not the
        # intent to make one.
        self.constrained_output = constrained_output

    def run_task(self, task: SubagentTask) -> RoleExecution:
        output_schema = task.output_schema if self.constrained_output else None
        if output_schema is not None:
            response = self.client.generate_constrained(
                model=task.model,
                prompt=task.prompt,
                max_tokens=task.max_tokens,
                temperature=task.temperature,
                output_schema=output_schema,
                role=task.role,
                system=task.system,
            )
        elif task.system:
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
            metadata=_with_constrained_flag(response.metadata, requested=output_schema is not None),
        )


def _with_constrained_flag(metadata: dict[str, Any], *, requested: bool) -> dict[str, Any]:
    """Stamp every role execution with whether its output was schema-enforced.

    Present on all three paths, not just the constrained one, so "this run was
    unconstrained" is a recorded fact rather than the absence of a key. A
    backend that reported its own value keeps it; anything else is False,
    because a schema that was requested and silently not applied is exactly the
    case this flag exists to expose.
    """
    stamped = dict(metadata)
    if "constrained" not in stamped:
        stamped["constrained"] = False
    if not requested:
        stamped["constrained"] = False
    return stamped
