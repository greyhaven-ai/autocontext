from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign_tasks import _checkpoint
from autocontext.weight_evolution.cpu_joint_control_models import (
    JOINT_CONTROL_FAMILIES,
    JOINT_CONTROL_REPEATS,
    JOINT_CONTROL_TASK_SEEDS,
    CpuJointControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_joint_control_oracle import expected_joint_control_run, joint_control_gate
from autocontext.weight_evolution.cpu_joint_control_tasks import joint_control_task_payloads
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec

FIXTURE = Path(__file__).parent / "fixtures" / "weight_reproduction" / "cpu_joint_control_oracle.json"


def _task(index: int = 0) -> tuple[Any, ...]:
    family = JOINT_CONTROL_FAMILIES[index]
    return joint_control_task_payloads(task_id=f"task-{index + 1:02}", generation_seed=JOINT_CONTROL_TASK_SEEDS[family],
                                       family=family)


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("family", "inference_seed", "policy", "order_seed", "selected_options",
                                     "confirmation", "audit_status")} | {
        "accuracies": {role: {condition: score["accuracy"] for condition, score in scores.items()}
                       for role, scores in row["scores"].items()},
        "reference_accuracy": {key: value["accuracy"] for key, value in row["reference_scores"].items()}}


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("repeat", JOINT_CONTROL_REPEATS)
@pytest.mark.parametrize("policy", ("adaptive-v1", "fixed-order-v1", "seeded-random-v1"))
def test_full_finite_oracle_matches_golden_and_declared_family(index: int, repeat: Any, policy: str) -> None:
    spec, provenance, _ = _task(index)
    row = expected_joint_control_run(spec, provenance, inference_seed=repeat.inference_seed, policy=policy,
                                    order_seed=repeat.search_order_seed if policy == "seeded-random-v1" else 0)
    fixture = json.loads(FIXTURE.read_text())
    key = f"task-{index + 1:02}.{repeat.repeat_id}.{policy}"
    assert _compact(row) == fixture["runs"][key]
    assert row["confirmation"]["status"] == ("confirmed" if index == 0 else "vetoed")
    assert row["audit_status"] == ("completed" if index == 0 else "withheld")
    scores = row["scores"]["confirmation"]
    if index == 0:
        assert [scores[key]["accuracy"] for key in ("A", "C", "W", "D")] == [0.5, 0.75, 0.75, 1.0]
    else:
        assert all(score["accuracy"] == 1.0 for score in scores.values())
    assert row["audit_scores_scope"] == "construction-expectations-not-measured-evidence"


@pytest.mark.parametrize("failed", ("C", "W", "D"))
def test_single_component_veto_fixture(failed: str) -> None:
    accuracies = {"A": 0.5, "C": 0.75, "W": 0.75, "D": 1.0}
    accuracies[failed] = 0.5
    result = joint_control_gate(accuracies)
    assert result == json.loads(FIXTURE.read_text())["single_component_veto"][failed]
    assert result["outcomes"] == {key: key != failed for key in ("C", "W", "D")}
    assert result["status"] == "vetoed"


@pytest.mark.parametrize("failed", ("C", "W", "D"))
def test_neutralizing_one_option_surface_vetoes_only_that_component(failed: str) -> None:
    spec, provenance, _ = _task()
    raw = spec.to_dict()
    if failed == "C":
        for index, option in enumerate(raw["options"]):
            if option["mechanism"] == "C":
                feature = provenance.feature_indices[6 + index // 2]
                option["context"] = " ".join([spec.model.vocabulary[feature]] * option["step"])
    elif failed == "D":
        for option in raw["options"]:
            if option["mechanism"] == "D":
                option["decoding"]["top_k"] = 1
    else:
        # Context-hint rows never occur in bare task prompts. Distinct edits to
        # that row therefore remain real checkpoint choices with zero W gain.
        coordinate = provenance.feature_indices[4] * 2 + 1
        for ordinal, candidate in enumerate(raw["candidates"][1:], 1):
            candidate["program"]["edits"][0]["replacements"] = [
                {"index": coordinate, "value_hex": struct.pack("<f", float(ordinal)).hex()}]
            weights = list(provenance.base_weights)
            weights[coordinate] = float(ordinal)
            checkpoint, _ = _checkpoint(tuple(weights))
            candidate.update(output_sha256=hashlib.sha256(checkpoint).hexdigest(), output_size_bytes=len(checkpoint))
    altered = CpuSearchSpec.model_validate_json(json.dumps(raw))
    facts = provenance.to_dict()
    facts["options_digest"] = canonical_digest(raw["options"])
    facts["candidates_digest"] = canonical_digest(raw["candidates"])
    result = expected_joint_control_run(altered, CpuJointControlTaskProvenance.model_validate_json(json.dumps(facts)),
                                        inference_seed=0, policy="adaptive-v1")
    assert result["confirmation"] == json.loads(FIXTURE.read_text())["single_component_veto"][failed]
    assert result["selected_options"][failed] is None
    assert result["audit_status"] == "withheld"


@pytest.mark.parametrize("gain,passes", [(0.1, True), (math.nextafter(0.1, 0.0), False),
                                       (math.nextafter(0.1, 1.0), True)])
def test_threshold_boundary_uses_inclusive_binary64_comparison(gain: float, passes: bool) -> None:
    result = joint_control_gate({"A": 0.0, "C": gain, "W": gain, "D": gain})
    assert (result["status"] == "confirmed") is passes


def test_heldout_changes_cannot_change_practice_selection() -> None:
    spec, provenance, _ = _task()
    original = expected_joint_control_run(spec, provenance, inference_seed=0, policy="adaptive-v1")
    raw = spec.to_dict()
    for case in raw["cases"]:
        if case["role"] != "practice":
            case["expected"] = "one" if case["expected"] == "zero" else "zero"
    altered = CpuSearchSpec.model_validate_json(json.dumps(raw))
    facts = provenance.to_dict()
    facts["cases_digest"] = canonical_digest([{k: case[k] for k in ("role", "prompt", "expected")} for case in raw["cases"]])
    oracle = expected_joint_control_run(altered, CpuJointControlTaskProvenance.model_validate_json(json.dumps(facts)),
                                       inference_seed=0, policy="adaptive-v1")
    assert oracle["selected_options"] == original["selected_options"]
    assert oracle["histories"] == original["histories"]
    assert oracle["reference_scores"] == original["reference_scores"]
    assert oracle["confirmation"] != original["confirmation"]


@pytest.mark.parametrize("surface", ("cases", "options", "candidates"))
def test_oracle_rejects_unbound_task_surface(surface: str) -> None:
    spec, provenance, _ = _task()
    facts = provenance.to_dict()
    facts[f"{surface}_digest"] = "0" * 64
    with pytest.raises(ValueError, match="provenance"):
        expected_joint_control_run(spec, CpuJointControlTaskProvenance.model_validate_json(json.dumps(facts)),
                                   inference_seed=0, policy="adaptive-v1")


def test_oracle_never_calls_worker_decoder_or_search_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.weight_evolution import cpu_model, cpu_runner, cpu_search_policy

    def forbid(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("oracle crossed into measured execution or policy implementation")

    monkeypatch.setattr(cpu_model, "decode_cpu_logits", forbid)
    monkeypatch.setattr(cpu_model, "infer_cpu", forbid)
    monkeypatch.setattr(cpu_runner, "run_cpu_batch", forbid)
    monkeypatch.setattr(cpu_search_policy, "choose_policy_option", forbid)
    monkeypatch.setattr(cpu_search_policy, "select_best", forbid)
    spec, provenance, _ = _task()
    result = expected_joint_control_run(spec, provenance, inference_seed=0, policy="adaptive-v1")
    assert result["confirmation"]["status"] == "confirmed"


@pytest.mark.parametrize("seed", (-1, 2, True, 406))
def test_unpinned_inference_seeds_are_not_accepted(seed: Any) -> None:
    spec, provenance, _ = _task()
    with pytest.raises(ValueError, match="declared"):
        expected_joint_control_run(spec, provenance, inference_seed=seed, policy="adaptive-v1")
