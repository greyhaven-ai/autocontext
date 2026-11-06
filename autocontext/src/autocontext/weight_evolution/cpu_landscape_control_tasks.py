"""Deterministic teacher, repair landscapes and exact byte regeneration for M19.

Two declared instances change the balanced four-class teacher, corruption and
margins. Multi-entry weight repairs expose useful continuation, a misleading
plateau and neutral ties. Zero-logit context and top-k-one decoding remain
neutral anchors, with no held-out shift. No inference result selects tasks,
options, seeds or labels.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateSpec
from autocontext.weight_evolution.cpu_campaign import METERS, _pin
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignBudget
from autocontext.weight_evolution.cpu_campaign_tasks import _checkpoint, token_count_ray
from autocontext.weight_evolution.cpu_diagnostic import _disjoint, _entries
from autocontext.weight_evolution.cpu_landscape_control_models import (
    LANDSCAPE_CONTROL_FAMILIES,
    LANDSCAPE_CONTROL_REPEATS,
    LANDSCAPE_CONTROL_ROLES,
    LANDSCAPE_CONTROL_ROSTER,
    MAX_LANDSCAPE_CONTROL_BYTES,
    CpuLandscapeControlCampaignLock,
    CpuLandscapeControlCampaignSpec,
    CpuLandscapeControlCampaignTask,
    CpuLandscapeControlTaskProvenance,
    LandscapeControlFamily,
    landscape_base_pairs,
)
from autocontext.weight_evolution.cpu_landscape_control_oracle import expected_landscape_control_run
from autocontext.weight_evolution.cpu_models import CpuDecoding, CpuModelContract, CpuWorkerLimits
from autocontext.weight_evolution.cpu_search import load_cpu_search, search_implementation_digest, validate_cpu_search
from autocontext.weight_evolution.cpu_search_models import CpuSearchBudget, CpuSearchBundleLock, CpuSearchOption, CpuSearchSpec
from autocontext.weight_evolution.cpu_study import CpuCase
from autocontext.weight_evolution.session import _same
from autocontext.weight_evolution.tensor_edits import (
    CheckpointContract,
    TensorContract,
    TensorEdit,
    TensorEditProgram,
    TensorReplacement,
)

_VOCABULARY = tuple(f"token{i}" for i in range(8))


def _permutation(seed: int, domain: str, count: int) -> tuple[int, ...]:
    # A hash ordering is a complete, bounded construction, with integer ties.
    return tuple(sorted(range(count), key=lambda i: (
        hashlib.sha256(f"sha256-search-landscapes-v1:{seed}:{domain}:{i}".encode()).digest(), i)))


def _encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _identity(data: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def landscape_control_implementation_digest() -> str:
    package = Path(__file__).parent
    return canonical_digest({"implementation": "generated-cpu-search-landscapes-v1", "sources": {
        name: file_identity(package / name) for name in (
            "cpu_landscape_control_models.py", "cpu_landscape_control_tasks.py", "cpu_landscape_control_oracle.py")},
        "search_implementation": search_implementation_digest()})


def _repair_classes(family: LandscapeControlFamily, instance_index: int, lane: int, step: int) -> set[int]:
    """Declared candidate effects, without measuring or filtering any outcomes."""
    corrupted = {2, 3} if instance_index == 1 else {1, 2, 3}
    first = min(corrupted)
    if family == "neutral-v1":
        return set()
    if family == "informative-improvement-v1":
        if lane == 0:
            return {first} if step == 1 else corrupted
        return {max(corrupted)} if lane == 1 else set()
    # A promising first repair leads to a plateau. Exploration's last first-step
    # option repairs everything; following the plateau consumes that last probe.
    if lane == 0:
        return {first}
    return corrupted if lane == 2 else set()


def landscape_control_task_payloads(
    *, task_id: str, generation_seed: int, family: LandscapeControlFamily, instance_index: int = 1,
) -> tuple[CpuSearchSpec, CpuLandscapeControlTaskProvenance, dict[str, bytes]]:
    """Construct declared bytes without inference, task rejection, or seed search.

    Weight edits repair explicit teacher classes. The two instances change the
    teacher, initial corruption and margins. Context and decoding deliberately
    remain neutral anchors, so the unchanged joint gate always vetoes audit.
    """
    if family not in LANDSCAPE_CONTROL_FAMILIES or type(generation_seed) is not int or not 0 <= generation_seed <= 2**32 - 1:
        raise ValueError("invalid landscape control family or generation seed")
    pairs = landscape_base_pairs(instance_index)
    labels = (0, 1, 0, 1) if instance_index == 1 else (1, 0, 1, 0)
    features = _permutation(generation_seed, "features", 8)
    weights = [0.0] * 16
    for feature, pair in zip(features, pairs, strict=True):
        weights[feature * 2:feature * 2 + 2] = pair
    base, tensor_bytes = _checkpoint(tuple(weights))
    contract = CheckpointContract(**_identity(base), max_changed_entries=8, tensors=(
        TensorContract(name="projection.bias", dtype="F32", shape=(2,),
                       sha256=hashlib.sha256(tensor_bytes["projection.bias"]).hexdigest()),
        TensorContract(name="projection.weight", dtype="F32", shape=(8, 2), editable=True,
                       sha256=hashlib.sha256(tensor_bytes["projection.weight"]).hexdigest()),
    ))
    model = CpuModelContract(vocabulary=_VOCABULARY, output_tokens=("zero", "one"))
    candidates = [TensorCandidateSpec(candidate_id="base", contract=contract, output_sha256=contract.sha256,
                                      output_size_bytes=contract.size_bytes)]
    options: list[CpuSearchOption] = []
    for mechanism in ("C", "W", "D"):
        names = _permutation(generation_seed, f"{mechanism}-names", 6)
        # Interleaving is fixed before scores: A1 B1 C1 A2 B2 C2. Each direction
        # still declares contiguous ascending steps, as the shared contract requires.
        for ordinal, (step, lane) in enumerate((step, lane) for step in (1, 2) for lane in range(3)):
            option_id = f"{mechanism.lower()}-{names[ordinal] + 1:02}"
            coords: dict[str, Any] = {"option_id": option_id, "mechanism": mechanism,
                                      "direction": f"lane-{lane + 1:02}", "step": step}
            if mechanism == "C":
                prefix = ([features[4]] if lane == 0 else [features[5]] if lane == 1 else [features[4], features[5]])
                options.append(CpuSearchOption(**coords, context=" ".join(_VOCABULARY[f] for f in prefix * step)))
            elif mechanism == "D":
                options.append(CpuSearchOption(**coords, decoding=CpuDecoding(
                    mode="sample", temperature=float(2 * lane + step), top_k=1, seed=0)))
            else:
                repaired = _repair_classes(family, instance_index, lane, step)
                edited = list(weights)
                for feature_class, pair in enumerate(pairs[:4]):
                    original_winner = 0 if pair[0] > pair[1] else 1
                    winner = labels[feature_class] if feature_class in repaired else original_winner
                    # Margin changes make all six checkpoint byte strings distinct,
                    # including neutral score ties, without changing teacher labels.
                    margin = max(pair) + ordinal + instance_index
                    edited[features[feature_class] * 2:features[feature_class] * 2 + 2] = (
                        (float(margin), 0.0) if winner == 0 else (0.0, float(margin)))
                output, _ = _checkpoint(tuple(edited))
                candidate_id = f"candidate-{names[ordinal] + 1:02}"
                replacements = tuple(TensorReplacement(index=index, value_hex=struct.pack("<f", value).hex())
                                     for index, value in enumerate(edited) if value != weights[index])
                program = TensorEditProgram(input_digest=contract.digest, edits=(TensorEdit(
                    name="projection.weight", expected_sha256=contract.tensors[1].sha256, dtype="F32", shape=(8, 2),
                    replacements=replacements),))
                candidates.append(TensorCandidateSpec(candidate_id=candidate_id, contract=contract, program=program,
                    output_sha256=hashlib.sha256(output).hexdigest(), output_size_bytes=len(output)))
                options.append(CpuSearchOption(**coords, candidate_id=candidate_id))
    cases = []
    rays: set[tuple[int, ...]] = set()
    for role_index, role in enumerate(LANDSCAPE_CONTROL_ROLES):
        for index, slot in enumerate(_permutation(generation_seed, f"{role}-order", 8)):
            feature_class, replica = divmod(slot, 2)
            counts = [0] * 8
            counts[features[feature_class]] = 1
            counts[features[6]], counts[features[7]] = 2 * role_index + replica + 1, 1
            prompt = " ".join(token for token, amount in zip(_VOCABULARY, counts, strict=True) for _ in range(amount))
            ray = token_count_ray(prompt, _VOCABULARY)
            if ray in rays:
                raise ValueError("landscape construction repeats a normalized input ray")
            rays.add(ray)
            cases.append(CpuCase(case_id=f"{role}-{index + 1:02}", role=role, prompt=prompt,  # type: ignore[arg-type]
                                 expected=model.output_tokens[labels[feature_class]]))
    calls = 26 * 8
    spec = CpuSearchSpec(study_id=f"landscape-{task_id}", model=model, candidates=tuple(candidates), options=tuple(options),
        rounds=3, baseline_decoding=CpuDecoding(seed=0), evaluation_seed=0,
        limits=CpuWorkerLimits(memory_mb=512, wall_seconds=5.0, max_input_tokens=12),
        budget=CpuSearchBudget(max_proposals=9, max_materializations=7, max_inference_calls=calls,
                              max_input_tokens=12 * calls, max_output_tokens=calls),
        cases=tuple(cases), minimum_confirmation_gain=0.1, implementation_digest=search_implementation_digest())
    semantic_cases = [{"role": case.role, "prompt": case.prompt, "expected": case.expected} for case in cases]
    provenance = CpuLandscapeControlTaskProvenance(family=family, task_id=task_id, generation_seed=generation_seed,
        instance_index=instance_index, generator_source_sha256=file_identity(Path(__file__))["sha256"],
        feature_indices=features, base_weights=tuple(weights), teacher_labels=labels,
        model_digest=model.digest, base_sha256=contract.sha256, cases_digest=canonical_digest(semantic_cases),
        candidates_digest=canonical_digest([c.to_dict() for c in candidates]),
        options_digest=canonical_digest([o.to_dict() for o in options]),
        semantic_digest=canonical_digest({"model": model.to_dict(), "base": contract.sha256, "cases": semantic_cases}))
    encoded = _encode(spec.to_dict())
    lock = CpuSearchBundleLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
                              base_sha256=contract.sha256, base_size_bytes=contract.size_bytes)
    return spec, provenance, {"study.json": encoded, "base.safetensors": base,
                              "bundle.lock.json": _encode(lock.to_dict()), "task.provenance.json": _encode(provenance.to_dict())}


def landscape_control_schedule(
    campaign: CpuLandscapeControlCampaignSpec, templates: dict[str, CpuSearchSpec],
) -> list[tuple[dict[str, Any], CpuSearchSpec]]:
    rows = []
    for task in campaign.tasks:
        for repeat in campaign.repeats:
            for policy in campaign.policies:
                run_id = f"{task.task_id}.{repeat.repeat_id}.{policy}"
                raw = templates[task.task_id].to_dict()
                raw.update(study_id=run_id, evaluation_seed=repeat.inference_seed,
                           policy={"name": policy, "order_seed": repeat.search_order_seed if policy == "seeded-random-v1" else 0})
                raw["baseline_decoding"]["seed"] = repeat.inference_seed
                for option in raw["options"]:
                    if option["decoding"] is not None:
                        option["decoding"]["seed"] = repeat.inference_seed
                child = CpuSearchSpec.model_validate_json(json.dumps(raw))
                rows.append(({"run_id": run_id, "task_id": task.task_id, "repeat_id": repeat.repeat_id,
                              "instance_index": task.instance_index,
                              "policy": policy, "inference_seed": repeat.inference_seed,
                              "order_seed": raw["policy"]["order_seed"], "study_digest": child.digest,
                              "reserved_capacity": child.required_usage}, child))
    return rows


def build_landscape_control_campaign(
    directory: Path, *, campaign_id: str = "cpu-search-landscapes-v1",
) -> CpuLandscapeControlCampaignSpec:
    _disjoint(directory)
    if directory.exists():
        raise ValueError("landscape control builder requires a fresh directory")
    payloads_by_task, descriptors = {}, []
    templates, provenances = {}, {}
    usage = dict.fromkeys(METERS, 0)
    for task_id, family, instance_index, seed in LANDSCAPE_CONTROL_ROSTER:
        template, provenance_value, payloads = landscape_control_task_payloads(
            task_id=task_id, generation_seed=seed, family=family, instance_index=instance_index)
        templates[task_id], provenances[task_id] = template, provenance_value
        payloads_by_task[task_id] = payloads
        for meter, value in template.required_usage.items():
            usage[meter] += value * len(LANDSCAPE_CONTROL_REPEATS) * 3
        task_lock, provenance = _identity(payloads["bundle.lock.json"]), _identity(payloads["task.provenance.json"])
        descriptors.append(CpuLandscapeControlCampaignTask(task_id=task_id, generation_seed=seed, family=family,
            instance_index=instance_index,
            bundle_lock_sha256=task_lock["sha256"], bundle_lock_size_bytes=task_lock["size_bytes"],
            provenance_sha256=provenance["sha256"], provenance_size_bytes=provenance["size_bytes"]))
    oracle_bytes = _oracle_bytes(templates, provenances)
    spec = CpuLandscapeControlCampaignSpec(campaign_id=campaign_id, tasks=tuple(descriptors),
        implementation_digest=landscape_control_implementation_digest(),
        oracle_sha256=hashlib.sha256(oracle_bytes).hexdigest(), oracle_size_bytes=len(oracle_bytes),
        budget=CpuCampaignBudget(max_runs=36, **{f"max_{meter}": value for meter, value in usage.items()}))
    encoded = _encode(spec.to_dict())
    lock = CpuLandscapeControlCampaignLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded))
    directory.mkdir(parents=True)
    for task_id, payloads in payloads_by_task.items():
        task_directory = directory / "tasks" / task_id
        task_directory.mkdir(parents=True)
        for name, payload in payloads.items():
            (task_directory / name).write_bytes(payload)
    (directory / "campaign.json").write_bytes(encoded)
    (directory / "bundle.lock.json").write_bytes(_encode(lock.to_dict()))
    (directory / "oracle.json").write_bytes(oracle_bytes)
    return spec


def _oracle_bytes(
    templates: dict[str, CpuSearchSpec], provenances: dict[str, CpuLandscapeControlTaskProvenance],
) -> bytes:
    from autocontext.weight_evolution.cpu_campaign_models import CAMPAIGN_POLICIES

    rows = []
    for task_id, template in templates.items():
        for repeat in LANDSCAPE_CONTROL_REPEATS:
            for policy in CAMPAIGN_POLICIES:
                expected = expected_landscape_control_run(template, provenances[task_id], inference_seed=repeat.inference_seed,
                    policy=policy, order_seed=repeat.search_order_seed if policy == "seeded-random-v1" else 0)
                if expected["confirmation"]["status"] != provenances[task_id].expected_joint_status:
                    raise ValueError("landscape construction disagrees with its declared gate; no replacement task is permitted")
                rows.append({"run_id": f"{task_id}.{repeat.repeat_id}.{policy}", "task_id": task_id,
                             "repeat_id": repeat.repeat_id, **expected})
    # These finite algebraic expectations are part of the construction, not a
    # criterion for replacing a task or searching for a favorable inference seed.
    for task_id, facts in provenances.items():
        baseline = 4 if facts.instance_index == 1 else 2
        for repeat in LANDSCAPE_CONTROL_REPEATS:
            paired = {row["policy"]: row for row in rows
                      if row["task_id"] == task_id and row["repeat_id"] == repeat.repeat_id}
            adaptive = paired["adaptive-v1"]["scores"]["practice"]["W"]["correct"]
            fixed = paired["fixed-order-v1"]["scores"]["practice"]["W"]["correct"]
            expected_pair = {"informative-improvement-v1": (8, baseline + 2),
                        "misleading-improvement-v1": (baseline + 2, 8), "neutral-v1": (baseline, baseline)}[facts.family]
            if (adaptive, fixed) != expected_pair:
                raise ValueError("landscape construction misses its pinned W contrast; no replacement task is permitted")
    return _encode({"schema_version": 1, "kind": "predeclared-landscape-control-oracle-v1",
                    "scope": "construction-expectations-not-measured-evidence", "runs": rows})


def _read(path: Path) -> bytes:
    with regular_file(path) as stream:
        data = stream.read(MAX_LANDSCAPE_CONTROL_BYTES + 1)
    if len(data) > MAX_LANDSCAPE_CONTROL_BYTES:
        raise ValueError("landscape control asset exceeds its byte cap")
    return data


def load_landscape_control_campaign(
    directory: Path,
) -> tuple[CpuLandscapeControlCampaignSpec, dict[str, CpuSearchSpec], dict[str, CpuLandscapeControlTaskProvenance]]:
    """Regenerate every task byte, even when an attacker has rehashed its lock."""
    _disjoint(directory)
    if _entries(directory, 4) != {"campaign.json", "bundle.lock.json", "oracle.json", "tasks"}:
        raise ValueError("landscape control campaign inventory changed")
    raw_lock = json.loads(_read(directory / "bundle.lock.json"))
    lock = CpuLandscapeControlCampaignLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "landscape control lock representation changed")
    _pin(directory / "campaign.json", lock.spec_sha256, lock.spec_size_bytes)
    raw = json.loads(_read(directory / "campaign.json"))
    spec = CpuLandscapeControlCampaignSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "landscape control campaign representation changed")
    if spec.implementation_digest != landscape_control_implementation_digest():
        raise ValueError("landscape control implementation changed")
    _pin(directory / "oracle.json", spec.oracle_sha256, spec.oracle_size_bytes)
    if _entries(directory / "tasks", 6) != {task.task_id for task in spec.tasks}:
        raise ValueError("landscape control task inventory changed")
    templates, provenance = {}, {}
    for task in spec.tasks:
        bundle = directory / "tasks" / task.task_id
        expected, facts, payloads = landscape_control_task_payloads(
            task_id=task.task_id, generation_seed=task.generation_seed, family=task.family, instance_index=task.instance_index)
        if _entries(bundle, 4) != set(payloads):
            raise ValueError("landscape control task inventory changed")
        _pin(bundle / "bundle.lock.json", task.bundle_lock_sha256, task.bundle_lock_size_bytes)
        _pin(bundle / "task.provenance.json", task.provenance_sha256, task.provenance_size_bytes)
        for name, value in payloads.items():
            if _read(bundle / name) != value:
                raise ValueError(f"landscape control task differs from deterministic generation: {name}")
        validate_cpu_search(bundle)
        if load_cpu_search(bundle) != expected:
            raise ValueError("landscape control task differs from validated search")
        templates[task.task_id], provenance[task.task_id] = expected, facts
    required = {meter: sum(t.required_usage[meter] for t in templates.values()) * 6 for meter in METERS}
    if spec.budget != CpuCampaignBudget(max_runs=36, **{f"max_{m}": value for m, value in required.items()}):
        raise ValueError("landscape control full capacity changed")
    if _read(directory / "oracle.json") != _oracle_bytes(templates, provenance):
        raise ValueError("landscape control oracle differs from predeclared construction expectations")
    return spec, templates, provenance
