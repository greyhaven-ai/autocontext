"""Adapt exhaustive practice references to verified versioned control campaigns.

M12 reference contracts, execution journals and descriptive score verification
are reused. This adapter changes only source provenance and family reporting;
it never routes a new control family through the frozen M11 task validator.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.cpu_campaign import _inventory
from autocontext.weight_evolution.cpu_control import _load, _schedule, control_implementation_digest, report_cpu_control_campaign
from autocontext.weight_evolution.cpu_control_models import CpuControlCampaignSpec, CpuControlTaskProvenance
from autocontext.weight_evolution.cpu_control_report import build_cpu_control_transfer_report
from autocontext.weight_evolution.cpu_diagnostic import (
    _claim,
    _disjoint,
    _entries,
    _prepare,
    _row,
    _search_details,
    _source_paths,
)
from autocontext.weight_evolution.cpu_diagnostic_models import (
    MAX_DIAGNOSTIC_FILE_BYTES,
    CpuDiagnosticBudget,
    CpuDiagnosticCase,
    CpuDiagnosticLock,
    CpuDiagnosticReference,
    CpuDiagnosticSpec,
)
from autocontext.weight_evolution.cpu_diagnostic_plan import _source_run
from autocontext.weight_evolution.cpu_diagnostic_reference import (
    CpuDiagnosticReferenceBlockedError,
    run_cpu_diagnostic_reference,
    verify_cpu_diagnostic_reference,
)
from autocontext.weight_evolution.cpu_diagnostic_report import build_cpu_diagnostic_report
from autocontext.weight_evolution.cpu_search import _existing_lock
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.session import _STAGING_NAME, _immutable_json, _read_record, _same


def _source(
    bundle: Path, output: Path,
) -> tuple[CpuControlCampaignSpec, dict[str, CpuSearchSpec], dict[str, CpuControlTaskProvenance], dict[str, Any]]:
    report = report_cpu_control_campaign(bundle, output_dir=output)
    campaign, templates, provenance = _load(bundle)
    expected = [row["run_id"] for row, _ in _schedule(campaign, templates)]
    if ([row["run_id"] for row in report["rows"]] != expected
            or any(row["status"] != "completed" for row in report["rows"])
            or report["status_counts"]["completed"] != len(expected)):
        raise ValueError("control references require every source search to be completed and sealed")
    return campaign, templates, provenance, report


def _spec(campaign: CpuControlCampaignSpec, templates: dict[str, CpuSearchSpec], source_report: dict[str, Any],
          *, source_output: Path, diagnostic_id: str) -> CpuDiagnosticSpec:
    children = {row["run_id"]: child for row, child in _schedule(campaign, templates)}
    rows = {row["run_id"]: row for row in source_report["rows"]}
    references = []
    for task in campaign.tasks:
        for repeat in campaign.repeats:
            reference_id = f"{task.task_id}.{repeat.repeat_id}"
            run_ids = [f"{reference_id}.{policy}" for policy in campaign.policies]
            child = children[run_ids[0]]
            practice = tuple(case for case in child.cases if case.role == "practice")
            references.append(CpuDiagnosticReference(
                reference_id=reference_id, task_id=task.task_id, repeat_id=repeat.repeat_id,
                inference_seed=repeat.inference_seed,
                source_runs=tuple(_source_run(rows[run_id], children[run_id], source_output) for run_id in run_ids),
                model=child.model, candidates=child.candidates, options=child.options,
                baseline_decoding=child.baseline_decoding, limits=child.limits,
                practice=tuple(CpuDiagnosticCase(case_id=case.case_id, prompt=case.prompt) for case in practice),
                practice_cases_digest=canonical_digest([case.to_dict() for case in practice]),
                source_options_digest=canonical_digest([option.to_dict() for option in child.options]),
            ))
    usage = {meter: sum(ref.required_usage[meter] for ref in references) for meter in references[0].required_usage}
    return CpuDiagnosticSpec(
        diagnostic_id=diagnostic_id, campaign_digest=campaign.digest, runtime_digest=source_report["runtime_digest"],
        campaign_report_digest=canonical_digest(source_report), campaign_ledger_digest=source_report["ledger_digest"],
        references=tuple(references), budget=CpuDiagnosticBudget(
            max_references=len(references), max_proposals=0,
            **{f"max_{meter}": value for meter, value in usage.items() if meter != "proposals"}),
        implementation_digest=control_implementation_digest(),
    )


def build_cpu_control_diagnostic_plan(
    campaign_bundle: Path, campaign_output: Path, *, output: Path, diagnostic_id: str = "cpu-control-reference-v1",
) -> CpuDiagnosticSpec:
    _disjoint(campaign_bundle, campaign_output, output)
    if output.exists():
        raise ValueError("control diagnostic plan requires a fresh directory")
    campaign, templates, _, source_report = _source(campaign_bundle, campaign_output)
    spec = _spec(campaign, templates, source_report, source_output=campaign_output, diagnostic_id=diagnostic_id)
    encoded = (json.dumps(spec.to_dict(), sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    lock = CpuDiagnosticLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded))
    output.mkdir(parents=True)
    (output / "diagnostic.json").write_bytes(encoded)
    (output / "bundle.lock.json").write_text(json.dumps(lock.to_dict(), sort_keys=True, indent=2) + "\n")
    return spec


def _context(
    plan: Path, campaign_bundle: Path, campaign_output: Path,
) -> tuple[CpuDiagnosticSpec, CpuControlCampaignSpec, dict[str, CpuSearchSpec],
           dict[str, CpuControlTaskProvenance], dict[str, Any]]:
    _disjoint(plan, campaign_bundle, campaign_output)
    if _entries(plan, 2) != {"diagnostic.json", "bundle.lock.json"}:
        raise ValueError("control diagnostic plan contains missing or unowned assets")
    raw_lock = load_small_json(plan / "bundle.lock.json")
    lock = CpuDiagnosticLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "control diagnostic lock changed its typed representation")
    identity = file_identity(plan / "diagnostic.json", max_bytes=lock.spec_size_bytes)
    if identity["sha256"] != lock.spec_sha256 or identity["size_bytes"] != lock.spec_size_bytes:
        raise ValueError("control diagnostic plan byte/hash pin mismatch")
    raw = load_small_json(plan / "diagnostic.json", max_bytes=MAX_DIAGNOSTIC_FILE_BYTES)
    spec = CpuDiagnosticSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "control diagnostic plan changed its typed representation")
    if spec.implementation_digest != control_implementation_digest():
        raise ValueError("control diagnostic implementation source changed")
    campaign, templates, provenance, source_report = _source(campaign_bundle, campaign_output)
    expected = _spec(campaign, templates, source_report, source_output=campaign_output, diagnostic_id=spec.diagnostic_id)
    _same(spec.to_dict(), expected.to_dict(), "control diagnostic differs from complete verified source schedule")
    return spec, campaign, templates, provenance, source_report


def validate_cpu_control_diagnostic(plan: Path, *, campaign_bundle: Path, campaign_output: Path) -> dict[str, Any]:
    spec, _, _, _, _ = _context(plan, campaign_bundle, campaign_output)
    return {"diagnostic_digest": spec.digest, "campaign_digest": spec.campaign_digest,
            "source_report_digest": spec.campaign_report_digest, "reference_count": len(spec.references),
            "source_run_count": sum(len(ref.source_runs) for ref in spec.references), "required_usage": spec.required_usage,
            "model_execution_performed": False, "gpu_execution_deferred": True, "promotion_eligible": False}


def _run(plan: Path, *, campaign_bundle: Path, campaign_output: Path, output_dir: Path, execute: bool) -> dict[str, Any]:
    _disjoint(plan, campaign_bundle, campaign_output, output_dir)
    root = output_dir.resolve()
    spec, campaign, templates, provenance, source_report = _context(plan, campaign_bundle, campaign_output)
    details = _search_details(spec, campaign_output)
    manifest = {"schema_version": 1, "diagnostic_digest": spec.digest, "campaign_digest": campaign.digest,
                "campaign_report_digest": spec.campaign_report_digest, "runtime_digest": spec.runtime_digest,
                "implementation_digest": spec.implementation_digest,
                "schedule": [ref.reference_id for ref in spec.references]}
    if root.exists():
        unknown = _entries(root, 24) - {".cpu-diagnostic.lock", "manifest.json", "claims", "results", "references", "report.json"}
        if any(not _STAGING_NAME.fullmatch(name) for name in unknown):
            raise ValueError("control diagnostic output contains unowned evidence")
        if (root / "manifest.json").exists():
            _same(_read_record(root / "manifest.json"), manifest, "control diagnostic manifest changed")
    if execute:
        root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cpu-diagnostic.lock"
    if lock.exists():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock) if execute else _existing_lock(lock):
        claimed = _prepare(root, manifest, write=execute, reference_count=len(spec.references))
        allowed = {ref.reference_id for ref in spec.references[:claimed]}
        if _entries(root / "references", len(spec.references)) - allowed:
            raise ValueError("control diagnostic retains an unclaimed reference")
        rows = []
        previous = "0" * 64
        pending = []
        for index, reference in enumerate(spec.references):
            claim = _claim(spec, reference, index, previous)
            name = f"{index:08d}.json"
            child = root / "references" / reference.reference_id
            checkpoint, materializations, cases = _source_paths(reference, campaign_bundle, campaign_output, templates)
            if index < claimed:
                _same(_read_record(root / "claims" / name), claim, "control reference claim differs from its schedule")
                if (root / "results" / name).exists():
                    result = _read_record(root / "results" / name)
                    evidence = canonical_digest(_inventory(child))
                    if result.get("claim_digest") != canonical_digest(claim) or result.get("evidence_digest") != evidence:
                        raise ValueError("control reference binding or retained evidence changed")
                    if result.get("status") == "completed":
                        summary = verify_cpu_diagnostic_reference(spec, reference, checkpoint_path=checkpoint,
                            materializations=materializations, cases=cases, output_dir=child)
                        row = _row(reference, "completed", summary)
                    elif result.get("status") == "failed":
                        row = _row(reference, "failed")
                    else:
                        raise ValueError("invalid retained control reference status")
                    _same(result, {"claim_digest": canonical_digest(claim), "status": row["status"],
                                   "evidence_digest": evidence, "row": row}, "control reference differs from verified evidence")
                    rows.append(row)
                    previous = canonical_digest(result)
                    continue
            rows.append(_row(reference, "pending" if index < claimed else "not-started"))
            pending.append((index, reference))
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
        original = build_cpu_diagnostic_report(spec, source_report, rows, details)
        # Keep verified per-reference/per-search evidence, while all quality
        # summaries belong to a declared control family rather than a mixture.
        diagnostics = {key: value for key, value in original.items() if key not in {"policy_summary", "semantic_digest"}}
        diagnostics["report_kind"] = "cpu_control_diagnostic_evidence"
        diagnostics["family_pooling_performed"] = False
        diagnostics["original_diagnostic_report_digest"] = canonical_digest(original)
        diagnostics["semantic_digest"] = canonical_digest(diagnostics)
        transfer = build_cpu_control_transfer_report(campaign, source_report, diagnostics, provenance)
        report = {"schema_version": 1, "report_kind": "cpu_control_diagnostic", "campaign_digest": campaign.digest,
                  "diagnostic_digest": spec.digest, "diagnostics": diagnostics, "transfer": transfer,
                  "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False}
        report["semantic_digest"] = canonical_digest(report)
        report["ledger_digest"] = previous
        if (root / "report.json").exists():
            _same(_read_record(root / "report.json"), report, "control diagnostic report differs from verified evidence")
        elif execute:
            _immutable_json(root / "report.json", report)
        return report


def run_cpu_control_diagnostic(plan: Path, *, campaign_bundle: Path, campaign_output: Path, output_dir: Path) -> dict[str, Any]:
    return _run(plan, campaign_bundle=campaign_bundle, campaign_output=campaign_output, output_dir=output_dir, execute=True)


def report_cpu_control_diagnostic(
    plan: Path, *, campaign_bundle: Path, campaign_output: Path, output_dir: Path,
) -> dict[str, Any]:
    return _run(plan, campaign_bundle=campaign_bundle, campaign_output=campaign_output, output_dir=output_dir, execute=False)
