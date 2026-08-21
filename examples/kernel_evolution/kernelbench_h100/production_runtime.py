"""Fail-closed production composition for the H100 kernel campaign.

The Docker worker protects the host from generated source.  It does not, by
itself, protect an authoritative Python evaluator that imports that source in
the same interpreter.  ``require_protected_evaluator_boundary`` therefore
keeps the production campaign disabled until the adapter is split across a
trusted evaluator and an isolated GPU candidate executor.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, NoReturn

from autocontext.execution.scenario_remote_package import require_pinned_runtime_image
from autocontext.kernel_evolution import DockerGPUDeviceGrant, DockerKernelWorkerLimits

PROTECTED_EVALUATOR_BOUNDARY = "trusted-evaluator/isolated-gpu-candidate-v1"


class ProductionEvaluatorBoundaryUnavailable(RuntimeError):
    """Raised rather than producing evidence controlled by candidate code."""


def require_protected_evaluator_boundary() -> NoReturn:
    """Fail before GPU work while the adapter and candidate share authority."""
    raise ProductionEvaluatorBoundaryUnavailable(
        "production H100 campaigns are disabled: adapter.py imports generated candidate code into the same "
        "interpreter that owns private plans, correctness, CUDA timing, telemetry, and report.json. A production "
        f"campaign requires {PROTECTED_EVALUATOR_BOUNDARY}: an evaluator-owned protocol that keeps private plan "
        "material and authoritative measurement/report controls outside the candidate process. Docker isolation "
        "protects the host but cannot establish that evidence boundary."
    )


@dataclass(frozen=True, slots=True)
class H100DockerRuntimeConfig:
    """Explicit, serializable inputs for one production Docker worker."""

    image: str
    docker_binary: str
    nvidia_smi_binary: str
    container_python: str
    gpu_device: str
    gpu_isolation_kind: Literal["mig"]
    gpu_memory_bytes: int
    limits: DockerKernelWorkerLimits
    timeout_seconds: float = 240.0

    def __post_init__(self) -> None:
        require_pinned_runtime_image(self.image)
        for name in ("image", "docker_binary", "nvidia_smi_binary", "container_python", "gpu_device"):
            value = getattr(self, name)
            if not value.strip() or any(character in value for character in "\r\n\0"):
                raise ValueError(f"{name} must be a non-empty single-line value")
        if self.gpu_memory_bytes < 1:
            raise ValueError("gpu_memory_bytes must be positive")
        DockerGPUDeviceGrant(
            device_id=self.gpu_device,
            isolation_kind=self.gpu_isolation_kind,
            enforced_memory_bytes=self.gpu_memory_bytes,
        )
        container_python = PurePosixPath(self.container_python)
        if not container_python.is_absolute() or ".." in container_python.parts:
            raise ValueError("container_python must be an absolute normalized path inside the pinned image")
        if self.gpu_memory_bytes > self.limits.max_gpu_memory_bytes:
            raise ValueError("attested GPU capacity cannot exceed max_gpu_memory_bytes")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")

    def manifest(self) -> dict[str, object]:
        """Return non-secret deployment inputs captured alongside a run."""
        return {
            "image": self.image,
            "docker_binary": self.docker_binary,
            "nvidia_smi_binary": self.nvidia_smi_binary,
            "container_python": self.container_python,
            "gpu_device": self.gpu_device,
            "gpu_isolation_kind": self.gpu_isolation_kind,
            "gpu_memory_bytes": self.gpu_memory_bytes,
            "timeout_seconds": self.timeout_seconds,
            "limits": asdict(self.limits),
            "evidence_boundary": {
                "required": PROTECTED_EVALUATOR_BOUNDARY,
                "available": False,
            },
        }


def _compose_docker_evaluator(
    *,
    runtime: H100DockerRuntimeConfig,
    bundle: Path,
    adapter_name: str,
    autokernel_root: Path,
    private_plan: Path,
    problem_id: str,
    precision_profile: str,
    plan_commitment: str,
    proposal_cap: int,
    familywise_alpha: float,
) -> NoReturn:
    """Refuse runnable composition until the trusted evaluator exists.

    Keeping this guard at the construction boundary prevents library callers
    from bypassing the campaign CLI's preflight and obtaining a runnable
    same-interpreter evaluator.
    """
    del (
        runtime,
        bundle,
        adapter_name,
        autokernel_root,
        private_plan,
        problem_id,
        precision_profile,
        plan_commitment,
        proposal_cap,
        familywise_alpha,
    )
    require_protected_evaluator_boundary()


__all__ = [
    "H100DockerRuntimeConfig",
    "PROTECTED_EVALUATOR_BOUNDARY",
    "ProductionEvaluatorBoundaryUnavailable",
    "require_protected_evaluator_boundary",
]
