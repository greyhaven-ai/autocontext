"""Claude Code CLI runtime — wraps `claude -p` for agent execution.

Uses Claude Code's print mode as a one-shot agent runtime with full
tool access, structured output, and cost tracking.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field

from autocontext.runtimes.base import AgentOutput, AgentRuntime

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ClaudeCLIConfig:
    """Configuration for the Claude CLI runtime."""

    model: str = "sonnet"
    fallback_model: str | None = "haiku"
    tools: str | None = None  # None = default tools, "" = no tools
    permission_mode: str = "bypassPermissions"
    session_persistence: bool = False
    session_id: str | None = None  # Set to maintain context across rounds
    timeout: float = 600.0  # AC-588: per-call default (was 300, AC-570 raised from 120)
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    extra_args: list[str] = field(default_factory=list)


class ClaudeCLIRuntime(AgentRuntime):
    """Agent runtime that invokes `claude -p` (Claude Code print mode).

    Requires the Claude CLI to be installed and authenticated.

    Features:
    - Full Claude Code tool access (Bash, Read, Write, Edit, etc.)
    - Structured JSON output via --json-schema
    - Cost tracking from JSON output (total_cost_usd)
    - Session management for multi-round improvement loops
    - Model selection with fallback
    """

    def __init__(self, config: ClaudeCLIConfig | None = None) -> None:
        self._config = config or ClaudeCLIConfig()
        self._total_cost: float = 0.0
        self._claude_path = shutil.which("claude")

    @property
    def available(self) -> bool:
        """Check if the claude CLI is available."""
        return self._claude_path is not None

    @property
    def total_cost(self) -> float:
        """Accumulated cost across all invocations."""
        return self._total_cost

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> AgentOutput:
        args = self._build_args(system=system, schema=schema)
        return self._invoke(prompt, args)

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
        args = self._build_args(system=system)
        return self._invoke(revision_prompt, args)

    def _build_args(
        self,
        system: str | None = None,
        schema: dict | None = None,
    ) -> list[str]:
        """Build the claude CLI argument list."""
        claude = self._claude_path or "claude"
        args = [claude, "-p", "--output-format", "json"]

        # Model
        args.extend(["--model", self._config.model])
        if self._config.fallback_model:
            args.extend(["--fallback-model", self._config.fallback_model])

        # Tools
        if self._config.tools is not None:
            args.extend(["--tools", self._config.tools])

        # Permissions
        args.extend(["--permission-mode", self._config.permission_mode])

        # Session
        if not self._config.session_persistence:
            args.append("--no-session-persistence")
        if self._config.session_id:
            args.extend(["--session-id", self._config.session_id])

        # System prompt
        if system:
            args.extend(["--system-prompt", system])
        elif self._config.system_prompt:
            args.extend(["--system-prompt", self._config.system_prompt])

        if self._config.append_system_prompt:
            args.extend(["--append-system-prompt", self._config.append_system_prompt])

        # JSON schema
        if schema:
            args.extend(["--json-schema", json.dumps(schema)])

        # Extra args
        args.extend(self._config.extra_args)

        return args

    def _invoke(self, prompt: str, args: list[str]) -> AgentOutput:
        """Execute claude -p and parse the JSON result."""
        logger.info(
            "claude-cli invoke: model=%s timeout=%ds",
            self._config.model,
            int(self._config.timeout),
        )

        start = time.monotonic()
        try:
            result = subprocess.run(
                args,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._config.timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error("claude CLI timed out after %.0fs", self._config.timeout)
            return AgentOutput(text="", metadata={"error": "timeout"})
        except FileNotFoundError:
            logger.error("claude CLI not found. Install Claude Code first.")
            return AgentOutput(text="", metadata={"error": "claude_not_found"})

        elapsed = time.monotonic() - start
        logger.debug(
            "claude-cli completed in %.1fs (budget %ds)",
            elapsed,
            int(self._config.timeout),
        )

        if result.returncode != 0:
            logger.warning("claude CLI exited with code %d: %s", result.returncode, result.stderr[:200])
            # Try to use stdout anyway — sometimes there's partial output
            if not result.stdout.strip():
                return AgentOutput(
                    text="",
                    metadata={"error": "nonzero_exit", "stderr": result.stderr[:500]},
                )

        return self._parse_output(result.stdout)

    def _parse_output(self, raw: str) -> AgentOutput:
        """Parse JSON output from claude -p --output-format json."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # Fall back to treating raw output as text
            logger.warning("failed to parse claude CLI JSON output, using raw text")
            return AgentOutput(text=raw.strip())

        text = data.get("result", "")
        cost = data.get("total_cost_usd")
        if cost is not None:
            self._total_cost += cost

        session_id = data.get("session_id")
        model = None

        # Extract model from modelUsage if available
        model_usage = data.get("modelUsage", {})
        if model_usage:
            model = next(iter(model_usage.keys()), None)

        structured = data.get("structured_output")

        return AgentOutput(
            text=text,
            structured=structured,
            cost_usd=cost,
            model=model,
            session_id=session_id,
            metadata={
                "duration_ms": data.get("duration_ms"),
                "duration_api_ms": data.get("duration_api_ms"),
                "num_turns": data.get("num_turns"),
                "is_error": data.get("is_error", False),
                "usage": data.get("usage", {}),
            },
        )


def create_session_runtime(
    model: str = "sonnet",
    tools: str | None = None,
    system_prompt: str | None = None,
) -> ClaudeCLIRuntime:
    """Create a ClaudeCLIRuntime with a shared session ID for multi-round loops.

    The session ID allows Claude Code to maintain context across rounds,
    so it remembers previous outputs and judge feedback.
    """
    config = ClaudeCLIConfig(
        model=model,
        tools=tools,
        session_id=str(uuid.uuid4()),
        session_persistence=True,
        system_prompt=system_prompt,
    )
    return ClaudeCLIRuntime(config)
