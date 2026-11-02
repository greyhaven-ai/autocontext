"""Validate saved shard evidence and score a frozen base-versus-patch replay.

Hashes bind saved evidence to the frozen plan; they are integrity checks, not
remote attestation. Model/scorer failures never become zero-score observations.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from autocontext.analytics.paired_statistics import paired_confidence_interval
from autocontext.weight_evolution.artifacts import canonical_digest, regular_file
from autocontext.weight_evolution.datasets import cohort_digest
from autocontext.weight_evolution.inference_models import GenerationSample, InferenceRequest, InferenceResult
from autocontext.weight_evolution.models import _unique_object
from autocontext.weight_evolution.reference import PublishedMetric, PublishedResults
from autocontext.weight_evolution.runtime import REQUIRED_PACKAGES
from autocontext.weight_evolution.scorers import ScoredSample, grouped_sample_accuracy

if TYPE_CHECKING:
    from autocontext.weight_evolution.cohort import ReplayPlan, ReplayShard
    from autocontext.weight_evolution.datasets import EvaluationProblem, VerifiedDataset

MAX_EVIDENCE_BYTES = 256 * 1024 * 1024
_VARIANTS = ("base", "published_patch")


class EvidenceError(ValueError):
    """Saved evidence is incomplete or differs from the predeclared replay."""


class ReplayScorer(Protocol):
    @property
    def lock_digest(self) -> str: ...

    @property
    def execution_kind(self) -> str: ...

    def provenance(self) -> dict[str, Any]: ...

    def validate_cohort(self, dataset: str, problems: Sequence[EvaluationProblem]) -> None: ...

    def score_sample(self, dataset: str, problem: EvaluationProblem, sample: GenerationSample) -> int: ...


@dataclass(frozen=True)
class VerifiedShard:
    shard_id: str
    result: InferenceResult
    report_sha256: str
    execution_receipt_sha256: str
    harness_digest: str
    elapsed_seconds: float


@dataclass(frozen=True)
class _ShardSummary:
    """Keep identities and metrics, never an entire cohort's generated tokens."""

    shard_id: str
    request: InferenceRequest
    report_sha256: str
    execution_receipt_sha256: str
    result_digest: str
    elapsed_seconds: float


@dataclass(frozen=True)
class ReproductionEvaluation:
    payload: dict[str, Any]

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload)

    def to_dict(self) -> dict[str, Any]:
        # Return data independent of the original dict and normalize tuples.
        return json.loads(json.dumps(self.payload, allow_nan=False))  # type: ignore[no-any-return]


def _read_object(path: Path, limit: int) -> tuple[dict[str, Any], str, int]:
    with regular_file(path) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise EvidenceError(f"evidence exceeds byte budget: {path.name}")
    value = json.loads(data, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise EvidenceError(f"evidence must be an object: {path.name}")
    # Forbid nonfinite numbers even in unchecked metadata fields.
    json.dumps(value, allow_nan=False)
    return value, hashlib.sha256(data).hexdigest(), len(data)


def _finite_number(value: object, label: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):  # type: ignore[arg-type]
        raise EvidenceError(f"invalid {label}")
    result = float(value)  # type: ignore[arg-type]
    if result < 0 or (positive and result <= 0):
        raise EvidenceError(f"invalid {label}")
    return result


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise EvidenceError(f"invalid {label}")
    return value


def read_shard_evidence(
    plan: ReplayPlan,
    shard: ReplayShard,
    directory: Path,
    *,
    allow_fixture: bool = False,
) -> VerifiedShard:
    """Read one completed M2 worker directory and check every frozen identity."""
    if directory.is_symlink() or not directory.is_dir():
        raise EvidenceError("shard evidence must be a real directory")
    if {path.name for path in directory.iterdir()} != {"report.json", "execution.receipt.json"}:
        raise EvidenceError("shard directory is incomplete or contains unexpected files")
    report, report_sha, size = _read_object(directory / "report.json", min(plan.limits.max_report_bytes, MAX_EVIDENCE_BYTES))
    receipt, receipt_sha, _ = _read_object(directory / "execution.receipt.json", 1024 * 1024)
    result = InferenceResult.model_validate_json(json.dumps(report.get("inference"), allow_nan=False))
    expected = shard.request
    for document in (report, receipt):
        if (
            document.get("ok") is not True or document.get("operation") != "infer"
            or document.get("lock_digest") != plan.lock_digest
            or document.get("runtime_digest") != plan.runtime_digest
            or document.get("request_digest") != expected.digest
        ):
            raise EvidenceError("worker envelope differs from frozen replay inputs")
    if (
        result.request != expected or result.request_digest != expected.digest
        or result.lock_digest != plan.lock_digest or result.runtime_digest != plan.runtime_digest
        or result.runtime_image != plan.runtime_image or receipt.get("image") != plan.runtime_image
        or receipt.get("variant") != expected.variant or receipt.get("mode") != expected.mode
        or receipt.get("report_sha256") != report_sha
    ):
        raise EvidenceError("worker evidence differs from its request, runtime or report digest")
    if result.execution_kind != "vllm" and not allow_fixture:
        raise EvidenceError("fixture model evidence cannot enter a reproduction report")
    if receipt.get("model_execution_performed") is not (result.execution_kind == "vllm"):
        raise EvidenceError("model-execution receipt disagrees with evidence kind")
    if receipt.get("network") != "none" or receipt.get("scope") != "development_replay":
        raise EvidenceError("unexpected worker execution scope")
    samples = len(expected.problems) * result.samples_per_problem
    if (receipt.get("problems"), receipt.get("samples"), receipt.get("max_generated_tokens")) != (
        len(expected.problems), samples, samples * result.max_new_tokens,
    ):
        raise EvidenceError("worker receipt sample/token budget differs from request")
    checkpoints = {pin.variant: pin for pin in plan.checkpoints}
    pin = checkpoints[expected.variant]
    if (
        result.checkpoint_receipt_digest != pin.receipt_digest
        or result.checkpoint_sha256 != pin.checkpoint_sha256
        or result.tensor_content_digest != pin.tensor_content_digest
    ):
        raise EvidenceError("checkpoint evidence differs from the frozen variant")
    limits = receipt.get("limits")
    if not isinstance(limits, dict) or type(limits.get("max_report_bytes")) is not int:
        raise EvidenceError("worker receipt lacks bounded report limits")
    if not 0 < limits["max_report_bytes"] <= MAX_EVIDENCE_BYTES or size > limits["max_report_bytes"]:
        raise EvidenceError("report exceeded its recorded byte budget")
    _finite_number(limits.get("timeout_seconds"), "worker timeout", positive=True)
    if any(limits.get(name) != expected_value for name, expected_value in (
        ("max_report_bytes", plan.limits.max_report_bytes), ("timeout_seconds", plan.limits.shard_timeout_seconds),
        ("memory_mb", plan.limits.memory_mb), ("cpu_count", plan.limits.cpu_count),
    )):
        raise EvidenceError("worker resource limits differ from frozen replay")
    image = receipt.get("image_identity")
    if not isinstance(image, dict) or image.get("platform") != "linux/amd64":
        raise EvidenceError("missing or invalid immutable image evidence")
    repo_digests = image.get("repo_digests")
    if not isinstance(repo_digests, list) or not any(
        isinstance(item, str) and item.rsplit("@", 1)[-1] == plan.runtime_image.rsplit("@", 1)[-1]
        for item in repo_digests
    ):
        raise EvidenceError("Docker image identity differs from pinned image")
    image_id = image.get("id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise EvidenceError("invalid installed Docker image identity")
    _digest(image_id[7:], "installed image digest")
    if report.get("observed_runtime") != result.runtime_environment:
        raise EvidenceError("observed runtime differs between envelope and raw evidence")
    if result.execution_kind == "vllm":
        environment = result.runtime_environment
        versions = environment.get("packages")
        if (environment.get("platform") != "linux/amd64" or not isinstance(environment.get("python_version"), str)
                or not isinstance(versions, dict)
                or any(not isinstance(versions.get(name), str) or not versions[name] for name in REQUIRED_PACKAGES)):
            raise EvidenceError("live inference lacks complete observed runtime versions")
        if result.hardware.get("accelerator") != "cuda" or not result.hardware.get("device_name"):
            raise EvidenceError("live inference lacks CUDA hardware observations")
    harness = _digest(receipt.get("harness_digest"), "harness digest")
    elapsed = _finite_number(receipt.get("elapsed_seconds"), "worker elapsed time")
    return VerifiedShard(shard.shard_id, result, report_sha, receipt_sha, harness, elapsed)


def validate_run_evidence(
    plan: ReplayPlan, shard: ReplayShard, directory: Path, *, allow_fixture: bool = False,
) -> InferenceResult:
    """Validate a completed shard for orchestration/resume and return its result."""
    return read_shard_evidence(plan, shard, directory, allow_fixture=allow_fixture).result


def _percentile95(values: Sequence[int]) -> int:
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)]


def _metrics(samples: Sequence[dict[str, Any]], samples_per_problem: int, elapsed: float) -> dict[str, Any]:
    grouped = grouped_sample_accuracy(
        [ScoredSample(sample["problem_id"], str(sample["sample_index"]), sample["score"]) for sample in samples],
        samples_per_problem=samples_per_problem,
    )
    tokens = [sample["output_tokens"] for sample in samples]
    return {
        "mean_sample_accuracy": grouped.mean_accuracy,
        "n_problems": grouped.n_problems, "n_samples": grouped.n_samples,
        "samples_per_problem": samples_per_problem,
        "problem_scores": dict(grouped.problem_scores),
        "token_limit_rate": statistics.fmean(sample["finish_reason"] == "length" for sample in samples),
        "mean_generated_tokens": statistics.fmean(tokens), "p95_generated_tokens": _percentile95(tokens),
        "total_generated_tokens": sum(tokens), "total_input_tokens": sum(sample["input_tokens"] for sample in samples),
        "worker_elapsed_seconds": elapsed,
    }


def evaluate_reproduction(
    plan: ReplayPlan,
    datasets: Mapping[str, VerifiedDataset],
    scorer: ReplayScorer,
    runs: Mapping[str, Path],
    *,
    allow_fixture: bool = False,
    published: PublishedResults | None = None,
) -> ReproductionEvaluation:
    """Replay scoring only after exact dataset, shard and runtime coverage checks.

    The plan is the caller's frozen trust anchor. It must originate from verified
    checkpoint/data assets. Saved hashes cannot attest an untrusted remote host.
    """
    from autocontext.weight_evolution.cohort import validate_plan_datasets

    try:
        validate_plan_datasets(plan, tuple(datasets.values()))
    except ValueError as exc:
        raise EvidenceError(f"dataset snapshot differs from frozen replay: {exc}") from exc
    if scorer.lock_digest != plan.scorer_lock_digest:
        raise EvidenceError("scorer lock differs from frozen replay")
    if scorer.execution_kind not in {"official", "fixture"}:
        raise EvidenceError("unknown scorer execution kind")
    if scorer.execution_kind != "official" and not allow_fixture:
        raise EvidenceError("fixture scorer cannot enter a reproduction report")
    if published is None or published.digest != plan.published_results_digest:
        raise EvidenceError("published reference differs from frozen replay")
    cohort_names = {cohort.dataset for cohort in plan.cohorts}
    if set(datasets) != cohort_names:
        raise EvidenceError("dataset inputs do not exactly cover the replay cohorts")
    expected_ids = {shard.shard_id for shard in plan.shards}
    if len(expected_ids) != len(plan.shards) or set(runs) != expected_ids:
        raise EvidenceError("missing, duplicate or unexpected shard evidence")
    selected: dict[str, tuple[EvaluationProblem, ...]] = {}
    full_cohorts = True
    for cohort in plan.cohorts:
        dataset = datasets[cohort.dataset]
        if (
            dataset.dataset != cohort.dataset or dataset.digest != cohort.dataset_sha256
            or dataset.lock_digest != plan.dataset_lock_digest
            or cohort_digest(dataset.problems) != dataset.digest
        ):
            raise EvidenceError("dataset snapshot differs from frozen replay")
        all_problems = {problem.problem_id: problem for problem in dataset.problems}
        if len(all_problems) != len(dataset.problems) or len(set(cohort.problem_ids)) != len(cohort.problem_ids):
            raise EvidenceError("duplicate problem in dataset or planned cohort")
        if any(identity not in all_problems for identity in cohort.problem_ids):
            raise EvidenceError("planned problem is absent from verified dataset")
        selected[cohort.dataset] = tuple(all_problems[identity] for identity in cohort.problem_ids)
        full_cohorts &= tuple(cohort.problem_ids) == tuple(problem.problem_id for problem in dataset.problems)

    # Pass one validates every shard before any scoring. Only compact hashes,
    # identities and request metadata survive the iteration, bounding raw token
    # memory by one shard rather than all generations of a full benchmark.
    evidence: list[_ShardSummary] = []
    common: dict[str, Any] | None = None
    prompt_identities: dict[tuple[str, str], tuple[str, str]] = {}
    seen_reports: set[str] = set()
    for planned_shard in plan.shards:
        verified = read_shard_evidence(plan, planned_shard, runs[planned_shard.shard_id], allow_fixture=allow_fixture)
        observations = {"harness_digest": verified.harness_digest,
                        "runtime_environment": verified.result.runtime_environment,
                        "hardware": verified.result.hardware, "execution_kind": verified.result.execution_kind}
        if common is not None and canonical_digest(common) != canonical_digest(observations):
            raise EvidenceError("inference harness, observed runtime, hardware or execution kind changed between shards")
        common = observations
        if verified.report_sha256 in seen_reports:
            raise EvidenceError("a report was reused as multiple shards")
        seen_reports.add(verified.report_sha256)
        for sample in verified.result.samples:
            key = (verified.result.request.dataset, sample.problem_id)
            identity = (sample.prompt_sha256, canonical_digest(sample.prompt_token_ids))
            if key in prompt_identities and prompt_identities[key] != identity:
                raise EvidenceError("rendered prompt/tokenization changed between samples or variants")
            prompt_identities[key] = identity
        evidence.append(_ShardSummary(verified.shard_id, planned_shard.request, verified.report_sha256,
                                      verified.execution_receipt_sha256, verified.result.digest, verified.elapsed_seconds))
        del verified
    assert common is not None
    by_cohort: dict[tuple[str, str], list[_ShardSummary]] = {}
    for summary in evidence:
        request = summary.request
        by_cohort.setdefault((request.dataset, request.variant), []).append(summary)
    ordinals = {shard.shard_id: shard.ordinal for shard in plan.shards}
    for items in by_cohort.values():
        items.sort(key=lambda item: ordinals[item.shard_id])
    if set(by_cohort) != {(name, variant) for name in cohort_names for variant in _VARIANTS}:
        raise EvidenceError("evidence does not cover both variants for every cohort")
    for name, problems in selected.items():
        expected_problem_ids = tuple(problem.problem_id for problem in problems)
        for variant in _VARIANTS:
            shards = by_cohort[(name, variant)]
            actual_problems = tuple(problem for shard in shards for problem in shard.request.problems)
            if tuple(problem.problem_id for problem in actual_problems) != expected_problem_ids:
                raise EvidenceError("shard membership/order differs from frozen cohort")
            if any(actual.text != expected.text for actual, expected in zip(actual_problems, problems, strict=True)):
                raise EvidenceError("shard prompt text differs from the verified dataset")
            if any(shard.request.dataset_sha256 != datasets[name].digest for shard in shards):
                raise EvidenceError("inference request dataset hash differs from verified snapshot")
        scorer.validate_cohort(name, problems)

    # All provenance/coverage and every gold/instruction are validated before a
    # single response is graded, including responses with no extractable answer.
    rows: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    frozen_shards = {shard.shard_id: shard for shard in plan.shards}
    for name in sorted(cohort_names):
        problem_lookup = {problem.problem_id: problem for problem in selected[name]}
        arm_metrics: dict[str, dict[str, Any]] = {}
        for variant in _VARIANTS:
            arm_rows = []
            shards = by_cohort[(name, variant)]
            for shard in shards:
                current = read_shard_evidence(
                    plan, frozen_shards[shard.shard_id], runs[shard.shard_id], allow_fixture=allow_fixture,
                )
                if (current.report_sha256, current.execution_receipt_sha256) != (
                    shard.report_sha256, shard.execution_receipt_sha256,
                ):
                    raise EvidenceError("saved shard changed between validation and scoring")
                for sample in current.result.samples:
                    score = scorer.score_sample(name, problem_lookup[sample.problem_id], sample)
                    if type(score) is not int or score not in (0, 1):
                        raise EvidenceError("scorer returned a nonbinary score")
                    row = {
                        "dataset": name, "variant": variant, "problem_id": sample.problem_id,
                        "sample_index": sample.sample_index, "seed": sample.seed, "score": score,
                        "input_tokens": sample.input_tokens, "output_tokens": sample.output_tokens,
                        "finish_reason": sample.finish_reason, "response_sha256": sample.response_sha256,
                        "prompt_sha256": sample.prompt_sha256, "shard_id": shard.shard_id,
                        "raw_report_sha256": shard.report_sha256,
                    }
                    arm_rows.append(row)
                    rows.append(row)
            arm_metrics[variant] = _metrics(
                arm_rows, 4 if name == "math500" else 2, sum(shard.elapsed_seconds for shard in shards),
            )
        base, patch = arm_metrics["base"], arm_metrics["published_patch"]
        effects = [patch["problem_scores"][identity] - base["problem_scores"][identity] for identity in problem_lookup]
        mean, lower, upper = paired_confidence_interval(effects, 1.959963984540054, max_looks=1)
        comparisons.append({
            "dataset": name, "dataset_sha256": datasets[name].digest,
            "base": base, "published_patch": patch,
            "paired_effect": {
                "mean": mean, "lower": lower, "upper": upper, "confidence_level": 0.95,
                "method": "paired_student_t_by_problem", "max_looks": 1,
                "scope": "descriptive_per_benchmark_not_familywise_or_promotion",
                "zero_empirical_variance": len(set(effects)) == 1,
                "warning": "Small or zero-variance cohorts cannot establish noninferiority or promotion.",
            },
        })
    fixture = scorer.execution_kind == "fixture" or common["execution_kind"] == "fixture"
    kind = ("fixture" if fixture else "smoke" if plan.mode == "smoke"
            else "full_cohort_replay" if full_cohorts else "subset_replay")
    payload: dict[str, Any] = {
        "schema_version": 1, "status": "complete", "evidence_kind": kind,
        "plan_digest": plan.digest, "lock_digest": plan.lock_digest, "runtime_digest": plan.runtime_digest,
        "runtime_image": plan.runtime_image, "dataset_lock_digest": plan.dataset_lock_digest,
        "scorer_lock_digest": plan.scorer_lock_digest, "scorer": scorer.provenance(),
        "runtime_environment": common["runtime_environment"],
        "hardware": common["hardware"], "harness_digest": common["harness_digest"],
        "checkpoints": [pin.to_dict() for pin in plan.checkpoints],
        "comparisons": comparisons, "samples": rows,
        "shards": [{"shard_id": shard.shard_id, "report_sha256": shard.report_sha256,
                    "execution_receipt_sha256": shard.execution_receipt_sha256,
                    "result_digest": shard.result_digest} for shard in evidence],
        "promotion_decision": None, "behavioral_reproduction_claim": False,
        "fidelity_notes": list(dict.fromkeys([*plan.fidelity_notes,
            "This is a frozen checkpoint comparison, not evidence of autonomous edit discovery or deployment readiness.",
            "The chosen runtime/hardware may differ from the unreleased original environment.",
            "The pinned official dataset snapshots are not proven identical to "
            "the original experiment's private benchmark files.",
            "The original IFEval adapter was not released; this report uses the separately pinned official scorer contract.",
            "Bounded contiguous shards differ from upstream strided batching; sampled outputs need not be bit-identical.",
            "Saved hashes verify consistency with the frozen plan; they do not cryptographically attest the execution host.",
            "Paired intervals use per-problem mean effects, are descriptive per benchmark, "
            "and make no familywise or promotion claim.",
        ])),
    }
    reference_metrics: dict[tuple[str, str], PublishedMetric] = {
        (metric.dataset, metric.variant): metric for metric in published.metrics
    }
    for comparison in comparisons:
        source_comparable = kind == "full_cohort_replay"
        comparison["published_comparison"] = {"same_full_cohort_size": all(
            comparison[variant]["n_problems"] == reference_metrics[(comparison["dataset"], variant)].n_problems
            for variant in _VARIANTS
        ), "scope": "full_replay_descriptive_discrepancy" if source_comparable else "context_only_not_comparable"}
        for variant in _VARIANTS:
            metric = reference_metrics[(comparison["dataset"], variant)]
            measured = comparison[variant]
            comparable = source_comparable and measured["n_problems"] == metric.n_problems
            comparison["published_comparison"][variant] = {
                "accuracy": metric.accuracy, "token_limit_rate": metric.token_limit_rate,
                "mean_generated_tokens": metric.mean_tokens, "n_problems": metric.n_problems,
                "accuracy_difference": measured["mean_sample_accuracy"] - metric.accuracy if comparable else None,
                "token_limit_rate_difference": measured["token_limit_rate"] - metric.token_limit_rate if comparable else None,
                "mean_generated_tokens_difference": (
                    measured["mean_generated_tokens"] - metric.mean_tokens if comparable else None
                ),
            }
    payload["published_results_digest"] = published.digest
    payload["published_reference"] = published.to_dict()
    return ReproductionEvaluation(payload)
