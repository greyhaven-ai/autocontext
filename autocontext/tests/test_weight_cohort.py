from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import CheckpointReceipt
from autocontext.weight_evolution.cohort import (
    ReplayLimits,
    ReplayPlan,
    build_replay_plan,
    estimate_report_bytes,
    load_replay_plan,
    validate_plan_datasets,
)
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem, VerifiedDataset, cohort_digest
from autocontext.weight_evolution.models import load_reproduction_lock
from autocontext.weight_evolution.runtime import load_runtime_lock

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/weight_evolution/qwen_reproduction"


def replay_context(*, count: int = 3, mode: str = "reproduction", limits: dict[str, Any] | None = None) -> SimpleNamespace:
    lock = load_reproduction_lock(EXAMPLE / "reproduction.lock.json")
    runtime = load_runtime_lock(EXAMPLE / "runtime.lock.json")
    problems = tuple(EvaluationProblem(problem_id=f"synthetic/{i}", text=f"Synthetic problem {i}", answer=str(i))
                     for i in range(count))
    dataset = VerifiedDataset("math500", cohort_digest(problems), problems, "a" * 64, "b" * 64)
    receipts = tuple(CheckpointReceipt(
        variant, lock.digest, str(index) * 64, str(index + 2) * 64, (), (), (), (), 0, 0,
    ) for index, variant in enumerate(("base", "published_patch"), 1))
    bounds = ReplayLimits(**{
        "max_shard_problems": 1, "max_total_generated_tokens": 1000000000,
        "max_total_wall_seconds": 10000000.0, **(limits or {}),
    })
    plan = build_replay_plan(
        lock, runtime, (dataset,), receipts, scorer_lock_digest="c" * 64, published_results_digest="d" * 64,
        limits=bounds, mode=mode, smoke_max_new_tokens=16 if mode == "smoke" else None,  # type: ignore[arg-type]
        smoke_problem_limit=2,
    )
    return SimpleNamespace(lock=lock, runtime=runtime, dataset=dataset, receipts=receipts, limits=bounds, plan=plan)


def test_replay_plan_covers_every_problem_once_per_variant_and_alternates_first_arm() -> None:
    context = replay_context(count=3)
    plan = context.plan
    assert len(plan.shards) == 6
    assert [s.request.variant for s in plan.shards] == [
        "base", "published_patch", "published_patch", "base", "base", "published_patch",
    ]
    for variant in ("base", "published_patch"):
        ids = [p.problem_id for s in plan.shards if s.request.variant == variant for p in s.request.problems]
        assert ids == [p.problem_id for p in context.dataset.problems]
    assert plan.total_generated_tokens == 3 * 2 * 4 * 32768
    assert {identity.variant for identity in plan.checkpoints} == {"base", "published_patch"}
    assert len({identity.receipt_digest for identity in plan.checkpoints}) == 2
    validate_plan_datasets(plan, (context.dataset,))


def test_inference_projection_omits_math_gold_and_ifeval_instruction_metadata() -> None:
    context = replay_context(count=1)
    instruction = DatasetInstruction(instruction_id="keywords:existence", kwargs={"keywords": ["secret-marker"]})
    problem = EvaluationProblem(problem_id="ifeval-synthetic", text="Say hello.", instructions=(instruction,))
    dataset = VerifiedDataset("ifeval", cohort_digest((problem,)), (problem,), "a" * 64, "b" * 64)
    plan = build_replay_plan(context.lock, context.runtime, (context.dataset, dataset), context.receipts,
                             scorer_lock_digest="c" * 64, published_results_digest="d" * 64, limits=context.limits)
    for shard in plan.shards:
        raw = json.dumps(shard.request.to_dict())
        assert '"answer"' not in raw and '"instructions"' not in raw and "secret-marker" not in raw
        assert set(shard.request.problems[0].to_dict()) == {"problem_id", "text"}


def test_identical_inputs_generate_identical_plan_and_digest() -> None:
    assert replay_context().plan.to_dict() == replay_context().plan.to_dict()
    assert replay_context().plan.digest == replay_context().plan.digest


@pytest.mark.parametrize("boundary", ["problems", "tokens", "report"])
def test_each_shard_boundary_independently_splits_the_cohort(boundary: str) -> None:
    limits: dict[str, Any] = {"max_shard_problems": 10}
    if boundary == "problems":
        limits["max_shard_problems"] = 2
    elif boundary == "tokens":
        limits["max_shard_generated_tokens"] = 2 * 4 * 32768
    else:
        one = replay_context(count=1).plan.shards[0].estimated_report_bytes
        limits["max_report_bytes"] = one
    plan = replay_context(count=5, limits=limits).plan
    expected = [2, 2, 1] if boundary != "report" else [1] * 5
    assert [len(s.request.problems) for s in plan.shards if s.request.variant == "base"] == expected
    assert all(s.estimated_report_bytes == estimate_report_bytes(s.request) for s in plan.shards)


@pytest.mark.parametrize("limits", [{"max_report_bytes": 65536}, {"max_shard_generated_tokens": 1}])
def test_single_problem_that_exceeds_bounds_fails_instead_of_truncating(limits: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="one problem cannot fit"):
        replay_context(count=1, limits=limits)


@pytest.mark.parametrize("limits", [{"max_total_generated_tokens": 1}, {"max_total_wall_seconds": 1.0}])
def test_total_reservations_must_fit_explicit_study_budget(limits: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="total"):
        replay_context(count=1, limits=limits)


def test_smoke_mode_labels_reduced_coverage_and_decoding_budget() -> None:
    plan = replay_context(count=5, mode="smoke").plan
    assert plan.mode == "smoke" and plan.cohorts[0].complete_dataset is False
    assert len(plan.cohorts[0].problem_ids) == 2
    assert all(s.request.mode == "smoke" and s.request.smoke_max_new_tokens == 16 for s in plan.shards)
    assert plan.resource_summary()["max_generated_tokens"] == 2 * 2 * 4 * 16
    assert plan.resource_summary()["model_execution_performed"] is False


@pytest.mark.parametrize("mutation", ["missing_arm", "duplicate_shard", "cohort_id", "ordinal", "paired_prompt", "budget",
                                      "report_estimate", "checkpoint_variant", "mode", "coverage"])
def test_saved_plan_rejects_inconsistent_or_tampered_coverage_and_budgets(mutation: str) -> None:
    raw = replay_context().plan.to_dict()
    if mutation == "missing_arm":
        raw["shards"].pop()
    elif mutation == "duplicate_shard":
        raw["shards"].append(raw["shards"][0])
    elif mutation == "cohort_id":
        raw["cohorts"][0]["problem_ids"][0] = "other"
    elif mutation == "ordinal":
        raw["shards"][0]["ordinal"] = 10
    elif mutation == "paired_prompt":
        raw["shards"][0]["request"]["problems"][0]["text"] = "Modified input"
    elif mutation == "budget":
        raw["limits"]["max_total_generated_tokens"] = 1
    elif mutation == "report_estimate":
        raw["shards"][0]["estimated_report_bytes"] -= 1
    elif mutation == "checkpoint_variant":
        raw["checkpoints"][1]["variant"] = "base"
    elif mutation == "mode":
        raw["mode"] = "smoke"
    else:
        raw["cohorts"][0]["complete_dataset"] = False
    with pytest.raises(ValueError):
        ReplayPlan.model_validate_json(json.dumps(raw))


def test_dataset_binding_rejects_foreign_digest_and_forged_prompt() -> None:
    context = replay_context(count=1)
    foreign = VerifiedDataset("math500", "f" * 64, context.dataset.problems, "a" * 64, "b" * 64)
    with pytest.raises(ValueError, match="dataset identity"):
        validate_plan_datasets(context.plan, (foreign,))
    raw = context.plan.to_dict()
    for shard in raw["shards"]:
        shard["request"]["problems"][0]["text"] = "Changed prompt!!!!!!!"
        request = context.plan.shards[0].request.model_copy(update={
            "problems": (context.plan.shards[0].request.problems[0].model_copy(update={"text": "Changed prompt!!!!!!!"}),),
        })
        shard["estimated_report_bytes"] = estimate_report_bytes(request)
    forged = ReplayPlan.model_validate_json(json.dumps(raw))
    with pytest.raises(ValueError, match="prompt differs"):
        validate_plan_datasets(forged, (context.dataset,))


def test_plan_loader_checks_duplicate_json_and_roundtrips(tmp_path: Path) -> None:
    plan = replay_context().plan
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan.to_dict()))
    assert load_replay_plan(path) == plan
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="duplicate"):
        load_replay_plan(path)


def test_checkpoint_and_dataset_lock_mismatches_rejected_before_planning() -> None:
    context = replay_context(count=1)
    wrong = VerifiedDataset("gsm8k", context.dataset.digest, context.dataset.problems, "a" * 64, "e" * 64)
    with pytest.raises(ValueError, match="share one verified"):
        build_replay_plan(context.lock, context.runtime, (context.dataset, wrong), context.receipts,
                          scorer_lock_digest="c" * 64, published_results_digest="d" * 64, limits=context.limits)
    changed = context.receipts[0].to_dict()
    wrong_receipt = SimpleNamespace(**changed, digest="e" * 64)
    wrong_receipt.lock_digest = "e" * 64
    with pytest.raises(ValueError, match="checkpoint receipt differs"):
        build_replay_plan(context.lock, context.runtime, (context.dataset,), (wrong_receipt, context.receipts[1]),
                          scorer_lock_digest="c" * 64, published_results_digest="d" * 64, limits=context.limits)
