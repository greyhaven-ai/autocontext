"""AC-679 (slice 3a): cross-runtime TraceFindingReport JSON contract.

The canonical fixture at ``fixtures/cross-runtime/trace-finding-report.json``
is the wire-format contract that both Python and TypeScript validate against.
Either runtime should be able to consume the other's output without
shape drift.

This file pins the Python side of that contract:

* the shared fixture parses through the Pydantic schema,
* round-trips through ``model_dump`` without changing field names or order,
* negative-shape mutations (wrong category enum value, missing required
  field) are rejected by the schema.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.analytics.cross_runtime_trace_findings import (
    TRACE_FINDING_CATEGORIES,
    CrossRuntimeTraceFinding,
    CrossRuntimeTraceFindingReport,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "fixtures" / "cross-runtime" / "trace-finding-report.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_shared_fixture_parses_under_pydantic_schema() -> None:
    report = CrossRuntimeTraceFindingReport.model_validate(_load_fixture())

    assert report.trace_id == "trace_cross_runtime_canonical"
    assert report.source_harness == "autocontext"
    assert len(report.findings) == 2
    assert len(report.failure_motifs) == 2
    # Findings carry both ID and category so a downstream consumer can
    # filter without re-extracting from the source trace.
    assert report.findings[0].finding_id == "finding-0"
    assert report.findings[0].category == "tool_call_failure"
    assert report.findings[1].category == "low_outcome_score"


def test_shared_fixture_round_trips_with_camelcase_field_names() -> None:
    """The wire format MUST be camelCase so TS consumers don't need a
    field-name shim. We use Pydantic aliases for the snake_case Python
    surface; ``model_dump(by_alias=True)`` is the canonical wire form."""
    raw = _load_fixture()
    report = CrossRuntimeTraceFindingReport.model_validate(raw)
    dumped = report.model_dump(by_alias=True, exclude_none=False)

    # Pin the camelCase keys at every level.
    assert "reportId" in dumped
    assert "traceId" in dumped
    assert "sourceHarness" in dumped
    assert "failureMotifs" in dumped
    assert "createdAt" in dumped
    assert "findingId" in dumped["findings"][0]
    assert "evidenceMessageIndexes" in dumped["findings"][0]
    assert "occurrenceCount" in dumped["failureMotifs"][0]


def test_taxonomy_is_in_lockstep_with_ts() -> None:
    """The Python and TS taxonomies MUST stay in lockstep. If either side
    adds a category without the other, this test catches the drift before
    a TS-produced report fails to parse on Python."""
    assert set(TRACE_FINDING_CATEGORIES) == {
        "tool_call_failure",
        "agent_refusal",
        "low_outcome_score",
        "dimension_inconsistency",
    }


def test_unknown_category_is_rejected() -> None:
    bad = _load_fixture()
    bad["findings"][0]["category"] = "not_a_real_category"
    with pytest.raises(ValidationError):
        CrossRuntimeTraceFindingReport.model_validate(bad)


def test_non_positive_occurrence_count_is_rejected() -> None:
    bad = _load_fixture()
    bad["failureMotifs"][0]["occurrenceCount"] = 0
    with pytest.raises(ValidationError):
        CrossRuntimeTraceFindingReport.model_validate(bad)


def test_missing_required_field_is_rejected() -> None:
    bad = _load_fixture()
    del bad["traceId"]
    with pytest.raises(ValidationError):
        CrossRuntimeTraceFindingReport.model_validate(bad)


def test_negative_evidence_message_index_is_rejected() -> None:
    bad = _load_fixture()
    bad["findings"][0]["evidenceMessageIndexes"] = [-1]
    with pytest.raises(ValidationError):
        CrossRuntimeTraceFindingReport.model_validate(bad)


def test_finding_severity_is_constrained_to_taxonomy() -> None:
    """severity must be one of low / medium / high to mirror the Zod enum
    on the TS side; a free-form string would let Python accept reports
    that TS would reject."""
    bad = deepcopy(_load_fixture())
    bad["findings"][0]["severity"] = "fatal"
    with pytest.raises(ValidationError):
        CrossRuntimeTraceFindingReport.model_validate(bad)


def test_cross_runtime_trace_finding_constructible_via_snake_case_kwargs() -> None:
    """The Python surface accepts snake_case kwargs for ergonomic use even
    though the JSON wire format is camelCase. This keeps the model usable
    in plain Python code without callers having to thread alias names."""
    finding = CrossRuntimeTraceFinding(
        finding_id="f-x",
        category="agent_refusal",
        severity="medium",
        title="t",
        description="d",
        evidence_message_indexes=[3],
    )
    assert finding.finding_id == "f-x"
    assert finding.evidence_message_indexes == [3]
    assert finding.model_dump(by_alias=True)["findingId"] == "f-x"
