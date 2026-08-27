"""Protected evaluator composition for the H100 conformance campaign.

The accelerator-neutral boundary is constructible for manifest inspection, but
execution deliberately remains unavailable. ``require_protected_evaluator_boundary``
keeps production disabled until role isolation, trusted mutation observation,
comparable timing boundaries, and crash-safe container creation are implemented
and validated on H100/MIG.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
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
    canonical_authority_digest,
    protected_evaluator_boundary_requirements,
    read_authority_hmac_secret,
)

_SAFE_AUTHORITY_HMAC_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


class ProductionEvaluatorBoundaryUnavailable(RuntimeError):
    """Raised rather than producing evidence controlled by candidate code."""


def require_protected_evaluator_boundary() -> NoReturn:
    """Fail before production GPU work while required authority boundaries are absent."""
    raise ProductionEvaluatorBoundaryUnavailable(
        f"production H100 campaigns remain disabled for {PROTECTED_EVALUATOR_BOUNDARY}: "
        "independently attested evaluator/candidate/incumbent grants, trusted out-of-process input-mutation "
        "observation, comparable candidate/incumbent/reference timing boundaries, and crash-safe container "
        "creation are not yet implemented. "
        "A hardware run alone cannot remove this release guard."
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
    authority_hmac_key_id: str
    authority_hmac_secret_path: Path = field(repr=False)
    limits: DockerKernelWorkerLimits
    timeout_seconds: float = 240.0

    def __post_init__(self) -> None:
        require_pinned_runtime_image(self.image)
        for name in ("image", "docker_binary", "nvidia_smi_binary", "container_python", "gpu_device"):
            value = getattr(self, name)
            if not value.strip() or any(character in value for character in "\r\n\0"):
                raise ValueError(f"{name} must be a non-empty single-line value")
        if _SAFE_AUTHORITY_HMAC_KEY_ID.fullmatch(self.authority_hmac_key_id) is None:
            raise ValueError("authority_hmac_key_id must be a safe non-empty identifier")
        read_authority_hmac_secret(self.authority_hmac_secret_path)
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
        requirements = protected_evaluator_boundary_requirements()
        return {
            "image": self.image,
            "docker_binary": self.docker_binary,
            "nvidia_smi_binary": self.nvidia_smi_binary,
            "container_python": self.container_python,
            "gpu_device": self.gpu_device,
            "gpu_isolation_kind": self.gpu_isolation_kind,
            "gpu_memory_bytes": self.gpu_memory_bytes,
            "authority_authentication": {
                "algorithm": "hmac-sha256",
                "key_id": self.authority_hmac_key_id,
            },
            "timeout_seconds": self.timeout_seconds,
            "limits": asdict(self.limits),
            "evidence_boundary": {
                "required": PROTECTED_EVALUATOR_BOUNDARY,
                "available": all(requirement.get("available") is True for requirement in requirements.values()),
                "requirements": requirements,
                "unmet_requirements": [
                    str(requirement["reason"])
                    for requirement in requirements.values()
                    if requirement.get("available") is not True
                ],
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
    """Compose the protected path for inspection while execution stays fail-closed.

    The normal campaign still calls :func:`require_protected_evaluator_boundary`
    before this factory. The runner itself also returns
    ``resource_policy_unsupported`` before Docker until every authority boundary
    represented in its manifest is available.
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
        authority_hmac_key_id=runtime.authority_hmac_key_id,
        authority_hmac_secret_path=runtime.authority_hmac_secret_path,
        limits=runtime.limits,
        docker_binary=runtime.docker_binary,
    )
    runner_manifest = runner.manifest()
    evaluator_build_digest = runner_manifest.get("evaluator_build_digest")
    if not isinstance(evaluator_build_digest, str):
        raise ValueError("protected runner manifest omitted its evaluator build digest")
    boundary_manifest_digest = canonical_authority_digest(runner_manifest)
    return KernelBenchmarkEvaluator(
        runner,
        KernelBenchmarkEvaluatorConfig(
            problem_id=problem_id,
            timeout_seconds=runtime.timeout_seconds,
            min_timing_blocks=8,
            bootstrap_samples=None,
            statistics_method="paired-sign-eprocess/v1",
            finite_sample_improvement_margin=0.05,
            require_resource_telemetry=True,
            require_authority_receipt=True,
            authority_hmac_key_id=runtime.authority_hmac_key_id,
            authority_hmac_secret_path=runtime.authority_hmac_secret_path,
            expected_evaluator_build_digest=evaluator_build_digest,
            expected_boundary_manifest_digest=boundary_manifest_digest,
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
