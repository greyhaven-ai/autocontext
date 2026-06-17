"""Negative branch results as reusable run evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

FailureKind = Literal[
    "verification_failed",
    "score_regression",
    "pruned",
    "refused",
    "dead_end",
    "timeout",
    "harness_error",
    "unsafe_action",
]
NegativeResultDisposition = Literal["caution", "hard_ban", "noise"]

_NEGATIVE_EVENTS = {
    "branch_failed",
    "branch_pruned",
    "branch_rejected",
    "candidate_rejected",
    "evaluation_failed",
    "gate_rollback",
    "harness_refused",
}
_EVENT_FAILURE_KIND: dict[str, FailureKind] = {
    "branch_pruned": "pruned",
    "branch_rejected": "dead_end",
    "candidate_rejected": "verification_failed",
    "evaluation_failed": "verification_failed",
    "gate_rollback": "score_regression",
    "harness_refused": "refused",
}
_FAILURE_KINDS: set[str] = {
    "verification_failed",
    "score_regression",
    "pruned",
    "refused",
    "dead_end",
    "timeout",
    "harness_error",
    "unsafe_action",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls.model_validate(data)


class NegativeEvidenceReference(_StrictModel):
    uri: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class NegativeBranchLineageEdge(_StrictModel):
    parent_branch_id: str = Field(min_length=1)
    child_branch_id: str = Field(min_length=1)
    event_id: str | None


class NegativeResultEntry(_StrictModel):
    result_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    hypothesis_node_id: str | None
    occurred_at: str = Field(min_length=1)
    failure_kind: FailureKind
    disposition: NegativeResultDisposition
    reason: str = Field(min_length=1)
    score_delta: float | None
    evaluated_seeds: list[str]
    evaluated_probes: list[str]
    branch_lineage: list[NegativeBranchLineageEdge]
    evidence_refs: list[NegativeEvidenceReference]
    generation_index: int | None = Field(default=None, ge=0)


class FailureModeSummary(_StrictModel):
    failure_kind: FailureKind
    disposition: NegativeResultDisposition
    count: int = Field(ge=0)
    result_ids: list[str]


class NegativeResultLedger(_StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    entries: list[NegativeResultEntry]
    failure_mode_summary: list[FailureModeSummary]

    def to_markdown(self) -> str:
        lessons = render_negative_result_lessons(self)
        summary_lines = [
            f"- {item.failure_kind}/{item.disposition}: {item.count} ({', '.join(item.result_ids)})"
            for item in self.failure_mode_summary
        ]
        return "\n".join(
            [
                f"# Negative Result Ledger: {self.run_id}",
                "",
                "## Failure Modes",
                *(summary_lines or ["- None"]),
                "",
                "## Prompt Lessons",
                lessons or "- None",
                "",
            ]
        )


def build_negative_result_ledger(
    *,
    run_id: str,
    events: list[dict[str, Any]],
    generated_at: str | None = None,
) -> NegativeResultLedger:
    entries = [_entry for event in events if (_entry := _entry_from_event(event)) is not None]
    return NegativeResultLedger(
        run_id=run_id,
        generated_at=generated_at or datetime.now().astimezone().isoformat(),
        entries=entries,
        failure_mode_summary=_failure_mode_summary(entries),
    )


def render_negative_result_lessons(ledger: NegativeResultLedger, *, max_entries: int = 4) -> str:
    """Compact, evidence-backed prompt lessons; noise is intentionally omitted."""

    entries = [entry for entry in ledger.entries if entry.disposition != "noise" and entry.evidence_refs]
    rank = {"hard_ban": 0, "caution": 1, "noise": 2}
    lines: list[str] = []
    for entry in sorted(entries, key=lambda item: (rank[item.disposition], item.result_id))[:max_entries]:
        evidence = "; ".join(ref.summary for ref in entry.evidence_refs[:2])
        delta = f", delta={entry.score_delta:g}" if entry.score_delta is not None else ""
        if entry.disposition == "hard_ban":
            prefix = "Hard ban"
            suffix = "do not repeat without new evidence"
        else:
            prefix = "Caution"
            suffix = "not a ban; explore only with differentiating evidence"
        lines.append(
            f"- {prefix}: {entry.failure_kind} on {entry.branch_id} "
            f"({entry.result_id}{delta}) — {entry.reason}; evidence: {evidence}; {suffix}."
        )
    return "\n".join(lines)


def _entry_from_event(event: dict[str, Any]) -> NegativeResultEntry | None:
    raw_payload = event.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    event_type = _string(event.get("event_type") or event.get("event"))
    failure_kind = _failure_kind(payload, event_type)
    if failure_kind is None and event_type not in _NEGATIVE_EVENTS:
        return None
    branch_id = _string(event.get("branch_id") or payload.get("branch_id"))
    if not branch_id:
        return None
    event_id = _string(event.get("event_id")) or (f"seq-{event.get('seq')}" if event.get("seq") is not None else "")
    result_id = _string(payload.get("result_id")) or event_id or f"negative-{len(branch_id)}"
    score_delta = _score_delta(payload)
    return NegativeResultEntry(
        result_id=result_id,
        branch_id=branch_id,
        hypothesis_node_id=_string_or_none(payload.get("hypothesis_node_id") or event.get("hypothesis_node_id")),
        occurred_at=_string(event.get("timestamp") or event.get("ts")) or datetime.now().astimezone().isoformat(),
        failure_kind=failure_kind or _EVENT_FAILURE_KIND.get(event_type, "dead_end"),
        disposition=_disposition(payload.get("disposition")),
        reason=_string(payload.get("reason") or event.get("reason")) or "Negative branch result recorded.",
        score_delta=score_delta,
        evaluated_seeds=_string_list(payload.get("evaluated_seeds") or payload.get("seeds")),
        evaluated_probes=_string_list(payload.get("evaluated_probes") or payload.get("probes")),
        branch_lineage=_branch_lineage(event, payload, event_id, branch_id),
        evidence_refs=_evidence_refs(payload),
        generation_index=_int_or_none(payload.get("generation_index") or event.get("generation_index")),
    )


def _failure_mode_summary(entries: list[NegativeResultEntry]) -> list[FailureModeSummary]:
    groups: dict[tuple[FailureKind, NegativeResultDisposition], list[str]] = {}
    for entry in entries:
        groups.setdefault((entry.failure_kind, entry.disposition), []).append(entry.result_id)
    return [
        FailureModeSummary(failure_kind=kind, disposition=disposition, count=len(ids), result_ids=ids)
        for (kind, disposition), ids in sorted(groups.items())
    ]


def _failure_kind(payload: dict[str, Any], event_type: str) -> FailureKind | None:
    value = _string(payload.get("failure_kind"))
    if value in _FAILURE_KINDS:
        return value  # type: ignore[return-value]
    return _EVENT_FAILURE_KIND.get(event_type)


def _disposition(value: Any) -> NegativeResultDisposition:
    raw = _string(value)
    if raw in {"caution", "hard_ban", "noise"}:
        return raw  # type: ignore[return-value]
    return "caution"


def _score_delta(payload: dict[str, Any]) -> float | None:
    explicit = _float_or_none(payload.get("score_delta"))
    if explicit is not None:
        return round(explicit, 6)
    score = _float_or_none(payload.get("score"))
    baseline = _float_or_none(payload.get("baseline_score"))
    return round(score - baseline, 6) if score is not None and baseline is not None else None


def _branch_lineage(
    event: dict[str, Any],
    payload: dict[str, Any],
    event_id: str,
    branch_id: str,
) -> list[NegativeBranchLineageEdge]:
    raw = payload.get("branch_lineage")
    if isinstance(raw, list):
        edges = [_edge_from_dict(edge) for edge in raw if isinstance(edge, dict)]
        return [edge for edge in edges if edge is not None]
    parent = _string(event.get("parent_branch_id") or payload.get("parent_branch_id"))
    if not parent:
        return []
    return [NegativeBranchLineageEdge(parent_branch_id=parent, child_branch_id=branch_id, event_id=event_id or None)]


def _edge_from_dict(edge: dict[str, Any]) -> NegativeBranchLineageEdge | None:
    parent = _string(edge.get("parent_branch_id"))
    child = _string(edge.get("child_branch_id"))
    if not parent or not child:
        return None
    return NegativeBranchLineageEdge(
        parent_branch_id=parent,
        child_branch_id=child,
        event_id=_string_or_none(edge.get("event_id")),
    )


def _evidence_refs(payload: dict[str, Any]) -> list[NegativeEvidenceReference]:
    refs = payload.get("evidence_refs")
    if isinstance(refs, list):
        return [ref for item in refs if (ref := _evidence_ref(item)) is not None]
    uri = _string(payload.get("evidence_uri"))
    summary = _string(payload.get("evidence_summary"))
    return [NegativeEvidenceReference(uri=uri, summary=summary)] if uri and summary else []


def _evidence_ref(value: Any) -> NegativeEvidenceReference | None:
    if not isinstance(value, dict):
        return None
    uri = _string(value.get("uri"))
    summary = _string(value.get("summary"))
    return NegativeEvidenceReference(uri=uri, summary=summary) if uri and summary else None


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _string_or_none(value: Any) -> str | None:
    result = _string(value)
    return result or None


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


__all__ = [
    "FailureKind",
    "FailureModeSummary",
    "NegativeBranchLineageEdge",
    "NegativeEvidenceReference",
    "NegativeResultDisposition",
    "NegativeResultEntry",
    "NegativeResultLedger",
    "build_negative_result_ledger",
    "render_negative_result_lessons",
]
