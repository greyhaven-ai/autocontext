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
