from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.cpu_models import CpuDecoding
from autocontext.weight_evolution.cpu_search_models import (
    CpuSearchBudget,
    CpuSearchBundleLock,
    CpuSearchOption,
    CpuSearchSpec,
)
from autocontext.weight_evolution.cpu_study import CpuStudySpec


@pytest.fixture
def search_spec() -> CpuSearchSpec:
    bundle = Path(__file__).resolve().parents[2] / "examples/weight_evolution/cpu_inference/bundle/study.json"
    fixed = CpuStudySpec.model_validate_json(bundle.read_text())
    return CpuSearchSpec(
        study_id="search-contract-test", model=fixed.model, candidates=fixed.candidates, rounds=2,
        options=(CpuSearchOption(option_id="c1", mechanism="C", direction="prefix", step=1, context="hint"),
                 CpuSearchOption(option_id="c2", mechanism="C", direction="prefix", step=2, context="hint hint"),
                 CpuSearchOption(option_id="w1", mechanism="W", direction="first", step=1, candidate_id="helpful"),
                 CpuSearchOption(option_id="w2", mechanism="W", direction="second", step=1, candidate_id="harmful"),
                 CpuSearchOption(option_id="d1", mechanism="D", direction="temperature", step=1,
                                 decoding=CpuDecoding(mode="sample", seed=406, temperature=1.0)),
                 CpuSearchOption(option_id="d2", mechanism="D", direction="temperature", step=2,
                                 decoding=CpuDecoding(mode="sample", seed=406, temperature=0.5))),
        baseline_decoding=CpuDecoding(seed=406), evaluation_seed=406, limits=fixed.limits,
        budget=CpuSearchBudget(), cases=fixed.cases, minimum_confirmation_gain=0.1,
        implementation_digest="0" * 64,
    )


def test_preflight_reserves_all_arms_restoration_and_both_held_out_roles(search_spec: CpuSearchSpec) -> None:
    assert search_spec.required_usage == {"proposals": 6, "materializations": 3, "inference_calls": 115,
                                          "input_tokens": 420, "output_tokens": 115}
    assert search_spec.baseline.candidate_id == "base"
    assert [option.option_id for option in search_spec.options_for("W")] == ["w1", "w2"]
    assert CpuSearchSpec.model_validate_json(json.dumps(search_spec.to_dict())) == search_spec
    assert len(search_spec.digest) == 64
    assert search_spec.study_kind == "adaptive_cpu_inference"
    assert search_spec.gpu_execution_deferred is True and search_spec.promotion_eligible is False
    assert "model_execution_performed" not in search_spec.to_dict()


@pytest.mark.parametrize("mechanism,field,value", [("C", "candidate_id", "helpful"), ("C", "decoding", {}),
                                                   ("W", "context", "hint"), ("D", "context", "hint")])
def test_options_cannot_change_another_mechanism_surface(mechanism: str, field: str, value: Any) -> None:
    option: dict[str, Any] = {"option_id": "option", "mechanism": mechanism, "direction": "direction", "step": 1,
                             {"C": "context", "W": "candidate_id", "D": "decoding"}[mechanism]:
                             {"C": "hint", "W": "helpful", "D": {}}[mechanism]}
    option[field] = value
    with pytest.raises(ValueError, match="exactly"):
        CpuSearchOption.model_validate_json(json.dumps(option))


@pytest.mark.parametrize("mechanism", ["C", "W", "D"])
def test_options_require_their_surface(mechanism: str) -> None:
    with pytest.raises(ValueError, match="exactly"):
        CpuSearchOption.model_validate_json(json.dumps({"option_id": "option", "mechanism": mechanism,
                                                       "direction": "direction", "step": 1}))


@pytest.mark.parametrize("mutation", [
    "option-id", "option-coordinate", "option-gap", "option-order", "context-duplicate", "context-unknown",
    "context-empty", "seed-baseline", "seed-option", "greedy-option", "sample-baseline", "top-k-baseline",
    "top-k-option", "decoding-duplicate", "unknown-checkpoint", "repeated-checkpoint", "unused-checkpoint",
    "candidate-id", "candidate-bytes", "missing-baseline", "contract", "case-id", "case-id-long",
    "case-prompt-duplicate", "case-label", "case-token", "case-count", "round-count", "token-limit",
    "budget-proposals", "budget-materializations", "budget-inference", "budget-input", "budget-output",
    "boolean-version", "integer-gpu-flag", "integer-promotion-flag", "extra-gold", "extra-routes",
])
def test_invalid_search_design_rejected_before_work(search_spec: CpuSearchSpec, mutation: str) -> None:
    value = search_spec.to_dict()
    if mutation == "option-id":
        value["options"][1]["option_id"] = "c1"
    elif mutation == "option-coordinate":
        value["options"][1]["step"] = 1
    elif mutation == "option-gap":
        value["options"][1]["step"] = 3
    elif mutation == "option-order":
        value["options"][0], value["options"][1] = value["options"][1], value["options"][0]
    elif mutation == "context-duplicate":
        value["options"][1]["context"] = " hint\t"
    elif mutation == "context-unknown":
        value["options"][0]["context"] = "outside"
    elif mutation == "context-empty":
        value["options"][0]["context"] = "\t "
    elif mutation == "seed-baseline":
        value["baseline_decoding"]["seed"] += 1
    elif mutation == "seed-option":
        value["options"][4]["decoding"]["seed"] += 1
    elif mutation == "greedy-option":
        value["options"][4]["decoding"]["mode"] = "greedy"
    elif mutation == "sample-baseline":
        value["baseline_decoding"]["mode"] = "sample"
    elif mutation == "top-k-baseline":
        value["baseline_decoding"]["top_k"] = 3
    elif mutation == "top-k-option":
        value["options"][4]["decoding"]["top_k"] = 3
    elif mutation == "decoding-duplicate":
        value["options"][5]["decoding"] = value["options"][4]["decoding"]
    elif mutation == "unknown-checkpoint":
        value["options"][2]["candidate_id"] = "missing"
    elif mutation == "repeated-checkpoint":
        value["options"][3]["candidate_id"] = value["options"][2]["candidate_id"]
    elif mutation == "unused-checkpoint":
        value["options"] = [option for option in value["options"] if option["option_id"] != "w2"]
        value["rounds"] = 1
    elif mutation == "candidate-id":
        value["candidates"][1]["candidate_id"] = value["candidates"][0]["candidate_id"]
    elif mutation == "candidate-bytes":
        value["candidates"][2]["output_sha256"] = value["candidates"][1]["output_sha256"]
    elif mutation == "missing-baseline":
        value["candidates"] = value["candidates"][1:]
    elif mutation == "contract":
        value["candidates"][2]["contract"]["max_changed_entries"] += 1
    elif mutation == "case-id":
        value["cases"][1]["case_id"] = value["cases"][0]["case_id"]
    elif mutation == "case-id-long":
        value["cases"][0]["case_id"] = "x" * 65
    elif mutation == "case-prompt-duplicate":
        value["cases"][5]["prompt"] = "\t" + value["cases"][0]["prompt"] + " "
    elif mutation == "case-label":
        value["cases"][0]["expected"] = "outside"
    elif mutation == "case-token":
        value["cases"][0]["prompt"] = "outside"
    elif mutation == "case-count":
        value["cases"] = [case for case in value["cases"] if case["role"] != "audit"]
    elif mutation == "round-count":
        value["rounds"] = 3
    elif mutation == "token-limit":
        value["limits"]["max_input_tokens"] = 4
    elif mutation.startswith("budget-"):
        field = {"proposals": "proposals", "materializations": "materializations", "inference": "inference_calls",
                 "input": "input_tokens", "output": "output_tokens"}[mutation.removeprefix("budget-")]
        value["budget"][f"max_{field}"] = search_spec.required_usage[field] - 1
    elif mutation == "boolean-version":
        value["schema_version"] = True
    elif mutation == "integer-gpu-flag":
        value["gpu_execution_deferred"] = 1
    elif mutation == "integer-promotion-flag":
        value["promotion_eligible"] = 0
    else:
        value[mutation.removeprefix("extra-")] = {"audit-answer": "one"}
    with pytest.raises(ValueError):
        CpuSearchSpec.model_validate_json(json.dumps(value))


def test_exact_budget_is_accepted_and_lower_one_is_not(search_spec: CpuSearchSpec) -> None:
    value = search_spec.to_dict()
    value["budget"] = {f"max_{name}": count for name, count in search_spec.required_usage.items()}
    assert CpuSearchSpec.model_validate_json(json.dumps(value)).required_usage == search_spec.required_usage


def test_prompt_byte_bound_is_checked_even_when_token_count_fits(search_spec: CpuSearchSpec) -> None:
    value = search_spec.to_dict()
    token = "\U0001f408" * 64
    value["model"]["vocabulary"][0] = token
    value["options"][0]["context"] = token
    value["options"][1]["context"] = f"{token} {token}"
    value["limits"]["max_input_tokens"] = 64
    value["cases"] = [{"case_id": f"{role}-{index}", "role": role,
                       "prompt": " ".join([token] * count), "expected": "zero"}
                      for index, (role, count) in enumerate((("practice", 30), ("practice", 31),
                                                            ("confirmation", 32), ("confirmation", 33),
                                                            ("audit", 34), ("audit", 35)))]
    with pytest.raises(ValueError, match="UTF-8 byte"):
        CpuSearchSpec.model_validate_json(json.dumps(value))


def test_proposal_and_registry_bounds_are_finite() -> None:
    for field, value in (("max_proposals", 49), ("max_materializations", 9), ("max_inference_calls", 2049),
                         ("max_input_tokens", 131073), ("max_output_tokens", 2049), ("max_proposals", True)):
        with pytest.raises(ValueError):
            CpuSearchBudget.model_validate_json(json.dumps({field: value}))


def test_bundle_lock_is_distinct_from_prior_cpu_fixture_and_strict() -> None:
    lock = CpuSearchBundleLock(spec_sha256="0" * 64, spec_size_bytes=1, base_sha256="1" * 64, base_size_bytes=1)
    assert lock.kind == "cpu_adaptive_bundle"
    for change in ({"schema_version": True}, {"kind": "cpu_inference_bundle"}, {"spec_size_bytes": 1048577}):
        with pytest.raises(ValueError):
            CpuSearchBundleLock.model_validate_json(json.dumps({**lock.to_dict(), **change}))
