"""Shared immutable contract helpers for the synthetic multi-workload study."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

MANIFEST_SCHEMA = "autocontext.synthetic-kernel-study-manifest/v1"
PROBLEM_SCHEMA = "autocontext.synthetic-kernel-study-problem/v1"
PROTOCOL_COMPATIBILITY_VERSION = "autocontext.kernel-protocol-compatibility/v1"
EVIDENCE_ORIGIN: Literal["synthetic"] = "synthetic"
SYNTHETIC_BACKEND_IDENTITY = "synthetic-multi-workload-marker-adapter/v1"
Role = Literal["primary", "confirmation"]
_WORKLOAD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


def digest(content: str | bytes) -> str:
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return digest(encoded)


def validated_workload_id(value: object) -> str:
    if not isinstance(value, str) or _WORKLOAD_ID.fullmatch(value) is None:
        raise ValueError("workload IDs must be lowercase safe path components")
    return value


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
    for workload_id in workload_ids:
        validated_workload_id(workload_id)
    if len(set(families)) < 3:
        raise ValueError("multi-workload manifest requires at least three families")
    if payload.get("evidence_origin") != EVIDENCE_ORIGIN:
        raise ValueError("the checked-in conformance manifest must identify synthetic evidence")
    warning = payload.get("warning")
    if not isinstance(warning, str) or not warning.strip():
        raise ValueError("the synthetic conformance manifest requires a conspicuous warning")
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


def shape_profile_id(workload: dict[str, Any]) -> str:
    """Bind only the family-independent public shape/layout contract."""
    return canonical_digest(
        {
            "schema_version": "autocontext.synthetic-shape-profile/v1",
            "shape_classes": sorted(case["shape_class"] for case in workload["cases"]),
        }
    )


def hardware_payload(problem: dict[str, Any]) -> dict[str, Any]:
    """Build the exact synthetic hardware identity pinned by a problem contract."""
    environment = problem["environment"]
    environments = {
        "synthetic-sm90": ("sm90-demo", "Synthetic H100", "demo-580"),
        "synthetic-sm100": ("sm100-demo", "Synthetic B200", "demo-590"),
    }
    if environment not in environments:
        raise ValueError("synthetic problem selected an unknown environment")
    architecture, device_name, driver = environments[environment]
    fingerprint_payload = {
        "schema_version": "autocontext.synthetic-workload-fingerprint/v1",
        "problem_id": problem["problem_id"],
        "workload_id": problem["workload_id"],
        "workload_family_contract": problem["workload_family_contract"],
        "shape_profile_id": problem["shape_profile_id"],
        "reference_identity": problem["reference_identity"],
        "baseline_latency_ms": problem["baseline_latency_ms"],
        "protocol": problem["protocol"],
        "cases": problem["cases"],
    }
    return {
        "backend": "synthetic-cuda",
        "architecture": architecture,
        "device_name": device_name,
        "runtime": "cuda-demo-12.8",
        "driver": driver,
        "toolchain": SYNTHETIC_BACKEND_IDENTITY,
        "workload_family_id": workload_family_id(problem),
        "workload_fingerprint": canonical_digest(fingerprint_payload),
        "metadata": {
            "environment": environment,
            "evidence_origin": problem["evidence_origin"],
            "study_execution_id": problem["study_execution_id"],
            "study_manifest_digest": problem["study_manifest_digest"],
            "study_contract_digest": problem["study_contract_digest"],
            "study_backend_identity": problem["study_backend_identity"],
            "evidence_warning": problem["evidence_warning"],
        },
    }


def problem_payload(
    workload: dict[str, Any],
    *,
    role: Role,
    environment: str = "synthetic-sm90",
    seed_commitment: str | None = None,
    evidence_purpose: str = "campaign",
    source_workload_id: str | None = None,
    study_execution_id: str,
    study_manifest_digest: str,
    study_contract_digest: str,
    study_backend_identity: str,
    evidence_warning: str,
) -> dict[str, Any]:
    if role not in {"primary", "confirmation"}:
        raise ValueError("problem role must be primary or confirmation")
    return {
        "schema_version": PROBLEM_SCHEMA,
        "problem_id": workload["problem_id"],
        "workload_id": workload["workload_id"],
        "workload_family": workload["workload_family"],
        "workload_family_contract": workload_family_contract(workload),
        "shape_profile_id": shape_profile_id(workload),
        "reference_identity": workload["reference_identity"],
        "role": role,
        "environment": environment,
        "seed_commitment": seed_commitment or workload[f"{role}_seed_commitment"],
        "evidence_purpose": evidence_purpose,
        "source_workload_id": source_workload_id or workload["workload_id"],
        "target_workload_id": workload["workload_id"],
        "evidence_origin": EVIDENCE_ORIGIN,
        "study_execution_id": study_execution_id,
        "study_manifest_digest": study_manifest_digest,
        "study_contract_digest": study_contract_digest,
        "study_backend_identity": study_backend_identity,
        "evidence_warning": evidence_warning,
        "baseline_latency_ms": workload["baseline_latency_ms"],
        "protocol": workload["protocol"],
        "cases": workload["cases"],
    }


def reserved_seed_commitment(
    workload: dict[str, Any],
    *,
    role: Role,
    evidence_purpose: str,
    source_workload_id: str,
    environment: str,
    study_execution_id: str,
    study_manifest_digest: str,
    study_contract_digest: str,
    study_backend_identity: str,
) -> str:
    """Derive one immutable plan commitment for one non-campaign study look."""
    if not evidence_purpose.strip():
        raise ValueError("evidence purpose must not be empty")
    return canonical_digest(
        {
            "kind": "autocontext.synthetic-kernel-study-plan/v1",
            "base_commitment": workload[f"{role}_seed_commitment"],
            "role": role,
            "evidence_purpose": evidence_purpose,
            "source_workload_id": source_workload_id,
            "target_workload_id": workload["workload_id"],
            "environment": environment,
            "study_execution_id": study_execution_id,
            "study_manifest_digest": study_manifest_digest,
            "study_contract_digest": study_contract_digest,
            "study_backend_identity": study_backend_identity,
        }
    )


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
