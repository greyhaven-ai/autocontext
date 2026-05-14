"""AC-679 (slice 3a): cross-runtime TraceFindingReport JSON contract.

The TypeScript package defines the canonical Zod schema for TraceFindingReport
at ``ts/src/analytics/trace-findings.ts``. This module is the Python-side
mirror: a Pydantic model with camelCase JSON aliases so that the two
runtimes agree on the wire format byte-for-byte, even though Python's
internal report types (``TraceWriteup`` / ``WeaknessReport`` in
``trace_reporter.py``) use a different shape.

The single shared fixture at
``fixtures/cross-runtime/trace-finding-report.json`` is validated by both
runtimes' test suites; any drift in either schema breaks that test.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

# In lockstep with TRACE_FINDING_CATEGORIES in ts/src/analytics/trace-findings.ts.
# A test in `test_cross_runtime_trace_findings.py` pins the set so adding
# a category to one runtime without the other fails CI before a TS-produced
# report can fail to parse on Python.
TraceFindingCategoryLiteral = Literal[
    "tool_call_failure",
    "agent_refusal",
    "low_outcome_score",
    "dimension_inconsistency",
]

TRACE_FINDING_CATEGORIES: tuple[str, ...] = (
    "tool_call_failure",
    "agent_refusal",
    "low_outcome_score",
    "dimension_inconsistency",
)

SeverityLiteral = Literal["low", "medium", "high"]


class _CamelModel(BaseModel):
    """Base model that accepts BOTH camelCase (wire format) and snake_case
    (Python ergonomics) field names, and dumps camelCase under
    ``by_alias=True``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class CrossRuntimeTraceFinding(_CamelModel):
    finding_id: str = Field(alias="findingId", min_length=1)
    category: TraceFindingCategoryLiteral
    severity: SeverityLiteral
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence_message_indexes: list[NonNegativeInt] = Field(alias="evidenceMessageIndexes", default_factory=list)

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):  # type: ignore[no-untyped-def]
        schema = handler(source_type)
        return schema


class CrossRuntimeFailureMotif(_CamelModel):
    motif_id: str = Field(alias="motifId", min_length=1)
    category: TraceFindingCategoryLiteral
    occurrence_count: int = Field(alias="occurrenceCount", gt=0)
    evidence_message_indexes: list[NonNegativeInt] = Field(alias="evidenceMessageIndexes", default_factory=list)
    description: str = Field(min_length=1)


class CrossRuntimeTraceFindingReport(_CamelModel):
    report_id: str = Field(alias="reportId", min_length=1)
    trace_id: str = Field(alias="traceId", min_length=1)
    source_harness: str = Field(alias="sourceHarness", min_length=1)
    findings: list[CrossRuntimeTraceFinding] = Field(default_factory=list)
    failure_motifs: list[CrossRuntimeFailureMotif] = Field(alias="failureMotifs", default_factory=list)
    summary: str = Field(min_length=1)
    created_at: str = Field(alias="createdAt", min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)


__all__ = [
    "TRACE_FINDING_CATEGORIES",
    "TraceFindingCategoryLiteral",
    "SeverityLiteral",
    "CrossRuntimeTraceFinding",
    "CrossRuntimeFailureMotif",
    "CrossRuntimeTraceFindingReport",
]
