"""Freeze a diagnostic schedule from independently verified M11 evidence."""

from __future__ import annotations

import hashlib
import json
import os
from itertools import combinations
from pathlib import Path
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json
from autocontext.weight_evolution.cpu_campaign import _load, _schedule, report_cpu_campaign
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignSpec
from autocontext.weight_evolution.cpu_diagnostic_models import (
    MAX_DIAGNOSTIC_FILE_BYTES,
    CpuDiagnosticBudget,
    CpuDiagnosticCase,
    CpuDiagnosticLock,
    CpuDiagnosticReference,
    CpuDiagnosticSourceRun,
    CpuDiagnosticSpec,
)
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.session import _read_record, _same


def _safe(path: Path) -> None:
    if any(part.is_symlink() for part in (path.absolute(), *path.absolute().parents)):
        raise ValueError("diagnostic paths must not traverse symlinks")


def _disjoint(*paths: Path) -> None:
    for path in paths:
        _safe(path)
    for left, right in combinations((path.resolve() for path in paths), 2):
        if left.is_relative_to(right) or right.is_relative_to(left):
            raise ValueError("diagnostic plan and source campaign directories must not overlap")


def _implementation_digest() -> str:
    # The coordinator includes the new plan, report and execution modules in
    # its closure without changing any source pinned by the retained M11 run.
    from autocontext.weight_evolution.cpu_diagnostic import diagnostic_implementation_digest

    return diagnostic_implementation_digest()


def verify_cpu_diagnostic_source(
    campaign_bundle: Path, campaign_output: Path,
) -> tuple[CpuCampaignSpec, dict[str, CpuSearchSpec], dict[str, Any]]:
    """Read-only proof of the complete original roster, including child replay."""
    _safe(campaign_bundle)
    _safe(campaign_output)
    report = report_cpu_campaign(campaign_bundle, output_dir=campaign_output)
    campaign, templates = _load(campaign_bundle)
    schedule = _schedule(campaign, templates)
    expected_ids = [row["run_id"] for row, _ in schedule]
    rows = report["rows"]
    if ([row["run_id"] for row in rows] != expected_ids
            or any(row["status"] != "completed" for row in rows)
            or report["status_counts"]["completed"] != len(expected_ids)):
        raise ValueError("diagnostics require every pinned source campaign run to be completed")
    if report["campaign_digest"] != campaign.digest:
        raise ValueError("diagnostic source campaign identity changed")
    return campaign, templates, report


def _source_run(row: dict[str, Any], child: CpuSearchSpec, campaign_output: Path) -> CpuDiagnosticSourceRun:
    directory = campaign_output / "searches" / row["run_id"]
    summary = _read_record(directory / "complete.json")
    selection = _read_record(directory / "selection.json")
    semantic = {key: value for key, value in summary.items()
                if key not in {"semantic_digest", "receipt_evidence_digests", "journal_digest"}}
    if (canonical_digest(semantic) != row["summary_semantic_digest"]
            or summary["semantic_digest"] != row["summary_semantic_digest"]
            or summary["study_digest"] != child.digest or row["study_digest"] != child.digest
            or summary["selection_digest"] != canonical_digest(selection)
            or selection["study_digest"] != child.digest):
        raise ValueError("diagnostic source selection or summary differs from verified campaign evidence")
    return CpuDiagnosticSourceRun(run_id=row["run_id"], policy=row["policy"], study_digest=child.digest,
                                  selection_digest=summary["selection_digest"],
                                  summary_semantic_digest=row["summary_semantic_digest"])


def _spec_from_source(
    campaign_bundle: Path, campaign_output: Path, *, diagnostic_id: str,
    verified_source: tuple[CpuCampaignSpec, dict[str, CpuSearchSpec], dict[str, Any]] | None = None,
) -> CpuDiagnosticSpec:
    campaign, templates, report = (verify_cpu_diagnostic_source(campaign_bundle, campaign_output)
                                   if verified_source is None else verified_source)
    children = {row["run_id"]: child for row, child in _schedule(campaign, templates)}
    rows = {row["run_id"]: row for row in report["rows"]}
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
                source_runs=tuple(_source_run(rows[run_id], children[run_id], campaign_output) for run_id in run_ids),
                model=child.model, candidates=child.candidates, options=child.options,
                baseline_decoding=child.baseline_decoding, limits=child.limits,
                practice=tuple(CpuDiagnosticCase(case_id=case.case_id, prompt=case.prompt) for case in practice),
                practice_cases_digest=canonical_digest([case.to_dict() for case in practice]),
                source_options_digest=canonical_digest([option.to_dict() for option in child.options]),
            ))
    usage = {meter: sum(ref.required_usage[meter] for ref in references) for meter in references[0].required_usage}
    return CpuDiagnosticSpec(
        diagnostic_id=diagnostic_id, campaign_digest=campaign.digest, runtime_digest=report["runtime_digest"],
        campaign_report_digest=canonical_digest(report), campaign_ledger_digest=report["ledger_digest"],
        references=tuple(references), budget=CpuDiagnosticBudget(
            max_references=len(references), max_proposals=0,
            **{f"max_{meter}": value for meter, value in usage.items() if meter != "proposals"}),
        implementation_digest=_implementation_digest(),
    )


def build_cpu_diagnostic_plan(
    campaign_bundle: Path, campaign_output: Path, *, output: Path, diagnostic_id: str = "cpu-search-diagnostic-v1",
) -> CpuDiagnosticSpec:
    """Freeze a fresh plan after source replay, without new model execution."""
    _safe(output)
    if output.exists():
        raise ValueError("diagnostic plan requires a fresh output directory")
    _disjoint(campaign_bundle, campaign_output, output)
    spec = _spec_from_source(campaign_bundle, campaign_output, diagnostic_id=diagnostic_id)
    encoded = (json.dumps(spec.to_dict(), sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    lock = CpuDiagnosticLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded))
    output.mkdir(parents=True)
    (output / "diagnostic.json").write_bytes(encoded)
    (output / "bundle.lock.json").write_text(json.dumps(lock.to_dict(), sort_keys=True, indent=2) + "\n")
    return spec


def load_cpu_diagnostic_plan(plan: Path) -> CpuDiagnosticSpec:
    """Read exact plan bytes and implementation pins; no campaign verification."""
    _safe(plan)
    expected_entries = {"diagnostic.json", "bundle.lock.json"}
    entries: set[str] = set()
    with os.scandir(plan) as iterator:
        for entry in iterator:
            if len(entries) >= 2 or entry.name not in expected_entries:
                raise ValueError("diagnostic plan contains missing or unowned assets")
            entries.add(entry.name)
    if entries != expected_entries:
        raise ValueError("diagnostic plan contains missing or unowned assets")
    raw_lock = load_small_json(plan / "bundle.lock.json")
    lock = CpuDiagnosticLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "diagnostic lock changed its exact typed representation")
    identity = file_identity(plan / "diagnostic.json", max_bytes=lock.spec_size_bytes)
    if identity["sha256"] != lock.spec_sha256 or identity["size_bytes"] != lock.spec_size_bytes:
        raise ValueError("diagnostic plan byte/hash pin mismatch")
    raw = load_small_json(plan / "diagnostic.json", max_bytes=MAX_DIAGNOSTIC_FILE_BYTES)
    spec = CpuDiagnosticSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "diagnostic plan changed its exact typed representation")
    if spec.implementation_digest != _implementation_digest():
        raise ValueError("diagnostic implementation source changed")
    return spec


def load_cpu_diagnostic_context(
    plan: Path, *, campaign_bundle: Path, campaign_output: Path,
) -> tuple[CpuDiagnosticSpec, dict[str, CpuSearchSpec], dict[str, Any]]:
    """Validate the plan and return source context after exactly one replay."""
    _disjoint(plan, campaign_bundle, campaign_output)
    spec = load_cpu_diagnostic_plan(plan)
    source = verify_cpu_diagnostic_source(campaign_bundle, campaign_output)
    expected = _spec_from_source(campaign_bundle, campaign_output, diagnostic_id=spec.diagnostic_id,
                                 verified_source=source)
    _same(spec.to_dict(), expected.to_dict(), "diagnostic plan differs from the complete verified source schedule")
    return spec, source[1], source[2]


def validate_cpu_diagnostic_plan(
    plan: Path, *, campaign_bundle: Path, campaign_output: Path,
) -> CpuDiagnosticSpec:
    """Rebuild every planned reference from verified retained evidence in memory."""
    return load_cpu_diagnostic_context(plan, campaign_bundle=campaign_bundle, campaign_output=campaign_output)[0]
