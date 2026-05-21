"""AC-711: static content rubric for the Hermes ``autocontext`` skill.

Validates that the rendered SKILL.md guides a Hermes agent toward
the right autocontext workflow without requiring a live LLM in the
test loop. Every predicate is a pure function over the skill text;
a CI failure here means the shipped skill drifted away from one of
the AC-711 evaluation criteria.

Why not call a live LLM:

- Cost + flake. A real agent answer is non-deterministic and
  burns budget on each CI run.
- Coverage. Real-LLM evaluation tells us how *one* agent
  interpreted the skill; static predicates tell us whether the
  text *contains the guidance* every agent needs.
- Maintainability. A predicate is a one-line regex/substring
  check the author can read and update; an LLM grading prompt is
  another thing to keep in sync.

This module covers the AC-711 evaluation criteria:

1. Prefer CLI when MCP is not configured.
2. Use MCP only when configured / trusted / explicitly requested.
3. Never mutate Hermes skills for inspect/train workflows.
4. Explain privacy tradeoffs before ingesting sessions.
5. Document ``export_skill`` (the install path).
6. Separate Hermes Curator and autocontext responsibilities.

Each criterion maps to one or more :class:`ExpectedBehavior`
predicates. The :data:`DEFAULT_RUBRIC` then attaches those
behaviors to AC-711's six fixture prompts.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from autocontext.hermes.skill import render_autocontext_skill

PredicateFn = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class TaskPrompt:
    """One realistic agent prompt under evaluation."""

    id: str
    text: str
    scenario: str


@dataclass(frozen=True, slots=True)
class ExpectedBehavior:
    """A named predicate that must hold on the skill text.

    ``name`` is the stable identifier the rubric and reports refer
    to (e.g. ``"prefers_cli_when_mcp_unconfigured"``).
    ``description`` is the one-line human-readable rationale.
    ``predicate`` runs against the rendered skill text and returns
    True iff the behavior is supported by the text.
    """

    name: str
    description: str
    predicate: PredicateFn

    def run(self, skill_text: str) -> bool:
        return bool(self.predicate(skill_text))


@dataclass(frozen=True, slots=True)
class ValidationCase:
    """A prompt paired with the behaviors a correct skill must support."""

    prompt: TaskPrompt
    expected: tuple[ExpectedBehavior, ...]


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Per-case outcome."""

    prompt_id: str
    scenario: str
    passed: bool
    matched_behaviors: tuple[str, ...]
    missing_behaviors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "scenario": self.scenario,
            "passed": self.passed,
            "matched_behaviors": list(self.matched_behaviors),
            "missing_behaviors": list(self.missing_behaviors),
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Aggregate report across all cases in a rubric run."""

    results: tuple[ValidationResult, ...]
    case_count: int
    passed_count: int
    failed_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
        }


def validate_skill(
    *,
    skill: str | None = None,
    rubric: tuple[ValidationCase, ...] | None = None,
) -> ValidationReport:
    """Run ``rubric`` against the rendered Hermes skill text.

    ``skill`` defaults to :func:`render_autocontext_skill`; ``rubric``
    defaults to :data:`DEFAULT_RUBRIC`. Tests monkeypatch this
    module's ``render_autocontext_skill`` to simulate a broken skill,
    so the default-skill path stays exercised in CI.
    """
    if skill is None:
        skill = render_autocontext_skill()
    if rubric is None:
        rubric = DEFAULT_RUBRIC

    results: list[ValidationResult] = []
    for case in rubric:
        matched: list[str] = []
        missing: list[str] = []
        for behavior in case.expected:
            if behavior.run(skill):
                matched.append(behavior.name)
            else:
                missing.append(behavior.name)
        results.append(
            ValidationResult(
                prompt_id=case.prompt.id,
                scenario=case.prompt.scenario,
                passed=not missing,
                matched_behaviors=tuple(matched),
                missing_behaviors=tuple(missing),
            )
        )
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    return ValidationReport(
        results=tuple(results),
        case_count=len(results),
        passed_count=passed,
        failed_count=failed,
    )


def render_markdown_report(report: ValidationReport) -> str:
    """Operator-facing markdown summary of a rubric run.

    Used by the ``autoctx hermes validate-skill`` CLI when
    ``--output`` is passed. The summary is the AC-711 deliverable
    "record validation results in the project or docs".
    """
    lines: list[str] = [
        "# Hermes `autocontext` skill validation (AC-711)",
        "",
        f"Cases: **{report.case_count}** · passed: **{report.passed_count}** · failed: **{report.failed_count}**",
        "",
        "## Per-case results",
        "",
    ]
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        lines.append(f"- `{result.prompt_id}` ({result.scenario}) — **{status}**")
        if result.matched_behaviors:
            for name in result.matched_behaviors:
                lines.append(f"  - matched: `{name}`")
        if result.missing_behaviors:
            for name in result.missing_behaviors:
                lines.append(f"  - missing: `{name}`")
    lines.append("")
    return "\n".join(lines)


# --- Predicate library ----------------------------------------------------
#
# Each predicate is a tiny pure function. Keep them DRY: build a
# small palette here and reuse across the rubric, so a future skill
# refactor that drifts on one of these is caught everywhere at once.


def _has_cli_first_guidance(text: str) -> bool:
    """Skill explicitly orders CLI ahead of MCP for the default
    case."""
    pattern = re.compile(r"\bCLI[\s-]?first\b", re.IGNORECASE)
    return bool(pattern.search(text)) or "Use the CLI first" in text


def _mcp_gated_on_configuration(text: str) -> bool:
    """Skill says MCP is conditional, not the default."""
    return "MCP is optional" in text or re.search(r"Use MCP\s+only\s+when", text, re.IGNORECASE) is not None


def _refuses_skill_mutation_for_inspect_or_train(text: str) -> bool:
    """Skill tells the agent not to mutate ~/.hermes/skills/ for
    inspect/train workflows."""
    return (
        "do not edit Hermes skills" in text
        or "do not edit hermes skills" in text.lower()
        or "Hermes Curator owns Hermes skill mutation" in text
    )


def _explains_privacy_before_session_ingest(text: str) -> bool:
    """Skill warns about content sensitivity for sessions or
    trajectories before recommending an ingest. The marker is
    intentionally narrow: the skill must mention *both* a privacy
    concept (privacy / redact / opt-in / sensitive) *and* a session
    or trajectory context, so a generic "we are privacy aware" line
    does not satisfy this on its own.
    """
    privacy_kw = re.compile(
        r"privacy|redact(?:ion|ed)?|opt[-\s]?in|sensitive",
        re.IGNORECASE,
    )
    session_kw = re.compile(r"session|trajector", re.IGNORECASE)
    return bool(privacy_kw.search(text) and session_kw.search(text))


def _documents_export_skill_path(text: str) -> bool:
    """Skill names ``autoctx hermes export-skill`` so an agent
    asked to install / refresh the skill can find it."""
    return "export-skill" in text or "autoctx hermes export-skill" in text


def _separates_curator_and_autocontext(text: str) -> bool:
    """Skill cleanly separates Hermes Curator from autocontext
    responsibilities."""
    return (
        "Hermes Curator owns Hermes skill mutation" in text or re.search(r"Hermes Curator.*owns", text, re.IGNORECASE) is not None
    )


# Behavior catalog. Reused across fixture prompts so a single
# predicate failure shows up everywhere relevant (DRY).
_PREFERS_CLI = ExpectedBehavior(
    name="prefers_cli_when_mcp_unconfigured",
    description="The skill orders CLI ahead of MCP for the default case.",
    predicate=_has_cli_first_guidance,
)
_MCP_GATED = ExpectedBehavior(
    name="uses_mcp_only_when_configured",
    description="The skill gates MCP on the environment being configured.",
    predicate=_mcp_gated_on_configuration,
)
_NO_SKILL_MUTATION = ExpectedBehavior(
    name="never_mutates_hermes_skills_for_inspect_or_train",
    description="The skill refuses direct mutation of ~/.hermes/skills/ for inspect/train workflows.",
    predicate=_refuses_skill_mutation_for_inspect_or_train,
)
_PRIVACY_BEFORE_SESSIONS = ExpectedBehavior(
    name="explains_privacy_before_session_ingest",
    description="The skill warns about privacy / redaction in the context of sessions / trajectories.",
    predicate=_explains_privacy_before_session_ingest,
)
_EXPORT_SKILL = ExpectedBehavior(
    name="documents_export_skill_path",
    description="The skill names autoctx hermes export-skill as the install path.",
    predicate=_documents_export_skill_path,
)
_CURATOR_SEPARATION = ExpectedBehavior(
    name="separates_curator_and_autocontext_responsibilities",
    description="The skill separates Hermes Curator from autocontext responsibilities.",
    predicate=_separates_curator_and_autocontext,
)


# --- Default rubric -------------------------------------------------------
#
# Six fixture prompts from AC-711, each annotated with the
# behaviors the rendered skill must support so an agent presented
# with that prompt can take the right action.

DEFAULT_RUBRIC: tuple[ValidationCase, ...] = (
    ValidationCase(
        prompt=TaskPrompt(
            id="p1",
            text="Evaluate this agent strategy and improve it over several runs.",
            scenario="evaluate_and_improve",
        ),
        expected=(_PREFERS_CLI, _CURATOR_SEPARATION),
    ),
    ValidationCase(
        prompt=TaskPrompt(
            id="p2",
            text="Export the best learned approach as a Hermes skill.",
            scenario="export_best_as_skill",
        ),
        expected=(_EXPORT_SKILL, _NO_SKILL_MUTATION),
    ),
    ValidationCase(
        prompt=TaskPrompt(
            id="p3",
            text="Look at my Hermes curator reports and tell me what Autocontext can train from.",
            scenario="look_at_curator_reports",
        ),
        expected=(_NO_SKILL_MUTATION, _CURATOR_SEPARATION, _PRIVACY_BEFORE_SESSIONS),
    ),
    ValidationCase(
        prompt=TaskPrompt(
            id="p4",
            text="Use local MLX to train an advisor from my traces.",
            scenario="use_local_mlx_to_train",
        ),
        expected=(_PRIVACY_BEFORE_SESSIONS, _NO_SKILL_MUTATION),
    ),
    ValidationCase(
        prompt=TaskPrompt(
            id="p5",
            text="Should I use MCP or CLI here?",
            scenario="mcp_vs_cli",
        ),
        expected=(_PREFERS_CLI, _MCP_GATED),
    ),
    ValidationCase(
        prompt=TaskPrompt(
            id="p6",
            text="I want Autocontext to improve Hermes Curator without replacing it.",
            scenario="improve_curator_without_replacing",
        ),
        expected=(_CURATOR_SEPARATION, _NO_SKILL_MUTATION),
    ),
)


__all__ = [
    "DEFAULT_RUBRIC",
    "ExpectedBehavior",
    "TaskPrompt",
    "ValidationCase",
    "ValidationReport",
    "ValidationResult",
    "render_autocontext_skill",
    "render_markdown_report",
    "validate_skill",
]
