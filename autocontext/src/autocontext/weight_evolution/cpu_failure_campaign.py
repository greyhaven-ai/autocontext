"""One-attempt CPU supervision with separately pinned failure observations.

Only the coordinator is new. Native search code, task generation, selection,
confirmation and resource enforcement retain their original implementation pins.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.cpu_campaign import METERS, _bundle_bytes, _completed_row, _derived_bundle, _inventory, _pin
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignRepeat
from autocontext.weight_evolution.cpu_control import _load as _load_control
from autocontext.weight_evolution.cpu_control import _missing, _schedule, control_implementation_digest
from autocontext.weight_evolution.cpu_control_report import build_cpu_control_report
from autocontext.weight_evolution.cpu_control_tasks import build_cpu_control_campaign
from autocontext.weight_evolution.cpu_diagnostic import _disjoint, _entries
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticBudget
from autocontext.weight_evolution.cpu_failure_campaign_models import CpuFailureCampaignLock, CpuFailureCampaignSpec
from autocontext.weight_evolution.cpu_failure_models import CpuFailureContract, CpuFailureRecord
from autocontext.weight_evolution.cpu_failure_prefix import inspect_cpu_search_prefix
from autocontext.weight_evolution.cpu_failure_record import build_cpu_failure_record, validate_cpu_failure_record
from autocontext.weight_evolution.cpu_runner import runtime_identity
from autocontext.weight_evolution.cpu_search import _existing_lock, run_cpu_search
from autocontext.weight_evolution.session import _STAGING_NAME, _clear_staging, _immutable_json, _read_record, _same


def failure_implementation_digest() -> str:
    package = Path(__file__).parent
    names = ("cpu_failure_models.py", "cpu_failure_record.py", "cpu_failure_prefix.py",
             "cpu_failure_campaign_models.py", "cpu_failure_campaign.py", "cpu_failure_diagnostic.py")
    return canonical_digest({"supervisor": {name: file_identity(package / name) for name in names},
                             "native_controls": control_implementation_digest()})


def _encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _reference_budget(control: Any, templates: dict[str, Any]) -> CpuDiagnosticBudget:
    total = {meter: 0 for meter in METERS}
    for task in control.tasks:
        child = templates[task.task_id]
        cases = [case for case in child.cases if case.role == "practice"]
        calls = (1 + len(child.options)) * len(cases)
        usage = {"proposals": 0, "materializations": len(child.candidates), "inference_calls": calls,
                 "output_tokens": calls,
                 "input_tokens": (1 + len(child.options)) * sum(len(c.prompt.split()) for c in cases)
                 + len(cases) * sum(len((o.context or "").split()) for o in child.options)}
        for meter in total:
            total[meter] += usage[meter] * len(control.repeats)
    return CpuDiagnosticBudget(max_references=len(control.tasks) * len(control.repeats), max_proposals=0,
                               **{f"max_{meter}": value for meter, value in total.items() if meter != "proposals"})


def build_cpu_failure_campaign(
    directory: Path, *, preset: str = "fixture", campaign_id: str = "cpu-failure-pilot-v1",
    instances_per_family: int | None = None, repeats: tuple[CpuCampaignRepeat, ...] | None = None,
    cases_per_role: int = 8,
) -> CpuFailureCampaignSpec:
    _disjoint(directory)
    if directory.exists():
        raise ValueError("observed campaign requires a fresh bundle directory")
    control = build_cpu_control_campaign(directory / "campaign", preset=preset, campaign_id=campaign_id,
        instances_per_family=instances_per_family, repeats=repeats, cases_per_role=cases_per_role)
    _, templates, _ = _load_control(directory / "campaign")
    runtime = runtime_identity()
    identity = file_identity(directory / "campaign" / "bundle.lock.json")
    spec = CpuFailureCampaignSpec(campaign_id=campaign_id, control_campaign_digest=control.digest,
        control_lock_sha256=identity["sha256"], control_lock_size_bytes=identity["size_bytes"],
        runtime_digest=canonical_digest(runtime), implementation_digest=failure_implementation_digest(),
        search_budget=control.budget, reference_budget=_reference_budget(control, templates))
    encoded, encoded_runtime = _encode(spec.to_dict()), _encode(runtime)
    lock = CpuFailureCampaignLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
        runtime_sha256=hashlib.sha256(encoded_runtime).hexdigest(), runtime_size_bytes=len(encoded_runtime))
    (directory / "supervisor.json").write_bytes(encoded)
    (directory / "runtime.json").write_bytes(encoded_runtime)
    (directory / "bundle.lock.json").write_bytes(_encode(lock.to_dict()))
    return spec


def _load(directory: Path) -> tuple[CpuFailureCampaignSpec, Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    _disjoint(directory)
    if _entries(directory, 4) != {"supervisor.json", "runtime.json", "bundle.lock.json", "campaign"}:
        raise ValueError("observed campaign bundle inventory changed")
    raw_lock = load_small_json(directory / "bundle.lock.json")
    lock = CpuFailureCampaignLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "observed campaign lock representation changed")
    _pin(directory / "supervisor.json", lock.spec_sha256, lock.spec_size_bytes)
    _pin(directory / "runtime.json", lock.runtime_sha256, lock.runtime_size_bytes)
    raw = load_small_json(directory / "supervisor.json")
    spec = CpuFailureCampaignSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "observed campaign typed representation changed")
    if spec.implementation_digest != failure_implementation_digest():
        raise ValueError("observed campaign implementation changed")
    runtime = load_small_json(directory / "runtime.json")
    if canonical_digest(runtime) != spec.runtime_digest:
        raise ValueError("observed campaign runtime pin changed")
    _pin(directory / "campaign" / "bundle.lock.json", spec.control_lock_sha256, spec.control_lock_size_bytes)
    control, templates, provenance = _load_control(directory / "campaign")
    if (control.digest != spec.control_campaign_digest or control.campaign_id != spec.campaign_id
            or control.budget != spec.search_budget or _reference_budget(control, templates) != spec.reference_budget):
        raise ValueError("observed campaign design or complete capacity changed")
    return spec, control, templates, provenance, runtime


def validate_cpu_failure_campaign(directory: Path) -> dict[str, Any]:
    spec, control, _, _, _ = _load(directory)
    return {"supervisor_digest": spec.digest, "campaign_digest": control.digest,
            "runtime_matches": canonical_digest(runtime_identity()) == spec.runtime_digest,
            "task_count": len(control.tasks), "search_count": spec.search_budget.max_runs,
            "search_budget": spec.search_budget.to_dict(), "conditional_reference_budget": spec.reference_budget.to_dict(),
            "model_execution_performed": False, "gpu_execution_deferred": True, "promotion_eligible": False}


def _preflight(root: Path, manifest: dict[str, Any], *, lock_name: str, children: tuple[str, ...]) -> None:
    allowed = {lock_name, "manifest.json", "claims", "results", "failures", "report.json", *children}
    if root.exists():
        unknown = _entries(root, 32) - allowed
        if any(not _STAGING_NAME.fullmatch(name) for name in unknown):
            raise ValueError("observed output contains unowned evidence")
        if not (root / "manifest.json").exists() and _entries(root, 32) - {lock_name} - unknown:
            raise ValueError("observed output has evidence without its manifest")
        if (root / "manifest.json").exists():
            _same(_read_record(root / "manifest.json"), manifest, "observed output manifest changed")


def _prepare(root: Path, manifest: dict[str, Any], *, execute: bool, count: int,
             lock_name: str, children: tuple[str, ...]) -> int:
    _preflight(root, manifest, lock_name=lock_name, children=children)
    if execute:
        _clear_staging(root)
        _immutable_json(root / "manifest.json", manifest)
    elif not (root / "manifest.json").exists():
        raise ValueError("observed output manifest missing")
    for name in ("claims", "results", "failures", *children):
        path = root / name
        if execute:
            path.mkdir(exist_ok=True)
        _entries(path, count + 16)
        if execute:
            _clear_staging(path)
    claims, results = _entries(root / "claims", count), _entries(root / "results", count)
    if (claims != {f"{i:08d}.json" for i in range(len(claims))}
            or results != {f"{i:08d}.json" for i in range(len(results))}
            or not results <= claims or len(claims) - len(results) > 1):
        raise ValueError("observed output is not a bounded contiguous journal")
    if _entries(root / "failures", count) - claims:
        raise ValueError("observed failure has no parent claim")
    if (root / "report.json").exists() and (len(claims) != count or claims != results):
        raise ValueError("completed observed report is missing retained journal evidence")
    return len(claims)


def _observation(path: Path, contract: CpuFailureContract, *, supervisor_digest: str, run_id: str,
                 study_digest: str, runtime_digest: str, prefix: dict[str, Any]) -> CpuFailureRecord | None:
    if not path.exists():
        return None
    raw = _read_record(path)
    record = CpuFailureRecord.model_validate_json(json.dumps(raw))
    _same(raw, record.to_dict(), "observed failure typed representation changed")
    validate_cpu_failure_record(record, contract, supervisor_digest=supervisor_digest, run_id=run_id,
        study_digest=study_digest, runtime_digest=runtime_digest,
        terminal_claim=prefix["terminal_claim"], terminal_result=prefix["terminal_result"])
    return record


def _failure_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"failed_runs": len(items), "recorded_observations": sum(item["record"] is not None for item in items),
            "lost_observations": sum(item["record"] is None for item in items),
            "historical_failure_cause_recovered": False,
            "scope": "observed-exception-text-not-inferred-worker-cause-or-usage"}


def _run(directory: Path, *, output_dir: Path, execute: bool) -> dict[str, Any]:
    _disjoint(directory, output_dir)
    spec, control, templates, _, runtime = _load(directory)
    if canonical_digest(runtime_identity()) != spec.runtime_digest:
        raise ValueError("observed campaign requires its pinned host/runtime")
    schedule = _schedule(control, templates)
    root = output_dir.resolve()
    manifest = {"schema_version": 1, "supervisor": spec.to_dict(), "supervisor_digest": spec.digest,
                "runtime": runtime, "schedule": [row for row, _ in schedule]}
    _preflight(root, manifest, lock_name=".cpu-failure.lock", children=("bundles", "searches"))
    if execute:
        root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cpu-failure.lock"
    if lock.exists():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock) if execute else _existing_lock(lock):
        claimed = _prepare(root, manifest, execute=execute, count=len(schedule),
                           lock_name=lock.name, children=("bundles", "searches"))
        allowed = {row["run_id"] for row, _ in schedule[:claimed]}
        if any(_entries(root / name, len(schedule)) - allowed for name in ("bundles", "searches")):
            raise ValueError("observed campaign contains an unclaimed child")
        previous = "0" * 64
        rows: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        pending: list[tuple[int, dict[str, Any], Any]] = []
        for index, (row, child) in enumerate(schedule):
            name = f"{index:08d}.json"
            claim = {"schema_version": 1, "supervisor_digest": spec.digest, "index": index,
                     "run": row, "previous_result_digest": previous}
            bundle, child_root = root / "bundles" / row["run_id"], root / "searches" / row["run_id"]
            if index < claimed:
                _same(_read_record(root / "claims" / name), claim, "observed parent claim changed")
                if bundle.exists():
                    _entries(bundle, 3)
                    _derived_bundle(bundle, _bundle_bytes(directory / "campaign", row["task_id"], child), write=False)
                    prefix = inspect_cpu_search_prefix(bundle, child_root)
                elif child_root.exists():
                    raise ValueError("observed child evidence has no derived bundle")
                else:
                    prefix = {"status": "absent", "summary": None, "terminal_claim": None, "terminal_result": None}
                record = _observation(root / "failures" / name, spec.failure_contract,
                    supervisor_digest=spec.digest, run_id=row["run_id"], study_digest=child.digest,
                    runtime_digest=spec.runtime_digest, prefix=prefix)
                if (root / "results" / name).exists():
                    result = _read_record(root / "results" / name)
                    if result.get("status") == "completed":
                        if prefix["status"] != "completed" or record is not None:
                            raise ValueError("completed observed child lacks completed native evidence")
                        actual = _completed_row(row, prefix["summary"], child_root)
                    elif result.get("status") == "failed":
                        if prefix["status"] == "completed" and record is None:
                            raise ValueError("completed native child cannot become an unobserved failure")
                        actual = _missing(row, "failed")
                    else:
                        raise ValueError("invalid observed result status")
                    expected = {"claim_digest": canonical_digest(claim), "status": actual["status"],
                                "evidence_digest": canonical_digest(_inventory(child_root)), "row": actual,
                                "failure_digest": record.digest if record is not None else None}
                    _same(result, expected, "observed result, native evidence or failure binding changed")
                    rows.append(actual)
                    if actual["status"] == "failed":
                        failures.append({"run_id": row["run_id"], "record": record.to_dict() if record else None})
                    previous = canonical_digest(result)
                    continue
            rows.append(_missing(row, "pending" if index < claimed else "not-started"))
            pending.append((index, row, child))
        if execute:
            for index, row, child in pending:
                name = f"{index:08d}.json"
                claim = {"schema_version": 1, "supervisor_digest": spec.digest, "index": index,
                         "run": row, "previous_result_digest": previous}
                _immutable_json(root / "claims" / name, claim)
                bundle, child_root = root / "bundles" / row["run_id"], root / "searches" / row["run_id"]
                record = None
                if index < claimed:
                    # A prior parent claim may already have dispatched. Never call the child again.
                    prefix = (inspect_cpu_search_prefix(bundle, child_root) if bundle.exists() else
                              {"status": "absent", "summary": None, "terminal_claim": None, "terminal_result": None})
                    record = _observation(root / "failures" / name, spec.failure_contract,
                        supervisor_digest=spec.digest, run_id=row["run_id"], study_digest=child.digest,
                        runtime_digest=spec.runtime_digest, prefix=prefix)
                    actual = (_completed_row(row, prefix["summary"], child_root)
                              if prefix["status"] == "completed" and record is None else _missing(row, "failed"))
                else:
                    _derived_bundle(bundle, _bundle_bytes(directory / "campaign", row["task_id"], child), write=True)
                    try:
                        summary = run_cpu_search(bundle, output_dir=child_root)
                    except Exception as error:
                        prefix = inspect_cpu_search_prefix(bundle, child_root)
                        record = build_cpu_failure_record(spec.failure_contract, supervisor_digest=spec.digest,
                            run_id=row["run_id"], study_digest=child.digest, runtime_digest=spec.runtime_digest,
                            terminal_claim=prefix["terminal_claim"], terminal_result=prefix["terminal_result"],
                            exception=error)
                        _immutable_json(root / "failures" / name, record.to_dict())
                        actual = _missing(row, "failed")
                    else:
                        actual = _completed_row(row, summary, child_root)
                result = {"claim_digest": canonical_digest(claim), "status": actual["status"],
                          "evidence_digest": canonical_digest(_inventory(child_root)), "row": actual,
                          "failure_digest": record.digest if record is not None else None}
                _immutable_json(root / "results" / name, result)
                rows[index] = actual
                if actual["status"] == "failed":
                    failures.append({"run_id": row["run_id"], "record": record.to_dict() if record else None})
                previous = canonical_digest(result)
        campaign = build_cpu_control_report(control, rows, runtime_digest=spec.runtime_digest)
        campaign["ledger_digest"] = previous
        report = {"schema_version": 1, "report_kind": "observed_cpu_control_campaign", "supervisor_digest": spec.digest,
                  "campaign": campaign, "failures": failures, "failure_coverage": _failure_summary(failures),
                  "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False}
        report["semantic_digest"] = canonical_digest(report)
        report["ledger_digest"] = previous
        if (root / "report.json").exists():
            _same(_read_record(root / "report.json"), report, "observed report changed")
        elif execute:
            _immutable_json(root / "report.json", report)
        return report


def run_cpu_failure_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    return _run(directory, output_dir=output_dir, execute=True)


def report_cpu_failure_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    return _run(directory, output_dir=output_dir, execute=False)
