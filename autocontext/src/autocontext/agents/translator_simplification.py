"""Translator simplification and analyst+coach consolidation spike (AC-188).

Track 1: Deterministic strategy extraction that can replace LLM-based
         translator calls when competitor output contains parseable JSON.

Track 2: Consolidated analyst+coach output model and benchmark harness
         for evaluating whether two separate roles can be merged without
         quality loss.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Track 1: Deterministic strategy extraction
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")


def extract_strategy_deterministic(raw_text: str) -> dict[str, Any] | None:
    """Try to extract a JSON strategy dict from raw competitor output without an LLM.

    Returns the parsed dict if successful, None if no valid JSON object found.
    Tries in order: fenced code blocks, then bare JSON objects in the text.
    """
    if not raw_text or not raw_text.strip():
        return None

    # Try fenced code blocks first
    for match in _JSON_FENCE_RE.finditer(raw_text):
        result = _try_parse_object(match.group(1).strip())
        if result is not None:
            return result

    # Try bare JSON objects in text
    for match in _JSON_OBJECT_RE.finditer(raw_text):
        result = _try_parse_object(match.group(0))
        if result is not None:
            return result

    # Last resort: try the whole text as JSON
    return _try_parse_object(raw_text.strip())


def _try_parse_object(text: str) -> dict[str, Any] | None:
    """Attempt to parse text as a JSON object. Returns None on failure or if not a dict."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        logger.debug("agents.translator_simplification: suppressed json.JSONDecodeError), ValueError", exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Track 2: Consolidated role output
# ---------------------------------------------------------------------------

_PLAYBOOK_RE = re.compile(
    r"<!--\s*PLAYBOOK_START\s*-->(.*?)<!--\s*PLAYBOOK_END\s*-->",
    re.DOTALL,
)
_LESSONS_RE = re.compile(
    r"<!--\s*LESSONS_START\s*-->(.*?)<!--\s*LESSONS_END\s*-->",
    re.DOTALL,
)
_HINTS_RE = re.compile(
    r"<!--\s*COMPETITOR_HINTS_START\s*-->(.*?)<!--\s*COMPETITOR_HINTS_END\s*-->",
    re.DOTALL,
)


def _extract_section_bullets(markdown: str, heading: str) -> list[str]:
    """Extract bullet points under a markdown heading."""
    bullets: list[str] = []
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(markdown)
    if not match:
        return bullets

    after = markdown[match.end():]
    for line in after.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("<!--"):
            break
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())

    return bullets


def _extract_marker_bullets(text: str) -> list[str]:
    """Extract bullet points from marker-delimited content."""
    bullets: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
    return bullets


class ConsolidatedRoleOutput(BaseModel):
    """Combined analyst+coach output for consolidation benchmarking."""

    raw_markdown: str
    findings: list[str]
    root_causes: list[str]
    recommendations: list[str]
    playbook: str
    lessons: list[str]
    hints: list[str]
    parse_success: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsolidatedRoleOutput:
        return cls.model_validate(data)


def parse_consolidated_output(markdown: str) -> ConsolidatedRoleOutput:
    """Parse a combined analyst+coach markdown output into structured fields."""
    findings = _extract_section_bullets(markdown, "Findings")
    root_causes = _extract_section_bullets(markdown, "Root Causes")
    recommendations = _extract_section_bullets(markdown, "Actionable Recommendations")

    playbook_match = _PLAYBOOK_RE.search(markdown)
    playbook = playbook_match.group(1).strip() if playbook_match else ""

    lessons_match = _LESSONS_RE.search(markdown)
    lessons = _extract_marker_bullets(lessons_match.group(1)) if lessons_match else []

    hints_match = _HINTS_RE.search(markdown)
    hints = _extract_marker_bullets(hints_match.group(1)) if hints_match else []

    return ConsolidatedRoleOutput(
        raw_markdown=markdown,
        findings=findings,
        root_causes=root_causes,
        recommendations=recommendations,
        playbook=playbook,
        lessons=lessons,
        hints=hints,
        parse_success=True,
    )


# ---------------------------------------------------------------------------
# Track 2: Benchmark comparison
# ---------------------------------------------------------------------------


class RoleBenchmarkResult(BaseModel):
    """Metrics from one configuration (two-role or consolidated)."""

    mode: str  # "two_role" or "consolidated"
    findings_count: int
    root_causes_count: int
    recommendations_count: int
    playbook_length: int
    lessons_count: int
    hints_count: int
    total_tokens: int
    total_latency_ms: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoleBenchmarkResult:
        return cls.model_validate(data)


def compare_role_outputs(
    two_role: RoleBenchmarkResult,
    consolidated: RoleBenchmarkResult,
) -> dict[str, Any]:
    """Compare two-role vs consolidated outputs and recommend.

    Returns a dict with deltas and a recommendation string.
    Quality is assessed by comparing counts of findings, recommendations,
    lessons, and hints. If consolidated retains >= 70% of each, and saves
    tokens, it's viable.
    """
    token_savings = two_role.total_tokens - consolidated.total_tokens
    latency_savings = two_role.total_latency_ms - consolidated.total_latency_ms
    findings_delta = consolidated.findings_count - two_role.findings_count
    root_causes_delta = consolidated.root_causes_count - two_role.root_causes_count
    recs_delta = consolidated.recommendations_count - two_role.recommendations_count
    lessons_delta = consolidated.lessons_count - two_role.lessons_count

    # Quality retention check: consolidated retains >= 70% of two-role outputs
    quality_checks = []
    if two_role.findings_count > 0:
        quality_checks.append(consolidated.findings_count / two_role.findings_count >= 0.7)
    if two_role.root_causes_count > 0:
        quality_checks.append(consolidated.root_causes_count / two_role.root_causes_count >= 0.7)
    if two_role.recommendations_count > 0:
        quality_checks.append(consolidated.recommendations_count / two_role.recommendations_count >= 0.7)
    if two_role.lessons_count > 0:
        quality_checks.append(consolidated.lessons_count / two_role.lessons_count >= 0.7)
    if two_role.hints_count > 0:
        quality_checks.append(consolidated.hints_count / two_role.hints_count >= 0.7)

    quality_retained = all(quality_checks) if quality_checks else True

    if quality_retained and token_savings > 0:
        recommendation = "consolidated_viable"
    elif not quality_retained:
        recommendation = "two_role_preferred"
    else:
        recommendation = "inconclusive"

    return {
        "token_savings": token_savings,
        "latency_savings_ms": latency_savings,
        "findings_delta": findings_delta,
        "root_causes_delta": root_causes_delta,
        "recommendations_delta": recs_delta,
        "lessons_delta": lessons_delta,
        "quality_retained": quality_retained,
        "recommendation": recommendation,
    }
