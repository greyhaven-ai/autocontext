"""Domain-agnostic REPL types for multi-turn exploration sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ReplCommand:
    """A code string to execute in the REPL worker."""

    code: str


@dataclass(slots=True)
class ReplResult:
    """Result of executing a single code block in the REPL."""

    stdout: str
    error: str | None
    answer: dict[str, Any]


@dataclass(slots=True)
class ExecutionRecord:
    """Record of a single code execution within an RLM session."""

    turn: int
    code: str
    stdout: str
    error: str | None
    answer_ready: bool


@dataclass(slots=True)
class RlmContext:
    """Data prepared for injection into a REPL namespace."""

    variables: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
