"""Observed practice references bound to the complete new supervisor evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, load_small_json, regular_file
from autocontext.weight_evolution.cpu_campaign import _inventory, _pin
from autocontext.weight_evolution.cpu_control_diagnostic import _spec as _control_reference_spec
from autocontext.weight_evolution.cpu_control_report import build_cpu_control_transfer_report
from autocontext.weight_evolution.cpu_diagnostic import _claim, _disjoint, _entries, _row, _search_details, _source_paths
from autocontext.weight_evolution.cpu_diagnostic_models import MAX_DIAGNOSTIC_FILE_BYTES, CpuDiagnosticSpec
from autocontext.weight_evolution.cpu_diagnostic_reference import run_cpu_diagnostic_reference
from autocontext.weight_evolution.cpu_diagnostic_report import build_cpu_diagnostic_report
from autocontext.weight_evolution.cpu_failure_campaign import (
    _encode,
    _failure_summary,
    _load,
    _observation,
    _preflight,
    _prepare,
    failure_implementation_digest,
    report_cpu_failure_campaign,
)
from autocontext.weight_evolution.cpu_failure_campaign_models import (
    CpuFailureDiagnosticBinding,
    CpuFailureDiagnosticLock,
)
from autocontext.weight_evolution.cpu_failure_prefix import inspect_cpu_reference_prefix
from autocontext.weight_evolution.cpu_failure_record import build_cpu_failure_record
from autocontext.weight_evolution.cpu_search import _existing_lock
from autocontext.weight_evolution.session import _immutable_json, _read_record, _same


def _source(bundle: Path, output: Path) -> tuple[Any, ...]:
    supervisor, campaign, templates, provenance, _ = _load(bundle)
    report = report_cpu_failure_campaign(bundle, output_dir=output)
    source = report["campaign"]
    if (source["status_counts"]["completed"] != supervisor.search_budget.max_runs
            or any(row["status"] != "completed" for row in source["rows"])):
        raise ValueError("observed references require every source search to be completed and sealed")
    return supervisor, campaign, templates, provenance, report


def _plan(source: tuple[Any, ...], output: Path, diagnostic_id: str) -> tuple[CpuDiagnosticSpec, CpuFailureDiagnosticBinding]:
    supervisor, campaign, templates, _, report = source
    # The native diagnostic contract consumes the pure family-report projection.
    # A separate mandatory binding pins the full actual supervisor and its ledger.
    raw = _control_reference_spec(campaign, templates, report["campaign"], source_output=output,
                                  diagnostic_id=diagnostic_id).to_dict()
    raw["implementation_digest"] = failure_implementation_digest()
    spec = CpuDiagnosticSpec.model_validate_json(json.dumps(raw))
    if spec.budget != supervisor.reference_budget:
        raise ValueError("observed reference plan exceeds or changes its predeclared conditional capacity")
    binding = CpuFailureDiagnosticBinding(supervisor_digest=supervisor.digest,
        source_report_digest=canonical_digest(report), source_ledger_digest=report["ledger_digest"],
        diagnostic_digest=spec.digest, implementation_digest=failure_implementation_digest())
    return spec, binding


def build_cpu_failure_diagnostic_plan(
    campaign_bundle: Path, campaign_output: Path, *, output: Path, diagnostic_id: str = "cpu-observed-reference-v1",
) -> CpuDiagnosticSpec:
    _disjoint(campaign_bundle, campaign_output, output)
    if output.exists():
        raise ValueError("observed reference plan requires a fresh directory")
    spec, binding = _plan(_source(campaign_bundle, campaign_output), campaign_output, diagnostic_id)
    encoded, encoded_binding = _encode(spec.to_dict()), _encode(binding.to_dict())
    lock = CpuFailureDiagnosticLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
        binding_sha256=hashlib.sha256(encoded_binding).hexdigest(), binding_size_bytes=len(encoded_binding))
    output.mkdir(parents=True)
    (output / "diagnostic.json").write_bytes(encoded)
    (output / "binding.json").write_bytes(encoded_binding)
    (output / "bundle.lock.json").write_bytes(_encode(lock.to_dict()))
    return spec


def _context(plan: Path, bundle: Path, output: Path) -> tuple[Any, ...]:
    _disjoint(plan, bundle, output)
    if _entries(plan, 3) != {"diagnostic.json", "binding.json", "bundle.lock.json"}:
        raise ValueError("observed reference plan inventory changed")
    raw_lock = load_small_json(plan / "bundle.lock.json")
    lock = CpuFailureDiagnosticLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "observed reference lock representation changed")
    _pin(plan / "diagnostic.json", lock.spec_sha256, lock.spec_size_bytes)
    _pin(plan / "binding.json", lock.binding_sha256, lock.binding_size_bytes)
    raw = load_small_json(plan / "diagnostic.json", max_bytes=MAX_DIAGNOSTIC_FILE_BYTES)
    spec = CpuDiagnosticSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "observed reference plan representation changed")
    raw_binding = load_small_json(plan / "binding.json")
    binding = CpuFailureDiagnosticBinding.model_validate_json(json.dumps(raw_binding))
    _same(raw_binding, binding.to_dict(), "observed reference binding representation changed")
    source = _source(bundle, output)
    expected, expected_binding = _plan(source, output, spec.diagnostic_id)
    _same(spec.to_dict(), expected.to_dict(), "observed native reference plan changed")
    _same(binding.to_dict(), expected_binding.to_dict(), "observed source supervisor/report binding changed")
    return spec, binding, *source


def validate_cpu_failure_diagnostic(plan: Path, *, campaign_bundle: Path, campaign_output: Path) -> dict[str, Any]:
    spec, binding, *_ = _context(plan, campaign_bundle, campaign_output)
    return {"diagnostic_digest": spec.digest, "source_supervisor_digest": binding.supervisor_digest,
            "source_report_digest": binding.source_report_digest, "binding_digest": binding.digest,
            "reference_count": len(spec.references), "required_usage": spec.required_usage,
            "model_execution_performed": False, "gpu_execution_deferred": True, "promotion_eligible": False}


def _run(plan: Path, *, campaign_bundle: Path, campaign_output: Path, output_dir: Path, execute: bool) -> dict[str, Any]:
    _disjoint(plan, campaign_bundle, campaign_output, output_dir)
    spec, binding, supervisor, campaign, templates, provenance, full_source = _context(plan, campaign_bundle, campaign_output)
    source = full_source["campaign"]
    details = _search_details(spec, campaign_output)
    root = output_dir.resolve()
    manifest = {"schema_version": 1, "binding": binding.to_dict(), "binding_digest": binding.digest,
                "diagnostic_digest": spec.digest, "runtime_digest": spec.runtime_digest,
                "schedule": [ref.reference_id for ref in spec.references]}
    _preflight(root, manifest, lock_name=".cpu-failure-reference.lock", children=("references",))
    if execute:
        root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cpu-failure-reference.lock"
    if lock.exists():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock) if execute else _existing_lock(lock):
        claimed = _prepare(root, manifest, execute=execute, count=len(spec.references),
                           lock_name=lock.name, children=("references",))
        allowed = {ref.reference_id for ref in spec.references[:claimed]}
        if _entries(root / "references", len(spec.references)) - allowed:
            raise ValueError("observed diagnostic contains an unclaimed reference")
        rows = []
        failures = []
        pending = []
        previous = "0" * 64
        for index, ref in enumerate(spec.references):
            claim = {**_claim(spec, ref, index, previous), "binding_digest": binding.digest}
            name = f"{index:08d}.json"
            child = root / "references" / ref.reference_id
            checkpoint, materializations, cases = _source_paths(ref, campaign_bundle / "campaign", campaign_output, templates)
            if index < claimed:
                _same(_read_record(root / "claims" / name), claim, "observed reference claim changed")
                prefix = inspect_cpu_reference_prefix(spec, ref, checkpoint_path=checkpoint,
                    materializations=materializations, cases=cases, output_dir=child)
                record = _observation(root / "failures" / name, supervisor.failure_contract,
                    supervisor_digest=binding.digest, run_id=ref.reference_id, study_digest=ref.digest,
                    runtime_digest=spec.runtime_digest, prefix=prefix)
                if (root / "results" / name).exists():
                    result = _read_record(root / "results" / name)
                    if result.get("status") == "completed":
                        if prefix["status"] != "completed" or record is not None:
                            raise ValueError("completed observed reference has incomplete native evidence")
                        row = _row(ref, "completed", prefix["summary"])
                    elif result.get("status") == "failed":
                        if prefix["status"] == "completed" and record is None:
                            raise ValueError("completed native reference cannot become an unobserved failure")
                        row = _row(ref, "failed")
                    else:
                        raise ValueError("invalid observed reference status")
                    _same(result, {"claim_digest": canonical_digest(claim), "status": row["status"], "row": row,
                        "evidence_digest": canonical_digest(_inventory(child)),
                        "failure_digest": record.digest if record is not None else None},
                        "observed reference result, prefix or observation changed")
                    rows.append(row)
                    if row["status"] == "failed":
                        failures.append({"run_id": ref.reference_id, "record": record.to_dict() if record else None})
                    previous = canonical_digest(result)
                    continue
            rows.append(_row(ref, "pending" if index < claimed else "not-started"))
            pending.append((index, ref))
        if execute:
            for index, ref in pending:
                claim = {**_claim(spec, ref, index, previous), "binding_digest": binding.digest}
                name = f"{index:08d}.json"
                child = root / "references" / ref.reference_id
                checkpoint, materializations, cases = _source_paths(ref, campaign_bundle / "campaign", campaign_output, templates)
                _immutable_json(root / "claims" / name, claim)
                record = None
                if index < claimed:
                    prefix = inspect_cpu_reference_prefix(spec, ref, checkpoint_path=checkpoint,
                        materializations=materializations, cases=cases, output_dir=child)
                    record = _observation(root / "failures" / name, supervisor.failure_contract,
                        supervisor_digest=binding.digest, run_id=ref.reference_id, study_digest=ref.digest,
                        runtime_digest=spec.runtime_digest, prefix=prefix)
                    row = (_row(ref, "completed", prefix["summary"])
                           if prefix["status"] == "completed" and record is None else _row(ref, "failed"))
                else:
                    try:
                        summary = run_cpu_diagnostic_reference(spec, ref, checkpoint_path=checkpoint,
                            materializations=materializations, cases=cases, output_dir=child)
                    except Exception as error:
                        prefix = inspect_cpu_reference_prefix(spec, ref, checkpoint_path=checkpoint,
                            materializations=materializations, cases=cases, output_dir=child)
                        record = build_cpu_failure_record(supervisor.failure_contract, supervisor_digest=binding.digest,
                            run_id=ref.reference_id, study_digest=ref.digest, runtime_digest=spec.runtime_digest,
                            terminal_claim=prefix["terminal_claim"], terminal_result=prefix["terminal_result"],
                            exception=error)
                        _immutable_json(root / "failures" / name, record.to_dict())
                        row = _row(ref, "failed")
                    else:
                        row = _row(ref, "completed", summary)
                result = {"claim_digest": canonical_digest(claim), "status": row["status"], "row": row,
                          "evidence_digest": canonical_digest(_inventory(child)),
                          "failure_digest": record.digest if record is not None else None}
                _immutable_json(root / "results" / name, result)
                rows[index] = row
                if row["status"] == "failed":
                    failures.append({"run_id": ref.reference_id, "record": record.to_dict() if record else None})
                previous = canonical_digest(result)
        original = build_cpu_diagnostic_report(spec, source, rows, details)
        diagnostics = {k: v for k, v in original.items() if k not in {"policy_summary", "semantic_digest"}}
        diagnostics.update(report_kind="cpu_control_diagnostic_evidence", family_pooling_performed=False,
                           original_diagnostic_report_digest=canonical_digest(original))
        diagnostics["semantic_digest"] = canonical_digest(diagnostics)
        transfer = build_cpu_control_transfer_report(campaign, source, diagnostics, provenance)
        report = {"schema_version": 1, "report_kind": "observed_cpu_control_diagnostic",
                  "source_supervisor_digest": supervisor.digest, "source_report_digest": binding.source_report_digest,
                  "binding_digest": binding.digest, "diagnostic_digest": spec.digest,
                  "diagnostics": diagnostics, "transfer": transfer, "failures": failures,
                  "failure_coverage": _failure_summary(failures), "gpu_execution_deferred": True,
                  "promotion_eligible": False, "benchmark_result_claimed": False}
        report["semantic_digest"] = canonical_digest(report)
        report["ledger_digest"] = previous
        if (root / "report.json").exists():
            _same(_read_record(root / "report.json"), report, "observed diagnostic report changed")
        elif execute:
            _immutable_json(root / "report.json", report)
        return report


def run_cpu_failure_diagnostic(plan: Path, *, campaign_bundle: Path, campaign_output: Path, output_dir: Path) -> dict[str, Any]:
    return _run(plan, campaign_bundle=campaign_bundle, campaign_output=campaign_output, output_dir=output_dir, execute=True)


def report_cpu_failure_diagnostic(
    plan: Path, *, campaign_bundle: Path, campaign_output: Path, output_dir: Path,
) -> dict[str, Any]:
    return _run(plan, campaign_bundle=campaign_bundle, campaign_output=campaign_output, output_dir=output_dir, execute=False)
