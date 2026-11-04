"""Deterministic transfer controls whose W behavior follows from construction.

Only structural input collisions are rejected. Tasks, seeds and option placement
are never selected by inference scores. Family/teacher/expected-control metadata
stays in provenance; search policies receive the ordinary bounded option space.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateSpec
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignBudget, CpuCampaignRepeat
from autocontext.weight_evolution.cpu_campaign_tasks import PILOT_TASK_SEEDS as M11_TASK_SEEDS
from autocontext.weight_evolution.cpu_campaign_tasks import _checkpoint, token_count_ray
from autocontext.weight_evolution.cpu_control_models import (
    CONTROL_FAMILIES,
    CONTROL_ROLES,
    MAX_CONTROL_FILE_BYTES,
    ControlFamily,
    ControlRole,
    CpuControlCampaignLock,
    CpuControlCampaignSpec,
    CpuControlCampaignTask,
    CpuControlExpectedBehavior,
    CpuControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_models import CpuDecoding, CpuModelContract, CpuWorkerLimits
from autocontext.weight_evolution.cpu_search import search_implementation_digest
from autocontext.weight_evolution.cpu_search_models import CpuSearchBudget, CpuSearchBundleLock, CpuSearchOption, CpuSearchSpec
from autocontext.weight_evolution.cpu_study import CpuCase
from autocontext.weight_evolution.tensor_edits import (
    CheckpointContract,
    TensorContract,
    TensorEdit,
    TensorEditProgram,
    TensorReplacement,
)

CONTROL_TASK_SEEDS: dict[ControlFamily, tuple[int, ...]] = {
    "transfer-repair-v1": (1000003, 1000033, 1000037, 1000039),
    "correct-baseline-v1": (1100009, 1100021, 1100029, 1100039),
    "practice-shortcut-v1": (1200007, 1200013, 1200029, 1200037),
}
CONTROL_REPEATS = (
    CpuCampaignRepeat(repeat_id="repeat-01", inference_seed=4700001, search_order_seed=5700001),
    CpuCampaignRepeat(repeat_id="repeat-02", inference_seed=4700033, search_order_seed=5700033),
)
_VOCABULARY = tuple(f"token{index}" for index in range(8))


class _ControlStream:
    """Versioned domain-separated integer draws, independent of library RNGs."""

    def __init__(self, seed: int, family: ControlFamily, domain: str) -> None:
        self.prefix = f"sha256-transfer-controls-v1:{seed}:{family}:{domain}:"
        self.counter = 0

    def below(self, bound: int) -> int:
        cutoff = 2**256 - 2**256 % bound
        while True:
            value = int.from_bytes(hashlib.sha256(f"{self.prefix}{self.counter}".encode()).digest(), "big")
            self.counter += 1
            if value < cutoff:
                return value % bound

    def permutation(self, size: int) -> tuple[int, ...]:
        remaining = list(range(size))
        return tuple(remaining.pop(self.below(len(remaining))) for _ in range(size))


def _encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _identity(value: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)}


def _cases(seed: int, family: ControlFamily, count: int, features: tuple[int, ...]) -> tuple[CpuCase, ...]:
    stream = _ControlStream(seed, family, "balanced-prompts")
    seen: set[tuple[int, ...]] = set()
    cases = []
    for role in CONTROL_ROLES:
        # Balance is part of the population definition, not an outcome filter.
        labels = [value % 2 for value in stream.permutation(count)]
        for index, label in enumerate(labels):
            for _ in range(10000):
                counts = [0] * 8
                counts[features[label]] = 1
                spurious_label = 1 - label if family == "practice-shortcut-v1" and role != "practice" else label
                counts[features[2 + spurious_label]] = 1
                for coordinate in features[4:7]:
                    counts[coordinate] = stream.below(4)
                prompt = " ".join(token for token, amount in zip(_VOCABULARY, counts, strict=True) for _ in range(amount))
                ray = token_count_ray(prompt, _VOCABULARY)
                if ray not in seen:
                    seen.add(ray)
                    break
            else:
                raise ValueError("control generator exhausted distinct structural input rays")
            # Both positive and negative evidence occur exactly once and are
            # mutually exclusive. The teacher ignores every other coordinate.
            margin = 2 * counts[features[0]] - 2 * counts[features[1]]
            cases.append(CpuCase(case_id=f"{role}-{index + 1:02}", role=role, prompt=prompt,
                                 expected="zero" if margin >= 0 else "one"))
    return tuple(cases)


def _task_payloads(
    *, task_id: str, generation_seed: int, family: ControlFamily, cases_per_role: int,
) -> tuple[CpuSearchSpec, CpuControlTaskProvenance, dict[str, bytes]]:
    if family not in CONTROL_FAMILIES:
        raise ValueError("unknown control family")
    if type(generation_seed) is not int or not 0 <= generation_seed <= 2**32 - 1 or generation_seed in M11_TASK_SEEDS:
        raise ValueError("control seed must be a bounded integer distinct from the M11 task seeds")
    if type(cases_per_role) is not int or not 4 <= cases_per_role <= 16 or cases_per_role % 2:
        raise ValueError("control cohorts require an even case count from four through sixteen")
    features = _ControlStream(generation_seed, family, "feature-permutation").permutation(8)
    teacher = [0] * 8
    teacher[features[0]], teacher[features[1]] = 2, -2
    weights = [float(value) if column == 0 else 0.0 for value in teacher for column in range(2)]
    if family == "transfer-repair-v1":
        weights[features[1] * 2] = 2.0
        target, first, delta = features[1] * 2, -2.0, -4.0
    elif family == "correct-baseline-v1":
        target, first, delta = features[1] * 2, 0.0, 2.0
    else:
        weights = [0.0] * 16
        target, first, delta = features[3] * 2, -2.0, -2.0
    model = CpuModelContract(vocabulary=_VOCABULARY, output_tokens=("zero", "one"))
    base_bytes, payloads = _checkpoint(tuple(weights))
    contract = CheckpointContract(
        **_identity(base_bytes), max_changed_entries=1,
        tensors=(TensorContract(name="projection.bias", dtype="F32", shape=(2,),
                                sha256=hashlib.sha256(payloads["projection.bias"]).hexdigest()),
                 TensorContract(name="projection.weight", dtype="F32", shape=(8, 2), editable=True,
                                sha256=hashlib.sha256(payloads["projection.weight"]).hexdigest())),
    )
    candidates = [TensorCandidateSpec(candidate_id="base", contract=contract, output_sha256=contract.sha256,
                                      output_size_bytes=contract.size_bytes)]
    options: list[CpuSearchOption] = []
    designated: list[str] = []
    for mechanism in ("C", "W", "D"):
        stream = _ControlStream(generation_seed, family, f"{mechanism}-option-placement")
        lane_order, lane_names, option_names = stream.permutation(2), stream.permutation(2), stream.permutation(4)
        ordinal = 0
        for lane in lane_order:
            for step in (1, 2):
                option_id = f"{mechanism.lower()}-{option_names[ordinal] + 1:02}"
                ordinal += 1
                coordinates: dict[str, Any] = {"option_id": option_id, "mechanism": mechanism,
                                               "direction": f"lane-{lane_names[lane] + 1:02}", "step": step}
                if mechanism == "C":
                    options.append(CpuSearchOption(**coordinates, context=" ".join([_VOCABULARY[features[lane]]] * step)))
                elif mechanism == "D":
                    temperature = ((0.5, 0.25), (2.0, 4.0))[lane][step - 1]
                    options.append(CpuSearchOption(**coordinates, decoding=CpuDecoding(
                        mode="sample", temperature=temperature, top_k=2, seed=0)))
                else:
                    coordinate, value = ((target, first + (step - 1) * delta) if lane == 0 else
                                         (features[7] * 2, float(step)))
                    edited = list(weights)
                    edited[coordinate] = value
                    output, _ = _checkpoint(tuple(edited))
                    candidate_id = f"candidate-{option_names[ordinal - 1] + 1:02}"
                    program = TensorEditProgram(input_digest=contract.digest, edits=(TensorEdit(
                        name="projection.weight", expected_sha256=contract.tensors[1].sha256,
                        dtype="F32", shape=(8, 2), replacements=(TensorReplacement(
                            index=coordinate, value_hex=struct.pack("<f", value).hex()),)),))
                    candidates.append(TensorCandidateSpec(candidate_id=candidate_id, contract=contract, program=program,
                                                           output_sha256=hashlib.sha256(output).hexdigest(),
                                                           output_size_bytes=len(output)))
                    options.append(CpuSearchOption(**coordinates, candidate_id=candidate_id))
                    if lane == 0:
                        designated.append(option_id)
    cases = _cases(generation_seed, family, cases_per_role, features)
    calls = 26 * cases_per_role
    spec = CpuSearchSpec(
        study_id=f"control-{task_id}", model=model, candidates=tuple(candidates), options=tuple(options), rounds=3,
        baseline_decoding=CpuDecoding(seed=0), evaluation_seed=0,
        limits=CpuWorkerLimits(memory_mb=512, wall_seconds=5.0, max_input_tokens=14),
        budget=CpuSearchBudget(max_proposals=9, max_materializations=5, max_inference_calls=calls,
                               max_input_tokens=14 * calls, max_output_tokens=calls),
        cases=cases, minimum_confirmation_gain=0.1, implementation_digest=search_implementation_digest(),
    )
    baseline = 1.0 if family == "correct-baseline-v1" else 0.5
    expected: dict[ControlRole, float] = (
        {role: 0.5 for role in CONTROL_ROLES} if family == "correct-baseline-v1" else
        {role: 1.0 for role in CONTROL_ROLES} if family == "transfer-repair-v1" else
        {"practice": 1.0, "confirmation": 0.0, "audit": 0.0})
    semantic_cases = [{"role": case.role, "prompt": case.prompt, "expected": case.expected} for case in cases]
    provenance = CpuControlTaskProvenance(
        family=family, generator_source_sha256=file_identity(Path(__file__), max_bytes=MAX_CONTROL_FILE_BYTES)["sha256"],
        task_id=task_id, generation_seed=generation_seed, cases_per_role=cases_per_role, feature_indices=features,
        teacher_weights=tuple(teacher), model_digest=model.digest, base_sha256=contract.sha256,
        cases_digest=canonical_digest(semantic_cases), candidates_digest=canonical_digest([c.to_dict() for c in candidates]),
        options_digest=canonical_digest([option.to_dict() for option in options]),
        semantic_digest=canonical_digest({"model": model.to_dict(), "base": contract.sha256, "cases": semantic_cases}),
        expected_behavior=CpuControlExpectedBehavior(baseline_accuracy={role: baseline for role in CONTROL_ROLES},
                                                    designated_weight_option_ids=tuple(designated),
                                                    designated_weight_accuracy=expected),
    )
    encoded = _encode(spec.to_dict())
    lock = CpuSearchBundleLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
                               base_sha256=contract.sha256, base_size_bytes=contract.size_bytes)
    return spec, provenance, {"study.json": encoded, "base.safetensors": base_bytes,
                              "bundle.lock.json": _encode(lock.to_dict()),
                              "task.provenance.json": _encode(provenance.to_dict())}


def _fresh(directory: Path) -> None:
    if directory.exists() or any(part.is_symlink() for part in (directory.absolute(), *directory.absolute().parents)):
        raise ValueError("control builders require a fresh directory without symlinks")


def build_cpu_control_task(
    directory: Path, *, task_id: str, generation_seed: int, family: ControlFamily, cases_per_role: int = 8,
) -> CpuSearchSpec:
    _fresh(directory)
    spec, _, payloads = _task_payloads(task_id=task_id, generation_seed=generation_seed, family=family,
                                      cases_per_role=cases_per_role)
    directory.mkdir(parents=True)
    for name, data in payloads.items():
        (directory / name).write_bytes(data)
    return spec


def _read(path: Path) -> bytes:
    with regular_file(path) as stream:
        data = stream.read(MAX_CONTROL_FILE_BYTES + 1)
    if len(data) > MAX_CONTROL_FILE_BYTES:
        raise ValueError("control task asset exceeds its byte cap")
    return data


def validate_control_task_provenance(directory: Path, spec: CpuSearchSpec | None = None) -> CpuControlTaskProvenance:
    """Regenerate all expected task bytes without inference or file writes."""
    provenance = CpuControlTaskProvenance.model_validate_json(_read(directory / "task.provenance.json"))
    expected, expected_provenance, payloads = _task_payloads(
        task_id=provenance.task_id, generation_seed=provenance.generation_seed, family=provenance.family,
        cases_per_role=provenance.cases_per_role,
    )
    if spec is not None and spec != expected:
        raise ValueError("control task differs from deterministic generation")
    for name, data in payloads.items():
        if _read(directory / name) != data:
            raise ValueError(f"control task asset differs from deterministic generation: {name}")
    return expected_provenance


def _implementation_digest() -> str:
    from autocontext.weight_evolution.cpu_control import control_implementation_digest

    return control_implementation_digest()


def build_cpu_control_campaign(
    directory: Path, *, preset: str = "fixture", instances_per_family: int | None = None,
    repeats: tuple[CpuCampaignRepeat, ...] | None = None, cases_per_role: int = 8,
    campaign_id: str = "cpu-transfer-controls-v1",
) -> CpuControlCampaignSpec:
    """Pin all three balanced families before any search outcome is available."""
    _fresh(directory)
    if preset not in {"fixture", "pilot"}:
        raise ValueError("unknown control campaign preset")
    count = (2 if preset == "fixture" else 4) if instances_per_family is None else instances_per_family
    if type(count) is not int or not 1 <= count <= 4:
        raise ValueError("control campaigns require one through four instances per family")
    actual_repeats: tuple[CpuCampaignRepeat, ...] = CONTROL_REPEATS[:1] if preset == "fixture" else CONTROL_REPEATS
    if repeats is not None:
        actual_repeats = repeats
    descriptors: list[CpuControlCampaignTask] = []
    payloads_by_task: dict[str, dict[str, bytes]] = {}
    usage = {name: 0 for name in ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")}
    semantic_tasks: set[str] = set()
    for family in CONTROL_FAMILIES:
        for seed in CONTROL_TASK_SEEDS[family][:count]:
            task_id = f"task-{len(descriptors) + 1:02}"
            template, provenance, payloads = _task_payloads(
                task_id=task_id, generation_seed=seed, family=family, cases_per_role=cases_per_role,
            )
            if provenance.semantic_digest in semantic_tasks:
                raise ValueError("control campaign repeats a semantic task instance")
            semantic_tasks.add(provenance.semantic_digest)
            payloads_by_task[task_id] = payloads
            for name, value in template.required_usage.items():
                usage[name] += value * len(actual_repeats) * 3
            task_lock, task_provenance = _identity(payloads["bundle.lock.json"]), _identity(payloads["task.provenance.json"])
            descriptors.append(CpuControlCampaignTask(task_id=task_id, generation_seed=seed, family=family,
                bundle_lock_sha256=task_lock["sha256"], bundle_lock_size_bytes=task_lock["size_bytes"],
                provenance_sha256=task_provenance["sha256"], provenance_size_bytes=task_provenance["size_bytes"]))
    spec = CpuControlCampaignSpec(
        campaign_id=campaign_id, tasks=tuple(descriptors), repeats=actual_repeats,
        budget=CpuCampaignBudget(max_runs=len(descriptors) * len(actual_repeats) * 3,
                                 **{f"max_{name}": value for name, value in usage.items()}),
        implementation_digest=_implementation_digest(),
    )
    encoded = _encode(spec.to_dict())
    lock = CpuControlCampaignLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded))
    directory.mkdir(parents=True)
    for task_id, payloads in payloads_by_task.items():
        task_directory = directory / "tasks" / task_id
        task_directory.mkdir(parents=True)
        for name, data in payloads.items():
            (task_directory / name).write_bytes(data)
    (directory / "campaign.json").write_bytes(encoded)
    (directory / "bundle.lock.json").write_bytes(_encode(lock.to_dict()))
    return spec
