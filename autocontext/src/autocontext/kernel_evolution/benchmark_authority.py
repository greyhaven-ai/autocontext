"""Authenticated authority and timing-boundary policy for benchmark reports."""

from __future__ import annotations

from autocontext.kernel_evolution.authority_protocol import (
    read_authority_hmac_secret,
    verify_authority_receipt,
)
from autocontext.kernel_evolution.evaluator_config import KernelBenchmarkEvaluatorConfig
from autocontext.kernel_evolution.models import KernelBenchmarkReport

AuthorityRejection = tuple[str, str]


class BenchmarkAuthorityVerifier:
    """Keep host trust material private while returning bounded rejection details."""

    def __init__(self, config: KernelBenchmarkEvaluatorConfig) -> None:
        self._config = config
        self._secret = (
            read_authority_hmac_secret(config.authority_hmac_secret_path)
            if config.authority_hmac_secret_path is not None
            else None
        )

    def verify_receipt(self, report: KernelBenchmarkReport) -> AuthorityRejection | None:
        """Authenticate required receipts against the operator-pinned identity."""

        if not self._config.require_authority_receipt:
            return None
        receipt = report.evaluator_authority_receipt
        if receipt is None:
            return (
                "missing_authority_receipt",
                "Production evaluation requires a trusted-evaluator authority receipt.",
            )
        assert self._config.authority_hmac_key_id is not None
        assert self._config.expected_evaluator_build_digest is not None
        assert self._config.expected_boundary_manifest_digest is not None
        assert self._secret is not None
        try:
            verify_authority_receipt(
                receipt,
                report.model_dump(mode="json"),
                trusted_key_id=self._config.authority_hmac_key_id,
                trusted_secret=self._secret,
                expected_evaluator_build_digest=self._config.expected_evaluator_build_digest,
                expected_boundary_manifest_digest=self._config.expected_boundary_manifest_digest,
            )
        except (TypeError, ValueError) as exc:
            return (
                "invalid_authority_receipt",
                f"Trusted-evaluator authority receipt verification failed: {exc}",
            )
        return None

    def verify_timing_comparability(self, report: KernelBenchmarkReport) -> AuthorityRejection | None:
        """Reject metrics that mix incomparable candidate/reference timing boundaries."""

        evidence = report.metadata.get("timing_comparability")
        if self._config.require_authority_receipt and not isinstance(evidence, dict):
            return (
                "timing_boundary_mismatch",
                "Protected evaluation omitted its trusted timing-boundary comparability evidence.",
            )
        if isinstance(evidence, dict) and (
            evidence.get("candidate_incumbent_comparable") is not True
            or evidence.get("reference_comparable") is not True
            or evidence.get("promotion_comparison") != ["candidate_ms", "incumbent_ms"]
        ):
            return (
                "timing_boundary_mismatch",
                "Candidate, incumbent, and reference timings were not measured under comparable trusted boundaries.",
            )
        return None


__all__ = ["BenchmarkAuthorityVerifier"]
