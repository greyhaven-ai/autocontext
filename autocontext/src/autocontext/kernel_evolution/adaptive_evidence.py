"""Lifecycle guards for quarantined adaptive kernel evidence."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.kernel_evolution.lineage import KernelLineageStore
    from autocontext.kernel_evolution.models import KernelBenchmarkObservation, KernelPromotionDecision


def validate_sealed_evidence_roots(
    *,
    finite_sample: bool,
    confirmation_enabled: bool,
    quarantine_primary_evidence: bool,
    public_root: Path,
    sealed_audit_root: Path | None,
) -> None:
    """Require disjoint durable storage for evidence hidden during adaptation."""
    required = finite_sample and (confirmation_enabled or quarantine_primary_evidence)
    if required and sealed_audit_root is None:
        raise ValueError("finite-sample adaptive evidence requires a separate sealed_audit_root")
    if not required or sealed_audit_root is None:
        return
    resolved_public = public_root.resolve()
    resolved_audit = sealed_audit_root.resolve()
    if (
        resolved_public == resolved_audit
        or resolved_public.is_relative_to(resolved_audit)
        or resolved_audit.is_relative_to(resolved_public)
    ):
        raise ValueError("sealed_audit_root must be disjoint from the public lineage root")


def release_sealed_audit_best_effort(store: KernelLineageStore) -> None:
    """Publish terminal evidence without replacing the campaign's real error."""
    try:
        store.release_sealed_audit()
    except Exception:
        # The operator-only copy was already written atomically.
        pass


def confirmation_identity_unavailable(
    *,
    finite_sample: bool,
    decision: KernelPromotionDecision | None,
    observation: KernelBenchmarkObservation | None,
) -> bool:
    """Return whether an attempted holdout cannot be safely reserved for reuse."""
    return finite_sample and decision is not None and (
        observation is None or observation.report is None or observation.protocol_id is None
    )


def terminal_error_text(
    exc: BaseException,
    *,
    finite_sample: bool,
    confirmation_enabled: bool,
    quarantine_primary_evidence: bool,
) -> str:
    """Keep adaptive evidence out of a public failure manifest."""
    if finite_sample and (confirmation_enabled or quarantine_primary_evidence):
        return "terminal failure; detailed adaptive evidence remains in sealed audit"
    return str(exc)[:1_000]


__all__ = [
    "confirmation_identity_unavailable",
    "release_sealed_audit_best_effort",
    "terminal_error_text",
    "validate_sealed_evidence_roots",
]
