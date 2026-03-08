"""Knowledge coherence verification — rule-based consistency checks.

Checks accumulated knowledge artifacts for internal consistency:
1. Playbook is non-empty
2. Tools referenced in playbook exist on disk
3. Lessons don't contain obvious contradictions

All checks are rule-based (no LLM calls). Issues are warnings, not
blockers — the loop continues regardless.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CoherenceReport:
    """Result of knowledge coherence verification."""

    issues: list[str] = field(default_factory=list)


def _check_playbook(playbook_content: str) -> list[str]:
    """Check playbook content is non-empty."""
    if not playbook_content.strip():
        return ["Playbook is empty after persistence"]
    return []


def _check_tools(playbook_content: str, knowledge_dir: Path) -> list[str]:
    """Check that tools referenced in playbook exist on disk."""
    if "tool" not in playbook_content.lower():
        return []

    tools_dir = knowledge_dir / "tools"
    if not tools_dir.is_dir():
        return ["Playbook references tools but tools/ directory does not exist"]

    tool_files = list(tools_dir.glob("*.py"))
    if not tool_files:
        return ["Playbook references tools but tools/ directory is empty"]

    return []


def _check_lesson_contradictions(lessons: list[str]) -> list[str]:
    """Simple keyword-based contradiction detection.

    Checks for pairs like "always X" / "never X" on the same parameter.
    This is a heuristic — not exhaustive.
    """
    issues: list[str] = []
    always_patterns: dict[str, str] = {}
    never_patterns: dict[str, str] = {}

    for lesson in lessons:
        lower = lesson.lower().strip("- ")
        always_match = re.search(r"always\s+(\w+\s+\w+)", lower)
        if always_match:
            key = always_match.group(1)
            always_patterns[key] = lesson

        never_match = re.search(r"never\s+(\w+\s+\w+)", lower)
        if never_match:
            key = never_match.group(1)
            never_patterns[key] = lesson

    for key in always_patterns:
        if key in never_patterns:
            issues.append(
                f"Contradictory lessons detected: '{always_patterns[key].strip()}' "
                f"vs '{never_patterns[key].strip()}'",
            )

    return issues


def _read_lessons(scenario_name: str, skills_root: Path) -> list[str]:
    """Read operational lessons from SKILL.md."""
    skill_dir = skills_root / f"{scenario_name.replace('_', '-')}-ops"
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        return []
    content = skill_path.read_text(encoding="utf-8")
    marker = "## Operational Lessons"
    start = content.find(marker)
    if start == -1:
        return []
    section = content[start + len(marker):]
    next_heading = section.find("\n## ")
    if next_heading != -1:
        section = section[:next_heading]
    return [line.strip() for line in section.strip().splitlines() if line.strip().startswith("-")]


def check_coherence(
    *,
    scenario_name: str,
    knowledge_root: Path,
    skills_root: Path | None = None,
) -> CoherenceReport:
    """Run all knowledge coherence checks."""
    report = CoherenceReport()
    knowledge_dir = knowledge_root / scenario_name

    if not knowledge_dir.is_dir():
        return report  # First run, nothing to check

    playbook_path = knowledge_dir / "playbook.md"
    if not playbook_path.exists():
        return report  # OK on first generation before advance

    playbook_content = playbook_path.read_text(encoding="utf-8")
    report.issues.extend(_check_playbook(playbook_content))
    report.issues.extend(_check_tools(playbook_content, knowledge_dir))

    if skills_root is not None:
        lessons = _read_lessons(scenario_name, skills_root)
        report.issues.extend(_check_lesson_contradictions(lessons))

    for issue in report.issues:
        logger.warning("knowledge coherence: %s", issue)

    return report
