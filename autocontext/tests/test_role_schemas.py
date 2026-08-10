"""AC-913: schema-enforced role outputs, and the drift that used to be silent.

The corpus in ``docs/ac913-format-drift-measurement.json`` is real llama3.1:8b
output recorded before this work. These tests replay it offline, so the
regression is pinned in CI without needing a live model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MEASUREMENT = Path(__file__).resolve().parents[2] / "docs" / "ac913-format-drift-measurement.json"


def _recorded_drift_sample() -> str:
    payload = json.loads(MEASUREMENT.read_text(encoding="utf-8"))
    sample = payload["sample_drifted_output"]
    assert sample.strip(), "measurement file carries no recorded sample to replay"
    return str(sample)


def test_recorded_real_output_is_lost_by_the_markdown_scraper() -> None:
    """The defect, replayed from a real recorded response rather than described.

    This is not a hypothetical: it is what llama3.1:8b actually returned for
    the shipped analyst instruction. Its analysis was correct; it wrote
    ``### Findings`` with ``* `` bullets, and the scraper needs ``## Findings``
    with ``- ``. If this ever starts passing, the scraper became more tolerant
    and the measurement in docs/ needs re-recording.
    """
    from autocontext.agents.parsers import _extract_section_bullets

    text = _recorded_drift_sample()
    for heading in ("Findings", "Root Causes", "Actionable Recommendations"):
        assert _extract_section_bullets(text, heading) == [], heading


def test_schema_validation_turns_drift_into_a_typed_error() -> None:
    """AC-913 criterion 2: drift is an error, never a silently empty section."""
    from autocontext.agents.role_schemas import RoleOutputValidationError, parse_analyst_constrained

    with pytest.raises(RoleOutputValidationError) as excinfo:
        parse_analyst_constrained(_recorded_drift_sample())

    assert excinfo.value.role == "analyst"
    assert excinfo.value.reason
    # The offending text rides along, so a caller can log what drifted instead
    # of reporting that validation failed with no way to see why.
    assert excinfo.value.raw_text == _recorded_drift_sample()


def test_valid_payload_populates_every_section() -> None:
    from autocontext.agents.role_schemas import parse_analyst_constrained

    result = parse_analyst_constrained(
        json.dumps(
            {
                "findings": ["reached the first flag in 6 steps"],
                "root_causes": ["no tiebreak between equidistant flags"],
                "recommendations": ["add a deterministic tiebreak on flag id"],
            }
        )
    )
    assert result.findings == ["reached the first flag in 6 steps"]
    assert result.root_causes == ["no tiebreak between equidistant flags"]
    assert result.recommendations == ["add a deterministic tiebreak on flag id"]


def test_rendered_markdown_round_trips_through_the_old_scraper() -> None:
    """The rendered view must satisfy the parser it replaces.

    This is what makes the change safe rather than a flag day: every existing
    markdown consumer keeps working, because the derived form is exactly the
    shape the scrape path was always looking for.
    """
    from autocontext.agents.parsers import _extract_section_bullets
    from autocontext.agents.role_schemas import parse_analyst_constrained

    result = parse_analyst_constrained(
        json.dumps(
            {
                "findings": ["a", "b"],
                "root_causes": ["c"],
                "recommendations": ["d"],
            }
        )
    )
    assert _extract_section_bullets(result.raw_markdown, "Findings") == ["a", "b"]
    assert _extract_section_bullets(result.raw_markdown, "Root Causes") == ["c"]
    assert _extract_section_bullets(result.raw_markdown, "Actionable Recommendations") == ["d"]


def test_missing_field_is_rejected_not_defaulted() -> None:
    """A partial object must fail, not silently yield an empty list.

    Defaulting here would reintroduce exactly the failure mode this replaces:
    a section that is empty because nothing was said, indistinguishable from
    one that is empty because the model omitted it.
    """
    from autocontext.agents.role_schemas import RoleOutputValidationError, parse_analyst_constrained

    with pytest.raises(RoleOutputValidationError):
        parse_analyst_constrained(json.dumps({"findings": ["a"], "root_causes": ["b"]}))


def test_extra_field_is_rejected() -> None:
    """additionalProperties: false, enforced on the way back in too."""
    from autocontext.agents.role_schemas import RoleOutputValidationError, parse_analyst_constrained

    with pytest.raises(RoleOutputValidationError):
        parse_analyst_constrained(json.dumps({"findings": [], "root_causes": [], "recommendations": [], "extra": 1}))


def test_provider_schema_is_strict_and_complete() -> None:
    """The schema sent to the backend must actually constrain what comes back.

    Without ``additionalProperties: false`` and a full ``required`` list, a
    backend can satisfy the schema while omitting the fields the role exists to
    produce -- constrained decoding that constrains nothing.
    """
    from autocontext.agents.role_schemas import ANALYST_SCHEMA

    schema = ANALYST_SCHEMA.schema
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"findings", "root_causes", "recommendations"}
    for field in ("findings", "root_causes", "recommendations"):
        assert schema["properties"][field]["type"] == "array"
        assert schema["properties"][field]["items"]["type"] == "string"


def test_coach_drift_is_a_typed_error_not_a_swallowed_playbook() -> None:
    """Coach's fail-open is quieter than analyst's and worse.

    With no markers at all, parse_coach_sections treats the ENTIRE response as
    the playbook -- preamble, reasoning and all -- and that playbook is
    persisted and steers the next generation. Schema validation refuses it
    instead.
    """
    from autocontext.agents.coach import parse_coach_sections
    from autocontext.agents.role_schemas import RoleOutputValidationError, parse_coach_constrained

    prose = "Sure! Here is my advice:\n\nTry moving faster and avoid the guards."

    # Today: the whole thing silently becomes the playbook.
    playbook, lessons, hints = parse_coach_sections(prose)
    assert playbook == prose.strip()
    assert (lessons, hints) == ("", "")

    # With a schema: refused, loudly.
    with pytest.raises(RoleOutputValidationError) as excinfo:
        parse_coach_constrained(prose)
    assert excinfo.value.role == "coach"


def test_coach_rendered_markdown_round_trips_through_the_marker_parser() -> None:
    from autocontext.agents.coach import parse_coach_sections
    from autocontext.agents.role_schemas import parse_coach_constrained

    result = parse_coach_constrained(
        json.dumps({"playbook": "P", "lessons": "L", "hints": "H"})
    )
    assert parse_coach_sections(result.raw_markdown) == ("P", "L", "H")


def test_architect_malformed_proposal_raises_instead_of_yielding_no_tools() -> None:
    """Architect already spoke JSON; the gap was that failure meant an empty list.

    parse_architect_tool_specs returns [] for a malformed proposal, which is
    indistinguishable from a deliberate "I propose nothing".
    """
    from autocontext.agents.architect import parse_architect_tool_specs
    from autocontext.agents.role_schemas import RoleOutputValidationError, parse_architect_constrained

    malformed = '{"tools": [{"name": "probe"}]}'  # missing description and code

    assert parse_architect_tool_specs(malformed) == []

    with pytest.raises(RoleOutputValidationError) as excinfo:
        parse_architect_constrained(malformed)
    assert excinfo.value.role == "architect"


def test_architect_valid_proposal_survives_validation() -> None:
    from autocontext.agents.role_schemas import parse_architect_constrained

    result = parse_architect_constrained(
        json.dumps(
            {
                "tools": [{"name": "probe", "description": "d", "code": "def probe(): ..."}],
                "changelog_entry": "added probe",
            }
        )
    )
    assert [spec["name"] for spec in result.tool_specs] == ["probe"]
    assert result.changelog_entry == "added probe"


def test_every_role_schema_is_strict_and_complete() -> None:
    """Same guard as the analyst's, applied to all three.

    A schema missing additionalProperties:false or a full required list lets a
    backend satisfy it while omitting the fields the role exists to produce.
    """
    from autocontext.agents.role_schemas import ANALYST_SCHEMA, ARCHITECT_SCHEMA, COACH_SCHEMA

    expected = {
        "analyst_output": {"findings", "root_causes", "recommendations"},
        "coach_output": {"playbook", "lessons", "hints"},
        "architect_output": {"tools", "changelog_entry"},
    }
    for schema in (ANALYST_SCHEMA, COACH_SCHEMA, ARCHITECT_SCHEMA):
        assert schema.schema["additionalProperties"] is False, schema.name
        assert set(schema.schema["required"]) == expected[schema.name], schema.name
