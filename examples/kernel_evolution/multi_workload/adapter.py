"""Synthetic adapter proving the shared multi-workload evidence contract.

This adapter never claims accelerator performance.  It deterministically emits
the same strict report schema used by an operator-owned benchmark so CI can
exercise workload isolation, primary/confirmation evidence, and transfer gates.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from contract import (
    PROBLEM_SCHEMA,
    canonical_digest,
    digest,
    protocol_payload,
    workload_family_id,
)

SCHEMA_VERSION = "autocontext.kernelbench-eval/v3"


def _marker(source: str, name: str) -> str:
    prefix = f"# {name}:"
    for line in source.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _candidate_contract(source: str) -> tuple[set[str], dict[str, float]]:
    correct = {value.strip() for value in _marker(source, "fake-correct-families").split(",") if value.strip()}
    raw_latencies = _marker(source, "fake-family-latencies-ms")
    latencies = json.loads(raw_latencies)
    if not isinstance(latencies, dict):
        raise ValueError("candidate latency marker must be a JSON object")
    normalized: dict[str, float] = {}
    for family, value in latencies.items():
        latency = float(value)
        if not isinstance(family, str) or not family or not math.isfinite(latency) or latency <= 0:
            raise ValueError("candidate latency markers must be positive finite values")
        normalized[family] = latency
    return correct, normalized


def _hardware(problem: dict[str, Any]) -> dict[str, Any]:
    environment = problem["environment"]
    environments = {
        "synthetic-sm90": ("sm90-demo", "Synthetic H100", "demo-580"),
        "synthetic-sm100": ("sm100-demo", "Synthetic B200", "demo-590"),
    }
    if environment not in environments:
        raise ValueError("synthetic problem selected an unknown environment")
    architecture, device_name, driver = environments[environment]
    fingerprint_payload = {key: value for key, value in problem.items() if key != "environment"}
    return {
        "backend": "synthetic-cuda",
        "architecture": architecture,
        "device_name": device_name,
        "runtime": "cuda-demo-12.8",
        "driver": driver,
        "toolchain": "multi-workload-marker-adapter-v1",
        "workload_family_id": workload_family_id(problem),
        "workload_fingerprint": canonical_digest(fingerprint_payload),
        "metadata": {
            "environment": environment,
            "warning": "synthetic orchestration evidence; not a GPU measurement",
        },
    }


def _base_report(args: argparse.Namespace, problem: dict[str, Any]) -> dict[str, Any]:
    candidate_bytes = args.candidate.read_bytes()
    incumbent_bytes = args.incumbent.read_bytes()
    if digest(candidate_bytes) != args.candidate_source_digest:
        raise ValueError("candidate source digest does not match staged bytes")
    if digest(incumbent_bytes) != args.incumbent_source_digest:
        raise ValueError("incumbent source digest does not match staged bytes")
    hardware = _hardware(problem)
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_status": "candidate_error",
        "failure_kind": "contract",
        "problem_id": problem["problem_id"],
        "artifact_identity_version": args.artifact_identity_version,
        "candidate_artifact_digest": args.candidate_artifact_digest,
        "incumbent_artifact_digest": args.incumbent_artifact_digest,
        "candidate_source_digest": args.candidate_source_digest,
        "incumbent_source_digest": args.incumbent_source_digest,
        "candidate_source_suffix": args.candidate_source_suffix,
        "incumbent_source_suffix": args.incumbent_source_suffix,
        "candidate_entrypoint": args.candidate_entrypoint,
        "incumbent_entrypoint": args.incumbent_entrypoint,
        "baseline_id": digest(problem["reference_identity"]),
        "hardware": hardware,
        "hardware_scope_id": canonical_digest(hardware),
        "protocol": protocol_payload(problem),
        "compile": {
            "candidate_passed": True,
            "incumbent_passed": True,
            "candidate_compile_ms": 0.01,
            "diagnostics": "",
        },
        "correctness": None,
        "performance": None,
        "resources": {
            "candidate_artifact_digest": args.candidate_artifact_digest,
            "incumbent_artifact_digest": args.incumbent_artifact_digest,
            "candidate_peak_memory_bytes": 2_000_000,
            "incumbent_peak_memory_bytes": 2_000_000,
            "device_total_memory_bytes": 80_000_000_000,
        },
        "metadata": {
            "adapter": "synthetic-multi-workload/v1",
            "workload_id": problem["workload_id"],
            "workload_family": problem["workload_family"],
            "profile_role": problem["role"],
            "case_manifest": [
                {
                    "name": case["name"],
                    "split": case["split"],
                    "shape_class": case["shape_class"],
                }
                for case in problem["cases"]
            ],
            "warning": "synthetic orchestration evidence; not a GPU measurement",
        },
    }


def _correctness(problem: dict[str, Any], *, correct: bool) -> dict[str, Any]:
    cases = problem["cases"]
    slices = []
    for split in ("train", "holdout"):
        count = sum(case["split"] == split for case in cases)
        slices.append(
            {
                "name": split,
                "split": split,
                "cases_run": count,
                "cases_passed": count if correct else 0,
                "passed": correct,
            }
        )
    hidden = sum(case["split"] == "holdout" for case in cases)
    return {
        "passed": correct,
        "tests_run": len(cases),
        "tests_passed": len(cases) if correct else 0,
        "hidden_tests_run": hidden,
        "hidden_tests_passed": hidden if correct else 0,
        "max_abs_error": 0.0 if correct else 1.0,
        "max_rel_error": 0.0 if correct else 1.0,
        "parameter_state_match": True,
        "input_mutation_detected": False,
        "failures": [] if correct else ["synthetic required correctness slices failed"],
        "slices": slices,
    }


def _performance(
    problem: dict[str, Any],
    *,
    candidate_latency: float,
    incumbent_latency: float,
) -> dict[str, Any]:
    cases = problem["cases"]
    candidate_values = [candidate_latency * float(case["latency_scale"]) for case in cases]
    incumbent_values = [incumbent_latency * float(case["latency_scale"]) for case in cases]
    reference_values = [value * 1.25 for value in incumbent_values]
    blocks = [
        {
            "block": block,
            "candidate_ms": statistics.geometric_mean(candidate_values),
            "incumbent_ms": statistics.geometric_mean(incumbent_values),
            "reference_ms": statistics.geometric_mean(reference_values),
        }
        for block in range(int(problem["protocol"]["timing_blocks"]))
    ]
    case_reports = []
    for case, candidate_ms, incumbent_ms, reference_ms in zip(
        cases,
        candidate_values,
        incumbent_values,
        reference_values,
        strict=True,
    ):
        floor = 0.98
        case_reports.append(
            {
                "name": case["name"],
                "split": case["split"],
                "candidate_median_ms": candidate_ms,
                "incumbent_median_ms": incumbent_ms,
                "reference_median_ms": reference_ms,
                "minimum_speedup_vs_incumbent": floor,
                "passed_no_regression": incumbent_ms / candidate_ms >= floor,
            }
        )
    return {"blocks": blocks, "cases": case_reports}


def main() -> None:
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
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--problem", type=Path, required=True)
    args = parser.parse_args()

    problem = json.loads(args.problem.read_text(encoding="utf-8"))
    if not isinstance(problem, dict) or problem.get("schema_version") != PROBLEM_SCHEMA:
        raise SystemExit("synthetic problem has an unsupported schema")
    report = _base_report(args, problem)
    try:
        candidate_source = args.candidate.read_text(encoding="utf-8")
        incumbent_source = args.incumbent.read_text(encoding="utf-8")
        compile(candidate_source, str(args.candidate), "exec")
        compile(incumbent_source, str(args.incumbent), "exec")
        candidate_families, candidate_latencies = _candidate_contract(candidate_source)
        incumbent_families, incumbent_latencies = _candidate_contract(incumbent_source)
    except (SyntaxError, ValueError, json.JSONDecodeError) as exc:
        report["failure_kind"] = "syntax" if isinstance(exc, SyntaxError) else "contract"
        report["compile"]["candidate_passed"] = False
        report["compile"]["diagnostics"] = str(exc)
    else:
        family = problem["workload_family"]
        incumbent_correct = family in incumbent_families
        if not incumbent_correct or family not in incumbent_latencies:
            report["evaluation_status"] = "infrastructure_error"
            report["failure_kind"] = "reference_failure"
            report["compile"]["diagnostics"] = "pinned incumbent does not satisfy this workload"
        else:
            candidate_correct = family in candidate_families and family in candidate_latencies
            report["correctness"] = _correctness(problem, correct=candidate_correct)
            if candidate_correct:
                report["performance"] = _performance(
                    problem,
                    candidate_latency=candidate_latencies[family],
                    incumbent_latency=incumbent_latencies[family],
                )
                report["evaluation_status"] = "complete"
                report["failure_kind"] = None
            else:
                report["failure_kind"] = "correctness"

    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(args.report)


if __name__ == "__main__":
    main()
