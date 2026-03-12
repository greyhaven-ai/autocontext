"""Agent runtime abstraction for AutoContext.

Runtimes handle generation and revision of agent outputs.
AutoContext orchestrates and judges; runtimes do the actual work.
"""

from autocontext.runtimes.base import AgentOutput, AgentRuntime
from autocontext.runtimes.claude_cli import ClaudeCLIRuntime
from autocontext.runtimes.direct_api import DirectAPIRuntime

__all__ = [
    "AgentRuntime",
    "AgentOutput",
    "DirectAPIRuntime",
    "ClaudeCLIRuntime",
]
