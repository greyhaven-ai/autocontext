"""Claude Code CLI runtime — wraps `claude -p` for agent execution.

Uses Claude Code's print mode as a one-shot agent runtime with full
tool access, structured output, and cost tracking.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from autocontext.runtimes.base import AgentOutput, AgentRuntime
from autocontext.runtimes.runtime_budget import RuntimeBudget, RuntimeBudgetExpired

logger = logging.getLogger(__name__)

# AC-761 / AC-735: how long we let the drain after a timeout-kill take
# before giving up entirely. claude-cli helper processes may keep pipe
# fds open even after the parent dies, so we cap the wait rather than
# relying on subprocess.run's unbounded inner communicate() drain.
_TIMEOUT_KILL_GRACE_SECONDS = 5.0


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Send SIGKILL to the whole process group of `proc`.

    AC-761 / AC-735: `subprocess.run`'s built-in timeout handling calls
    `proc.kill()` which only targets the immediate child. claude-cli is
    a Node script that spawns helper processes; those grandchildren
    inherit the parent's pipe fds, so killing the parent alone leaves
    the pipes open and the subsequent communicate() drain blocks
    indefinitely. Killing the whole process group avoids that.

    No-op + best-effort on Windows (`os.killpg` is POSIX-only) -- the
    bug repros on macOS/Linux; Windows fallback uses plain `proc.kill`.
    """
    if sys.platform == "win32":
        try:
            proc.kill()
        except (ProcessLookupError, OSError) as exc:
            logger.debug("claude-cli kill skipped: %s", exc)
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError) as exc:
        logger.debug("claude-cli getpgid failed: %s", exc)
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError) as exc:
        logger.debug("claude-cli killpg skipped: %s", exc)


def _run_with_group_kill(
    args: list[str],
    *,
    prompt: str,
    timeout: float,
    grace_seconds: float = _TIMEOUT_KILL_GRACE_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run claude-cli with a bounded wall-clock and process-group kill.

    Drop-in replacement for `subprocess.run(..., timeout=timeout)` that:

    1. Spawns the child in its own session so the helper processes it
       launches inherit a fresh process group.
    2. On timeout, sends SIGKILL to that whole process group rather than
       only the immediate child, so grandchildren that hold pipe fds
       open cannot stall the drain.
    3. Bounds the post-kill drain by `grace_seconds`, so even a
       pathological wedged pipe cannot extend wall-clock past
       `timeout + grace_seconds`.

    Re-raises `subprocess.TimeoutExpired` on timeout so the caller's
    retry/backoff path keeps working.
    """
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_group(proc)
        # Bounded drain: pull whatever the pipes will yield, but never
        # block longer than `grace_seconds`. If the drain itself stalls
        # (e.g., a wedged grandchild still has the fd), we accept the
        # loss and let the timeout propagate.
        try:
            proc.communicate(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            logger.warning(
                "claude-cli drain stalled after SIGKILL; abandoning pipes (grace=%.1fs)",
                grace_seconds,
            )
        # Best-effort close so leaked fds don't accumulate across retries.
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        raise exc
    return subprocess.CompletedProcess(
        args=args,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
    )


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
        self._budget: RuntimeBudget | None = None  # AC-735

    def attach_budget(self, budget: RuntimeBudget | None) -> None:
        """Attach a wall-clock budget to bound total runtime (AC-735).

        Once attached, every ``_invoke`` checks the budget before spawning
        a subprocess and caps the per-call subprocess timeout to the
        smaller of the configured timeout and the remaining budget.
        """
        self._budget = budget

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

        # Tools — AC-736: emit as a single ``--tools=<value>`` token so
        # an operator-supplied empty value (``AUTOCONTEXT_CLAUDE_TOOLS=""``,
        # meaning "run with no tools") doesn't render as a confusing
        # ``--tools  --permission-mode`` double-space in ``ps`` listings.
        if self._config.tools is not None:
            args.append(f"--tools={self._config.tools}")

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

            # AC-735: external wall-clock budget (across invocations).
            # Layered on top of upstream's per-invocation retry cap so a
            # caller-supplied RuntimeBudget short-circuits even mid-retry.
            if self._budget is not None:
                try:
                    self._budget.ensure_not_expired()
                except RuntimeBudgetExpired as exc:
                    logger.error("claude-cli skipped: %s", exc)
                    return AgentOutput(
                        text="",
                        metadata={
                            "error": "runtime_budget_expired",
                            "message": str(exc),
                            "total_seconds": exc.total_seconds,
                            "elapsed_seconds": exc.elapsed_seconds,
                            "attempts": attempt_index,
                        },
                    )

            timeout = self._attempt_timeout(total_start)
            if self._budget is not None:
                timeout = self._budget.cap_call_timeout(timeout)
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
                result = _run_with_group_kill(args, prompt=prompt, timeout=timeout)
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - start
                if attempt_index < max_retries and self._has_retry_budget(total_start):
                    delay = self._retry_delay(attempt_index)
                    remaining = self._remaining_total_budget(total_start)
                    # AC-735: external budget must also cover the planned
                    # sleep — without this guard the sleep itself can push
                    # the runtime past the advertised wall-clock cap.
                    external_remaining = self._budget.remaining() if self._budget is not None else None
                    if (remaining is not None and delay >= remaining) or (
                        external_remaining is not None and delay >= external_remaining
                    ):
                        logger.warning(
                            "claude-cli retry skipped reason=budget_exhausted "
                            "delay=%.2fs internal_remaining=%s "
                            "external_remaining=%s elapsed=%.1fs",
                            delay,
                            f"{remaining:.2f}" if remaining is not None else "n/a",
                            f"{external_remaining:.2f}" if external_remaining is not None else "n/a",
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
        return base * (multiplier**retry_index)

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


def build_claude_cli_runtime(
    settings: Any,
    *,
    model_override: str | None = None,
) -> ClaudeCLIRuntime:
    """Single source of truth for settings-driven ClaudeCLIRuntime construction.

    Wires retry config from settings AND attaches a RuntimeBudget when
    ``settings.claude_max_total_seconds > 0``. All call sites that build
    a ClaudeCLIRuntime from AppSettings must route through here so the
    advertised wall-clock cap is enforced uniformly across:

    - ``build_client_from_settings`` (default agent provider)
    - ``create_role_client('claude-cli', ...)`` (per-role overrides)
    - ``providers.registry.get_provider('claude-cli', ...)`` (judge etc.)
    """
    config = ClaudeCLIConfig(
        model=model_override or settings.claude_model or "sonnet",
        tools=settings.claude_tools,
        permission_mode=settings.claude_permission_mode,
        session_persistence=settings.claude_session_persistence,
        timeout=settings.claude_timeout,
        max_retries=settings.claude_max_retries,
        retry_backoff_seconds=settings.claude_retry_backoff_seconds,
        retry_backoff_multiplier=settings.claude_retry_backoff_multiplier,
        max_total_seconds=settings.claude_max_total_seconds,
    )
    runtime = ClaudeCLIRuntime(config)
    if settings.claude_max_total_seconds > 0:
        runtime.attach_budget(RuntimeBudget.starting_now(total_seconds=settings.claude_max_total_seconds))
    return runtime


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
