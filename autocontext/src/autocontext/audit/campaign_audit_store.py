"""Cross-process persistence and attempt accounting for campaign audits."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autocontext.context_bundles.models import stable_digest
from autocontext.util.file_lock import advisory_path_lock
from autocontext.util.json_io import read_json_guarded, write_json

if TYPE_CHECKING:
    from autocontext.audit.campaign_auditor import (
        CampaignAuditDisposition,
        CampaignAuditRecord,
    )


class CampaignAuditStore:
    """Durable audit cache with serialized campaign-level budget claims."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def read_by_fingerprint(
        self,
        campaign_id: str,
        fingerprint: str,
        *,
        configuration_fingerprint: str | None = None,
    ) -> CampaignAuditRecord | None:
        if configuration_fingerprint is None:
            matches = self._records_for_evidence(campaign_id, fingerprint)
            if not matches:
                return None
            # Preserve the original evidence-only lookup for callers that do
            # not yet know about configuration fingerprints. Transient attempt
            # records and later configuration-specific reviews may coexist for
            # one evidence packet; prefer a usable completed review, then the
            # latest valid record when no call completed.
            completed = [record for record in matches if record.audit.status == "completed"]
            return max(completed or matches, key=_record_recency_key)
        data = read_json_guarded(self._path(campaign_id, _cache_fingerprint(fingerprint, configuration_fingerprint)))
        return _record_from_data(data)

    def write(self, record: CampaignAuditRecord) -> Path:
        with self.campaign_lock(record.audit.campaign_id):
            return self._write_unlocked(record)

    def _write_unlocked(self, record: CampaignAuditRecord) -> Path:
        cache_fingerprint = _cache_fingerprint(
            record.audit.evidence_fingerprint,
            record.audit.configuration_fingerprint,
        )
        deterministic_pre_call_failure = (
            record.audit.status == "failed" and record.audit.failure_reason == "bounded evidence packet exceeds max_input_chars"
        )
        if record.audit.status in {"timed_out", "canceled"} or (
            record.audit.status == "failed" and not deterministic_pre_call_failure
        ):
            cache_fingerprint = f"{cache_fingerprint}--attempt-{record.audit.audit_id}"
        path = self._path(record.audit.campaign_id, cache_fingerprint)
        write_json(path, record.to_dict())
        return path

    @contextmanager
    def campaign_lock(self, campaign_id: str) -> Iterator[None]:
        """Serialize budget claims and record updates across processes."""

        directory = self.root / _safe_segment(campaign_id)
        directory.mkdir(parents=True, exist_ok=True)
        with advisory_path_lock(directory / ".audit.lock"):
            yield

    def count(self, campaign_id: str) -> int:
        directory = self.root / _safe_segment(campaign_id)
        return len(list(directory.glob("*.json"))) if directory.exists() else 0

    def records(self, campaign_id: str) -> tuple[CampaignAuditRecord, ...]:
        """Return every valid durable record for campaign reporting."""

        directory = self.root / _safe_segment(campaign_id)
        if not directory.exists():
            return ()
        records = [
            record
            for path in sorted(directory.glob("*.json"))
            if (record := _record_from_data(read_json_guarded(path))) is not None
        ]
        return tuple(sorted(records, key=_record_recency_key))

    def call_count(self, campaign_id: str) -> int:
        """Return durable provider-call claims, including legacy attempts."""

        directory = self.root / _safe_segment(campaign_id)
        if not directory.exists():
            return 0
        # New calls are claimed before provider dispatch and remain in this
        # append-only journal even if the process dies before writing an audit.
        claim_paths = list((directory / "attempts").glob("*.json"))
        claim_ids = {path.stem for path in claim_paths}
        attempted = len(claim_paths)
        for path in directory.glob("*.json"):
            data = read_json_guarded(path)
            record = _record_from_data(data)
            if record is None:
                # Corrupt legacy state must fail closed rather than reopen a
                # paid-call budget.
                attempted += 1
                continue
            # Audits associated with a present journal slot have already been
            # counted. If a claim was lost or only partially migrated, the
            # durable audit itself remains a fail-closed fallback claim.
            if record.audit.model_call_attempt_id is None or record.audit.model_call_attempt_id not in claim_ids:
                attempted += int(record.audit.model_call_attempted)
        return attempted

    def _reserve_call_unlocked(
        self,
        *,
        campaign_id: str,
        evidence_fingerprint: str,
        configuration_fingerprint: str,
    ) -> str:
        """Durably claim one provider attempt before dispatch."""

        attempt_id = str(uuid.uuid4())
        write_json(
            self.root / _safe_segment(campaign_id) / "attempts" / f"{attempt_id}.json",
            {
                "schema_version": 1,
                "attempt_id": attempt_id,
                "campaign_id": campaign_id,
                "evidence_fingerprint": evidence_fingerprint,
                "configuration_fingerprint": configuration_fingerprint,
                "claimed_at": datetime.now().astimezone().isoformat(),
            },
        )
        return attempt_id

    def _release_call_unlocked(self, campaign_id: str, attempt_id: str) -> None:
        """Release a reservation only when local submission provably failed."""

        claim = self.root / _safe_segment(campaign_id) / "attempts" / f"{attempt_id}.json"
        claim.unlink(missing_ok=True)

    def add_disposition(
        self,
        campaign_id: str,
        evidence_fingerprint: str,
        disposition: CampaignAuditDisposition,
    ) -> CampaignAuditRecord:
        with self.campaign_lock(campaign_id):
            records = self._records_for_evidence(campaign_id, evidence_fingerprint)
            record = next((item for item in records if item.audit.audit_id == disposition.audit_id), None)
            if record is None:
                raise ValueError("audit record not found")
            if any(item.disposition_id == disposition.disposition_id for item in record.dispositions):
                return record
            updated = record.model_copy(update={"dispositions": [*record.dispositions, disposition]})
            self._write_unlocked(updated)
            return updated

    def _records_for_evidence(self, campaign_id: str, fingerprint: str) -> list[CampaignAuditRecord]:
        directory = self.root / _safe_segment(campaign_id)
        records: list[CampaignAuditRecord] = []
        for path in sorted(directory.glob("*.json")) if directory.exists() else ():
            record = _record_from_data(read_json_guarded(path))
            if record is not None and record.audit.evidence_fingerprint == fingerprint:
                records.append(record)
        return records

    def _path(self, campaign_id: str, fingerprint: str) -> Path:
        return self.root / _safe_segment(campaign_id) / f"{_safe_segment(fingerprint)}.json"


def _record_from_data(data: Any) -> CampaignAuditRecord | None:
    if not isinstance(data, dict):
        return None
    # Local import keeps the public record contracts in campaign_auditor while
    # allowing the persistence implementation to live in this focused module.
    from autocontext.audit.campaign_auditor import CampaignAuditRecord

    try:
        return CampaignAuditRecord.from_dict(data)
    except (TypeError, ValueError):
        return None


def _record_recency_key(record: CampaignAuditRecord) -> tuple[float, str, str]:
    """Return a deterministic chronology key for legacy evidence-only reads."""

    reviewed_at = record.audit.reviewed_at
    try:
        parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        timestamp = parsed.timestamp()
    except (OverflowError, ValueError):
        timestamp = float("-inf")
    return timestamp, reviewed_at, record.audit.audit_id


def _cache_fingerprint(evidence_fingerprint: str, configuration_fingerprint: str) -> str:
    if not configuration_fingerprint:
        return evidence_fingerprint
    return stable_digest(
        {
            "evidence_fingerprint": evidence_fingerprint,
            "configuration_fingerprint": configuration_fingerprint,
        }
    )


def _safe_segment(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("audit path identity must be one non-empty segment")
    return value


__all__ = ["CampaignAuditStore"]
