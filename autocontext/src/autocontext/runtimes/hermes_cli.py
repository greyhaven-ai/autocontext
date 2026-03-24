"""Hermes CLI runtime — wraps `hermes` for agent execution (AC-351).

Uses Hermes's non-interactive mode as an agent runtime, capturing
output and normalizing into autocontext artifacts. Follows the same
pattern as CodexCLIRuntime and PiCLIRuntime.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field

from autocontext.runtimes.base import AgentOutput, AgentRuntime

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HermesCLIConfig:
    """Configuration for the Hermes CLI runtime."""

    hermes_command: str = "hermes"
    model: str = ""
    timeout: float = 120.0
    workspace: str = ""
    base_url: str = ""
    api_key: str = ""
    extra_args: list[str] = field(default_factory=list)


class HermesCLIRuntime(AgentRuntime):
    """Agent runtime that invokes the Hermes CLI.

    Requires the Hermes CLI to be installed and accessible on PATH.
    """

    def __init__(self, config: HermesCLIConfig | None = None) -> None:
        self._config = config or HermesCLIConfig()
        self._hermes_path = shutil.which(self._config.hermes_command)

    @property
    def available(self) -> bool:
        """Check if the hermes CLI is available."""
        return self._hermes_path is not None

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> AgentOutput:
        del schema
        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n{prompt}"
        return self._invoke(full_prompt)

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
        return self._invoke(full_prompt)

    def _build_args(self, prompt: str) -> list[str]:
        hermes = self._hermes_path or self._config.hermes_command
        args = [hermes, "chat", "--query", prompt]

        if self._config.model:
            args.extend(["--model", self._config.model])

        # Hermes routes custom OpenAI-compatible endpoints through its "main"
        # provider config rather than dedicated --base-url/--api-key flags.
        if self._config.base_url or self._config.api_key:
            args.extend(["--provider", "main"])

        args.extend(self._config.extra_args)
        return args

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self._config.base_url:
            env["OPENAI_BASE_URL"] = self._config.base_url
        if self._config.api_key:
            env["OPENAI_API_KEY"] = self._config.api_key
        return env

    def _invoke(self, prompt: str) -> AgentOutput:
        args = self._build_args(prompt)
        logger.info("invoking hermes: %s", " ".join(args[:6]) + "...")
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self._config.timeout,
                cwd=self._config.workspace or None,
                env=self._build_env(),
            )
        except subprocess.TimeoutExpired:
            logger.error("hermes timed out after %.0fs", self._config.timeout)
            return AgentOutput(text="", metadata={"error": "timeout"})
        except FileNotFoundError:
            logger.error("hermes CLI not found at: %s", self._config.hermes_command)
            return AgentOutput(text="", metadata={"error": "hermes_not_found"})

        if result.returncode != 0:
            logger.warning("hermes exited with code %d: %s", result.returncode, result.stderr[:200])
            if not result.stdout.strip():
                return AgentOutput(
                    text="",
                    metadata={
                        "error": "nonzero_exit",
                        "exit_code": result.returncode,
                        "stderr": result.stderr[:500],
                    },
                )

        return self._parse_output(result.stdout)

    def _parse_output(self, raw: str) -> AgentOutput:
        """Parse output — handles JSON response or plain text."""
        stripped = raw.strip()
        if not stripped:
            return AgentOutput(text="")

        # Try JSON parsing (Hermes may return structured responses)
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                text = parsed.get("response", parsed.get("text", parsed.get("content", "")))
                if text:
                    return AgentOutput(text=str(text), metadata={"raw_json": parsed})
        except (json.JSONDecodeError, TypeError):
            pass

        return AgentOutput(text=stripped)
