from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_landscape_control_models import (
    LANDSCAPE_CONTROL_REPEATS,
    LANDSCAPE_CONTROL_ROSTER,
    CpuLandscapeControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_landscape_control_oracle import expected_landscape_control_run, landscape_control_gate
from autocontext.weight_evolution.cpu_landscape_control_tasks import landscape_control_task_payloads
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec

FIXTURE = Path(__file__).parent / "fixtures" / "weight_reproduction" / "cpu_landscape_control_oracle.json"


def _task(index: int = 0) -> tuple[Any, ...]:
    task_id, family, instance_index, seed = LANDSCAPE_CONTROL_ROSTER[index]
    return landscape_control_task_payloads(task_id=task_id, generation_seed=seed, family=family,
                                          instance_index=instance_index)


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("family", "instance_index", "inference_seed", "policy", "order_seed",
                                     "selected_options", "histories", "confirmation", "audit_status")} | {
        "accuracies": {role: {condition: score["accuracy"] for condition, score in scores.items()}
                       for role, scores in row["scores"].items()},
        "reference_accuracy": {key: value["accuracy"] for key, value in row["reference_scores"].items()},
        "full_run_digest": canonical_digest(row)}


@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize("repeat", LANDSCAPE_CONTROL_REPEATS)
@pytest.mark.parametrize("policy", ("adaptive-v1", "fixed-order-v1", "seeded-random-v1"))
def test_full_finite_oracle_matches_golden_with_neutral_anchors(index: int, repeat: Any, policy: str) -> None:
    spec, provenance, _ = _task(index)
    row = expected_landscape_control_run(spec, provenance, inference_seed=repeat.inference_seed, policy=policy,
                                        order_seed=repeat.search_order_seed if policy == "seeded-random-v1" else 0)
    fixture = json.loads(FIXTURE.read_text())
    key = f"task-{index + 1:02}.{repeat.repeat_id}.{policy}"
    assert _compact(row) == fixture["runs"][key]
    assert row["confirmation"]["status"] == "vetoed" and row["audit_status"] == "withheld"
    baseline = 0.5 if provenance.instance_index == 1 else 0.25
    for role, scores in row["scores"].items():
        assert [scores[key]["accuracy"] for key in ("A", "C", "D")] == [baseline] * 3
        assert scores["W"] == scores["CW"]
        assert all(case["case_id"].startswith(role) for score in scores.values() for case in score["cases"])
    assert row["selected_options"]["C"] is None and row["selected_options"]["D"] is None
    assert row["audit_scores_scope"] == "construction-expectations-not-measured-evidence"


@pytest.mark.parametrize("index", range(6))
def test_analytic_weight_contrasts_include_adaptive_wins_losses_and_nulls(index: int) -> None:
    spec, facts, _ = _task(index)
    rows = {policy: expected_landscape_control_run(spec, facts, inference_seed=0, policy=policy)
            for policy in ("adaptive-v1", "fixed-order-v1")}
    baseline = 4 if facts.instance_index == 1 else 2
    expected = {
        "informative-improvement-v1": ([baseline, baseline + 2, 8, baseline + 2],
                                      [baseline, baseline + 2, baseline + 2, baseline]),
        "misleading-improvement-v1": ([baseline, baseline + 2, baseline + 2, baseline],
                                     [baseline, baseline + 2, baseline, 8]),
        "neutral-v1": ([baseline] * 4, [baseline] * 4),
    }[facts.family]
    for row, correct in zip(rows.values(), expected, strict=True):
        assert [trial["correct"] for trial in row["histories"]["W"]] == correct
        reference = row["reference_practice"]["W"]["best_correct"]
        assert reference == (baseline if facts.family == "neutral-v1" else 8)
        selected = row["scores"]["practice"]["W"]["correct"]
        assert reference - selected == reference - max(correct)
    contrast = (rows["adaptive-v1"]["scores"]["practice"]["W"]["correct"]
                - rows["fixed-order-v1"]["scores"]["practice"]["W"]["correct"])
    magnitude = 2 if facts.instance_index == 1 else 4
    assert contrast == {"informative-improvement-v1": magnitude,
                        "misleading-improvement-v1": -magnitude, "neutral-v1": 0}[facts.family]


@pytest.mark.parametrize("index", (0, 1))
def test_optimal_repairs_apply_all_replacements(index: int) -> None:
    spec, facts, _ = _task(index)
    row = expected_landscape_control_run(spec, facts, inference_seed=0, policy="adaptive-v1")
    selected = next(option for option in spec.options if option.option_id == row["selected_options"]["W"])
    candidate = next(candidate for candidate in spec.candidates if candidate.candidate_id == selected.candidate_id)
    assert candidate.program is not None and len(candidate.program.edits[0].replacements) > 1
    assert row["scores"]["practice"]["W"]["correct"] == 8
    assert all(case["correct"] for case in row["scores"]["practice"]["W"]["cases"])


@pytest.mark.parametrize("gain,passes", [(0.1, True), (math.nextafter(0.1, 0.0), False),
                                       (math.nextafter(0.1, 1.0), True)])
def test_gate_retains_inclusive_binary64_threshold(gain: float, passes: bool) -> None:
    result = landscape_control_gate({"A": 0.0, "C": gain, "W": gain, "D": gain})
    assert (result["status"] == "confirmed") is passes


@pytest.mark.parametrize("index", (0, 2, 4))
def test_heldout_changes_cannot_change_practice_selection_or_reference(index: int) -> None:
    spec, provenance, _ = _task(index)
    original = expected_landscape_control_run(spec, provenance, inference_seed=0, policy="adaptive-v1")
    raw = spec.to_dict()
    for case in raw["cases"]:
        if case["role"] != "practice":
            case["expected"] = "one" if case["expected"] == "zero" else "zero"
    altered = CpuSearchSpec.model_validate_json(json.dumps(raw))
    facts = provenance.to_dict()
    facts["cases_digest"] = canonical_digest([{k: case[k] for k in ("role", "prompt", "expected")} for case in raw["cases"]])
    oracle = expected_landscape_control_run(altered, CpuLandscapeControlTaskProvenance.model_validate_json(json.dumps(facts)),
                                           inference_seed=0, policy="adaptive-v1")
    assert oracle["selected_options"] == original["selected_options"]
    assert oracle["histories"] == original["histories"]
    assert oracle["reference_scores"] == original["reference_scores"]
    assert oracle["scores"]["confirmation"] != original["scores"]["confirmation"]


@pytest.mark.parametrize("surface", ("cases", "options", "candidates"))
def test_oracle_rejects_unbound_task_surface(surface: str) -> None:
    spec, provenance, _ = _task()
    facts = provenance.to_dict()
    facts[f"{surface}_digest"] = "0" * 64
    with pytest.raises(ValueError, match="provenance"):
        expected_landscape_control_run(spec, CpuLandscapeControlTaskProvenance.model_validate_json(json.dumps(facts)),
                                       inference_seed=0, policy="adaptive-v1")


@pytest.mark.parametrize("surface", ("bias-sha", "weight-shape"))
def test_oracle_rejects_rehashed_tensor_semantics(surface: str) -> None:
    spec, provenance, _ = _task()
    tensor_index = 0 if surface == "bias-sha" else 1
    tensor = spec.baseline.contract.tensors[tensor_index]
    altered = tensor.model_copy(update={"sha256": "0" * 64} if surface == "bias-sha" else {"shape": (4, 4)})
    tensors = list(spec.baseline.contract.tensors)
    tensors[tensor_index] = altered
    contract = spec.baseline.contract.model_copy(update={"tensors": tuple(tensors)})
    candidates = tuple(candidate.model_copy(update={"contract": contract}) for candidate in spec.candidates)
    changed = spec.model_copy(update={"candidates": candidates})
    facts = provenance.model_copy(update={"candidates_digest": canonical_digest([c.to_dict() for c in candidates])})
    with pytest.raises(ValueError, match="tensor shape, bias or base weights"):
        expected_landscape_control_run(changed, facts, inference_seed=0, policy="adaptive-v1")


def test_oracle_never_calls_worker_decoder_or_search_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.weight_evolution import cpu_model, cpu_runner, cpu_search_policy
    from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry

    def forbid(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("oracle crossed into measured execution or policy implementation")

    monkeypatch.setattr(cpu_model, "decode_cpu_logits", forbid)
    monkeypatch.setattr(cpu_model, "infer_cpu", forbid)
    monkeypatch.setattr(cpu_runner, "run_cpu_batch", forbid)
    monkeypatch.setattr(cpu_search_policy, "choose_policy_option", forbid)
    monkeypatch.setattr(cpu_search_policy, "select_best", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    spec, provenance, _ = _task()
    row = expected_landscape_control_run(spec, provenance, inference_seed=0, policy="adaptive-v1")
    assert row["scores"]["practice"]["W"]["accuracy"] == 1.0


@pytest.mark.parametrize("seed", (-1, 2, True, 406))
def test_unpinned_inference_seeds_are_not_accepted(seed: Any) -> None:
    spec, provenance, _ = _task()
    with pytest.raises(ValueError, match="declared"):
        expected_landscape_control_run(spec, provenance, inference_seed=seed, policy="adaptive-v1")
