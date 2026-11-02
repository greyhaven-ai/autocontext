"""Synthetic evidence exercises the same receipt boundaries as archived runs."""

from __future__ import annotations

import hashlib
import json
import os
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest

from autocontext.weight_evolution import evaluation as evaluation_module
from autocontext.weight_evolution.cohort import (
    CheckpointIdentity,
    CohortIdentity,
    ReplayLimits,
    ReplayPlan,
    ReplayShard,
    estimate_report_bytes,
)
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem, VerifiedDataset, cohort_digest
from autocontext.weight_evolution.evaluation import (
    EvidenceError,
    evaluate_reproduction,
    read_shard_evidence,
    validate_run_evidence,
)
from autocontext.weight_evolution.inference_models import (
    GenerationSample,
    InferenceProblem,
    InferenceRequest,
    InferenceResult,
    text_digest,
)
from autocontext.weight_evolution.reference import load_published_results
from autocontext.weight_evolution.report import render_reproduction_report, write_reproduction_report
from autocontext.weight_evolution.runtime import REQUIRED_PACKAGES

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "qwen_reproduction"


class FixtureScorer:
    lock_digest = "a" * 64
    execution_kind = "fixture"

    def __init__(self) -> None:
        self.validated: list[str] = []
        self.scored: list[str] = []

    def provenance(self):
        return {"execution_kind": self.execution_kind, "lock_digest": self.lock_digest, "implementation": "synthetic"}

    def validate_cohort(self, dataset, problems):
        assert all(problem.answer for problem in problems)
        self.validated.append(dataset)

    def score_sample(self, dataset, problem, sample):
        assert dataset in self.validated
        self.scored.append(problem.problem_id)
        return int(sample.text == problem.answer)


def _case(tmp_path: Path, *, mode: str = "smoke", kind: str = "fixture") -> SimpleNamespace:
    problems = tuple(EvaluationProblem(problem_id=f"p{i}", text=f"Question {i}?", answer=str(i)) for i in range(2))
    dataset = VerifiedDataset("math500", cohort_digest(problems), problems, "b" * 64, "c" * 64)
    published = load_published_results(EXAMPLE / "published-results.lock.json")
    # Synthetic reference sizes permit discrepancy arithmetic tests without
    # pretending these two problems are the released MATH-500 dataset.
    published = published.model_copy(update={"metrics": tuple(
        metric.model_copy(update={"n_problems": 2}) if metric.dataset == "math500" else metric
        for metric in published.metrics
    )})
    checkpoints = tuple(CheckpointIdentity(
        variant=variant, receipt_digest=digit * 64, checkpoint_sha256=digit * 64, tensor_content_digest=digit * 64,
    ) for variant, digit in (("base", "d"), ("published_patch", "e")))
    shards = []
    for ordinal, problem in enumerate(problems):
        for variant in ("base", "published_patch"):
            request = InferenceRequest(
                variant=variant, dataset="math500", dataset_sha256=dataset.digest,
                problems=(InferenceProblem(problem_id=problem.problem_id, text=problem.text),),
                mode=mode, smoke_max_new_tokens=8 if mode == "smoke" else None,
            )
            shards.append(ReplayShard(shard_id=f"math500-{ordinal:05d}-{variant}", ordinal=ordinal,
                                      request=request, estimated_report_bytes=estimate_report_bytes(request)))
    limits = ReplayLimits(max_shard_problems=1, max_total_generated_tokens=1000000, max_total_wall_seconds=10000.0)
    plan = ReplayPlan(
        lock_digest="1" * 64, runtime_digest="2" * 64,
        runtime_image="vllm/vllm-openai@sha256:" + "3" * 64,
        dataset_lock_digest=dataset.lock_digest, scorer_lock_digest=FixtureScorer.lock_digest,
        published_results_digest=published.digest, mode=mode,
        cohorts=(CohortIdentity(dataset="math500", dataset_sha256=dataset.digest,
                                problem_ids=tuple(p.problem_id for p in problems), complete_dataset=True),),
        checkpoints=checkpoints, shards=tuple(shards), limits=limits,
    )
    runtime = {"python_version": "3.12.11", "platform": "linux/amd64",
               "packages": {name: "1.0.0" for name in REQUIRED_PACKAGES}}
    runs = {}
    for shard in plan.shards:
        request = shard.request
        ordinal = shard.ordinal
        cap = request.smoke_max_new_tokens or 32768
        pin = next(pin for pin in checkpoints if pin.variant == request.variant)
        # Base: problem means .25, 0. Patch: problem means 1, .5.
        n_correct = (1 if ordinal == 0 else 0) if request.variant == "base" else (4 if ordinal == 0 else 2)
        prompt = f"<user>{request.problems[0].text}</user><think>"
        samples = tuple(GenerationSample(
            problem_id=f"p{ordinal}", sample_index=index, rendered_prompt=prompt, prompt_sha256=text_digest(prompt),
            prompt_token_ids=(10 + ordinal, 20), input_tokens=2,
            text=str(ordinal) if index < n_correct else "wrong",
            response_sha256=text_digest(str(ordinal) if index < n_correct else "wrong"),
            token_ids=(30, 31), output_tokens=2, finish_reason="stop", stop_reason=248044,
        ) for index in range(4))
        result = InferenceResult(
            execution_kind=kind, lock_digest=plan.lock_digest, runtime_digest=plan.runtime_digest,
            runtime_image=plan.runtime_image, runtime_environment=runtime,
            checkpoint_receipt_digest=pin.receipt_digest, checkpoint_sha256=pin.checkpoint_sha256,
            tensor_content_digest=pin.tensor_content_digest, request_digest=request.digest, request=request,
            max_new_tokens=cap, samples_per_problem=4,
            sampling={"n": 4, "temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5,
                      "max_tokens": cap, "seed": 0, "stop": None, "stop_token_ids": None, "ignore_eos": False},
            elapsed_seconds=1.0, hardware={"accelerator": "cuda", "device_name": "fixture GPU"}, samples=samples,
        )
        report = {"ok": True, "operation": "infer", "lock_digest": plan.lock_digest,
                  "runtime_digest": plan.runtime_digest, "request_digest": request.digest,
                  "observed_runtime": runtime, "inference": result.to_dict()}
        receipt = {
            "ok": True, "operation": "infer", "lock_digest": plan.lock_digest,
            "runtime_digest": plan.runtime_digest, "request_digest": request.digest,
            "image": plan.runtime_image, "variant": request.variant, "mode": mode,
            "model_execution_performed": kind == "vllm", "network": "none", "scope": "development_replay",
            "problems": 1, "samples": 4, "max_generated_tokens": cap * 4,
            "limits": {"max_report_bytes": limits.max_report_bytes, "timeout_seconds": limits.shard_timeout_seconds,
                       "memory_mb": limits.memory_mb, "cpu_count": limits.cpu_count},
            "image_identity": {"platform": "linux/amd64", "repo_digests": [plan.runtime_image], "id": "sha256:" + "4" * 64},
            "harness_digest": "5" * 64, "elapsed_seconds": 2.0,
        }
        directory = tmp_path / shard.shard_id
        directory.mkdir()
        (directory / "report.json").write_text(json.dumps(report))
        receipt["report_sha256"] = hashlib.sha256((directory / "report.json").read_bytes()).hexdigest()
        (directory / "execution.receipt.json").write_text(json.dumps(receipt))
        runs[shard.shard_id] = directory
    return SimpleNamespace(plan=plan, dataset=dataset, published=published, runs=runs, scorer=FixtureScorer())


def _evaluate(case, **kwargs):
    return evaluate_reproduction(case.plan, {"math500": case.dataset}, case.scorer, case.runs,
                                 published=case.published, allow_fixture=True, **kwargs)


def _mutate(case, mutate, *, recompute=True):
    path = case.runs[case.plan.shards[0].shard_id]
    report = json.loads((path / "report.json").read_bytes())
    receipt = json.loads((path / "execution.receipt.json").read_bytes())
    mutate(report, receipt)
    (path / "report.json").write_text(json.dumps(report))
    if recompute:
        receipt["report_sha256"] = hashlib.sha256((path / "report.json").read_bytes()).hexdigest()
    (path / "execution.receipt.json").write_text(json.dumps(receipt))


def test_grouped_mean_pairing_and_fixture_provenance(tmp_path: Path):
    case = _case(tmp_path)
    report = _evaluate(case).to_dict()
    comparison = report["comparisons"][0]
    assert comparison["base"]["mean_sample_accuracy"] == 0.125
    assert comparison["published_patch"]["mean_sample_accuracy"] == 0.75
    assert comparison["paired_effect"]["mean"] == 0.625
    assert comparison["base"]["problem_scores"] == {"p0": 0.25, "p1": 0.0}
    assert comparison["base"]["worker_elapsed_seconds"] == 4.0
    assert comparison["base"]["p95_generated_tokens"] == 2
    assert report["evidence_kind"] == "fixture"
    assert report["behavioral_reproduction_claim"] is False
    assert report["promotion_decision"] is None
    assert len(report["samples"]) == 16
    assert len(report["shards"]) == 4
    assert comparison["published_comparison"]["base"]["accuracy_difference"] is None


def test_read_validator_is_reusable_for_resume(tmp_path: Path):
    case = _case(tmp_path)
    shard = case.plan.shards[0]
    result = validate_run_evidence(case.plan, shard, case.runs[shard.shard_id], allow_fixture=True)
    assert result.request == shard.request


@pytest.mark.parametrize("boundary", ["scorer", "model"])
def test_fixture_evidence_requires_explicit_opt_in(tmp_path: Path, boundary: str):
    case = _case(tmp_path)
    if boundary == "model":
        case.scorer.execution_kind = "official"
    with pytest.raises(EvidenceError, match="fixture"):
        evaluate_reproduction(case.plan, {"math500": case.dataset}, case.scorer, case.runs, published=case.published)


def test_smoke_and_full_replay_classification_are_distinct(tmp_path: Path):
    case = _case(tmp_path, mode="reproduction", kind="vllm")
    case.scorer.execution_kind = "official"
    report = _evaluate(case).to_dict()
    assert report["evidence_kind"] == "full_cohort_replay"
    source = report["comparisons"][0]["published_comparison"]
    assert source["same_full_cohort_size"] is True
    assert source["base"]["accuracy_difference"] == pytest.approx(0.125 - source["base"]["accuracy"])
    assert report["behavioral_reproduction_claim"] is False


def test_official_smoke_still_cannot_claim_full_reproduction(tmp_path: Path):
    case = _case(tmp_path, kind="vllm")
    case.scorer.execution_kind = "official"
    report = _evaluate(case).to_dict()
    assert report["evidence_kind"] == "smoke"
    assert report["comparisons"][0]["published_comparison"]["base"]["accuracy_difference"] is None


@pytest.mark.parametrize("mutation", [
    lambda r, e: r.update(ok=False),
    lambda r, e: e.update(ok=False),
    lambda r, e: r.update(lock_digest="0" * 64),
    lambda r, e: e.update(request_digest="0" * 64),
    lambda r, e: e.update(model_execution_performed=True),
    lambda r, e: e.update(samples=3),
    lambda r, e: e.update(max_generated_tokens=99999),
    lambda r, e: e["limits"].update(max_report_bytes=1024),
    lambda r, e: e["limits"].update(timeout_seconds=5.0),
    lambda r, e: e["image_identity"].update(platform="linux/arm64"),
    lambda r, e: e["image_identity"].update(repo_digests=["wrong@sha256:" + "0" * 64]),
    lambda r, e: r["inference"].update(checkpoint_sha256="0" * 64),
    lambda r, e: r["inference"].update(runtime_digest="0" * 64),
    lambda r, e: r["inference"].update(samples=[]),
    lambda r, e: r["inference"].update(samples=r["inference"]["samples"] * 2),
])
def test_tampered_or_incomplete_evidence_never_reaches_scoring(tmp_path: Path, mutation):
    case = _case(tmp_path)
    _mutate(case, mutation)
    with pytest.raises(ValueError):
        _evaluate(case)
    assert not case.scorer.scored


def test_rehashed_foreign_checkpoint_is_still_rejected(tmp_path: Path):
    case = _case(tmp_path)
    _mutate(case, lambda r, e: r["inference"].update(checkpoint_sha256="0" * 64))
    with pytest.raises(EvidenceError, match="checkpoint"):
        _evaluate(case)


def test_changed_report_bytes_must_match_completion_hash(tmp_path: Path):
    case = _case(tmp_path)
    _mutate(case, lambda r, e: r.update(extra="unrecorded"), recompute=False)
    with pytest.raises(EvidenceError, match="report digest"):
        _evaluate(case)


@pytest.mark.parametrize("field", ["hardware", "runtime_environment"])
def test_cross_shard_runtime_and_hardware_consistency(tmp_path: Path, field: str):
    case = _case(tmp_path)

    def mutate(report, receipt):
        report["inference"][field]["different"] = "changed"
        if field == "runtime_environment":
            report["observed_runtime"] = report["inference"][field]
    _mutate(case, mutate)
    with pytest.raises(EvidenceError, match="changed"):
        _evaluate(case)


def test_harness_consistency_and_matching_prompts(tmp_path: Path):
    case = _case(tmp_path)
    _mutate(case, lambda r, e: e.update(harness_digest="f" * 64))
    with pytest.raises(EvidenceError, match="harness"):
        _evaluate(case)


def test_rehashed_altered_prompt_tokens_fail_pairing(tmp_path: Path):
    case = _case(tmp_path)

    def mutate(report, receipt):
        for sample in report["inference"]["samples"]:
            sample["prompt_token_ids"] = [99, 20]
    _mutate(case, mutate)
    with pytest.raises(EvidenceError, match="prompt/tokenization"):
        _evaluate(case)


@pytest.mark.parametrize("coverage", ["missing", "extra", "unexpected_file", "missing_receipt", "symlink"])
def test_saved_evidence_must_have_exact_coverage(tmp_path: Path, coverage: str):
    case = _case(tmp_path)
    key = case.plan.shards[0].shard_id
    if coverage == "missing":
        case.runs.pop(key)
    elif coverage == "extra":
        case.runs["extra"] = case.runs[key]
    elif coverage == "unexpected_file":
        (case.runs[key] / "other.json").write_text("{}")
    elif coverage == "missing_receipt":
        (case.runs[key] / "execution.receipt.json").unlink()
    else:
        path = case.runs[key] / "report.json"
        saved = tmp_path / "report-copy.json"
        path.rename(saved)
        path.symlink_to(saved)
    with pytest.raises((ValueError, OSError)):
        _evaluate(case)


def test_dataset_scoring_payload_must_still_match_normalized_digest(tmp_path: Path):
    case = _case(tmp_path)
    changed = case.dataset.problems[0].model_copy(update={"answer": "different"})
    case.dataset = VerifiedDataset("math500", case.dataset.digest, (changed, case.dataset.problems[1]), "b" * 64, "c" * 64)
    with pytest.raises(EvidenceError, match="snapshot"):
        _evaluate(case)


def test_scorer_and_published_reference_must_match_plan(tmp_path: Path):
    case = _case(tmp_path)
    case.scorer.lock_digest = "f" * 64
    with pytest.raises(EvidenceError, match="scorer"):
        _evaluate(case)
    case.scorer.lock_digest = "a" * 64
    with pytest.raises(EvidenceError, match="published"):
        evaluate_reproduction(case.plan, {"math500": case.dataset}, case.scorer, case.runs, allow_fixture=True)


def test_scorer_failure_is_not_a_zero_observation(tmp_path: Path):
    case = _case(tmp_path)

    def fail(*args):
        raise RuntimeError("unavailable scorer resource")
    case.scorer.validate_cohort = fail
    with pytest.raises(RuntimeError, match="unavailable"):
        _evaluate(case)
    assert not case.scorer.scored


def test_json_markdown_are_replayable_and_do_not_embed_raw_answers(tmp_path: Path):
    case = _case(tmp_path)
    first = _evaluate(case)
    second = _evaluate(case)
    assert first.digest == second.digest
    assert render_reproduction_report(first) == render_reproduction_report(second)
    output = tmp_path / "report"
    published = write_reproduction_report(first, output)
    assert published["report_digest"] == first.digest
    saved = json.loads((output / "report.json").read_bytes())
    assert saved == first.to_dict()
    assert "answer" not in saved["samples"][0]
    assert "text" not in saved["samples"][0]
    assert "fixture" in (output / "report.md").read_text()
    assert (output / "report.md").read_text().startswith("# Qwen base-versus-patch fixture report\n")
    assert "not pass@k" in (output / "report.md").read_text()
    assert all(note in saved["fidelity_notes"] for note in case.plan.fidelity_notes)
    assert "not proven identical" in (output / "report.md").read_text()
    assert "original IFEval adapter was not released" in (output / "report.md").read_text()
    with pytest.raises(ValueError, match="new directory"):
        write_reproduction_report(first, output)


def test_duplicate_json_keys_are_rejected(tmp_path: Path):
    case = _case(tmp_path)
    path = case.runs[case.plan.shards[0].shard_id] / "execution.receipt.json"
    path.write_text('{"ok": true, "ok": true}')
    with pytest.raises(ValueError, match="duplicate"):
        _evaluate(case)


def test_nonfinite_metadata_is_rejected(tmp_path: Path):
    case = _case(tmp_path)
    _mutate(case, lambda r, e: e.update(elapsed_seconds=float("inf")))
    with pytest.raises(ValueError):
        _evaluate(case)


def test_shard_validator_rejects_missing_live_runtime_metadata(tmp_path: Path):
    case = _case(tmp_path, kind="vllm")
    _mutate(case, lambda r, e: (r.update(observed_runtime={}), r["inference"].update(runtime_environment={})))
    shard = case.plan.shards[0]
    with pytest.raises(EvidenceError, match="runtime versions"):
        read_shard_evidence(case.plan, shard, case.runs[shard.shard_id])


def test_validation_does_not_retain_all_raw_generation_arrays(tmp_path: Path, monkeypatch):
    case = _case(tmp_path)
    original = evaluation_module.read_shard_evidence
    references = []
    live_counts = []

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        references.append(weakref.ref(result.result))
        live_counts.append(sum(reference() is not None for reference in references))
        return result
    monkeypatch.setattr(evaluation_module, "read_shard_evidence", observed)
    _evaluate(case)
    assert len(references) == 2 * len(case.plan.shards)
    assert max(live_counts) <= 2


def test_evidence_changed_after_preflight_is_not_scored_as_the_original(tmp_path: Path):
    case = _case(tmp_path)
    original = case.scorer.validate_cohort

    def mutate_after_validation(dataset, problems):
        original(dataset, problems)
        _mutate(case, lambda report, receipt: report.update(new="changed after validation"))
    case.scorer.validate_cohort = mutate_after_validation
    with pytest.raises(EvidenceError, match="changed between validation and scoring"):
        _evaluate(case)
    assert not case.scorer.scored


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("AUTOCONTEXT_SCORER_ASSET_ROOT"),
                    reason="requires explicit pinned scorer environment/assets")
@pytest.mark.parametrize("dataset_name", ["math500", "ifeval"])
def test_official_scorer_replays_synthetic_raw_evidence(tmp_path: Path, dataset_name: str):
    """Opt-in CPU integration; model outputs remain explicitly synthetic."""
    from autocontext.weight_evolution.scorer_runtime import load_pinned_scorer, load_scorer_lock

    case = _case(tmp_path)
    case.published = load_published_results(EXAMPLE / "published-results.lock.json")
    scorer = load_pinned_scorer(load_scorer_lock(EXAMPLE / "scorer.lock.json"), Path(os.environ["AUTOCONTEXT_SCORER_ASSET_ROOT"]))
    if dataset_name == "ifeval":
        problems = tuple(EvaluationProblem(
            problem_id=f"p{i}", text=f"For example {i}, include the word sunshine.",
            instructions=(DatasetInstruction(instruction_id="keywords:existence", kwargs={"keywords": ["sunshine"]}),),
        ) for i in range(2))
        case.dataset = VerifiedDataset("ifeval", cohort_digest(problems), problems, "b" * 64, "c" * 64)
    new_shards = []
    new_runs = {}
    for shard in case.plan.shards:
        problem = case.dataset.problems[shard.ordinal]
        request = shard.request.model_copy(update={
            "dataset": dataset_name, "dataset_sha256": case.dataset.digest,
            "problems": (InferenceProblem(problem_id=problem.problem_id, text=problem.text),),
        })
        new_shard = ReplayShard(
            shard_id=f"{dataset_name}-{shard.ordinal:05d}-{request.variant}", ordinal=shard.ordinal,
            request=request, estimated_report_bytes=estimate_report_bytes(request),
        )
        directory = case.runs[shard.shard_id]
        report = json.loads((directory / "report.json").read_bytes())
        receipt = json.loads((directory / "execution.receipt.json").read_bytes())
        result = report["inference"]
        n = 4 if dataset_name == "math500" else 2
        result.update(request=request.to_dict(), request_digest=request.digest, samples_per_problem=n)
        result["sampling"]["n"] = n
        result["samples"] = result["samples"][:n]
        for sample in result["samples"]:
            original = sample["text"]
            sample["rendered_prompt"] = f"<user>{problem.text}</user><think>"
            sample["prompt_sha256"] = text_digest(sample["rendered_prompt"])
            sample["text"] = ("Thought.</think>\\boxed{" + ("999" if original == "wrong" else original) + "}"
                              if dataset_name == "math500" else "Thought.</think>" +
                              ("cloud" if original == "wrong" else "sunshine"))
            sample["response_sha256"] = text_digest(sample["text"])
        report["request_digest"] = request.digest
        receipt.update(request_digest=request.digest, samples=n, max_generated_tokens=n * 8)
        (directory / "report.json").write_text(json.dumps(report))
        receipt["report_sha256"] = hashlib.sha256((directory / "report.json").read_bytes()).hexdigest()
        (directory / "execution.receipt.json").write_text(json.dumps(receipt))
        new_shards.append(new_shard)
        new_runs[new_shard.shard_id] = directory
    plan = case.plan.model_copy(update={
        "scorer_lock_digest": scorer.lock_digest, "shards": tuple(new_shards),
        "published_results_digest": case.published.digest,
        "cohorts": (CohortIdentity(dataset=dataset_name, dataset_sha256=case.dataset.digest,
                                    problem_ids=("p0", "p1"), complete_dataset=True),),
    })
    results = [evaluate_reproduction(plan, {dataset_name: case.dataset}, scorer, new_runs,
                                    published=case.published, allow_fixture=True) for _ in range(2)]
    assert results[0].digest == results[1].digest
    payload = results[0].to_dict()
    assert payload["evidence_kind"] == "fixture"
    assert payload["scorer"]["execution_kind"] == "official"
    metrics = payload["comparisons"][0]
    assert metrics["base"]["mean_sample_accuracy"] == (0.125 if dataset_name == "math500" else 0.25)
    assert metrics["published_patch"]["mean_sample_accuracy"] == (0.75 if dataset_name == "math500" else 1.0)
    write_reproduction_report(results[0], tmp_path / f"{dataset_name}-official-scorer-fixture-report")
