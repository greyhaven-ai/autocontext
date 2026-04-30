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
    max_retries: int = 2
    retry_backoff_seconds: float = 0.25
    retry_backoff_multiplier: float = 2.0
    max_total_seconds: float = 25 * 60.0
    timeout_warning_fraction: float = 0.8
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
        total_start = time.monotonic()
        max_retries = max(0, int(self._config.max_retries))
        total_attempts = max_retries + 1

        for attempt_index in range(total_attempts):
            attempt = attempt_index + 1
            timeout = self._attempt_timeout(total_start)
            if timeout <= 0:
                return self._timeout_output(
                    attempts=attempt_index,
                    total_elapsed=time.monotonic() - total_start,
                    retry_exhausted=True,
                )

            logger.info(
                "claude-cli invoke: model=%s timeout=%ds attempt=%d/%d",
                self._config.model,
                int(timeout),
                attempt,
                total_attempts,
            )

            start = time.monotonic()
            try:
                result = subprocess.run(
                    args,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - start
                if attempt_index < max_retries and self._has_retry_budget(total_start):
                    delay = self._retry_delay(attempt_index)
                    remaining = self._remaining_total_budget(total_start)
                    if remaining is not None and delay >= remaining:
                        logger.warning(
                            "claude-cli retry skipped reason=timeout_budget_exhausted "
                            "delay=%.2fs remaining=%.2fs elapsed=%.1fs",
                            delay,
                            remaining,
                            elapsed,
                        )
                        return self._timeout_output(
                            attempts=attempt,
                            total_elapsed=time.monotonic() - total_start,
                            retry_exhausted=True,
                        )
                    logger.warning(
                        "claude-cli retry attempt=%d/%d reason=timeout delay=%.2fs elapsed=%.1fs",
                        attempt,
                        max_retries,
                        delay,
                        elapsed,
                    )
                    time.sleep(delay)
                    continue
                return self._timeout_output(
                    attempts=attempt,
                    total_elapsed=time.monotonic() - total_start,
                    retry_exhausted=True,
                )
            except FileNotFoundError:
                logger.error("claude CLI not found. Install Claude Code first.")
                return AgentOutput(text="", metadata={"error": "claude_not_found", "attempts": attempt})

            elapsed = time.monotonic() - start
            logger.debug(
                "claude-cli completed in %.1fs (budget %ds)",
                elapsed,
                int(timeout),
            )
            self._warn_if_slow_attempt(elapsed, timeout, attempt)

            if result.returncode != 0:
                logger.warning("claude CLI exited with code %d: %s", result.returncode, result.stderr[:200])
                # Try to use stdout anyway — sometimes there's partial output
                if not result.stdout.strip():
                    return AgentOutput(
                        text="",
                        metadata={
                            "error": "nonzero_exit",
                            "stderr": result.stderr[:500],
                            "attempts": attempt,
                            "retry_count": attempt_index,
                        },
                    )

            output = self._parse_output(result.stdout)
            output.metadata = {
                **output.metadata,
                "attempts": attempt,
                "retry_count": attempt_index,
            }
            return output

        return self._timeout_output(
            attempts=total_attempts,
            total_elapsed=time.monotonic() - total_start,
            retry_exhausted=True,
        )

    def _remaining_total_budget(self, total_start: float) -> float | None:
        max_total = float(self._config.max_total_seconds)
        if max_total <= 0:
            return None
        return max(0.0, max_total - (time.monotonic() - total_start))

    def _attempt_timeout(self, total_start: float) -> float:
        remaining = self._remaining_total_budget(total_start)
        if remaining is None:
            return float(self._config.timeout)
        return min(float(self._config.timeout), remaining)

    def _has_retry_budget(self, total_start: float) -> bool:
        remaining = self._remaining_total_budget(total_start)
        return remaining is None or remaining > 0

    def _retry_delay(self, retry_index: int) -> float:
        base = max(0.0, float(self._config.retry_backoff_seconds))
        multiplier = max(1.0, float(self._config.retry_backoff_multiplier))
        return base * (multiplier ** retry_index)

    def _warn_if_slow_attempt(self, elapsed: float, timeout: float, attempt: int) -> None:
        fraction = float(self._config.timeout_warning_fraction)
        if fraction <= 0:
            return
        threshold = timeout * fraction
        if elapsed >= threshold:
            logger.warning(
                "claude-cli slow invoke: attempt=%d elapsed=%.1fs timeout=%.0fs",
                attempt,
                elapsed,
                timeout,
            )

    def _timeout_output(
        self,
        *,
        attempts: int,
        total_elapsed: float,
        retry_exhausted: bool,
    ) -> AgentOutput:
        logger.error(
            "claude CLI timed out after %d attempt(s) (timeout=%.0fs total_elapsed=%.1fs)",
            attempts,
            self._config.timeout,
            total_elapsed,
        )
        return AgentOutput(
            text="",
            metadata={
                "error": "timeout",
                "attempts": attempts,
                "retry_count": max(0, attempts - 1),
                "retry_exhausted": retry_exhausted,
                "total_elapsed_seconds": total_elapsed,
            },
        )

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
