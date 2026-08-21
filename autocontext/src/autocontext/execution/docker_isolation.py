"""Shared fail-closed Docker command construction for hostile workloads."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DockerIsolationLimits:
    """Kernel-enforced limits shared by research and GPU workers."""

    memory_mb: int
    cpu_count: float
    pids_limit: int
    cpu_time_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.memory_mb < 64:
            raise ValueError("Docker isolation memory_mb must be at least 64")
        if not math.isfinite(self.cpu_count) or self.cpu_count <= 0:
            raise ValueError("Docker isolation cpu_count must be positive and finite")
        if self.pids_limit < 1:
            raise ValueError("Docker isolation pids_limit must be positive")
        if self.cpu_time_seconds is not None and self.cpu_time_seconds < 1:
            raise ValueError("Docker isolation cpu_time_seconds must be positive")


def sanitized_docker_environment() -> dict[str, str]:
    """Return only daemon-routing values; candidate credentials never cross."""

    return {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG") if key in os.environ}


def build_docker_isolation_command(
    *,
    docker_binary: str,
    image: str,
    container_name: str,
    labels: Mapping[str, str],
    limits: DockerIsolationLimits,
    readonly_mounts: Mapping[Path, str],
    writable_mounts: Mapping[Path, str],
    tmpfs_mounts: Mapping[str, str],
    argv: Sequence[str],
    gpu_device: str | None = None,
    auto_remove: bool = True,
    working_dir: str | None = None,
    ulimits: Mapping[str, tuple[int, int]] | None = None,
    environment: Mapping[str, str] | None = None,
) -> list[str]:
    """Build one shell-free, deny-network, least-privilege Docker invocation."""

    if not argv:
        raise ValueError("Docker isolation argv must not be empty")
    if gpu_device is not None:
        if (
            not gpu_device.strip()
            or gpu_device.strip().casefold() == "all"
            or re.fullmatch(r"[A-Za-z0-9_.:/-]+", gpu_device) is None
        ):
            raise ValueError("GPU isolation requires one explicit device id, never 'all'")
        if any(char in gpu_device for char in "\r\n\0"):
            raise ValueError("GPU device id contains forbidden control characters")
    command = [docker_binary, "run", "--pull", "never"]
    if auto_remove:
        command.append("--rm")
    command.extend(("--name", container_name))
    for name, value in sorted(labels.items()):
        if not name.strip() or any(char in f"{name}{value}" for char in "\r\n\0"):
            raise ValueError("Docker isolation labels must be non-empty single-line values")
        command.extend(("--label", f"{name}={value}"))
    command.extend(
        (
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(limits.pids_limit),
            "--memory",
            f"{limits.memory_mb}m",
            "--memory-swap",
            f"{limits.memory_mb}m",
            "--cpus",
            str(limits.cpu_count),
        )
    )
    if limits.cpu_time_seconds is not None:
        command.extend(("--ulimit", f"cpu={limits.cpu_time_seconds}:{limits.cpu_time_seconds}"))
    for name, (soft, hard) in sorted((ulimits or {}).items()):
        if not name.isascii() or not name.replace("_", "").isalnum() or min(soft, hard) < 1 or soft > hard:
            raise ValueError("Docker ulimits require safe names and positive soft/hard values")
        command.extend(("--ulimit", f"{name}={soft}:{hard}"))
    for target, options in sorted(tmpfs_mounts.items()):
        if not target.startswith("/") or "\n" in options:
            raise ValueError("Docker tmpfs targets must be absolute and options single-line")
        command.extend(("--tmpfs", f"{target}:{options}"))
    for source, target in sorted(readonly_mounts.items(), key=lambda item: item[1]):
        command.extend(("--mount", f"type=bind,src={source},dst={target},readonly"))
    for source, target in sorted(writable_mounts.items(), key=lambda item: item[1]):
        command.extend(("--mount", f"type=bind,src={source},dst={target}"))
    if gpu_device is not None:
        command.extend(("--gpus", f"device={gpu_device}"))
    if working_dir is not None:
        if not working_dir.startswith("/") or any(char in working_dir for char in "\r\n\0"):
            raise ValueError("Docker working_dir must be an absolute single-line path")
        command.extend(("--workdir", working_dir))
    command.extend(("--env", "LANG=C.UTF-8", "--env", "HOME=/tmp", "--user", f"{os.getuid()}:{os.getgid()}"))
    clean_environment = {
        "LANG": "C.UTF-8",
        "HOME": "/tmp",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    for name, value in (environment or {}).items():
        if (
            name in clean_environment
            or not name.isascii()
            or not name.replace("_", "").isalnum()
            or any(char in value for char in "\r\n\0")
        ):
            raise ValueError("Docker isolated environment contains an unsafe or reserved key/value")
        clean_environment[name] = value
    command.extend((image, "env", "-i", *(f"{name}={value}" for name, value in clean_environment.items()), *argv))
    return command


__all__ = ["DockerIsolationLimits", "build_docker_isolation_command", "sanitized_docker_environment"]
