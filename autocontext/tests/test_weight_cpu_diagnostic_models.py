from __future__ import annotations

import json
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_models import CAMPAIGN_POLICIES
from autocontext.weight_evolution.cpu_campaign_tasks import _task_payloads
from autocontext.weight_evolution.cpu_diagnostic_models import (
    CpuDiagnosticBudget,
    CpuDiagnosticCase,
    CpuDiagnosticLock,
    CpuDiagnosticReference,
    CpuDiagnosticSourceRun,
    CpuDiagnosticSpec,
)


@pytest.fixture
def reference() -> CpuDiagnosticReference:
    child, _, _ = _task_payloads(task_id="task-01", generation_seed=31, cases_per_role=2)
    practice = [case for case in child.cases if case.role == "practice"]
    return CpuDiagnosticReference(
        reference_id="task-01.r0", task_id="task-01", repeat_id="r0", inference_seed=0,
        source_runs=tuple(CpuDiagnosticSourceRun(run_id=f"task-01.r0.{policy}", policy=policy,
                                                study_digest="1" * 64, selection_digest="2" * 64,
                                                summary_semantic_digest="3" * 64) for policy in CAMPAIGN_POLICIES),
        model=child.model, candidates=child.candidates, options=child.options,
        baseline_decoding=child.baseline_decoding, limits=child.limits,
        practice=tuple(CpuDiagnosticCase(case_id=case.case_id, prompt=case.prompt) for case in practice),
        practice_cases_digest=canonical_digest([case.to_dict() for case in practice]),
        source_options_digest=canonical_digest([option.to_dict() for option in child.options]),
    )


def spec_for(reference: CpuDiagnosticReference) -> CpuDiagnosticSpec:
    other = reference.to_dict()
    other["task_id"], other["reference_id"] = "task-02", "task-02.r0"
    for run in other["source_runs"]:
        run["run_id"] = f"task-02.r0.{run['policy']}"
    references = (reference, CpuDiagnosticReference.model_validate_json(json.dumps(other)))
    usage = {name: sum(ref.required_usage[name] for ref in references) for name in reference.required_usage}
    return CpuDiagnosticSpec(
        diagnostic_id="diagnostic-test", campaign_digest="4" * 64, runtime_digest="5" * 64,
        campaign_report_digest="6" * 64, campaign_ledger_digest="7" * 64, references=references,
        budget=CpuDiagnosticBudget(max_references=2, max_proposals=0,
                                   **{f"max_{name}": value for name, value in usage.items() if name != "proposals"}),
        implementation_digest="8" * 64,
    )


def test_reference_reserves_one_shared_exhaustive_practice_schedule(reference: CpuDiagnosticReference) -> None:
    spec = spec_for(reference)
    assert len(reference.source_runs) == 3 and len(reference.options) == 12
    assert reference.required_usage["inference_calls"] == 13 * 2
    assert reference.required_usage["proposals"] == 0
    assert reference.required_usage["materializations"] == len(reference.candidates) == 5
    assert reference.required_usage["output_tokens"] == reference.required_usage["inference_calls"]
    assert spec.required_usage["references"] == 2
    assert spec.required_usage["inference_calls"] == 52
    serialized = spec.to_dict()
    assert CpuDiagnosticSpec.model_validate_json(json.dumps(serialized)) == spec
    assert all(set(case) == {"case_id", "prompt"} for ref in serialized["references"] for case in ref["practice"])
    assert spec.gpu_execution_deferred and not spec.promotion_eligible


@pytest.mark.parametrize("mutation", [
    "seed", "decoding-seed", "baseline-mode", "source-order", "source-run", "options-pin", "baseline-key",
    "unknown-token", "gold", "heldout-role", "case-alias", "option-alias", "unused-checkpoint", "input-cap",
])
def test_reference_rejects_changed_or_ambiguous_worker_inputs(reference: CpuDiagnosticReference, mutation: str) -> None:
    value = reference.to_dict()
    if mutation == "seed":
        value["inference_seed"] += 1
    elif mutation == "decoding-seed":
        value["options"][-1]["decoding"]["seed"] += 1
    elif mutation == "baseline-mode":
        value["baseline_decoding"]["mode"] = "sample"
    elif mutation == "source-order":
        value["source_runs"].reverse()
    elif mutation == "source-run":
        value["source_runs"][0]["run_id"] = "other.r0.adaptive-v1"
    elif mutation == "options-pin":
        value["source_options_digest"] = "0" * 64
    elif mutation == "baseline-key":
        value["options"][0]["option_id"] = "A"
    elif mutation == "unknown-token":
        value["practice"][0]["prompt"] = "unknown-token"
    elif mutation == "gold":
        value["practice"][0]["expected"] = "zero"
    elif mutation == "heldout-role":
        value["practice"][0]["role"] = "confirmation"
    elif mutation == "case-alias":
        value["practice"][1]["case_id"] = value["practice"][0]["case_id"].upper()
    elif mutation == "option-alias":
        value["options"][1]["option_id"] = value["options"][0]["option_id"].upper()
    elif mutation == "unused-checkpoint":
        value["options"] = [option for option in value["options"] if option["option_id"] != "w-b-2"]
    else:
        value["limits"]["max_input_tokens"] = 1
    if mutation != "options-pin":
        value["source_options_digest"] = canonical_digest(value["options"])
    with pytest.raises(ValueError):
        CpuDiagnosticReference.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("meter", ["references", "materializations", "inference_calls", "input_tokens", "output_tokens"])
def test_diagnostic_reserves_all_references_before_execution(reference: CpuDiagnosticReference, meter: str) -> None:
    value = spec_for(reference).to_dict()
    value["budget"][f"max_{meter}"] -= 1
    with pytest.raises(ValueError, match="complete exhaustive"):
        CpuDiagnosticSpec.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("field,value", [("schema_version", True), ("gpu_execution_deferred", 1),
                                         ("promotion_eligible", 0)])
def test_evidence_flags_preserve_json_types(reference: CpuDiagnosticReference, field: str, value: Any) -> None:
    data = spec_for(reference).to_dict()
    data[field] = value
    with pytest.raises(ValueError, match="literal type"):
        CpuDiagnosticSpec.model_validate_json(json.dumps(data))


def test_plan_rejects_casefold_aliases_and_policy_subsets(reference: CpuDiagnosticReference) -> None:
    value = spec_for(reference).to_dict()
    value["references"][1] = reference.to_dict()
    with pytest.raises(ValueError, match="alias"):
        CpuDiagnosticSpec.model_validate_json(json.dumps(value))
    value = spec_for(reference).to_dict()
    value["policies"] = value["policies"][:2]
    with pytest.raises(ValueError, match="three source policies"):
        CpuDiagnosticSpec.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("size", [0, 16 * 1024 * 1024 + 1, True])
def test_plan_lock_bounds_bytes_without_type_coercion(size: int) -> None:
    with pytest.raises(ValueError):
        CpuDiagnosticLock(spec_sha256="0" * 64, spec_size_bytes=size)
