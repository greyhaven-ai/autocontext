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

_VALID_HERMES_PROVIDERS = frozenset({
    "auto",
    "openrouter",
    "nous",
    "openai-codex",
    "copilot-acp",
    "copilot",
    "anthropic",
    "huggingface",
    "zai",
    "kimi-coding",
    "minimax",
    "minimax-cn",
    "kilocode",
})
_PROVIDER_ALIASES = {
    "codex": "openai-codex",
}
_LEGACY_CUSTOM_ENDPOINT_PROVIDERS = frozenset({"main", "custom"})


@dataclass(slots=True)
class HermesCLIConfig:
    """Configuration for the Hermes CLI runtime.

    Matches Hermes Agent's documented CLI flags:
    https://hermes-agent.nousresearch.com/docs/user-guide/cli/
    """

    hermes_command: str = "hermes"
    model: str = ""
    timeout: float = 120.0
    workspace: str = ""
    base_url: str = ""       # Passed via OPENAI_BASE_URL env (not a CLI flag)
    api_key: str = ""        # Passed via OPENAI_API_KEY env (not a CLI flag)
    toolsets: str = ""       # -t/--toolsets: comma-separated toolset names
    skills: str = ""         # -s/--skills: skill name to preload
    worktree: bool = False   # --worktree: isolated git worktree
    quiet: bool = False      # --quiet: suppress UI chrome
    provider: str = ""       # --provider: force specific provider
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

    def _normalized_provider(self) -> str:
        provider = self._config.provider.strip().lower()
        return _PROVIDER_ALIASES.get(provider, provider)

    def _uses_custom_endpoint(self) -> bool:
        provider = self._normalized_provider()
        return bool(self._config.base_url) or provider in _LEGACY_CUSTOM_ENDPOINT_PROVIDERS or (
            not provider and bool(self._config.api_key)
        )

    def _build_args(self, prompt: str) -> list[str]:
        hermes = self._hermes_path or self._config.hermes_command
        args = [hermes, "chat", "--query", prompt]

        if self._config.model:
            args.extend(["--model", self._config.model])

        provider = self._normalized_provider()
        if provider and not self._uses_custom_endpoint() and provider in _VALID_HERMES_PROVIDERS:
            args.extend(["--provider", provider])

        if self._config.toolsets:
            args.extend(["--toolsets", self._config.toolsets])

        if self._config.skills:
            args.extend(["--skills", self._config.skills])

        if self._config.worktree:
            args.append("--worktree")

        if self._config.quiet:
            args.append("--quiet")

        args.extend(self._config.extra_args)
        return args

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Hermes treats custom OpenAI-compatible endpoints as a first-class
        # routing path. When base_url is present, it takes precedence over
        # any provider setting, and legacy "main" configs should continue
        # to work without passing a removed --provider main flag.
        use_custom_endpoint = self._uses_custom_endpoint()
        if use_custom_endpoint and self._config.base_url:
            env["OPENAI_BASE_URL"] = self._config.base_url
        if use_custom_endpoint and self._config.api_key:
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
            logger.debug("runtimes.hermes_cli: suppressed json.JSONDecodeError), TypeError", exc_info=True)

        return AgentOutput(text=stripped)
