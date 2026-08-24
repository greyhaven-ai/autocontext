"""Shared immutable contract helpers for the synthetic multi-workload study."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

MANIFEST_SCHEMA = "autocontext.synthetic-kernel-study-manifest/v1"
PROBLEM_SCHEMA = "autocontext.synthetic-kernel-study-problem/v1"
PROTOCOL_COMPATIBILITY_VERSION = "autocontext.kernel-protocol-compatibility/v1"
Role = Literal["primary", "confirmation"]


def digest(content: str | bytes) -> str:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return digest(encoded)


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("multi-workload manifest has an unsupported schema")
    workloads = payload.get("workloads")
    if not isinstance(workloads, list) or len(workloads) < 3:
        raise ValueError("multi-workload manifest requires at least three workloads")
    workload_ids = [item.get("workload_id") for item in workloads if isinstance(item, dict)]
    families = [item.get("workload_family") for item in workloads if isinstance(item, dict)]
    if len(workload_ids) != len(workloads) or len(set(workload_ids)) != len(workload_ids):
        raise ValueError("multi-workload manifest workload IDs must be unique")
    if len(set(families)) < 3:
        raise ValueError("multi-workload manifest requires at least three families")
    return payload


def workload_family_contract(workload: dict[str, Any]) -> dict[str, Any]:
    return {
        "workload_family": workload["workload_family"],
        "reference_identity": workload["reference_identity"],
        "reference_implementation": workload["reference_implementation"],
        "case_contract": [
            {
                "name": case["name"],
                "split": case["split"],
                "shape_class": case["shape_class"],
                "minimum_speedup_vs_incumbent": 0.98,
            }
            for case in workload["cases"]
        ],
    }


def workload_family_id(workload: dict[str, Any]) -> str:
    embedded = workload.get("workload_family_contract")
    contract = embedded if isinstance(embedded, dict) else workload_family_contract(workload)
    return canonical_digest(contract)


def problem_payload(
    workload: dict[str, Any],
    *,
    role: Role,
    environment: str = "synthetic-sm90",
) -> dict[str, Any]:
    if role not in {"primary", "confirmation"}:
        raise ValueError("problem role must be primary or confirmation")
    return {
        "schema_version": PROBLEM_SCHEMA,
        "problem_id": workload["problem_id"],
        "workload_id": workload["workload_id"],
        "workload_family": workload["workload_family"],
        "workload_family_contract": workload_family_contract(workload),
        "reference_identity": workload["reference_identity"],
        "role": role,
        "environment": environment,
        "seed_commitment": workload[f"{role}_seed_commitment"],
        "baseline_latency_ms": workload["baseline_latency_ms"],
        "protocol": workload["protocol"],
        "cases": workload["cases"],
    }


def protocol_payload(problem: dict[str, Any]) -> dict[str, Any]:
    cases = problem["cases"]
    protocol = problem["protocol"]
    return {
        "correctness_trials": len(cases),
        "hidden_trials": sum(case["split"] == "holdout" for case in cases),
        "warmup_runs": protocol["warmup_runs"],
        "timing_blocks": protocol["timing_blocks"],
        "calls_per_block": protocol["calls_per_block"],
        "atol": protocol["atol"],
        "rtol": protocol["rtol"],
        "seed_commitment": digest(problem["seed_commitment"]),
        "compatibility_version": PROTOCOL_COMPATIBILITY_VERSION,
    }


def candidate_source(candidate: dict[str, Any]) -> str:
    correct = ",".join(candidate["correct_families"])
    latencies = json.dumps(candidate["latency_ms"], sort_keys=True, separators=(",", ":"))
    tags = ",".join(candidate["strategy_tags"])
    return (
        f"# fake-correct-families: {correct}\n"
        f"# fake-family-latencies-ms: {latencies}\n"
        f"# fake-strategy-tags: {tags}\n"
        "def kernel_fn(*inputs):\n"
        "    return inputs[0]\n"
    )


def baseline_source(workloads: list[dict[str, Any]]) -> str:
    families = ",".join(workload["workload_family"] for workload in workloads)
    latencies = {workload["workload_family"]: float(workload["baseline_latency_ms"]) for workload in workloads}
    return candidate_source(
        {
            "correct_families": families.split(","),
            "latency_ms": latencies,
            "strategy_tags": ["pinned-reference-composition"],
        }
    )


def strategy_tags(source: str) -> tuple[str, ...]:
    prefix = "# fake-strategy-tags:"
    for line in source.splitlines():
        if line.startswith(prefix):
            return tuple(tag.strip() for tag in line[len(prefix) :].split(",") if tag.strip())
    return ()
