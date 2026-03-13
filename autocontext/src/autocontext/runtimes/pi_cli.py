"""Pi CLI runtime — wraps `pi --print` for agent execution.

Uses Pi's non-interactive print mode as a one-shot agent runtime,
capturing output and normalizing into AutoContext artifacts.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from autocontext.runtimes.base import AgentOutput, AgentRuntime
from autocontext.runtimes.pi_artifacts import PiExecutionTrace

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PiCLIConfig:
    """Configuration for the Pi CLI runtime."""

    pi_command: str = "pi"
    model: str = ""
    timeout: float = 120.0
    json_output: bool = True
    workspace: str = ""
    extra_args: list[str] = field(default_factory=list)


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
        args = self._build_args()
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
        args = self._build_args()
        return self._invoke(full_prompt, args)

    def _build_args(self) -> list[str]:
        """Build the pi CLI argument list."""
        pi = self._pi_path or self._config.pi_command
        args = [pi, "--print"]

        if self._config.model:
            args.extend(["--model", self._config.model])

        if self._config.workspace:
            args.extend(["--workspace", self._config.workspace])

        args.extend(self._config.extra_args)
        return args

    def _invoke(self, prompt: str, args: list[str]) -> AgentOutput:
        """Execute pi --print and parse the result."""
        logger.info("invoking pi CLI: %s", " ".join(args[:4]) + "...")
        t0 = time.monotonic()

        try:
            result = subprocess.run(
                args,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._config.timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error("pi CLI timed out after %.0fs", self._config.timeout)
            return AgentOutput(text="", metadata={"error": "timeout"})
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
                pass

        # Raw text fallback
        return AgentOutput(
            text=raw.strip(),
            cost_usd=0.0,
            model="pi",
            metadata={"exit_code": exit_code},
        )
