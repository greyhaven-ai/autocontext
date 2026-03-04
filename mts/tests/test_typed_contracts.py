"""Tests for typed role handoff contracts and parsers."""

from __future__ import annotations

from mts.agents.contracts import AnalystOutput, ArchitectOutput, CoachOutput, CompetitorOutput
from mts.agents.parsers import (
    _extract_section_bullets,
    parse_analyst_output,
    parse_architect_output,
    parse_coach_output,
    parse_competitor_output,
)
from mts.agents.types import AgentOutputs
from mts.harness.core.types import RoleExecution, RoleUsage

# ---------- Contract construction ----------


def test_competitor_output_defaults() -> None:
    out = CompetitorOutput(raw_text="hello", strategy={"a": 1}, reasoning="my reasoning")
    assert out.raw_text == "hello"
    assert out.strategy == {"a": 1}
    assert out.reasoning == "my reasoning"
    assert out.is_code_strategy is False


def test_analyst_output_defaults() -> None:
    out = AnalystOutput(raw_markdown="# Analysis")
    assert out.raw_markdown == "# Analysis"
    assert out.findings == []
    assert out.root_causes == []
    assert out.recommendations == []
    assert out.parse_success is True


def test_coach_output_defaults() -> None:
    out = CoachOutput(raw_markdown="# Coach")
    assert out.raw_markdown == "# Coach"
    assert out.playbook == ""
    assert out.lessons == ""
    assert out.hints == ""
    assert out.parse_success is True


def test_architect_output_defaults() -> None:
    out = ArchitectOutput(raw_markdown="# Architect")
    assert out.raw_markdown == "# Architect"
    assert out.tool_specs == []
    assert out.changelog_entry == ""
    assert out.parse_success is True


# ---------- _extract_section_bullets ----------


def test_extract_bullets_single_heading() -> None:
    md = "## Findings\n- First finding\n- Second finding\n"
    bullets = _extract_section_bullets(md, "Findings")
    assert bullets == ["First finding", "Second finding"]


def test_extract_bullets_no_matching_heading() -> None:
    md = "## Other Section\n- Some bullet\n"
    bullets = _extract_section_bullets(md, "Findings")
    assert bullets == []


def test_extract_bullets_stops_at_next_heading() -> None:
    md = "## Findings\n- Finding one\n## Root Causes\n- Cause one\n"
    bullets = _extract_section_bullets(md, "Findings")
    assert bullets == ["Finding one"]


def test_extract_bullets_no_bullets_under_heading() -> None:
    md = "## Findings\nJust some text, no bullets.\n"
    bullets = _extract_section_bullets(md, "Findings")
    assert bullets == []


def test_extract_bullets_stops_at_sub_heading() -> None:
    md = "## Findings\n- Finding one\n### Details\n- Detail one\n"
    bullets = _extract_section_bullets(md, "Findings")
    assert bullets == ["Finding one"]


# ---------- parse_competitor_output ----------


def test_parse_competitor_basic() -> None:
    out = parse_competitor_output("raw strategy text", {"x": 1})
    assert out.raw_text == "raw strategy text"
    assert out.strategy == {"x": 1}
    assert out.is_code_strategy is False


def test_parse_competitor_code_strategy() -> None:
    out = parse_competitor_output("```python\ncode\n```", {"__code__": "code"}, is_code_strategy=True)
    assert out.is_code_strategy is True
    assert out.strategy == {"__code__": "code"}


# ---------- parse_analyst_output ----------


def test_parse_analyst_well_formed() -> None:
    md = (
        "## Findings\n- Finding A\n- Finding B\n"
        "## Root Causes\n- Cause X\n"
        "## Actionable Recommendations\n- Do Y\n- Do Z\n"
    )
    out = parse_analyst_output(md)
    assert out.parse_success is True
    assert out.findings == ["Finding A", "Finding B"]
    assert out.root_causes == ["Cause X"]
    assert out.recommendations == ["Do Y", "Do Z"]
    assert out.raw_markdown == md


def test_parse_analyst_missing_sections() -> None:
    md = "Some unstructured analyst output without headings."
    out = parse_analyst_output(md)
    assert out.parse_success is True
    assert out.findings == []
    assert out.root_causes == []
    assert out.recommendations == []


def test_parse_analyst_failure() -> None:
    """Force a parse failure by making _extract_section_bullets raise."""
    import mts.agents.parsers as parsers_mod

    original = parsers_mod._extract_section_bullets

    def _raise(md: str, heading: str) -> list[str]:
        raise RuntimeError("boom")

    parsers_mod._extract_section_bullets = _raise  # type: ignore[assignment]
    try:
        out = parse_analyst_output("any markdown")
        assert out.parse_success is False
        assert out.raw_markdown == "any markdown"
    finally:
        parsers_mod._extract_section_bullets = original  # type: ignore[assignment]


# ---------- parse_coach_output ----------


def test_parse_coach_well_formed() -> None:
    md = (
        "<!-- PLAYBOOK_START -->\nPlaybook content\n<!-- PLAYBOOK_END -->\n"
        "<!-- LESSONS_START -->\nLesson 1\n<!-- LESSONS_END -->\n"
        "<!-- COMPETITOR_HINTS_START -->\nHint A\n<!-- COMPETITOR_HINTS_END -->\n"
    )
    out = parse_coach_output(md)
    assert out.parse_success is True
    assert out.playbook == "Playbook content"
    assert out.lessons == "Lesson 1"
    assert out.hints == "Hint A"


def test_parse_coach_missing_markers() -> None:
    md = "Just a raw playbook without any markers."
    out = parse_coach_output(md)
    assert out.parse_success is True
    assert out.playbook == md.strip()
    assert out.lessons == ""
    assert out.hints == ""


def test_parse_coach_failure() -> None:
    """Force a parse failure by making parse_coach_sections raise."""
    import mts.agents.parsers as parsers_mod

    original = parsers_mod.parse_coach_sections

    def _raise(content: str) -> tuple[str, str, str]:
        raise RuntimeError("boom")

    parsers_mod.parse_coach_sections = _raise  # type: ignore[assignment]
    try:
        out = parse_coach_output("any markdown")
        assert out.parse_success is False
        assert out.raw_markdown == "any markdown"
    finally:
        parsers_mod.parse_coach_sections = original  # type: ignore[assignment]


# ---------- parse_architect_output ----------


def test_parse_architect_with_tools() -> None:
    md = (
        "Some analysis.\n"
        '```json\n{"tools": [{"name": "t1", "description": "desc", "code": "pass"}]}\n```\n'
    )
    out = parse_architect_output(md)
    assert out.parse_success is True
    assert len(out.tool_specs) == 1
    assert out.tool_specs[0]["name"] == "t1"


def test_parse_architect_no_json_block() -> None:
    md = "Architect output without any JSON."
    out = parse_architect_output(md)
    assert out.parse_success is True
    assert out.tool_specs == []


# ---------- AgentOutputs integration ----------


def test_agent_outputs_with_typed_fields() -> None:
    """Verify typed fields are consistent with string fields on AgentOutputs."""
    coach_md = (
        "<!-- PLAYBOOK_START -->\nMy playbook\n<!-- PLAYBOOK_END -->\n"
        "<!-- LESSONS_START -->\nLesson\n<!-- LESSONS_END -->\n"
        "<!-- COMPETITOR_HINTS_START -->\nHint\n<!-- COMPETITOR_HINTS_END -->\n"
    )

    competitor_typed = parse_competitor_output("raw", {"s": 1})
    analyst_typed = parse_analyst_output("## Findings\n- F1\n")
    coach_typed = parse_coach_output(coach_md)
    architect_typed = parse_architect_output("no tools")

    usage = RoleUsage(input_tokens=0, output_tokens=0, latency_ms=0, model="m")
    exec_ = RoleExecution(role="test", content="c", usage=usage, subagent_id="sa", status="ok")
    outputs = AgentOutputs(
        strategy={"s": 1},
        analysis_markdown="## Findings\n- F1\n",
        coach_markdown=coach_md,
        coach_playbook="My playbook",
        coach_lessons="Lesson",
        coach_competitor_hints="Hint",
        architect_markdown="no tools",
        architect_tools=[],
        role_executions=[exec_] * 5,
        competitor_output=competitor_typed,
        analyst_output=analyst_typed,
        coach_output=coach_typed,
        architect_output=architect_typed,
    )

    assert outputs.competitor_output is not None
    assert outputs.competitor_output.strategy == outputs.strategy
    assert outputs.analyst_output is not None
    assert outputs.analyst_output.findings == ["F1"]
    assert outputs.coach_output is not None
    assert outputs.coach_output.playbook == outputs.coach_playbook
    assert outputs.coach_output.lessons == outputs.coach_lessons
    assert outputs.coach_output.hints == outputs.coach_competitor_hints
    assert outputs.architect_output is not None
    assert outputs.architect_output.tool_specs == outputs.architect_tools
