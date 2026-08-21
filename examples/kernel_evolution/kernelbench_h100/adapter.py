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
import statistics
import subprocess
import sys
import time
import uuid
from itertools import permutations
from pathlib import Path
from typing import Any

import torch
from profile_contract import PROFILE_NAMES, PROFILES, load_private_plan

SCHEMA_VERSION = "autocontext.kernelbench-eval/v2"
PROTOCOL_COMPATIBILITY_VERSION = "autocontext.kernel-protocol-compatibility/v1"
WARMUP_RUNS = 3
TIMING_BLOCKS = 8
CALLS_PER_BLOCK = 10


def _digest(payload: bytes | str) -> str:
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _digest(encoded)


def _timing_orders(seed_material: str, names: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    generator = random.Random(int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16))
    cycles = [list(permutations(names)) for _ in range(math.ceil(TIMING_BLOCKS / math.factorial(len(names))))]
    for cycle in cycles:
        generator.shuffle(cycle)
    orders = tuple(order for cycle in cycles for order in cycle)[:TIMING_BLOCKS]

    for name in names:
        for position in range(len(names)):
            appearances = sum(order[position] == name for order in orders)
            if appearances < 2:
                raise RuntimeError(f"unbalanced timing schedule: {name} occupies position {position} only {appearances} times")
    return orders


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


def _compare(output, expected, *, atol: float, rtol: float) -> tuple[bool, float, float]:
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
        return bool(torch.allclose(output_f, expected_f, atol=atol, rtol=rtol)), max_abs, max_rel
    if isinstance(output, (list, tuple)) and isinstance(expected, (list, tuple)):
        if len(output) != len(expected):
            return False, float("inf"), float("inf")
        comparisons = [_compare(a, b, atol=atol, rtol=rtol) for a, b in zip(output, expected, strict=True)]
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
            "device_grant": os.environ.get("AUTOCONTEXT_GPU_DEVICE_ID", "unsafe-local-device-0"),
            "multiprocessors": str(properties.multi_processor_count),
        },
    }


def _case_tensor(rows: int, columns: int, case: dict[str, Any], *, layout: str, salt: int):
    generator = torch.Generator(device="cpu")
    generator.manual_seed(case["seed"] + salt)
    storage_shape = (columns, rows) if layout == "transposed" else (rows, columns)
    value_class = case["value_class"]
    minimum = float(case["magnitude_min"])
    maximum = float(case["magnitude_max"])
    if value_class == "positive-unit":
        values = torch.rand(storage_shape, generator=generator, dtype=torch.float32)
    elif value_class == "dynamic-range":
        exponents = torch.rand(storage_shape, generator=generator, dtype=torch.float32)
        exponents = math.log10(minimum) + exponents * (math.log10(maximum) - math.log10(minimum))
        signs = torch.where(
            torch.rand(storage_shape, generator=generator) < 0.5,
            torch.tensor(-1.0),
            torch.tensor(1.0),
        )
        values = signs * torch.pow(10.0, exponents)
    else:
        values = torch.rand(storage_shape, generator=generator, dtype=torch.float32) * 2.0 - 1.0
        values *= maximum
        if value_class == "small":
            values *= minimum / maximum
        elif value_class == "cancellation":
            values.reshape(-1)[1::2].neg_()
    return values.t().cuda() if layout == "transposed" else values.cuda()


def _apply_layout(values, layout: str):
    if layout == "transposed":
        return values.t().contiguous().t().cuda()
    return values.contiguous().cuda()


def _case_inputs(case: dict[str, Any]):
    if case["value_class"] == "cancellation":
        generator = torch.Generator(device="cpu")
        generator.manual_seed(case["seed"])
        pairs = case["k"] // 2
        maximum = float(case["magnitude_max"])
        a_base = (torch.rand((case["m"], pairs), generator=generator) * 2.0 - 1.0) * maximum
        b_base = (torch.rand((pairs, case["n"]), generator=generator) * 2.0 - 1.0) * maximum
        a = torch.repeat_interleave(a_base, 2, dim=1)
        a[:, 1::2] *= 1.0003
        b = torch.repeat_interleave(b_base, 2, dim=0)
        b[1::2].neg_()
        return [_apply_layout(a, case["a_layout"]), _apply_layout(b, case["b_layout"])]
    return [
        _case_tensor(case["m"], case["k"], case, layout=case["a_layout"], salt=0),
        _case_tensor(case["k"], case["n"], case, layout=case["b_layout"], salt=1_000_003),
    ]


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


def _cuda_peaks(model, inputs) -> tuple[int, int]:
    """Measure one identity's allocation window independently."""

    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    with torch.inference_mode():
        model(*_clone(inputs))
    torch.cuda.synchronize()
    return int(torch.cuda.max_memory_allocated()), int(torch.cuda.max_memory_reserved())


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
    profile_name: str,
    role: str,
    public_case_manifest: list[dict[str, str]],
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
            "candidate_artifact_digest": candidate_artifact_digest,
            "incumbent_artifact_digest": incumbent_artifact_digest,
            "candidate_peak_allocated_bytes": None,
            "candidate_peak_reserved_bytes": None,
            "incumbent_peak_allocated_bytes": None,
            "incumbent_peak_reserved_bytes": None,
            "candidate_peak_memory_bytes": None,
            "incumbent_peak_memory_bytes": None,
            "device_total_memory_bytes": torch.cuda.get_device_properties(0).total_memory,
        },
        "metadata": {
            "adapter": "autoctx-live-kernelbench-smoke/v1",
            "benchmark_profile": profile_name,
            "profile_role": role,
            "case_manifest": public_case_manifest,
        },
    }


def main(role: str = "primary") -> None:
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
    parser.add_argument("--precision-profile", choices=PROFILE_NAMES, required=True)
    parser.add_argument("--private-plan", type=Path, required=True)
    parser.add_argument("--plan-commitment", required=True)
    parser.add_argument("--proposal-cap", type=int, required=True)
    parser.add_argument("--familywise-alpha", type=float, required=True)
    args = parser.parse_args()

    if role not in {"primary", "confirmation"}:
        raise SystemExit("adapter role must be primary or confirmation")
    if not 1 <= args.proposal_cap <= 10_000:
        raise SystemExit("proposal cap must be between 1 and 10000")
    if not math.isfinite(args.familywise_alpha) or not 0 < args.familywise_alpha < 0.5:
        raise SystemExit("familywise alpha must be in (0, 0.5)")
    profile = PROFILES[args.precision_profile]
    private_plan = load_private_plan(
        args.private_plan,
        profile_name=profile.name,
        role=role,
        expected_commitment=args.plan_commitment,
    )
    cases = private_plan["cases"]

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required; CPU fallback is intentionally forbidden")
    capability = torch.cuda.get_device_capability(0)
    device_name = torch.cuda.get_device_name(0)
    if capability != (9, 0) or "H100" not in device_name:
        raise SystemExit(f"this example requires an NVIDIA H100 (SM90); found {device_name!r} with capability {capability}")
    sys.path.insert(0, str(args.autokernel_root.resolve()))
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.use_deterministic_algorithms(True)

    candidate_bytes = args.candidate.read_bytes()
    incumbent_bytes = args.incumbent.read_bytes()
    if _digest(candidate_bytes) != args.candidate_source_digest or _digest(incumbent_bytes) != args.incumbent_source_digest:
        raise SystemExit("runner-provided source digest does not match the exact staged source bytes")
    reference_bytes = args.reference.read_bytes()
    protocol = {
        "correctness_trials": len(cases),
        "hidden_trials": sum(case["split"] == "holdout" for case in cases),
        "warmup_runs": WARMUP_RUNS,
        "timing_blocks": TIMING_BLOCKS,
        "calls_per_block": CALLS_PER_BLOCK,
        "atol": profile.atol,
        "rtol": profile.rtol,
        "seed_commitment": args.plan_commitment,
        "compatibility_version": PROTOCOL_COMPATIBILITY_VERSION,
        "semantics": profile.semantics(),
        "sequential_testing": {
            "method": "bonferroni",
            "proposal_cap": args.proposal_cap,
            "familywise_alpha": args.familywise_alpha,
        },
    }
    workload = _digest(
        reference_bytes
        + json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + args.problem_id.encode("utf-8")
    )
    workload_family = _digest(
        reference_bytes
        + json.dumps(profile.semantics(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        + args.problem_id.encode("utf-8")
    )
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
        profile_name=profile.name,
        role=role,
        public_case_manifest=[{"name": case["name"], "split": case["split"]} for case in cases],
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

        compile_inputs = _case_inputs(next(case for case in cases if case["split"] == "train"))
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
        report["failure_kind"] = "oom" if isinstance(exc, torch.cuda.OutOfMemoryError) else "compile"
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

    slice_results: list[dict[str, Any]] = []
    for case in cases:
        source_inputs = _case_inputs(case)
        candidate_inputs = _clone(source_inputs)
        incumbent_inputs = _clone(source_inputs)
        reference_inputs = _clone(source_inputs)
        candidate_before = _clone(candidate_inputs)
        try:
            with torch.inference_mode():
                expected = reference(*reference_inputs)
                incumbent_output = incumbent(*incumbent_inputs)
                candidate_output = candidate(*candidate_inputs)
        except torch.cuda.OutOfMemoryError as exc:
            report["evaluation_status"] = "candidate_error"
            report["failure_kind"] = "oom"
            report["compile"]["diagnostics"] = f"CUDA OOM during correctness: {exc}"
            _write_report(args.report, report)
            return
        torch.cuda.synchronize()
        candidate_match, abs_error, rel_error = _compare(
            candidate_output,
            expected,
            atol=profile.atol,
            rtol=profile.rtol,
        )
        incumbent_match, _, _ = _compare(
            incumbent_output,
            expected,
            atol=profile.atol,
            rtol=profile.rtol,
        )
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
            if case["split"] == "holdout":
                hidden_passed += 1
        else:
            failures.append(
                f"case {case['name']} ({case['split']}) failed: match={candidate_match}, input_mutation={mutated}, "
                f"max_abs={abs_error:.6g}, max_rel={rel_error:.6g}"
            )
        if not incumbent_match:
            incumbent_failed = True
        slice_results.append(
            {
                "name": case["name"],
                "split": case["split"],
                "cases_run": 1,
                "cases_passed": int(passed),
                "passed": passed,
            }
        )

    correctness_passed = (
        tests_passed == len(cases) and hidden_passed == sum(case["split"] == "holdout" for case in cases) and not input_mutation
    )
    report["correctness"] = {
        "passed": correctness_passed,
        "tests_run": len(cases),
        "tests_passed": tests_passed,
        "hidden_tests_run": sum(case["split"] == "holdout" for case in cases),
        "hidden_tests_passed": hidden_passed,
        "max_abs_error": max_abs_error if finite_error_metrics else None,
        "max_rel_error": max_rel_error if finite_error_metrics else None,
        "parameter_state_match": True,
        "input_mutation_detected": input_mutation,
        "failures": failures,
        "slices": slice_results,
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

    timing_cases = {case["name"]: (case, _case_inputs(case)) for case in cases}
    try:
        candidate_allocated = candidate_reserved = 0
        incumbent_allocated = incumbent_reserved = 0
        for _case, timing_inputs in timing_cases.values():
            allocated, reserved = _cuda_peaks(candidate, timing_inputs)
            candidate_allocated = max(candidate_allocated, allocated)
            candidate_reserved = max(candidate_reserved, reserved)
            allocated, reserved = _cuda_peaks(incumbent, timing_inputs)
            incumbent_allocated = max(incumbent_allocated, allocated)
            incumbent_reserved = max(incumbent_reserved, reserved)
            for model in (candidate, incumbent, reference):
                with torch.inference_mode():
                    for _ in range(WARMUP_RUNS):
                        model(*timing_inputs)
        torch.cuda.synchronize()
        report["resources"].update(
            {
                "candidate_peak_allocated_bytes": candidate_allocated,
                "candidate_peak_reserved_bytes": candidate_reserved,
                "incumbent_peak_allocated_bytes": incumbent_allocated,
                "incumbent_peak_reserved_bytes": incumbent_reserved,
                "candidate_peak_memory_bytes": candidate_reserved,
                "incumbent_peak_memory_bytes": incumbent_reserved,
            }
        )
    except torch.cuda.OutOfMemoryError as exc:
        report["evaluation_status"] = "candidate_error"
        report["failure_kind"] = "oom"
        report["compile"]["diagnostics"] = f"CUDA OOM during resource/warmup measurement: {exc}"
        _write_report(args.report, report)
        return

    models = {
        "candidate_ms": candidate,
        "incumbent_ms": incumbent,
        "reference_ms": reference,
    }
    names = tuple(models)
    blocks: list[dict[str, Any]] = []
    per_case_blocks: dict[str, dict[str, list[float]]] = {name: {model_name: [] for model_name in names} for name in timing_cases}
    model_orders = _timing_orders(args.plan_commitment, names)
    case_order = private_plan["timing_order"]
    try:
        for block_index, order in enumerate(model_orders):
            block: dict[str, Any] = {"block": block_index}
            aggregated: dict[str, list[float]] = {name: [] for name in names}
            rotated_cases = case_order[block_index % len(case_order) :] + case_order[: block_index % len(case_order)]
            for case_name in rotated_cases:
                _, timing_inputs = timing_cases[case_name]
                for name in order:
                    elapsed = _timed_call(models[name], timing_inputs, CALLS_PER_BLOCK)
                    per_case_blocks[case_name][name].append(elapsed)
                    aggregated[name].append(elapsed)
            for name in names:
                block[name] = statistics.geometric_mean(aggregated[name])
            blocks.append(block)
    except torch.cuda.OutOfMemoryError as exc:
        report["evaluation_status"] = "candidate_error"
        report["failure_kind"] = "oom"
        report["compile"]["diagnostics"] = f"CUDA OOM during timing: {exc}"
        _write_report(args.report, report)
        return

    case_performance = []
    for case_name in case_order:
        case, _ = timing_cases[case_name]
        values = per_case_blocks[case_name]
        candidate_median = statistics.median(values["candidate_ms"])
        incumbent_median = statistics.median(values["incumbent_ms"])
        floor = 0.98
        case_performance.append(
            {
                "name": case_name,
                "split": case["split"],
                "candidate_median_ms": candidate_median,
                "incumbent_median_ms": incumbent_median,
                "reference_median_ms": statistics.median(values["reference_ms"]),
                "minimum_speedup_vs_incumbent": floor,
                "passed_no_regression": incumbent_median / candidate_median + 1.0e-12 >= floor,
            }
        )

    report["evaluation_status"] = "complete"
    report["failure_kind"] = None
    report["performance"] = {"blocks": blocks, "cases": case_performance}
    _write_report(args.report, report)


if __name__ == "__main__":
    main()
