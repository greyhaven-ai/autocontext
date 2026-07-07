"""Deterministic post-proposal leakage audit (AC-879).

Pure functions over declared integrity metadata plus observed access records.
No filesystem or network access: the caller supplies the access log. Maps a run
to clean | contaminated | unknown so a verified gate can fail closed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from autocontext.harness_optimization.contract.models import IntegrityMetadata


@dataclass(frozen=True, slots=True)
class AccessRecord:
    resource: str
    source_id: str
    kind: str  # "file" | "trace" | "web" | "split"


@dataclass(frozen=True, slots=True)
class LeakageAudit:
    status: str  # "clean" | "contaminated" | "unknown"
    reasons: tuple[str, ...]


def _web_host(resource: str) -> str:
    parsed = urlparse(resource if "://" in resource else f"//{resource}")
    return parsed.hostname or resource


def audit_leakage(metadata: IntegrityMetadata, access_records: Sequence[AccessRecord]) -> LeakageAudit:
    forbidden = set(metadata.forbidden_sources)
    split_ids = set(metadata.split_ids)
    allowlist = set(metadata.web_allowlist or [])
    reasons: list[str] = []

    for rec in access_records:
        if rec.source_id in forbidden:
            reasons.append(f"forbidden source read: {rec.source_id} ({rec.resource})")
    for rec in access_records:
        if rec.kind == "split" and rec.resource in split_ids and rec.source_id in forbidden:
            reasons.append(f"forbidden split touched: {rec.resource}")
    for rec in access_records:
        if rec.kind == "web":
            host = _web_host(rec.resource)
            if metadata.web_policy == "blocked":
                reasons.append(f"web access under blocked policy: {host}")
            elif metadata.web_policy == "allowlist" and host not in allowlist:
                reasons.append(f"web host not in allowlist: {host}")

    if reasons:
        return LeakageAudit(status="contaminated", reasons=tuple(reasons))

    covered = {rec.source_id for rec in access_records}
    unknown = [s for s in metadata.required_sources if s not in covered and s not in metadata.allowed_sources]
    if unknown:
        return LeakageAudit(
            status="unknown",
            reasons=tuple(f"required source unproven: {s}" for s in unknown),
        )
    return LeakageAudit(status="clean", reasons=())
