"""Cross-process persistence and attempt accounting for campaign audits."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autocontext.audit.campaign_audit_boundary import redacted_identity
from autocontext.context_bundles.models import stable_digest
from autocontext.util.file_lock import advisory_path_lock
from autocontext.util.json_io import read_json_guarded

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
        cache_fingerprint = _cache_fingerprint(fingerprint, configuration_fingerprint)
        matches = [
            record
            for record in self._records_for_evidence(campaign_id, fingerprint)
            if record.audit.configuration_fingerprint == configuration_fingerprint
            and _record_storage_fingerprint(record) == cache_fingerprint
        ]
        return max(matches, key=_record_recency_key) if matches else None

    def write(self, record: CampaignAuditRecord) -> Path:
        with self.campaign_lock(record.audit.campaign_id):
            return self._write_unlocked(record)

    def _write_unlocked(self, record: CampaignAuditRecord) -> Path:
        path = self._path(record.audit.campaign_id, _record_storage_fingerprint(record))
        _write_json_durable(path, record.to_dict())
        return path

    @contextmanager
    def campaign_lock(self, campaign_id: str) -> Iterator[None]:
        """Serialize budget claims and record updates across processes."""

        canonical_id = _canonical_campaign_id(campaign_id)
        directory = self._campaign_directory(canonical_id)
        directory.mkdir(parents=True, exist_ok=True)
        with advisory_path_lock(directory / ".audit.lock"):
            for legacy in self._legacy_campaign_directories(campaign_id):
                if legacy == directory or not legacy.exists():
                    continue
                with advisory_path_lock(legacy / ".audit.lock"):
                    self._migrate_legacy_unlocked(canonical_id, legacy)
            yield

    def count(self, campaign_id: str) -> int:
        return len(self.records(campaign_id))

    def records(self, campaign_id: str) -> tuple[CampaignAuditRecord, ...]:
        """Return every valid durable record for campaign reporting."""

        records: dict[str, CampaignAuditRecord] = {}
        for path in self._record_paths(campaign_id):
            record = _record_from_data(read_json_guarded(path))
            if record is not None:
                records[record.audit.audit_id] = record
        return tuple(sorted(records.values(), key=_record_recency_key))

    def call_count(self, campaign_id: str) -> int:
        """Return durable provider-call claims, including legacy attempts."""

        # New calls are claimed before provider dispatch and remain in this
        # append-only journal even if the process dies before writing an audit.
        claim_ids: set[str] = set()
        corrupt_claims = 0
        for path in self._attempt_paths(campaign_id):
            data = read_json_guarded(path)
            attempt_id = data.get("attempt_id") if isinstance(data, dict) else None
            if isinstance(attempt_id, str) and attempt_id:
                claim_ids.add(attempt_id)
            else:
                corrupt_claims += 1
        attempted = len(claim_ids) + corrupt_claims
        records: dict[str, CampaignAuditRecord] = {}
        for path in self._record_paths(campaign_id):
            data = read_json_guarded(path)
            record = _record_from_data(data)
            if record is None:
                # Corrupt legacy state must fail closed rather than reopen a
                # paid-call budget.
                attempted += 1
                continue
            records[record.audit.audit_id] = record
        for record in records.values():
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
        _write_json_durable(
            self._attempt_path(campaign_id, attempt_id),
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

        for claim in self._attempt_paths(campaign_id):
            data = read_json_guarded(claim)
            if isinstance(data, dict) and data.get("attempt_id") == attempt_id:
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
        return [record for record in self.records(campaign_id) if record.audit.evidence_fingerprint == fingerprint]

    def _path(self, campaign_id: str, fingerprint: str) -> Path:
        return self._campaign_directory(campaign_id) / f"{_digest_segment('run', fingerprint)}.json"

    def _attempt_path(self, campaign_id: str, attempt_id: str) -> Path:
        return self._campaign_directory(campaign_id) / "attempts" / f"{_digest_segment('attempt', attempt_id)}.json"

    def _campaign_directory(self, campaign_id: str) -> Path:
        return self.root / _digest_segment("campaign", _canonical_campaign_id(campaign_id))

    def _legacy_campaign_directory(self, campaign_id: str) -> Path:
        return self.root / _legacy_safe_segment(campaign_id)

    def _legacy_campaign_directories(self, campaign_id: str) -> tuple[Path, ...]:
        identities = (campaign_id, _canonical_campaign_id(campaign_id))
        by_path = {self._legacy_campaign_directory(identity): None for identity in identities}
        return tuple(by_path)

    def _campaign_directories(self, campaign_id: str) -> tuple[Path, ...]:
        canonical = self._campaign_directory(campaign_id)
        return tuple(dict.fromkeys((canonical, *self._legacy_campaign_directories(campaign_id))))

    def _record_paths(self, campaign_id: str) -> tuple[Path, ...]:
        return tuple(
            path
            for directory in self._campaign_directories(campaign_id)
            if directory.exists()
            for path in sorted(directory.glob("*.json"))
        )

    def _attempt_paths(self, campaign_id: str) -> tuple[Path, ...]:
        return tuple(
            path
            for directory in self._campaign_directories(campaign_id)
            if directory.exists()
            for path in sorted((directory / "attempts").glob("*.json"))
        )

    def _migrate_legacy_unlocked(self, campaign_id: str, legacy: Path) -> None:
        """Move valid legacy artifacts under digest-only canonical names."""

        for path in sorted(legacy.glob("*.json")):
            record = _record_from_data(read_json_guarded(path))
            if record is None or _canonical_campaign_id(record.audit.campaign_id) != campaign_id:
                continue
            _validated_disposition_history(record, source="legacy")
            target = self._path(campaign_id, _record_storage_fingerprint(record))
            if target.exists():
                canonical = _record_from_data(read_json_guarded(target))
                if canonical is None:
                    raise ValueError("canonical campaign audit record is invalid; legacy migration refused")
                merged = _merge_migration_records(canonical, record)
                if merged != canonical:
                    _write_json_durable(target, merged.to_dict())
            else:
                _write_json_durable(target, record.to_dict())
            path.unlink(missing_ok=True)
        for path in sorted((legacy / "attempts").glob("*.json")):
            data = read_json_guarded(path)
            attempt_id = data.get("attempt_id") if isinstance(data, dict) else None
            attempt_campaign = data.get("campaign_id") if isinstance(data, dict) else None
            if (
                not isinstance(attempt_id, str)
                or not attempt_id
                or not isinstance(attempt_campaign, str)
                or _canonical_campaign_id(attempt_campaign) != campaign_id
            ):
                continue
            target = self._attempt_path(campaign_id, attempt_id)
            if target.exists():
                canonical_data = read_json_guarded(target)
                if canonical_data != data:
                    raise ValueError("canonical campaign audit attempt conflicts with legacy state")
            else:
                _write_json_durable(target, data)
            path.unlink(missing_ok=True)


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


def _merge_migration_records(
    canonical: CampaignAuditRecord,
    legacy: CampaignAuditRecord,
) -> CampaignAuditRecord:
    """Merge one audit's disposition history or fail closed on ambiguity."""

    if canonical.audit != legacy.audit:
        raise ValueError("canonical campaign audit record conflicts with legacy audit binding")
    dispositions = _validated_disposition_history(canonical, source="canonical")
    legacy_dispositions = _validated_disposition_history(legacy, source="legacy")
    for item in legacy_dispositions.values():
        prior = dispositions.get(item.disposition_id)
        if prior is not None and prior != item:
            raise ValueError("canonical campaign audit disposition conflicts with legacy state")
        dispositions[item.disposition_id] = item
    ordered = sorted(dispositions.values(), key=_disposition_recency_key)
    return canonical.model_copy(update={"dispositions": ordered})


def _validated_disposition_history(
    record: CampaignAuditRecord,
    *,
    source: str,
) -> dict[str, CampaignAuditDisposition]:
    dispositions: dict[str, CampaignAuditDisposition] = {}
    for item in record.dispositions:
        if item.audit_id != record.audit.audit_id:
            raise ValueError(f"{source} campaign audit disposition has a foreign audit binding")
        if item.disposition_id in dispositions:
            raise ValueError(f"{source} campaign audit disposition history contains a duplicate identity")
        dispositions[item.disposition_id] = item
    return dispositions


def _disposition_recency_key(disposition: CampaignAuditDisposition) -> tuple[float, str, str]:
    recorded_at = disposition.recorded_at
    try:
        parsed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        timestamp = parsed.timestamp()
    except (OverflowError, ValueError):
        timestamp = float("-inf")
    return timestamp, recorded_at, disposition.disposition_id


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


def _record_storage_fingerprint(record: CampaignAuditRecord) -> str:
    cache_fingerprint = _cache_fingerprint(
        record.audit.evidence_fingerprint,
        record.audit.configuration_fingerprint,
    )
    deterministic_pre_call_failure = (
        record.audit.status == "failed"
        and record.audit.failure_reason == "bounded evidence packet exceeds max_input_chars"
    )
    if record.audit.status in {"timed_out", "canceled"} or (
        record.audit.status == "failed" and not deterministic_pre_call_failure
    ):
        return f"{cache_fingerprint}--attempt-{record.audit.audit_id}"
    return cache_fingerprint


def _digest_segment(kind: str, value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("audit path identity must be non-empty")
    return f"{kind}-{stable_digest({kind: value})}"


def _canonical_campaign_id(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("audit campaign identity must be non-empty")
    return redacted_identity(value, "campaign")


def _legacy_safe_segment(value: str) -> str:
    """Reproduce the pre-digest layout solely for migration and reads."""

    if not value:
        raise ValueError("audit path identity must be non-empty")
    if (
        value not in {".", ".."}
        and len(value.encode("utf-8")) <= 120
        and all(character.isascii() and (character.isalnum() or character in "-_.") for character in value)
    ):
        return value
    return f"%{stable_digest({'audit_path_identity': value})}"


def _write_json_durable(path: Path, data: dict[str, Any]) -> None:
    """Atomically replace one artifact after syncing its bytes and directory entry."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        try:
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written == 0:
                    raise OSError("campaign audit write made no progress")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["CampaignAuditStore"]
