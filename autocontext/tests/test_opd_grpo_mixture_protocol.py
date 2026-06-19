from __future__ import annotations

import importlib
from typing import Any


def _protocol() -> Any:
    return importlib.import_module("autocontext.training.autoresearch.mixture_protocol")


def test_protocol_builds_matched_compute_matrix() -> None:
    protocol = _protocol()
    matrix = protocol.build_experiment_matrix(scenario="gsm8k", seeds=[0, 1], steps=[1000], prompts=384)

    arms = {run["arm"] for run in matrix["runs"]}
    assert arms == {"grpo", "full_opd", "positive_opd", "mixed_positive_opd_grpo"}
    assert {run["max_steps"] for run in matrix["runs"]} == {1000}
    assert {run["n_prompts"] for run in matrix["runs"]} == {384}
    assert matrix["seed_notes"] == "2 seeds: 0, 1"


def test_mixed_arm_has_recipe_but_not_default_promotion() -> None:
    protocol = _protocol()
    matrix = protocol.build_experiment_matrix(scenario="gsm8k", seeds=[0], steps=[1000], prompts=384)
    mixed = next(run for run in matrix["runs"] if run["arm"] == "mixed_positive_opd_grpo")

    assert mixed["training_mixture"] == "positive_opd=0.5,grpo=0.5"
    assert "--training-mixture positive_opd=0.5,grpo=0.5" in mixed["command"]
    assert matrix["promotion_policy"] == "Do not promote mixed mode unless held-out score improves without collapse."


def test_promotion_gate_requires_heldout_lift_without_collapse() -> None:
    protocol = _protocol()
    collapsed = protocol.summarize_mixture_results(
        [
            {"arm": "grpo", "seed": 0, "heldout_score": 0.64, "entropy": 4.0, "diversity": 0.4},
            {"arm": "mixed_positive_opd_grpo", "seed": 0, "heldout_score": 0.70, "entropy": 0.1, "diversity": 0.01},
        ]
    )
    healthy = protocol.summarize_mixture_results(
        [
            {"arm": "grpo", "seed": 0, "heldout_score": 0.64, "entropy": 4.0, "diversity": 0.4},
            {"arm": "mixed_positive_opd_grpo", "seed": 0, "heldout_score": 0.70, "entropy": 3.0, "diversity": 0.3},
        ]
    )

    assert collapsed["promotion"]["promote_mixed"] is False
    assert collapsed["promotion"]["reason"] == "collapse_detected"
    assert healthy["promotion"]["promote_mixed"] is True
    assert healthy["promotion"]["reason"] == "heldout_improved_without_collapse"


def test_report_requires_training_diagnostics_fields() -> None:
    protocol = _protocol()
    report = protocol.render_protocol_report(protocol.build_experiment_matrix("gsm8k", seeds=[0], steps=[1000]))

    for field in ["heldout_score", "response_length", "diversity", "entropy", "kl", "token_pressure", "cost_time"]:
        assert field in report
    assert "AC-787/AC-789" in report
