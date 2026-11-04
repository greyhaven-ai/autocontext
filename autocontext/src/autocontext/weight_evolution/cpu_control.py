"""Versioned CPU calibration campaigns using the existing bounded search worker.

Control provenance belongs to the coordinator. Derived search bundles contain
only the ordinary search contract and checkpoint, never the teacher or family
metadata. Original campaign and diagnostic source closures remain unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.cpu_campaign import METERS, _bundle_bytes, _completed_row, _derived_bundle, _inventory, _pin
from autocontext.weight_evolution.cpu_control_models import (
    CpuControlCampaignLock,
    CpuControlCampaignSpec,
    CpuControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_control_report import build_cpu_control_report
from autocontext.weight_evolution.cpu_control_tasks import validate_control_task_provenance
from autocontext.weight_evolution.cpu_diagnostic import _disjoint, _entries, diagnostic_implementation_digest
from autocontext.weight_evolution.cpu_runner import runtime_identity
from autocontext.weight_evolution.cpu_search import (
    CpuSearchBlockedError,
    _existing_lock,
    load_cpu_search,
    run_cpu_search,
    validate_cpu_search,
    verify_cpu_search,
)
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.session import _STAGING_NAME, _clear_staging, _immutable_json, _read_record, _same


class CpuControlError(ValueError):
    """Control inputs, provenance or retained campaign evidence are invalid."""


def control_implementation_digest() -> str:
    package = Path(__file__).parent
    names = ("cpu_control.py", "cpu_control_models.py", "cpu_control_tasks.py",
             "cpu_control_report.py", "cpu_control_diagnostic.py")
    return canonical_digest({"control": {name: file_identity(package / name) for name in names},
                             "reference_and_search": diagnostic_implementation_digest()})


def _load(directory: Path) -> tuple[CpuControlCampaignSpec, dict[str, CpuSearchSpec], dict[str, CpuControlTaskProvenance]]:
    _disjoint(directory)
    if _entries(directory, 3) != {"campaign.json", "bundle.lock.json", "tasks"}:
        raise CpuControlError("control campaign contains missing or unowned assets")
    raw_lock = load_small_json(directory / "bundle.lock.json")
    lock = CpuControlCampaignLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "control campaign lock changed its typed representation")
    _pin(directory / "campaign.json", lock.spec_sha256, lock.spec_size_bytes)
    raw = load_small_json(directory / "campaign.json")
    spec = CpuControlCampaignSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "control campaign changed its typed representation")
    if spec.implementation_digest != control_implementation_digest():
        raise CpuControlError("control campaign implementation source changed")
    if _entries(directory / "tasks", len(spec.tasks)) != {task.task_id for task in spec.tasks}:
        raise CpuControlError("control task inventory differs from the complete pinned roster")
    templates, provenance = {}, {}
    capacities = []
    identities: set[str] = set()
    for task in spec.tasks:
        bundle = directory / "tasks" / task.task_id
        if _entries(bundle, 4) != {"study.json", "bundle.lock.json", "base.safetensors", "task.provenance.json"}:
            raise CpuControlError("control task contains missing or unowned assets")
        _pin(bundle / "bundle.lock.json", task.bundle_lock_sha256, task.bundle_lock_size_bytes)
        _pin(bundle / "task.provenance.json", task.provenance_sha256, task.provenance_size_bytes)
        validate_cpu_search(bundle)
        template = load_cpu_search(bundle)
        if template.policy.name != "adaptive-v1" or template.evaluation_seed != 0:
            raise CpuControlError("control task templates require seed-zero adaptive policy")
        generated = validate_control_task_provenance(bundle, template)
        if (generated.task_id, generated.generation_seed, generated.family) != (task.task_id, task.generation_seed, task.family):
            raise CpuControlError("control task provenance differs from its campaign descriptor")
        if generated.semantic_digest in identities:
            raise CpuControlError("control campaign repeats a semantic task instance")
        identities.add(generated.semantic_digest)
        templates[task.task_id], provenance[task.task_id] = template, generated
        capacities.append({"rounds": template.rounds, "budget": template.budget.to_dict(),
                           "limits": template.limits.to_dict(), "gain": template.minimum_confirmation_gain,
                           "case_counts": [sum(c.role == role for c in template.cases)
                                           for role in ("practice", "confirmation", "audit")],
                           "option_counts": [len(template.options_for(m)) for m in ("C", "W", "D")]})
    if any(capacity != capacities[0] for capacity in capacities):
        raise CpuControlError("control families must share search capacity, cohort sizes and confirmation rules")
    required = {meter: sum(t.required_usage[meter] for t in templates.values()) * len(spec.repeats) * len(spec.policies)
                for meter in METERS}
    if any(value > getattr(spec.budget, f"max_{meter}") for meter, value in required.items()):
        raise CpuControlError("control campaign cannot reserve the complete schedule")
    return spec, templates, provenance


def load_cpu_control_campaign(directory: Path) -> CpuControlCampaignSpec:
    return _load(directory)[0]


def _schedule(spec: CpuControlCampaignSpec, templates: dict[str, CpuSearchSpec]) -> list[tuple[dict[str, Any], CpuSearchSpec]]:
    schedule = []
    for task in spec.tasks:
        for repeat in spec.repeats:
            for policy in spec.policies:
                run_id = f"{task.task_id}.{repeat.repeat_id}.{policy}"
                raw = templates[task.task_id].to_dict()
                raw["study_id"] = run_id
                raw["evaluation_seed"] = repeat.inference_seed
                raw["baseline_decoding"]["seed"] = repeat.inference_seed
                raw["policy"] = {"name": policy,
                                 "order_seed": repeat.search_order_seed if policy == "seeded-random-v1" else 0}
                for option in raw["options"]:
                    if option["decoding"] is not None:
                        option["decoding"]["seed"] = repeat.inference_seed
                child = CpuSearchSpec.model_validate_json(json.dumps(raw))
                row = {"run_id": run_id, "task_id": task.task_id, "repeat_id": repeat.repeat_id,
                       "policy": policy, "inference_seed": repeat.inference_seed,
                       "order_seed": raw["policy"]["order_seed"], "study_digest": child.digest,
                       "reserved_capacity": child.required_usage}
                schedule.append((row, child))
    return schedule


def validate_cpu_control_campaign(directory: Path) -> dict[str, Any]:
    spec, templates, _ = _load(directory)
    schedule = _schedule(spec, templates)
    return {"campaign_digest": spec.digest, "task_count": len(spec.tasks), "run_count": len(schedule),
            "families": sorted({task.family for task in spec.tasks}), "policies": list(spec.policies),
            "required_usage": {m: sum(row["reserved_capacity"][m] for row, _ in schedule) for m in METERS},
            "model_execution_performed": False, "gpu_execution_deferred": True, "promotion_eligible": False,
            "scope": "constructed-control-validation-no-search-or-inference"}


def _prepare(root: Path, manifest: dict[str, Any], *, execute: bool, count: int) -> int:
    _entries(root, 24)
    if execute:
        _clear_staging(root)
    entries = _entries(root, 8)
    allowed = {".cpu-control.lock", "manifest.json", "claims", "results", "bundles", "searches", "report.json"}
    if entries - allowed or ("manifest.json" not in entries and entries - {".cpu-control.lock"}):
        raise CpuControlError("control output contains unowned evidence")
    if "manifest.json" in entries:
        _same(_read_record(root / "manifest.json"), manifest, "control campaign manifest changed")
    elif execute:
        _immutable_json(root / "manifest.json", manifest)
    else:
        raise CpuControlError("control campaign manifest missing")
    for name in ("claims", "results", "bundles", "searches"):
        path = root / name
        if execute:
            path.mkdir(exist_ok=True)
        if not path.is_dir():
            raise CpuControlError("control journal directory missing")
        _entries(path, count + 16)
        if execute:
            _clear_staging(path)
    claims, results = _entries(root / "claims", count), _entries(root / "results", count)
    if (claims != {f"{i:08d}.json" for i in range(len(claims))} or not results <= claims
            or results != {f"{i:08d}.json" for i in range(len(results))} or len(claims) - len(results) > 1):
        raise CpuControlError("control journal is not a bounded contiguous prefix")
    if "report.json" in entries and (len(claims) != count or results != claims):
        raise CpuControlError("completed control report is missing retained evidence")
    return len(claims)


def _missing(row: dict[str, Any], status: str) -> dict[str, Any]:
    result = {**row, "status": status, "charged_usage": None, "confirmation": None,
              "confirmation_scores": None, "audit_scores": None}
    if status == "failed":
        result["error_type"] = "CpuSearchBlockedError"
    return result


def _run(directory: Path, *, output_dir: Path, execute: bool) -> dict[str, Any]:
    _disjoint(directory, output_dir)
    root = output_dir.resolve()
    spec, templates, _ = _load(directory)
    schedule = _schedule(spec, templates)
    runtime = runtime_identity()
    runtime_digest = canonical_digest(runtime)
    manifest = {"schema_version": 1, "campaign": spec.to_dict(), "campaign_digest": spec.digest,
                "runtime": runtime, "runtime_digest": runtime_digest, "schedule": [row for row, _ in schedule]}
    if root.exists():
        unknown = _entries(root, 24) - {".cpu-control.lock", "manifest.json", "claims", "results", "bundles", "searches",
                                       "report.json"}
        if any(not _STAGING_NAME.fullmatch(name) for name in unknown):
            raise CpuControlError("control output contains unowned evidence")
        if (root / "manifest.json").exists():
            _same(_read_record(root / "manifest.json"), manifest, "control campaign manifest changed")
    if execute:
        root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cpu-control.lock"
    if lock.exists():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock) if execute else _existing_lock(lock):
        claimed = _prepare(root, manifest, execute=execute, count=len(schedule))
        allowed = {row["run_id"] for row, _ in schedule[:claimed]}
        for folder in ("bundles", "searches"):
            if _entries(root / folder, len(schedule)) - allowed:
                raise CpuControlError("control campaign retains an unclaimed child")
        previous = "0" * 64
        rows = []
        pending = []
        for index, (row, child) in enumerate(schedule):
            claim = {"schema_version": 1, "campaign_digest": spec.digest, "runtime_digest": runtime_digest,
                     "index": index, "run": row, "previous_result_digest": previous}
            name = f"{index:08d}.json"
            bundle, child_root = root / "bundles" / row["run_id"], root / "searches" / row["run_id"]
            if index < claimed:
                _same(_read_record(root / "claims" / name), claim, "control claim differs from its pinned schedule")
                if (root / "results" / name).exists():
                    _entries(bundle, 3)
                    _derived_bundle(bundle, _bundle_bytes(directory, row["task_id"], child), write=False)
                    result = _read_record(root / "results" / name)
                    evidence = canonical_digest(_inventory(child_root))
                    if result.get("claim_digest") != canonical_digest(claim) or result.get("evidence_digest") != evidence:
                        raise CpuControlError("control result binding or retained child evidence changed")
                    if result.get("status") == "completed":
                        actual = _completed_row(row, verify_cpu_search(bundle, output_dir=child_root), child_root)
                    elif result.get("status") == "failed":
                        actual = _missing(row, "failed")
                    else:
                        raise CpuControlError("invalid control result status")
                    _same(result, {"claim_digest": canonical_digest(claim), "status": actual["status"],
                                   "evidence_digest": evidence, "row": actual}, "control result differs from verified evidence")
                    rows.append(actual)
                    previous = canonical_digest(result)
                    continue
            rows.append(_missing(row, "pending" if index < claimed else "not-started"))
            pending.append((index, row, child))
        if execute:
            for index, row, child in pending:
                claim = {"schema_version": 1, "campaign_digest": spec.digest, "runtime_digest": runtime_digest,
                         "index": index, "run": row, "previous_result_digest": previous}
                name = f"{index:08d}.json"
                _immutable_json(root / "claims" / name, claim)
                bundle, child_root = root / "bundles" / row["run_id"], root / "searches" / row["run_id"]
                if bundle.exists():
                    _entries(bundle, 19)
                _derived_bundle(bundle, _bundle_bytes(directory, row["task_id"], child), write=True)
                try:
                    actual = _completed_row(row, run_cpu_search(bundle, output_dir=child_root), child_root)
                except CpuSearchBlockedError:
                    actual = _missing(row, "failed")
                result = {"claim_digest": canonical_digest(claim), "status": actual["status"],
                          "evidence_digest": canonical_digest(_inventory(child_root)), "row": actual}
                _immutable_json(root / "results" / name, result)
                rows[index] = actual
                previous = canonical_digest(result)
        report = build_cpu_control_report(spec, rows, runtime_digest=runtime_digest)
        report["ledger_digest"] = previous
        if (root / "report.json").exists():
            _same(_read_record(root / "report.json"), report, "control report differs from verified evidence")
        elif execute:
            _immutable_json(root / "report.json", report)
        return report


def run_cpu_control_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    return _run(directory, output_dir=output_dir, execute=True)


def report_cpu_control_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    return _run(directory, output_dir=output_dir, execute=False)
