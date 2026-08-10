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

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autocontext.agents.contracts import AnalystOutput
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


class AnalystPayload(_StrictModel):
    """The analyst's three sections, as data rather than headings."""

    findings: list[str] = Field(description="What the run showed, one claim per item.")
    root_causes: list[str] = Field(description="Why each failure happened.")
    recommendations: list[str] = Field(
        description="Concrete changes for the next generation, each naming a parameter or behavior."
    )


def _output_schema(name: str, model: type[BaseModel]) -> OutputSchema:
    """Build the provider-facing schema from the same model used to validate."""
    schema: dict[str, Any] = model.model_json_schema()
    # The title is pydantic bookkeeping, not part of the contract, and some
    # backends echo it back into the generated object.
    schema.pop("title", None)
    return OutputSchema(name=name, schema=schema)


ANALYST_SCHEMA = _output_schema("analyst_output", AnalystPayload)


def parse_analyst_constrained(raw_text: str) -> AnalystOutput:
    """Validate a schema-constrained analyst response into the typed contract.

    Raises:
        RoleOutputValidationError: if the payload is not valid JSON or does not
            match the schema. Deliberately loud: the whole point is that drift
            stops being indistinguishable from "the analyst had nothing to say".
    """
    try:
        payload = AnalystPayload.model_validate_json(raw_text)
    except ValidationError as exc:
        raise RoleOutputValidationError("analyst", str(exc), raw_text) from exc
    except ValueError as exc:  # malformed JSON
        raise RoleOutputValidationError("analyst", f"not valid JSON: {exc}", raw_text) from exc

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
