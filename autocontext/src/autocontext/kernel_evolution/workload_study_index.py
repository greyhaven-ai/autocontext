"""External evidence-index replay for measured workload studies."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from autocontext.kernel_evolution.models import canonical_digest, content_digest, kernel_benchmark_report_digest

if TYPE_CHECKING:
    from autocontext.kernel_evolution.models import KernelBenchmarkObservation
    from autocontext.kernel_evolution.workload_study import KernelWorkloadStudyReport


@dataclass(frozen=True, slots=True)
class _ExpectedEvidence:
    observation: KernelBenchmarkObservation
    protocol_id: str
    plan_commitment: str
    protected: bool
    stream_id: str
    execution_index: int
    campaign_workload_id: str | None = None
    evaluation_wall_seconds: float | None = None
    evaluation_cost_usd: float | None = None


def _expected_observations(
    report: KernelWorkloadStudyReport,
) -> Iterator[_ExpectedEvidence]:
    specs = {spec.workload_id: spec for spec in report.workload_specs}
    for run in report.workload_runs:
        spec = specs[run.workload_id]
        confirmation_index = 0
        for execution_index, attempt in enumerate(run.result.attempts, start=1):
            yield _ExpectedEvidence(
                attempt.observation,
                spec.primary_protocol.protocol_id,
                spec.primary_protocol.plan_commitment,
                False,
                f"campaign/{run.workload_id}/primary",
                execution_index,
                campaign_workload_id=run.workload_id,
            )
            if attempt.confirmation_decision is not None:
                reservation = spec.confirmation_protocols[confirmation_index]
                confirmation_index += 1
                if attempt.confirmation_observation is not None:
                    yield _ExpectedEvidence(
                        attempt.confirmation_observation,
                        reservation.protocol_id,
                        reservation.plan_commitment,
                        True,
                        f"campaign/{run.workload_id}/confirmation-{confirmation_index:04d}",
                        1,
                        campaign_workload_id=run.workload_id,
                    )
        for workload_phase in (run.primary, run.confirmation):
            yield _ExpectedEvidence(
                workload_phase.observation,
                workload_phase.protocol_id,
                workload_phase.plan_commitment,
                True,
                f"final/{run.workload_id}/{workload_phase.role}",
                1,
                evaluation_wall_seconds=float(workload_phase.evaluation_wall_seconds),
                evaluation_cost_usd=float(workload_phase.evaluation_cost_usd),
            )
    for transfer in report.transfers:
        for transfer_phase in (transfer.primary, transfer.confirmation):
            yield _ExpectedEvidence(
                transfer_phase.observation,
                transfer_phase.protocol_id,
                transfer_phase.plan_commitment,
                True,
                (
                    f"transfer/{transfer.source_workload_id}-to-{transfer.target_workload_id}/"
                    f"{'-'.join(transfer.dimensions)}/{transfer_phase.role}"
                ),
                1,
                evaluation_wall_seconds=float(transfer_phase.evaluation_wall_seconds),
                evaluation_cost_usd=float(transfer_phase.evaluation_cost_usd),
            )


def _observation_key(expected: _ExpectedEvidence) -> tuple[object, ...]:
    observation = expected.observation
    report_digest = (
        kernel_benchmark_report_digest(observation.report) if observation.report is not None else None
    )
    return (
        expected.stream_id,
        expected.execution_index,
        expected.protocol_id,
        expected.plan_commitment,
        content_digest(observation.model_dump_json(indent=2)),
        report_digest,
    )


def _record_key(record: dict[str, Any]) -> tuple[object, ...]:
    return (
        record.get("stream_id"),
        record.get("sequence"),
        record.get("planned_protocol_id"),
        record.get("planned_plan_commitment"),
        record.get("observation_digest"),
        record.get("report_digest"),
    )


def _accounting_value(record: dict[str, Any], name: str) -> float:
    value = record.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"evidence index {name} must be finite and non-negative")
    return float(value)


def _raw_link_key(record: dict[str, Any], *, derived: bool) -> tuple[object, ...]:
    digest_name = "derived_from_observation_digest" if derived else "observation_digest"
    return (
        record.get(digest_name),
        record.get("planned_protocol_id"),
        record.get("planned_plan_commitment"),
        record.get("execution_id"),
        record.get("candidate_artifact_digest"),
        record.get("candidate_source_digest"),
        record.get("incumbent_artifact_digest"),
        record.get("incumbent_source_digest"),
    )


def _expected_execution_id(payload: dict[str, Any], expected: _ExpectedEvidence) -> str:
    observation = expected.observation
    kind = (
        "autocontext.synthetic-kernel-evaluation/v1"
        if payload.get("schema_version") == "autocontext.synthetic-kernel-study-evidence-index/v1"
        else "autocontext.kernel-evaluation/v1"
    )
    return canonical_digest(
        {
            "kind": kind,
            "study_execution_id": payload.get("study_execution_id"),
            "workload_specs_digest": payload.get("workload_specs_digest"),
            "stream_id": expected.stream_id,
            "execution_index": expected.execution_index,
            "candidate_artifact_digest": observation.candidate_artifact_digest,
            "incumbent_artifact_digest": observation.incumbent_artifact_digest,
            "protocol_id": expected.protocol_id,
            "plan_commitment": expected.plan_commitment,
        }
    )


def validate_measured_evidence_index(
    report: KernelWorkloadStudyReport,
    trust: dict[str, Any],
) -> None:
    raw = trust.get("evidence_index_bytes")
    if not isinstance(raw, bytes):
        raise ValueError("measured study trust root must provide the exact evidence index bytes")
    if content_digest(raw) != report.provenance.evidence_index_digest:
        raise ValueError("measured study evidence index bytes disagree with their pinned digest")
    try:
        payload: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("measured study evidence index is not valid JSON") from exc
    provenance = report.provenance
    expected_schema = (
        "autocontext.synthetic-kernel-study-evidence-index/v1"
        if provenance.evidence_kind == "synthetic"
        else "autocontext.kernel-study-evidence-index/v1"
    )
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != expected_schema
        or not isinstance(payload.get("records"), list)
    ):
        raise ValueError("measured study evidence index is malformed")
    expected_header = {
        "evidence_origin": provenance.evidence_kind,
        "evidence_warning": provenance.warning,
        "study_execution_id": provenance.study_execution_id,
        "workload_specs_digest": provenance.workload_specs_digest,
        "study_manifest_digest": provenance.manifest_digest,
        "study_contract_digest": provenance.contract_digest,
        "study_backend_identity": provenance.backend_identity,
    }
    if any(payload.get(name) != value for name, value in expected_header.items()):
        raise ValueError("measured study evidence index disagrees with report provenance")
    records: list[dict[str, Any]] = payload["records"]
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("measured study evidence index records must be objects")
    raw_rows: Counter[tuple[object, ...]] = Counter()
    derived_rows: Counter[tuple[object, ...]] = Counter()
    raw_by_link: dict[tuple[object, ...], dict[str, Any]] = {}
    accounting: dict[int, tuple[float, float]] = {}
    execution_pairs: dict[str, set[tuple[object, object]]] = defaultdict(set)
    execution_kinds: dict[str, Counter[object]] = defaultdict(Counter)
    for record in records:
        sequence = record.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("evidence index sequence must be a positive integer")
        execution_id = record.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("evidence index records require a non-empty execution identity")
        kind = record.get("record_kind")
        pair = (record.get("planned_protocol_id"), record.get("planned_plan_commitment"))
        execution_pairs[execution_id].add(pair)
        execution_kinds[execution_id][kind] += 1
        wall = _accounting_value(record, "evaluation_wall_seconds")
        cost = _accounting_value(record, "evaluation_cost_usd")
        accounting[id(record)] = (wall, cost)
        if kind == "raw-evaluation":
            link = _raw_link_key(record, derived=False)
            if (
                record.get("chargeable") is not True
                or record.get("derived_from_observation_digest") is not None
                or not isinstance(record.get("observation_digest"), str)
                or not isinstance(record.get("report_digest"), str)
                or record.get("eligible") is not True
                or record.get("rejection_reason") is not None
            ):
                raise ValueError("raw evidence index rows must be chargeable, eligible, report-backed, and underived")
            raw_rows[link] += 1
            raw_by_link[link] = record
        elif kind == "derived-rejection":
            link = _raw_link_key(record, derived=True)
            if (
                record.get("chargeable") is not False
                or not isinstance(record.get("derived_from_observation_digest"), str)
                or record.get("report_digest") is not None
                or record.get("eligible") is not False
                or record.get("rejection_reason") != "study_wall_budget_exceeded"
                or wall != 0.0
                or cost != 0.0
            ):
                raise ValueError("derived evidence index rows must be zero-charge reportless deadline rejections")
            derived_rows[link] += 1
        elif kind == "evaluation":
            if record.get("chargeable") is not True or record.get("derived_from_observation_digest") is not None:
                raise ValueError("evaluation index rows must be chargeable and underived")
        else:
            raise ValueError("measured study evidence index contains an unknown record kind")
    if raw_rows != derived_rows or any(count != 1 for count in raw_rows.values()):
        raise ValueError("raw evidence index rows require one matching derived rejection")
    if any(len(pairs) != 1 for pairs in execution_pairs.values()):
        raise ValueError("evidence index execution identities cannot span protocol reservations")
    allowed_execution_shapes = (
        Counter({"evaluation": 1}),
        Counter({"raw-evaluation": 1, "derived-rejection": 1}),
    )
    if any(kinds not in allowed_execution_shapes for kinds in execution_kinds.values()):
        raise ValueError("each evidence index execution identity must describe exactly one physical evaluation")
    expected_rows = tuple(_expected_observations(report))
    expected = Counter(_observation_key(item) for item in expected_rows)
    indexed = Counter(
        _record_key(record)
        for record in records
        if record.get("record_kind") != "raw-evaluation"
    )
    if indexed != expected:
        raise ValueError("measured study evidence index omits, duplicates, or adds a study observation")
    indexed_by_key: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("record_kind") != "raw-evaluation":
            indexed_by_key[_record_key(record)].append(record)
    campaign_accounting: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for expected_row in expected_rows:
        observation = expected_row.observation
        record = indexed_by_key[_observation_key(expected_row)].pop()
        kind = record.get("record_kind")
        if (
            record.get("candidate_artifact_digest") != observation.candidate_artifact_digest
            or record.get("candidate_source_digest") != observation.candidate_source_digest
            or record.get("incumbent_artifact_digest") != observation.incumbent_artifact_digest
            or record.get("incumbent_source_digest") != observation.incumbent_source_digest
            or record.get("eligible") is not observation.eligible
            or record.get("rejection_reason") != observation.rejection_reason
            or record.get("protocol_id") != observation.protocol_id
        ):
            raise ValueError("evidence index row metadata disagrees with its embedded observation")
        if record.get("execution_id") != _expected_execution_id(payload, expected_row):
            raise ValueError("evidence index execution identity does not replay from its physical evaluation")
        if observation.report is not None and kind != "evaluation":
            raise ValueError("report-backed observations must be indexed as direct evaluations")
        if kind == "derived-rejection" and observation.rejection_reason != "study_wall_budget_exceeded":
            raise ValueError("only deadline rejections may derive from a retained raw evaluation")
        wall, cost = accounting[id(record)]
        if kind == "derived-rejection":
            raw_record = raw_by_link[_raw_link_key(record, derived=True)]
            if (
                raw_record.get("stream_id") != f"{expected_row.stream_id}/over-budget-raw"
                or raw_record.get("sequence") != 1
            ):
                raise ValueError("retained raw evidence is not bound to its derived execution stream")
            raw_wall, raw_cost = accounting[id(raw_record)]
            wall += raw_wall
            cost += raw_cost
        if expected_row.campaign_workload_id is not None:
            totals = campaign_accounting[expected_row.campaign_workload_id]
            totals[0] += wall
            totals[1] += cost
        else:
            assert expected_row.evaluation_wall_seconds is not None
            assert expected_row.evaluation_cost_usd is not None
            if expected_row.evaluation_wall_seconds < wall or expected_row.evaluation_cost_usd != cost:
                raise ValueError("phase accounting disagrees with its externally pinned evaluation record")
    runs = {run.workload_id: run for run in report.workload_runs}
    for workload_id, (wall, cost) in campaign_accounting.items():
        run = runs[workload_id]
        minimum_wall = float(run.budget_state.wall_seconds) + wall
        minimum_cost = float(run.budget_state.cost_usd) + cost
        if float(run.runner_wall_seconds) < minimum_wall or float(run.runner_cost_usd) < minimum_cost:
            raise ValueError("runner accounting omits externally pinned campaign usage")
    protected_pairs = {
        (item.protocol_id, item.plan_commitment) for item in expected_rows if item.protected
    }
    executions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in records:
        pair = (record.get("planned_protocol_id"), record.get("planned_plan_commitment"))
        execution_id = record.get("execution_id")
        if pair in protected_pairs:
            assert isinstance(execution_id, str)
            executions[pair].add(execution_id)
    if any(len(executions[pair]) != 1 for pair in protected_pairs):
        raise ValueError("protected protocol reservations must have exactly one indexed execution")


__all__ = ["validate_measured_evidence_index"]
