from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import ModuleType

import pytest

from autocontext.kernel_evolution import (
    RELAXED_PRECISION_SEMANTICS,
    STRICT_FP32_SEMANTICS,
    KernelProtocolSemantics,
)


def _profile_contract() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "examples" / "kernel_evolution" / "kernelbench_h100" / "profile_contract.py"
    spec = importlib.util.spec_from_file_location("kernel_h100_profile_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _strict_plan(*, role: str, seed_offset: int = 0) -> dict[str, object]:
    case_families = [
        ("signed-square", 129, 129, 131, "contiguous", "contiguous", "signed", 0.1, 1.0),
        ("small-rect", 127, 191, 65, "transposed", "contiguous", "small", 1e-5, 1e-3),
        ("large-rect", 193, 71, 137, "contiguous", "transposed", "large", 10.0, 100.0),
        ("cancel-square", 257, 257, 262, "transposed", "transposed", "cancellation", 0.1, 2.0),
        ("dynamic-rect", 73, 211, 89, "contiguous", "transposed", "dynamic-range", 1e-5, 1e3),
    ]
    cases = [(name, split, *fields) for split in ("train", "holdout") for name, *fields in case_families]
    rendered = [
        {
            "name": f"{name}-{split}-{role}",
            "split": split,
            "seed": seed_offset + index + 101,
            "m": m,
            "n": n,
            "k": k,
            "a_layout": a_layout,
            "b_layout": b_layout,
            "value_class": value_class,
            "magnitude_min": minimum,
            "magnitude_max": maximum,
        }
        for index, (name, split, m, n, k, a_layout, b_layout, value_class, minimum, maximum) in enumerate(cases)
    ]
    return {
        "schema_version": "autocontext.kernel-private-plan/v1",
        "profile_name": "strict-fp32-v1",
        "role": role,
        "cases": rendered,
        "timing_order": [case["name"] for case in (rendered if role == "primary" else reversed(rendered))],
    }


def test_private_plans_publish_only_commitments_and_require_fresh_inputs(tmp_path: Path) -> None:
    contract = _profile_contract()
    primary_payload = _strict_plan(role="primary")
    confirmation_payload = _strict_plan(role="confirmation", seed_offset=10_000)
    primary_path = tmp_path / "primary-private.json"
    confirmation_path = tmp_path / "confirmation-private.json"
    primary_path.write_text(json.dumps(primary_payload), encoding="utf-8")
    confirmation_path.write_text(json.dumps(confirmation_payload), encoding="utf-8")
    primary_commitment = contract.private_plan_commitment(primary_path)
    confirmation_commitment = contract.private_plan_commitment(confirmation_path)

    primary = contract.load_private_plan(
        primary_path,
        profile_name="strict-fp32-v1",
        role="primary",
        expected_commitment=primary_commitment,
    )
    confirmation = contract.load_private_plan(
        confirmation_path,
        profile_name="strict-fp32-v1",
        role="confirmation",
        expected_commitment=confirmation_commitment,
    )
    contract.assert_fresh_plans(primary, confirmation)

    assert primary_commitment != confirmation_commitment
    assert primary_commitment.startswith("sha256:")
    with pytest.raises(ValueError, match="published commitment"):
        contract.load_private_plan(
            primary_path,
            profile_name="strict-fp32-v1",
            role="primary",
            expected_commitment=confirmation_commitment,
        )


def test_private_plan_rejects_reused_confirmation_material(tmp_path: Path) -> None:
    contract = _profile_contract()
    primary = _strict_plan(role="primary")
    confirmation = _strict_plan(role="confirmation")
    confirmation["cases"] = [dict(case) for case in primary["cases"]]
    confirmation["timing_order"] = list(reversed(primary["timing_order"]))

    with pytest.raises(ValueError, match="disjoint inputs"):
        contract.assert_fresh_plans(primary, confirmation)


def test_private_plan_rejects_same_relative_confirmation_order() -> None:
    contract = _profile_contract()
    primary = _strict_plan(role="primary")
    confirmation = _strict_plan(role="confirmation", seed_offset=10_000)
    confirmation["timing_order"] = [case["name"] for case in confirmation["cases"]]

    with pytest.raises(ValueError, match="timing orders must differ"):
        contract.assert_fresh_plans(primary, confirmation)


def test_strict_plan_requires_varied_coverage_in_each_split(tmp_path: Path) -> None:
    contract = _profile_contract()
    payload = _strict_plan(role="primary")
    payload["cases"] = [case for case in payload["cases"] if case["split"] == "train" or case["value_class"] == "signed"]
    payload["timing_order"] = [case["name"] for case in payload["cases"]]
    path = tmp_path / "under-covered.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="holdout split is missing required"):
        contract.load_private_plan(
            path,
            profile_name="strict-fp32-v1",
            role="primary",
            expected_commitment=contract.private_plan_commitment(path),
        )


def test_strict_plan_requires_layout_coverage_for_each_operand_in_each_split(tmp_path: Path) -> None:
    contract = _profile_contract()
    payload = _strict_plan(role="primary")
    for case in payload["cases"]:
        case["a_layout"] = "contiguous"
    path = tmp_path / "operand-layout-under-covered.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="train split is missing required A layouts: transposed"):
        contract.load_private_plan(
            path,
            profile_name="strict-fp32-v1",
            role="primary",
            expected_commitment=contract.private_plan_commitment(path),
        )


@pytest.mark.parametrize(
    ("value_class", "minimum", "maximum", "message"),
    [
        ("small", 1.0, 1.0e9, "small magnitudes"),
        ("large", 1.0e-12, 1.0e-6, "large magnitudes"),
        ("dynamic-range", 0.1, 10.0, "six orders"),
        ("cancellation", 1.0, 1.0001, "paired perturbation"),
        ("invented", 0.1, 1.0, "canonical strict-fp32-v1 classes"),
    ],
)
def test_strict_plan_rejects_mislabeled_value_semantics(
    tmp_path: Path,
    value_class: str,
    minimum: float,
    maximum: float,
    message: str,
) -> None:
    contract = _profile_contract()
    payload = _strict_plan(role="primary")
    target = next(case for case in payload["cases"] if case["value_class"] == "small")
    target.update(value_class=value_class, magnitude_min=minimum, magnitude_max=maximum)
    path = tmp_path / f"bad-{value_class}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        contract.load_private_plan(
            path,
            profile_name="strict-fp32-v1",
            role="primary",
            expected_commitment=contract.private_plan_commitment(path),
        )


def test_profiles_are_explicit_and_results_are_namespaced(tmp_path: Path) -> None:
    contract = _profile_contract()
    strict = contract.PROFILES["strict-fp32-v1"]
    relaxed = contract.PROFILES["relaxed-precision-v1"]

    assert strict.input_downcast_allowed is False
    assert strict.required_shape_classes == ("non-tile-square", "rectangular")
    assert set(strict.required_value_classes) >= {"signed", "cancellation", "dynamic-range"}
    assert relaxed.input_downcast_allowed is True
    assert relaxed.atol == relaxed.rtol == 0.01
    assert contract.profile_output_root(tmp_path, strict.name) != contract.profile_output_root(tmp_path, relaxed.name)
    assert KernelProtocolSemantics.model_validate(strict.semantics()) == STRICT_FP32_SEMANTICS
    assert KernelProtocolSemantics.model_validate(relaxed.semantics()) == RELAXED_PRECISION_SEMANTICS


def test_gpu_attestation_environment_is_complete_and_canonical() -> None:
    contract = _profile_contract()
    environment = {
        "AUTOCONTEXT_GPU_DEVICE_ID": "MIG-GPU-deadbeef/1/0",
        "AUTOCONTEXT_GPU_ISOLATION_KIND": "mig",
        "AUTOCONTEXT_GPU_ENFORCED_MEMORY_BYTES": str(8 * 1024**3),
        "AUTOCONTEXT_GPU_ATTESTOR_ID": "nvidia-smi-nvml-mig-v1",
        "AUTOCONTEXT_GPU_ATTESTATION_DIGEST": f"sha256:{'a' * 64}",
    }

    assert contract.gpu_attestation_metadata(environment) == {
        "device_grant": "MIG-GPU-deadbeef/1/0",
        "device_isolation_kind": "mig",
        "device_enforced_memory_bytes": str(8 * 1024**3),
        "device_attestor_id": "nvidia-smi-nvml-mig-v1",
        "device_attestation_digest": f"sha256:{'a' * 64}",
    }
    assert contract.gpu_attestation_metadata({})["device_isolation_kind"] == "unattested"

    with pytest.raises(RuntimeError, match="incomplete"):
        contract.gpu_attestation_metadata({"AUTOCONTEXT_GPU_DEVICE_ID": "MIG-GPU-deadbeef/1/0"})
    with pytest.raises(RuntimeError, match="canonical positive decimal"):
        contract.gpu_attestation_metadata({**environment, "AUTOCONTEXT_GPU_ENFORCED_MEMORY_BYTES": "08589934592"})
    with pytest.raises(RuntimeError, match="digest is invalid"):
        contract.gpu_attestation_metadata({**environment, "AUTOCONTEXT_GPU_ATTESTATION_DIGEST": "unattested"})


def test_exact_recursive_champion_is_strict_rejected_but_retains_relaxed_evidence(tmp_path: Path) -> None:
    contract = _profile_contract()
    bundle = Path(__file__).resolve().parents[2] / "examples" / "kernel_evolution" / "kernelbench_h100"
    source = (bundle / "recursive_champion.py").read_text(encoding="utf-8")
    evidence = json.loads((bundle / "verified_recursive_h100_result.json").read_text(encoding="utf-8"))
    reassessment = json.loads((bundle / "profile_reassessment.json").read_text(encoding="utf-8"))
    digest = f"sha256:{hashlib.sha256(source.encode()).hexdigest()}"

    tree = ast.parse(source)
    fp16_downcasts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "to"
        and any(
            keyword.arg == "dtype" and isinstance(keyword.value, ast.Attribute) and keyword.value.attr == "float16"
            for keyword in node.keywords
        )
    ]
    assert len(fp16_downcasts) == 2
    assert "assert a.shape == (4096, 4096)" in source
    assert digest == evidence["artifacts"]["champion_artifact_digest"]
    assert digest == reassessment["candidate"]["legacy_source_digest"]

    # Reproduce the exact candidate strategy on a deterministic scalar matmul:
    # downcast both FP32 inputs to IEEE binary16, then accumulate/output FP32.
    def quantize_fp16(value: float) -> float:
        return struct.unpack(">e", struct.pack(">e", value))[0]

    left = right = 1.0003
    expected = left * right
    downcast_output = quantize_fp16(left) * quantize_fp16(right)
    absolute_error = abs(downcast_output - expected)
    strict = contract.PROFILES["strict-fp32-v1"]
    relaxed = contract.PROFILES["relaxed-precision-v1"]

    assert absolute_error > strict.atol + strict.rtol * abs(expected)
    assert absolute_error <= relaxed.atol + relaxed.rtol * abs(expected)
    assert reassessment["profiles"]["strict-fp32-v1"]["result"] == "rejected_by_protocol_contract"
    assert reassessment["profiles"]["relaxed-precision-v1"]["result"] == "retained_historical_evidence"
    assert contract.profile_output_root(tmp_path, strict.name) != contract.profile_output_root(tmp_path, relaxed.name)
