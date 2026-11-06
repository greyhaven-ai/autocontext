"""Preserve M19 task bytes, then vary only declared W order and trial capacity."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, regular_file
from autocontext.weight_evolution.cpu_campaign import METERS, _pin
from autocontext.weight_evolution.cpu_campaign_models import CAMPAIGN_POLICIES, CpuCampaignBudget
from autocontext.weight_evolution.cpu_diagnostic import _disjoint, _entries
from autocontext.weight_evolution.cpu_landscape_control_models import LandscapeControlFamily
from autocontext.weight_evolution.cpu_landscape_control_tasks import (
    landscape_control_implementation_digest,
    landscape_control_task_payloads,
)
from autocontext.weight_evolution.cpu_robustness_control_models import (
    MAX_ROBUSTNESS_CONTROL_BYTES,
    ROBUSTNESS_CONTROL_REPEATS,
    ROBUSTNESS_CONTROL_ROSTER,
    CpuRobustnessControlCampaignLock,
    CpuRobustnessControlCampaignSpec,
    CpuRobustnessControlCampaignTask,
    CpuRobustnessControlTaskProvenance,
    RobustnessOrder,
    RobustnessPanel,
    RobustnessRounds,
)
from autocontext.weight_evolution.cpu_robustness_control_oracle import (
    expected_robustness_control_run,
    offline_robustness_oracle,
)
from autocontext.weight_evolution.cpu_search import load_cpu_search, search_implementation_digest, validate_cpu_search
from autocontext.weight_evolution.cpu_search_models import CpuSearchBundleLock, CpuSearchSpec
from autocontext.weight_evolution.session import _same


def _encode(value: Any, *, compact: bool = False) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=None if compact else 2,
                       separators=(",", ":") if compact else None, allow_nan=False) + "\n").encode()


def _identity(data: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def robustness_control_implementation_digest() -> str:
    package = Path(__file__).parent
    return canonical_digest({"implementation": "generated-cpu-search-robustness-v1", "sources": {
        name: file_identity(package / name) for name in (
            "cpu_robustness_control_models.py", "cpu_robustness_control_tasks.py", "cpu_robustness_control_oracle.py")},
        "preserved_m19_implementation": landscape_control_implementation_digest(),
        "search_implementation": search_implementation_digest()})


def robustness_control_task_payloads(
    *, task_id: str, base_task_id: str, generation_seed: int, family: LandscapeControlFamily,
    instance_index: int, order_id: RobustnessOrder, rounds: int, panel: RobustnessPanel,
) -> tuple[CpuSearchSpec, CpuRobustnessControlTaskProvenance, dict[str, bytes]]:
    """Generate the canonical source first; never recompute edits after reordering."""
    identity = (task_id, base_task_id, family, instance_index, generation_seed, order_id, rounds, panel)
    if (type(instance_index) is not int or type(generation_seed) is not int or type(rounds) is not int
            or identity not in ROBUSTNESS_CONTROL_ROSTER):
        raise ValueError("robustness generator requires a declared variant and exact integer inputs")
    original, source, source_payloads = landscape_control_task_payloads(
        task_id=base_task_id, generation_seed=generation_seed, family=family, instance_index=instance_index)
    original_w = original.options_for("W")
    w = original_w if order_id == "canonical" else tuple(original_w[index] for index in (2, 1, 0, 5, 4, 3))
    # Reorder the immutable option objects only. In particular, the M19 repair
    # margins depend on original ordinal and must never be regenerated here.
    raw = original.to_dict()
    raw["study_id"] = f"robustness-{task_id}"
    raw["options"] = [option.to_dict() for option in (*original.options_for("C"), *w, *original.options_for("D"))]
    raw["rounds"] = rounds
    calls = (3 * rounds + 17) * 8
    raw["budget"] = {"max_proposals": 3 * rounds, "max_materializations": 7, "max_inference_calls": calls,
                     "max_input_tokens": 12 * calls, "max_output_tokens": calls}
    template = CpuSearchSpec.model_validate_json(json.dumps(raw))
    source_lock, source_facts = _identity(source_payloads["bundle.lock.json"]), _identity(source_payloads["task.provenance.json"])
    provenance = CpuRobustnessControlTaskProvenance(
        task_id=task_id, base_task_id=base_task_id, generation_seed=generation_seed, family=family,
        instance_index=instance_index, order_id=order_id, rounds=cast(RobustnessRounds, rounds), panel=panel,
        generator_source_sha256=file_identity(Path(__file__))["sha256"], source=source,
        source_bundle_lock_sha256=source_lock["sha256"], source_bundle_lock_size_bytes=source_lock["size_bytes"],
        source_provenance_sha256=source_facts["sha256"], source_provenance_size_bytes=source_facts["size_bytes"],
        source_option_ids=tuple(option.option_id for option in original.options),
        source_w_order=tuple(option.option_id for option in original_w), w_order=tuple(option.option_id for option in w),
        options_digest=canonical_digest(raw["options"]))
    encoded = _encode(template.to_dict())
    lock = CpuSearchBundleLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
                              base_sha256=original.baseline.output_sha256, base_size_bytes=original.baseline.output_size_bytes)
    return template, provenance, {"study.json": encoded, "base.safetensors": source_payloads["base.safetensors"],
                                 "bundle.lock.json": _encode(lock.to_dict()),
                                 "task.provenance.json": _encode(provenance.to_dict())}


def robustness_control_schedule(
    campaign: CpuRobustnessControlCampaignSpec, templates: dict[str, CpuSearchSpec],
) -> list[tuple[dict[str, Any], CpuSearchSpec]]:
    rows = []
    for task in campaign.tasks:
        for repeat in campaign.repeats:
            for policy in campaign.policies:
                run_id = f"{task.task_id}.{repeat.repeat_id}.{policy}"
                raw = templates[task.task_id].to_dict()
                raw.update(study_id=run_id, policy={"name": policy, "order_seed": 0})
                child = CpuSearchSpec.model_validate_json(json.dumps(raw))
                rows.append(({"run_id": run_id, "task_id": task.task_id, "base_task_id": task.base_task_id,
                              "repeat_id": repeat.repeat_id, "family": task.family, "instance_index": task.instance_index,
                              "order_id": task.order_id, "rounds": task.rounds, "panel": task.panel,
                              "policy": policy, "inference_seed": 0, "order_seed": 0,
                              "study_digest": child.digest, "reserved_capacity": child.required_usage}, child))
    return rows


def build_robustness_oracle(
    templates: dict[str, CpuSearchSpec], provenances: dict[str, CpuRobustnessControlTaskProvenance],
) -> dict[str, Any]:
    rows = []
    for task_id, template in templates.items():
        for policy in CAMPAIGN_POLICIES:
            expected = expected_robustness_control_run(template, provenances[task_id], inference_seed=0,
                                                      policy=policy, order_seed=0)
            if expected["confirmation"]["status"] != "vetoed":
                raise ValueError("robustness neutral anchors failed; task replacement is not permitted")
            rows.append({"run_id": f"{task_id}.repeat-01.{policy}", "task_id": task_id, "repeat_id": "repeat-01", **expected})
    offline = offline_robustness_oracle(templates, provenances)
    if len(rows) != 45 or not offline["invariants"]["all_passed"]:
        raise ValueError("robustness complete finite construction failed; no outcome filtering is permitted")
    return {"schema_version": 1, "kind": "predeclared-robustness-control-oracle-v1",
            "scope": "construction-expectations-not-measured-evidence", "runs": rows, "offline": offline}


def _oracle_bytes(templates: dict[str, CpuSearchSpec], provenances: dict[str, CpuRobustnessControlTaskProvenance]) -> bytes:
    result = _encode(build_robustness_oracle(templates, provenances), compact=True)
    if len(result) > MAX_ROBUSTNESS_CONTROL_BYTES:
        raise ValueError("robustness oracle exceeds its declared byte bound")
    return result


def build_robustness_control_campaign(
    directory: Path, *, campaign_id: str = "cpu-search-robustness-v1",
) -> CpuRobustnessControlCampaignSpec:
    _disjoint(directory)
    if directory.exists():
        raise ValueError("robustness control builder requires a fresh directory")
    payloads_by_task, descriptors = {}, []
    templates, provenances = {}, {}
    usage = dict.fromkeys(METERS, 0)
    for task_id, base_task_id, family, instance_index, seed, order_id, rounds, panel in ROBUSTNESS_CONTROL_ROSTER:
        template, facts, payloads = robustness_control_task_payloads(task_id=task_id, base_task_id=base_task_id,
            generation_seed=seed, family=family, instance_index=instance_index, order_id=order_id, rounds=rounds, panel=panel)
        templates[task_id], provenances[task_id] = template, facts
        payloads_by_task[task_id] = payloads
        for meter, value in template.required_usage.items():
            usage[meter] += value * len(ROBUSTNESS_CONTROL_REPEATS) * 3
        task_lock, provenance = _identity(payloads["bundle.lock.json"]), _identity(payloads["task.provenance.json"])
        descriptors.append(CpuRobustnessControlCampaignTask(task_id=task_id, base_task_id=base_task_id,
            generation_seed=seed, family=family, instance_index=instance_index, order_id=order_id, rounds=rounds, panel=panel,
            bundle_lock_sha256=task_lock["sha256"], bundle_lock_size_bytes=task_lock["size_bytes"],
            provenance_sha256=provenance["sha256"], provenance_size_bytes=provenance["size_bytes"]))
    oracle = _oracle_bytes(templates, provenances)
    spec = CpuRobustnessControlCampaignSpec(campaign_id=campaign_id, tasks=tuple(descriptors),
        implementation_digest=robustness_control_implementation_digest(),
        oracle_sha256=hashlib.sha256(oracle).hexdigest(), oracle_size_bytes=len(oracle),
        budget=CpuCampaignBudget(max_runs=45, **{f"max_{meter}": value for meter, value in usage.items()}))
    encoded = _encode(spec.to_dict())
    lock = CpuRobustnessControlCampaignLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded))
    directory.mkdir(parents=True)
    for task_id, payloads in payloads_by_task.items():
        task_directory = directory / "tasks" / task_id
        task_directory.mkdir(parents=True)
        for name, payload in payloads.items():
            (task_directory / name).write_bytes(payload)
    (directory / "campaign.json").write_bytes(encoded)
    (directory / "bundle.lock.json").write_bytes(_encode(lock.to_dict()))
    (directory / "oracle.json").write_bytes(oracle)
    return spec


def _read(path: Path) -> bytes:
    with regular_file(path) as stream:
        data = stream.read(MAX_ROBUSTNESS_CONTROL_BYTES + 1)
    if len(data) > MAX_ROBUSTNESS_CONTROL_BYTES:
        raise ValueError("robustness control asset exceeds its byte cap")
    return data


def load_robustness_control_campaign(
    directory: Path,
) -> tuple[CpuRobustnessControlCampaignSpec, dict[str, CpuSearchSpec], dict[str, CpuRobustnessControlTaskProvenance]]:
    """Regenerate all semantic assets and expectations, including rehashed drift."""
    _disjoint(directory)
    if _entries(directory, 4) != {"campaign.json", "bundle.lock.json", "oracle.json", "tasks"}:
        raise ValueError("robustness control campaign inventory changed")
    raw_lock = json.loads(_read(directory / "bundle.lock.json"))
    lock = CpuRobustnessControlCampaignLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "robustness control lock representation changed")
    _pin(directory / "campaign.json", lock.spec_sha256, lock.spec_size_bytes)
    raw = json.loads(_read(directory / "campaign.json"))
    spec = CpuRobustnessControlCampaignSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "robustness control campaign representation changed")
    if spec.implementation_digest != robustness_control_implementation_digest():
        raise ValueError("robustness control implementation changed")
    _pin(directory / "oracle.json", spec.oracle_sha256, spec.oracle_size_bytes)
    if _entries(directory / "tasks", 15) != {task.task_id for task in spec.tasks}:
        raise ValueError("robustness control task inventory changed")
    templates, provenance = {}, {}
    for task in spec.tasks:
        bundle = directory / "tasks" / task.task_id
        expected, facts, payloads = robustness_control_task_payloads(task_id=task.task_id, base_task_id=task.base_task_id,
            generation_seed=task.generation_seed, family=task.family, instance_index=task.instance_index,
            order_id=task.order_id, rounds=task.rounds, panel=task.panel)
        if _entries(bundle, 4) != set(payloads):
            raise ValueError("robustness control task inventory changed")
        _pin(bundle / "bundle.lock.json", task.bundle_lock_sha256, task.bundle_lock_size_bytes)
        _pin(bundle / "task.provenance.json", task.provenance_sha256, task.provenance_size_bytes)
        for name, value in payloads.items():
            if _read(bundle / name) != value:
                raise ValueError(f"robustness control task differs from preserved deterministic generation: {name}")
        validate_cpu_search(bundle)
        if load_cpu_search(bundle) != expected:
            raise ValueError("robustness control task differs from validated search")
        templates[task.task_id], provenance[task.task_id] = expected, facts
    required = {meter: sum(t.required_usage[meter] for t in templates.values()) * 3 for meter in METERS}
    if spec.budget != CpuCampaignBudget(max_runs=45, **{f"max_{meter}": value for meter, value in required.items()}):
        raise ValueError("robustness control full capacity changed")
    if _read(directory / "oracle.json") != _oracle_bytes(templates, provenance):
        raise ValueError("robustness control oracle differs from its complete finite construction")
    return spec, templates, provenance
