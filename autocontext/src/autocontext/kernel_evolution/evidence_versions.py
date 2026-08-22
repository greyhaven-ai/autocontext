"""Explicit reader compatibility and verification status for kernel evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from autocontext.kernel_evolution.models import KernelEvolutionResult

KernelEvidenceVerificationStatus = Literal[
    "legacy-v2-unverified-policy-replay",
    "legacy-v3-empirical-unverified-policy-replay",
    "v4-finite-sample-policy-replay-verified",
]


class KernelEvidenceVersionError(ValueError):
    """Raised when a reader cannot make an unambiguous schema decision."""


@dataclass(frozen=True, slots=True)
class KernelEvidenceReadResult:
    result: KernelEvolutionResult
    verification_status: KernelEvidenceVerificationStatus
    decision_policy_id: str | None


_RESULT_VERSION = {
    "autocontext.kernel-result/v2": 2,
    "autocontext.kernel-result/v3": 3,
    "autocontext.kernel-result/v4": 4,
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise KernelEvidenceVersionError(f"kernel evidence contains duplicate JSON key {key!r}")
        payload[key] = value
    return payload


def _reject_non_finite_constant(value: str) -> None:
    raise KernelEvidenceVersionError(f"kernel evidence contains non-finite JSON number {value}")


def read_kernel_evolution_result(
    raw: str | bytes | dict[str, Any],
    *,
    max_supported_version: int = 4,
) -> KernelEvidenceReadResult:
    """Read one result with explicit legacy and forward-compatibility behavior."""

    if isinstance(raw, dict):
        payload = raw
    else:
        try:
            payload = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_constant,
            )
        except KernelEvidenceVersionError:
            raise
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise KernelEvidenceVersionError("kernel evidence is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise KernelEvidenceVersionError("kernel result evidence must be a JSON object")
    schema = payload.get("schema_version")
    version = _RESULT_VERSION.get(schema) if isinstance(schema, str) else None
    if version is None:
        raise KernelEvidenceVersionError("kernel result schema_version is missing or unsupported")
    if version > max_supported_version:
        raise KernelEvidenceVersionError(
            f"kernel result v{version} requires a newer reader; this reader supports through v{max_supported_version}"
        )
    result = KernelEvolutionResult.model_validate(payload)
    if version == 2:
        return KernelEvidenceReadResult(
            result=result,
            verification_status="legacy-v2-unverified-policy-replay",
            decision_policy_id=None,
        )
    if version == 3:
        return KernelEvidenceReadResult(
            result=result,
            verification_status="legacy-v3-empirical-unverified-policy-replay",
            decision_policy_id=result.decision_policy.policy_id if result.decision_policy is not None else None,
        )
    if result.decision_policy is None or result.decision_policy_id != result.decision_policy.policy_id:
        raise KernelEvidenceVersionError("v4 kernel result has ambiguous decision-policy identity")
    return KernelEvidenceReadResult(
        result=result,
        verification_status="v4-finite-sample-policy-replay-verified",
        decision_policy_id=result.decision_policy_id,
    )


__all__ = [
    "KernelEvidenceReadResult",
    "KernelEvidenceVerificationStatus",
    "KernelEvidenceVersionError",
    "read_kernel_evolution_result",
]
