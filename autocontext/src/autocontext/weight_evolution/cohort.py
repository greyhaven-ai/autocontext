"""Frozen paired replay plans with explicit cohort and resource boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.weight_evolution.artifacts import CheckpointReceipt, load_small_json
from autocontext.weight_evolution.datasets import VerifiedDataset, cohort_digest
from autocontext.weight_evolution.inference_models import InferenceProblem, InferenceRequest, content_digest
from autocontext.weight_evolution.models import Digest, FrozenModel, ReproductionLock
from autocontext.weight_evolution.runtime import RuntimeLock

DatasetName = Literal["math500", "gsm8k", "ifeval"]
Variant = Literal["base", "published_patch"]


class ReplayLimits(FrozenModel):
    max_shard_problems: int = Field(default=8, ge=1, le=128)
    max_shard_generated_tokens: int = Field(default=1048576, ge=1, le=16777216)
    max_report_bytes: int = Field(default=64 * 1024 * 1024, ge=65536, le=256 * 1024 * 1024)
    shard_timeout_seconds: float = Field(default=1800.0, gt=0, le=86400, allow_inf_nan=False)
    max_total_generated_tokens: int = Field(gt=0, le=1000000000)
    max_total_wall_seconds: float = Field(gt=0, le=31536000, allow_inf_nan=False)
    memory_mb: int = Field(default=16384, ge=64, le=1048576)
    cpu_count: float = Field(default=4.0, gt=0, le=256, allow_inf_nan=False)


class CheckpointIdentity(FrozenModel):
    variant: Variant
    receipt_digest: Digest
    checkpoint_sha256: Digest
    tensor_content_digest: Digest


class CohortIdentity(FrozenModel):
    dataset: DatasetName
    dataset_sha256: Digest
    problem_ids: tuple[str, ...] = Field(min_length=1)
    complete_dataset: bool


def estimate_report_bytes(request: InferenceRequest) -> int:
    """Planning estimate, not an upper bound on arbitrary decoded Unicode.

    Reserve 32 bytes/output token plus generous prompt and framing overhead.
    The worker separately enforces the hard serialized byte cap; an unusually
    large response fails that shard rather than publishing incomplete evidence.
    """
    samples = 4 if request.dataset == "math500" else 2
    tokens = request.smoke_max_new_tokens or 32768
    return 65536 + sum(
        4096 + len(problem.text.encode("utf-8")) * 6
        + samples * (tokens * 32 + len(problem.text.encode("utf-8")) * 12 + 65536)
        for problem in request.problems
    )


class ReplayShard(FrozenModel):
    shard_id: str = Field(pattern=r"^(math500|gsm8k|ifeval)-[0-9]{5}-(base|published_patch)$")
    ordinal: int = Field(ge=0, le=99999)
    request: InferenceRequest
    estimated_report_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.shard_id != f"{self.request.dataset}-{self.ordinal:05d}-{self.request.variant}":
            raise ValueError("shard identity differs from its request")
        if self.estimated_report_bytes != estimate_report_bytes(self.request):
            raise ValueError("report estimate differs from the fixed planning policy")
        return self


class ReplayPlan(FrozenModel):
    schema_version: Literal[1] = 1
    lock_digest: Digest
    runtime_digest: Digest
    runtime_image: str
    dataset_lock_digest: Digest
    scorer_lock_digest: Digest
    published_results_digest: Digest
    mode: Literal["reproduction", "smoke"]
    cohorts: tuple[CohortIdentity, ...] = Field(min_length=1, max_length=3)
    checkpoints: tuple[CheckpointIdentity, ...] = Field(min_length=2, max_length=2)
    shards: tuple[ReplayShard, ...] = Field(min_length=2, max_length=20000)
    limits: ReplayLimits
    report_estimator: Literal["32-bytes-per-token-plus-prompt-v1"] = "32-bytes-per-token-plus-prompt-v1"
    fidelity_notes: tuple[str, ...] = (
        "Bounded contiguous cohorts replace the source's strided batch layout; batch/order changes may change sampled outputs.",
        "Report sizes are estimates; worker byte caps fail closed and do not truncate or omit samples.",
        "The chosen inference/scoring environment is pinned separately from the unreleased original environment.",
        "This is reproduction evidence, not a deployment promotion or fresh editing-agent discovery.",
    )

    @model_validator(mode="after")
    def consistent_plan(self) -> Self:
        if {item.variant for item in self.checkpoints} != {"base", "published_patch"}:
            raise ValueError("replay requires both base and published-patch checkpoints")
        cohorts = {item.dataset: item for item in self.cohorts}
        if len(cohorts) != len(self.cohorts):
            raise ValueError("duplicate cohort")
        if len({item.shard_id for item in self.shards}) != len(self.shards):
            raise ValueError("duplicate shard identity")
        pairs: dict[tuple[str, int], dict[str, InferenceRequest]] = {}
        for shard in self.shards:
            request = shard.request
            cohort = cohorts.get(request.dataset)
            if cohort is None or request.dataset_sha256 != cohort.dataset_sha256 or request.mode != self.mode:
                raise ValueError("shard differs from its frozen cohort/mode")
            if len(request.problems) > self.limits.max_shard_problems:
                raise ValueError("shard problem limit exceeded")
            n = 4 if request.dataset == "math500" else 2
            if len(request.problems) * n * (request.smoke_max_new_tokens or 32768) > self.limits.max_shard_generated_tokens:
                raise ValueError("shard token limit exceeded")
            if shard.estimated_report_bytes > self.limits.max_report_bytes:
                raise ValueError("shard report estimate exceeds byte limit")
            pairs.setdefault((request.dataset, shard.ordinal), {})[request.variant] = request
        for requests in pairs.values():
            if set(requests) != {"base", "published_patch"}:
                raise ValueError("each cohort shard requires both variants")
            base, patch = requests["base"], requests["published_patch"]
            if base.problems != patch.problems or base.smoke_max_new_tokens != patch.smoke_max_new_tokens:
                raise ValueError("paired shard inputs or sampling budget differ")
        for cohort in self.cohorts:
            if len(set(cohort.problem_ids)) != len(cohort.problem_ids):
                raise ValueError("duplicate cohort problem identity")
            if self.mode == "reproduction" and not cohort.complete_dataset:
                raise ValueError("reproduction requires complete locked datasets")
            for variant in ("base", "published_patch"):
                shards = sorted((s for s in self.shards if s.request.dataset == cohort.dataset
                                 and s.request.variant == variant), key=lambda s: s.ordinal)
                if [s.ordinal for s in shards] != list(range(len(shards))):
                    raise ValueError("shard ordinals must be contiguous")
                ids = tuple(p.problem_id for s in shards for p in s.request.problems)
                if ids != cohort.problem_ids:
                    raise ValueError("shards must cover the exact ordered cohort once per variant")
        if self.total_generated_tokens > self.limits.max_total_generated_tokens:
            raise ValueError("replay exceeds total declared token ceiling")
        if len(self.shards) * self.limits.shard_timeout_seconds > self.limits.max_total_wall_seconds:
            raise ValueError("replay exceeds total reserved worker wall-time ceiling")
        return self

    @property
    def total_generated_tokens(self) -> int:
        return sum(len(s.request.problems) * (4 if s.request.dataset == "math500" else 2)
                   * (s.request.smoke_max_new_tokens or 32768) for s in self.shards)

    @property
    def digest(self) -> str:
        return content_digest(self.to_dict())

    def resource_summary(self) -> dict[str, Any]:
        return {"plan_digest": self.digest, "mode": self.mode, "shards": len(self.shards),
                "cohorts": [{"dataset": c.dataset, "problems": len(c.problem_ids),
                             "complete_dataset": c.complete_dataset} for c in self.cohorts],
                "max_generated_tokens": self.total_generated_tokens,
                "reserved_worker_seconds": len(self.shards) * self.limits.shard_timeout_seconds,
                "max_total_report_bytes": len(self.shards) * self.limits.max_report_bytes,
                "image": self.runtime_image, "model_execution_performed": False,
                "fidelity_notes": self.fidelity_notes}


def build_replay_plan(
    lock: ReproductionLock, runtime: RuntimeLock, datasets: tuple[VerifiedDataset, ...],
    checkpoint_receipts: tuple[CheckpointReceipt, ...], *, scorer_lock_digest: str,
    published_results_digest: str, limits: ReplayLimits, mode: Literal["reproduction", "smoke"] = "reproduction",
    smoke_max_new_tokens: int | None = None, smoke_problem_limit: int = 16,
) -> ReplayPlan:
    if not datasets or len({d.lock_digest for d in datasets}) != 1:
        raise ValueError("datasets must share one verified dataset lock")
    if type(smoke_problem_limit) is not int or smoke_problem_limit < 1:
        raise ValueError("smoke cohort size must be a positive integer")
    identities: list[CheckpointIdentity] = []
    for receipt in checkpoint_receipts:
        if receipt.lock_digest != lock.digest:
            raise ValueError("checkpoint receipt differs from reproduction lock")
        identities.append(CheckpointIdentity(variant=receipt.variant, receipt_digest=receipt.digest,  # type: ignore[arg-type]
                                             checkpoint_sha256=receipt.checkpoint_sha256,
                                             tensor_content_digest=receipt.tensor_content_digest))
    cohorts: list[CohortIdentity] = []
    shards: list[ReplayShard] = []
    for dataset in datasets:
        selected = dataset.problems[:smoke_problem_limit] if mode == "smoke" else dataset.problems
        cohorts.append(CohortIdentity(dataset=dataset.dataset, dataset_sha256=dataset.digest,
                                      problem_ids=tuple(p.problem_id for p in selected),
                                      complete_dataset=len(selected) == len(dataset.problems)))
        batches: list[tuple[InferenceProblem, ...]] = []
        current: tuple[InferenceProblem, ...] = ()

        def request(
            problems: tuple[InferenceProblem, ...], variant: Variant = "base", current_dataset: VerifiedDataset = dataset,
        ) -> InferenceRequest:
            return InferenceRequest(variant=variant, dataset=current_dataset.dataset, dataset_sha256=current_dataset.digest,
                                    problems=problems, mode=mode, smoke_max_new_tokens=smoke_max_new_tokens)

        def fits(problems: tuple[InferenceProblem, ...]) -> bool:
            candidate = request(problems)
            samples = getattr(lock.evaluation.samples_per_problem, candidate.dataset)
            return (len(problems) <= limits.max_shard_problems
                    and len(problems) * samples * (candidate.smoke_max_new_tokens or 32768) <= limits.max_shard_generated_tokens
                    and estimate_report_bytes(candidate) <= limits.max_report_bytes)

        for problem in selected:
            item = InferenceProblem(problem_id=problem.problem_id, text=problem.text)
            if current and not fits((*current, item)):
                batches.append(current)
                current = ()
            if not fits((item,)):
                raise ValueError(f"one problem cannot fit the shard limits: {problem.problem_id}")
            current += (item,)
        if current:
            batches.append(current)
        for ordinal, batch in enumerate(batches):
            # Alternate which arm executes first without changing within-arm cohort order.
            variants: tuple[Variant, Variant] = ("base", "published_patch") if ordinal % 2 == 0 else ("published_patch", "base")
            for variant in variants:
                payload = request(batch, variant)
                shards.append(ReplayShard(shard_id=f"{dataset.dataset}-{ordinal:05d}-{variant}", ordinal=ordinal,
                                          request=payload, estimated_report_bytes=estimate_report_bytes(payload)))
    plan = ReplayPlan(lock_digest=lock.digest, runtime_digest=runtime.digest, runtime_image=runtime.image,
                      dataset_lock_digest=datasets[0].lock_digest, scorer_lock_digest=scorer_lock_digest,
                      published_results_digest=published_results_digest, mode=mode, cohorts=tuple(cohorts),
                      checkpoints=tuple(identities), shards=tuple(shards), limits=limits)
    validate_plan_datasets(plan, datasets)
    return plan


def load_replay_plan(path: Path) -> ReplayPlan:
    return ReplayPlan.model_validate_json(json.dumps(load_small_json(path, max_bytes=32 * 1024 * 1024)))


def validate_plan_datasets(plan: ReplayPlan, datasets: tuple[VerifiedDataset, ...]) -> None:
    """Bind projected inference inputs back to the verified scoring-only cohort."""
    by_name = {dataset.dataset: dataset for dataset in datasets}
    if len(by_name) != len(datasets) or set(by_name) != {cohort.dataset for cohort in plan.cohorts}:
        raise ValueError("verified datasets differ from planned cohorts")
    for cohort in plan.cohorts:
        dataset = by_name[cohort.dataset]
        if (dataset.digest != cohort.dataset_sha256 or dataset.lock_digest != plan.dataset_lock_digest
                or cohort_digest(dataset.problems) != dataset.digest):
            raise ValueError("dataset identity differs from frozen plan")
        selected = tuple(problem for problem in dataset.problems if problem.problem_id in set(cohort.problem_ids))
        if tuple(problem.problem_id for problem in selected) != cohort.problem_ids:
            raise ValueError("planned problems differ from verified dataset membership/order")
        if cohort.complete_dataset != (len(selected) == len(dataset.problems)):
            raise ValueError("planned complete-dataset claim differs from actual coverage")
        actual = {problem.problem_id: problem for problem in selected}
        for shard in (s for s in plan.shards if s.request.dataset == cohort.dataset):
            if any(problem.text != actual[problem.problem_id].text for problem in shard.request.problems):
                raise ValueError("inference prompt differs from the verified benchmark problem")
