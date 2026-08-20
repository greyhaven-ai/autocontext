from __future__ import annotations

import concurrent.futures
import json
import threading
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


class _MalformedClient(_FakeClient):
    def generate(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.prompts.append(kwargs["prompt"])
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class _CancellableHandle:
    def __init__(self) -> None:
        self._canceled = threading.Event()
        self._finished = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        self._canceled.wait()
        self._finished.set()

    def result(self, timeout: float) -> Any:
        if not self._finished.wait(timeout):
            raise concurrent.futures.TimeoutError
        raise RuntimeError("canceled call has no response")

    def cancel(self) -> bool:
        self._canceled.set()
        self.thread.join(timeout=1.0)
        return not self.thread.is_alive()


class _CancellableClient:
    def __init__(self) -> None:
        self.handle: _CancellableHandle | None = None

    def start_generate(self, **kwargs: Any) -> _CancellableHandle:
        del kwargs
        self.handle = _CancellableHandle()
        return self.handle


def _report() -> Any:
    from autocontext.analytics.campaign_mode_report import CampaignModeReport

    fixture = json.loads(REPORT_FIXTURE.read_text(encoding="utf-8"))
    return CampaignModeReport.from_dict(fixture["cases"][0]["expected_report"])


def _packet(**overrides: Any) -> Any:
    from autocontext.audit.campaign_auditor import build_campaign_audit_packet

    values: dict[str, Any] = {
        "checkpoint": "pre_promotion",
        "bundle_lineage": [{"bundle_digest": "sha256:bundle", "parent_digest": None, "evaluator_epoch": "eval-7"}],
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
        "hidden_holdout_answers_included": False,
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
        "allow_uncancellable_transport": True,
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
    gate = (
        _packet()
        .gate_decisions[0]
        .model_copy(
            update={
                "decision": "promote",
                "claim": "The prompt edit causally improved score.",
                "evidence_level": "component_correlated",
            }
        )
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

    metric = (
        _packet()
        .metric_summaries[0]
        .model_copy(update={"classification": "candidate_failure", "infrastructure_error": True, "valid": False})
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


def test_transient_timeout_is_not_reused_as_a_permanent_cache_entry(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    client = _FakeClient(delay=0.05)
    auditor = CampaignAuditor(
        _config(timeout_seconds=0.001),
        client=client,
        store=CampaignAuditStore(tmp_path),
    )

    packet = _packet()
    first = auditor.review(packet)
    second = auditor.review(packet)
    restored = auditor.store.read_by_fingerprint(packet.campaign_id, packet.fingerprint)

    assert first is not None and first.status == "timed_out"
    assert second is not None and second.status == "timed_out"
    assert client.calls == 2
    assert restored is not None and restored.audit == second


def test_legacy_evidence_lookup_prefers_completed_retry_after_timeout(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    config = _config(timeout_seconds=0.001)
    packet = _packet()

    timed_out = CampaignAuditor(config, client=_FakeClient(delay=0.05), store=store).review(packet)
    completed = CampaignAuditor(config, client=_FakeClient(), store=store).review(packet)
    restored = store.read_by_fingerprint(packet.campaign_id, packet.fingerprint)

    assert timed_out is not None and timed_out.status == "timed_out"
    assert completed is not None and completed.status == "completed"
    assert restored is not None and restored.audit == completed


def test_cache_identity_includes_effective_auditor_configuration(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    first_client = _FakeClient()
    second_client = _FakeClient()

    first = CampaignAuditor(_config(prompt_version="auditor-v1"), client=first_client, store=store).review(_packet())
    time.sleep(0.002)
    second = CampaignAuditor(
        _config(prompt_version="auditor-v2", policy="pause_recommended_on_critical"),
        client=second_client,
        store=store,
    ).review(_packet())
    restored = store.read_by_fingerprint(_packet().campaign_id, _packet().fingerprint)

    assert first is not None and second is not None
    assert first.audit_id != second.audit_id
    assert first.configuration_fingerprint != second.configuration_fingerprint
    assert first_client.calls == 1
    assert second_client.calls == 1
    assert restored is not None and restored.audit == second


def test_concurrent_reviews_share_one_atomic_budget_claim(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    client = _FakeClient(delay=0.05)
    auditor = CampaignAuditor(
        _config(max_calls_per_campaign=1),
        client=client,
        store=CampaignAuditStore(tmp_path),
    )
    packet = _packet()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: auditor.review(packet), range(2)))

    assert client.calls == 1
    assert results[0] is not None and results[0].status == "completed"
    assert results[1] == results[0]


def test_budget_counts_only_model_attempts_and_denials_do_not_grow_store(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAudit, CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    first_client = _FakeClient()
    limited = CampaignAuditor(_config(max_calls_per_campaign=1), client=first_client, store=store)
    packet = _packet()
    denied_packet_one = packet.model_copy(update={"run_id": "run-denied-1"})
    denied_packet_two = packet.model_copy(update={"run_id": "run-denied-2"})

    first = limited.review(packet)
    denied_one = limited.review(denied_packet_one)
    denied_two = limited.review(denied_packet_two)

    assert first is not None and first.status == "completed"
    assert denied_one is not None and denied_one.status == "budget_exhausted"
    assert denied_two is not None and denied_two.status == "budget_exhausted"
    assert denied_one.model_call_attempted is False
    assert denied_two.model_call_attempted is False
    legacy_denial = denied_one.to_dict()
    del legacy_denial["model_call_attempted"]
    assert CampaignAudit.from_dict(legacy_denial).model_call_attempted is False
    assert first_client.calls == 1
    assert store.call_count(first.campaign_id) == 1
    assert store.count(first.campaign_id) == 1

    raised_client = _FakeClient()
    raised = CampaignAuditor(_config(max_calls_per_campaign=2), client=raised_client, store=store)
    after_raise = raised.review(denied_packet_one)

    assert after_raise is not None and after_raise.status == "completed"
    assert after_raise.model_call_attempted is True
    assert raised_client.calls == 1
    assert store.call_count(first.campaign_id) == 2


def test_malformed_response_keeps_durable_attempt_claim_and_exhausts_budget(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    client = _MalformedClient()
    auditor = CampaignAuditor(_config(max_calls_per_campaign=1), client=client, store=store)
    packet = _packet()

    failed = auditor.review(packet)
    denied = auditor.review(packet.model_copy(update={"run_id": "second-run"}))

    assert failed is not None and failed.status == "failed"
    assert failed.failure_reason == "invalid auditor response: AttributeError"
    assert failed.model_call_attempted is True
    assert failed.model_call_attempt_id
    assert denied is not None and denied.status == "budget_exhausted"
    assert client.calls == 1
    assert store.call_count(packet.campaign_id) == 1
    claims = list((tmp_path / packet.campaign_id / "attempts").glob("*.json"))
    assert len(claims) == 1


def test_audit_record_remains_budget_fallback_when_attempt_claim_is_lost(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    client = _FakeClient()
    auditor = CampaignAuditor(_config(max_calls_per_campaign=1), client=client, store=store)
    packet = _packet()

    completed = auditor.review(packet)
    assert completed is not None and completed.model_call_attempt_id
    claim = tmp_path / packet.campaign_id / "attempts" / f"{completed.model_call_attempt_id}.json"
    claim.unlink()

    assert store.call_count(packet.campaign_id) == 1
    denied = auditor.review(packet.model_copy(update={"run_id": "second-run"}))
    assert denied is not None and denied.status == "budget_exhausted"
    assert client.calls == 1


def test_local_submission_failure_releases_pre_dispatch_budget_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    client = _FakeClient()
    auditor = CampaignAuditor(_config(max_calls_per_campaign=1), client=client, store=store)
    packet = _packet()

    with monkeypatch.context() as scoped:
        scoped.setattr(
            concurrent.futures.ThreadPoolExecutor,
            "submit",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("local submit failed")),
        )
        failed = auditor.review(packet)

    assert failed is not None and failed.status == "failed"
    assert failed.model_call_attempted is False
    assert failed.model_call_attempt_id is None
    assert client.calls == 0
    assert store.call_count(packet.campaign_id) == 0
    assert list((tmp_path / packet.campaign_id / "attempts").glob("*.json")) == []

    completed = auditor.review(packet)
    assert completed is not None and completed.status == "completed"
    assert client.calls == 1
    assert store.call_count(packet.campaign_id) == 1


def test_failure_path_applies_configured_safe_pause_policy(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    auditor = CampaignAuditor(
        _config(timeout_seconds=0.001, policy="pause_recommended_on_critical"),
        client=_FakeClient(delay=0.05),
        store=CampaignAuditStore(tmp_path),
    )

    audit = auditor.review(_packet(integrity_alerts=["Possible holdout answer leakage detected."]))

    assert audit is not None and audit.status == "timed_out"
    assert audit.policy_outcome == "safe_pause_recommended"


def test_packet_strips_free_text_and_refuses_declared_holdout_answers(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    client = _FakeClient()
    auditor = CampaignAuditor(_config(), client=client, store=CampaignAuditStore(tmp_path))
    packet = _packet(
        artifact_refs=[{"uri": "artifact://trials/1", "digest": "sha256:trials", "summary": "Hidden holdout answer: 42"}]
    )

    assert auditor.review(packet) is not None
    assert "Hidden holdout answer: 42" not in client.prompts[0]

    unsafe = packet.model_copy(update={"hidden_holdout_answers_included": True})
    with pytest.raises(ValueError, match="hidden holdout answers"):
        auditor.review(unsafe)


def test_replicate_measurements_are_not_repeated_experiments_without_submission_ids() -> None:
    from autocontext.audit.campaign_auditor import detect_campaign_integrity_findings

    base = _packet().metric_summaries[0]
    replicates = [
        base.model_copy(update={"trial_id": f"trial-{index}", "fixture_digest": f"fixture-{index}"}).to_dict()
        for index in range(3)
    ]
    packet = _packet(metric_summaries=replicates)

    assert "repeated_unchanged_experiment" not in {finding.category for finding in detect_campaign_integrity_findings(packet)}

    repeated_submissions = [
        base.model_copy(update={"trial_id": f"trial-{index}", "experiment_id": f"experiment-{index}"}).to_dict()
        for index in range(3)
    ]
    packet = _packet(metric_summaries=repeated_submissions)
    assert "repeated_unchanged_experiment" in {finding.category for finding in detect_campaign_integrity_findings(packet)}


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


def test_concurrent_operator_dispositions_do_not_lose_updates(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import (
        CampaignAuditor,
        CampaignAuditStore,
        make_operator_disposition,
    )

    store = CampaignAuditStore(tmp_path)
    audit = CampaignAuditor(_config(), client=_FakeClient(), store=store).review(_packet())
    assert audit is not None
    dispositions = [
        make_operator_disposition(
            audit,
            operator=f"operator-{index}@example.com",
            disposition="deferred",
            rationale=f"Follow-up {index}",
            recorded_at=f"2026-08-17T12:00:0{index}Z",
        )
        for index in range(2)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda disposition: store.add_disposition(
                    audit.campaign_id,
                    audit.evidence_fingerprint,
                    disposition,
                ),
                dispositions,
            )
        )

    restored = store.read_by_fingerprint(audit.campaign_id, audit.evidence_fingerprint)
    assert restored is not None
    assert {item.disposition_id for item in restored.dispositions} == {item.disposition_id for item in dispositions}


def test_disabled_auditor_spends_no_call(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    client = _FakeClient()
    auditor = CampaignAuditor(_config(enabled=False), client=client, store=CampaignAuditStore(tmp_path))

    assert auditor.review(_packet()) is None
    assert client.calls == 0


def test_audit_checkpoint_cancellation_is_terminal_for_that_evidence_decision(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    client = _FakeClient(delay=0.05)
    store = CampaignAuditStore(tmp_path)
    auditor = CampaignAuditor(_config(), client=client, store=store)
    cancellation = threading.Event()
    timer = threading.Timer(0.005, cancellation.set)
    timer.start()
    started = time.monotonic()

    canceled = auditor.review(_packet(), cancellation_event=cancellation)
    elapsed = time.monotonic() - started
    timer.join(timeout=1)

    assert canceled is not None and canceled.status == "canceled"
    assert canceled.model_call_attempted is True
    assert elapsed < 0.05
    assert store.call_count(canceled.campaign_id) == 1


def test_pre_dispatch_cancellation_spends_no_audit_budget(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    client = _FakeClient()
    store = CampaignAuditStore(tmp_path)
    auditor = CampaignAuditor(_config(), client=client, store=store)
    cancellation = threading.Event()
    cancellation.set()

    canceled = auditor.review(_packet(), cancellation_event=cancellation)

    assert canceled is not None and canceled.status == "canceled"
    assert canceled.model_call_attempted is False
    assert client.calls == 0
    assert store.call_count(canceled.campaign_id) == 0


def test_production_auditor_requires_and_uses_terminable_transport(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    with pytest.raises(ValueError, match="requires a cancellable transport"):
        CampaignAuditor(
            _config(allow_uncancellable_transport=False),
            client=_FakeClient(),
            store=CampaignAuditStore(tmp_path / "legacy"),
        )

    client = _CancellableClient()
    auditor = CampaignAuditor(
        _config(allow_uncancellable_transport=False, timeout_seconds=0.005),
        client=client,
        store=CampaignAuditStore(tmp_path / "cancellable"),
    )

    audit = auditor.review(_packet())

    assert audit is not None and audit.status == "timed_out"
    assert audit.failure_reason == "auditor model call timed out and was canceled"
    assert client.handle is not None
    assert not client.handle.thread.is_alive()
