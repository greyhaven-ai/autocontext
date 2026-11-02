"""Bounded, offline Docker execution for trusted Qwen replay workers.

This development runner executes repository code and data-only artifacts. It
does not accept editor code or certify shared-GPU isolation/promotion evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from autocontext.execution.docker_isolation import (
    DockerIsolationLimits,
    build_docker_isolation_command,
    sanitized_docker_environment,
)
from autocontext.kernel_evolution import _process_control
from autocontext.kernel_evolution.docker_watchdog import (
    create_docker_container,
    launch_bounded_output_drains,
    launch_deadline_watchdog,
    remove_docker_container,
    start_attached_docker_container,
    terminate_process_group,
    verify_docker_container_removed,
)
from autocontext.util.json_io import write_json
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.inference_models import InferenceResult, load_inference_request
from autocontext.weight_evolution.models import _unique_object, load_reproduction_lock
from autocontext.weight_evolution.runtime import load_runtime_lock


@dataclass(frozen=True)
class WeightWorkerLimits:
    timeout_seconds: float = 1800.0
    memory_mb: int = 16384
    cpu_count: float = 4.0
    pids_limit: int = 512
    max_output_bytes: int = 1024 * 1024
    max_report_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        DockerIsolationLimits(memory_mb=self.memory_mb, cpu_count=self.cpu_count, pids_limit=self.pids_limit)
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 86400:
            raise ValueError("worker timeout must be positive, finite and at most 24 hours")
        for value in (self.max_output_bytes, self.max_report_bytes):
            if type(value) is not int or not 0 < value <= 256 * 1024 * 1024:
                raise ValueError("worker output bounds must be positive integers at most 256 MiB")


def _real_path(path: Path, *, directory: bool) -> Path:
    absolute = path.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError(f"symlink input paths are not supported: {path}")
    resolved = absolute.resolve(strict=True)
    if not (resolved.is_dir() if directory else resolved.is_file()):
        raise ValueError(f"unexpected input file type: {path}")
    if any(char in str(resolved) for char in ",\n\r\0"):
        raise ValueError("Docker mount paths contain unsupported characters")
    return resolved


def _read_report(path: Path, limit: int) -> dict[str, Any]:
    _real_path(path, directory=False)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("worker report exceeds byte budget")
    value = json.loads(data, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("worker report must be an object")
    return value


def _harness_sources() -> tuple[Path, list[Path]]:
    package = Path(__file__).resolve().parent.parent
    files = [*sorted((package / "weight_evolution").glob("*.py")), package / "runtime_images.py",
             package / "util" / "json_io.py", package / "util" / "file_lock.py", package / "util" / "models.py"]
    return package, files


def worker_harness_digest() -> str:
    """Fingerprint the same source closure copied into each isolated worker."""
    package, files = _harness_sources()
    digest = hashlib.sha256()
    for source in files:
        digest.update(str(source.relative_to(package)).encode() + b"\0" + hashlib.sha256(source.read_bytes()).digest())
    return digest.hexdigest()


def _copy_harness(destination: Path) -> str:
    """Copy only trusted source modules, excluding editable install/site hooks."""
    package, files = _harness_sources()
    digest = hashlib.sha256()
    for source in files:
        relative = source.relative_to(package)
        data = source.read_bytes()
        target = destination / "autocontext" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        digest.update(str(relative).encode() + b"\0" + hashlib.sha256(data).digest())
    # Namespace packages avoid importing autocontext's unrelated application stack.
    return digest.hexdigest()


class WeightDockerRunner:
    def __init__(self, *, docker_binary: str = "docker", limits: WeightWorkerLimits | None = None) -> None:
        self.docker_binary = docker_binary
        self.limits = limits or WeightWorkerLimits()

    def plan(
        self, *, operation: Literal["reconstruct", "infer"], lock_path: Path, runtime_path: Path,
        asset_root: Path, variant: Literal["base", "published_patch"] = "published_patch",
        checkpoint: Path | None = None, request_path: Path | None = None, gpu_device: str | None = None,
    ) -> dict[str, Any]:
        if operation not in {"reconstruct", "infer"} or variant not in {"base", "published_patch"}:
            raise ValueError("unsupported worker operation or variant")
        lock = load_reproduction_lock(_real_path(lock_path, directory=False))
        runtime = load_runtime_lock(_real_path(runtime_path, directory=False))
        _real_path(asset_root, directory=True)
        plan: dict[str, Any] = {
            "operation": operation, "lock_digest": lock.digest, "runtime_digest": runtime.digest,
            "image": runtime.image, "platform": runtime.platform, "limits": asdict(self.limits),
            "variant": variant, "network": "none", "model_execution_performed": False,
            "gpu_device": gpu_device, "scope": "development_replay", "gpu_memory_isolation": "not_attested",
            "fidelity_notes": list(runtime.fidelity_notes),
        }
        if operation == "infer":
            if checkpoint is None or request_path is None or gpu_device is None:
                raise ValueError("inference requires a checkpoint, request and one explicit GPU device")
            _real_path(checkpoint, directory=True)
            request = load_inference_request(_real_path(request_path, directory=False))
            if request.variant != variant:
                raise ValueError("request variant differs from selected checkpoint variant")
            samples = getattr(lock.evaluation.samples_per_problem, request.dataset)
            tokens = request.smoke_max_new_tokens if request.mode == "smoke" else lock.evaluation.max_new_tokens
            plan.update(request_digest=request.digest, problems=len(request.problems),
                        samples=len(request.problems) * samples,
                        max_generated_tokens=len(request.problems) * samples * int(tokens or 0), mode=request.mode)
        elif checkpoint is not None or request_path is not None or gpu_device is not None:
            raise ValueError("CPU reconstruction does not accept inference inputs or a GPU")
        # Also validate the device without requiring Docker to be installed for dry runs.
        build_docker_isolation_command(
            docker_binary=self.docker_binary, image=runtime.image, container_name="weight-plan", labels={},
            limits=DockerIsolationLimits(self.limits.memory_mb, self.limits.cpu_count, self.limits.pids_limit),
            readonly_mounts={}, writable_mounts={}, tmpfs_mounts={}, argv=["python3"], gpu_device=gpu_device,
        )
        return plan

    def _image_identity(self, image: str) -> dict[str, Any]:
        result = subprocess.run(
            [self.docker_binary, "image", "inspect", image], capture_output=True, text=True,
            check=False, timeout=10, env=sanitized_docker_environment(),
        )
        if result.returncode:
            raise RuntimeError("pinned image is not installed; explicitly pull the locked digest before execution")
        data = json.loads(result.stdout)[0]
        expected_digest = image.rsplit("@", 1)[1]
        if (data.get("Os"), data.get("Architecture")) != ("linux", "amd64") or not any(
            item.rsplit("@", 1)[-1] == expected_digest for item in data.get("RepoDigests", [])
        ):
            raise RuntimeError("installed Docker image does not match locked digest/platform")
        return {"id": data["Id"], "repo_digests": data["RepoDigests"], "platform": "linux/amd64"}

    def run(
        self, *, operation: Literal["reconstruct", "infer"], lock_path: Path, runtime_path: Path,
        asset_root: Path, output_dir: Path, variant: Literal["base", "published_patch"] = "published_patch",
        checkpoint: Path | None = None, request_path: Path | None = None, gpu_device: str | None = None,
        expected_harness_digest: str | None = None,
    ) -> dict[str, Any]:
        plan = self.plan(operation=operation, lock_path=lock_path, runtime_path=runtime_path,
                         asset_root=asset_root, variant=variant, checkpoint=checkpoint,
                         request_path=request_path, gpu_device=gpu_device)
        parent = _real_path(output_dir.parent, directory=True)
        destination = parent / output_dir.name
        if destination.exists() or destination.is_symlink():
            raise ValueError("worker output directory must not already exist")
        binary = shutil.which(self.docker_binary)
        if binary is None:
            raise RuntimeError("Docker executable is unavailable")
        self.docker_binary = binary
        image_identity = self._image_identity(plan["image"])
        with tempfile.TemporaryDirectory(prefix=".weight-worker-", dir=parent) as staging:
            root = Path(staging)
            inputs, output, harness = root / "input", root / "output", root / "harness"
            inputs.mkdir()
            output.mkdir()
            harness_digest = _copy_harness(harness)
            if expected_harness_digest is not None and harness_digest != expected_harness_digest:
                raise ValueError("worker source changed after replay harness identity was frozen")
            # Snapshot validated contract inputs; no mutable file paths enter the request.
            locked = load_reproduction_lock(lock_path)
            runtime = load_runtime_lock(runtime_path)
            if locked.digest != plan["lock_digest"] or runtime.digest != plan["runtime_digest"]:
                raise ValueError("contract/runtime changed after the resource plan was prepared")
            for name, value in (("lock.json", locked.to_dict()), ("runtime.json", runtime.to_dict())):
                write_json(inputs / name, value)
            mounts = {_real_path(asset_root, directory=True): "/assets", inputs: "/input", harness: "/harness"}
            argv = ["python3", "-B", "-m", "autocontext.weight_evolution.worker_entrypoint", operation,
                    "--lock", "/input/lock.json", "--runtime", "/input/runtime.json", "--asset-root", "/assets",
                    "--output", "/output", "--variant", variant, "--max-report-bytes", str(self.limits.max_report_bytes)]
            if operation == "infer":
                assert checkpoint is not None and request_path is not None
                mounts[_real_path(checkpoint, directory=True)] = "/checkpoint"
                frozen_request = load_inference_request(request_path)
                if frozen_request.digest != plan["request_digest"]:
                    raise ValueError("inference request changed after the resource plan was prepared")
                write_json(inputs / "request.json", frozen_request.to_dict())
                argv.extend(("--checkpoint", "/checkpoint", "--request", "/input/request.json"))
            name = f"autocontext-weight-{uuid.uuid4().hex}"
            expires = time.time() + self.limits.timeout_seconds
            command = build_docker_isolation_command(
                docker_binary=binary, image=plan["image"], container_name=name,
                labels={"ai.autocontext.weight-worker": name, "ai.autocontext.expires-at": str(expires)},
                limits=DockerIsolationLimits(self.limits.memory_mb, self.limits.cpu_count, self.limits.pids_limit),
                readonly_mounts=mounts, writable_mounts={output: "/output"},
                tmpfs_mounts={"/tmp": "rw,nosuid,nodev,size=4g", "/dev/shm": "rw,nosuid,nodev,size=1g"},
                argv=argv, gpu_device=gpu_device, auto_remove=False, working_dir="/harness",
                entrypoint="/usr/bin/env", environment={
                    "PYTHONPATH": "/harness", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HOME": "/tmp/hf",
                    "VLLM_CACHE_ROOT": "/tmp/vllm", "VLLM_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1",
                    "LD_LIBRARY_PATH": "/usr/local/nvidia/lib64:/usr/local/nvidia/lib:/usr/local/cuda/lib64",
                    "CUDA_HOME": "/usr/local/cuda",
                },
            )
            process = None
            watchdog = None
            drains: list[threading.Thread] = []
            stdout, stderr = _process_control.BoundedOutput(), _process_control.BoundedOutput()
            quota = threading.Event()
            started = time.monotonic()
            try:
                create_docker_container(command, timeout_seconds=min(10, self.limits.timeout_seconds))
                watchdog = launch_deadline_watchdog(
                    binary, name, expires, root / "watchdog.ready", owner_label="ai.autocontext.weight-worker",
                )
                process = start_attached_docker_container(binary, name, capture_output=True)
                drains = launch_bounded_output_drains(
                    process, max_output_bytes=self.limits.max_output_bytes, stdout=stdout, stderr=stderr,
                    quota_exceeded=quota, terminate=lambda: remove_docker_container(binary, name),
                )
                code = process.wait(timeout=max(0.001, expires - time.time()))
                for thread in drains:
                    thread.join(timeout=5)
                if quota.is_set() or stdout.read_failed or stderr.read_failed or any(t.is_alive() for t in drains):
                    raise RuntimeError("worker output exceeded budget or could not be read completely")
                if code != 0:
                    raise RuntimeError(f"weight worker failed (exit {code}): {stderr.text[-2000:]}")
            finally:
                removed = False
                try:
                    remove_docker_container(binary, name)
                    verify_docker_container_removed(binary, name)
                    removed = True
                finally:
                    if process is not None:
                        terminate_process_group(process, description="weight worker Docker CLI")
                    # If daemon cleanup fails, preserve the independent watchdog
                    # so it can still enforce expiry after the coordinator exits.
                    if watchdog is not None and removed:
                        terminate_process_group(watchdog, description="weight worker deadline watchdog")
                    for thread in drains:
                        thread.join(timeout=5)
            report = _read_report(output / "report.json", self.limits.max_report_bytes)
            expected_files = {"report.json", "checkpoint"} if operation == "reconstruct" else {"report.json"}
            if {path.name for path in output.iterdir()} != expected_files:
                raise ValueError("worker emitted unexpected output files")
            if report.get("ok") is not True or report.get("lock_digest") != plan["lock_digest"]:
                raise ValueError("worker result failed contract verification")
            if report.get("runtime_digest") != plan["runtime_digest"] or report.get("operation") != operation:
                raise ValueError("worker result failed runtime/operation verification")
            if operation == "reconstruct":
                from autocontext.weight_evolution.patch_worker import verify_checkpoint

                verified = verify_checkpoint(load_reproduction_lock(lock_path), asset_root, output / "checkpoint")
                if verified.variant != variant:
                    raise ValueError("reconstructed variant differs from requested variant")
                if canonical_digest(report.get("checkpoint")) != verified.digest:
                    raise ValueError("worker checkpoint report differs from independently verified evidence")
            else:
                if report.get("request_digest") != plan["request_digest"]:
                    raise ValueError("worker inference request identity mismatch")
                from autocontext.weight_evolution.inference_worker import sampling_parameters
                from autocontext.weight_evolution.patch_worker import verify_checkpoint

                assert checkpoint is not None and request_path is not None
                lock = load_reproduction_lock(lock_path)
                request = load_inference_request(request_path)
                verified = verify_checkpoint(lock, asset_root, checkpoint, expected_variant=variant)
                result = InferenceResult.model_validate_json(json.dumps(report.get("inference"), allow_nan=False))
                if (
                    result.execution_kind != "vllm" or result.lock_digest != lock.digest
                    or result.runtime_digest != plan["runtime_digest"] or result.runtime_image != plan["image"]
                    or result.request_digest != plan["request_digest"] or result.request != request
                    or result.checkpoint_receipt_digest != verified.digest
                    or result.checkpoint_sha256 != verified.checkpoint_sha256
                    or result.tensor_content_digest != verified.tensor_content_digest
                    or result.sampling != sampling_parameters(lock, request)
                    or result.max_new_tokens != result.sampling["max_tokens"]
                    or result.samples_per_problem != result.sampling["n"]
                ):
                    raise ValueError("inference evidence differs from verified execution inputs")
            receipt = {**plan, "ok": True, "image_identity": image_identity,
                       "harness_digest": harness_digest, "elapsed_seconds": time.monotonic() - started,
                       "report_sha256": hashlib.sha256((output / "report.json").read_bytes()).hexdigest(),
                       "model_execution_performed": operation == "infer"}
            write_json(output / "execution.receipt.json", receipt)
            # Reserve the destination without overwriting a racing directory.
            # The completion receipt is published last; interrupted publication
            # is visibly incomplete and never accepted as a successful run.
            destination.mkdir()
            for artifact in output.iterdir():
                if artifact.name != "execution.receipt.json":
                    os.rename(artifact, destination / artifact.name)
            os.rename(output / "execution.receipt.json", destination / "execution.receipt.json")
            return receipt
