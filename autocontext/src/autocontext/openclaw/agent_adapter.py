"""OpenClaw agent adapter for running agents inside the AutoContext harness (AC-193).

Provides:
- OpenClawAgentProtocol: structural typing for OpenClaw-compatible agents
- OpenClawExecutionTrace: structured capture of agent execution traces
- OpenClawClient: LanguageModelClient adapter with retry and timeout
- OpenClawAdapterError: adapter-specific exception
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Protocol, cast, runtime_checkable

from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.core.types import ModelResponse, RoleExecution, RoleUsage


class OpenClawAdapterError(Exception):
    """Raised when the OpenClaw adapter encounters an unrecoverable error."""


@dataclass(slots=True)
class TraceStep:
    """A single reasoning or action step in an execution trace."""

    type: str
    content: str
    duration_ms: int


@dataclass(slots=True)
class TraceToolCall:
    """A single tool invocation in an execution trace."""

    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    duration_ms: int


@dataclass(slots=True)
class OpenClawExecutionTrace:
    """Structured capture of an OpenClaw agent execution.

    Maps the raw trace dict from an OpenClaw agent into typed fields
    for AutoContext evaluation records.
    """

    output: str
    model: str
    steps: list[TraceStep]
    tool_calls: list[TraceToolCall]
    input_tokens: int
    output_tokens: int
    total_duration_ms: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OpenClawExecutionTrace:
        """Parse a raw trace dict into a typed OpenClawExecutionTrace."""
        usage = data.get("usage", {})
        return cls(
            output=str(data.get("output", "")),
            model=str(data.get("model", "")),
            steps=[
                TraceStep(
                    type=str(s.get("type", "")),
                    content=str(s.get("content", "")),
                    duration_ms=int(s.get("duration_ms", 0)),
                )
                for s in data.get("steps", [])
            ],
            tool_calls=[
                TraceToolCall(
                    name=str(tc.get("name", "")),
                    input=dict(tc.get("input", {})),
                    output=dict(tc.get("output", {})),
                    duration_ms=int(tc.get("duration_ms", 0)),
                )
                for tc in data.get("tool_calls", [])
            ],
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            total_duration_ms=int(data.get("total_duration_ms", 0)),
        )

    def to_role_usage(self) -> RoleUsage:
        """Convert trace usage into AutoContext RoleUsage."""
        return RoleUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=self.total_duration_ms,
            model=self.model,
        )

    def to_evaluation_summary(self) -> dict[str, Any]:
        """Build a summary dict suitable for AutoContext evaluation records."""
        return {
            "steps": len(self.steps),
            "tool_calls": len(self.tool_calls),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_duration_ms": self.total_duration_ms,
        }

    def to_role_execution(self, role: str) -> RoleExecution:
        """Convert trace into an AutoContext RoleExecution record."""
        return RoleExecution(
            role=role,
            content=self.output,
            usage=self.to_role_usage(),
            subagent_id=f"openclaw-{uuid.uuid4().hex[:10]}",
            status="completed",
        )


@runtime_checkable
class OpenClawAgentProtocol(Protocol):
    """Structural typing protocol for OpenClaw-compatible agents.

    Any object with an `execute` method matching this signature can be
    used as an OpenClaw agent inside the AutoContext harness.
    """

    def execute(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute a prompt and return a trace dict."""
        ...


@dataclass
class OpenClawClient(LanguageModelClient):
    """LanguageModelClient adapter for OpenClaw agents.

    Wraps an OpenClawAgentProtocol-compatible agent with retry logic,
    timeout enforcement, and structured trace capture.
    """

    agent: Any
    max_retries: int = 2
    timeout_seconds: float = 30.0
    retry_base_delay: float = 0.25
    last_trace: OpenClawExecutionTrace | None = field(default=None, init=False, repr=False)

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        """Execute the OpenClaw agent and return an AutoContext ModelResponse."""
        trace_dict = self._execute_with_retry(
            prompt=prompt,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        trace = OpenClawExecutionTrace.from_dict(trace_dict)
        self.last_trace = trace
        return ModelResponse(text=trace.output, usage=trace.to_role_usage())

    def generate_multiturn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        """Combine system + messages into a single prompt for the OpenClaw agent."""
        user_parts = [m["content"] for m in messages if m.get("role") == "user"]
        combined = system + "\n\n" + "\n\n".join(user_parts)
        return self.generate(
            model=model,
            prompt=combined,
            max_tokens=max_tokens,
            temperature=temperature,
            role=role,
        )

    def _execute_with_retry(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Call the agent with retry and timeout logic."""
        attempts = 1 + self.max_retries
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                return self._execute_with_timeout(
                    prompt=prompt,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except Exception as exc:
                last_error = exc
                if attempt < attempts - 1:
                    delay = self.retry_base_delay * (2 ** attempt)
                    time.sleep(delay)

        raise OpenClawAdapterError(
            f"OpenClaw agent failed after {attempts} attempts: {last_error}",
        ) from last_error

    def _execute_with_timeout(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Execute with a hard caller-facing timeout.

        The worker runs on a daemon thread so a timed-out agent does not block
        the main harness loop while it finishes in the background.
        """
        result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

        def _run() -> None:
            try:
                result = self.agent.execute(
                    prompt=prompt,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=None,
                )
            except Exception as exc:  # pragma: no cover - surfaced via queue
                result_queue.put(("error", exc))
                return
            result_queue.put(("result", result))

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout=self.timeout_seconds)
        if worker.is_alive():
            raise OpenClawAdapterError(
                f"OpenClaw agent timed out after {self.timeout_seconds}s",
            )
        try:
            status, payload = result_queue.get_nowait()
        except Empty as exc:  # pragma: no cover - defensive guard
            raise OpenClawAdapterError("OpenClaw agent exited without returning a trace") from exc
        if status == "error":
            raise OpenClawAdapterError(str(payload)) from cast(Exception, payload)
        return cast(dict[str, Any], payload)
