"""Schema-enforced role outputs (AC-913).

Today a role's output is recovered by scraping markdown headings, and drift is
silent: ``_extract_section_bullets`` returns ``[]`` for a heading it cannot
find, so a correct analysis with the wrong heading level becomes an empty
contract and the loop continues as though the role said nothing. Measured on
llama3.1:8b, that happened in 20 of 20 trials -- see
``docs/ac913-format-drift-measurement.json``.

This module gives each role a schema instead. The same declaration does two
jobs: it is sent to the provider to constrain decoding, and it validates what
comes back. Anything that does not conform raises ``RoleOutputValidationError``
rather than yielding an empty section.

Pydantic is used rather than a JSON Schema library because it is already a core
dependency and does both halves. Adding ``jsonschema`` would mean touching
``uv.lock``, which carries a deliberate supply-chain quarantine.

Markdown is kept, but as a *rendered view* of validated data (the direction the
issue leans toward), so ``analysis.md`` stays readable without being the thing
the pipeline depends on for correctness.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from autocontext.agents.contracts import AnalystOutput, ArchitectOutput, CoachOutput
from autocontext.providers.base import OutputSchema


class RoleOutputValidationError(ValueError):
    """A role's output did not conform to its schema.

    Carries the role and the underlying reason so a caller can log which role
    drifted and on what, instead of discovering an empty contract later with no
    trace of why. This is the typed failure AC-913 requires in place of the
    silent-empty path.
    """

    def __init__(self, role: str, reason: str, raw_text: str) -> None:
        super().__init__(f"{role} output failed schema validation: {reason}")
        self.role = role
        self.reason = reason
        self.raw_text = raw_text


class _StrictModel(BaseModel):
    """Base for role payloads.

    ``extra="forbid"`` emits ``additionalProperties: false``, which OpenAI's
    strict json_schema mode requires and which stops a backend from padding the
    object with fields no role reads.
    """

    model_config = ConfigDict(extra="forbid")


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AnalystPayload(_StrictModel):
    """The analyst's three sections, as data rather than headings."""

    findings: list[NonEmptyText] = Field(min_length=1, description="What the run showed, one claim per item.")
    root_causes: list[NonEmptyText] = Field(min_length=1, description="Why each failure happened.")
    recommendations: list[NonEmptyText] = Field(
        min_length=1,
        description="Concrete changes for the next generation, each naming a parameter or behavior."
    )


def _output_schema(name: str, model: type[BaseModel]) -> OutputSchema:
    """Build the provider-facing schema from the same model used to validate."""
    schema: dict[str, Any] = model.model_json_schema()
    # The title is pydantic bookkeeping, not part of the contract, and some
    # backends echo it back into the generated object.
    schema.pop("title", None)
    return OutputSchema(name=name, schema=schema)


class CoachPayload(_StrictModel):
    """The coach's three sections.

    Today these are delimited by HTML comment markers, and the fallback when a
    model emits no markers at all is to treat the entire response as the
    playbook -- preamble, reasoning and all. That is a quieter failure than the
    analyst's empty section but a worse one: the playbook is persisted and
    steers the next generation. A schema removes the marker convention rather
    than asking a model to honor it.
    """

    playbook: str = Field(description="The complete replacement playbook, consolidated and deduplicated.")
    lessons: str = Field(description="Operational lessons, each a concrete prescriptive rule.")
    hints: str = Field(description="Concrete hints for the competitor's next attempt.")


class ArchitectToolSpec(_StrictModel):
    """One proposed tool. Mirrors what parse_architect_tool_specs already requires."""

    name: NonEmptyText = Field(description="Tool identifier.")
    description: NonEmptyText = Field(description="What the tool does and when to reach for it.")
    code: NonEmptyText = Field(description="Complete implementation.")


class ArchitectHarnessSpec(_StrictModel):
    """One executable pre-tournament strategy validator."""

    name: NonEmptyText = Field(description="Validator identifier.")
    description: str = Field(description="What the validator protects against, or empty.")
    code: NonEmptyText = Field(description="Complete validate_strategy implementation.")


class ArchitectMutationSpec(_StrictModel):
    """One persistent harness mutation, with inapplicable selectors empty."""

    type: Literal["prompt_fragment", "context_policy", "completion_check", "tool_instruction"]
    target_role: str = Field(description="Target role for prompt_fragment, otherwise empty.")
    component: str = Field(description="Context component for context_policy, otherwise empty.")
    tool_name: str = Field(description="Tool identifier for tool_instruction, otherwise empty.")
    content: NonEmptyText
    rationale: str


class ArchitectDAGChange(_StrictModel):
    """One role-DAG change directive."""

    action: Literal["add_role", "remove_role"]
    name: NonEmptyText
    depends_on: list[NonEmptyText] = Field(description="Dependencies for add_role; empty for remove_role.")


class ArchitectTuningParameter(_StrictModel):
    """One bounded adaptive configuration proposal."""

    name: Literal[
        "backpressure_min_delta",
        "matches_per_generation",
        "rlm_max_turns",
        "architect_every_n_gens",
        "probe_matches",
    ]
    value: float


class ArchitectPayload(_StrictModel):
    """The architect's proposal.

    All fields are required so OpenAI strict mode can enforce the object. Empty
    lists represent channels with no proposal; rendered markdown omits their
    legacy marker blocks, preserving the existing downstream behavior.
    """

    observed_bottlenecks: list[str] = Field(description="Observed infrastructure bottlenecks.")
    impact_hypothesis: str = Field(description="Expected impact of the proposed changes.")
    tools: list[ArchitectToolSpec] = Field(description="Proposed tools; an empty list means no proposal.")
    harness: list[ArchitectHarnessSpec] = Field(description="Proposed executable harness validators.")
    mutations: list[ArchitectMutationSpec] = Field(description="Proposed persistent harness mutations.")
    dag_changes: list[ArchitectDAGChange] = Field(description="Proposed role-DAG changes.")
    tuning_parameters: list[ArchitectTuningParameter] = Field(description="Proposed adaptive parameters.")
    tuning_reasoning: str = Field(description="Reasoning for adaptive parameters, or empty.")
    changelog_entry: str = Field(description="One line describing the change, or empty if nothing is proposed.")


ANALYST_SCHEMA = _output_schema("analyst_output", AnalystPayload)
COACH_SCHEMA = _output_schema("coach_output", CoachPayload)
ARCHITECT_SCHEMA = _output_schema("architect_output", ArchitectPayload)


def parse_analyst_constrained(raw_text: str) -> AnalystOutput:
    """Validate a schema-constrained analyst response into the typed contract.

    Raises:
        RoleOutputValidationError: if the payload is not valid JSON or does not
            match the schema. Deliberately loud: the whole point is that drift
            stops being indistinguishable from "the analyst had nothing to say".
    """
    payload: AnalystPayload = _validate("analyst", AnalystPayload, raw_text)
    return AnalystOutput(
        raw_markdown=render_analyst_markdown(payload),
        findings=payload.findings,
        root_causes=payload.root_causes,
        recommendations=payload.recommendations,
        parse_success=True,
    )


def render_analyst_markdown(payload: AnalystPayload) -> str:
    """Render validated data back into the markdown shape the repo already reads.

    Deliberately emits exactly what ``_extract_section_bullets`` expects: ``##``
    headings and ``- `` bullets. That keeps analysis.md readable, keeps every
    existing markdown consumer working, and means the rendered form round-trips
    through the old parser -- which is what makes replacing the scrape path
    safe rather than a flag day.
    """
    sections = (
        ("Findings", payload.findings),
        ("Root Causes", payload.root_causes),
        ("Actionable Recommendations", payload.recommendations),
    )
    blocks = []
    for heading, items in sections:
        lines = "\n".join(f"- {item}" for item in items)
        blocks.append(f"## {heading}\n\n{lines}" if lines else f"## {heading}\n")
    return "\n\n".join(blocks) + "\n"


def _validate(role: str, model: type[BaseModel], raw_text: str) -> Any:
    """Validate one role payload, converting any failure into the typed error."""
    try:
        return model.model_validate_json(raw_text)
    except ValidationError as exc:
        raise RoleOutputValidationError(role, str(exc), raw_text) from exc
    except ValueError as exc:  # malformed JSON
        raise RoleOutputValidationError(role, f"not valid JSON: {exc}", raw_text) from exc


def parse_coach_constrained(raw_text: str) -> CoachOutput:
    """Validate a schema-constrained coach response into the typed contract."""
    payload: CoachPayload = _validate("coach", CoachPayload, raw_text)
    return CoachOutput(
        raw_markdown=render_coach_markdown(payload),
        playbook=payload.playbook,
        lessons=payload.lessons,
        hints=payload.hints,
        parse_success=True,
    )


def parse_architect_constrained(raw_text: str) -> ArchitectOutput:
    """Validate a schema-constrained architect response into the typed contract."""
    payload: ArchitectPayload = _validate("architect", ArchitectPayload, raw_text)
    rendered = render_architect_markdown(payload)
    # Keep the existing AST validation for executable harness code even after
    # the outer object has passed schema validation.
    from autocontext.agents.architect import parse_architect_harness_specs

    return ArchitectOutput(
        raw_markdown=rendered,
        tool_specs=[spec.model_dump() for spec in payload.tools],
        harness_specs=parse_architect_harness_specs(rendered),
        changelog_entry=payload.changelog_entry,
        parse_success=True,
    )


def render_architect_markdown(payload: ArchitectPayload) -> str:
    """Render every validated architect channel into its legacy wire format."""
    bottlenecks = "\n".join(f"- {item}" for item in payload.observed_bottlenecks)
    tools_summary = "\n".join(f"- **{tool.name}**: {tool.description}" for tool in payload.tools)
    blocks = [
        f"## Observed Bottlenecks\n\n{bottlenecks}" if bottlenecks else "## Observed Bottlenecks\n",
        f"## Tool Proposals\n\n{tools_summary}" if tools_summary else "## Tool Proposals\n",
        f"## Impact Hypothesis\n\n{payload.impact_hypothesis}",
        "```json\n" + json.dumps({"tools": [tool.model_dump() for tool in payload.tools]}) + "\n```",
    ]
    if payload.harness:
        blocks.append(
            "<!-- HARNESS_START -->\n"
            + json.dumps({"harness": [spec.model_dump() for spec in payload.harness]})
            + "\n<!-- HARNESS_END -->"
        )
    if payload.mutations:
        blocks.append(
            "<!-- MUTATIONS_START -->\n"
            + json.dumps({"mutations": [mutation.model_dump() for mutation in payload.mutations]})
            + "\n<!-- MUTATIONS_END -->"
        )
    if payload.dag_changes:
        blocks.append(
            "<!-- DAG_CHANGES_START -->\n"
            + json.dumps({"changes": [change.model_dump() for change in payload.dag_changes]})
            + "\n<!-- DAG_CHANGES_END -->"
        )
    if payload.tuning_parameters:
        tuning: dict[str, float | str] = {
            parameter.name: parameter.value for parameter in payload.tuning_parameters
        }
        tuning["reasoning"] = payload.tuning_reasoning
        blocks.append(
            "<!-- TUNING_PROPOSAL_START -->\n"
            + json.dumps(tuning)
            + "\n<!-- TUNING_PROPOSAL_END -->"
        )
    if payload.changelog_entry:
        blocks.append(f"## Changelog\n\n{payload.changelog_entry}")
    return "\n\n".join(blocks) + "\n"


def render_coach_markdown(payload: CoachPayload) -> str:
    """Render validated coach data back into the marker format the repo reads.

    Emits the same delimiters parse_coach_sections expects, so the rendered
    form round-trips through the existing extractor and every consumer of the
    marker convention keeps working.
    """
    return (
        "<!-- PLAYBOOK_START -->\n"
        f"{payload.playbook}\n"
        "<!-- PLAYBOOK_END -->\n\n"
        "<!-- LESSONS_START -->\n"
        f"{payload.lessons}\n"
        "<!-- LESSONS_END -->\n\n"
        "<!-- COMPETITOR_HINTS_START -->\n"
        f"{payload.hints}\n"
        "<!-- COMPETITOR_HINTS_END -->\n"
    )
