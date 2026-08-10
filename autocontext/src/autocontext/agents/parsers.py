from __future__ import annotations

import logging
import re
from typing import Any

from autocontext.agents.architect import parse_architect_harness_specs, parse_architect_tool_specs
from autocontext.agents.coach import parse_coach_sections
from autocontext.agents.contracts import AnalystOutput, ArchitectOutput, CoachOutput, CompetitorOutput
from autocontext.harness.core.types import RoleExecution

logger = logging.getLogger(__name__)


def _extract_section_bullets(markdown: str, heading: str) -> list[str]:
    """Extract bullet points under a markdown heading.

    Looks for lines starting with '- ' under a ## heading matching the text.
    Stops at the next heading of equal or higher level.
    """
    bullets: list[str] = []
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(markdown)
    if not match:
        return bullets

    after = markdown[match.end():]
    for line in after.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            break  # Stop at any heading level (##, ###, etc.)
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())

    return bullets


def parse_competitor_output(
    raw_text: str,
    strategy: dict[str, Any],
    is_code_strategy: bool = False,
) -> CompetitorOutput:
    """Parse competitor output into typed contract."""
    return CompetitorOutput(
        raw_text=raw_text,
        strategy=strategy,
        reasoning=raw_text.strip(),
        is_code_strategy=is_code_strategy,
    )


def parse_analyst_output(raw_markdown: str) -> AnalystOutput:
    """Parse analyst markdown into typed contract."""
    try:
        findings = _extract_section_bullets(raw_markdown, "Findings")
        root_causes = _extract_section_bullets(raw_markdown, "Root Causes")
        recommendations = _extract_section_bullets(raw_markdown, "Actionable Recommendations")
        return AnalystOutput(
            raw_markdown=raw_markdown,
            findings=findings,
            root_causes=root_causes,
            recommendations=recommendations,
            parse_success=True,
        )
    except Exception:
        logger.warning("failed to parse analyst output", exc_info=True)
        return AnalystOutput(raw_markdown=raw_markdown, parse_success=False)


def parse_coach_output(raw_markdown: str) -> CoachOutput:
    """Parse coach markdown into typed contract."""
    try:
        playbook, lessons, hints = parse_coach_sections(raw_markdown)
        return CoachOutput(
            raw_markdown=raw_markdown,
            playbook=playbook,
            lessons=lessons,
            hints=hints,
            parse_success=True,
        )
    except Exception:
        logger.warning("failed to parse coach output", exc_info=True)
        return CoachOutput(raw_markdown=raw_markdown, parse_success=False)


def parse_architect_output(raw_markdown: str) -> ArchitectOutput:
    """Parse architect markdown into typed contract."""
    try:
        tool_specs = parse_architect_tool_specs(raw_markdown)
        harness_specs = parse_architect_harness_specs(raw_markdown)
        return ArchitectOutput(
            raw_markdown=raw_markdown,
            tool_specs=tool_specs,
            harness_specs=harness_specs,
            parse_success=True,
        )
    except Exception:
        logger.warning("failed to parse architect output", exc_info=True)
        return ArchitectOutput(raw_markdown=raw_markdown, parse_success=False)


def _was_constrained(execution: RoleExecution) -> bool:
    """Whether this role's output was actually schema-enforced by the backend.

    Reads what the backend reported, not what was requested. A schema that was
    asked for and silently not applied reports False here, which is the whole
    reason the flag is recorded per execution (AC-913).
    """
    return bool(execution.metadata.get("constrained"))


def parse_analyst_exec(execution: RoleExecution) -> AnalystOutput:
    """Parse an analyst execution by whichever path its backend actually used.

    Constrained output is validated and drift raises. Unconstrained output
    keeps the markdown scrape, unchanged -- so a backend with no constrained
    decoding (Anthropic today, every CLI runtime) behaves exactly as before.
    """
    if _was_constrained(execution):
        from autocontext.agents.role_schemas import parse_analyst_constrained

        return parse_analyst_constrained(execution.content)
    return parse_analyst_output(execution.content)


def parse_coach_exec(execution: RoleExecution) -> CoachOutput:
    """Coach counterpart of parse_analyst_exec."""
    if _was_constrained(execution):
        from autocontext.agents.role_schemas import parse_coach_constrained

        return parse_coach_constrained(execution.content)
    return parse_coach_output(execution.content)


def parse_architect_exec(execution: RoleExecution) -> ArchitectOutput:
    """Architect counterpart of parse_analyst_exec."""
    if _was_constrained(execution):
        from autocontext.agents.role_schemas import parse_architect_constrained

        return parse_architect_constrained(execution.content)
    return parse_architect_output(execution.content)
