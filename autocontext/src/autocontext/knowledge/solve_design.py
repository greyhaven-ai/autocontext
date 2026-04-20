"""Helpers for solve-on-demand scenario design prompts."""

from __future__ import annotations

import re

from autocontext.config.settings import AppSettings
from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

_SOLVE_DESCRIPTION_SKIP_SECTIONS = frozenset(
    {
        "Why This Matters",
        "What This Tests",
        "Implementation Guidance",
        "Acceptance",
        "Why existing scenarios don't cover this",
        "Dependencies",
    }
)
_SOLVE_DESCRIPTION_SKIP_LINE_PREFIXES = (
    "**Priority:**",
    "**Generations to signal:**",
)
_SOLVE_INLINE_EXAMPLE_PAREN_RE = re.compile(
    r"\(\s*(?:e\.g\.,?|eg,?|for example,?)[^)]*\)",
    re.IGNORECASE,
)
_SOLVE_AGENT_TASK_DESIGN_KEEP_SECTIONS = frozenset(
    {
        "Objective",
        "Description",
        "Scenario Design",
        "Evaluation Dimensions",
        "Success Criteria",
    }
)
_SOLVE_AGENT_TASK_DESIGN_MAX_CHARS = 1000
_SOLVE_AGENT_TASK_DESIGN_MAX_SECTION_LINES = 5
_SOLVE_CREATOR_PI_TIMEOUT_FLOOR_SECONDS = 600.0
_SOLVE_RUNTIME_HEAVY_TASK_PROMPT_RE = re.compile(
    r"\b(run|execute|inspect)\b.*\b(provider|repository|scenario|generations?|command|file|artifact)\b",
    re.IGNORECASE,
)


def _build_solve_description_brief(description: str) -> str:
    lines: list[str] = []
    skipping_section = False
    for raw_line in description.splitlines():
        heading_match = re.match(r"^\s*#{2,6}\s+(.+?)\s*$", raw_line)
        if heading_match is not None:
            title = heading_match.group(1).strip()
            skipping_section = title in _SOLVE_DESCRIPTION_SKIP_SECTIONS
            if not skipping_section:
                lines.append(raw_line)
            continue

        stripped = raw_line.strip()
        if stripped.startswith(_SOLVE_DESCRIPTION_SKIP_LINE_PREFIXES):
            continue
        if not skipping_section:
            lines.append(raw_line)

    brief = "\n".join(lines).strip()
    brief = _SOLVE_INLINE_EXAMPLE_PAREN_RE.sub("", brief)
    brief = re.sub(r"\n{3,}", "\n\n", brief)
    brief = re.sub(r"[ \t]{2,}", " ", brief)
    return brief or description.strip()


def _build_solve_agent_task_design_brief(description: str) -> str:
    brief = _build_solve_description_brief(description)
    if len(brief) <= _SOLVE_AGENT_TASK_DESIGN_MAX_CHARS:
        return brief

    lines: list[str] = []
    current_section: str | None = None
    current_section_lines = 0
    title_captured = False

    for raw_line in brief.splitlines():
        heading_match = re.match(r"^\s*#{2,6}\s+(.+?)\s*$", raw_line)
        if heading_match is not None:
            title = heading_match.group(1).strip()
            if title in _SOLVE_AGENT_TASK_DESIGN_KEEP_SECTIONS:
                current_section = title
                current_section_lines = 0
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(raw_line)
                lines.append("")
            else:
                current_section = None
            continue

        stripped = raw_line.strip()
        if not title_captured and stripped:
            lines.append(raw_line)
            title_captured = True
            continue
        if current_section is None:
            continue
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if stripped.startswith("```"):
            continue
        if current_section_lines >= _SOLVE_AGENT_TASK_DESIGN_MAX_SECTION_LINES:
            continue
        lines.append(raw_line)
        current_section_lines += 1

    compact = "\n".join(lines).strip()
    compact = re.sub(r"\n{3,}", "\n\n", compact)
    while len(compact) > _SOLVE_AGENT_TASK_DESIGN_MAX_CHARS and "\n\n" in compact:
        compact = compact.rsplit("\n\n", 1)[0].strip()
    if len(compact) > _SOLVE_AGENT_TASK_DESIGN_MAX_CHARS:
        compact = compact[:_SOLVE_AGENT_TASK_DESIGN_MAX_CHARS].rsplit("\n", 1)[0].strip()
    return compact or brief[:_SOLVE_AGENT_TASK_DESIGN_MAX_CHARS].strip()


def _settings_for_solve_creator(settings: AppSettings) -> AppSettings:
    if settings.agent_provider not in {"pi", "pi-rpc"}:
        return settings
    if float(settings.pi_timeout) >= _SOLVE_CREATOR_PI_TIMEOUT_FLOOR_SECONDS:
        return settings
    return settings.model_copy(update={"pi_timeout": _SOLVE_CREATOR_PI_TIMEOUT_FLOOR_SECONDS})


def _solve_task_spec_needs_compact_retry(spec: AgentTaskSpec) -> bool:
    if spec.output_format != "json_schema":
        return False
    if spec.sample_input not in {None, ""}:
        return False
    prompt = spec.task_prompt.strip()
    if "if available" in prompt.lower():
        return True
    return bool(_SOLVE_RUNTIME_HEAVY_TASK_PROMPT_RE.search(prompt))
