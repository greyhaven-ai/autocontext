"""Synthetic external adapter implementing autocontext.kernelbench-eval/v3.

This is deliberately not a performance benchmark. It makes the subprocess
boundary and JSON contract runnable on a laptop; replace it with a pinned GPU
worker that owns KernelBench inputs, compilation, and interleaved CUDA timing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "autocontext.kernelbench-eval/v3"
PROTOCOL_COMPATIBILITY_VERSION = "autocontext.kernel-protocol-compatibility/v1"


def digest(content: str | bytes) -> str:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return digest(encoded)


def marker(source: str, name: str) -> str | None:
    match = re.search(rf"^# {re.escape(name)}:\s*(.+?)\s*$", source, flags=re.MULTILINE)
    return match.group(1) if match else None


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

    candidate_bytes = args.candidate.read_bytes()
    incumbent_bytes = args.incumbent.read_bytes()
    if digest(candidate_bytes) != args.candidate_source_digest or digest(incumbent_bytes) != args.incumbent_source_digest:
        raise SystemExit("runner-provided source digest does not match the exact staged source bytes")
    candidate_source = candidate_bytes.decode("utf-8")
    incumbent_source = incumbent_bytes.decode("utf-8")
    problem_bytes = args.problem.read_bytes()
    problem = json.loads(problem_bytes)
    workload_family = {key: value for key, value in problem.items() if key != "seed_commitment"}
    hardware = {
        "backend": "synthetic-cuda",
        "architecture": "sm90-demo",
        "device_name": "Synthetic H100",
        "runtime": "cuda-demo-12.8",
        "driver": "demo-580",
        "toolchain": "python-marker-adapter-v1",
        "workload_family_id": canonical_digest(workload_family),
        "workload_fingerprint": digest(problem_bytes),
        "metadata": {"warning": "orchestration demo; not a GPU measurement"},
    }
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_status": "complete",
        "failure_kind": None,
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
        "baseline_id": digest(problem["reference_id"]),
        "hardware": hardware,
        "hardware_scope_id": canonical_digest(hardware),
        "protocol": {
            "correctness_trials": problem["correctness_trials"],
            "hidden_trials": problem["hidden_trials"],
            "warmup_runs": 3,
            "timing_blocks": problem["timing_blocks"],
            "calls_per_block": problem["calls_per_block"],
            "atol": 0.01,
            "rtol": 0.01,
            "seed_commitment": digest(problem["seed_commitment"]),
            "compatibility_version": PROTOCOL_COMPATIBILITY_VERSION,
        },
        "compile": {
            "candidate_passed": True,
            "incumbent_passed": True,
            "candidate_compile_ms": 0.0,
            "diagnostics": "",
        },
        "correctness": None,
        "performance": None,
        "resources": {
            "candidate_peak_memory_bytes": 1_000_000,
            "incumbent_peak_memory_bytes": 1_000_000,
            "device_total_memory_bytes": 80_000_000_000,
        },
        "metadata": {"adapter": "synthetic"},
    }

    try:
        compile(candidate_source, str(args.candidate), "exec")
        compile(incumbent_source, str(args.incumbent), "exec")
    except SyntaxError as exc:
        report["evaluation_status"] = "candidate_error"
        report["failure_kind"] = "syntax"
        report["compile"]["candidate_passed"] = False
        report["compile"]["diagnostics"] = str(exc)
    else:
        correct = marker(candidate_source, "fake-kernel-correct") == "true"
        trials = int(problem["correctness_trials"])
        hidden = int(problem["hidden_trials"])
        report["correctness"] = {
            "passed": correct,
            "tests_run": trials,
            "tests_passed": trials if correct else trials - 1,
            "hidden_tests_run": hidden,
            "hidden_tests_passed": hidden if correct else hidden - 1,
            "max_abs_error": 0.0 if correct else 1.0,
            "max_rel_error": 0.0 if correct else 1.0,
            "parameter_state_match": True,
            "input_mutation_detected": False,
            "failures": [] if correct else ["synthetic hidden holdout failed"],
        }
        if not correct:
            report["evaluation_status"] = "candidate_error"
            report["failure_kind"] = "correctness"
        else:
            candidate_ms = float(marker(candidate_source, "fake-kernel-latency-ms") or "nan")
            incumbent_ms = float(marker(incumbent_source, "fake-kernel-latency-ms") or "nan")
            reference_ms = float(problem["reference_latency_ms"])
            report["performance"] = {
                "blocks": [
                    {
                        "block": block,
                        "candidate_ms": candidate_ms,
                        "incumbent_ms": incumbent_ms,
                        "reference_ms": reference_ms,
                    }
                    for block in range(int(problem["timing_blocks"]))
                ]
            }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(args.report)


if __name__ == "__main__":
    main()
