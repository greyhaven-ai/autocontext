"""Pi RPC runtime — spike for deeper session/branch integration.

Prototype runtime that communicates with Pi via HTTP RPC rather than
CLI subprocess. Supports session persistence and branching for retry
strategies.

NOTE: This is a spike (AC-225). The RPC protocol may not be finalized.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from autocontext.runtimes.base import AgentOutput, AgentRuntime
from autocontext.runtimes.pi_artifacts import PiExecutionTrace

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PiRPCConfig:
    """Configuration for the Pi RPC runtime."""

    endpoint: str = "http://localhost:3284"
    api_key: str = ""
    timeout: float = 120.0
    session_persistence: bool = True
    branch_on_retry: bool = True


class PiRPCRuntime(AgentRuntime):
    """Agent runtime that communicates with Pi via HTTP RPC.

    Supports session persistence and branching for multi-round
    improvement loops.
    """

    def __init__(self, config: PiRPCConfig | None = None) -> None:
        self._config = config or PiRPCConfig()
        self._current_session_id: str | None = None

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> AgentOutput:
        import httpx

        payload: dict[str, object] = {"prompt": prompt}
        if system:
            payload["system"] = system
        if schema:
            payload["schema"] = schema
        if self._current_session_id and self._config.session_persistence:
            payload["session_id"] = self._current_session_id

        try:
            response = httpx.post(
                f"{self._config.endpoint}/v1/generate",
                json=payload,
                headers=self._headers(),
                timeout=self._config.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            logger.error("Pi RPC timed out after %.0fs", self._config.timeout)
            return AgentOutput(text="", metadata={"error": "timeout"})
        except httpx.HTTPError as exc:
            logger.error("Pi RPC error: %s", exc)
            return AgentOutput(text="", metadata={"error": "rpc_error", "detail": str(exc)})

        text = data.get("result", data.get("output", ""))
        session_id = data.get("session_id")
        if session_id:
            self._current_session_id = session_id

        trace = PiExecutionTrace(
            session_id=session_id or "",
            prompt_context=prompt,
            raw_output=str(data),
            normalized_output=text,
            cost_usd=data.get("cost_usd", 0.0),
            model=data.get("model", "pi"),
            metadata={"rpc_response": data},
        )

        return AgentOutput(
            text=text,
            cost_usd=data.get("cost_usd", 0.0),
            model=data.get("model", "pi"),
            session_id=session_id,
            metadata={"pi_trace": trace},
        )

    def revise(
        self,
        prompt: str,
        previous_output: str,
        feedback: str,
        system: str | None = None,
    ) -> AgentOutput:
        if self._config.branch_on_retry and self._current_session_id:
            self._current_session_id = self.branch_session(self._current_session_id)

        revision_prompt = (
            f"Revise the following output based on the judge's feedback.\n\n"
            f"## Original Output\n{previous_output}\n\n"
            f"## Judge Feedback\n{feedback}\n\n"
            f"## Original Task\n{prompt}\n\n"
            "Produce an improved version:"
        )
        return self.generate(revision_prompt, system=system)

    def create_session(self) -> str:
        """Create a new Pi session and return its ID."""
        import httpx

        try:
            response = httpx.post(
                f"{self._config.endpoint}/v1/sessions",
                headers=self._headers(),
                timeout=self._config.timeout,
            )
            response.raise_for_status()
            data = response.json()
            session_id: str = data.get("session_id", str(uuid.uuid4()))
            self._current_session_id = session_id
            return session_id
        except httpx.HTTPError as exc:
            logger.error("failed to create Pi session: %s", exc)
            fallback = str(uuid.uuid4())
            self._current_session_id = fallback
            return fallback

    def branch_session(self, session_id: str) -> str:
        """Branch an existing session for retry divergence."""
        import httpx

        try:
            response = httpx.post(
                f"{self._config.endpoint}/v1/sessions/{session_id}/branch",
                headers=self._headers(),
                timeout=self._config.timeout,
            )
            response.raise_for_status()
            data = response.json()
            branch_id: str = data.get("branch_id", str(uuid.uuid4()))
            self._current_session_id = branch_id
            return branch_id
        except httpx.HTTPError as exc:
            logger.error("failed to branch Pi session: %s", exc)
            fallback = str(uuid.uuid4())
            self._current_session_id = fallback
            return fallback

    def resume_session(self, session_id: str) -> None:
        """Resume an existing session by setting the current session ID."""
        self._current_session_id = session_id

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers
