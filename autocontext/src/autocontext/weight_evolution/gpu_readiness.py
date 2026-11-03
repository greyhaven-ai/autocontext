"""Read-only prerequisites for local NVIDIA replay, not inference acceptance.

Queries use the runner's Docker routing environment and bounded process/output
handling. No image is pulled and no container or model is started here.
"""

from __future__ import annotations

import csv
import io
import json
import math
import platform
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.execution.docker_isolation import DockerIsolationLimits, sanitized_docker_environment
from autocontext.kernel_evolution import _process_control
from autocontext.kernel_evolution.docker_watchdog import launch_bounded_output_drains
from autocontext.weight_evolution.models import _unique_object
from autocontext.weight_evolution.runtime import RuntimeLock

MAX_PROBE_OUTPUT_BYTES = 256 * 1024
_GPU_UUID = r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


@dataclass(frozen=True)
class _CommandResult:
    returncode: int | None
    stdout: str = ""
    error: str | None = None


def _run_command(argv: list[str], timeout_seconds: float) -> _CommandResult:
    """Bound both pipes and terminate the probe's process group on every exit."""
    try:
        process = subprocess.Popen(  # noqa: S603
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env=sanitized_docker_environment(),
        )
    except OSError as exc:
        return _CommandResult(None, error=f"probe executable unavailable: {type(exc).__name__}")
    stdout, stderr = _process_control.BoundedOutput(), _process_control.BoundedOutput()
    quota = threading.Event()
    controller = _process_control.ProcessTreeController(process, None)
    drains = launch_bounded_output_drains(
        process, max_output_bytes=MAX_PROBE_OUTPUT_BYTES, stdout=stdout, stderr=stderr,
        quota_exceeded=quota, terminate=controller.terminate,
    )
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        controller.terminate()
        controller.wait_for_parent()
        for thread in drains:
            thread.join(timeout=2)
    if timed_out:
        return _CommandResult(process.returncode, error="probe timed out")
    if quota.is_set():
        return _CommandResult(process.returncode, error="probe exceeded output limit")
    if controller.errors or stdout.read_failed or stderr.read_failed or any(t.is_alive() for t in drains):
        return _CommandResult(process.returncode, error="probe output or process cleanup failed")
    if process.returncode:
        return _CommandResult(process.returncode, error=f"probe failed (exit {process.returncode}): {stderr.text[-240:]}")
    return _CommandResult(process.returncode, stdout=stdout.text)


def _object(text: str) -> dict[str, Any]:
    result = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(result, dict):
        raise ValueError("expected a JSON object")
    return result


def _single_object(text: str) -> dict[str, Any]:
    result = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict):
        raise ValueError("expected exactly one JSON object")
    return result[0]


def _gpu_row(text: str, device: str) -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(text.strip()), skipinitialspace=True))
    if len(rows) != 1 or len(rows[0]) != 8:
        raise ValueError("selected device did not resolve to exactly one physical GPU")
    index, uuid, name, driver, total, free, capability, mig_mode = (part.strip() for part in rows[0])
    if not index.isascii() or not index.isdigit() or re.fullmatch(_GPU_UUID, uuid) is None:
        raise ValueError("GPU index or UUID is invalid")
    if (device.isdigit() and int(device) != int(index)) or (not device.isdigit() and device.lower() != uuid.lower()):
        raise ValueError("NVIDIA device identity differs from selected device")
    if not name or not re.fullmatch(r"\d+(?:\.\d+)+", driver):
        raise ValueError("GPU name or driver version is missing")
    if not total.isdigit() or not free.isdigit() or not 0 < int(total) or int(free) > int(total):
        raise ValueError("GPU memory observations are invalid")
    if re.fullmatch(r"\d+\.\d+", capability) is None:
        raise ValueError("GPU compute capability is unavailable")
    if mig_mode not in {"Disabled", "[N/A]", "N/A"}:
        raise ValueError("MIG-enabled or unknown MIG mode is unsupported; select a physical GPU with MIG disabled")
    return {"index": int(index), "uuid": f"GPU-{uuid[4:].lower()}", "name": name, "driver_version": driver,
            "memory_total_mib": int(total), "memory_free_mib": int(free), "compute_capability": capability,
            "mig_mode": mig_mode}


def probe_gpu_readiness(
    *, runtime: RuntimeLock, gpu_device: str, output_parent: Path, required_free_bytes: int,
    memory_mb: int = 16384, cpu_count: float = 4.0, docker_binary: str = "docker", timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Return machine-readable prerequisites; success still requires live smoke.

The controller and Docker daemon must share a local Linux/amd64 filesystem.
SSH/TCP Docker endpoints cannot substantiate local bind mounts or disk/GPU checks.
    Physical GPU index or full UUID is required; MIG selection is not supported by
    this probe. Memory and free disk are observations, never exclusive reservations.
    For a planned output directory, inspect its nearest existing ancestor without
    creating directories. Resolve symlinks only for this read-only disk query;
    the execution workflows separately reject symlink output paths.
"""
    DockerIsolationLimits(memory_mb=memory_mb, cpu_count=cpu_count, pids_limit=512)
    if type(required_free_bytes) is not int or required_free_bytes < 1:
        raise ValueError("required_free_bytes must be a positive integer")
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
        raise ValueError("probe timeout must be positive and at most 30 seconds")
    if re.fullmatch(rf"(?:[0-9]+|{_GPU_UUID})", gpu_device) is None:
        raise ValueError("readiness requires one physical GPU index or full GPU UUID")
    requested_parent = output_parent.resolve(strict=False)
    parent = requested_parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError("output_parent or its nearest existing ancestor must be a directory")
    blockers: list[dict[str, str]] = []
    observations: dict[str, Any] = {
        "coordinator": {"system": platform.system(), "machine": platform.machine()},
        "disk": {"path": str(parent), "requested_path": str(requested_parent),
                 "required_free_bytes": required_free_bytes},
    }

    def block(code: str, message: str) -> None:
        blockers.append({"code": code, "message": message})

    def query(code: str, argv: list[str]) -> str | None:
        result = _run_command(argv, timeout_seconds)
        if result.error is not None or result.returncode != 0:
            block(code, result.error or "probe failed")
            return None
        return result.stdout

    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        block("coordinator_platform", "Run on the Linux/amd64 NVIDIA host that owns the local Docker daemon and files.")
    try:
        free_bytes = shutil.disk_usage(parent).free
        observations["disk"]["free_bytes"] = free_bytes
        if free_bytes < required_free_bytes:
            block("insufficient_disk", "Output filesystem has less free space than the frozen run requires.")
    except OSError as exc:
        block("disk_unavailable", f"Cannot inspect output filesystem: {type(exc).__name__}")

    docker = shutil.which(docker_binary)
    endpoint: str | None = None
    if docker is None:
        block("docker_unavailable", "Docker executable is unavailable.")
    else:
        endpoint = sanitized_docker_environment().get("DOCKER_HOST")
        if endpoint is None:
            raw = query("docker_context_unavailable", [docker, "context", "inspect"])
            if raw is not None:
                try:
                    context = _single_object(raw)
                    endpoint = context["Endpoints"]["docker"]["Host"]
                    if not isinstance(endpoint, str):
                        raise ValueError("Docker endpoint is not a string")
                except (ValueError, KeyError, TypeError):
                    block("docker_context_invalid", "Docker context did not identify one daemon endpoint.")
        local_endpoint = isinstance(endpoint, str) and endpoint.startswith("unix:///")
        observations["docker_route"] = {"local_unix_endpoint": local_endpoint}
        if endpoint is not None and not local_endpoint:
            block("remote_docker_unsupported",
                  "Use a local Unix Docker endpoint on the GPU host; remote bind mounts are unverified.")
        if local_endpoint:
            raw = query("docker_daemon_unavailable", [docker, "info", "--format", "{{json .}}"])
            if raw is not None:
                try:
                    info = _object(raw)
                    if not isinstance(info.get("ID"), str) or not info["ID"]:
                        raise ValueError("missing daemon ID")
                    runtimes = info.get("Runtimes", {})
                    observations["daemon"] = {"id": info["ID"], "os": info.get("OSType"),
                                              "architecture": info.get("Architecture"),
                                              "version": info.get("ServerVersion"),
                                              "runtimes": sorted(runtimes) if isinstance(runtimes, dict) else []}
                    if info.get("OSType") != "linux" or info.get("Architecture") not in {"x86_64", "amd64"}:
                        block("daemon_platform", "Docker daemon must run Linux/amd64 without emulation.")
                    for field in ("MemTotal", "NCPU"):
                        if type(info.get(field)) is not int or info[field] < 1:
                            raise ValueError("missing daemon resource observations")
                    observations["daemon"].update(memory_bytes=info["MemTotal"], cpu_count=info["NCPU"])
                    if info["MemTotal"] < memory_mb * 1024 * 1024:
                        block("insufficient_host_memory", "Daemon memory is below the worker's frozen RAM limit.")
                    if info["NCPU"] < cpu_count:
                        block("insufficient_host_cpu", "Daemon CPU count is below the worker's frozen CPU limit.")
                except (ValueError, TypeError):
                    block("docker_daemon_invalid", "Docker daemon metadata is malformed or incomplete.")
            raw = query("pinned_image_unavailable", [docker, "image", "inspect", runtime.image])
            if raw is not None:
                try:
                    image = _single_object(raw)
                    digests = image.get("RepoDigests", [])
                    expected = runtime.image.rsplit("@", 1)[1]
                    if not isinstance(digests, list) or not all(isinstance(item, str) for item in digests):
                        raise ValueError("invalid image digests")
                    if (image.get("Os"), image.get("Architecture")) != ("linux", "amd64") or not any(
                        item.rsplit("@", 1)[-1] == expected for item in digests
                    ) or not isinstance(image.get("Id"), str) or not image["Id"]:
                        raise ValueError("image identity mismatch")
                    config = image.get("Config") or {}
                    environment = config.get("Env", [])
                    observations["image"] = {"id": image["Id"], "repo_digests": sorted(digests),
                                             "platform": "linux/amd64", "cuda_metadata": [
                                                 value for value in environment if isinstance(value, str)
                                                 and value.startswith(("CUDA_VERSION=", "NVIDIA_REQUIRE_CUDA="))
                                             ]}
                except (ValueError, TypeError, AttributeError):
                    block("pinned_image_mismatch", "Installed image does not match the locked digest/platform or metadata.")

    nvidia = shutil.which("nvidia-smi")
    if nvidia is None:
        block("nvidia_smi_unavailable", "Host NVIDIA driver query executable is unavailable.")
    else:
        raw = query("nvidia_device_unavailable", [
            nvidia, f"--id={gpu_device}",
            "--query-gpu=index,uuid,name,driver_version,memory.total,memory.free,compute_cap,mig.mode.current",
            "--format=csv,noheader,nounits",
        ])
        if raw is not None:
            try:
                observations["selected_gpu"] = _gpu_row(raw, gpu_device)
            except ValueError as exc:
                block("nvidia_device_invalid", str(exc))

    return {
        "schema_version": 1, "ready_for_acceptance": not blockers, "blockers": blockers,
        "runtime_digest": runtime.digest, "gpu_device": gpu_device, "observations": observations,
        "model_execution_performed": False, "container_execution_performed": False,
        "compatibility": "requires_live_acceptance", "gpu_memory_isolation": "not_attested",
        "notes": [
            "Read-only prerequisites cannot prove toolkit, driver, CUDA, GPU architecture or model-loader compatibility.",
            "A successful pinned-image reconstruction and paired CUDA smoke run are still required.",
            "Disk, host RAM and GPU memory observations are not reservations; no exclusive GPU-memory ownership is attested.",
        ],
    }
