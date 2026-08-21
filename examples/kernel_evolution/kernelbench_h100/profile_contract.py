"""Public H100 precision profiles and private-plan commitment validation.

Exact seeds, shapes, ranges, and case order live only in a worker-supplied JSON
plan.  Campaign artifacts publish the canonical digest returned here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

STRICT_PROFILE = "strict-fp32-v1"
RELAXED_PROFILE = "relaxed-precision-v1"
PROFILE_NAMES = (STRICT_PROFILE, RELAXED_PROFILE)
PLAN_SCHEMA = "autocontext.kernel-private-plan/v1"
GPU_ATTESTATION_ENV = {
    "device_grant": "AUTOCONTEXT_GPU_DEVICE_ID",
    "device_isolation_kind": "AUTOCONTEXT_GPU_ISOLATION_KIND",
    "device_enforced_memory_bytes": "AUTOCONTEXT_GPU_ENFORCED_MEMORY_BYTES",
    "device_attestor_id": "AUTOCONTEXT_GPU_ATTESTOR_ID",
    "device_attestation_digest": "AUTOCONTEXT_GPU_ATTESTATION_DIGEST",
}


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    name: str
    atol: float
    rtol: float
    minimum_input_precision: str
    input_downcast_allowed: bool
    input_family: str
    required_shape_classes: tuple[str, ...]
    required_layouts: tuple[str, ...]
    required_value_classes: tuple[str, ...]

    def semantics(self) -> dict[str, Any]:
        return {
            "profile_name": self.name,
            "numerical": {
                "input_dtype": "float32",
                "minimum_input_precision": self.minimum_input_precision,
                "accumulation_dtype": "float32",
                "output_dtype": "float32",
                "input_downcast_allowed": self.input_downcast_allowed,
            },
            "reference": {
                "implementation": "torch.matmul",
                "precision": "float32",
                "tf32_allowed": False,
                "deterministic_algorithms": True,
            },
            "inputs": {
                "family": self.input_family,
                "required_shape_classes": list(self.required_shape_classes),
                "required_layouts": list(self.required_layouts),
                "required_value_classes": list(self.required_value_classes),
                "required_slices": ["train", "holdout"],
            },
            "enforcement": {
                "require_every_correctness_slice": True,
                "require_every_case_no_regression": True,
                "require_paired_aggregate_performance": True,
                "candidate_controls_protected": True,
                "minimum_case_speedup_vs_incumbent": 0.98,
            },
        }


PROFILES = {
    STRICT_PROFILE: ProfileSpec(
        name=STRICT_PROFILE,
        atol=1.0e-4,
        rtol=1.0e-4,
        minimum_input_precision="float32",
        input_downcast_allowed=False,
        input_family="matmul-generalization-v1",
        required_shape_classes=("non-tile-square", "rectangular"),
        required_layouts=("contiguous", "transposed"),
        required_value_classes=("signed", "small", "large", "cancellation", "dynamic-range"),
    ),
    RELAXED_PROFILE: ProfileSpec(
        name=RELAXED_PROFILE,
        atol=1.0e-2,
        rtol=1.0e-2,
        minimum_input_precision="float16",
        input_downcast_allowed=True,
        input_family="matmul-fixed-square-legacy-v1",
        required_shape_classes=("tile-aligned-square",),
        required_layouts=("contiguous",),
        required_value_classes=("positive-unit",),
    ),
}


def gpu_attestation_metadata(environment: Mapping[str, str]) -> dict[str, str]:
    """Validate worker-provided GPU attestation as canonical identity metadata."""
    metadata = {name: environment.get(variable) for name, variable in GPU_ATTESTATION_ENV.items()}
    if not any(value is not None for value in metadata.values()):
        return {
            "device_grant": "unsafe-local-device-0",
            "device_isolation_kind": "unattested",
            "device_enforced_memory_bytes": "unattested",
            "device_attestor_id": "unattested",
            "device_attestation_digest": "unattested",
        }
    if any(value is None for value in metadata.values()):
        raise RuntimeError("Docker GPU attestation environment is incomplete")
    canonical = {name: value for name, value in metadata.items() if value is not None}
    if canonical["device_isolation_kind"] not in {"mig", "hardware-partition"}:
        raise RuntimeError("Docker GPU attestation has an unsupported isolation kind")
    if not canonical["device_attestor_id"].strip() or any(character in canonical["device_attestor_id"] for character in "\r\n\0"):
        raise RuntimeError("Docker GPU attestation has an invalid attestor identity")
    enforced_memory_bytes = canonical["device_enforced_memory_bytes"]
    if (
        not enforced_memory_bytes.isascii()
        or not enforced_memory_bytes.isdecimal()
        or int(enforced_memory_bytes) < 1
        or str(int(enforced_memory_bytes)) != enforced_memory_bytes
    ):
        raise RuntimeError("Docker GPU attestation capacity must be a canonical positive decimal integer")
    if re.fullmatch(r"sha256:[0-9a-f]{64}", canonical["device_attestation_digest"]) is None:
        raise RuntimeError("Docker GPU attestation digest is invalid")
    return canonical


def canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def private_plan_commitment(path: Path) -> str:
    """Return the public commitment without exposing private plan material."""
    return canonical_digest(json.loads(path.read_text(encoding="utf-8")))


def profile_output_root(root: Path, profile_name: str) -> Path:
    """Namespace results so strict and relaxed evidence cannot overwrite."""
    if profile_name not in PROFILES:
        raise ValueError(f"unknown precision profile {profile_name!r}")
    return root / profile_name


def load_private_plan(
    path: Path,
    *,
    profile_name: str,
    role: Literal["primary", "confirmation"],
    expected_commitment: str,
) -> dict[str, Any]:
    """Load a worker-private plan and verify its public commitment and coverage."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("private plan must be a JSON object")
    if canonical_digest(payload) != expected_commitment:
        raise ValueError("private plan does not match its published commitment")
    if payload.get("schema_version") != PLAN_SCHEMA:
        raise ValueError(f"private plan schema_version must be {PLAN_SCHEMA!r}")
    if payload.get("profile_name") != profile_name or payload.get("role") != role:
        raise ValueError("private plan profile or role does not match the selected campaign")
    spec = PROFILES.get(profile_name)
    if spec is None:
        raise ValueError(f"unknown precision profile {profile_name!r}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) < 2:
        raise ValueError("private plan must contain at least two cases")
    _validate_cases(cases, spec=spec)
    timing_order = payload.get("timing_order")
    names = [case["name"] for case in cases]
    if not isinstance(timing_order, list) or sorted(timing_order) != sorted(names):
        raise ValueError("timing_order must contain every case exactly once")
    return payload


def _validate_cases(cases: list[Any], *, spec: ProfileSpec) -> None:
    names: set[str] = set()
    seeds: set[int] = set()
    coverage: dict[str, dict[str, set[str]]] = {
        split: {
            "shape classes": set(),
            "A layouts": set(),
            "B layouts": set(),
            "value classes": set(),
        }
        for split in ("train", "holdout")
    }
    required = {
        "name",
        "split",
        "seed",
        "m",
        "n",
        "k",
        "a_layout",
        "b_layout",
        "value_class",
        "magnitude_min",
        "magnitude_max",
    }
    for case in cases:
        if not isinstance(case, dict) or set(case) != required:
            raise ValueError("each private case must contain exactly the documented fields")
        name = case["name"]
        seed = case["seed"]
        split = case["split"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("private case names must be unique and non-empty")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 or seed in seeds:
            raise ValueError("private case seeds must be unique non-negative integers")
        if split not in {"train", "holdout"}:
            raise ValueError("private case split must be train or holdout")
        dimensions = (case["m"], case["n"], case["k"])
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in dimensions):
            raise ValueError("private case dimensions must be positive integers")
        for key in ("magnitude_min", "magnitude_max"):
            value = case[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be positive and finite")
        if case["magnitude_min"] >= case["magnitude_max"]:
            raise ValueError("magnitude_min must be less than magnitude_max")
        value_class = case["value_class"]
        if value_class not in spec.required_value_classes:
            raise ValueError(f"value_class must be one of the canonical {spec.name} classes")
        minimum = float(case["magnitude_min"])
        maximum = float(case["magnitude_max"])
        if value_class == "positive-unit" and maximum > 1.0:
            raise ValueError("positive-unit magnitudes must be in (0, 1]")
        if value_class == "small" and maximum > 1.0e-2:
            raise ValueError("small magnitudes must not exceed 1e-2")
        if value_class == "large" and minimum < 1.0:
            raise ValueError("large magnitudes must be at least 1")
        if value_class == "dynamic-range" and (minimum >= 1.0 or maximum <= 1.0 or maximum / minimum < 1.0e6):
            raise ValueError("dynamic-range cases must span 1 with at least six orders of magnitude")
        if value_class == "cancellation" and maximum / 1.0003 <= minimum:
            raise ValueError("cancellation magnitudes must leave room for the paired perturbation")
        if case["value_class"] == "cancellation" and case["k"] % 2:
            raise ValueError("cancellation cases require an even k dimension")
        case_layouts = {case["a_layout"], case["b_layout"]}
        if not case_layouts <= {"contiguous", "transposed"}:
            raise ValueError("layouts must be contiguous or transposed")
        names.add(name)
        seeds.add(seed)
        split_coverage = coverage[split]
        split_coverage["A layouts"].add(case["a_layout"])
        split_coverage["B layouts"].add(case["b_layout"])
        split_coverage["value classes"].add(case["value_class"])
        m, n, k = dimensions
        if m == n and any(value % 128 for value in dimensions):
            split_coverage["shape classes"].add("non-tile-square")
        elif m == n == k and all(value % 128 == 0 for value in dimensions):
            split_coverage["shape classes"].add("tile-aligned-square")
        if len({m, n, k}) > 1:
            split_coverage["shape classes"].add("rectangular")
    for split, split_coverage in coverage.items():
        for label, required_values in (
            ("shape classes", spec.required_shape_classes),
            ("A layouts", spec.required_layouts),
            ("B layouts", spec.required_layouts),
            ("value classes", spec.required_value_classes),
        ):
            missing = sorted(set(required_values) - split_coverage[label])
            if missing:
                raise ValueError(f"private plan {split} split is missing required {label}: {', '.join(missing)}")


def assert_fresh_plans(primary: dict[str, Any], confirmation: dict[str, Any]) -> None:
    """Reject confirmation plans that reuse any primary input or order material."""
    if primary.get("profile_name") != confirmation.get("profile_name"):
        raise ValueError("primary and confirmation plans must use the same precision profile")
    if len(primary["cases"]) != len(confirmation["cases"]):
        raise ValueError("primary and confirmation plans must use compatible case counts")
    primary_splits = sorted(case["split"] for case in primary["cases"])
    confirmation_splits = sorted(case["split"] for case in confirmation["cases"])
    if primary_splits != confirmation_splits:
        raise ValueError("primary and confirmation plans must use compatible split counts")
    primary_cases = {
        (case["seed"], case["m"], case["n"], case["k"], case["a_layout"], case["b_layout"]) for case in primary["cases"]
    }
    confirmation_cases = {
        (case["seed"], case["m"], case["n"], case["k"], case["a_layout"], case["b_layout"]) for case in confirmation["cases"]
    }
    if primary_cases & confirmation_cases:
        raise ValueError("primary and confirmation plans must use disjoint inputs")

    def relative_order(plan: dict[str, Any]) -> tuple[int, ...]:
        by_name = {case["name"]: index for index, case in enumerate(plan["cases"])}
        return tuple(by_name[name] for name in plan["timing_order"])

    if relative_order(primary) == relative_order(confirmation):
        raise ValueError("primary and confirmation timing orders must differ")
