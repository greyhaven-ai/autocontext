"""Protected evaluator composition for the H100 conformance campaign.

The accelerator-neutral boundary is implemented and can be composed for a
live validation run.  ``require_protected_evaluator_boundary`` deliberately
keeps the production campaign disabled until that exact Docker/MIG path has
produced and replayed a real H100 authority receipt.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, NoReturn

from autocontext.execution.scenario_remote_package import require_pinned_runtime_image
from autocontext.kernel_evolution import (
    PROTECTED_EVALUATOR_BOUNDARY,
    DockerGPUDeviceGrant,
    DockerKernelWorkerLimits,
    DockerProtectedKernelBenchmarkRunner,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    NvidiaSMIGPUDeviceAttestor,
)


class ProductionEvaluatorBoundaryUnavailable(RuntimeError):
    """Raised rather than producing evidence controlled by candidate code."""


def require_protected_evaluator_boundary() -> NoReturn:
    """Fail before production GPU work while live validation is pending."""
    raise ProductionEvaluatorBoundaryUnavailable(
        "production H100 campaigns remain disabled pending a real H100/MIG validation run for "
        f"{PROTECTED_EVALUATOR_BOUNDARY}. The implemented evaluator-owned protocol keeps private plans and "
        "authoritative correctness, measurement, telemetry, and report controls outside generated-code containers; "
        "the release guard must remain until its live authority receipt is replay-verified."
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
) -> KernelBenchmarkEvaluator:
    """Compose the protected path used for live boundary validation.

    The normal campaign still calls :func:`require_protected_evaluator_boundary`
    before this factory.  Keeping the factory runnable lets operators exercise
    the exact future production path without weakening that release guard.
    """
    bundle = bundle.resolve(strict=True)
    autokernel_root = autokernel_root.resolve(strict=True)
    private_plan = private_plan.resolve(strict=True)
    if not (autokernel_root / "kernel.py").is_file():
        raise ValueError("autokernel_root must contain the pinned kernel.py incumbent")
    if Path(adapter_name).name != adapter_name or not adapter_name.endswith(".py"):
        raise ValueError("adapter_name must be a Python filename within the immutable bundle")
    adapter = bundle / adapter_name
    reference = bundle / "reference.py"
    authority_worker = bundle / "authority_worker.py"
    for path in (adapter, reference, authority_worker, bundle / "authority_transport.py", bundle / "profile_contract.py"):
        if not path.is_file():
            raise ValueError(f"protected evaluator input is missing: {path.name}")

    gpu_grant = DockerGPUDeviceGrant(
        device_id=runtime.gpu_device,
        isolation_kind=runtime.gpu_isolation_kind,
        enforced_memory_bytes=runtime.gpu_memory_bytes,
    )
    gpu_attestor = NvidiaSMIGPUDeviceAttestor(runtime.nvidia_smi_binary)
    runner = DockerProtectedKernelBenchmarkRunner(
        [
            runtime.container_python,
            f"/evaluator/0/{adapter_name}",
            "--candidate-socket",
            "{candidate_socket}",
            "--incumbent-socket",
            "{incumbent_socket}",
            "--artifact-identity-version",
            "{artifact_identity_version}",
            "--candidate-artifact-digest",
            "{candidate_artifact_digest}",
            "--incumbent-artifact-digest",
            "{incumbent_artifact_digest}",
            "--candidate-source-digest",
            "{candidate_source_digest}",
            "--incumbent-source-digest",
            "{incumbent_source_digest}",
            "--candidate-source-suffix",
            "{candidate_source_suffix}",
            "--incumbent-source-suffix",
            "{incumbent_source_suffix}",
            "--candidate-entrypoint",
            "{candidate_entrypoint}",
            "--incumbent-entrypoint",
            "{incumbent_entrypoint}",
            "--reference",
            "/evaluator/0/reference.py",
            "--report",
            "{report}",
            "--problem-id",
            problem_id,
            "--autokernel-root",
            "/evaluator/0",
            "--precision-profile",
            precision_profile,
            "--private-plan",
            "/evaluator/1",
            "--plan-commitment",
            plan_commitment,
            "--proposal-cap",
            str(proposal_cap),
            "--familywise-alpha",
            f"{familywise_alpha:.17g}",
        ],
        image=runtime.image,
        container_python=runtime.container_python,
        evaluator_immutable_paths=(bundle, private_plan),
        evaluator_build_paths=(bundle,),
        candidate_runtime_path=authority_worker,
        # Candidate and incumbent source are staged explicitly.  Do not mount
        # the surrounding AutoKernel checkout (including its .git metadata).
        candidate_support_paths=(),
        gpu_grant=gpu_grant,
        gpu_attestor=gpu_attestor,
        limits=runtime.limits,
        docker_binary=runtime.docker_binary,
    )
    return KernelBenchmarkEvaluator(
        runner,
        KernelBenchmarkEvaluatorConfig(
            problem_id=problem_id,
            timeout_seconds=runtime.timeout_seconds,
            min_timing_blocks=8,
            bootstrap_samples=20_000,
            require_resource_telemetry=True,
            require_authority_receipt=True,
            adaptive_feedback_policy="aggregate-gates",
            max_gpu_memory_bytes=runtime.gpu_memory_bytes,
        ),
    )


__all__ = [
    "H100DockerRuntimeConfig",
    "PROTECTED_EVALUATOR_BOUNDARY",
    "ProductionEvaluatorBoundaryUnavailable",
    "require_protected_evaluator_boundary",
]
