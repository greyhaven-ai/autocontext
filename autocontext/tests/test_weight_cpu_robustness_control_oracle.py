from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_robustness_control_models import (
    ROBUSTNESS_CONTROL_ROSTER,
    ROBUSTNESS_POLICY_SEEDS,
    CpuRobustnessControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_robustness_control_oracle import (
    expected_robustness_control_run,
    legal_weight_orders,
    offline_robustness_oracle,
)
from autocontext.weight_evolution.cpu_robustness_control_tasks import (
    build_robustness_oracle,
    robustness_control_task_payloads,
)
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec

FIXTURE = Path(__file__).parent / "fixtures" / "weight_reproduction" / "cpu_robustness_control_oracle.json"


def _task(index: int = 0) -> tuple[Any, ...]:
    task_id, base_task_id, family, instance_index, seed, order_id, rounds, panel = ROBUSTNESS_CONTROL_ROSTER[index]
    return robustness_control_task_payloads(task_id=task_id, base_task_id=base_task_id, generation_seed=seed,
        family=family, instance_index=instance_index, order_id=order_id, rounds=rounds, panel=panel)


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in ("task_id", "base_task_id", "order_id", "rounds", "panel", "family",
                                     "instance_index", "policy", "order_seed", "selected_options", "confirmation")} | {
        "weight_history": row["histories"]["W"],
        "practice_correct": {key: score["correct"] for key, score in row["scores"]["practice"].items()},
        "reference_digest": canonical_digest(row["reference_scores"]), "full_run_digest": canonical_digest(row)}


def _offline_compact(offline: dict[str, Any]) -> dict[str, Any]:
    return {key: offline[key] for key in ("order_count", "budgets", "policy_seeds", "trajectory_count",
                                         "base_task_count", "inference_seed", "sampling_unit", "invariants")} | {
        "tasks": [{"base_task_id": task["base_task_id"], "family": task["family"],
                   "declared_order_indices": task["declared_order_indices"],
                   "score_correct": {key: score["correct"] for key, score in task["score_table"].items()},
                   "full_task_digest": canonical_digest(task)} for task in offline["tasks"]],
        "full_offline_digest": canonical_digest(offline)}


@pytest.fixture(scope="module")
def controls() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    templates, facts = {}, {}
    for index, row in enumerate(ROBUSTNESS_CONTROL_ROSTER):
        template, provenance, _ = _task(index)
        templates[row[0]], facts[row[0]] = template, provenance
    return templates, facts, build_robustness_oracle(templates, facts)


def test_all_45_measured_expectations_and_6480_offline_trajectories_match_compact_golden(
    controls: tuple[dict[str, Any], ...],
) -> None:
    fixture = json.loads(FIXTURE.read_text())
    oracle = controls[2]
    assert len(oracle["runs"]) == 45
    assert {row["run_id"]: _compact(row) for row in oracle["runs"]} == fixture["runs"]
    assert _offline_compact(oracle["offline"]) == fixture["offline"]
    assert canonical_digest(oracle) == fixture["full_oracle_digest"]
    for row in oracle["runs"]:
        assert row["confirmation"]["status"] == "vetoed" and row["audit_status"] == "withheld"
        assert row["selected_options"]["C"] is row["selected_options"]["D"] is None
        assert row["model_execution_performed"] is False
        for scores in row["scores"].values():
            assert scores["A"] == scores["C"] == scores["D"]
            assert scores["W"] == scores["CW"]


@pytest.mark.parametrize("index", range(12))
def test_manual_core_histories_show_canonical_effects_and_reverse_order_nulls(index: int) -> None:
    template, facts, _ = _task(index)
    baseline = 4 if facts.instance_index == 1 else 2
    if facts.order_id == "canonical":
        expected = {
            "informative-improvement-v1": ([baseline, baseline + 2, 8, baseline + 2],
                                          [baseline, baseline + 2, baseline + 2, baseline]),
            "misleading-improvement-v1": ([baseline, baseline + 2, baseline + 2, baseline],
                                         [baseline, baseline + 2, baseline, 8]),
            "neutral-v1": ([baseline] * 4, [baseline] * 4),
        }[facts.family]
    else:
        expected = {
            "informative-improvement-v1": ([baseline, baseline, baseline + 2, baseline + 2],
                                          [baseline, baseline, baseline + 2, baseline + 2]),
            "misleading-improvement-v1": ([baseline, 8, 8, baseline], [baseline, 8, baseline, baseline + 2]),
            "neutral-v1": ([baseline] * 4, [baseline] * 4),
        }[facts.family]
    for policy, correct in zip(("adaptive-v1", "fixed-order-v1"), expected, strict=True):
        row = expected_robustness_control_run(template, facts, policy=policy)
        assert [trial["correct"] for trial in row["histories"]["W"]] == correct
        assert row["scores"]["practice"]["W"]["correct"] == max(correct)


def test_complete_order_budget_seed_domain_and_independent_invariants(controls: tuple[dict[str, Any], ...]) -> None:
    templates, facts, oracle = controls
    offline = oracle["offline"]
    assert offline["trajectory_count"] == 6480
    assert all(offline["invariants"].values())
    for task in offline["tasks"]:
        template = templates[f"{task['base_task_id']}.canonical.r3"]
        provenance = facts[f"{task['base_task_id']}.canonical.r3"]
        assert len(task["orders"]) == len(set(task["orders"])) == 90
        options = {option.option_id: option for option in template.options_for("W")}
        for identifiers in task["orders"]:
            assert set(identifiers) == set(options)
            positions = {(options[key].direction, options[key].step): index for index, key in enumerate(identifiers)}
            assert all(positions[direction, 1] < positions[direction, 2] for direction in ("lane-01", "lane-02", "lane-03"))
        assert task["orders"][task["declared_order_indices"]["canonical"]] == provenance.source_w_order
        assert task["orders"][task["declared_order_indices"]["reverse-lanes"]] == tuple(
            provenance.source_w_order[index] for index in (2, 1, 0, 5, 4, 3))
        trajectories = {(row["order_index"], row["rounds"], row["policy"], row["order_seed"]): row
                        for row in task["trajectories"]}
        assert len(trajectories) == 1080
        assert set(trajectories) == {(index, rounds, policy, seed) for index in range(90)
                                     for rounds in (1, 3, 6) for policy, seed in ROBUSTNESS_POLICY_SEEDS}
        for (index, rounds, policy, seed), row in trajectories.items():
            full = trajectories[index, 6, policy, seed]
            assert row["option_ids"] == full["option_ids"][:rounds]
            assert row["correct"] == full["correct"][:rounds + 1]
            assert full["regret_correct"] == 0 and set(full["option_ids"]) == set(options)
            assert row["correct"] == [task["score_table"][key]["correct"] for key in ["A", *row["option_ids"]]]
            if policy == "seeded-random-v1":
                assert row["option_ids"] == trajectories[0, rounds, policy, seed]["option_ids"]
            if task["family"] == "neutral-v1":
                assert row["selected_option"] is None and row["regret_correct"] == 0
        # The finite construction does not require adaptive advantage across orders.
        contrasts = {trajectories[index, 3, "adaptive-v1", 0]["selected_correct"]
                     - trajectories[index, 3, "fixed-order-v1", 0]["selected_correct"] for index in range(90)}
        assert 0 in contrasts
        assert (len(contrasts) > 1) is (task["family"] != "neutral-v1")


def test_reference_scores_are_identical_across_orders_and_budget_variants(controls: tuple[dict[str, Any], ...]) -> None:
    reference_by_base: dict[str, Any] = {}
    for row in controls[2]["runs"]:
        reference = row["reference_scores"]
        assert len(reference) == 19
        if row["base_task_id"] not in reference_by_base:
            reference_by_base[row["base_task_id"]] = reference
        assert reference == reference_by_base[row["base_task_id"]]
        if row["rounds"] == 6:
            for mechanism in ("C", "W", "D"):
                assert row["scores"]["practice"][mechanism]["correct"] == row["reference_practice"][mechanism]["best_correct"]


def test_heldout_changes_cannot_change_practice_history_or_reference() -> None:
    template, provenance, _ = _task()
    original = expected_robustness_control_run(template, provenance, policy="adaptive-v1")
    raw, facts = template.to_dict(), provenance.to_dict()
    for case in raw["cases"]:
        if case["role"] != "practice":
            case["expected"] = "one" if case["expected"] == "zero" else "zero"
    facts["source"]["cases_digest"] = canonical_digest([
        {key: case[key] for key in ("role", "prompt", "expected")} for case in raw["cases"]])
    altered = expected_robustness_control_run(CpuSearchSpec.model_validate_json(json.dumps(raw)),
        CpuRobustnessControlTaskProvenance.model_validate_json(json.dumps(facts)), policy="adaptive-v1")
    assert altered["histories"] == original["histories"]
    assert altered["selected_options"] == original["selected_options"]
    assert altered["reference_scores"] == original["reference_scores"]
    assert altered["scores"]["confirmation"] != original["scores"]["confirmation"]


def test_oracle_never_invokes_measured_worker_decoder_policy_or_materializer(monkeypatch: pytest.MonkeyPatch) -> None:
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
    templates, facts = {}, {}
    for index, row in enumerate(ROBUSTNESS_CONTROL_ROSTER):
        template, provenance, _ = _task(index)
        templates[row[0]], facts[row[0]] = template, provenance
    oracle = build_robustness_oracle(templates, facts)
    assert oracle["offline"]["trajectory_count"] == 6480
    assert oracle["offline"]["model_execution_performed"] is False


@pytest.mark.parametrize("field,value", [("inference_seed", True), ("inference_seed", 1), ("inference_seed", -1),
    ("order_seed", True), ("order_seed", 1), ("order_seed", -1)])
def test_measured_expectations_accept_only_pinned_seed_zero(field: str, value: Any) -> None:
    template, facts, _ = _task()
    with pytest.raises(ValueError, match="declared"):
        expected_robustness_control_run(template, facts, policy="seeded-random-v1", **{field: value})


@pytest.mark.parametrize("surface", ("cases", "options", "candidates"))
def test_unbound_semantic_surfaces_are_rejected(surface: str) -> None:
    template, facts, _ = _task()
    raw = facts.to_dict()
    raw["source"][surface + "_digest"] = "0" * 64
    with pytest.raises(ValueError, match="provenance"):
        expected_robustness_control_run(template,
            CpuRobustnessControlTaskProvenance.model_validate_json(json.dumps(raw)), policy="adaptive-v1")


@pytest.mark.parametrize("surface", ("not-weight", "missing", "repeated", "bad-directions"))
def test_order_enumeration_rejects_changed_domain(surface: str) -> None:
    template, _, _ = _task()
    options = template.options_for("W")
    if surface == "not-weight":
        options = template.options_for("C")
    elif surface == "missing":
        options = options[:-1]
    elif surface == "repeated":
        options = (*options[:-1], options[0])
    else:
        options = tuple(option.model_copy(update={"direction": "one-lane"}) for option in options)
    with pytest.raises(ValueError):
        legal_weight_orders(options)


def test_offline_domain_refuses_missing_base_instance(controls: tuple[dict[str, Any], ...]) -> None:
    templates, facts, _ = controls
    with pytest.raises(ValueError, match="exactly six"):
        offline_robustness_oracle(templates, {key: value for key, value in facts.items() if value.base_task_id != "task-06"})
