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


@dataclass(slots=True)
class LibrarianFlag:
    severity: str  # "concern" or "violation"
    description: str
    cited_section: str
    recommendation: str


@dataclass(slots=True)
class LibrarianOutput:
    raw_markdown: str
    book_name: str
    advisory: str
    flags: list[LibrarianFlag]
    cited_sections: list[str]
    parse_success: bool = True


@dataclass(slots=True)
class ArchivistDecision:
    flag_source: str
    book_name: str
    verdict: str  # "dismissed", "soft_flag", "hard_gate"
    reasoning: str
    cited_passage: str


@dataclass(slots=True)
class ArchivistOutput:
    raw_markdown: str
    decisions: list[ArchivistDecision]
    synthesis: str
    parse_success: bool = True
