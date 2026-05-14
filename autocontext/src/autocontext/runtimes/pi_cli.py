"""Pi CLI runtime — wraps `pi --print` for agent execution.

Uses Pi's non-interactive print mode as a one-shot agent runtime,
capturing output and normalizing into autocontext artifacts.
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
from dataclasses import dataclass, field
from typing import Any

from autocontext.runtimes.base import AgentOutput, AgentRuntime
from autocontext.runtimes.pi_artifacts import PiExecutionTrace
from autocontext.runtimes.pi_defaults import PI_DEFAULT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# AC-764: mirror the Claude CLI hard-timeout shape for Pi CLI calls.
# `subprocess.run(..., timeout=...)` only kills the immediate child and can then
# block in an unbounded communicate() drain when grandchildren keep inherited
# stdout/stderr pipe fds open. Pi is also a Node-based CLI that may spawn helper
# processes, so use a fresh process group and a bounded post-kill drain.
_TIMEOUT_KILL_GRACE_SECONDS = 5.0


@dataclass(slots=True)
class PiCLIConfig:
    """Configuration for the Pi CLI runtime."""

    pi_command: str = "pi"
    model: str = ""
    timeout: float = PI_DEFAULT_TIMEOUT_SECONDS
    json_output: bool = True
    workspace: str = ""
    no_context_files: bool = False
    extra_args: list[str] = field(default_factory=list)


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """Best-effort SIGKILL for the full Pi process group."""

    if sys.platform == "win32":
        try:
            proc.kill()
        except (ProcessLookupError, OSError) as exc:
            logger.debug("pi-cli kill skipped: %s", exc)
        return

    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError) as exc:
        logger.debug("pi-cli getpgid failed: %s", exc)
        return

    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError) as exc:
        logger.debug("pi-cli killpg skipped: %s", exc)


def _bounded_drain_and_close(proc: subprocess.Popen[str], grace_seconds: float) -> None:
    """Drain after process-group kill with a strict grace, then close pipes."""

    try:
        proc.communicate(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        logger.warning(
            "pi-cli drain stalled after SIGKILL; abandoning pipes (grace=%.1fs)",
            grace_seconds,
        )
    except (OSError, ValueError):
        # Pipes may already be closed during interpreter shutdown or after an
        # interrupted communicate call. Cleanup should not mask the original
        # timeout/interrupt that the caller handles.
        pass

    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _run_with_group_kill(
    args: list[str],
    *,
    timeout: float,
    cwd: str | None = None,
    grace_seconds: float = _TIMEOUT_KILL_GRACE_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run Pi CLI with process-group kill and bounded post-timeout cleanup.

    Re-raises `subprocess.TimeoutExpired` on timeout so `PiCLIRuntime` can keep
    its existing timeout metadata contract. Also cleans up on `KeyboardInterrupt`
    or any other abnormal exit because `start_new_session=True` detaches the Pi
    child from the terminal's signal-delivery group.
    """

    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "cwd": cwd,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(args, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        _bounded_drain_and_close(proc, grace_seconds)
        raise
    except BaseException:
        _kill_process_group(proc)
        _bounded_drain_and_close(proc, grace_seconds)
        raise

    return subprocess.CompletedProcess(
        args=args,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout,
        stderr=stderr,
    )


class PiCLIRuntime(AgentRuntime):
    """Agent runtime that invokes the Pi CLI in non-interactive mode.

    Requires the Pi CLI to be installed and accessible on PATH.
    """

    def __init__(self, config: PiCLIConfig | None = None) -> None:
        self._config = config or PiCLIConfig()
        self._pi_path = shutil.which(self._config.pi_command)

    @property
    def available(self) -> bool:
        """Check if the pi CLI is available."""
        return self._pi_path is not None

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> AgentOutput:
        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n{prompt}"
        args = self._build_args(full_prompt)
        return self._invoke(full_prompt, args)

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
        full_prompt = revision_prompt
        if system:
            full_prompt = f"{system}\n\n{revision_prompt}"
        args = self._build_args(full_prompt)
        return self._invoke(full_prompt, args)

    def _build_args(self, prompt: str) -> list[str]:
        """Build the pi CLI argument list.

        Uses --print for one-shot mode per Pi's documented interface.
        Workspace is handled via subprocess cwd (Pi has no --workspace flag).
        """
        pi = self._pi_path or self._config.pi_command
        args = [pi, "--print"]

        if self._config.model:
            args.extend(["--model", self._config.model])
        if self._config.no_context_files:
            args.append("--no-context-files")

        # NOTE: Pi does not have a --workspace CLI flag.
        # Workspace is passed as subprocess cwd instead (see _invoke).

        args.extend(self._config.extra_args)
        args.append(prompt)
        return args

    def _invoke(self, prompt: str, args: list[str]) -> AgentOutput:
        """Execute pi --print and parse the result."""
        logger.info("invoking pi CLI: %s", " ".join(args[:4]) + "...")
        t0 = time.monotonic()

        try:
            result = _run_with_group_kill(
                args,
                timeout=self._config.timeout,
                cwd=self._config.workspace or None,
            )
        except subprocess.TimeoutExpired:
            logger.error("pi CLI timed out after %.0fs", self._config.timeout)
            return AgentOutput(
                text="",
                metadata={"error": "timeout", "timeout_seconds": self._config.timeout},
            )
        except FileNotFoundError:
            logger.error("pi CLI not found at %r", self._config.pi_command)
            return AgentOutput(text="", metadata={"error": "pi_not_found"})

        duration_ms = int((time.monotonic() - t0) * 1000)

        if result.returncode != 0:
            logger.warning("pi CLI exited with code %d: %s", result.returncode, result.stderr[:200])
            if not result.stdout.strip():
                return AgentOutput(
                    text="",
                    metadata={"error": "nonzero_exit", "exit_code": result.returncode, "stderr": result.stderr[:500]},
                )

        output = self._parse_output(result.stdout, result.returncode)

        # Attach PiExecutionTrace for artifact persistence (AC-224)
        trace = PiExecutionTrace(
            session_id=output.session_id or "",
            prompt_context=prompt,
            raw_output=result.stdout,
            normalized_output=output.text,
            exit_code=result.returncode,
            duration_ms=duration_ms,
            cost_usd=output.cost_usd or 0.0,
            model=output.model or "pi",
        )
        output.metadata["pi_trace"] = trace

        return output

    def _parse_output(self, raw: str, exit_code: int) -> AgentOutput:
        """Parse output from pi --print."""
        if self._config.json_output:
            try:
                data = json.loads(raw)
                text = data.get("result", data.get("output", ""))
                if not isinstance(text, str) or not text.strip():
                    text = json.dumps(data)
                return AgentOutput(
                    text=text,
                    cost_usd=data.get("cost_usd", 0.0),
                    model=data.get("model", "pi"),
                    session_id=data.get("session_id"),
                    metadata={
                        "exit_code": exit_code,
                        "raw_json": data,
                    },
                )
            except (json.JSONDecodeError, TypeError):
                logger.debug("runtimes.pi_cli: suppressed json.JSONDecodeError), TypeError", exc_info=True)

        # Raw text fallback
        return AgentOutput(
            text=raw.strip(),
            cost_usd=0.0,
            model="pi",
            metadata={"exit_code": exit_code},
        )
