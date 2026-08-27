"""Bounded, read-only campaign auditor with deterministic integrity preflight (AC-980)."""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from autocontext.audit import campaign_audit_routes as routes
from autocontext.audit.campaign_audit_boundary import (
    bounded_redacted_text,
    evidence_fingerprint,
    validate_evidence_boundary,
)
from autocontext.audit.campaign_audit_packet_builder import build_campaign_audit_packet
from autocontext.audit.campaign_audit_store import CampaignAuditStore
from autocontext.audit.campaign_audit_transport import (
    AuditorCallHandle,
    AuditorModelClient,
    AuditorModelResponse,
    CancellableAuditorModelClient,
    execute_auditor_call,
)
from autocontext.context_bundles.models import canonical_json, stable_digest
from autocontext.util.models import StrictModel

AuditCheckpoint = Literal["pre_promotion", "inconclusive_gate", "integrity_alert", "final_completion"]
AuditSeverity = Literal["info", "low", "medium", "high", "critical"]
AuditCategory = Literal[
    "non_comparable_cohorts",
    "evaluator_epoch_mismatch",
    "data_leakage",
    "missing_reconstruction_evidence",
    "infrastructure_misclassification",
    "unsupported_causal_claim",
    "repeated_unchanged_experiment",
    "other",
]
AuditPolicyOutcome = Literal["advisory", "review_required", "safe_pause_recommended"]


def _default_checkpoints() -> list[AuditCheckpoint]:
    return ["pre_promotion", "inconclusive_gate", "integrity_alert", "final_completion"]


class CampaignAuditConfig(StrictModel):
    enabled: bool = False
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    auditor_route: routes.CampaignAuditRoute | None = None
    proposer_provider: str = Field(min_length=1)
    proposer_model: str = Field(min_length=1)
    proposer_routes: list[routes.CampaignAuditRoute] = Field(default_factory=list)
    allow_same_route: bool = False
    checkpoints: list[AuditCheckpoint] = Field(default_factory=_default_checkpoints)
    max_calls_per_campaign: int = Field(default=8, ge=1)
    max_input_chars: int = Field(default=24_000, ge=1_000)
    max_output_tokens: int = Field(default=1_200, ge=64)
    timeout_seconds: float = Field(default=30.0, gt=0)
    allow_uncancellable_transport: bool = False
    prompt_version: str = "campaign-auditor-v1"
    policy: Literal["advisory", "review_required_on_high", "pause_recommended_on_critical"] = "advisory"
    input_cost_per_million: float = Field(default=0.0, ge=0.0)
    output_cost_per_million: float = Field(default=0.0, ge=0.0)

    @field_validator("provider", "proposer_provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("campaign audit provider must be non-empty")
        return normalized

    @field_validator("model", "proposer_model")
    @classmethod
    def _normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("campaign audit model must be non-empty")
        return normalized


class AuditEvidenceReference(StrictModel):
    uri: str = Field(min_length=1)
    digest: str | None
    summary: str = Field(min_length=1)


class AuditProtocolLane(StrictModel):
    lane_id: str
    verifier_contract_ref: str
    seed_count: int = Field(ge=0)


class AuditBundleLineage(StrictModel):
    bundle_digest: str
    parent_digest: str | None
    evaluator_epoch: str


class AuditMetricSummary(StrictModel):
    trial_id: str
    branch_id: str
    candidate_digest: str
    cohort: str
    evaluator_epoch: str
    verifier_digest: str
    fixture_digest: str
    score: float | None
    valid: bool
    classification: Literal[
        "candidate_result",
        "candidate_failure",
        "infrastructure_failure",
        "canceled",
        "budget_exhausted",
    ]
    infrastructure_error: bool
    reconstruction_ref: str | None
    experiment_id: str | None = None
    attempt: int = Field(default=0, ge=0)
    consumed_tokens: int = Field(default=0, ge=0)
    consumed_wall_seconds: float = Field(default=0.0, ge=0.0)
    consumed_compute_units: float = Field(default=0.0, ge=0.0)
    consumed_jobs: int = Field(default=0, ge=0)
    cleanup_succeeded: bool | None = None


class AuditGateDecision(StrictModel):
    gate_id: str
    decision: str
    claim: str
    evidence_level: Literal["causal_ablation", "paired_shadow", "component_correlated", "unspecified"]
    evaluator_epoch: str
    cohort: str
    artifact_ref: str | None


class AuditNegativeResult(StrictModel):
    result_id: str
    disposition: str
    reason: str
    evidence_refs: list[str] = Field(min_length=1)


class CampaignAuditEvidencePacket(StrictModel):
    schema_version: Literal[1] = 1
    access_scope: Literal["read_only"] = "read_only"
    hidden_holdout_answers_included: bool
    credentials_included: Literal[False] = False
    boundary_provenance: Literal["whitelisted_redacted_v1"]
    boundary_digest: str = Field(min_length=1)
    campaign_id: str
    run_id: str
    scenario_name: str
    checkpoint: AuditCheckpoint
    terminal_state: str
    protocol_lanes: list[AuditProtocolLane]
    bundle_lineage: list[AuditBundleLineage]
    metric_summaries: list[AuditMetricSummary]
    gate_decisions: list[AuditGateDecision]
    negative_results: list[AuditNegativeResult]
    integrity_alerts: list[str]
    artifact_refs: list[AuditEvidenceReference] = Field(min_length=1)

    @property
    def fingerprint(self) -> str:
        return evidence_fingerprint(self.to_dict())


class CampaignAuditFinding(StrictModel):
    finding_id: str
    category: AuditCategory
    severity: AuditSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=1)
    evidence_refs: list[str]
    affected_branches: list[str]
    affected_trials: list[str]
    recommended_action: str = Field(min_length=1)
    source: Literal["deterministic_preflight", "llm"]


class CampaignAudit(StrictModel):
    schema_version: Literal[1] = 1
    audit_id: str
    campaign_id: str
    checkpoint: AuditCheckpoint
    reviewed_at: str
    status: Literal["completed", "timed_out", "canceled", "failed", "budget_exhausted"]
    evidence_fingerprint: str
    configuration_fingerprint: str = ""
    findings: list[CampaignAuditFinding]
    recommended_action: str
    policy_outcome: AuditPolicyOutcome
    provider: str
    model: str
    prompt_version: str
    auditor_route: routes.CampaignAuditRoute | None = None
    proposer_routes: list[routes.CampaignAuditRoute] = Field(default_factory=list)
    route_distinct_from_proposer: bool
    frozen_non_trainable: Literal[True] = True
    model_call_attempted: bool = True
    model_call_attempt_id: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost: float = Field(ge=0.0)
    failure_reason: str | None

    @model_validator(mode="before")
    @classmethod
    def _infer_legacy_attempt_status(cls, value: Any) -> Any:
        """Migrate pre-field records without poisoning budgets after upgrade."""

        if isinstance(value, dict) and "model_call_attempted" not in value:
            migrated = dict(value)
            # The former exhausted branch persisted without a provider call.
            # Other legacy failures are charged because their origin is lost.
            migrated["model_call_attempted"] = (
                value.get("status") != "budget_exhausted"
                and value.get("failure_reason") != "bounded evidence packet exceeds max_input_chars"
            )
            return migrated
        return value


class CampaignAuditDisposition(StrictModel):
    disposition_id: str
    audit_id: str
    operator: str
    disposition: Literal["accepted", "dismissed", "mitigated", "deferred"]
    rationale: str
    recorded_at: str


class CampaignAuditRecord(StrictModel):
    audit: CampaignAudit
    dispositions: list[CampaignAuditDisposition]


class CampaignAuditor:
    def __init__(
        self,
        config: CampaignAuditConfig,
        *,
        client: AuditorModelClient,
        store: CampaignAuditStore,
    ) -> None:
        proposer_routes = routes.normalize_proposer_routes(
            config.proposer_provider, config.proposer_model, config.proposer_routes
        )
        auditor_route = config.auditor_route or routes.CampaignAuditRoute.resolved(config.provider, config.model)
        if (auditor_route.provider, auditor_route.model) != (config.provider, config.model):
            raise ValueError("campaign auditor route identity must match the configured provider and model")
        self.config = config.model_copy(
            update={"auditor_route": auditor_route, "proposer_routes": proposer_routes}
        )
        self.client = client
        self.store = store
        self._route_distinct = routes.auditor_route_is_distinct(auditor_route, proposer_routes)
        if not self._route_distinct and not self.config.allow_same_route:
            raise ValueError("auditor route must differ from every proposer route unless explicitly overridden")
        if (
            self.config.enabled
            and not self.config.allow_uncancellable_transport
            and not callable(getattr(self.client, "start_generate", None))
        ):
            raise ValueError(
                "enabled campaign auditor requires a cancellable transport; "
                "set allow_uncancellable_transport only for trusted legacy clients"
            )

    def review(
        self,
        packet: CampaignAuditEvidencePacket,
        *,
        cancellation_event: threading.Event | None = None,
    ) -> CampaignAudit | None:
        """Run one cached advisory review; disabled/failure paths never touch deterministic gates."""

        if not self.config.enabled or packet.checkpoint not in self.config.checkpoints:
            return None
        validate_evidence_boundary(packet)
        if cancellation_event is not None and cancellation_event.is_set():
            audit = self._failure_audit(
                packet,
                "canceled",
                "campaign audit canceled before model dispatch",
                model_call_attempted=False,
            )
            self.store.write(CampaignAuditRecord(audit=audit, dispositions=[]))
            return audit
        # Cache lookup, call-budget claim, provider request, and durable write
        # form one campaign transaction. This intentionally serializes the
        # bounded advisory route so concurrent callers cannot duplicate a
        # billable request or bypass max_calls_per_campaign.
        with self.store.campaign_lock(packet.campaign_id):
            return self._review_locked(packet, cancellation_event=cancellation_event)

    def _review_locked(
        self,
        packet: CampaignAuditEvidencePacket,
        *,
        cancellation_event: threading.Event | None,
    ) -> CampaignAudit:
        configuration_fingerprint = stable_digest(self.config.to_dict())
        cached = self.store.read_by_fingerprint(
            packet.campaign_id,
            packet.fingerprint,
            configuration_fingerprint=configuration_fingerprint,
        )
        if cached is not None:
            return cached.audit
        if cancellation_event is not None and cancellation_event.is_set():
            audit = self._failure_audit(
                packet,
                "canceled",
                "campaign audit canceled before model dispatch",
                model_call_attempted=False,
            )
            self.store._write_unlocked(CampaignAuditRecord(audit=audit, dispositions=[]))
            return audit
        if self.store.call_count(packet.campaign_id) >= self.config.max_calls_per_campaign:
            # This is a deterministic pre-call decision, not another audit
            # attempt. Do not persist one record per denied packet: doing so
            # would grow the store without bound and poison later budget raises.
            return self._failure_audit(
                packet,
                "budget_exhausted",
                "campaign auditor call budget exhausted",
                model_call_attempted=False,
            )

        prompt = _render_prompt(packet, self.config.prompt_version)
        if len(prompt) > self.config.max_input_chars:
            audit = self._failure_audit(
                packet,
                "failed",
                "bounded evidence packet exceeds max_input_chars",
                model_call_attempted=False,
            )
            self.store._write_unlocked(CampaignAuditRecord(audit=audit, dispositions=[]))
            return audit

        attempt_id = self.store._reserve_call_unlocked(
            campaign_id=packet.campaign_id,
            evidence_fingerprint=packet.fingerprint,
            configuration_fingerprint=configuration_fingerprint,
        )
        outcome = execute_auditor_call(
            self.client,
            model=self.config.model,
            prompt=prompt,
            max_tokens=self.config.max_output_tokens,
            timeout_seconds=self.config.timeout_seconds,
            cancellation_event=cancellation_event,
        )
        if outcome.response is not None:
            try:
                audit = self._completed_audit(
                    packet,
                    outcome.response,
                    outcome.latency_ms,
                    model_call_attempt_id=attempt_id,
                )
            except Exception as exc:
                input_tokens, output_tokens = _response_usage(outcome.response)
                audit = self._failure_audit(
                    packet,
                    "failed",
                    f"invalid auditor response: {type(exc).__name__}",
                    model_call_attempt_id=attempt_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=outcome.latency_ms,
                )
        else:
            if not outcome.model_call_attempted:
                self.store._release_call_unlocked(packet.campaign_id, attempt_id)
            audit = self._failure_audit(
                packet,
                outcome.failure_status or "failed",
                outcome.failure_reason or "auditor model call failed",
                model_call_attempted=outcome.model_call_attempted,
                model_call_attempt_id=attempt_id if outcome.model_call_attempted else None,
                latency_ms=outcome.latency_ms,
            )
        self.store._write_unlocked(CampaignAuditRecord(audit=audit, dispositions=[]))
        return audit

    def _completed_audit(
        self,
        packet: CampaignAuditEvidencePacket,
        response: AuditorModelResponse,
        latency_ms: int,
        *,
        model_call_attempt_id: str,
    ) -> CampaignAudit:
        parsed = json.loads(response.text)
        if not isinstance(parsed, dict):
            raise TypeError("auditor response must be an object")
        allowed_refs = {ref.uri for ref in packet.artifact_refs}
        llm_findings = [_parse_llm_finding(item, allowed_refs) for item in parsed.get("findings", [])]
        deterministic = detect_campaign_integrity_findings(packet)
        findings = _deduplicate_findings([*deterministic, *llm_findings])
        recommended_action = bounded_redacted_text(parsed.get("recommended_action"), 600) or "Continue deterministic protocol."
        input_tokens = _usage_int(response.usage, "input_tokens")
        output_tokens = _usage_int(response.usage, "output_tokens")
        return CampaignAudit(
            audit_id=stable_digest(
                {
                    "fingerprint": packet.fingerprint,
                    "configuration": stable_digest(self.config.to_dict()),
                }
            ),
            campaign_id=packet.campaign_id,
            checkpoint=packet.checkpoint,
            reviewed_at=datetime.now().astimezone().isoformat(),
            status="completed",
            evidence_fingerprint=packet.fingerprint,
            configuration_fingerprint=stable_digest(self.config.to_dict()),
            findings=findings,
            recommended_action=recommended_action,
            policy_outcome=_policy_outcome(findings, self.config.policy),
            provider=self.config.provider,
            model=self.config.model,
            prompt_version=self.config.prompt_version,
            auditor_route=self.config.auditor_route,
            proposer_routes=list(self.config.proposer_routes),
            route_distinct_from_proposer=self._route_distinct,
            model_call_attempted=True,
            model_call_attempt_id=model_call_attempt_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost=round(
                input_tokens * self.config.input_cost_per_million / 1_000_000
                + output_tokens * self.config.output_cost_per_million / 1_000_000,
                8,
            ),
            failure_reason=None,
        )

    def _failure_audit(
        self,
        packet: CampaignAuditEvidencePacket,
        status: Literal["timed_out", "canceled", "failed", "budget_exhausted"],
        reason: str,
        *,
        model_call_attempted: bool = True,
        model_call_attempt_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
    ) -> CampaignAudit:
        reviewed_at = datetime.now().astimezone().isoformat()
        configuration_fingerprint = stable_digest(self.config.to_dict())
        findings = detect_campaign_integrity_findings(packet)
        return CampaignAudit(
            audit_id=stable_digest(
                {
                    "fingerprint": packet.fingerprint,
                    "configuration": configuration_fingerprint,
                    "status": status,
                    "reviewed_at": reviewed_at,
                }
            ),
            campaign_id=packet.campaign_id,
            checkpoint=packet.checkpoint,
            reviewed_at=reviewed_at,
            status=status,
            evidence_fingerprint=packet.fingerprint,
            configuration_fingerprint=configuration_fingerprint,
            findings=findings,
            recommended_action="Deterministic monitoring remains authoritative; retry or request operator review.",
            policy_outcome=_policy_outcome(findings, self.config.policy),
            provider=self.config.provider,
            model=self.config.model,
            prompt_version=self.config.prompt_version,
            auditor_route=self.config.auditor_route,
            proposer_routes=list(self.config.proposer_routes),
            route_distinct_from_proposer=self._route_distinct,
            model_call_attempted=model_call_attempted,
            model_call_attempt_id=model_call_attempt_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost=round(
                input_tokens * self.config.input_cost_per_million / 1_000_000
                + output_tokens * self.config.output_cost_per_million / 1_000_000,
                8,
            ),
            failure_reason=reason,
        )


def detect_campaign_integrity_findings(packet: CampaignAuditEvidencePacket) -> list[CampaignAuditFinding]:
    findings: list[CampaignAuditFinding] = []
    metrics = packet.metric_summaries
    fallback_refs = [ref.uri for ref in packet.artifact_refs]
    cohorts = {metric.cohort for metric in metrics}
    if len(cohorts) > 1 or "non_comparable_cohorts" in packet.integrity_alerts:
        findings.append(
            _finding(
                "non_comparable_cohorts",
                "high",
                "Candidate evidence mixes non-comparable cohorts.",
                metrics,
                "Re-run incumbent and challenger in one matched cohort.",
                fallback_refs,
            )
        )
    epochs = (
        {item.evaluator_epoch for item in packet.bundle_lineage}
        | {item.evaluator_epoch for item in metrics}
        | {item.evaluator_epoch for item in packet.gate_decisions}
    )
    if len(epochs) > 1 or "evaluator_epoch_mismatch" in packet.integrity_alerts:
        findings.append(
            _finding(
                "evaluator_epoch_mismatch",
                "high",
                "Evidence spans more than one evaluator epoch.",
                metrics,
                "Re-evaluate compared candidates under one evaluator epoch.",
                fallback_refs,
            )
        )
    if "data_leakage" in packet.integrity_alerts:
        findings.append(
            _finding(
                "data_leakage",
                "critical",
                "An integrity alert reports possible held-out data leakage.",
                metrics,
                "Pause promotion and rotate the compromised held-out set.",
                fallback_refs,
            )
        )
    if "evidence_truncated" in packet.integrity_alerts:
        findings.append(
            _finding(
                "other",
                "high",
                "The bounded audit packet summarizes a larger evidence set.",
                metrics,
                "Require operator review of the durable campaign report before promotion.",
                fallback_refs,
            )
        )
    missing_reconstruction = [metric for metric in metrics if metric.reconstruction_ref is None]
    if missing_reconstruction and any(gate.decision in {"accepted", "promote", "kept"} for gate in packet.gate_decisions):
        findings.append(
            _finding(
                "missing_reconstruction_evidence",
                "high",
                "Promotion evidence is missing raw-trial reconstruction references.",
                missing_reconstruction,
                "Attach reconstructible raw trials before promotion.",
                fallback_refs,
            )
        )
    misclassified = [metric for metric in metrics if metric.infrastructure_error and metric.classification == "candidate_failure"]
    if misclassified:
        findings.append(
            _finding(
                "infrastructure_misclassification",
                "high",
                "Infrastructure failures were counted as candidate failures.",
                misclassified,
                "Reclassify the trials and repeat them without candidate penalty.",
                fallback_refs,
            )
        )
    unsupported = [
        gate
        for gate in packet.gate_decisions
        if ("caus" in gate.claim.lower() or gate.decision in {"promote", "accepted"})
        and gate.evidence_level in {"component_correlated", "unspecified"}
    ]
    if unsupported:
        allowed_refs = set(fallback_refs)
        refs = [gate.artifact_ref for gate in unsupported if gate.artifact_ref in allowed_refs] or fallback_refs
        findings.append(
            CampaignAuditFinding(
                finding_id=stable_digest({"category": "unsupported_causal_claim", "gates": [g.gate_id for g in unsupported]}),
                category="unsupported_causal_claim",
                severity="high",
                confidence=1.0,
                summary="A promotion or causal claim relies on correlational or unspecified evidence.",
                evidence_refs=refs,
                affected_branches=[],
                affected_trials=[],
                recommended_action="Require paired or causal evidence and correct the report language.",
                source="deterministic_preflight",
            )
        )
    experiment_ids: dict[str, set[str]] = defaultdict(set)
    for metric in metrics:
        if metric.experiment_id:
            experiment_ids[metric.candidate_digest].add(metric.experiment_id)
    repeated_digests = {digest for digest, experiments in experiment_ids.items() if len(experiments) >= 3}
    repeated = [metric for metric in metrics if metric.candidate_digest in repeated_digests]
    if repeated:
        findings.append(
            _finding(
                "repeated_unchanged_experiment",
                "medium",
                "The same candidate digest was evaluated repeatedly without a material change.",
                repeated,
                "Stop unchanged retries and require a differentiated hypothesis.",
                fallback_refs,
            )
        )
    return findings


def make_operator_disposition(
    audit: CampaignAudit,
    *,
    operator: str,
    disposition: Literal["accepted", "dismissed", "mitigated", "deferred"],
    rationale: str,
    recorded_at: str | None = None,
) -> CampaignAuditDisposition:
    timestamp = recorded_at or datetime.now().astimezone().isoformat()
    return CampaignAuditDisposition(
        disposition_id=stable_digest(
            {"audit_id": audit.audit_id, "operator": operator, "disposition": disposition, "recorded_at": timestamp}
        ),
        audit_id=audit.audit_id,
        operator=operator,
        disposition=disposition,
        rationale=rationale,
        recorded_at=timestamp,
    )


def _render_prompt(packet: CampaignAuditEvidencePacket, prompt_version: str) -> str:
    return "\n".join(
        [
            f"Campaign integrity audit ({prompt_version}).",
            "You are an independent, frozen, read-only reviewer.",
            "Deterministic monitors and evaluators are authoritative.",
            "Do not rewrite scores, context, active state, or promotion decisions.",
            "Cite only artifact URIs present in artifact_refs.",
            'Return JSON: {"findings": [...], "recommended_action": "..."}.',
            "Each finding requires category, severity, confidence, summary, evidence_refs,",
            "affected_branches, affected_trials, and recommended_action.",
            "Evidence packet:",
            canonical_json(packet.to_dict()),
        ]
    )


def _parse_llm_finding(value: Any, allowed_refs: set[str]) -> CampaignAuditFinding:
    if not isinstance(value, dict):
        raise TypeError("audit finding must be an object")
    payload = dict(value)
    payload["source"] = "llm"
    payload.setdefault("finding_id", stable_digest(payload))
    finding = CampaignAuditFinding.from_dict(payload)
    if not finding.evidence_refs:
        raise ValueError("auditor finding must cite evidence")
    if any(ref not in allowed_refs for ref in finding.evidence_refs):
        raise ValueError("auditor finding cites evidence outside the packet")
    return finding


def _finding(
    category: AuditCategory,
    severity: AuditSeverity,
    summary: str,
    metrics: list[AuditMetricSummary],
    recommended_action: str,
    fallback_refs: list[str],
) -> CampaignAuditFinding:
    allowed_refs = set(fallback_refs)
    reconstruction_refs = sorted({metric.reconstruction_ref for metric in metrics if metric.reconstruction_ref in allowed_refs})
    evidence_refs = reconstruction_refs or fallback_refs[:2]
    return CampaignAuditFinding(
        finding_id=stable_digest({"category": category, "trials": [metric.trial_id for metric in metrics]}),
        category=category,
        severity=severity,
        confidence=1.0,
        summary=summary,
        evidence_refs=evidence_refs,
        affected_branches=sorted({metric.branch_id for metric in metrics}),
        affected_trials=sorted({metric.trial_id for metric in metrics}),
        recommended_action=recommended_action,
        source="deterministic_preflight",
    )


def _deduplicate_findings(findings: list[CampaignAuditFinding]) -> list[CampaignAuditFinding]:
    result: list[CampaignAuditFinding] = []
    seen: set[tuple[AuditCategory, str]] = set()
    for finding in findings:
        key = (finding.category, finding.summary)
        if key not in seen:
            result.append(finding)
            seen.add(key)
    return result


def _policy_outcome(
    findings: list[CampaignAuditFinding],
    policy: Literal["advisory", "review_required_on_high", "pause_recommended_on_critical"],
) -> AuditPolicyOutcome:
    severities = {finding.severity for finding in findings}
    if policy == "pause_recommended_on_critical" and "critical" in severities:
        return "safe_pause_recommended"
    if policy in {"review_required_on_high", "pause_recommended_on_critical"} and severities & {
        "high",
        "critical",
    }:
        return "review_required"
    return "advisory"


def _usage_int(usage: Any, field: str) -> int:
    value = getattr(usage, field, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _response_usage(response: AuditorModelResponse) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    return _usage_int(usage, "input_tokens"), _usage_int(usage, "output_tokens")


__all__ = [
    "AuditBundleLineage",
    "AuditCategory",
    "AuditCheckpoint",
    "AuditEvidenceReference",
    "AuditGateDecision",
    "AuditMetricSummary",
    "AuditNegativeResult",
    "AuditPolicyOutcome",
    "AuditProtocolLane",
    "AuditSeverity",
    "AuditorCallHandle",
    "CancellableAuditorModelClient",
    "CampaignAudit",
    "CampaignAuditConfig",
    "CampaignAuditDisposition",
    "CampaignAuditEvidencePacket",
    "CampaignAuditFinding",
    "CampaignAuditRecord",
    "CampaignAuditStore",
    "CampaignAuditor",
    "build_campaign_audit_packet",
    "detect_campaign_integrity_findings",
    "make_operator_disposition",
]
