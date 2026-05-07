"""Agent runtime abstraction for autocontext.

Runtimes handle generation and revision of agent outputs.
autocontext orchestrates and judges; runtimes do the actual work.
"""

from autocontext.runtimes.base import AgentOutput, AgentRuntime
from autocontext.runtimes.claude_cli import ClaudeCLIRuntime
from autocontext.runtimes.codex_cli import CodexCLIRuntime
from autocontext.runtimes.direct_api import DirectAPIRuntime
from autocontext.runtimes.workspace_env import (
    RuntimeCommandContext,
    RuntimeCommandGrant,
    RuntimeCommandHandler,
    RuntimeCommandResult,
    RuntimeExecOptions,
    RuntimeExecResult,
    RuntimeFileStat,
    RuntimeWorkspaceEnv,
    create_in_memory_workspace_env,
    create_local_workspace_env,
    define_runtime_command,
)

__all__ = [
    "AgentRuntime",
    "AgentOutput",
    "DirectAPIRuntime",
    "ClaudeCLIRuntime",
    "CodexCLIRuntime",
    "RuntimeCommandContext",
    "RuntimeCommandGrant",
    "RuntimeCommandHandler",
    "RuntimeCommandResult",
    "RuntimeExecOptions",
    "RuntimeExecResult",
    "RuntimeFileStat",
    "RuntimeWorkspaceEnv",
    "create_in_memory_workspace_env",
    "create_local_workspace_env",
    "define_runtime_command",
]


def list_cli_runtimes() -> list[dict[str, str]]:
    """List all subscription-backed CLI runtimes available."""
    return [
        {"name": "claude-cli", "command": "claude", "description": "Claude Code CLI (Anthropic subscription)"},
        {"name": "codex", "command": "codex", "description": "Codex CLI (OpenAI subscription)"},
        {"name": "pi", "command": "pi", "description": "Pi CLI (Inflection subscription)"},
    ]
