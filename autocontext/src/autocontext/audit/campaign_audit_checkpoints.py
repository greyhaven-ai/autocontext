"""Live checkpoint adapters for the bounded campaign auditor."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autocontext.audit.campaign_auditor import (
        AuditCheckpoint,
        AuditPolicyOutcome,
        CampaignAudit,
        CampaignAuditEvidencePacket,
        CampaignAuditor,
    )


class CampaignAuditCheckpointRunner:
    """Invoke the auditor from live checkpoints through one bounded packet factory."""

    def __init__(
        self,
        auditor: CampaignAuditor,
        packet_factory: Callable[[AuditCheckpoint, Mapping[str, Any]], CampaignAuditEvidencePacket],
    ) -> None:
        self.auditor = auditor
        self.packet_factory = packet_factory

    def review_checkpoint(
        self,
        checkpoint: AuditCheckpoint,
        evidence: Mapping[str, Any],
        *,
        cancellation_event: threading.Event | None = None,
    ) -> CampaignAudit | None:
        packet = self.packet_factory(checkpoint, evidence)
        if packet.checkpoint != checkpoint:
            raise ValueError("campaign audit packet factory returned the wrong checkpoint")
        audit = self.auditor.review(packet, cancellation_event=cancellation_event)
        return _apply_operator_disposition(self.auditor, audit)


class ContextBundlePrePromotionAuditor:
    """Adapter from the context-bundle promotion boundary to CampaignAuditor."""

    def __init__(
        self,
        auditor: CampaignAuditor,
        packet_factory: Callable[[Any, Any, tuple[Any, ...]], CampaignAuditEvidencePacket],
    ) -> None:
        self.auditor = auditor
        self.packet_factory = packet_factory

    def review_pre_promotion(
        self,
        candidate: Any,
        comparison: Any,
        trials: tuple[Any, ...],
        *,
        cancellation_event: threading.Event | None = None,
    ) -> AuditPolicyOutcome | None:
        packet = self.packet_factory(candidate, comparison, trials)
        if packet.checkpoint != "pre_promotion":
            raise ValueError("context bundle audit packet must use the pre_promotion checkpoint")
        audit = _apply_operator_disposition(
            self.auditor,
            self.auditor.review(packet, cancellation_event=cancellation_event),
        )
        if audit is None:
            return None
        if audit.status != "completed":
            return "safe_pause_recommended"
        return audit.policy_outcome


def _apply_operator_disposition(
    auditor: CampaignAuditor,
    audit: CampaignAudit | None,
) -> CampaignAudit | None:
    """Apply the latest durable operator resolution without mutating the audit."""

    if audit is None:
        return None
    record = auditor.store.read_by_fingerprint(
        audit.campaign_id,
        audit.evidence_fingerprint,
        configuration_fingerprint=audit.configuration_fingerprint,
    )
    if record is None or record.audit.audit_id != audit.audit_id or not record.dispositions:
        return audit
    disposition = record.dispositions[-1].disposition
    if disposition in {"dismissed", "mitigated"}:
        return audit.model_copy(update={"policy_outcome": "advisory"})
    if disposition == "deferred":
        return audit.model_copy(update={"policy_outcome": "safe_pause_recommended"})
    return audit


__all__ = ["CampaignAuditCheckpointRunner", "ContextBundlePrePromotionAuditor"]
