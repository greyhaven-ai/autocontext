"""Negative branch results as reusable run evidence."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import Field, FiniteFloat, model_validator

from autocontext.context_bundles.models import stable_digest
from autocontext.util.models import StrictModel

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
NegativeResultApplicabilityScope = Literal[
    "exact_bundle",
    "bundle_family",
    "scenario_local",
    "cross_scenario",
    "context_unknown",
]
NegativeResultApplicabilityState = Literal["applicable", "qualified", "retest_due", "superseded", "excluded"]
NegativeResultRetestOutcome = Literal["confirmed", "not_reproduced", "inconclusive"]

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


class NegativeEvidenceReference(StrictModel):
    uri: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class NegativeBranchLineageEdge(StrictModel):
    parent_branch_id: str = Field(min_length=1)
    child_branch_id: str = Field(min_length=1)
    event_id: str | None


class NegativeComponentDependency(StrictModel):
    component_kind: str = Field(min_length=1)
    key: str = Field(min_length=1)
    digest: str = Field(min_length=1)


class NegativeResultContext(StrictModel):
    scenario_name: str | None
    context_bundle_digest: str | None
    context_bundle_family: str | None
    evaluator_epoch: str | None
    verifier_digest: str | None
    trial_cohort: str | None
    component_dependencies: list[NegativeComponentDependency]
    environment_fingerprint: str | None


class NegativeResultApplicabilityContext(StrictModel):
    scenario_name: str
    context_bundle_digest: str
    context_bundle_family: str | None = None
    evaluator_epoch: str
    verifier_digest: str | None = None
    trial_cohort: str | None = None
    component_digests: dict[str, str] = Field(default_factory=dict)
    environment_fingerprint: str | None = None
    observed_at: str | None = None
    stronger_evidence_available: bool = False


class NegativeResultApplicability(StrictModel):
    state: NegativeResultApplicabilityState
    effective_disposition: NegativeResultDisposition | None
    reason: str = Field(min_length=1)
    retest_eligible: bool


class NegativeResultEntry(StrictModel):
    result_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    hypothesis_node_id: str | None
    occurred_at: str = Field(min_length=1)
    failure_kind: FailureKind
    disposition: NegativeResultDisposition
    reason: str = Field(min_length=1)
    score_delta: FiniteFloat | None
    evaluated_seeds: list[str]
    evaluated_probes: list[str]
    branch_lineage: list[NegativeBranchLineageEdge]
    evidence_refs: list[NegativeEvidenceReference]
    generation_index: int | None = Field(default=None, ge=0)
    applicability_scope: NegativeResultApplicabilityScope
    context: NegativeResultContext
    evidence_expires_at: str | None
    safety_policy_authority: str | None
    retest_of_result_id: str | None
    retest_outcome: NegativeResultRetestOutcome | None
    superseded_by_result_id: str | None


class FailureModeSummary(StrictModel):
    failure_kind: FailureKind
    disposition: NegativeResultDisposition
    count: int = Field(ge=0)
    result_ids: list[str]


class NegativeResultLedger(StrictModel):
    schema_version: Literal[2] = 2
    run_id: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    entries: list[NegativeResultEntry]
    failure_mode_summary: list[FailureModeSummary]

    @model_validator(mode="after")
    def _require_unique_result_ids(self) -> Self:
        _ensure_unique_result_ids(self.entries)
        return self

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        if data.get("schema_version") == 1:
            data = _migrate_v1_ledger(data)
        return cls.model_validate(data)

    def to_markdown(self, *, applicability_context: NegativeResultApplicabilityContext | None = None) -> str:
        lessons = render_negative_result_lessons(self, applicability_context=applicability_context)
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
    scenario_name: str,
    context_bundle_digest: str,
    evaluator_epoch: str,
    generated_at: str | None = None,
    context_bundle_family: str | None = None,
    verifier_digest: str | None = None,
    trial_cohort: str | None = None,
    component_dependencies: list[NegativeComponentDependency] | None = None,
    environment_fingerprint: str | None = None,
) -> NegativeResultLedger:
    defaults = NegativeResultContext(
        scenario_name=scenario_name,
        context_bundle_digest=context_bundle_digest,
        context_bundle_family=context_bundle_family,
        evaluator_epoch=evaluator_epoch,
        verifier_digest=verifier_digest,
        trial_cohort=trial_cohort,
        component_dependencies=component_dependencies or [],
        environment_fingerprint=environment_fingerprint,
    )
    entries = [
        _entry
        for event_index, event in enumerate(events)
        if (_entry := _entry_from_event(event, defaults, event_index=event_index)) is not None
    ]
    return NegativeResultLedger(
        run_id=run_id,
        generated_at=generated_at or datetime.now().astimezone().isoformat(),
        entries=entries,
        failure_mode_summary=_failure_mode_summary(entries),
    )


def render_negative_result_lessons(
    ledger: NegativeResultLedger,
    *,
    max_entries: int = 4,
    applicability_context: NegativeResultApplicabilityContext | None = None,
) -> str:
    """Compact, evidence-backed prompt lessons; noise is intentionally omitted."""

    entries = [entry for entry in ledger.entries if entry.disposition != "noise" and entry.evidence_refs]
    rank = {"hard_ban": 0, "caution": 1, "noise": 2}
    evaluated = [(entry, evaluate_negative_result_applicability(entry, applicability_context)) for entry in entries]
    renderable = [
        (entry, applicability) for entry, applicability in evaluated if applicability.state not in {"excluded", "superseded"}
    ]
    lines: list[str] = []
    for entry, applicability in sorted(
        renderable,
        key=lambda item: (rank[item[0].disposition], item[0].result_id),
    )[:max_entries]:
        evidence = "; ".join(ref.summary for ref in entry.evidence_refs[:2])
        delta = f", delta={entry.score_delta:g}" if entry.score_delta is not None else ""
        if applicability.effective_disposition == "hard_ban":
            prefix = "Hard ban"
            suffix = "do not repeat without new evidence"
        else:
            prefix = "Caution"
            suffix = "not a ban; explore only with differentiating evidence"
        lines.append(
            f"- {prefix}: {entry.failure_kind} on {entry.branch_id} "
            f"({entry.result_id}{delta}) — {entry.reason}; applicability: {applicability.reason}; "
            f"evidence: {evidence}; {suffix}."
        )
    return "\n".join(lines)


def evaluate_negative_result_applicability(
    entry: NegativeResultEntry,
    current: NegativeResultApplicabilityContext | None,
) -> NegativeResultApplicability:
    """Determine whether a negative result constrains the current runtime context."""

    if entry.disposition == "noise":
        return _applicability("excluded", None, "noise is retained for inspection but not injected", False)
    if entry.superseded_by_result_id:
        return _applicability("superseded", None, f"superseded by retest {entry.superseded_by_result_id}", False)
    if entry.applicability_scope == "context_unknown":
        return _applicability("qualified", "caution", "legacy evidence has unknown context and cannot impose a hard ban", True)
    if current is None:
        return _applicability("qualified", "caution", "current context was not supplied; applicability is unverified", True)

    recorded = entry.context
    context_mismatch = _context_mismatch(entry.applicability_scope, recorded, current)
    if context_mismatch:
        return _applicability("retest_due", "caution", context_mismatch, True)
    if entry.evidence_expires_at and _is_at_or_after(current.observed_at, entry.evidence_expires_at):
        return _applicability("retest_due", "caution", "evidence age limit was reached", True)
    if current.stronger_evidence_available:
        return _applicability("retest_due", "caution", "stronger evidence is available", True)
    if entry.disposition == "hard_ban" and entry.applicability_scope == "cross_scenario" and not entry.safety_policy_authority:
        return _applicability("qualified", "caution", "cross-scenario hard ban lacks safety-policy authority", True)
    return _applicability("applicable", entry.disposition, f"matched {entry.applicability_scope} context", True)


def link_negative_result_retest(
    ledger: NegativeResultLedger,
    *,
    original_result_id: str,
    retest_entry: NegativeResultEntry,
) -> NegativeResultLedger:
    """Append a retest while preserving and, when disproved, superseding the original."""

    _ensure_unique_result_ids(ledger.entries)
    if retest_entry.retest_of_result_id != original_result_id or retest_entry.retest_outcome is None:
        raise ValueError("retest entry must link to the original and declare an outcome")
    if any(entry.result_id == retest_entry.result_id for entry in ledger.entries):
        raise ValueError(f"retest result already exists: {retest_entry.result_id}")
    found = False
    entries: list[NegativeResultEntry] = []
    for entry in ledger.entries:
        if entry.result_id == original_result_id:
            found = True
            superseded_by = entry.superseded_by_result_id
            if _retest_can_supersede(entry, retest_entry):
                superseded_by = retest_entry.result_id
            entries.append(entry.model_copy(update={"superseded_by_result_id": superseded_by}))
        else:
            entries.append(entry)
    if not found:
        raise ValueError(f"unknown original result: {original_result_id}")
    entries.append(retest_entry)
    return ledger.model_copy(update={"entries": entries, "failure_mode_summary": _failure_mode_summary(entries)})


def _entry_from_event(
    event: dict[str, Any],
    defaults: NegativeResultContext,
    *,
    event_index: int,
) -> NegativeResultEntry | None:
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
    result_id = _string(payload.get("result_id")) or event_id or _fallback_result_id(event, event_index)
    score_delta = _score_delta(payload)
    context = _context_from_payload(payload, defaults)
    explicit_scope = _applicability_scope(payload.get("applicability_scope"))
    scope = explicit_scope or (
        "exact_bundle"
        if context.scenario_name and context.context_bundle_digest and context.evaluator_epoch
        else "context_unknown"
    )
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
        applicability_scope=scope,
        context=context,
        evidence_expires_at=_string_or_none(payload.get("evidence_expires_at")),
        safety_policy_authority=_string_or_none(payload.get("safety_policy_authority")),
        retest_of_result_id=_string_or_none(payload.get("retest_of_result_id")),
        retest_outcome=_retest_outcome(payload.get("retest_outcome")),
        superseded_by_result_id=_string_or_none(payload.get("superseded_by_result_id")),
    )


def _fallback_result_id(event: dict[str, Any], event_index: int) -> str:
    return f"negative-{stable_digest({'event': event, 'event_index': event_index})}"


def _ensure_unique_result_ids(entries: list[NegativeResultEntry]) -> None:
    seen: set[str] = set()
    for entry in entries:
        if entry.result_id in seen:
            raise ValueError(f"duplicate negative result ID: {entry.result_id}")
        seen.add(entry.result_id)


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
        return _round_six(explicit)
    score = _float_or_none(payload.get("score"))
    baseline = _float_or_none(payload.get("baseline_score"))
    return _round_six(score - baseline) if score is not None and baseline is not None else None


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


def _context_from_payload(payload: dict[str, Any], defaults: NegativeResultContext) -> NegativeResultContext:
    raw_context = payload.get("context")
    context = raw_context if isinstance(raw_context, dict) else payload
    raw_dependencies = context.get("component_dependencies")
    dependencies = (
        [item for value in raw_dependencies if (item := _component_dependency(value)) is not None]
        if isinstance(raw_dependencies, list)
        else defaults.component_dependencies
    )
    return NegativeResultContext(
        scenario_name=_string_or_none(context.get("scenario_name")) or defaults.scenario_name,
        context_bundle_digest=_string_or_none(context.get("context_bundle_digest")) or defaults.context_bundle_digest,
        context_bundle_family=_string_or_none(context.get("context_bundle_family")) or defaults.context_bundle_family,
        evaluator_epoch=_string_or_none(context.get("evaluator_epoch")) or defaults.evaluator_epoch,
        verifier_digest=_string_or_none(context.get("verifier_digest")) or defaults.verifier_digest,
        trial_cohort=_string_or_none(context.get("trial_cohort")) or defaults.trial_cohort,
        component_dependencies=dependencies,
        environment_fingerprint=_string_or_none(context.get("environment_fingerprint")) or defaults.environment_fingerprint,
    )


def _component_dependency(value: Any) -> NegativeComponentDependency | None:
    if not isinstance(value, dict):
        return None
    component_kind = _string(value.get("component_kind"))
    key = _string(value.get("key"))
    digest = _string(value.get("digest"))
    if not component_kind or not key or not digest:
        return None
    return NegativeComponentDependency(component_kind=component_kind, key=key, digest=digest)


def _applicability_scope(value: Any) -> NegativeResultApplicabilityScope | None:
    raw = _string(value)
    if raw in {"exact_bundle", "bundle_family", "scenario_local", "cross_scenario", "context_unknown"}:
        return raw  # type: ignore[return-value]
    return None


def _retest_outcome(value: Any) -> NegativeResultRetestOutcome | None:
    raw = _string(value)
    if raw in {"confirmed", "not_reproduced", "inconclusive"}:
        return raw  # type: ignore[return-value]
    return None


def _scope_mismatch(
    scope: NegativeResultApplicabilityScope,
    recorded: NegativeResultContext,
    current: NegativeResultApplicabilityContext,
) -> str | None:
    if recorded.scenario_name is None or recorded.evaluator_epoch is None:
        return "recorded context is incomplete"
    if scope == "exact_bundle" and recorded.context_bundle_digest != current.context_bundle_digest:
        return "context bundle changed"
    if scope == "bundle_family" and (
        recorded.context_bundle_family is None or recorded.context_bundle_family != current.context_bundle_family
    ):
        return "context bundle family changed"
    if scope in {"exact_bundle", "bundle_family", "scenario_local"} and (recorded.scenario_name != current.scenario_name):
        return "scenario changed"
    return None


def _context_mismatch(
    scope: NegativeResultApplicabilityScope,
    recorded: NegativeResultContext,
    current: NegativeResultApplicabilityContext,
) -> str | None:
    scope_mismatch = _scope_mismatch(scope, recorded, current)
    if scope_mismatch:
        return scope_mismatch
    if recorded.evaluator_epoch != current.evaluator_epoch:
        return "evaluator epoch changed"
    if recorded.verifier_digest and recorded.verifier_digest != current.verifier_digest:
        return "verifier changed"
    if recorded.trial_cohort and recorded.trial_cohort != current.trial_cohort:
        return "trial cohort changed"
    if recorded.environment_fingerprint and recorded.environment_fingerprint != current.environment_fingerprint:
        return "environment fingerprint changed"
    for dependency in recorded.component_dependencies:
        key = f"{dependency.component_kind}:{dependency.key}"
        if current.component_digests.get(key) != dependency.digest:
            return f"dependency {key} changed"
    return None


def _retest_can_supersede(original: NegativeResultEntry, retest: NegativeResultEntry) -> bool:
    if retest.retest_outcome != "not_reproduced":
        return False
    if retest.disposition == "noise":
        return False
    if not retest.evidence_refs or not (retest.evaluated_seeds or retest.evaluated_probes):
        return False
    original_time = _parse_iso_timestamp(original.occurred_at)
    retest_time = _parse_iso_timestamp(retest.occurred_at)
    if original_time is None or retest_time is None or retest_time < original_time:
        return False
    if original.safety_policy_authority != retest.safety_policy_authority:
        return False
    if original.applicability_scope != retest.applicability_scope:
        return False

    recorded = original.context
    candidate = retest.context
    recorded_identity = (
        recorded.scenario_name,
        recorded.context_bundle_digest,
        recorded.context_bundle_family,
        recorded.evaluator_epoch,
        recorded.verifier_digest,
        recorded.trial_cohort,
        recorded.environment_fingerprint,
    )
    candidate_identity = (
        candidate.scenario_name,
        candidate.context_bundle_digest,
        candidate.context_bundle_family,
        candidate.evaluator_epoch,
        candidate.verifier_digest,
        candidate.trial_cohort,
        candidate.environment_fingerprint,
    )
    if recorded_identity != candidate_identity:
        return False
    recorded_dependencies = sorted(
        (dependency.component_kind, dependency.key, dependency.digest) for dependency in recorded.component_dependencies
    )
    candidate_dependencies = sorted(
        (dependency.component_kind, dependency.key, dependency.digest) for dependency in candidate.component_dependencies
    )
    return recorded_dependencies == candidate_dependencies


def _applicability(
    state: NegativeResultApplicabilityState,
    effective_disposition: NegativeResultDisposition | None,
    reason: str,
    retest_eligible: bool,
) -> NegativeResultApplicability:
    return NegativeResultApplicability(
        state=state,
        effective_disposition=effective_disposition,
        reason=reason,
        retest_eligible=retest_eligible,
    )


def _is_at_or_after(observed_at: str | None, expires_at: str) -> bool:
    observed = _parse_iso_timestamp(observed_at) if observed_at else datetime.now(UTC)
    expires = _parse_iso_timestamp(expires_at)
    if observed is None or expires is None:
        return True
    return observed >= expires


def _parse_iso_timestamp(value: str) -> datetime | None:
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _round_six(value: float) -> float:
    """Round binary64 values half away from zero in both runtimes."""

    scaled = abs(value) * 1_000_000
    if not math.isfinite(scaled):
        return value
    rounded = math.floor(scaled + 0.5) / 1_000_000
    result = math.copysign(rounded, value)
    return 0.0 if result == 0 else result


def _migrate_v1_ledger(data: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(data)
    migrated["schema_version"] = 2
    raw_entries = data.get("entries")
    entries: list[Any] = []
    if isinstance(raw_entries, list):
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                entries.append(raw_entry)
                continue
            entry = dict(raw_entry)
            entry.update(
                {
                    "applicability_scope": "context_unknown",
                    "context": {
                        "scenario_name": None,
                        "context_bundle_digest": None,
                        "context_bundle_family": None,
                        "evaluator_epoch": None,
                        "verifier_digest": None,
                        "trial_cohort": None,
                        "component_dependencies": [],
                        "environment_fingerprint": None,
                    },
                    "evidence_expires_at": None,
                    "safety_policy_authority": None,
                    "retest_of_result_id": None,
                    "retest_outcome": None,
                    "superseded_by_result_id": None,
                }
            )
            entries.append(entry)
    migrated["entries"] = entries
    return migrated


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
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


__all__ = [
    "FailureKind",
    "FailureModeSummary",
    "NegativeBranchLineageEdge",
    "NegativeComponentDependency",
    "NegativeEvidenceReference",
    "NegativeResultApplicability",
    "NegativeResultApplicabilityContext",
    "NegativeResultApplicabilityScope",
    "NegativeResultApplicabilityState",
    "NegativeResultContext",
    "NegativeResultDisposition",
    "NegativeResultEntry",
    "NegativeResultLedger",
    "NegativeResultRetestOutcome",
    "build_negative_result_ledger",
    "evaluate_negative_result_applicability",
    "link_negative_result_retest",
    "render_negative_result_lessons",
]
