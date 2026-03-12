from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CompetitorOutput:
    raw_text: str
    strategy: dict[str, Any]
    reasoning: str
    is_code_strategy: bool = False


@dataclass(slots=True)
class AnalystOutput:
    raw_markdown: str
    findings: list[str] = field(default_factory=list)
    root_causes: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    parse_success: bool = True


@dataclass(slots=True)
class CoachOutput:
    raw_markdown: str
    playbook: str = ""
    lessons: str = ""
    hints: str = ""
    parse_success: bool = True


@dataclass(slots=True)
class ArchitectOutput:
    raw_markdown: str
    tool_specs: list[dict[str, Any]] = field(default_factory=list)
    harness_specs: list[dict[str, Any]] = field(default_factory=list)
    changelog_entry: str = ""
    parse_success: bool = True
