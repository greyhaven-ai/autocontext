"""Codex CLI runtime — wraps `codex exec` for agent execution (AC-317).

Uses OpenAI Codex CLI's non-interactive exec mode as an agent runtime
with full tool access, JSONL event streaming, and structured output.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field

from autocontext.runtimes.base import AgentOutput, AgentRuntime

logger = logging.getLogger(__name__)

CODEX_PROVIDER_TYPE = "codex"


@dataclass(slots=True)
class CodexCLIConfig:
    """Configuration for the Codex CLI runtime."""

    model: str = "o4-mini"
    approval_mode: str = "full-auto"
    timeout: float = 120.0
    workspace: str = ""
    quiet: bool = False
    extra_args: list[str] = field(default_factory=list)


class CodexCLIRuntime(AgentRuntime):
    """Agent runtime that invokes `codex exec` (Codex non-interactive mode).

    Requires the Codex CLI to be installed and authenticated.

    Features:
    - Full Codex tool access (shell, file operations, etc.)
    - JSONL event stream parsing
    - Structured output via --output-schema
    - Model selection
    """

    def __init__(self, config: CodexCLIConfig | None = None) -> None:
        self._config = config or CodexCLIConfig()
        self._codex_path = shutil.which("codex")

    @property
    def available(self) -> bool:
        return self._codex_path is not None

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        schema: dict | None = None,
    ) -> AgentOutput:
        args = self._build_args(schema=schema)
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
        args = self._build_args()
        return self._invoke(revision_prompt, args)

    def _build_args(
        self,
        schema: dict | None = None,
    ) -> list[str]:
        codex = self._codex_path or "codex"
        args = [codex, "exec"]

        args.extend(["--model", self._config.model])

        if self._config.approval_mode == "full-auto":
            args.append("--full-auto")

        if self._config.quiet:
            args.append("--quiet")

        if self._config.workspace:
            args.extend(["--cd", self._config.workspace])

        if schema:
            args.extend(["--output-schema", json.dumps(schema)])

        args.extend(self._config.extra_args)

        return args

    def _invoke(self, prompt: str, args: list[str]) -> AgentOutput:
        logger.info("invoking codex exec: %s", " ".join(args[:6]) + "...")

        # Append the prompt as the final positional argument
        full_args = [*args, prompt]

        try:
            result = subprocess.run(
                full_args,
                capture_output=True,
                text=True,
                timeout=self._config.timeout,
            )
        except subprocess.TimeoutExpired:
            logger.error("codex exec timed out after %.0fs", self._config.timeout)
            return AgentOutput(text="", metadata={"error": "timeout"})
        except FileNotFoundError:
            logger.error("codex CLI not found. Install Codex CLI first.")
            return AgentOutput(text="", metadata={"error": "codex_not_found"})

        if result.returncode != 0:
            logger.warning("codex exec exited with code %d: %s", result.returncode, result.stderr[:200])
            if not result.stdout.strip():
                return AgentOutput(
                    text="",
                    metadata={"error": "nonzero_exit", "stderr": result.stderr[:500]},
                )

        return self._parse_output(result.stdout)

    def _parse_output(self, raw: str) -> AgentOutput:
        """Parse output — handles JSONL event stream or plain text."""
        lines = raw.strip().splitlines()
        if not lines:
            return AgentOutput(text="")

        # Try JSONL parsing
        messages: list[str] = []
        is_jsonl = False
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                is_jsonl = True
                if isinstance(event, dict):
                    etype = event.get("type", "")
                    if etype == "item.message":
                        content = event.get("content", [])
                        for block in content:
                            if isinstance(block, dict) and "text" in block:
                                messages.append(block["text"])
                    elif "text" in event:
                        messages.append(event["text"])
            except (json.JSONDecodeError, TypeError):
                if not is_jsonl:
                    # Not JSONL — return as plain text
                    return AgentOutput(text=raw.strip())

        if messages:
            return AgentOutput(text="\n".join(messages))

        return AgentOutput(text=raw.strip())
