from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPORT_FIXTURE = ROOT / "docs" / "campaign-mode-report-parity-fixture.json"


class _FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None, *, delay: float = 0.0) -> None:
        self.payload = payload or {"findings": [], "recommended_action": "Continue."}
        self.delay = delay
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.prompts.append(kwargs["prompt"])
        if self.delay:
            time.sleep(self.delay)
        return SimpleNamespace(
            text=json.dumps(self.payload),
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


def _report() -> Any:
    from autocontext.analytics.campaign_mode_report import CampaignModeReport

    fixture = json.loads(REPORT_FIXTURE.read_text(encoding="utf-8"))
    return CampaignModeReport.from_dict(fixture["cases"][0]["expected_report"])


def _packet(**overrides: Any) -> Any:
    from autocontext.audit.campaign_auditor import build_campaign_audit_packet

    values: dict[str, Any] = {
        "checkpoint": "pre_promotion",
        "bundle_lineage": [
            {"bundle_digest": "sha256:bundle", "parent_digest": None, "evaluator_epoch": "eval-7"}
        ],
        "metric_summaries": [
            {
                "trial_id": "trial-1",
                "branch_id": "branch-a",
                "candidate_digest": "sha256:candidate-a",
                "cohort": "cohort-a",
                "evaluator_epoch": "eval-7",
                "verifier_digest": "sha256:verifier",
                "fixture_digest": "sha256:fixture",
                "score": 0.8,
                "valid": True,
                "classification": "candidate_result",
                "infrastructure_error": False,
                "reconstruction_ref": "artifact://trials/1",
            }
        ],
        "gate_decisions": [
            {
                "gate_id": "gate-1",
                "decision": "continue",
                "claim": "Matched evidence remains under review.",
                "evidence_level": "paired_shadow",
                "evaluator_epoch": "eval-7",
                "cohort": "cohort-a",
                "artifact_ref": "artifact://gate/1",
            }
        ],
        "negative_results": [],
        "artifact_refs": [
            {"uri": "artifact://trials/1", "digest": "sha256:trials", "summary": "Raw matched trials."},
            {"uri": "artifact://gate/1", "digest": "sha256:gate", "summary": "Gate decision."},
        ],
        "integrity_alerts": [],
    }
    report = overrides.pop("_report", _report())
    values.update(overrides)
    return build_campaign_audit_packet(report, **values)


def _config(**overrides: Any) -> Any:
    from autocontext.audit.campaign_auditor import CampaignAuditConfig

    values = {
        "enabled": True,
        "provider": "openai",
        "model": "auditor-model",
        "proposer_provider": "anthropic",
        "proposer_model": "proposer-model",
    }
    values.update(overrides)
    return CampaignAuditConfig(**values)


def test_valid_campaign_is_read_only_scoped_and_cached(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    report = _report()
    secret_lane = report.eval_lanes[0].model_copy(update={"holdout_refs": ["answer-key-secret"]})
    report = report.model_copy(update={"eval_lanes": [secret_lane]})
    secret = "sk-ant-abcdefghijklmnopqrstuvwxyz123456"
    secret_lane = secret_lane.model_copy(update={"verifier_contract_ref": f"verifier {secret}"})
    report = report.model_copy(update={"eval_lanes": [secret_lane]})
    packet = _packet(_report=report)
    client = _FakeClient()
    auditor = CampaignAuditor(_config(), client=client, store=CampaignAuditStore(tmp_path / "audits"))

    first = auditor.review(packet)
    second = auditor.review(packet)

    assert first is not None and first.status == "completed"
    assert first.findings == []
    assert first.policy_outcome == "advisory"
    assert first.frozen_non_trainable is True
    assert first.route_distinct_from_proposer is True
    assert client.calls == 1
    assert second == first
    prompt = client.prompts[0]
    assert "read_only" in prompt
    assert "answer-key-secret" not in prompt
    assert secret not in prompt


def test_route_must_be_independent_without_explicit_override(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    with pytest.raises(ValueError, match="must differ"):
        CampaignAuditor(
            _config(provider="anthropic", model="proposer-model"),
            client=_FakeClient(),
            store=CampaignAuditStore(tmp_path),
        )


def test_evaluator_mismatch_and_unsupported_promotion_claim_require_review(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    metric = _packet().metric_summaries[0].model_copy(update={"evaluator_epoch": "eval-8"})
    gate = _packet().gate_decisions[0].model_copy(
        update={
            "decision": "promote",
            "claim": "The prompt edit causally improved score.",
            "evidence_level": "component_correlated",
        }
    )
    packet = _packet(metric_summaries=[metric.to_dict()], gate_decisions=[gate.to_dict()])
    auditor = CampaignAuditor(
        _config(policy="review_required_on_high"),
        client=_FakeClient(),
        store=CampaignAuditStore(tmp_path),
    )

    audit = auditor.review(packet)

    assert audit is not None
    assert {finding.category for finding in audit.findings} >= {
        "evaluator_epoch_mismatch",
        "unsupported_causal_claim",
    }
    assert audit.policy_outcome == "review_required"
    assert all(finding.evidence_refs for finding in audit.findings)


def test_leakage_and_infrastructure_misclassification_are_detected(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    metric = _packet().metric_summaries[0].model_copy(
        update={"classification": "candidate_failure", "infrastructure_error": True, "valid": False}
    )
    packet = _packet(
        checkpoint="integrity_alert",
        metric_summaries=[metric.to_dict()],
        integrity_alerts=["Possible holdout answer leakage detected."],
    )
    auditor = CampaignAuditor(
        _config(policy="pause_recommended_on_critical"),
        client=_FakeClient(),
        store=CampaignAuditStore(tmp_path),
    )

    audit = auditor.review(packet)

    assert audit is not None
    assert {finding.category for finding in audit.findings} >= {
        "data_leakage",
        "infrastructure_misclassification",
    }
    assert audit.policy_outcome == "safe_pause_recommended"


def test_timeout_falls_back_without_changing_deterministic_state(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    client = _FakeClient(delay=0.05)
    auditor = CampaignAuditor(
        _config(timeout_seconds=0.001),
        client=client,
        store=CampaignAuditStore(tmp_path),
    )
    deterministic_state = {"score": 0.8, "active_bundle": "sha256:bundle"}

    audit = auditor.review(_packet())

    assert audit is not None and audit.status == "timed_out"
    assert audit.policy_outcome == "advisory"
    assert deterministic_state == {"score": 0.8, "active_bundle": "sha256:bundle"}


def test_operator_disposition_is_persisted_separately(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import (
        CampaignAuditor,
        CampaignAuditStore,
        make_operator_disposition,
    )

    store = CampaignAuditStore(tmp_path)
    audit = CampaignAuditor(_config(), client=_FakeClient(), store=store).review(_packet())
    assert audit is not None
    disposition = make_operator_disposition(
        audit,
        operator="operator@example.com",
        disposition="dismissed",
        rationale="Reviewed the cited raw trials; this was a false positive.",
        recorded_at="2026-08-17T12:00:00Z",
    )

    updated = store.add_disposition(audit.campaign_id, audit.evidence_fingerprint, disposition)
    restored = store.read_by_fingerprint(audit.campaign_id, audit.evidence_fingerprint)

    assert updated.audit == audit
    assert restored is not None and restored.dispositions == [disposition]


def test_disabled_auditor_spends_no_call(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    client = _FakeClient()
    auditor = CampaignAuditor(_config(enabled=False), client=client, store=CampaignAuditStore(tmp_path))

    assert auditor.review(_packet()) is None
    assert client.calls == 0
