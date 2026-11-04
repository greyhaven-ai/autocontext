"""Post-selection CPU diagnostics, independently pinned and budgeted.

The original campaign is read-only. A reference cannot run until every source
search is complete and its exact selections and evidence have been verified.
No reference result is an input to the original proposal or confirmation path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, regular_file
from autocontext.weight_evolution.cpu_campaign import _inventory, _safe, campaign_implementation_digest
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticReference, CpuDiagnosticSpec
from autocontext.weight_evolution.cpu_diagnostic_plan import load_cpu_diagnostic_context
from autocontext.weight_evolution.cpu_diagnostic_reference import (
    CpuDiagnosticReferenceBlockedError,
    run_cpu_diagnostic_reference,
    verify_cpu_diagnostic_reference,
)
from autocontext.weight_evolution.cpu_diagnostic_report import build_cpu_diagnostic_report
from autocontext.weight_evolution.cpu_search import _existing_lock
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.cpu_study import CpuCase
from autocontext.weight_evolution.session import _clear_staging, _immutable_json, _read_record, _same


class CpuDiagnosticError(ValueError):
    """Diagnostic ownership, provenance, or retained evidence is invalid."""


def diagnostic_implementation_digest() -> str:
    package = Path(__file__).parent
    names = ("cpu_diagnostic.py", "cpu_diagnostic_models.py", "cpu_diagnostic_plan.py",
             "cpu_diagnostic_reference.py", "cpu_diagnostic_report.py")
    return canonical_digest({"diagnostic": {name: file_identity(package / name) for name in names},
                             "campaign": campaign_implementation_digest()})


def _disjoint(*paths: Path) -> None:
    for path in paths:
        _safe(path.absolute())
    absolute = [path.resolve() for path in paths]
    for index, path in enumerate(absolute):
        if any(path.is_relative_to(other) or other.is_relative_to(path) for other in absolute[:index]):
            raise CpuDiagnosticError("diagnostic plan, output and source directories must not overlap")


def _entries(path: Path, maximum: int) -> set[str]:
    names: set[str] = set()
    with os.scandir(path) as entries:
        for entry in entries:
            if len(names) == maximum:
                raise CpuDiagnosticError("diagnostic directory exceeds its entry bound")
            _safe(Path(entry.path))
            names.add(entry.name)
    return names


def _prepare(root: Path, manifest: dict[str, Any], *, write: bool, reference_count: int) -> int:
    _entries(root, 24)
    if write:
        _clear_staging(root)
    entries = _entries(root, 8)
    allowed = {".cpu-diagnostic.lock", "manifest.json", "claims", "results", "references", "report.json"}
    if entries - allowed or ("manifest.json" not in entries and entries - {".cpu-diagnostic.lock"}):
        raise CpuDiagnosticError("diagnostic directory contains unowned evidence")
    if "manifest.json" in entries:
        _same(_read_record(root / "manifest.json"), manifest, "diagnostic manifest changed")
    elif write:
        _immutable_json(root / "manifest.json", manifest)
    else:
        raise CpuDiagnosticError("diagnostic manifest missing")
    for name in ("claims", "results", "references"):
        path = root / name
        if write:
            path.mkdir(exist_ok=True)
        if not path.is_dir():
            raise CpuDiagnosticError("diagnostic evidence directory missing")
        _entries(path, reference_count + 16)
        if write:
            _clear_staging(path)
    claims = _entries(root / "claims", reference_count)
    results = _entries(root / "results", reference_count)
    if (claims != {f"{i:08d}.json" for i in range(len(claims))} or not results <= claims
            or results != {f"{i:08d}.json" for i in range(len(results))} or len(claims) - len(results) > 1):
        raise CpuDiagnosticError("diagnostic journal is not a contiguous schedule prefix")
    if "report.json" in entries and (len(claims) != reference_count or results != claims):
        raise CpuDiagnosticError("completed diagnostic report is missing retained evidence")
    return len(claims)


def _row(reference: CpuDiagnosticReference, status: str, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"reference_id": reference.reference_id, "task_id": reference.task_id, "repeat_id": reference.repeat_id,
            "status": status, "reserved_capacity": reference.required_usage,
            "charged_usage": summary["charged_usage"] if summary else None, "scores": summary["scores"] if summary else None,
            "error_type": "CpuDiagnosticReferenceBlockedError" if status == "failed" else None}


def _claim(spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, index: int, previous: str) -> dict[str, Any]:
    return {"schema_version": 1, "diagnostic_digest": spec.digest, "reference_digest": reference.digest,
            "campaign_report_digest": spec.campaign_report_digest, "index": index,
            "reference_id": reference.reference_id, "reserved_capacity": reference.required_usage,
            "previous_result_digest": previous}


def _source_paths(reference: CpuDiagnosticReference, campaign_bundle: Path,
                  campaign_output: Path, templates: dict[str, CpuSearchSpec]) -> tuple[Path, Path, tuple[CpuCase, ...]]:
    return (campaign_bundle / "tasks" / reference.task_id / "base.safetensors",
            campaign_output / "searches" / reference.source_runs[0].run_id / "materializations",
            tuple(case for case in templates[reference.task_id].cases if case.role == "practice"))


def _search_details(spec: CpuDiagnosticSpec, campaign_output: Path) -> dict[str, Any]:
    details = {}
    for reference in spec.references:
        for run in reference.source_runs:
            root = campaign_output / "searches" / run.run_id
            details[run.run_id] = {"summary": _read_record(root / "complete.json"),
                                   "practice": _read_record(root / "reports" / "practice.json"),
                                   "confirmation": _read_record(root / "reports" / "confirmation.json")}
    return details


def validate_cpu_diagnostic(plan: Path, *, campaign_bundle: Path, campaign_output: Path) -> dict[str, Any]:
    _disjoint(plan, campaign_bundle, campaign_output)
    spec, _, _ = load_cpu_diagnostic_context(plan, campaign_bundle=campaign_bundle, campaign_output=campaign_output)
    return {"diagnostic_digest": spec.digest, "campaign_digest": spec.campaign_digest,
            "source_report_digest": spec.campaign_report_digest, "reference_count": len(spec.references),
            "source_run_count": sum(len(ref.source_runs) for ref in spec.references),
            "required_usage": spec.required_usage, "model_execution_performed": False,
            "gpu_execution_deferred": True, "scope": "read-only-source-and-plan-verification"}


def _run(plan: Path, *, campaign_bundle: Path, campaign_output: Path, output_dir: Path, execute: bool) -> dict[str, Any]:
    _disjoint(plan, campaign_bundle, campaign_output, output_dir)
    root = output_dir.resolve()
    spec, templates, source_report = load_cpu_diagnostic_context(
        plan, campaign_bundle=campaign_bundle, campaign_output=campaign_output,
    )
    details = _search_details(spec, campaign_output)
    manifest = {"schema_version": 1, "diagnostic_digest": spec.digest, "campaign_digest": spec.campaign_digest,
                "campaign_report_digest": spec.campaign_report_digest, "runtime_digest": spec.runtime_digest,
                "implementation_digest": spec.implementation_digest,
                "schedule": [ref.reference_id for ref in spec.references]}
    if execute:
        root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cpu-diagnostic.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock) if execute else _existing_lock(lock):
        claimed = _prepare(root, manifest, write=execute, reference_count=len(spec.references))
        allowed = {ref.reference_id for ref in spec.references[:claimed]}
        if _entries(root / "references", len(spec.references)) - allowed:
            raise CpuDiagnosticError("diagnostic retains an unclaimed reference")
        rows = []
        previous = "0" * 64
        pending = []
        for index, reference in enumerate(spec.references):
            claim = _claim(spec, reference, index, previous)
            name = f"{index:08d}.json"
            child = root / "references" / reference.reference_id
            checkpoint, materializations, cases = _source_paths(reference, campaign_bundle, campaign_output, templates)
            if index < claimed:
                _same(_read_record(root / "claims" / name), claim, "diagnostic claim differs from its pinned schedule")
                result_path = root / "results" / name
                if result_path.exists():
                    result = _read_record(result_path)
                    inventory_digest = canonical_digest(_inventory(child))
                    if result.get("claim_digest") != canonical_digest(claim) or result.get("evidence_digest") != inventory_digest:
                        raise CpuDiagnosticError("diagnostic result binding or retained reference evidence changed")
                    if result.get("status") == "completed":
                        summary = verify_cpu_diagnostic_reference(
                            spec, reference, checkpoint_path=checkpoint, materializations=materializations,
                            cases=cases, output_dir=child,
                        )
                        row = _row(reference, "completed", summary)
                    elif result.get("status") == "failed":
                        row = _row(reference, "failed")
                    else:
                        raise CpuDiagnosticError("invalid retained diagnostic result status")
                    _same(result, {"claim_digest": canonical_digest(claim), "status": row["status"],
                                   "evidence_digest": inventory_digest, "row": row},
                          "diagnostic result differs from verified reference evidence")
                    rows.append(row)
                    previous = canonical_digest(result)
                    continue
            rows.append(_row(reference, "pending" if index < claimed else "not-started"))
            pending.append((index, reference))
        # Validate every retained parent result and completed child before the
        # first new reference dispatch. Uncertain children validate their own
        # completed prefix and pending claim before being burned as a failure.
        if execute:
            for index, reference in pending:
                claim = _claim(spec, reference, index, previous)
                name = f"{index:08d}.json"
                _immutable_json(root / "claims" / name, claim)
                child = root / "references" / reference.reference_id
                checkpoint, materializations, cases = _source_paths(reference, campaign_bundle, campaign_output, templates)
                try:
                    summary = run_cpu_diagnostic_reference(spec, reference, checkpoint_path=checkpoint,
                                                           materializations=materializations, cases=cases, output_dir=child)
                    row = _row(reference, "completed", summary)
                except CpuDiagnosticReferenceBlockedError:
                    row = _row(reference, "failed")
                result = {"claim_digest": canonical_digest(claim), "status": row["status"],
                          "evidence_digest": canonical_digest(_inventory(child)), "row": row}
                _immutable_json(root / "results" / name, result)
                rows[index] = row
                previous = canonical_digest(result)
        report = build_cpu_diagnostic_report(spec, source_report, rows, details)
        report["ledger_digest"] = previous
        if (root / "report.json").exists():
            _same(_read_record(root / "report.json"), report, "diagnostic report differs from verified evidence")
        elif execute:
            _immutable_json(root / "report.json", report)
        return report


def run_cpu_diagnostic(plan: Path, *, campaign_bundle: Path, campaign_output: Path, output_dir: Path) -> dict[str, Any]:
    """Run/resume a separate reference schedule; never execute the source search."""
    return _run(plan, campaign_bundle=campaign_bundle, campaign_output=campaign_output, output_dir=output_dir, execute=True)


def report_cpu_diagnostic(plan: Path, *, campaign_bundle: Path, campaign_output: Path, output_dir: Path) -> dict[str, Any]:
    """Verify and describe retained references without writes or new inference."""
    return _run(plan, campaign_bundle=campaign_bundle, campaign_output=campaign_output, output_dir=output_dir, execute=False)
