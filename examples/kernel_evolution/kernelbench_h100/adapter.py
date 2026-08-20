#!/usr/bin/env python3
"""Pinned CUDA adapter for the AutoContext kernel benchmark v2 contract.

This smoke adapter is intentionally narrow: it evaluates KernelBench v0.1
Level 1 problem 1 (stateless 4096x4096 square matrix multiplication).  A
candidate may expose either KernelBench's ``ModelNew`` or AutoKernel's
``kernel_fn`` entrypoint.  The incumbent may additionally expose ``Model`` so
the pinned PyTorch reference can serve as the initial baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path
from typing import Any

import torch

SCHEMA_VERSION = "autocontext.kernelbench-eval/v2"
PROTOCOL_COMPATIBILITY_VERSION = "autocontext.kernel-protocol-compatibility/v1"
WARMUP_RUNS = 3
TIMING_BLOCKS = 8
CALLS_PER_BLOCK = 10
ATOL = 1.0e-2
RTOL = 1.0e-2


@dataclass(frozen=True, slots=True)
class BenchmarkProfile:
    """Host-owned inputs and ordering for one benchmark protocol."""

    name: str
    correctness_seeds: tuple[int, ...]
    hidden_trials: int
    compile_seed: int
    timing_input_seed: int
    timing_order_seed: int | None = None


PRIMARY_PROFILE = BenchmarkProfile(
    name="primary-v1",
    correctness_seeds=(17011, 17027, 17041, 17053, 17077),
    hidden_trials=3,
    compile_seed=16001,
    timing_input_seed=18001,
)
CONFIRMATION_PROFILE = BenchmarkProfile(
    name="fresh-confirmation-v1",
    correctness_seeds=(27011, 27031, 27043, 27059, 27077),
    hidden_trials=3,
    compile_seed=26003,
    timing_input_seed=28001,
    timing_order_seed=29009,
)


def _digest(payload: bytes | str) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _digest(encoded)


def _timing_orders(profile: BenchmarkProfile, names: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    if profile.timing_order_seed is None:
        orders = tuple(names[index % len(names) :] + names[: index % len(names)] for index in range(TIMING_BLOCKS))
    else:
        generator = random.Random(profile.timing_order_seed)
        first_cycle = list(permutations(names))
        second_cycle = list(permutations(names))
        generator.shuffle(first_cycle)
        generator.shuffle(second_cycle)
        orders = tuple([*first_cycle, *second_cycle[: TIMING_BLOCKS - len(first_cycle)]])

    for name in names:
        for position in range(len(names)):
            appearances = sum(order[position] == name for order in orders)
            if appearances < 2:
                raise RuntimeError(f"unbalanced timing schedule: {name} occupies position {position} only {appearances} times")
    return orders


def _seed_commitment(profile: BenchmarkProfile) -> str:
    # Preserve the exact primary commitment used by verified_h100_result.json.
    if profile.timing_order_seed is None:
        return _digest(",".join(str(seed) for seed in profile.correctness_seeds))
    names = ("candidate_ms", "incumbent_ms", "reference_ms")
    material = {
        "profile": profile.name,
        "correctness_seeds": list(profile.correctness_seeds),
        "compile_seed": profile.compile_seed,
        "timing_input_seed": profile.timing_input_seed,
        "timing_order_seed": profile.timing_order_seed,
        "timing_orders": [list(order) for order in _timing_orders(profile, names)],
    }
    return _canonical_digest(material)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _load_module(path: Path, role: str):
    name = f"_autoctx_kernel_smoke_{role}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Python module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _FunctionModel(torch.nn.Module):
    def __init__(self, function) -> None:
        super().__init__()
        self._function = function

    def forward(self, *inputs):
        return self._function(*inputs)


def _build_model(module, init_inputs: list[Any], *, entrypoint: str):
    target = getattr(module, entrypoint, None)
    if target is None:
        raise AttributeError(f"module does not expose requested entrypoint {entrypoint!r}")
    if isinstance(target, type) and issubclass(target, torch.nn.Module):
        return target(*init_inputs).cuda().eval()
    if callable(target):
        return _FunctionModel(target).cuda().eval()
    raise TypeError(f"entrypoint {entrypoint!r} is not callable")


def _clone(value):
    if isinstance(value, torch.Tensor):
        return value.clone()
    if isinstance(value, list):
        return [_clone(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clone(item) for item in value)
    if isinstance(value, dict):
        return {key: _clone(item) for key, item in value.items()}
    return value


def _to_cuda(value):
    if isinstance(value, torch.Tensor):
        return value.cuda(non_blocking=False)
    if isinstance(value, list):
        return [_to_cuda(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_to_cuda(item) for item in value)
    if isinstance(value, dict):
        return {key: _to_cuda(item) for key, item in value.items()}
    return value


def _equal(left, right) -> bool:
    if isinstance(left, torch.Tensor) and isinstance(right, torch.Tensor):
        return bool(torch.equal(left, right))
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    return left == right


def _compare(output, expected) -> tuple[bool, float, float]:
    if isinstance(output, torch.Tensor) and isinstance(expected, torch.Tensor):
        if output.shape != expected.shape or output.dtype != expected.dtype:
            return False, float("inf"), float("inf")
        output_f = output.detach().float()
        expected_f = expected.detach().float()
        if not bool(torch.isfinite(output_f).all()) or not bool(torch.isfinite(expected_f).all()):
            return False, float("inf"), float("inf")
        absolute = (output_f - expected_f).abs()
        max_abs = float(absolute.max().item())
        denominator = expected_f.abs().clamp_min(torch.finfo(torch.float32).tiny)
        max_rel = float((absolute / denominator).max().item())
        return bool(torch.allclose(output_f, expected_f, atol=ATOL, rtol=RTOL)), max_abs, max_rel
    if isinstance(output, (list, tuple)) and isinstance(expected, (list, tuple)):
        if len(output) != len(expected):
            return False, float("inf"), float("inf")
        comparisons = [_compare(a, b) for a, b in zip(output, expected, strict=True)]
        return (
            all(item[0] for item in comparisons),
            max((item[1] for item in comparisons), default=0.0),
            max((item[2] for item in comparisons), default=0.0),
        )
    return False, float("inf"), float("inf")


def _driver_version() -> str:
    completed = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    driver = completed.stdout.splitlines()[0].strip()
    if not driver:
        raise RuntimeError("nvidia-smi returned an empty driver version")
    return driver


def _hardware(workload_family_id: str, workload_fingerprint: str) -> dict[str, Any]:
    properties = torch.cuda.get_device_properties(0)
    major, minor = torch.cuda.get_device_capability(0)
    try:
        import triton

        triton_version = triton.__version__
    except ImportError:
        triton_version = "unavailable"
    return {
        "backend": "cuda",
        "architecture": f"sm{major}{minor}",
        "device_name": properties.name,
        "runtime": f"cuda-{torch.version.cuda}",
        "driver": _driver_version(),
        "toolchain": (f"python-{platform.python_version()}/torch-{torch.__version__}/triton-{triton_version}"),
        "workload_family_id": workload_family_id,
        "workload_fingerprint": workload_fingerprint,
        "metadata": {
            "device_index": "0",
            "multiprocessors": str(properties.multi_processor_count),
        },
    }


def _inputs(get_inputs, seed: int):
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    return _to_cuda(get_inputs())


def _timed_call(model, inputs, calls: int) -> float:
    torch.cuda.synchronize()
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    start.record()
    with torch.inference_mode():
        for _ in range(calls):
            model(*inputs)
    end.record()
    end.synchronize()
    elapsed = float(start.elapsed_time(end)) / calls
    if not elapsed > 0:
        raise RuntimeError(f"CUDA event returned non-positive latency: {elapsed}")
    return elapsed


def _base_report(
    *,
    problem_id: str,
    artifact_identity_version: str,
    candidate_artifact_digest: str,
    incumbent_artifact_digest: str,
    candidate_source_digest: str,
    incumbent_source_digest: str,
    candidate_source_suffix: str,
    incumbent_source_suffix: str,
    candidate_entrypoint: str,
    incumbent_entrypoint: str,
    reference_digest: str,
    hardware: dict[str, Any],
    protocol: dict[str, Any],
    profile: BenchmarkProfile,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_status": "infrastructure_error",
        "failure_kind": "contract",
        "problem_id": problem_id,
        "artifact_identity_version": artifact_identity_version,
        "candidate_artifact_digest": candidate_artifact_digest,
        "incumbent_artifact_digest": incumbent_artifact_digest,
        "candidate_source_digest": candidate_source_digest,
        "incumbent_source_digest": incumbent_source_digest,
        "candidate_source_suffix": candidate_source_suffix,
        "incumbent_source_suffix": incumbent_source_suffix,
        "candidate_entrypoint": candidate_entrypoint,
        "incumbent_entrypoint": incumbent_entrypoint,
        "baseline_id": reference_digest,
        "hardware": hardware,
        "hardware_scope_id": _canonical_digest(hardware),
        "protocol": protocol,
        "compile": {
            "candidate_passed": False,
            "incumbent_passed": False,
            "candidate_compile_ms": None,
            "diagnostics": "evaluation did not start",
        },
        "correctness": None,
        "performance": None,
        "resources": {
            "candidate_peak_memory_bytes": None,
            "incumbent_peak_memory_bytes": None,
            "device_total_memory_bytes": torch.cuda.get_device_properties(0).total_memory,
        },
        "metadata": {
            "adapter": "autoctx-live-kernelbench-smoke/v1",
            "benchmark_profile": profile.name,
        },
    }


def main(profile: BenchmarkProfile = PRIMARY_PROFILE) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--incumbent", type=Path, required=True)
    parser.add_argument("--artifact-identity-version", required=True)
    parser.add_argument("--candidate-artifact-digest", required=True)
    parser.add_argument("--incumbent-artifact-digest", required=True)
    parser.add_argument("--candidate-source-digest", required=True)
    parser.add_argument("--incumbent-source-digest", required=True)
    parser.add_argument("--candidate-source-suffix", required=True)
    parser.add_argument("--incumbent-source-suffix", required=True)
    parser.add_argument("--candidate-entrypoint", required=True)
    parser.add_argument("--incumbent-entrypoint", required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--problem-id", required=True)
    parser.add_argument("--autokernel-root", type=Path, required=True)
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required; CPU fallback is intentionally forbidden")
    capability = torch.cuda.get_device_capability(0)
    device_name = torch.cuda.get_device_name(0)
    if capability != (9, 0) or "H100" not in device_name:
        raise SystemExit(f"this example requires an NVIDIA H100 (SM90); found {device_name!r} with capability {capability}")
    sys.path.insert(0, str(args.autokernel_root.resolve()))

    candidate_bytes = args.candidate.read_bytes()
    incumbent_bytes = args.incumbent.read_bytes()
    if _digest(candidate_bytes) != args.candidate_source_digest or _digest(incumbent_bytes) != args.incumbent_source_digest:
        raise SystemExit("runner-provided source digest does not match the exact staged source bytes")
    reference_bytes = args.reference.read_bytes()
    protocol = {
        "correctness_trials": len(profile.correctness_seeds),
        "hidden_trials": profile.hidden_trials,
        "warmup_runs": WARMUP_RUNS,
        "timing_blocks": TIMING_BLOCKS,
        "calls_per_block": CALLS_PER_BLOCK,
        "atol": ATOL,
        "rtol": RTOL,
        "seed_commitment": _seed_commitment(profile),
        "compatibility_version": PROTOCOL_COMPATIBILITY_VERSION,
    }
    workload = _digest(
        reference_bytes
        + json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + args.problem_id.encode("utf-8")
    )
    workload_family = _digest(reference_bytes + args.problem_id.encode("utf-8"))
    report = _base_report(
        problem_id=args.problem_id,
        artifact_identity_version=args.artifact_identity_version,
        candidate_artifact_digest=args.candidate_artifact_digest,
        incumbent_artifact_digest=args.incumbent_artifact_digest,
        candidate_source_digest=args.candidate_source_digest,
        incumbent_source_digest=args.incumbent_source_digest,
        candidate_source_suffix=args.candidate_source_suffix,
        incumbent_source_suffix=args.incumbent_source_suffix,
        candidate_entrypoint=args.candidate_entrypoint,
        incumbent_entrypoint=args.incumbent_entrypoint,
        reference_digest=_digest(reference_bytes),
        hardware=_hardware(workload_family, workload),
        protocol=protocol,
        profile=profile,
    )

    try:
        reference_module = _load_module(args.reference, "reference")
        init_inputs = list(reference_module.get_init_inputs())
        reference = reference_module.Model(*init_inputs).cuda().eval()
        if reference.state_dict():
            raise RuntimeError("this smoke adapter only accepts the stateless pinned Level 1 problem 1")

        incumbent_module = _load_module(args.incumbent, "incumbent")
        incumbent = _build_model(
            incumbent_module,
            init_inputs,
            entrypoint=args.incumbent_entrypoint,
        )
        report["compile"]["incumbent_passed"] = True

        candidate_module = _load_module(args.candidate, "candidate")
        candidate = _build_model(
            candidate_module,
            init_inputs,
            entrypoint=args.candidate_entrypoint,
        )

        compile_inputs = _inputs(reference_module.get_inputs, profile.compile_seed)
        torch.cuda.synchronize()
        compile_started = time.perf_counter()
        with torch.inference_mode():
            candidate(*_clone(compile_inputs))
        torch.cuda.synchronize()
        report["compile"]["candidate_compile_ms"] = (time.perf_counter() - compile_started) * 1000.0
        with torch.inference_mode():
            incumbent(*_clone(compile_inputs))
        torch.cuda.synchronize()
        report["compile"]["candidate_passed"] = True
        report["compile"]["diagnostics"] = ""
    except Exception as exc:
        report["evaluation_status"] = "candidate_error"
        report["failure_kind"] = "compile"
        report["compile"]["diagnostics"] = f"{type(exc).__name__}: {exc}"
        _write_report(args.report, report)
        return

    tests_passed = 0
    hidden_passed = 0
    max_abs_error = 0.0
    max_rel_error = 0.0
    finite_error_metrics = True
    input_mutation = False
    failures: list[str] = []
    incumbent_failed = False

    for index, seed in enumerate(profile.correctness_seeds):
        source_inputs = _inputs(reference_module.get_inputs, seed)
        candidate_inputs = _clone(source_inputs)
        incumbent_inputs = _clone(source_inputs)
        reference_inputs = _clone(source_inputs)
        candidate_before = _clone(candidate_inputs)
        with torch.inference_mode():
            expected = reference(*reference_inputs)
            incumbent_output = incumbent(*incumbent_inputs)
            candidate_output = candidate(*candidate_inputs)
        torch.cuda.synchronize()
        candidate_match, abs_error, rel_error = _compare(candidate_output, expected)
        incumbent_match, _, _ = _compare(incumbent_output, expected)
        mutated = not _equal(candidate_before, candidate_inputs)
        input_mutation = input_mutation or mutated
        if math.isfinite(abs_error) and math.isfinite(rel_error):
            max_abs_error = max(max_abs_error, abs_error)
            max_rel_error = max(max_rel_error, rel_error)
        else:
            finite_error_metrics = False
        passed = candidate_match and not mutated
        if passed:
            tests_passed += 1
            if index >= len(profile.correctness_seeds) - profile.hidden_trials:
                hidden_passed += 1
        else:
            failures.append(
                f"trial {index} failed: match={candidate_match}, input_mutation={mutated}, "
                f"max_abs={abs_error:.6g}, max_rel={rel_error:.6g}"
            )
        if not incumbent_match:
            incumbent_failed = True

    correctness_passed = (
        tests_passed == len(profile.correctness_seeds) and hidden_passed == profile.hidden_trials and not input_mutation
    )
    report["correctness"] = {
        "passed": correctness_passed,
        "tests_run": len(profile.correctness_seeds),
        "tests_passed": tests_passed,
        "hidden_tests_run": profile.hidden_trials,
        "hidden_tests_passed": hidden_passed,
        "max_abs_error": max_abs_error if finite_error_metrics else None,
        "max_rel_error": max_rel_error if finite_error_metrics else None,
        "parameter_state_match": True,
        "input_mutation_detected": input_mutation,
        "failures": failures,
    }
    if incumbent_failed:
        report["evaluation_status"] = "infrastructure_error"
        report["failure_kind"] = "reference_failure"
        report["compile"]["diagnostics"] = "incumbent failed pinned correctness trials"
        _write_report(args.report, report)
        return
    if not correctness_passed:
        report["evaluation_status"] = "candidate_error"
        report["failure_kind"] = "correctness"
        _write_report(args.report, report)
        return

    timing_inputs = _inputs(reference_module.get_inputs, profile.timing_input_seed)
    for model in (candidate, incumbent, reference):
        with torch.inference_mode():
            for _ in range(WARMUP_RUNS):
                model(*timing_inputs)
    torch.cuda.synchronize()

    models = {
        "candidate_ms": candidate,
        "incumbent_ms": incumbent,
        "reference_ms": reference,
    }
    names = tuple(models)
    blocks: list[dict[str, Any]] = []
    for block_index, order in enumerate(_timing_orders(profile, names)):
        block: dict[str, Any] = {"block": block_index}
        for name in order:
            block[name] = _timed_call(models[name], timing_inputs, CALLS_PER_BLOCK)
        blocks.append(block)

    report["evaluation_status"] = "complete"
    report["failure_kind"] = None
    report["performance"] = {"blocks": blocks}
    _write_report(args.report, report)


if __name__ == "__main__":
    main()
