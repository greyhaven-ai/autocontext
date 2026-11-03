"""Serial bounded replay with durable claims and explicit crash reconciliation.

No automatic retries: a claim without complete verified evidence is ambiguous
and requires operator reconciliation. Reusing completed shards never dispatches
them again. This is a frozen replay controller, not the later editing campaign.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.util.json_io import write_json
from autocontext.weight_evolution.artifacts import load_small_json
from autocontext.weight_evolution.cohort import ReplayPlan, validate_plan_datasets
from autocontext.weight_evolution.datasets import VerifiedDataset
from autocontext.weight_evolution.docker_runner import WeightDockerRunner, WeightWorkerLimits, worker_harness_digest
from autocontext.weight_evolution.models import load_reproduction_lock
from autocontext.weight_evolution.patch_worker import verify_checkpoint
from autocontext.weight_evolution.runtime import load_runtime_lock


class ReplayIncompleteError(RuntimeError):
    """A prior dispatch cannot be retried safely without reconciliation."""


def _immutable_json(path: Path, value: dict[str, Any]) -> None:
    data = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode()
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def run_replay(
    plan: ReplayPlan, *, datasets: tuple[VerifiedDataset, ...], lock_path: Path, runtime_path: Path,
    asset_root: Path, checkpoints: Mapping[str, Path], output_dir: Path, gpu_device: str,
    runner: WeightDockerRunner | None = None,
) -> dict[str, Any]:
    """Execute the exact frozen plan once, or reconcile already completed work."""
    from autocontext.weight_evolution.evaluation import read_shard_evidence

    validate_plan_datasets(plan, datasets)
    lock, runtime = load_reproduction_lock(lock_path), load_runtime_lock(runtime_path)
    if lock.digest != plan.lock_digest or runtime.digest != plan.runtime_digest or runtime.image != plan.runtime_image:
        raise ValueError("replay contracts differ from the frozen plan")
    if set(checkpoints) != {"base", "published_patch"}:
        raise ValueError("replay requires both independently verified checkpoints")
    for expected in plan.checkpoints:
        verified = verify_checkpoint(lock, asset_root, checkpoints[expected.variant], expected_variant=expected.variant)
        if (verified.digest, verified.checkpoint_sha256, verified.tensor_content_digest) != (
            expected.receipt_digest, expected.checkpoint_sha256, expected.tensor_content_digest,
        ):
            raise ValueError("checkpoint differs from frozen replay identity")
    limits = WeightWorkerLimits(timeout_seconds=plan.limits.shard_timeout_seconds, memory_mb=plan.limits.memory_mb,
                                cpu_count=plan.limits.cpu_count, max_report_bytes=plan.limits.max_report_bytes)
    worker = runner or WeightDockerRunner(limits=limits)
    if worker.limits != limits:
        raise ValueError("worker resource limits differ from the frozen plan")
    root = output_dir.absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("replay output must not traverse symlinks")
    root.parent.mkdir(parents=True, exist_ok=True)
    with advisory_path_lock(root.parent / f".{root.name}.replay.lock"):
        harness_digest = worker_harness_digest()
        if root.exists():
            persisted = load_small_json(root / "plan.json", max_bytes=32 * 1024 * 1024)
            if persisted != plan.to_dict():
                raise ValueError("existing replay directory belongs to a different plan")
            if load_small_json(root / "harness.json", max_bytes=65536) != {"harness_digest": harness_digest}:
                raise ReplayIncompleteError("worker source changed; resume requires the original replay harness")
        else:
            root.mkdir()
            _immutable_json(root / "plan.json", plan.to_dict())
            _immutable_json(root / "harness.json", {"harness_digest": harness_digest})
            for name in ("claims", "requests", "runs", "failures", "completed"):
                (root / name).mkdir()
        for name in ("claims", "requests", "runs", "failures", "completed"):
            if (root / name).is_symlink() or not (root / name).is_dir():
                raise ReplayIncompleteError("replay state directory is missing or unsafe")
        completed: list[str] = []
        dispatched = 0
        for shard in plan.shards:
            claim_path = root / "claims" / f"{shard.shard_id}.json"
            run_path = root / "runs" / shard.shard_id
            complete_path = root / "completed" / f"{shard.shard_id}.json"
            claim = {"plan_digest": plan.digest, "shard_id": shard.shard_id,
                     "request_digest": shard.request.digest,
                     "reserved_generated_tokens": len(shard.request.problems)
                     * (4 if shard.request.dataset == "math500" else 2) * (shard.request.smoke_max_new_tokens or 32768),
                     "reserved_worker_seconds": plan.limits.shard_timeout_seconds}
            if claim_path.exists():
                if load_small_json(claim_path, max_bytes=65536) != claim:
                    raise ReplayIncompleteError("persisted dispatch claim differs from the frozen shard")
                if not run_path.exists():
                    raise ReplayIncompleteError(f"claimed shard has no complete result; do not auto-retry: {shard.shard_id}")
                evidence = read_shard_evidence(plan, shard, run_path)
                if evidence.harness_digest != harness_digest:
                    raise ReplayIncompleteError("completed result differs from the frozen replay harness")
                completion = {"plan_digest": plan.digest, "shard_id": shard.shard_id,
                              "report_sha256": evidence.report_sha256,
                              "execution_receipt_sha256": evidence.execution_receipt_sha256}
                if complete_path.exists():
                    if load_small_json(complete_path, max_bytes=65536) != completion:
                        raise ReplayIncompleteError("completed shard evidence was replaced after publication")
                else:
                    _immutable_json(complete_path, completion)
                completed.append(shard.shard_id)
                continue
            if run_path.exists() or complete_path.exists():
                raise ReplayIncompleteError("unclaimed output exists; refusing to adopt or overwrite it")
            request_path = root / "requests" / f"{shard.shard_id}.json"
            if request_path.exists():
                if load_small_json(request_path, max_bytes=16 * 1024 * 1024) != shard.request.to_dict():
                    raise ReplayIncompleteError("prepared request differs from frozen plan")
            else:
                _immutable_json(request_path, shard.request.to_dict())
            # Validate the exact worker inputs before persisting a costly claim.
            options: dict[str, Any] = dict(
                operation="infer", lock_path=lock_path, runtime_path=runtime_path,
                asset_root=asset_root, variant=shard.request.variant,
                checkpoint=checkpoints[shard.request.variant], request_path=request_path, gpu_device=gpu_device,
            )
            dispatch_plan = worker.plan(**options)
            if (dispatch_plan["lock_digest"], dispatch_plan["runtime_digest"], dispatch_plan["request_digest"]) != (
                plan.lock_digest, plan.runtime_digest, shard.request.digest,
            ):
                raise ValueError("worker inputs changed after the replay plan was frozen")
            _immutable_json(claim_path, claim)
            started = time.monotonic()
            try:
                worker.run(output_dir=run_path, expected_harness_digest=harness_digest, **options)
                evidence = read_shard_evidence(plan, shard, run_path)
                if evidence.harness_digest != harness_digest:
                    raise ReplayIncompleteError("worker result differs from the frozen replay harness")
                _immutable_json(complete_path, {"plan_digest": plan.digest, "shard_id": shard.shard_id,
                                               "report_sha256": evidence.report_sha256,
                                               "execution_receipt_sha256": evidence.execution_receipt_sha256})
            except BaseException as exc:
                _immutable_json(root / "failures" / f"{shard.shard_id}.json", {
                    "plan_digest": plan.digest, "shard_id": shard.shard_id, "status": "unreconciled",
                    "error_type": type(exc).__name__, "error": str(exc)[:4096],
                    "elapsed_seconds": time.monotonic() - started,
                    "automatic_retry_permitted": False,
                })
                raise
            completed.append(shard.shard_id)
            dispatched += 1
        receipt = {"schema_version": 1, "ok": True, "plan_digest": plan.digest,
                   "completed_shards": completed, "shard_count": len(completed),
                   "max_generated_tokens": plan.total_generated_tokens,
                   "reserved_worker_seconds": len(plan.shards) * plan.limits.shard_timeout_seconds}
        write_json(root / "replay.receipt.json", receipt)
        return {**receipt, "dispatched_this_invocation": dispatched}
