"""Deterministic CPU task families generated before any search outcome is known.

Independent SHA-256 substreams choose the latent teacher, base checkpoint,
candidate directions, and prompts. No example or candidate is filtered by its
score. The teacher supplies labels only to the coordinator's case inventory.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any, Literal

from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateSpec
from autocontext.weight_evolution.cpu_campaign_models import (
    MAX_CAMPAIGN_FILE_BYTES,
    CpuCampaignBudget,
    CpuCampaignLock,
    CpuCampaignRepeat,
    CpuCampaignSpec,
    CpuCampaignTask,
    CpuTaskProvenance,
)
from autocontext.weight_evolution.cpu_models import CpuDecoding, CpuModelContract, CpuWorkerLimits
from autocontext.weight_evolution.cpu_search import search_implementation_digest
from autocontext.weight_evolution.cpu_search_models import (
    CpuSearchBudget,
    CpuSearchBundleLock,
    CpuSearchOption,
    CpuSearchSpec,
)
from autocontext.weight_evolution.cpu_study import CpuCase
from autocontext.weight_evolution.tensor_edits import (
    CheckpointContract,
    TensorContract,
    TensorEdit,
    TensorEditProgram,
    TensorReplacement,
)

PILOT_TASK_SEEDS = (104729, 130363, 155921, 181081, 206369, 232003, 257053, 282407)
FIXTURE_TASK_SEEDS = PILOT_TASK_SEEDS[:3]
PILOT_REPEATS = (
    CpuCampaignRepeat(repeat_id="repeat-01", inference_seed=15485863, search_order_seed=32452843),
    CpuCampaignRepeat(repeat_id="repeat-02", inference_seed=15485917, search_order_seed=32452867),
    CpuCampaignRepeat(repeat_id="repeat-03", inference_seed=15485927, search_order_seed=32452889),
)
_VOCABULARY = tuple(f"token{index}" for index in range(8))
_ROLES: tuple[Literal["practice", "confirmation", "audit"], ...] = ("practice", "confirmation", "audit")


class _Stream:
    """Versioned integer draws without library RNG or floating point dependence."""

    def __init__(self, seed: int, domain: str) -> None:
        self.prefix = f"sha256-linear-separator-v1:{seed}:{domain}:"
        self.counter = 0

    def below(self, bound: int) -> int:
        cutoff = 2**256 - (2**256 % bound)
        while True:
            value = int.from_bytes(hashlib.sha256(f"{self.prefix}{self.counter}".encode()).digest(), "big")
            self.counter += 1
            if value < cutoff:
                return value % bound

    def distinct(self, size: int, count: int) -> tuple[int, ...]:
        remaining = list(range(size))
        return tuple(remaining.pop(self.below(len(remaining))) for _ in range(count))


def _encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _identity(data: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def _checkpoint(weights: tuple[float, ...]) -> tuple[bytes, dict[str, bytes]]:
    """Encode this family's fixed two F32 tensors entirely in memory.

    This is intentionally not a general checkpoint writer. The ordinary tensor
    parser and edit applier independently verify these bytes before execution.
    Keeping generation in memory lets report verification regenerate pins without
    writing checkpoints or consuming materialization reservations.
    """
    payloads = {"projection.bias": struct.pack("<ff", 0.0, 0.0),
                "projection.weight": struct.pack("<16f", *weights)}
    header = {"__metadata__": {"format": "pt"},
              "projection.bias": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]},
              "projection.weight": {"dtype": "F32", "shape": [8, 2], "data_offsets": [8, 72]}}
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 8)
    return struct.pack("<Q", len(encoded)) + encoded + b"".join(payloads.values()), payloads


def token_count_ray(prompt: str, vocabulary: tuple[str, ...]) -> tuple[int, ...]:
    """Identify permutations and proportional token repetitions as one input ray."""
    tokens = prompt.split()
    if not tokens or any(token not in vocabulary for token in tokens):
        raise ValueError("task prompt contains empty or unknown token content")
    counts = tuple(tokens.count(token) for token in vocabulary)
    divisor = math.gcd(*counts)
    return tuple(count // divisor for count in counts)


def _generated_cases(seed: int, count: int, teacher: tuple[int, ...]) -> tuple[CpuCase, ...]:
    stream = _Stream(seed, "prompts")
    seen: set[tuple[int, ...]] = set()
    cases: list[CpuCase] = []
    for role in _ROLES:
        for index in range(count):
            for _ in range(10000):
                counts = [0] * len(_VOCABULARY)
                for _ in range(4 + stream.below(7)):
                    counts[stream.below(len(_VOCABULARY))] += 1
                prompt = " ".join(token for token, amount in zip(_VOCABULARY, counts, strict=True) for _ in range(amount))
                ray = token_count_ray(prompt, _VOCABULARY)
                if ray not in seen:
                    seen.add(ray)
                    break
            else:
                raise ValueError("deterministic task generator exhausted distinct input rays")
            # The rejection criterion above is purely structural, never a label,
            # base score, candidate score, or search-policy outcome.
            margin = sum(weight * amount for weight, amount in zip(teacher, counts, strict=True))
            cases.append(CpuCase(case_id=f"{role}-{index + 1:02}", role=role, prompt=prompt,
                                 expected="zero" if margin >= 0 else "one"))
    return tuple(cases)


def _task_payloads(
    *, task_id: str, generation_seed: int, cases_per_role: int,
) -> tuple[CpuSearchSpec, CpuTaskProvenance, dict[str, bytes]]:
    if type(generation_seed) is not int or not 0 <= generation_seed <= 2**32 - 1:
        raise ValueError("task generation seed must be a bounded integer")
    if type(cases_per_role) is not int or not 2 <= cases_per_role <= 16:
        raise ValueError("task requires 2..16 cases per role")
    model = CpuModelContract(vocabulary=_VOCABULARY, output_tokens=("zero", "one"))
    teacher_stream = _Stream(generation_seed, "teacher")
    teacher = tuple((1 + teacher_stream.below(4)) * (-1 if teacher_stream.below(2) else 1) for _ in _VOCABULARY)
    base_stream = _Stream(generation_seed, "base")
    weights = tuple((base_stream.below(9) - 4) / 2 for _ in range(16))
    base_bytes, payloads = _checkpoint(weights)
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
    context_stream = _Stream(generation_seed, "context-directions")
    for direction, token_index in zip(("a", "b"), context_stream.distinct(8, 2), strict=True):
        for step in (1, 2):
            options.append(CpuSearchOption(option_id=f"c-{direction}-{step}", mechanism="C", direction=direction,
                                           step=step, context=" ".join([_VOCABULARY[token_index]] * step)))
    edit_stream = _Stream(generation_seed, "weight-directions")
    for direction, coordinate in zip(("a", "b"), edit_stream.distinct(16, 2), strict=True):
        delta = (1 + edit_stream.below(3)) * (-1 if edit_stream.below(2) else 1)
        for step in (1, 2):
            edited = list(weights)
            edited[coordinate] += step * delta
            output, _ = _checkpoint(tuple(edited))
            candidate_id = f"coordinate-{direction}-{step}"
            program = TensorEditProgram(input_digest=contract.digest, edits=(TensorEdit(
                name="projection.weight", expected_sha256=contract.tensors[1].sha256, dtype="F32", shape=(8, 2),
                replacements=(TensorReplacement(index=coordinate, value_hex=struct.pack("<f", edited[coordinate]).hex()),),
            ),))
            candidates.append(TensorCandidateSpec(candidate_id=candidate_id, contract=contract, program=program,
                                                   output_sha256=hashlib.sha256(output).hexdigest(),
                                                   output_size_bytes=len(output)))
            options.append(CpuSearchOption(option_id=f"w-{direction}-{step}", mechanism="W", direction=direction,
                                           step=step, candidate_id=candidate_id))
    for direction, temperatures in (("cool", (0.5, 0.25)), ("warm", (2.0, 4.0))):
        for step, temperature in enumerate(temperatures, 1):
            options.append(CpuSearchOption(option_id=f"d-{direction}-{step}", mechanism="D", direction=direction,
                                           step=step, decoding=CpuDecoding(mode="sample", temperature=temperature,
                                                                          top_k=2, seed=0)))
    cases = _generated_cases(generation_seed, cases_per_role, teacher)
    calls = 26 * cases_per_role
    spec = CpuSearchSpec(
        study_id=f"generated-{task_id}", model=model, candidates=tuple(candidates), options=tuple(options), rounds=3,
        baseline_decoding=CpuDecoding(seed=0), evaluation_seed=0,
        limits=CpuWorkerLimits(memory_mb=512, wall_seconds=5.0, max_input_tokens=12),
        budget=CpuSearchBudget(max_proposals=9, max_materializations=5, max_inference_calls=calls,
                               max_input_tokens=12 * calls, max_output_tokens=calls),
        cases=cases, minimum_confirmation_gain=0.1, implementation_digest=search_implementation_digest(),
    )
    semantic_cases = [{"role": case.role, "prompt": case.prompt, "expected": case.expected} for case in cases]
    provenance = CpuTaskProvenance(
        generator_source_sha256=file_identity(Path(__file__), max_bytes=MAX_CAMPAIGN_FILE_BYTES)["sha256"],
        task_id=task_id, generation_seed=generation_seed, cases_per_role=cases_per_role, teacher_weights=teacher,
        model_digest=model.digest, base_sha256=contract.sha256, cases_digest=canonical_digest(semantic_cases),
        candidates_digest=canonical_digest([candidate.to_dict() for candidate in candidates]),
        options_digest=canonical_digest([option.to_dict() for option in options]),
        semantic_digest=canonical_digest({"model": model.to_dict(), "base": contract.sha256,
                                          "cases": semantic_cases}),
    )
    study_bytes = _encode(spec.to_dict())
    lock = CpuSearchBundleLock(spec_sha256=hashlib.sha256(study_bytes).hexdigest(), spec_size_bytes=len(study_bytes),
                               base_sha256=contract.sha256, base_size_bytes=contract.size_bytes)
    return spec, provenance, {"study.json": study_bytes, "base.safetensors": base_bytes,
                              "bundle.lock.json": _encode(lock.to_dict()),
                              "task.provenance.json": _encode(provenance.to_dict())}


def _fresh(directory: Path) -> None:
    if directory.exists() or any(part.is_symlink() for part in (directory, *directory.parents)):
        raise ValueError("campaign builders require a fresh directory without symlinks")


def build_cpu_task(directory: Path, *, task_id: str, generation_seed: int, cases_per_role: int = 4) -> CpuSearchSpec:
    """Write a new task bundle; no candidate is run or selected by this builder."""
    _fresh(directory)
    spec, _, payloads = _task_payloads(task_id=task_id, generation_seed=generation_seed, cases_per_role=cases_per_role)
    directory.mkdir(parents=True)
    for name, data in payloads.items():
        (directory / name).write_bytes(data)
    return spec


def _read(path: Path) -> bytes:
    with regular_file(path) as stream:
        data = stream.read(MAX_CAMPAIGN_FILE_BYTES + 1)
    if len(data) > MAX_CAMPAIGN_FILE_BYTES:
        raise ValueError("campaign task asset exceeds its byte cap")
    return data


def validate_task_provenance(directory: Path, spec: CpuSearchSpec | None = None) -> CpuTaskProvenance:
    """Recompute every generated byte in memory, rejecting aliases and relabeling."""
    provenance = CpuTaskProvenance.model_validate_json(_read(directory / "task.provenance.json"))
    expected, expected_provenance, payloads = _task_payloads(
        task_id=provenance.task_id, generation_seed=provenance.generation_seed, cases_per_role=provenance.cases_per_role,
    )
    if spec is not None and spec != expected:
        raise ValueError("task template differs from deterministic generation")
    for name, data in payloads.items():
        if _read(directory / name) != data:
            raise ValueError(f"task asset differs from deterministic generation: {name}")
    return expected_provenance


def build_cpu_campaign(
    directory: Path, *, task_seeds: tuple[int, ...] = PILOT_TASK_SEEDS,
    repeats: tuple[CpuCampaignRepeat, ...] | None = None, campaign_id: str = "cpu-search-campaign-v1",
    cases_per_role: int = 4,
) -> CpuCampaignSpec:
    """Pin a complete campaign schedule without inference or outcome selection."""
    from autocontext.weight_evolution.cpu_campaign import campaign_implementation_digest

    _fresh(directory)
    if not 2 <= len(task_seeds) <= 32 or len(set(task_seeds)) != len(task_seeds):
        raise ValueError("campaign requires 2..32 distinct task generation seeds")
    actual_repeats = PILOT_REPEATS if repeats is None else repeats
    descriptors: list[CpuCampaignTask] = []
    payloads_by_task: dict[str, dict[str, bytes]] = {}
    usage = {name: 0 for name in ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")}
    semantics: set[str] = set()
    for index, seed in enumerate(task_seeds, 1):
        task_id = f"task-{index:02}"
        template, provenance, payloads = _task_payloads(task_id=task_id, generation_seed=seed, cases_per_role=cases_per_role)
        if provenance.semantic_digest in semantics:
            raise ValueError("campaign repeats a generated semantic task instance")
        semantics.add(provenance.semantic_digest)
        payloads_by_task[task_id] = payloads
        for name, value in template.required_usage.items():
            usage[name] += value * len(actual_repeats) * 3
        task_lock = _identity(payloads["bundle.lock.json"])
        provenance_identity = _identity(payloads["task.provenance.json"])
        descriptors.append(CpuCampaignTask(task_id=task_id, generation_seed=seed,
                                            bundle_lock_sha256=task_lock["sha256"],
                                            bundle_lock_size_bytes=task_lock["size_bytes"],
                                            provenance_sha256=provenance_identity["sha256"],
                                            provenance_size_bytes=provenance_identity["size_bytes"]))
    spec = CpuCampaignSpec(
        campaign_id=campaign_id, tasks=tuple(descriptors), repeats=actual_repeats,
        budget=CpuCampaignBudget(max_runs=len(task_seeds) * len(actual_repeats) * 3,
                                 **{f"max_{name}": value for name, value in usage.items()}),
        implementation_digest=campaign_implementation_digest(),
    )
    encoded = _encode(spec.to_dict())
    lock = CpuCampaignLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded))
    directory.mkdir(parents=True)
    for task_id, payloads in payloads_by_task.items():
        task_directory = directory / "tasks" / task_id
        task_directory.mkdir(parents=True)
        for name, data in payloads.items():
            (task_directory / name).write_bytes(data)
    (directory / "campaign.json").write_bytes(encoded)
    (directory / "bundle.lock.json").write_bytes(_encode(lock.to_dict()))
    return spec
