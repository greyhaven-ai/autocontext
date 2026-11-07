"""Independent arithmetic and finite policy-history oracle for M20.

No model decoder, search-policy helper, worker, materializer or measured report
is called. The 90 legal orderings preserve immutable option semantics. Offline
trajectories are construction expectations, never extra measured observations.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import struct
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_landscape_control_models import CpuLandscapeControlTaskProvenance
from autocontext.weight_evolution.cpu_models import CpuDecoding
from autocontext.weight_evolution.cpu_robustness_control_models import (
    ROBUSTNESS_LEGAL_ORDER_COUNT,
    ROBUSTNESS_OFFLINE_BUDGETS,
    ROBUSTNESS_OFFLINE_TRAJECTORY_COUNT,
    ROBUSTNESS_POLICY_SEEDS,
    CpuRobustnessControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_search_models import CpuSearchOption, CpuSearchSpec


def landscape_control_gate(accuracies: dict[str, float], *, minimum_gain: float = 0.1) -> dict[str, Any]:
    """The pinned binary64 comparison, including its inclusive boundary."""
    if set(accuracies) != {"A", "C", "W", "D"} or any(
        type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0
        for value in accuracies.values()
    ) or type(minimum_gain) is not float or not math.isfinite(minimum_gain) or not 0.0 < minimum_gain <= 1.0:
        raise ValueError("joint gate requires four finite accuracies and a positive bounded gain")
    outcomes = {key: accuracies[key] - accuracies["A"] >= minimum_gain for key in ("C", "W", "D")}
    return {"minimum_gain": minimum_gain, "outcomes": outcomes,
            "status": "confirmed" if all(outcomes.values()) else "vetoed"}


def _decode(logits: tuple[float, float], settings: CpuDecoding, seed: int) -> int:
    # Explicit two-class formula, independently expressed from the generic
    # worker's ranked-softmax loop. Common randomness is one draw per repeat.
    winner = 0 if logits[0] >= logits[1] else 1
    if settings.mode == "greedy" or settings.top_k == 1:
        return winner
    digest = hashlib.sha256(f"autocontext-cpu-sample-v1:{seed}".encode("ascii")).digest()
    uniform = (int.from_bytes(digest[:8], "big") >> 11) / 9007199254740992
    other_mass = math.exp(-abs(logits[0] - logits[1]) / settings.temperature)
    return winner if uniform * math.fsum((1.0, other_mass)) < 1.0 else 1 - winner


def _score(
    spec: CpuSearchSpec, provenance: CpuLandscapeControlTaskProvenance, *, role: str, seed: int,
    context: CpuSearchOption | None = None, weight: CpuSearchOption | None = None,
    decoding: CpuSearchOption | None = None,
) -> dict[str, Any]:
    weights = list(provenance.base_weights)
    if weight is not None:
        candidate = next(c for c in spec.candidates if c.candidate_id == weight.candidate_id)
        assert candidate.program is not None
        for edit in candidate.program.edits:
            if (edit.name != "projection.weight" or edit.dtype != "F32" or edit.shape != (8, 2)
                    or edit.expected_sha256 != hashlib.sha256(struct.pack("<16f", *provenance.base_weights)).hexdigest()):
                raise ValueError("landscape oracle supports only declared projection weight edits")
            for replacement in edit.replacements:
                weights[replacement.index] = struct.unpack("<f", bytes.fromhex(replacement.value_hex))[0]
    settings = decoding.decoding if decoding is not None else spec.baseline_decoding
    assert settings is not None
    cases: list[dict[str, Any]] = []
    for case in spec.cases:
        if case.role != role:
            continue
        tokens = ((context.context or "").split() if context is not None else []) + case.prompt.split()
        counts = [tokens.count(token) for token in spec.model.vocabulary]
        # Every construction value is a small integer, exactly representable;
        # count dot products therefore equal the worker's left-to-right sum.
        logits = tuple(float(sum(count * weights[index * 2 + output] for index, count in enumerate(counts)))
                       for output in (0, 1))
        prediction = spec.model.output_tokens[_decode((logits[0], logits[1]), settings, seed)]
        cases.append({"case_id": case.case_id, "prediction": prediction, "correct": prediction == case.expected})
    if not cases:
        raise ValueError("landscape oracle role has no cases")
    correct = sum(row["correct"] for row in cases)
    return {"correct": correct, "total": len(cases), "accuracy": correct / len(cases), "cases": cases}


def _order_rank(identifier: str, seed: int) -> bytes:
    data = identifier.encode()
    return hashlib.sha256(b"autocontext.cpu-search.seeded-random-v1\0" + seed.to_bytes(4, "big")
                          + len(data).to_bytes(2, "big") + data).digest()


def _selection(
    options: tuple[CpuSearchOption, ...], scores: dict[str | None, dict[str, Any]], *, policy: str, order_seed: int,
    rounds: int,
) -> tuple[CpuSearchOption | None, list[dict[str, Any]]]:
    """Independent finite simulation accepting only practice score tables."""
    if policy not in {"adaptive-v1", "fixed-order-v1", "seeded-random-v1"}:
        raise ValueError("unknown landscape oracle policy")
    if type(order_seed) is not int or not 0 <= order_seed <= 2**32 - 1 or policy != "seeded-random-v1" and order_seed != 0:
        raise ValueError("invalid landscape oracle search-order seed")
    order = list(options)
    if policy == "seeded-random-v1":
        order.sort(key=lambda option: (_order_rank(option.option_id, order_seed), option.option_id))
    trials: list[CpuSearchOption] = []
    baseline = scores[None]
    history = [{"option_id": None, "correct": baseline["correct"], "total": baseline["total"]}]
    for _ in range(rounds):
        remaining = [option for option in order if option not in trials]
        chosen = remaining[0]
        if policy == "adaptive-v1" and trials:
            last = trials[-1]
            continuation = None
            if history[-1]["correct"] > max(row["correct"] for row in history[:-1]):
                continuation = next((option for option in remaining
                                     if option.direction == last.direction and option.step == last.step + 1), None)
            unseen = next((option for option in remaining if option.direction not in {o.direction for o in trials}), None)
            chosen = continuation or unseen or chosen
        trials.append(chosen)
        score = scores[chosen.option_id]
        history.append({"option_id": chosen.option_id, "correct": score["correct"], "total": score["total"]})
    # Python max retains the first member of a tie: baseline, then earliest trial.
    best = max(history, key=lambda row: row["correct"])["option_id"]
    return next((option for option in options if option.option_id == best), None), history


def _validate_template(template: CpuSearchSpec, provenance: CpuRobustnessControlTaskProvenance) -> None:
    source = provenance.source
    if template.rounds != provenance.rounds or template.evaluation_seed != 0:
        raise ValueError("robustness oracle budget or inference seed differs from its provenance")
    if template.model.digest != source.model_digest or template.baseline.output_sha256 != source.base_sha256:
        raise ValueError("robustness oracle model or checkpoint differs from its provenance")
    cases = [{"role": case.role, "prompt": case.prompt, "expected": case.expected} for case in template.cases]
    payloads = {option.option_id: option.to_dict() for option in template.options}
    expected_ids = (*provenance.source_option_ids[:6], *provenance.w_order, *provenance.source_option_ids[12:])
    if (tuple(option.option_id for option in template.options) != expected_ids
            or set(payloads) != set(provenance.source_option_ids)
            or canonical_digest(list(payloads.values())) != provenance.options_digest
            or canonical_digest([payloads[key] for key in provenance.source_option_ids]) != source.options_digest
            or canonical_digest(cases) != source.cases_digest
            or canonical_digest([candidate.to_dict() for candidate in template.candidates]) != source.candidates_digest):
        raise ValueError("robustness oracle cases, candidates or preserved options differ from provenance")
    tensors = {tensor.name: tensor for tensor in template.baseline.contract.tensors}
    expected_tensors = {"projection.bias": ((2,), struct.pack("<ff", 0.0, 0.0), False),
                        "projection.weight": ((8, 2), struct.pack("<16f", *source.base_weights), True)}
    if (set(tensors) != set(expected_tensors) or len(template.model.vocabulary) != 8
            or template.model.output_tokens != ("zero", "one")):
        raise ValueError("robustness oracle requires its fixed binary model and tensor inventory")
    for name, (shape, tensor_payload, editable) in expected_tensors.items():
        tensor = tensors[name]
        if (tensor.dtype != "F32" or tensor.shape != shape or tensor.editable is not editable
                or tensor.sha256 != hashlib.sha256(tensor_payload).hexdigest()):
            raise ValueError("robustness oracle tensor shape, bias or base weights differ from provenance")


def expected_robustness_control_run(
    template: CpuSearchSpec, provenance: CpuRobustnessControlTaskProvenance, *, inference_seed: int = 0,
    policy: str, order_seed: int = 0,
) -> dict[str, Any]:
    """Predict a declared run, preserving hypothetical audit scores as oracle facts.

    Decoding is the unchanged top-k-one neutral anchor; random proposal order
    remains conditional on the fixed option identities and order seed.
    Neither seeds nor options are selected by confirmation or audit scores.
    """
    if type(inference_seed) is not int or inference_seed != 0:
        raise ValueError("robustness oracle only covers its declared inference seed zero")
    if type(order_seed) is not int or order_seed != 0:
        raise ValueError("measured robustness oracle only covers its declared order seed zero")
    _validate_template(template, provenance)
    source = provenance.source
    selected: dict[str, CpuSearchOption | None] = {}
    histories: dict[str, list[dict[str, Any]]] = {}
    references: dict[str, dict[str, Any]] = {}
    baseline = _score(template, source, role="practice", seed=inference_seed)
    reference_scores = {"A": baseline}
    for mechanism in ("C", "W", "D"):
        options = tuple(option for option in template.options if option.mechanism == mechanism)
        scores: dict[str | None, dict[str, Any]] = {None: baseline}
        for option in options:
            scores[option.option_id] = _score(template, source, role="practice", seed=inference_seed,
                context=option if mechanism == "C" else None, weight=option if mechanism == "W" else None,
                decoding=option if mechanism == "D" else None)
            reference_scores[option.option_id] = scores[option.option_id]
        selected[mechanism], histories[mechanism] = _selection(options, scores, policy=policy, order_seed=order_seed,
                                                               rounds=template.rounds)
        best = max(score["correct"] for score in scores.values())
        references[mechanism] = {"best_correct": best, "total": baseline["total"],
            "best_accuracy": best / baseline["total"],
            "option_scores": [{"option_id": identifier, **{k: score[k] for k in ("correct", "total", "accuracy")}}
                              for identifier, score in scores.items()]}
    scores_by_role: dict[str, dict[str, Any]] = {}
    for role in ("practice", "confirmation", "audit"):
        scores_by_role[role] = {
            condition: _score(template, source, role=role, seed=inference_seed,
                context=selected["C"] if condition in {"C", "CW"} else None,
                weight=selected["W"] if condition in {"W", "CW"} else None,
                decoding=selected["D"] if condition == "D" else None)
            for condition in ("A", "C", "W", "CW", "D")}
    gate = landscape_control_gate({key: scores_by_role["confirmation"][key]["accuracy"] for key in ("A", "C", "W", "D")},
                              minimum_gain=template.minimum_confirmation_gain)
    return {"schema_version": 1, "scope": "independent-arithmetic-robustness-oracle-v1",
            "base_task_id": provenance.base_task_id, "order_id": provenance.order_id,
            "rounds": provenance.rounds, "panel": provenance.panel, "family": provenance.family,
            "instance_index": provenance.instance_index, "inference_seed": inference_seed,
            "policy": policy, "order_seed": order_seed,
            "selected_options": {key: option.option_id if option else None for key, option in selected.items()},
            "histories": histories, "scores": scores_by_role, "confirmation": gate,
            "audit_status": "completed" if gate["status"] == "confirmed" else "withheld",
            "audit_scores_scope": "construction-expectations-not-measured-evidence",
            "reference_practice": references, "reference_scores": reference_scores, "model_execution_performed": False,
            "gpu_execution_deferred": True, "promotion_eligible": False}


def legal_weight_orders(options: tuple[CpuSearchOption, ...]) -> tuple[tuple[str, ...], ...]:
    """All 6!/2^3 orders, with each direction's first step preceding its second."""
    if len(options) != 6 or {option.mechanism for option in options} != {"W"}:
        raise ValueError("robustness order enumeration requires six weight options")
    if len({option.option_id for option in options}) != 6:
        raise ValueError("robustness order enumeration repeats an option")
    groups = {direction: {option.step for option in options if option.direction == direction}
              for direction in {option.direction for option in options}}
    if len(groups) != 3 or any(steps != {1, 2} for steps in groups.values()):
        raise ValueError("robustness order enumeration requires three two-step directions")
    orders = []
    for sequence in itertools.permutations(options):
        positions = {(option.direction, option.step): index for index, option in enumerate(sequence)}
        if all(positions[direction, 1] < positions[direction, 2] for direction in groups):
            orders.append(tuple(option.option_id for option in sequence))
    # Sorting IDs only enumerates the complete set; it never chooses a favorable
    # measured condition. IDs remain fixed, including for seeded-random-v1.
    result = tuple(sorted(orders))
    if len(result) != ROBUSTNESS_LEGAL_ORDER_COUNT:
        raise ValueError("robustness legal-order domain is not exactly ninety")
    return result


def offline_robustness_oracle(
    templates: dict[str, CpuSearchSpec], provenances: dict[str, CpuRobustnessControlTaskProvenance],
) -> dict[str, Any]:
    """Evaluate the complete finite W domain without invoking measured execution."""
    tasks: list[dict[str, Any]] = []
    invariant_names = ("score_tables_invariant", "random_order_invariant", "full_budget_zero_regret",
                       "budget_prefixes", "neutral_null")
    total = 0
    for task_id, provenance in provenances.items():
        if provenance.order_id != "canonical" or provenance.rounds != 3:
            continue
        template = templates[task_id]
        _validate_template(template, provenance)
        options = template.options_for("W")
        by_id = {option.option_id: option for option in options}
        orders = legal_weight_orders(options)
        baseline = _score(template, provenance.source, role="practice", seed=0)
        scores: dict[str | None, dict[str, Any]] = {None: baseline}
        scores.update({option.option_id: _score(template, provenance.source, role="practice", seed=0, weight=option)
                       for option in options})
        best = max(score["correct"] for score in scores.values())
        invariants = dict.fromkeys(invariant_names, True)
        random_histories: dict[int, list[dict[str, Any]]] = {}
        trajectories = []
        for order_index, identifiers in enumerate(orders):
            ordered = tuple(by_id[identifier] for identifier in identifiers)
            for option in ordered:
                invariants["score_tables_invariant"] &= scores[option.option_id] == _score(
                    template, provenance.source, role="practice", seed=0, weight=option)
            for policy, seed in ROBUSTNESS_POLICY_SEEDS:
                histories = {}
                for rounds in ROBUSTNESS_OFFLINE_BUDGETS:
                    selected, history = _selection(ordered, scores, policy=policy, order_seed=seed, rounds=rounds)
                    histories[rounds] = history
                    correct = max(row["correct"] for row in history)
                    trajectories.append({"order_index": order_index, "rounds": rounds, "policy": policy,
                        "order_seed": seed, "option_ids": [row["option_id"] for row in history[1:]],
                        "correct": [row["correct"] for row in history],
                        "selected_option": selected.option_id if selected else None,
                        "selected_correct": correct, "best_correct": best, "regret_correct": best - correct})
                    if rounds == 6:
                        invariants["full_budget_zero_regret"] &= correct == best
                    if provenance.family == "neutral-v1":
                        invariants["neutral_null"] &= all(row["correct"] == baseline["correct"] for row in history)
                invariants["budget_prefixes"] &= all(histories[rounds] == histories[6][:rounds + 1]
                                                    for rounds in ROBUSTNESS_OFFLINE_BUDGETS)
                if policy == "seeded-random-v1":
                    if seed not in random_histories:
                        random_histories[seed] = histories[6]
                    invariants["random_order_invariant"] &= histories[6] == random_histories[seed]
        invariants["all_passed"] = all(invariants.values())
        declared_orders = {"canonical": provenance.source_w_order,
                           "reverse-lanes": tuple(provenance.source_w_order[i] for i in (2, 1, 0, 5, 4, 3))}
        tasks.append({"base_task_id": provenance.base_task_id, "family": provenance.family,
                      "instance_index": provenance.instance_index, "orders": orders,
                      "declared_order_indices": {key: orders.index(value) for key, value in declared_orders.items()},
                      "score_table": {"A" if key is None else key: value for key, value in scores.items()},
                      "trajectories": trajectories, "invariants": invariants})
        total += len(trajectories)
    if len(tasks) != 6 or len({task["base_task_id"] for task in tasks}) != 6 or total != ROBUSTNESS_OFFLINE_TRAJECTORY_COUNT:
        raise ValueError("robustness offline oracle requires exactly six tasks and 6480 trajectories")
    aggregate = {key: all(task["invariants"][key] for task in tasks) for key in (*invariant_names, "all_passed")}
    return {"schema_version": 1, "scope": "finite-construction-expectations-not-measured-evidence",
            "order_count": ROBUSTNESS_LEGAL_ORDER_COUNT, "budgets": list(ROBUSTNESS_OFFLINE_BUDGETS),
            "policy_seeds": [{"policy": policy, "order_seed": seed} for policy, seed in ROBUSTNESS_POLICY_SEEDS],
            "trajectory_count": total, "base_task_count": 6, "inference_seed": 0,
            "sampling_unit": "base-task-instance", "model_execution_performed": False,
            "gpu_execution_deferred": True, "tasks": tasks, "invariants": aggregate}
