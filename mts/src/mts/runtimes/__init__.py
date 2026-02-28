"""Agent runtime abstraction for MTS.

Runtimes handle generation and revision of agent outputs.
MTS orchestrates and judges; runtimes do the actual work.
"""

from mts.runtimes.base import AgentOutput, AgentRuntime
from mts.runtimes.claude_cli import ClaudeCLIRuntime
from mts.runtimes.direct_api import DirectAPIRuntime

__all__ = [
    "AgentRuntime",
    "AgentOutput",
    "DirectAPIRuntime",
    "ClaudeCLIRuntime",
]
