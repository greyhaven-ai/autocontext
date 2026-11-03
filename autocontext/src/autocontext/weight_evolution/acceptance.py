"""Live-only Qwen loader/generation acceptance before the full GPU study.

Acceptance executes two checkpoint reconstructions and six one-problem inference
shards. It establishes a usable local execution path, never a benchmark effect.
Claims survive failures; ambiguous work cannot silently acquire a retry budget.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import (
    MAX_CHECKPOINT_BYTES,
    CheckpointReceipt,
    canonical_digest,
    file_identity,
    load_small_json,
)
from autocontext.weight_evolution.cohort import ReplayLimits, ReplayPlan, build_replay_plan, validate_plan_datasets
from autocontext.weight_evolution.datasets import VerifiedDataset, cohort_digest
from autocontext.weight_evolution.docker_runner import WeightDockerRunner, WeightWorkerLimits, worker_harness_digest
from autocontext.weight_evolution.evaluation import read_shard_evidence
from autocontext.weight_evolution.models import load_reproduction_lock
from autocontext.weight_evolution.patch_worker import verify_checkpoint
from autocontext.weight_evolution.replay import ReplayIncompleteError, _immutable_json, run_replay
from autocontext.weight_evolution.runtime import REQUIRED_PACKAGES, RuntimeLock, load_runtime_lock

_DATASETS: tuple[Literal["math500", "gsm8k", "ifeval"], ...] = ("math500", "gsm8k", "ifeval")
_VARIANTS: tuple[Literal["base", "published_patch"], ...] = ("base", "published_patch")


def _worker_limits(limits: ReplayLimits) -> WeightWorkerLimits:
    return WeightWorkerLimits(timeout_seconds=limits.shard_timeout_seconds, memory_mb=limits.memory_mb,
                              cpu_count=limits.cpu_count, max_report_bytes=limits.max_report_bytes)


def _ordered_datasets(datasets: tuple[VerifiedDataset, ...]) -> tuple[VerifiedDataset, ...]:
    by_name = {dataset.dataset: dataset for dataset in datasets}
    if len(datasets) != 3 or set(by_name) != set(_DATASETS) or len({d.lock_digest for d in datasets}) != 1:
        raise ValueError("GPU acceptance requires all three datasets from one verified lock")
    if any(not d.problems or cohort_digest(d.problems) != d.digest for d in datasets):
        raise ValueError("GPU acceptance dataset contents differ from their verified identities")
    return tuple(by_name[name] for name in _DATASETS)


def acceptance_resource_summary(
    *, lock_path: Path, runtime_path: Path, asset_root: Path, datasets: tuple[VerifiedDataset, ...],
    scorer_lock_digest: str, published_results_digest: str, limits: ReplayLimits,
    smoke_max_new_tokens: int = 64,
) -> dict[str, Any]:
    """Validate a fixed acceptance budget without Docker or model execution.

Asset bytes are independently verified by reconstruction, not by this dry plan.
The temporary checkpoint identities below only validate smoke shard bounds and
are never persisted or represented as verified checkpoint evidence.
    """
    del asset_root
    lock, runtime = load_reproduction_lock(lock_path), load_runtime_lock(runtime_path)
    datasets = _ordered_datasets(datasets)
    placeholders = tuple(CheckpointReceipt(v, lock.digest, "0" * 64, "0" * 64, (), (), (), (), 0, 0) for v in _VARIANTS)
    smoke = build_replay_plan(lock, runtime, datasets, placeholders, scorer_lock_digest=scorer_lock_digest,
                             published_results_digest=published_results_digest, limits=limits, mode="smoke",
                             smoke_max_new_tokens=smoke_max_new_tokens, smoke_problem_limit=1)
    reserved = 8 * limits.shard_timeout_seconds
    if reserved > limits.max_total_wall_seconds:
        raise ValueError("acceptance wall-time ceiling must include two reconstructions and six smoke shards")
    return {
        "schema_version": 1, "scope": "gpu_inference_acceptance", "lock_digest": lock.digest,
        "runtime_digest": runtime.digest, "runtime_image": runtime.image,
        "dataset_lock_digest": datasets[0].lock_digest,
        "datasets": [d.to_dict() for d in datasets], "scorer_lock_digest": scorer_lock_digest,
        "published_results_digest": published_results_digest, "limits": limits.to_dict(),
        "smoke_max_new_tokens": smoke_max_new_tokens, "problems_per_dataset": 1,
        "reconstruction_workers": 2, "inference_workers": 6,
        "max_generated_tokens": smoke.total_generated_tokens, "reserved_worker_seconds": reserved,
        "max_total_report_bytes": 8 * limits.max_report_bytes,
        "checkpoint_storage_reservation_bytes": 3 * MAX_CHECKPOINT_BYTES,
        "storage_note": "Two exported checkpoints plus one staging copy; excludes input assets, Docker image/cache and metadata.",
        "model_execution_performed": False, "benchmark_reproduction_claimed": False,
    }


def _safe_directory(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_dir():
        raise ValueError("acceptance evidence directory is missing or traverses a symlink")


def _object(path: Path, limit: int = 1024 * 1024) -> dict[str, Any]:
    value = load_small_json(path, max_bytes=limit)
    if not isinstance(value, dict):
        raise ValueError("acceptance evidence must be a JSON object")
    return value


def _runtime(runtime: RuntimeLock, observed: Any) -> dict[str, Any]:
    if not isinstance(observed, dict) or observed.get("platform") != runtime.platform:
        raise ValueError("acceptance lacks the pinned runtime platform")
    python = observed.get("python_version")
    packages = observed.get("packages")
    if (not isinstance(python, str) or python.split(".")[:len(runtime.python_version.split("."))]
            != runtime.python_version.split(".") or not isinstance(packages, dict)
            or any(not isinstance(packages.get(name), str) or not packages[name] for name in REQUIRED_PACKAGES)
            or any(packages.get(pin.name) != pin.version for pin in runtime.packages)):
        raise ValueError("acceptance runtime observations differ from declared pins")
    return observed


def _read_reconstruction(
    directory: Path, *, variant: Literal["base", "published_patch"], manifest: dict[str, Any], lock_path: Path,
    runtime_path: Path, asset_root: Path,
) -> tuple[CheckpointReceipt, dict[str, Any]]:
    _safe_directory(directory)
    if {p.name for p in directory.iterdir()} != {"checkpoint", "report.json", "execution.receipt.json"}:
        raise ValueError("acceptance reconstruction is incomplete or contains unexpected files")
    request = manifest["request"]
    report = _object(directory / "report.json", request["limits"]["max_report_bytes"])
    receipt = _object(directory / "execution.receipt.json")
    verified = verify_checkpoint(load_reproduction_lock(lock_path), asset_root, directory / "checkpoint",
                                 expected_variant=variant)
    for document in (report, receipt):
        if (document.get("ok") is not True or document.get("operation") != "reconstruct"
                or document.get("lock_digest") != request["lock_digest"]
                or document.get("runtime_digest") != request["runtime_digest"]):
            raise ValueError("reconstruction envelope differs from acceptance inputs")
    report_identity = file_identity(directory / "report.json", max_bytes=request["limits"]["max_report_bytes"])
    if (canonical_digest(report.get("checkpoint")) != verified.digest or receipt.get("variant") != variant
            or receipt.get("report_sha256") != report_identity["sha256"]
            or receipt.get("harness_digest") != manifest["harness_digest"]
            or receipt.get("image") != request["runtime_image"]
            or receipt.get("limits") != asdict(_worker_limits(ReplayLimits(**request["limits"])))
            or receipt.get("model_execution_performed") is not False
            or receipt.get("network") != "none" or receipt.get("scope") != "development_replay"):
        raise ValueError("reconstruction receipt differs from verified acceptance evidence")
    image = receipt.get("image_identity")
    if (not isinstance(image, dict) or image.get("platform") != "linux/amd64"
            or not isinstance(image.get("id"), str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image["id"]) is None
            or not isinstance(image.get("repo_digests"), list)
            or not any(isinstance(d, str) and d.rsplit("@", 1)[-1] == request["runtime_image"].rsplit("@", 1)[-1]
                       for d in image["repo_digests"])):
        raise ValueError("reconstruction image does not match the pinned runtime")
    observed = _runtime(load_runtime_lock(runtime_path), report.get("observed_runtime"))
    return verified, {"checkpoint_receipt_digest": verified.digest, "report_sha256": report_identity["sha256"],
                      "execution_receipt_sha256": file_identity(directory / "execution.receipt.json")["sha256"],
                      "image_identity": image, "runtime_observation": observed}


def _manifest(
    summary: dict[str, Any], gpu_device: str, expected_gpu_uuid: str | None, host_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    if re.fullmatch(r"(?:[0-9]+|GPU-[A-Za-z0-9-]+)", gpu_device) is None:
        raise ValueError("acceptance requires one explicit GPU index or UUID")
    if expected_gpu_uuid is not None and re.fullmatch(r"GPU-[A-Za-z0-9-]+", expected_gpu_uuid) is None:
        raise ValueError("expected acceptance GPU UUID is invalid")
    if host_identity is not None:
        if (not isinstance(host_identity, dict) or set(host_identity) != {"gpu_uuid", "driver_version", "daemon_id"}
                or any(not isinstance(value, str) or not value.strip() or value != value.strip()
                       or len(value) > 256 or any(ord(char) < 32 for char in value) for value in host_identity.values())
                or re.fullmatch(r"GPU-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", host_identity["gpu_uuid"]) is None):
            raise ValueError("acceptance host identity requires a canonical GPU UUID, driver version and daemon ID")
        if expected_gpu_uuid is not None and host_identity["gpu_uuid"] != expected_gpu_uuid:
            raise ValueError("acceptance host GPU UUID differs from expected GPU UUID")
    return {"request": summary, "gpu_device": gpu_device, "expected_gpu_uuid": expected_gpu_uuid,
            "harness_digest": worker_harness_digest(),
            "host_identity": dict(host_identity) if host_identity is not None else None}


def _smoke_plan(
    manifest: dict[str, Any], receipts: tuple[CheckpointReceipt, ...], datasets: tuple[VerifiedDataset, ...],
    lock_path: Path, runtime_path: Path,
) -> ReplayPlan:
    request = manifest["request"]
    return build_replay_plan(load_reproduction_lock(lock_path), load_runtime_lock(runtime_path), datasets, receipts,
                             scorer_lock_digest=request["scorer_lock_digest"],
                             published_results_digest=request["published_results_digest"],
                             limits=ReplayLimits(**request["limits"]), mode="smoke",
                             smoke_max_new_tokens=request["smoke_max_new_tokens"], smoke_problem_limit=1)


def _acceptance_evidence(
    root: Path, manifest: dict[str, Any], *, lock_path: Path, runtime_path: Path, asset_root: Path,
    datasets: tuple[VerifiedDataset, ...],
) -> tuple[dict[str, Any], ReplayPlan]:
    receipts, reconstructions = [], {}
    for variant in _VARIANTS:
        verified, identity = _read_reconstruction(root / "reconstruction" / variant, variant=variant, manifest=manifest,
                                                lock_path=lock_path, runtime_path=runtime_path, asset_root=asset_root)
        receipts.append(verified)
        reconstructions[variant] = identity
        expected_claim = {"manifest_digest": canonical_digest(manifest), "variant": variant,
                          "reserved_worker_seconds": manifest["request"]["limits"]["shard_timeout_seconds"]}
        if (_object(root / "claims" / f"{variant}.json") != expected_claim
                or _object(root / "completed" / f"{variant}.json") != identity):
            raise ValueError("reconstruction claim or completion changed after publication")
    smoke = _smoke_plan(manifest, tuple(receipts), datasets, lock_path, runtime_path)
    if (_object(root / "smoke" / "plan.json", 32 * 1024 * 1024) != smoke.to_dict()
            or _object(root / "smoke" / "harness.json") != {"harness_digest": manifest["harness_digest"]}):
        raise ValueError("acceptance smoke plan or harness differs from the fixed protocol")
    runs = root / "smoke" / "runs"
    _safe_directory(runs)
    if {p.name for p in runs.iterdir()} != {s.shard_id for s in smoke.shards}:
        raise ValueError("acceptance smoke evidence has missing or surplus shards")
    identities: dict[str, Any] = {}
    prompt_evidence: dict[tuple[str, str], tuple[str, tuple[int, ...]]] = {}
    hardware = runtime = image = None
    for shard in smoke.shards:
        evidence = read_shard_evidence(smoke, shard, runs / shard.shard_id)
        result = evidence.result
        if evidence.harness_digest != manifest["harness_digest"]:
            raise ValueError("acceptance inference harness differs from reconstruction")
        if any(sample.output_tokens < 1 or not sample.text.strip() for sample in result.samples):
            raise ValueError("acceptance requires nonempty generated text and token evidence for every sample")
        for sample in result.samples:
            key = (shard.request.dataset, sample.problem_id)
            prompt = (sample.rendered_prompt, sample.prompt_token_ids)
            if key in prompt_evidence and prompt_evidence[key] != prompt:
                raise ValueError("acceptance paired prompt rendering/tokenization differs")
            prompt_evidence[key] = prompt
        observed = _runtime(load_runtime_lock(runtime_path), result.runtime_environment)
        observed_image = _object(runs / shard.shard_id / "execution.receipt.json")["image_identity"]
        uuid = result.hardware.get("device_uuid")
        if not isinstance(uuid, str) or re.fullmatch(r"GPU-[A-Za-z0-9-]+", uuid) is None:
            raise ValueError("acceptance requires an observed GPU UUID")
        if manifest["expected_gpu_uuid"] is not None and uuid != manifest["expected_gpu_uuid"]:
            raise ValueError("observed GPU UUID differs from the requested acceptance device")
        if manifest["host_identity"] is not None and uuid != manifest["host_identity"]["gpu_uuid"]:
            raise ValueError("observed GPU UUID differs from the acceptance host identity")
        if hardware is None:
            hardware, runtime, image = result.hardware, observed, observed_image
        elif (hardware, runtime, image) != (result.hardware, observed, observed_image):
            raise ValueError("acceptance hardware/runtime/image observations differ across paired shards")
        completion = {"plan_digest": smoke.digest, "shard_id": shard.shard_id,
                      "report_sha256": evidence.report_sha256,
                      "execution_receipt_sha256": evidence.execution_receipt_sha256}
        claim = {"plan_digest": smoke.digest, "shard_id": shard.shard_id, "request_digest": shard.request.digest,
                 "reserved_generated_tokens": result.samples_per_problem * result.max_new_tokens,
                 "reserved_worker_seconds": smoke.limits.shard_timeout_seconds}
        if _object(root / "smoke" / "claims" / f"{shard.shard_id}.json") != claim:
            raise ValueError("acceptance smoke claim differs from fixed reservation")
        if _object(root / "smoke" / "completed" / f"{shard.shard_id}.json") != completion:
            raise ValueError("acceptance smoke completion evidence was replaced")
        identities[shard.shard_id] = completion
    if any((item["runtime_observation"], item["image_identity"]) != (runtime, image)
           for item in reconstructions.values()):
        raise ValueError("reconstruction and inference used different runtime/image observations")
    replay_receipt = _object(root / "smoke" / "replay.receipt.json")
    if (replay_receipt.get("ok") is not True or replay_receipt.get("plan_digest") != smoke.digest
            or replay_receipt.get("completed_shards") != [s.shard_id for s in smoke.shards]):
        raise ValueError("acceptance smoke replay completion is missing or inconsistent")
    return {"schema_version": 1, "ok": True, "scope": "gpu_inference_acceptance",
            "manifest_digest": canonical_digest(manifest), "smoke_plan_digest": smoke.digest,
            "gpu_device": manifest["gpu_device"], "hardware": hardware, "runtime_observation": runtime,
            "runtime_environment": runtime, "host_identity": manifest["host_identity"],
            "runtime_observation_digest": canonical_digest(runtime), "image_identity": image,
            "harness_digest": manifest["harness_digest"], "reconstructions": reconstructions,
            "smoke_evidence": identities, "model_execution_performed": True,
            "benchmark_reproduction_claimed": False}, smoke


def run_gpu_acceptance(
    *, lock_path: Path, runtime_path: Path, asset_root: Path, datasets: tuple[VerifiedDataset, ...],
    scorer_lock_digest: str, published_results_digest: str, output_dir: Path, gpu_device: str,
    limits: ReplayLimits, smoke_max_new_tokens: int = 64, expected_gpu_uuid: str | None = None,
    runner: WeightDockerRunner | None = None, host_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute or reconcile exactly two reconstructions and six live smoke shards."""
    datasets = _ordered_datasets(datasets)
    summary = acceptance_resource_summary(lock_path=lock_path, runtime_path=runtime_path, asset_root=asset_root,
                                          datasets=datasets, scorer_lock_digest=scorer_lock_digest,
                                          published_results_digest=published_results_digest, limits=limits,
                                          smoke_max_new_tokens=smoke_max_new_tokens)
    manifest = _manifest(summary, gpu_device, expected_gpu_uuid, host_identity)
    worker = runner or WeightDockerRunner(limits=_worker_limits(limits))
    if worker.limits != _worker_limits(limits):
        raise ValueError("worker resource limits differ from acceptance budget")
    root = output_dir.absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError("acceptance output must not traverse symlinks")
    root.parent.mkdir(parents=True, exist_ok=True)
    with advisory_path_lock(root.parent / f".{root.name}.acceptance.lock"):
        if root.exists():
            if _object(root / "manifest.json") != manifest:
                raise ReplayIncompleteError("acceptance inputs, GPU or harness changed; restore the frozen request")
        else:
            root.mkdir()
            _immutable_json(root / "manifest.json", manifest)
            for name in ("reconstruction", "claims", "completed", "failures"):
                (root / name).mkdir()
        for name in ("reconstruction", "claims", "completed", "failures"):
            _safe_directory(root / name)
        receipts = []
        for variant in _VARIANTS:
            directory = root / "reconstruction" / variant
            claim_path, complete_path = root / "claims" / f"{variant}.json", root / "completed" / f"{variant}.json"
            claim = {"manifest_digest": canonical_digest(manifest), "variant": variant,
                     "reserved_worker_seconds": limits.shard_timeout_seconds}
            if claim_path.exists():
                if _object(claim_path) != claim or not directory.exists():
                    raise ReplayIncompleteError("ambiguous reconstruction claim; do not auto-retry")
            else:
                if directory.exists() or complete_path.exists():
                    raise ReplayIncompleteError("unclaimed reconstruction output cannot be adopted")
                options = dict(operation="reconstruct", lock_path=lock_path, runtime_path=runtime_path,
                               asset_root=asset_root, variant=variant)
                dispatch = worker.plan(**options)  # type: ignore[arg-type]
                if (dispatch["lock_digest"], dispatch["runtime_digest"]) != (summary["lock_digest"], summary["runtime_digest"]):
                    raise ValueError("worker inputs changed before reconstruction dispatch")
                _immutable_json(claim_path, claim)
                try:
                    worker.run(output_dir=directory, expected_harness_digest=manifest["harness_digest"],
                               **options)  # type: ignore[arg-type]
                except BaseException as exc:
                    _immutable_json(root / "failures" / f"{variant}.json", {
                        "claim": claim, "error_type": type(exc).__name__, "error": str(exc)[:4096],
                        "automatic_retry_permitted": False,
                    })
                    raise
            verified, identity = _read_reconstruction(directory, variant=variant, manifest=manifest,
                                                     lock_path=lock_path, runtime_path=runtime_path, asset_root=asset_root)
            if complete_path.exists():
                if _object(complete_path) != identity:
                    raise ReplayIncompleteError("reconstruction completion evidence changed")
            else:
                _immutable_json(complete_path, identity)
            receipts.append(verified)
        smoke = _smoke_plan(manifest, tuple(receipts), datasets, lock_path, runtime_path)
        run_replay(smoke, datasets=datasets, lock_path=lock_path, runtime_path=runtime_path, asset_root=asset_root,
                   checkpoints={v: root / "reconstruction" / v / "checkpoint" for v in _VARIANTS},
                   output_dir=root / "smoke", gpu_device=gpu_device, runner=worker)
        evidence, _ = _acceptance_evidence(root, manifest, lock_path=lock_path, runtime_path=runtime_path,
                                          asset_root=asset_root, datasets=datasets)
        marker = root / "acceptance.receipt.json"
        if marker.exists():
            if _object(marker) != evidence:
                raise ValueError("acceptance completion evidence changed")
        else:
            _immutable_json(marker, evidence)
        return evidence


def verify_gpu_acceptance(
    path: Path, *, plan: ReplayPlan, lock_path: Path, runtime_path: Path, asset_root: Path,
    datasets: tuple[VerifiedDataset, ...], gpu_device: str | None = None, expected_gpu_uuid: str | None = None,
) -> dict[str, Any]:
    """Revalidate saved live acceptance and bind it to a proposed full replay."""
    if plan.mode != "reproduction" or {c.dataset for c in plan.cohorts} != set(_DATASETS):
        raise ValueError("acceptance gate requires a full three-dataset reproduction plan")
    validate_plan_datasets(plan, _ordered_datasets(datasets))
    _safe_directory(path)
    manifest = _object(path / "manifest.json")
    if (set(manifest) != {"request", "gpu_device", "expected_gpu_uuid", "harness_digest", "host_identity"}
            or not isinstance(manifest["request"], dict)
            or not {"limits", "smoke_max_new_tokens"}.issubset(manifest["request"])):
        raise ValueError("acceptance manifest is incomplete")
    saved = manifest["request"]
    summary = acceptance_resource_summary(lock_path=lock_path, runtime_path=runtime_path, asset_root=asset_root,
                                          datasets=datasets, scorer_lock_digest=plan.scorer_lock_digest,
                                          published_results_digest=plan.published_results_digest,
                                          limits=ReplayLimits(**saved["limits"]),
                                          smoke_max_new_tokens=saved["smoke_max_new_tokens"])
    if manifest != _manifest(summary, manifest["gpu_device"], manifest["expected_gpu_uuid"], manifest["host_identity"]):
        raise ValueError("acceptance inputs or harness no longer match the frozen evidence")
    evidence, smoke = _acceptance_evidence(path, manifest, lock_path=lock_path, runtime_path=runtime_path,
                                          asset_root=asset_root, datasets=_ordered_datasets(datasets))
    if _object(path / "acceptance.receipt.json") != evidence:
        raise ValueError("acceptance completion receipt differs from raw evidence")
    for name in ("lock_digest", "runtime_digest", "runtime_image", "dataset_lock_digest", "scorer_lock_digest",
                 "published_results_digest", "checkpoints"):
        if getattr(smoke, name) != getattr(plan, name):
            raise ValueError(f"full replay differs from accepted {name}")
    if gpu_device is not None and gpu_device != evidence["gpu_device"]:
        raise ValueError("full replay GPU selector differs from acceptance")
    if expected_gpu_uuid is not None and expected_gpu_uuid != evidence["hardware"]["device_uuid"]:
        raise ValueError("full replay GPU UUID differs from acceptance")
    return evidence
