"""Pin and verify the separate 36-search generated landscape-control campaign."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json
from autocontext.weight_evolution.cpu_campaign import METERS, _pin
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignBudget
from autocontext.weight_evolution.cpu_diagnostic import _disjoint, _entries
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticBudget
from autocontext.weight_evolution.cpu_execution_campaign import _reference_usage
from autocontext.weight_evolution.cpu_execution_outcomes import execution_runtime_identity
from autocontext.weight_evolution.cpu_landscape_campaign_models import (
    LANDSCAPE_CAMPAIGN_PROFILE,
    LANDSCAPE_CAMPAIGN_REFERENCE_COUNT,
    LANDSCAPE_CAMPAIGN_SEARCH_COUNT,
    CpuLandscapeCampaignLock,
    CpuLandscapeCampaignSpec,
)
from autocontext.weight_evolution.cpu_landscape_control_models import CpuLandscapeControlCampaignSpec
from autocontext.weight_evolution.cpu_landscape_control_tasks import (
    build_landscape_control_campaign,
    landscape_control_schedule,
    load_landscape_control_campaign,
)
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.session import _same


def _encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def landscape_campaign_implementation_digest() -> str:
    package = Path(__file__).parent
    return canonical_digest({"implementation": "measured-cpu-landscape-campaign-v1", "sources": {
        path.name: file_identity(path, max_bytes=1024 * 1024)
        for path in sorted(package.glob("cpu_landscape_campaign*.py"))}})


def _roster(control: CpuLandscapeControlCampaignSpec) -> dict[str, Any]:
    return {"tasks": [task.to_dict() for task in control.tasks],
            "repeats": [repeat.to_dict() for repeat in control.repeats], "policies": list(control.policies)}


def landscape_campaign_schedule(control: CpuLandscapeControlCampaignSpec,
                            templates: dict[str, CpuSearchSpec]) -> list[dict[str, Any]]:
    tasks = {task.task_id: task for task in control.tasks}
    rows = [{**row, "profile": LANDSCAPE_CAMPAIGN_PROFILE, "stage": "search",
             "family": tasks[row["task_id"]].family, "instance_index": tasks[row["task_id"]].instance_index}
            for row, _ in landscape_control_schedule(control, templates)]
    for task in control.tasks:
        for repeat in control.repeats:
            rows.append({"profile": LANDSCAPE_CAMPAIGN_PROFILE, "stage": "reference", "family": task.family,
                         "task_id": task.task_id, "instance_index": task.instance_index, "repeat_id": repeat.repeat_id,
                         "run_id": f"{task.task_id}.{repeat.repeat_id}", "inference_seed": repeat.inference_seed,
                         "reserved_capacity": _reference_usage(templates[task.task_id])})
    if len(rows) != LANDSCAPE_CAMPAIGN_SEARCH_COUNT + LANDSCAPE_CAMPAIGN_REFERENCE_COUNT:
        raise ValueError("landscape campaign requires its fixed thirty-six-search/twelve-reference roster")
    return rows


def _budgets(control: CpuLandscapeControlCampaignSpec,
             templates: dict[str, CpuSearchSpec]) -> tuple[CpuCampaignBudget, CpuDiagnosticBudget]:
    schedule = landscape_campaign_schedule(control, templates)
    search = CpuCampaignBudget(max_runs=LANDSCAPE_CAMPAIGN_SEARCH_COUNT,
        **{f"max_{meter}": sum(row["reserved_capacity"][meter] for row in schedule if row["stage"] == "search")
           for meter in METERS})
    reference = CpuDiagnosticBudget(max_references=LANDSCAPE_CAMPAIGN_REFERENCE_COUNT, max_proposals=0,
        **{f"max_{meter}": sum(row["reserved_capacity"][meter] for row in schedule if row["stage"] == "reference")
           for meter in METERS if meter != "proposals"})
    return search, reference


def build_cpu_landscape_campaign_bundle(
    directory: Path, *, campaign_id: str = "cpu-search-landscapes-v1",
) -> CpuLandscapeCampaignSpec:
    """Freeze tasks, direct-calculation expectations, runtime and full capacity before dispatch."""
    _disjoint(directory)
    directory = directory.resolve()
    if directory.exists():
        raise ValueError("measured landscape campaign requires a fresh bundle directory")
    control = build_landscape_control_campaign(directory / "campaign", campaign_id=campaign_id)
    _, templates, _ = load_landscape_control_campaign(directory / "campaign")
    runtime = execution_runtime_identity()
    search_budget, reference_budget = _budgets(control, templates)
    identity = file_identity(directory / "campaign" / "bundle.lock.json", max_bytes=4096)
    spec = CpuLandscapeCampaignSpec(campaign_id=campaign_id, control_campaign_digest=control.digest,
        control_lock_sha256=identity["sha256"], control_lock_size_bytes=identity["size_bytes"],
        roster_digest=canonical_digest(_roster(control)),
        schedule_digest=canonical_digest(landscape_campaign_schedule(control, templates)),
        implementation_digest=landscape_campaign_implementation_digest(),
        execution_implementation_digest=runtime["implementation_digest"],
        native_runtime_digest=runtime["native_runtime_digest"], measured_profile_digest=runtime["measured_profile_digest"],
        search_budget=search_budget, reference_budget=reference_budget)
    encoded, runtime_bytes = _encode(spec.to_dict()), _encode(runtime)
    lock = CpuLandscapeCampaignLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
        runtime_sha256=hashlib.sha256(runtime_bytes).hexdigest(), runtime_size_bytes=len(runtime_bytes))
    (directory / "landscape-campaign.json").write_bytes(encoded)
    (directory / "runtime.json").write_bytes(runtime_bytes)
    (directory / "bundle.lock.json").write_bytes(_encode(lock.to_dict()))
    return spec


def load_cpu_landscape_campaign_bundle(directory: Path,
) -> tuple[CpuLandscapeCampaignSpec, CpuLandscapeControlCampaignSpec, dict[str, CpuSearchSpec], dict[str, Any]]:
    """Regenerate the fixed task contract and reject consistently rehashed drift."""
    _disjoint(directory)
    if _entries(directory, 4) != {"landscape-campaign.json", "runtime.json", "bundle.lock.json", "campaign"}:
        raise ValueError("measured landscape campaign bundle inventory changed")
    raw_lock = load_small_json(directory / "bundle.lock.json", max_bytes=4096)
    lock = CpuLandscapeCampaignLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "measured landscape campaign lock representation changed")
    _pin(directory / "landscape-campaign.json", lock.spec_sha256, lock.spec_size_bytes)
    _pin(directory / "runtime.json", lock.runtime_sha256, lock.runtime_size_bytes)
    raw = load_small_json(directory / "landscape-campaign.json", max_bytes=128 * 1024)
    spec = CpuLandscapeCampaignSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "measured landscape campaign contract representation changed")
    runtime = load_small_json(directory / "runtime.json", max_bytes=256 * 1024)
    _same(runtime, execution_runtime_identity(), "measured landscape campaign runtime or source inventory changed")
    if (spec.implementation_digest != landscape_campaign_implementation_digest()
            or spec.execution_implementation_digest != runtime["implementation_digest"]
            or any(getattr(spec, field) != runtime[field] for field in ("native_runtime_digest", "measured_profile_digest"))):
        raise ValueError("measured landscape campaign implementation or execution pin changed")
    _pin(directory / "campaign" / "bundle.lock.json", spec.control_lock_sha256, spec.control_lock_size_bytes)
    # This loader regenerates every task and oracle artifact from the separate
    # fixed M19 contract. Earlier milestone identities are never admitted or relabelled.
    control, templates, _ = load_landscape_control_campaign(directory / "campaign")
    if (control.campaign_id != spec.campaign_id or control.digest != spec.control_campaign_digest
            or canonical_digest(_roster(control)) != spec.roster_digest
            or canonical_digest(landscape_campaign_schedule(control, templates)) != spec.schedule_digest
            or _budgets(control, templates) != (spec.search_budget, spec.reference_budget)
            or control.budget != spec.search_budget):
        raise ValueError("measured landscape campaign changed its fixed tasks, schedule or full capacity")
    return spec, control, templates, runtime


def validate_cpu_landscape_campaign_bundle(directory: Path) -> dict[str, Any]:
    spec, control, templates, _ = load_cpu_landscape_campaign_bundle(directory)
    return {"campaign_digest": spec.digest, "profile": LANDSCAPE_CAMPAIGN_PROFILE, "task_count": len(control.tasks),
        "searches": LANDSCAPE_CAMPAIGN_SEARCH_COUNT, "conditional_references": LANDSCAPE_CAMPAIGN_REFERENCE_COUNT,
        "schedule": landscape_campaign_schedule(control, templates), "roster_digest": spec.roster_digest,
        "schedule_digest": spec.schedule_digest, "oracle_sha256": control.oracle_sha256,
        "oracle_size_bytes": control.oracle_size_bytes, "oracle_run_count": LANDSCAPE_CAMPAIGN_SEARCH_COUNT,
        "search_budget": spec.search_budget.to_dict(),
        "reference_budget": spec.reference_budget.to_dict(), "model_execution_performed": False,
        "gpu_execution_deferred": True, "promotion_eligible": False,
        "scope": "fixed-landscape-control-validation-no-search-or-worker-inference"}
