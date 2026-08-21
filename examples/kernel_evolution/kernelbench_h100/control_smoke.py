#!/usr/bin/env python3
"""Exercise AutoContext's real external-kernel contract on a CUDA worker."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

PROBLEM_ID = "kernelbench-v0.1-level1-1-square-matmul-n4096"


def _load_contract_modules(source_root: Path):
    """Load the real models/benchmark modules without importing full AutoContext."""
    package_root = source_root / "autocontext"
    kernel_root = package_root / "kernel_evolution"
    for path in (kernel_root / "models.py", kernel_root / "benchmark.py"):
        if not path.exists():
            raise SystemExit(f"AutoContext kernel contract module not found: {path}")

    autocontext_package = types.ModuleType("autocontext")
    autocontext_package.__path__ = [str(package_root)]
    kernel_package = types.ModuleType("autocontext.kernel_evolution")
    kernel_package.__path__ = [str(kernel_root)]
    sys.modules["autocontext"] = autocontext_package
    sys.modules["autocontext.kernel_evolution"] = kernel_package

    loaded = []
    for short_name in ("models", "benchmark"):
        name = f"autocontext.kernel_evolution.{short_name}"
        spec = importlib.util.spec_from_file_location(name, kernel_root / f"{short_name}.py")
        if spec is None or spec.loader is None:
            raise SystemExit(f"cannot load {name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded[0], loaded[1]


def _promotion_decision(observation) -> dict[str, object]:
    if not observation.eligible:
        return {"promote": False, "decision": "rejected", "reason": observation.rejection_reason}
    assert observation.environment_drift_ratio is not None
    assert observation.relative_improvement is not None
    assert observation.speedup_lcb95 is not None
    assert observation.candidate_p95_ms is not None
    assert observation.incumbent_p95_ms is not None
    if observation.environment_drift_ratio > 0.10:
        return {"promote": False, "decision": "rejected", "reason": "unstable_environment"}
    if observation.relative_improvement + 1.0e-12 < 0.05:
        return {"promote": False, "decision": "rejected", "reason": "insufficient_improvement"}
    required_confident_speedup = 1.0 / (1.0 - 0.05)
    if observation.speedup_lcb95 + 1.0e-12 < required_confident_speedup:
        return {"promote": False, "decision": "rejected", "reason": "confidence_interval"}
    if observation.candidate_p95_ms > observation.incumbent_p95_ms * 1.05:
        return {"promote": False, "decision": "rejected", "reason": "tail_regression"}
    return {"promote": True, "decision": "promoted", "reason": "significant_improvement"}


def main() -> None:
    bundle = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--autokernel-root", type=Path, required=True)
    parser.add_argument(
        "--autocontext-src",
        type=Path,
        required=True,
        help="Path containing the autocontext Python package (normally autocontext/src)",
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        help="Candidate source; defaults to the bundled grouped SM90 Triton candidate",
    )
    parser.add_argument(
        "--adapter-python",
        type=Path,
        default=Path(sys.executable),
        help="Python with CUDA torch and Triton installed",
    )
    args = parser.parse_args()

    models_module, benchmark_module = _load_contract_modules(args.autocontext_src.resolve())
    ExternalKernelBenchmarkRunner = benchmark_module.ExternalKernelBenchmarkRunner
    KernelBenchmarkEvaluator = benchmark_module.KernelBenchmarkEvaluator
    KernelBenchmarkEvaluatorConfig = benchmark_module.KernelBenchmarkEvaluatorConfig
    KernelCandidate = models_module.KernelCandidate

    autokernel_root = args.autokernel_root.resolve()
    candidate_path = (args.candidate or (bundle / "tuned_candidate.py")).resolve()
    incumbent_path = autokernel_root / "kernel.py"
    reference_path = bundle / "reference.py"
    adapter_path = bundle / "adapter.py"

    for path in (candidate_path, incumbent_path, reference_path, adapter_path, args.adapter_python):
        if not path.exists():
            raise SystemExit(f"required path does not exist: {path}")

    candidate = KernelCandidate(source=candidate_path.read_text(encoding="utf-8"), entrypoint="kernel_fn")
    incumbent = KernelCandidate(source=incumbent_path.read_text(encoding="utf-8"), entrypoint="kernel_fn")
    external = ExternalKernelBenchmarkRunner(
        [
            os.path.abspath(os.fspath(args.adapter_python)),
            str(adapter_path),
            "--candidate",
            "{candidate}",
            "--incumbent",
            "{incumbent}",
            "--artifact-identity-version",
            "{artifact_identity_version}",
            "--candidate-artifact-digest",
            "{candidate_artifact_digest}",
            "--incumbent-artifact-digest",
            "{incumbent_artifact_digest}",
            "--candidate-source-digest",
            "{candidate_source_digest}",
            "--incumbent-source-digest",
            "{incumbent_source_digest}",
            "--candidate-source-suffix",
            "{candidate_source_suffix}",
            "--incumbent-source-suffix",
            "{incumbent_source_suffix}",
            "--candidate-entrypoint",
            "{candidate_entrypoint}",
            "--incumbent-entrypoint",
            "{incumbent_entrypoint}",
            "--reference",
            str(reference_path),
            "--report",
            "{report}",
            "--problem-id",
            PROBLEM_ID,
            "--autokernel-root",
            str(autokernel_root),
        ],
        cwd=autokernel_root,
        source_suffix=".py",
        trusted_unsafe=True,
        immutable_paths=[adapter_path, reference_path],
        max_output_bytes=64_000,
        max_report_bytes=2_000_000,
    )
    evaluator = KernelBenchmarkEvaluator(
        external,
        KernelBenchmarkEvaluatorConfig(
            problem_id=PROBLEM_ID,
            timeout_seconds=240.0,
            min_timing_blocks=8,
            bootstrap_samples=1_000,
            require_resource_telemetry=True,
        ),
    )
    baseline = evaluator.evaluate(incumbent, incumbent)
    if not baseline.eligible:
        print(baseline.model_dump_json(indent=2))
        raise SystemExit(f"baseline failed: {baseline.rejection_reason}")

    assert baseline.hardware_scope_id is not None
    assert baseline.baseline_id is not None
    assert baseline.protocol_id is not None
    assert baseline.protocol_compatibility_id is not None
    observation = evaluator.evaluate(
        candidate,
        incumbent,
        expected_scope_id=baseline.hardware_scope_id,
        expected_baseline_id=baseline.baseline_id,
        expected_protocol_id=baseline.protocol_id,
    )
    decision = _promotion_decision(observation)
    summary = {
        "problem_id": PROBLEM_ID,
        "artifact_identity_version": candidate.artifact_identity_version,
        "baseline": {
            "eligible": baseline.eligible,
            "artifact_digest": incumbent.artifact_digest,
            "source_digest": incumbent.source_digest,
            "median_ms": baseline.candidate_median_ms,
            "hardware_scope_id": baseline.hardware_scope_id,
            "baseline_id": baseline.baseline_id,
            "protocol_id": baseline.protocol_id,
            "protocol_compatibility_id": baseline.protocol_compatibility_id,
        },
        "candidate": {
            "path": str(candidate_path),
            "eligible": observation.eligible,
            "artifact_digest": candidate.artifact_digest,
            "source_digest": candidate.source_digest,
            "median_ms": observation.candidate_median_ms,
            "incumbent_median_ms": observation.incumbent_median_ms,
            "reference_median_ms": observation.reference_median_ms,
            "speedup_vs_incumbent": observation.speedup_vs_incumbent,
            "speedup_vs_reference": observation.speedup_vs_reference,
            "speedup_lcb95": observation.speedup_lcb95,
            "relative_improvement": observation.relative_improvement,
            "environment_drift_ratio": observation.environment_drift_ratio,
            "rejection_reason": observation.rejection_reason,
            "feedback": observation.feedback,
        },
        "promotion": decision,
        "report": observation.report.model_dump(mode="json") if observation.report is not None else None,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not decision["promote"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
