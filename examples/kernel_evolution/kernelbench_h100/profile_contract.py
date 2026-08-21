"""Public H100 precision profiles and private-plan commitment validation.

Exact seeds, shapes, ranges, and case order live only in a worker-supplied JSON
plan.  Campaign artifacts publish the canonical digest returned here.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

STRICT_PROFILE = "strict-fp32-v1"
RELAXED_PROFILE = "relaxed-precision-v1"
PROFILE_NAMES = (STRICT_PROFILE, RELAXED_PROFILE)
PLAN_SCHEMA = "autocontext.kernel-private-plan/v1"


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
    splits: set[str] = set()
    shape_classes: set[str] = set()
    layouts: set[str] = set()
    value_classes: set[str] = set()
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
        if case["magnitude_min"] > case["magnitude_max"]:
            raise ValueError("magnitude_min cannot exceed magnitude_max")
        if case["value_class"] == "cancellation" and case["k"] % 2:
            raise ValueError("cancellation cases require an even k dimension")
        case_layouts = {case["a_layout"], case["b_layout"]}
        if not case_layouts <= {"contiguous", "transposed"}:
            raise ValueError("layouts must be contiguous or transposed")
        names.add(name)
        seeds.add(seed)
        splits.add(split)
        layouts.update(case_layouts)
        value_classes.add(case["value_class"])
        m, n, k = dimensions
        if m == n and any(value % 128 for value in dimensions):
            shape_classes.add("non-tile-square")
        elif m == n == k and all(value % 128 == 0 for value in dimensions):
            shape_classes.add("tile-aligned-square")
        if len({m, n, k}) > 1:
            shape_classes.add("rectangular")
    if splits != {"train", "holdout"}:
        raise ValueError("private plans must contain disjoint train and holdout slices")
    for label, observed, required_values in (
        ("shape classes", shape_classes, spec.required_shape_classes),
        ("layouts", layouts, spec.required_layouts),
        ("value classes", value_classes, spec.required_value_classes),
    ):
        missing = sorted(set(required_values) - observed)
        if missing:
            raise ValueError(f"private plan is missing required {label}: {', '.join(missing)}")


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
