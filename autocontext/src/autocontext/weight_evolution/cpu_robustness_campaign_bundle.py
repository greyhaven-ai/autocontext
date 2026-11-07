"""Pin and verify the separate 45-search generated robustness-control campaign."""

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
from autocontext.weight_evolution.cpu_robustness_campaign_models import (
    ROBUSTNESS_CAMPAIGN_PROFILE,
    ROBUSTNESS_CAMPAIGN_REFERENCE_COUNT,
    ROBUSTNESS_CAMPAIGN_SEARCH_COUNT,
    CpuRobustnessCampaignLock,
    CpuRobustnessCampaignSpec,
)
from autocontext.weight_evolution.cpu_robustness_control_models import (
    ROBUSTNESS_LEGAL_ORDER_COUNT,
    ROBUSTNESS_OFFLINE_TRAJECTORY_COUNT,
    CpuRobustnessControlCampaignSpec,
    CpuRobustnessControlCampaignTask,
)
from autocontext.weight_evolution.cpu_robustness_control_tasks import (
    build_robustness_control_campaign,
    load_robustness_control_campaign,
    robustness_control_schedule,
)
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.session import _same


def _encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def robustness_campaign_implementation_digest() -> str:
    package = Path(__file__).parent
    return canonical_digest({"implementation": "measured-cpu-robustness-campaign-v1", "sources": {
        path.name: file_identity(path, max_bytes=1024 * 1024)
        for path in sorted(package.glob("cpu_robustness_campaign*.py"))}})


def _roster(control: CpuRobustnessControlCampaignSpec) -> dict[str, Any]:
    return {"tasks": [task.to_dict() for task in control.tasks],
            "repeats": [repeat.to_dict() for repeat in control.repeats], "policies": list(control.policies)}


def robustness_campaign_schedule(control: CpuRobustnessControlCampaignSpec,
                                 templates: dict[str, CpuSearchSpec]) -> list[dict[str, Any]]:
    tasks = {task.task_id: task for task in control.tasks}
    rows = [{**row, "profile": ROBUSTNESS_CAMPAIGN_PROFILE, "stage": "search",
             **_task_metadata(tasks[row["task_id"]])}
            for row, _ in robustness_control_schedule(control, templates)]
    for task in control.tasks:
        for repeat in control.repeats:
            rows.append({"profile": ROBUSTNESS_CAMPAIGN_PROFILE, "stage": "reference", **_task_metadata(task),
                         "task_id": task.task_id, "repeat_id": repeat.repeat_id,
                         "run_id": f"{task.task_id}.{repeat.repeat_id}", "inference_seed": repeat.inference_seed,
                         "reserved_capacity": _reference_usage(templates[task.task_id])})
    if len(rows) != ROBUSTNESS_CAMPAIGN_SEARCH_COUNT + ROBUSTNESS_CAMPAIGN_REFERENCE_COUNT:
        raise ValueError("robustness campaign requires its fixed forty-five-search/fifteen-reference roster")
    return rows


def _task_metadata(task: CpuRobustnessControlCampaignTask) -> dict[str, Any]:
    return {key: getattr(task, key) for key in (
        "base_task_id", "family", "instance_index", "order_id", "rounds", "panel")}


def _budgets(control: CpuRobustnessControlCampaignSpec,
             templates: dict[str, CpuSearchSpec]) -> tuple[CpuCampaignBudget, CpuDiagnosticBudget]:
    schedule = robustness_campaign_schedule(control, templates)
    search = CpuCampaignBudget(max_runs=ROBUSTNESS_CAMPAIGN_SEARCH_COUNT,
        **{f"max_{meter}": sum(row["reserved_capacity"][meter] for row in schedule if row["stage"] == "search")
           for meter in METERS})
    reference = CpuDiagnosticBudget(max_references=ROBUSTNESS_CAMPAIGN_REFERENCE_COUNT, max_proposals=0,
        **{f"max_{meter}": sum(row["reserved_capacity"][meter] for row in schedule if row["stage"] == "reference")
           for meter in METERS if meter != "proposals"})
    return search, reference


def build_cpu_robustness_campaign_bundle(
    directory: Path, *, campaign_id: str = "cpu-search-robustness-v1",
) -> CpuRobustnessCampaignSpec:
    """Freeze tasks, direct-calculation expectations, runtime and full capacity before dispatch."""
    _disjoint(directory)
    directory = directory.resolve()
    if directory.exists():
        raise ValueError("measured robustness campaign requires a fresh bundle directory")
    control = build_robustness_control_campaign(directory / "campaign", campaign_id=campaign_id)
    _, templates, _ = load_robustness_control_campaign(directory / "campaign")
    runtime = execution_runtime_identity()
    search_budget, reference_budget = _budgets(control, templates)
    identity = file_identity(directory / "campaign" / "bundle.lock.json", max_bytes=4096)
    spec = CpuRobustnessCampaignSpec(campaign_id=campaign_id, control_campaign_digest=control.digest,
        control_lock_sha256=identity["sha256"], control_lock_size_bytes=identity["size_bytes"],
        roster_digest=canonical_digest(_roster(control)),
        schedule_digest=canonical_digest(robustness_campaign_schedule(control, templates)),
        implementation_digest=robustness_campaign_implementation_digest(),
        execution_implementation_digest=runtime["implementation_digest"],
        native_runtime_digest=runtime["native_runtime_digest"], measured_profile_digest=runtime["measured_profile_digest"],
        search_budget=search_budget, reference_budget=reference_budget)
    encoded, runtime_bytes = _encode(spec.to_dict()), _encode(runtime)
    lock = CpuRobustnessCampaignLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
        runtime_sha256=hashlib.sha256(runtime_bytes).hexdigest(), runtime_size_bytes=len(runtime_bytes))
    (directory / "robustness-campaign.json").write_bytes(encoded)
    (directory / "runtime.json").write_bytes(runtime_bytes)
    (directory / "bundle.lock.json").write_bytes(_encode(lock.to_dict()))
    return spec


def load_cpu_robustness_campaign_bundle(directory: Path,
) -> tuple[CpuRobustnessCampaignSpec, CpuRobustnessControlCampaignSpec, dict[str, CpuSearchSpec], dict[str, Any]]:
    """Regenerate the fixed task contract and reject consistently rehashed drift."""
    _disjoint(directory)
    if _entries(directory, 4) != {"robustness-campaign.json", "runtime.json", "bundle.lock.json", "campaign"}:
        raise ValueError("measured robustness campaign bundle inventory changed")
    raw_lock = load_small_json(directory / "bundle.lock.json", max_bytes=4096)
    lock = CpuRobustnessCampaignLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "measured robustness campaign lock representation changed")
    _pin(directory / "robustness-campaign.json", lock.spec_sha256, lock.spec_size_bytes)
    _pin(directory / "runtime.json", lock.runtime_sha256, lock.runtime_size_bytes)
    raw = load_small_json(directory / "robustness-campaign.json", max_bytes=128 * 1024)
    spec = CpuRobustnessCampaignSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "measured robustness campaign contract representation changed")
    runtime = load_small_json(directory / "runtime.json", max_bytes=256 * 1024)
    _same(runtime, execution_runtime_identity(), "measured robustness campaign runtime or source inventory changed")
    if (spec.implementation_digest != robustness_campaign_implementation_digest()
            or spec.execution_implementation_digest != runtime["implementation_digest"]
            or any(getattr(spec, field) != runtime[field] for field in ("native_runtime_digest", "measured_profile_digest"))):
        raise ValueError("measured robustness campaign implementation or execution pin changed")
    _pin(directory / "campaign" / "bundle.lock.json", spec.control_lock_sha256, spec.control_lock_size_bytes)
    # This loader regenerates every task and oracle artifact from the separate
    # fixed M20 contract. Earlier milestone identities are never admitted or relabelled.
    control, templates, _ = load_robustness_control_campaign(directory / "campaign")
    if (control.campaign_id != spec.campaign_id or control.digest != spec.control_campaign_digest
            or canonical_digest(_roster(control)) != spec.roster_digest
            or canonical_digest(robustness_campaign_schedule(control, templates)) != spec.schedule_digest
            or _budgets(control, templates) != (spec.search_budget, spec.reference_budget)
            or control.budget != spec.search_budget):
        raise ValueError("measured robustness campaign changed its fixed tasks, schedule or full capacity")
    return spec, control, templates, runtime


def validate_cpu_robustness_campaign_bundle(directory: Path) -> dict[str, Any]:
    spec, control, templates, _ = load_cpu_robustness_campaign_bundle(directory)
    return {"campaign_digest": spec.digest, "profile": ROBUSTNESS_CAMPAIGN_PROFILE, "task_count": len(control.tasks),
        "base_task_count": len({task.base_task_id for task in control.tasks}), "variant_count": len(control.tasks),
        "searches": ROBUSTNESS_CAMPAIGN_SEARCH_COUNT, "conditional_references": ROBUSTNESS_CAMPAIGN_REFERENCE_COUNT,
        "schedule": robustness_campaign_schedule(control, templates), "roster_digest": spec.roster_digest,
        "schedule_digest": spec.schedule_digest, "oracle_sha256": control.oracle_sha256,
        "oracle_size_bytes": control.oracle_size_bytes, "oracle_run_count": ROBUSTNESS_CAMPAIGN_SEARCH_COUNT,
        "offline_trajectory_count": ROBUSTNESS_OFFLINE_TRAJECTORY_COUNT,
        "legal_weight_order_count": ROBUSTNESS_LEGAL_ORDER_COUNT,
        "search_budget": spec.search_budget.to_dict(),
        "reference_budget": spec.reference_budget.to_dict(), "model_execution_performed": False,
        "gpu_execution_deferred": True, "promotion_eligible": False,
        "scope": "fixed-robustness-control-validation-no-search-or-worker-inference"}
