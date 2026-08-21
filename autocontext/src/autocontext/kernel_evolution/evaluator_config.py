"""Configuration contract for the kernel benchmark evaluator."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from autocontext.kernel_evolution.authority_protocol import read_authority_hmac_secret
from autocontext.kernel_evolution.promotion_statistics import minimum_bootstrap_samples
from autocontext.kernel_evolution.protocols import KernelStatisticsPolicy

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")


@dataclass(frozen=True, slots=True)
class KernelBenchmarkEvaluatorConfig:
    problem_id: str
    timeout_seconds: float = 630.0
    min_timing_blocks: int = 5
    bootstrap_samples: int = 2_000
    max_feedback_chars: int = 4_000
    require_resource_telemetry: bool = False
    require_authority_receipt: bool = False
    authority_hmac_key_id: str | None = None
    authority_hmac_secret_path: Path | None = field(default=None, repr=False)
    expected_evaluator_build_digest: str | None = None
    expected_boundary_manifest_digest: str | None = None
    adaptive_feedback_policy: Literal["detailed", "aggregate-gates"] = "detailed"
    max_gpu_memory_bytes: int | None = None

    def __post_init__(self) -> None:
        if not self.problem_id.strip():
            raise ValueError("problem_id must not be empty")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.min_timing_blocks < 2:
            raise ValueError("min_timing_blocks must be at least 2")
        minimum_nominal_samples = minimum_bootstrap_samples(0.05)
        if self.bootstrap_samples < minimum_nominal_samples:
            raise ValueError(f"bootstrap_samples must be at least {minimum_nominal_samples}")
        if self.max_feedback_chars < 128:
            raise ValueError("max_feedback_chars must be at least 128")
        if self.adaptive_feedback_policy not in {"detailed", "aggregate-gates"}:
            raise ValueError("adaptive_feedback_policy must be detailed or aggregate-gates")
        if self.max_gpu_memory_bytes is not None and self.max_gpu_memory_bytes < 1:
            raise ValueError("max_gpu_memory_bytes must be positive")
        authority_values = (
            self.authority_hmac_key_id,
            self.authority_hmac_secret_path,
            self.expected_evaluator_build_digest,
            self.expected_boundary_manifest_digest,
        )
        if self.require_authority_receipt and any(value is None for value in authority_values):
            raise ValueError(
                "required authority receipts need a pinned HMAC key, secret file, evaluator build, and boundary digest"
            )
        if any(value is not None for value in authority_values) and any(value is None for value in authority_values):
            raise ValueError("authority trust configuration must be supplied as one complete set")
        if self.authority_hmac_key_id is not None:
            if _KEY_ID.fullmatch(self.authority_hmac_key_id) is None:
                raise ValueError("authority_hmac_key_id must be a safe non-empty identifier")
            assert self.authority_hmac_secret_path is not None
            read_authority_hmac_secret(self.authority_hmac_secret_path)
            for name in ("expected_evaluator_build_digest", "expected_boundary_manifest_digest"):
                value = getattr(self, name)
                if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
                    raise ValueError(f"{name} must be a branded SHA-256 digest")

    def validate_confidence_resolution(self, alpha: float) -> None:
        """Fail closed when the configured resampling cannot resolve ``alpha``."""
        required = minimum_bootstrap_samples(alpha)
        if self.bootstrap_samples < required:
            raise ValueError(
                f"bootstrap_samples ({self.bootstrap_samples}) cannot resolve alpha={alpha:.12g}; "
                f"at least {required} samples are required"
            )

    def manifest(self) -> dict[str, Any]:
        """Return evaluator policy without serializing the verifier secret path."""

        payload = asdict(self)
        payload.pop("authority_hmac_secret_path")
        payload["authority_trust"] = (
            {
                "algorithm": "hmac-sha256",
                "key_id": self.authority_hmac_key_id,
                "expected_evaluator_build_digest": self.expected_evaluator_build_digest,
                "expected_boundary_manifest_digest": self.expected_boundary_manifest_digest,
            }
            if self.authority_hmac_key_id is not None
            else None
        )
        payload.pop("authority_hmac_key_id")
        payload.pop("expected_evaluator_build_digest")
        payload.pop("expected_boundary_manifest_digest")
        return payload

    @property
    def statistics_policy(self) -> KernelStatisticsPolicy:
        """Canonical receipt for every derived benchmark observation."""
        return KernelStatisticsPolicy(
            bootstrap_samples=self.bootstrap_samples,
            min_timing_blocks=self.min_timing_blocks,
            require_resource_telemetry=self.require_resource_telemetry,
            max_gpu_memory_bytes=self.max_gpu_memory_bytes,
        )


__all__ = ["KernelBenchmarkEvaluatorConfig"]
