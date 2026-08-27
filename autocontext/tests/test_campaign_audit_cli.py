from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from typer.testing import CliRunner

from autocontext.audit import (
    CampaignAudit,
    CampaignAuditConfig,
    CampaignAuditor,
    CampaignAuditPacketIdentity,
    CampaignAuditStore,
    CampaignCheckpointPacketFactory,
)
from autocontext.audit.campaign_auditor import CampaignAuditRecord
from autocontext.cli import app


def _audit() -> CampaignAudit:
    return CampaignAudit(
        audit_id="audit-1",
        campaign_id="campaign-1",
        checkpoint="pre_promotion",
        reviewed_at="2026-08-20T12:00:00+00:00",
        status="completed",
        evidence_fingerprint="evidence-1",
        configuration_fingerprint="config-1",
        findings=[],
        recommended_action="Request operator review.",
        policy_outcome="review_required",
        provider="independent",
        model="auditor",
        prompt_version="campaign-auditor-v1",
        proposer_routes=[],
        route_distinct_from_proposer=True,
        model_call_attempted=True,
        model_call_attempt_id="attempt-1",
        input_tokens=10,
        output_tokens=5,
        latency_ms=20,
        estimated_cost=0.0,
        failure_reason=None,
    )


def test_campaign_audit_cli_lists_and_resolves_a_hold(tmp_path: Path) -> None:
    store = CampaignAuditStore(tmp_path)
    store.write(CampaignAuditRecord(audit=_audit(), dispositions=[]))
    runner = CliRunner()

    listed = runner.invoke(
        app,
        ["campaign", "audit", "list", "campaign-1", "--store-root", str(tmp_path), "--json"],
    )
    assert listed.exit_code == 0
    assert json.loads(listed.stdout)[0]["audit"]["audit_id"] == "audit-1"

    resolved = runner.invoke(
        app,
        [
            "campaign",
            "audit",
            "resolve",
            "campaign-1",
            "evidence-1",
            "--operator",
            "operator@example.com",
            "--disposition",
            "dismissed",
            "--rationale",
            "Verified false positive.",
            "--store-root",
            str(tmp_path),
        ],
    )

    assert resolved.exit_code == 0
    payload = json.loads(resolved.stdout)
    assert payload["audit_id"] == "audit-1"
    restored = store.read_by_fingerprint(
        "campaign-1",
        "evidence-1",
        configuration_fingerprint="config-1",
    )
    assert restored is not None
    assert restored.dispositions[-1].disposition == "dismissed"


def test_cli_raw_campaign_id_resolves_factory_redacted_durable_audit(tmp_path: Path) -> None:
    """The operator-facing raw identity and sealed packet identity share one store namespace."""

    raw_campaign_id = "sensitive.operator@example.com"
    packet = CampaignCheckpointPacketFactory(
        CampaignAuditPacketIdentity(
            campaign_id=raw_campaign_id,
            run_id=raw_campaign_id,
            scenario_name="othello",
            artifact_uri="artifact://campaign/plan",
            artifact_digest="sha256:immutable-plan",
        )
    )(
        "final_completion",
        {"campaign_id": raw_campaign_id, "status": "completed", "jobs": []},
    )

    class _Client:
        def generate(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                text=json.dumps({"findings": [], "recommended_action": "Continue."}),
                usage=SimpleNamespace(input_tokens=5, output_tokens=2),
            )

    store = CampaignAuditStore(tmp_path)
    audit = CampaignAuditor(
        CampaignAuditConfig(
            enabled=True,
            provider="deterministic",
            model="auditor",
            proposer_provider="deterministic",
            proposer_model="proposer",
            allow_uncancellable_transport=True,
        ),
        client=_Client(),
        store=store,
    ).review(packet)
    assert audit is not None
    assert audit.campaign_id != raw_campaign_id
    assert "@" not in audit.campaign_id

    runner = CliRunner()
    listed = runner.invoke(
        app,
        ["campaign", "audit", "list", raw_campaign_id, "--store-root", str(tmp_path), "--json"],
    )
    assert listed.exit_code == 0, listed.output
    listed_payload = json.loads(listed.stdout)
    assert listed_payload[0]["audit"]["audit_id"] == audit.audit_id

    resolved = runner.invoke(
        app,
        [
            "campaign",
            "audit",
            "resolve",
            raw_campaign_id,
            audit.evidence_fingerprint,
            "--operator",
            "operator",
            "--disposition",
            "accepted",
            "--rationale",
            "Reviewed sealed evidence.",
            "--store-root",
            str(tmp_path),
        ],
    )
    assert resolved.exit_code == 0, resolved.output
    restored = store.read_by_fingerprint(raw_campaign_id, audit.evidence_fingerprint)
    assert restored is not None
    assert restored.dispositions[-1].disposition == "accepted"
