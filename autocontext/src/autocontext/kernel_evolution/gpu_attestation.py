"""Trusted host-plane GPU partition attestation for Docker kernel workers."""

from __future__ import annotations

import ctypes
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from autocontext.execution.docker_isolation import sanitized_docker_environment
from autocontext.kernel_evolution.models import content_digest

GPUIsolationKind = Literal["mig", "hardware-partition", "visibility-only"]
_NVML_SUCCESS = 0
_NVML_ERROR_NOT_FOUND = 6
_NVML_UUID_BUFFER_SIZE = 128


@dataclass(frozen=True, slots=True)
class DockerGPUDeviceGrant:
    """An explicit GPU request whose claims must be independently attested."""

    device_id: str
    isolation_kind: GPUIsolationKind
    enforced_memory_bytes: int | None = None

    def __post_init__(self) -> None:
        if (
            not self.device_id.strip()
            or self.device_id.strip().casefold() == "all"
            or re.fullmatch(r"[A-Za-z0-9_.:/-]+", self.device_id) is None
        ):
            raise ValueError("GPU device grant must name one explicit device or partition")
        if any(char in self.device_id for char in "\r\n\0"):
            raise ValueError("GPU device grant contains forbidden control characters")
        if self.isolation_kind not in {"mig", "hardware-partition", "visibility-only"}:
            raise ValueError(f"unknown GPU isolation kind: {self.isolation_kind}")
        if self.isolation_kind == "mig" and not self.device_id.startswith("MIG-"):
            raise ValueError("MIG grants must use an explicit MIG UUID, never a GPU index or parent UUID")
        if self.isolation_kind in {"mig", "hardware-partition"}:
            if self.enforced_memory_bytes is None or self.enforced_memory_bytes < 1:
                raise ValueError("partitioned GPU grants require an expected enforced_memory_bytes capacity")
        elif self.enforced_memory_bytes is not None:
            raise ValueError("visibility-only GPU grants cannot claim a memory enforcement boundary")


@dataclass(frozen=True, slots=True)
class DockerGPUDeviceAttestation:
    """Host-verified identity and hard capacity for one GPU partition."""

    device_id: str
    isolation_kind: Literal["mig", "hardware-partition"]
    enforced_memory_bytes: int
    attestor_id: str

    def __post_init__(self) -> None:
        if (
            not self.device_id.strip()
            or self.device_id.strip().casefold() == "all"
            or re.fullmatch(r"[A-Za-z0-9_.:/-]+", self.device_id) is None
        ):
            raise ValueError("GPU attestation must name one explicit device or partition")
        if self.isolation_kind == "mig" and not self.device_id.startswith("MIG-"):
            raise ValueError("MIG attestations must bind an explicit MIG UUID")
        if self.enforced_memory_bytes < 1:
            raise ValueError("GPU attestation capacity must be positive")
        if not self.attestor_id.strip() or any(char in self.attestor_id for char in "\r\n\0"):
            raise ValueError("GPU attestor_id must be a non-empty single-line value")

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return content_digest(payload)


class DockerGPUDeviceAttestor(Protocol):
    """Trusted host-plane verifier for a requested GPU grant."""

    @property
    def attestor_id(self) -> str: ...

    def manifest(self) -> dict[str, Any]: ...

    def attest(self, grant: DockerGPUDeviceGrant) -> DockerGPUDeviceAttestation: ...


class _NvmlMemory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


def _nvml_check(result: int, operation: str) -> None:
    if result != _NVML_SUCCESS:
        raise RuntimeError(f"NVML {operation} failed with status {result}")


def _nvml_mig_capacity(device_id: str, nvml_library: str) -> int:
    """Return bytes from an exact MIG handle using NVIDIA's stable NVML ABI."""

    try:
        library = ctypes.CDLL(nvml_library)
    except OSError as exc:
        raise RuntimeError(f"NVML library is unavailable: {nvml_library}") from exc
    library.nvmlInit_v2.restype = ctypes.c_int
    library.nvmlShutdown.restype = ctypes.c_int
    library.nvmlDeviceGetCount_v2.argtypes = [ctypes.POINTER(ctypes.c_uint)]
    library.nvmlDeviceGetCount_v2.restype = ctypes.c_int
    library.nvmlDeviceGetHandleByIndex_v2.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
    library.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
    library.nvmlDeviceGetMaxMigDeviceCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    library.nvmlDeviceGetMaxMigDeviceCount.restype = ctypes.c_int
    library.nvmlDeviceGetMigDeviceHandleByIndex.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
    library.nvmlDeviceGetMigDeviceHandleByIndex.restype = ctypes.c_int
    library.nvmlDeviceIsMigDeviceHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    library.nvmlDeviceIsMigDeviceHandle.restype = ctypes.c_int
    library.nvmlDeviceGetUUID.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
    library.nvmlDeviceGetUUID.restype = ctypes.c_int
    library.nvmlDeviceGetMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_NvmlMemory)]
    library.nvmlDeviceGetMemoryInfo.restype = ctypes.c_int

    _nvml_check(int(library.nvmlInit_v2()), "initialization")
    try:
        count = ctypes.c_uint()
        _nvml_check(int(library.nvmlDeviceGetCount_v2(ctypes.byref(count))), "device enumeration")
        matches: list[int] = []
        for gpu_index in range(count.value):
            parent = ctypes.c_void_p()
            _nvml_check(
                int(library.nvmlDeviceGetHandleByIndex_v2(gpu_index, ctypes.byref(parent))),
                "parent handle lookup",
            )
            max_mig_count = ctypes.c_uint()
            result = int(library.nvmlDeviceGetMaxMigDeviceCount(parent, ctypes.byref(max_mig_count)))
            if result not in {_NVML_SUCCESS, 3}:  # NVML_ERROR_NOT_SUPPORTED
                _nvml_check(result, "MIG capacity lookup")
            if result != _NVML_SUCCESS:
                continue
            for mig_index in range(max_mig_count.value):
                mig = ctypes.c_void_p()
                result = int(library.nvmlDeviceGetMigDeviceHandleByIndex(parent, mig_index, ctypes.byref(mig)))
                if result == _NVML_ERROR_NOT_FOUND:
                    continue
                _nvml_check(result, "MIG handle lookup")
                is_mig = ctypes.c_uint()
                _nvml_check(int(library.nvmlDeviceIsMigDeviceHandle(mig, ctypes.byref(is_mig))), "MIG handle verification")
                if is_mig.value != 1:
                    raise RuntimeError("NVML returned a non-MIG handle during MIG enumeration")
                uuid_buffer = ctypes.create_string_buffer(_NVML_UUID_BUFFER_SIZE)
                _nvml_check(
                    int(library.nvmlDeviceGetUUID(mig, uuid_buffer, _NVML_UUID_BUFFER_SIZE)),
                    "MIG UUID lookup",
                )
                if uuid_buffer.value.decode("ascii", errors="strict") != device_id:
                    continue
                memory = _NvmlMemory()
                _nvml_check(int(library.nvmlDeviceGetMemoryInfo(mig, ctypes.byref(memory))), "MIG memory lookup")
                matches.append(int(memory.total))
        if len(matches) != 1 or matches[0] < 1:
            raise RuntimeError("NVML did not resolve exactly one active MIG UUID with positive capacity")
        return matches[0]
    finally:
        _nvml_check(int(library.nvmlShutdown()), "shutdown")


class NvidiaSMIGPUDeviceAttestor:
    """Cross-check MIG topology with nvidia-smi and capacity with a MIG NVML handle."""

    attestor_id = "nvidia-smi-nvml-mig-v1"

    def __init__(
        self,
        nvidia_smi_binary: str = "nvidia-smi",
        *,
        timeout_seconds: float = 10.0,
        nvml_library: str = "libnvidia-ml.so.1",
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("nvidia-smi timeout_seconds must be positive and finite")
        resolved = shutil.which(nvidia_smi_binary)
        if resolved is None:
            raise RuntimeError(f"nvidia-smi executable is unavailable: {nvidia_smi_binary}")
        if not nvml_library.strip() or any(char in nvml_library for char in "\r\n\0"):
            raise ValueError("nvml_library must be a non-empty single-line library name or path")
        self.nvidia_smi_binary = resolved
        self.timeout_seconds = timeout_seconds
        self.nvml_library = nvml_library

    def manifest(self) -> dict[str, Any]:
        return {
            "attestor_id": self.attestor_id,
            "nvidia_smi_binary": self.nvidia_smi_binary,
            "nvml_library": self.nvml_library,
            "timeout_seconds": self.timeout_seconds,
        }

    def attest(self, grant: DockerGPUDeviceGrant) -> DockerGPUDeviceAttestation:
        if grant.isolation_kind != "mig" or not grant.device_id.startswith("MIG-"):
            raise RuntimeError("NVIDIA attestation currently supports only explicit MIG UUID grants")
        listed = self._run([self.nvidia_smi_binary, "-L"])
        uuid_marker = f"(UUID: {grant.device_id})"
        if not any(line.lstrip().startswith("MIG ") and uuid_marker in line for line in listed.stdout.splitlines()):
            raise RuntimeError("requested MIG UUID is absent from the host's active MIG topology")
        capacity = self._run_nvml_capacity(grant.device_id)
        return DockerGPUDeviceAttestation(
            device_id=grant.device_id,
            isolation_kind="mig",
            enforced_memory_bytes=capacity,
            attestor_id=self.attestor_id,
        )

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(  # noqa: S603
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=sanitized_docker_environment(),
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-240:]
            raise RuntimeError(f"nvidia-smi GPU attestation failed: {detail or 'unknown error'}")
        return completed

    def _run_nvml_capacity(self, device_id: str) -> int:
        """Resolve NVML in a killable helper so a wedged call cannot block the coordinator."""
        completed = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--nvml-mig-capacity",
                device_id,
                self.nvml_library,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=sanitized_docker_environment(),
        )
        value = completed.stdout.strip()
        if completed.returncode != 0 or not value.isascii() or not value.isdecimal() or int(value) < 1:
            detail = (completed.stderr or completed.stdout).strip()[-240:]
            raise RuntimeError(f"NVML MIG capacity helper failed: {detail or 'unknown error'}")
        return int(value)


def _entrypoint(argv: list[str]) -> int:
    if len(argv) != 4 or argv[1] != "--nvml-mig-capacity":
        return 2
    try:
        capacity = _nvml_mig_capacity(argv[2], argv[3])
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(capacity)
    return 0


__all__ = [
    "DockerGPUDeviceAttestation",
    "DockerGPUDeviceAttestor",
    "DockerGPUDeviceGrant",
    "GPUIsolationKind",
    "NvidiaSMIGPUDeviceAttestor",
]


if __name__ == "__main__":  # pragma: no cover - exercised as a bounded attestation helper
    raise SystemExit(_entrypoint(sys.argv))
