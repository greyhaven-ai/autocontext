"""Independent arithmetic oracle for the finite M18 control population.

This module neither imports the model decoder nor calls search policy helpers,
workers, materialization, or any measured report. Labels and the finite seed
roster are fixed by construction. Held-out scores are calculated only after a
selection based on practice counts; oracle facts are coordinator metadata and
are never copied into search or exhaustive-reference input bundles.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_joint_control_models import CpuJointControlTaskProvenance
from autocontext.weight_evolution.cpu_models import CpuDecoding
from autocontext.weight_evolution.cpu_search_models import CpuSearchOption, CpuSearchSpec


def joint_control_gate(accuracies: dict[str, float], *, minimum_gain: float = 0.1) -> dict[str, Any]:
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
    spec: CpuSearchSpec, provenance: CpuJointControlTaskProvenance, *, role: str, seed: int,
    context: CpuSearchOption | None = None, weight: CpuSearchOption | None = None,
    decoding: CpuSearchOption | None = None,
) -> dict[str, Any]:
    weights = list(provenance.base_weights)
    if weight is not None:
        candidate = next(c for c in spec.candidates if c.candidate_id == weight.candidate_id)
        assert candidate.program is not None
        for edit in candidate.program.edits:
            if edit.name != "projection.weight":
                raise ValueError("joint oracle supports only declared projection weight edits")
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
        raise ValueError("joint oracle role has no cases")
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
        raise ValueError("unknown joint oracle policy")
    if type(order_seed) is not int or not 0 <= order_seed <= 2**32 - 1 or policy != "seeded-random-v1" and order_seed != 0:
        raise ValueError("invalid joint oracle search-order seed")
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


def expected_joint_control_run(
    template: CpuSearchSpec, provenance: CpuJointControlTaskProvenance, *, inference_seed: int,
    policy: str, order_seed: int = 0,
) -> dict[str, Any]:
    """Predict a declared run, preserving hypothetical audit scores as oracle facts.

    The population's decoding guarantee is conditional on seeds zero and one.
    Neither seeds nor options are selected by confirmation or audit scores.
    """
    if type(inference_seed) is not int or inference_seed not in {0, 1}:
        raise ValueError("joint oracle only covers the two declared inference seeds")
    if template.model.digest != provenance.model_digest or template.baseline.output_sha256 != provenance.base_sha256:
        raise ValueError("joint oracle model or checkpoint differs from its provenance")
    cases = [{"role": case.role, "prompt": case.prompt, "expected": case.expected} for case in template.cases]
    option_payloads = [option.to_dict() for option in template.options]
    for payload in option_payloads:
        if payload["decoding"] is not None:
            payload["decoding"]["seed"] = 0
    if (canonical_digest(cases) != provenance.cases_digest
            or canonical_digest([candidate.to_dict() for candidate in template.candidates]) != provenance.candidates_digest
            or canonical_digest(option_payloads) != provenance.options_digest):
        raise ValueError("joint oracle cases, candidates or options differ from provenance")
    selected: dict[str, CpuSearchOption | None] = {}
    histories: dict[str, list[dict[str, Any]]] = {}
    references: dict[str, dict[str, Any]] = {}
    baseline = _score(template, provenance, role="practice", seed=inference_seed)
    reference_scores = {"A": baseline}
    for mechanism in ("C", "W", "D"):
        options = tuple(option for option in template.options if option.mechanism == mechanism)
        scores: dict[str | None, dict[str, Any]] = {None: baseline}
        for option in options:
            scores[option.option_id] = _score(template, provenance, role="practice", seed=inference_seed,
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
            condition: _score(template, provenance, role=role, seed=inference_seed,
                context=selected["C"] if condition in {"C", "CW"} else None,
                weight=selected["W"] if condition in {"W", "CW"} else None,
                decoding=selected["D"] if condition == "D" else None)
            for condition in ("A", "C", "W", "CW", "D")}
    gate = joint_control_gate({key: scores_by_role["confirmation"][key]["accuracy"] for key in ("A", "C", "W", "D")},
                              minimum_gain=template.minimum_confirmation_gain)
    return {"schema_version": 1, "scope": "independent-arithmetic-fixed-seed-control-oracle-v1",
            "family": provenance.family, "inference_seed": inference_seed, "policy": policy, "order_seed": order_seed,
            "selected_options": {key: option.option_id if option else None for key, option in selected.items()},
            "histories": histories, "scores": scores_by_role, "confirmation": gate,
            "audit_status": "completed" if gate["status"] == "confirmed" else "withheld",
            "audit_scores_scope": "construction-expectations-not-measured-evidence",
            "reference_practice": references, "reference_scores": reference_scores, "model_execution_performed": False,
            "gpu_execution_deferred": True, "promotion_eligible": False}
