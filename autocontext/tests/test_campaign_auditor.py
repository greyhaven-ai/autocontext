from __future__ import annotations

import concurrent.futures
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPORT_FIXTURE = ROOT / "docs" / "campaign-mode-report-parity-fixture.json"


def _pid_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_recorded_process(pid_path: Path) -> None:
    try:
        process_id = int(pid_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return
    try:
        os.kill(process_id, signal.SIGKILL)
    except ProcessLookupError:
        pass


class _FakeClient:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        delay: float = 0.0,
        started_event: threading.Event | None = None,
        finished_event: threading.Event | None = None,
    ) -> None:
        self.payload = payload or {"findings": [], "recommended_action": "Continue."}
        self.delay = delay
        self.started_event = started_event
        self.finished_event = finished_event
        self.calls = 0
        self.prompts: list[str] = []
        self.worker_threads: list[threading.Thread] = []

    def generate(self, **kwargs: Any) -> Any:
        self.worker_threads.append(threading.current_thread())
        self.calls += 1
        self.prompts.append(kwargs["prompt"])
        if self.started_event is not None:
            self.started_event.set()
        try:
            if self.delay:
                time.sleep(self.delay)
            return SimpleNamespace(
                text=json.dumps(self.payload),
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )
        finally:
            if self.finished_event is not None:
                self.finished_event.set()

    def wait_for_worker_shutdown(
        self,
        *,
        expected_count: int = 1,
        timeout: float = 1.0,
    ) -> None:
        if self.finished_event is not None:
            assert self.finished_event.wait(timeout=timeout)
        deadline = time.monotonic() + timeout
        workers = tuple(self.worker_threads)
        assert len(workers) >= expected_count
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        assert not any(worker.is_alive() for worker in workers)


class _MalformedClient(_FakeClient):
    def generate(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.prompts.append(kwargs["prompt"])
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class _ThreadLeakingClient(_FakeClient):
    def generate(self, **kwargs: Any) -> Any:
        threading.Thread(target=time.sleep, args=(60.0,), daemon=False).start()
        return super().generate(**kwargs)


class _DescendantSpawningClient(_FakeClient):
    def __init__(self, pid_path: Path, *, block: bool = False) -> None:
        super().__init__()
        self.pid_path = pid_path
        self.block = block

    def generate(self, **kwargs: Any) -> Any:
        descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.pid_path.write_text(str(descendant.pid), encoding="utf-8")
        if self.block:
            time.sleep(60)
        return super().generate(**kwargs)


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


class _AmbiguousSubmissionClient:
    def __init__(self) -> None:
        self.calls = 0

    def start_generate(self, **kwargs: Any) -> Any:
        del kwargs
        self.calls += 1
        raise ConnectionError("response lost after provider submission")


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


def _packet_identity_in_child(output: Any) -> None:
    packet = _packet()
    output.put((packet.fingerprint, packet.boundary_digest))


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

    finished = threading.Event()
    client = _FakeClient(delay=0.05, finished_event=finished)
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
    client.wait_for_worker_shutdown()


def test_transient_timeout_is_not_reused_as_a_permanent_cache_entry(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    finished = threading.Event()
    client = _FakeClient(delay=0.05, finished_event=finished)
    auditor = CampaignAuditor(
        _config(timeout_seconds=0.001),
        client=client,
        store=CampaignAuditStore(tmp_path),
    )

    packet = _packet()
    first = auditor.review(packet)
    client.wait_for_worker_shutdown()
    finished.clear()
    second = auditor.review(packet)
    restored = auditor.store.read_by_fingerprint(packet.campaign_id, packet.fingerprint)

    assert first is not None and first.status == "timed_out"
    assert second is not None and second.status == "timed_out"
    assert client.calls == 2
    assert restored is not None and restored.audit == second
    client.wait_for_worker_shutdown(expected_count=2)


def test_legacy_evidence_lookup_prefers_completed_retry_after_timeout(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    config = _config(timeout_seconds=0.001)
    packet = _packet()

    finished = threading.Event()
    legacy_client = _FakeClient(delay=0.05, finished_event=finished)
    timed_out = CampaignAuditor(config, client=legacy_client, store=store).review(packet)
    legacy_client.wait_for_worker_shutdown()
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
    denied_packet_one = _packet(_report=_report().model_copy(update={"run_id": "run-denied-1"}))
    denied_packet_two = _packet(_report=_report().model_copy(update={"run_id": "run-denied-2"}))

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
    denied = auditor.review(_packet(_report=_report().model_copy(update={"run_id": "second-run"})))

    assert failed is not None and failed.status == "failed"
    assert failed.failure_reason == "invalid auditor response: AttributeError"
    assert failed.model_call_attempted is True
    assert failed.model_call_attempt_id
    assert failed.input_tokens == 100
    assert failed.output_tokens == 20
    assert denied is not None and denied.status == "budget_exhausted"
    assert client.calls == 1
    assert store.call_count(packet.campaign_id) == 1
    claims = list(tmp_path.rglob("attempts/*.json"))
    assert len(claims) == 1


def test_audit_record_remains_budget_fallback_when_attempt_claim_is_lost(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    client = _FakeClient()
    auditor = CampaignAuditor(_config(max_calls_per_campaign=1), client=client, store=store)
    packet = _packet()

    completed = auditor.review(packet)
    assert completed is not None and completed.model_call_attempt_id
    claim = next(
        path
        for path in tmp_path.rglob("attempts/*.json")
        if json.loads(path.read_text(encoding="utf-8"))["attempt_id"] == completed.model_call_attempt_id
    )
    claim.unlink()

    assert store.call_count(packet.campaign_id) == 1
    denied = auditor.review(_packet(_report=_report().model_copy(update={"run_id": "second-run"})))
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
    assert list(tmp_path.rglob("attempts/*.json")) == []

    completed = auditor.review(packet)
    assert completed is not None and completed.status == "completed"
    assert client.calls == 1
    assert store.call_count(packet.campaign_id) == 1


def test_failure_path_applies_configured_safe_pause_policy(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    finished = threading.Event()
    client = _FakeClient(delay=0.05, finished_event=finished)
    auditor = CampaignAuditor(
        _config(timeout_seconds=0.001, policy="pause_recommended_on_critical"),
        client=client,
        store=CampaignAuditStore(tmp_path),
    )

    audit = auditor.review(_packet(integrity_alerts=["Possible holdout answer leakage detected."]))

    assert audit is not None and audit.status == "timed_out"
    assert audit.policy_outcome == "safe_pause_recommended"
    client.wait_for_worker_shutdown()


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


def test_latest_operator_disposition_resolves_or_defers_cached_checkpoint(tmp_path: Path) -> None:
    from autocontext.audit.campaign_audit_checkpoints import CampaignAuditCheckpointRunner
    from autocontext.audit.campaign_auditor import (
        CampaignAuditor,
        CampaignAuditStore,
        make_operator_disposition,
    )

    metric = _packet().metric_summaries[0].model_copy(update={"evaluator_epoch": "wrong-epoch"})
    packet = _packet(metric_summaries=[metric.to_dict()])
    store = CampaignAuditStore(tmp_path)
    client = _FakeClient()
    auditor = CampaignAuditor(
        _config(policy="review_required_on_high"),
        client=client,
        store=store,
    )
    runner = CampaignAuditCheckpointRunner(auditor, lambda _checkpoint, _evidence: packet)

    held = runner.review_checkpoint("pre_promotion", {})
    assert held is not None and held.policy_outcome == "review_required"
    dismissed = make_operator_disposition(
        held,
        operator="operator@example.com",
        disposition="dismissed",
        rationale="The evaluator epoch alert was investigated and is a packet-label false positive.",
    )
    store.add_disposition(held.campaign_id, held.evidence_fingerprint, dismissed)

    resolved = runner.review_checkpoint("pre_promotion", {})
    assert resolved is not None and resolved.policy_outcome == "advisory"
    assert resolved.audit_id == held.audit_id
    assert client.calls == 1

    deferred = make_operator_disposition(
        held,
        operator="operator@example.com",
        disposition="deferred",
        rationale="Pause while a new evaluator contract is reviewed.",
    )
    store.add_disposition(held.campaign_id, held.evidence_fingerprint, deferred)
    paused = runner.review_checkpoint("pre_promotion", {})

    assert paused is not None and paused.policy_outcome == "safe_pause_recommended"
    assert client.calls == 1


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

    dispatched = threading.Event()
    finished = threading.Event()
    client = _FakeClient(delay=0.05, started_event=dispatched, finished_event=finished)
    store = CampaignAuditStore(tmp_path)
    auditor = CampaignAuditor(_config(), client=client, store=store)
    cancellation = threading.Event()

    def cancel_after_dispatch() -> None:
        if dispatched.wait(timeout=1.0):
            cancellation.set()

    canceler = threading.Thread(target=cancel_after_dispatch, daemon=True)
    canceler.start()

    canceled = auditor.review(_packet(), cancellation_event=cancellation)
    canceler.join(timeout=1.0)

    assert canceled is not None and canceled.status == "canceled"
    assert canceled.model_call_attempted is True
    assert not canceler.is_alive()
    assert store.call_count(canceled.campaign_id) == 1
    client.wait_for_worker_shutdown()


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
    assert store.count(canceled.campaign_id) == 1


def test_ambiguous_native_submission_failure_retains_durable_call_claim(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    client = _AmbiguousSubmissionClient()
    auditor = CampaignAuditor(_config(max_calls_per_campaign=1), client=client, store=store)

    failed = auditor.review(_packet())
    denied = auditor.review(_packet(_report=_report().model_copy(update={"run_id": "second-run"})))

    assert failed is not None and failed.status == "failed"
    assert failed.model_call_attempted is True
    assert failed.model_call_attempt_id is not None
    assert store.call_count(failed.campaign_id) == 1
    assert denied is not None and denied.status == "budget_exhausted"
    assert client.calls == 1


def test_complete_proposer_route_set_is_validated_persisted_and_fingerprinted(tmp_path: Path) -> None:
    from autocontext.audit import CampaignAuditRoute
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    matching_routes = [
        CampaignAuditRoute(provider="anthropic", model="coach"),
        CampaignAuditRoute(provider="openai", model="auditor-model"),
    ]
    with pytest.raises(ValueError, match="every proposer route"):
        CampaignAuditor(
            _config(provider="openai", model="auditor-model", proposer_routes=matching_routes),
            client=_FakeClient(),
            store=CampaignAuditStore(tmp_path / "rejected"),
        )

    store = CampaignAuditStore(tmp_path / "accepted")
    routes = [
        CampaignAuditRoute(provider="openai", model="architect"),
        CampaignAuditRoute(provider="anthropic", model="coach"),
        CampaignAuditRoute(provider="anthropic", model="coach"),
    ]
    first = CampaignAuditor(
        _config(proposer_routes=routes),
        client=_FakeClient(),
        store=store,
    ).review(_packet())
    second = CampaignAuditor(
        _config(proposer_routes=[*routes, CampaignAuditRoute(provider="google", model="researcher")]),
        client=_FakeClient(),
        store=store,
    ).review(_packet())

    assert first is not None and second is not None
    assert [(route.provider, route.model) for route in first.proposer_routes] == [
        ("anthropic", "coach"),
        ("openai", "architect"),
    ]
    assert first.route_distinct_from_proposer is True
    assert first.configuration_fingerprint != second.configuration_fingerprint


def test_boundary_seal_and_content_scan_reject_tampering_before_prompt(tmp_path: Path) -> None:
    from autocontext.audit.campaign_audit_boundary import evidence_boundary_digest
    from autocontext.audit.campaign_auditor import CampaignAuditEvidencePacket, CampaignAuditor, CampaignAuditStore

    client = _FakeClient()
    auditor = CampaignAuditor(_config(), client=client, store=CampaignAuditStore(tmp_path))
    packet = _packet()

    with pytest.raises(ValueError, match="boundary seal"):
        auditor.review(packet.model_copy(update={"run_id": "tampered"}))

    data = packet.to_dict()
    data["artifact_refs"][0]["uri"] = "artifact://HOLDOUT_ANSWER_42"
    data["boundary_digest"] = evidence_boundary_digest(data)
    forged = CampaignAuditEvidencePacket.from_dict(data)
    with pytest.raises(ValueError, match="boundary signature"):
        auditor.review(forged)

    holdout_packet = _packet(
        artifact_refs=[
            {
                "uri": "artifact://HOLDOUT_ANSWER_42",
                "digest": "sha256:artifact",
                "summary": "metadata only",
            }
        ]
    )
    with pytest.raises(ValueError, match="holdout answer"):
        auditor.review(holdout_packet)
    assert client.calls == 0


def test_evidence_fingerprint_is_stable_across_process_local_boundary_keys() -> None:
    packet = _packet()
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    process = context.Process(target=_packet_identity_in_child, args=(output,))
    process.start()
    try:
        child_fingerprint, child_seal = output.get(timeout=10)
    finally:
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0
    assert child_fingerprint == packet.fingerprint
    assert child_seal != packet.boundary_digest


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


def test_process_adapter_gives_synchronous_language_client_a_hard_cancel_boundary(tmp_path: Path) -> None:
    from autocontext.audit import build_cancellable_auditor_client
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    client = build_cancellable_auditor_client(_FakeClient(delay=1.0))
    auditor = CampaignAuditor(
        _config(allow_uncancellable_transport=False, timeout_seconds=0.01),
        client=client,
        store=CampaignAuditStore(tmp_path),
    )
    started = time.monotonic()

    audit = auditor.review(_packet())

    assert audit is not None and audit.status == "timed_out"
    assert audit.failure_reason == "auditor model call timed out and was canceled"
    assert time.monotonic() - started < 0.5


def test_process_adapter_reaps_child_after_response_with_live_client_thread() -> None:
    from autocontext.audit import build_cancellable_auditor_client
    from autocontext.audit.campaign_audit_clients import ProcessAuditorCallHandle

    client = build_cancellable_auditor_client(_ThreadLeakingClient())
    handle = client.start_generate(
        model="auditor-model",
        prompt="bounded evidence",
        max_tokens=64,
        temperature=0.0,
        role="campaign_auditor",
    )
    try:
        response = handle.result(timeout=1.0)

        assert isinstance(handle, ProcessAuditorCallHandle)
        assert response.text
        assert not handle.is_alive
    finally:
        handle.cancel()


@pytest.mark.skipif(os.name != "posix", reason="process-group isolation is POSIX-only")
def test_process_adapter_reaps_provider_descendant_after_response(tmp_path: Path) -> None:
    from autocontext.audit import build_cancellable_auditor_client

    pid_path = tmp_path / "descendant.pid"
    client = build_cancellable_auditor_client(_DescendantSpawningClient(pid_path))
    handle = client.start_generate(
        model="auditor-model",
        prompt="bounded evidence",
        max_tokens=64,
        temperature=0.0,
        role="campaign_auditor",
    )
    try:
        response = handle.result(timeout=2.0)
        descendant_pid = int(pid_path.read_text(encoding="utf-8"))

        assert response.text
        assert not handle.is_alive
        assert not _pid_exists(descendant_pid)
    finally:
        handle.cancel()
        _kill_recorded_process(pid_path)


@pytest.mark.skipif(os.name != "posix", reason="process-group isolation is POSIX-only")
def test_process_adapter_cancel_reaps_provider_descendant(tmp_path: Path) -> None:
    from autocontext.audit import build_cancellable_auditor_client

    pid_path = tmp_path / "descendant.pid"
    client = build_cancellable_auditor_client(_DescendantSpawningClient(pid_path, block=True))
    handle = client.start_generate(
        model="auditor-model",
        prompt="bounded evidence",
        max_tokens=64,
        temperature=0.0,
        role="campaign_auditor",
    )
    try:
        deadline = time.monotonic() + 2.0
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_path.exists()
        descendant_pid = int(pid_path.read_text(encoding="utf-8"))
        assert _pid_exists(descendant_pid)

        assert handle.cancel() is True
        assert not handle.is_alive
        assert not _pid_exists(descendant_pid)
    finally:
        handle.cancel()
        _kill_recorded_process(pid_path)


def test_process_adapter_fails_closed_before_dispatch_without_process_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.audit.campaign_audit_clients import ProcessCancellableAuditorModelClient
    from autocontext.audit.campaign_audit_transport import execute_auditor_call

    inner = _FakeClient()
    client = ProcessCancellableAuditorModelClient(inner)
    monkeypatch.setattr(
        "autocontext.audit.campaign_audit_clients._process_group_isolation_supported",
        lambda: False,
    )

    outcome = execute_auditor_call(
        client,
        model="auditor-model",
        prompt="bounded evidence",
        max_tokens=64,
        timeout_seconds=1.0,
        cancellation_event=None,
    )

    assert outcome.failure_status == "failed"
    assert outcome.model_call_attempted is False
    assert inner.calls == 0


def test_checkpoint_packet_factory_seals_context_and_scheduler_metadata() -> None:
    from autocontext.audit import CampaignAuditPacketIdentity, CampaignCheckpointPacketFactory
    from autocontext.audit.campaign_audit_boundary import validate_evidence_boundary

    factory = CampaignCheckpointPacketFactory(
        CampaignAuditPacketIdentity(
            campaign_id="campaign-1",
            run_id="run-1",
            scenario_name="othello",
            artifact_uri="artifact://campaign-1/checkpoint",
            evaluator_epoch="epoch-1",
            verifier_contract_ref="scenario:othello:v1",
        )
    )
    context_packet = factory(
        "pre_promotion",
        {
            "candidate": {
                "digest": "sha256:candidate",
                "parent_digest": "sha256:parent",
                "evaluator_epoch": "epoch-1",
                "components": [{"content": "HIDDEN ANSWER CONTENT MUST NOT CROSS"}],
            },
            "cohort": "cohort-1",
            "comparison": {"decision": "confirmed", "reason": "secret free text"},
            "trials": [
                {
                    "pair_key": "pair-1",
                    "candidate_digest": "sha256:candidate",
                    "cohort": "cohort-1",
                    "evaluator_epoch": "epoch-1",
                    "fixture_digest": "fixture-1",
                    "lane": "confirmation",
                    "candidate_score": 0.8,
                    "candidate_valid": True,
                }
            ],
        },
    )
    scheduler_packet = factory(
        "final_completion",
        {
            "campaign_id": "campaign-1",
            "jobs": [
                {
                    "job_id": "job-1",
                    "branch_id": "branch-a",
                    "status": "succeeded",
                    "scored_result": {
                        "outcome": "candidate_success",
                        "output_ref": "artifact://campaign-1/job-1",
                        "metadata": {"score": 1.0, "raw_output": "must not cross"},
                    },
                }
            ],
        },
    )

    validate_evidence_boundary(context_packet)
    validate_evidence_boundary(scheduler_packet)
    assert "HIDDEN ANSWER CONTENT MUST NOT CROSS" not in json.dumps(context_packet.to_dict())
    assert "must not cross" not in json.dumps(scheduler_packet.to_dict())
    assert context_packet.boundary_digest
    assert scheduler_packet.boundary_digest


def test_audit_store_maps_arbitrary_campaign_and_run_ids_without_changing_identity(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    report = _report().model_copy(update={"campaign_id": "org/team\\campaign", "run_id": "runs/2026\\08"})
    packet = _packet(_report=report)
    store = CampaignAuditStore(tmp_path)

    audit = CampaignAuditor(_config(), client=_FakeClient(), store=store).review(packet)

    assert audit is not None and audit.campaign_id == "org/team\\campaign"
    assert packet.run_id == "runs/2026\\08"
    assert store.count(packet.campaign_id) == 1
    restored = store.read_by_fingerprint(
        packet.campaign_id,
        packet.fingerprint,
        configuration_fingerprint=audit.configuration_fingerprint,
    )
    assert restored is not None and restored.audit == audit
    assert len([path for path in tmp_path.iterdir() if path.is_dir()]) == 1


def test_audit_store_migrates_legacy_paths_to_case_safe_digest_names(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    packet = _packet()
    audit = CampaignAuditor(_config(), client=_FakeClient(), store=store).review(packet)
    assert audit is not None and audit.model_call_attempt_id
    canonical_record = next(path for path in tmp_path.rglob("*.json") if "attempts" not in path.parts)
    canonical_claim = next(path for path in tmp_path.rglob("attempts/*.json"))
    legacy = tmp_path / packet.campaign_id
    (legacy / "attempts").mkdir(parents=True)
    legacy_record = legacy / "legacy-cache-fingerprint.json"
    legacy_claim = legacy / "attempts" / f"{audit.model_call_attempt_id}.json"
    canonical_record.replace(legacy_record)
    canonical_claim.replace(legacy_claim)

    restarted = CampaignAuditStore(tmp_path)
    assert restarted.read_by_fingerprint(packet.campaign_id, packet.fingerprint) is not None
    assert restarted.call_count(packet.campaign_id) == 1
    with restarted.campaign_lock(packet.campaign_id):
        pass

    assert not legacy_record.exists()
    assert not legacy_claim.exists()
    migrated_record = next(path for path in tmp_path.rglob("run-*.json"))
    migrated_claim = next(path for path in tmp_path.rglob("attempts/attempt-*.json"))
    assert migrated_record.parent.name.startswith("campaign-")
    assert migrated_claim.parent.parent == migrated_record.parent

    record = restarted.records(packet.campaign_id)[0]
    case_root = tmp_path / "case-identities"
    case_store = CampaignAuditStore(case_root)
    for campaign_id in ("Campaign", "campaign"):
        case_store.write(
            record.model_copy(
                update={
                    "audit": record.audit.model_copy(
                        update={"audit_id": f"audit-{campaign_id}", "campaign_id": campaign_id}
                    )
                }
            )
        )
    directories = sorted(path.name for path in case_root.iterdir() if path.is_dir())
    assert len(directories) == 2
    assert len(set(directories)) == 2
    assert all(name == name.lower() and name.startswith("campaign-") for name in directories)


def test_audit_store_rolling_migration_merges_newer_legacy_disposition(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import (
        CampaignAuditor,
        CampaignAuditStore,
        make_operator_disposition,
    )

    store = CampaignAuditStore(tmp_path)
    packet = _packet()
    audit = CampaignAuditor(_config(), client=_FakeClient(), store=store).review(packet)
    assert audit is not None
    canonical = store.records(packet.campaign_id)[0]
    assert canonical.dispositions == []
    newer = make_operator_disposition(
        audit,
        operator="rolling-upgrade@example.com",
        disposition="deferred",
        rationale="Preserve operator review written by the older coordinator.",
        recorded_at="2026-08-20T18:00:00Z",
    )
    legacy_directory = tmp_path / packet.campaign_id
    legacy_directory.mkdir(parents=True)
    legacy_path = legacy_directory / "legacy-cache.json"
    legacy_path.write_text(
        json.dumps(canonical.model_copy(update={"dispositions": [newer]}).to_dict()),
        encoding="utf-8",
    )

    with store.campaign_lock(packet.campaign_id):
        pass

    restored = store.read_by_fingerprint(packet.campaign_id, packet.fingerprint)
    assert restored is not None
    assert restored.dispositions == [newer]
    assert not legacy_path.exists()


def test_audit_store_rolling_migration_keeps_conflicting_legacy_record(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    store = CampaignAuditStore(tmp_path)
    packet = _packet()
    audit = CampaignAuditor(_config(), client=_FakeClient(), store=store).review(packet)
    assert audit is not None
    canonical = store.records(packet.campaign_id)[0]
    canonical_path = next(path for path in tmp_path.rglob("run-*.json"))
    conflicting = canonical.model_copy(
        update={
            "audit": canonical.audit.model_copy(update={"recommended_action": "Conflicting legacy payload."})
        }
    )
    legacy_directory = tmp_path / packet.campaign_id
    legacy_directory.mkdir(parents=True)
    legacy_path = legacy_directory / "legacy-conflict.json"
    legacy_path.write_text(json.dumps(conflicting.to_dict()), encoding="utf-8")

    with pytest.raises(ValueError, match="conflicts with legacy audit binding"):
        with store.campaign_lock(packet.campaign_id):
            pass

    assert legacy_path.exists()
    assert json.loads(canonical_path.read_text(encoding="utf-8")) == canonical.to_dict()


@pytest.mark.parametrize("invalid_source", ["canonical", "legacy"])
@pytest.mark.parametrize(
    ("defect", "error"),
    [("foreign", "foreign audit binding"), ("duplicate", "duplicate identity")],
)
def test_audit_store_migration_rejects_invalid_disposition_histories_without_deleting_legacy(
    tmp_path: Path,
    invalid_source: str,
    defect: str,
    error: str,
) -> None:
    from autocontext.audit.campaign_auditor import (
        CampaignAuditor,
        CampaignAuditStore,
        make_operator_disposition,
    )

    store = CampaignAuditStore(tmp_path)
    packet = _packet()
    audit = CampaignAuditor(_config(), client=_FakeClient(), store=store).review(packet)
    assert audit is not None
    base = store.records(packet.campaign_id)[0]
    disposition = make_operator_disposition(
        audit,
        operator="rolling-upgrade@example.com",
        disposition="dismissed",
        rationale="Invalid rolling-upgrade fixture.",
        recorded_at="2026-08-20T19:00:00Z",
    )
    invalid_history = (
        [disposition.model_copy(update={"audit_id": "foreign-audit"})]
        if defect == "foreign"
        else [disposition, disposition]
    )
    canonical = base.model_copy(update={"dispositions": invalid_history}) if invalid_source == "canonical" else base
    if invalid_source == "canonical":
        store.write(canonical)
    canonical_path = next(path for path in tmp_path.rglob("run-*.json"))
    canonical_payload = json.loads(canonical_path.read_text(encoding="utf-8"))
    legacy = base.model_copy(update={"dispositions": invalid_history}) if invalid_source == "legacy" else base
    legacy_directory = tmp_path / packet.campaign_id
    legacy_directory.mkdir(parents=True)
    legacy_path = legacy_directory / "legacy-invalid-dispositions.json"
    legacy_path.write_text(json.dumps(legacy.to_dict()), encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        with store.campaign_lock(packet.campaign_id):
            pass

    assert legacy_path.exists()
    assert json.loads(canonical_path.read_text(encoding="utf-8")) == canonical_payload


def test_route_independence_uses_provider_normalization(tmp_path: Path) -> None:
    from autocontext.audit import CampaignAuditRoute
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    with pytest.raises(ValueError, match="every proposer route"):
        CampaignAuditor(
            _config(
                provider=" OpenAI ",
                model=" auditor-model ",
                proposer_routes=[CampaignAuditRoute(provider="OPENAI", model="auditor-model")],
            ),
            client=_FakeClient(),
            store=CampaignAuditStore(tmp_path),
        )


def test_route_independence_uses_resolved_endpoint_identity_and_model(tmp_path: Path) -> None:
    from autocontext.audit import CampaignAuditRoute
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    auditor_route = CampaignAuditRoute.resolved(
        "openai",
        "shared-model",
        base_url="HTTPS://API.OPENAI.COM:443/v1/",
    )
    aliased_proposer = CampaignAuditRoute.resolved(
        "openai-compatible",
        "shared-model",
        base_url="https://api.openai.com/v1",
    )
    with pytest.raises(ValueError, match="every proposer route"):
        CampaignAuditor(
            _config(
                provider="openai",
                model="shared-model",
                auditor_route=auditor_route,
                proposer_routes=[aliased_proposer],
            ),
            client=_FakeClient(),
            store=CampaignAuditStore(tmp_path / "rejected"),
        )

    independent_proposer = CampaignAuditRoute.resolved(
        "openai-compatible",
        "shared-model",
        base_url="https://private.example.invalid/v1",
    )
    audit = CampaignAuditor(
        _config(
            provider="openai",
            model="shared-model",
            auditor_route=auditor_route,
            proposer_routes=[independent_proposer],
        ),
        client=_FakeClient(),
        store=CampaignAuditStore(tmp_path / "accepted"),
    ).review(_packet())

    assert audit is not None
    assert audit.auditor_route == auditor_route
    assert audit.route_distinct_from_proposer is True
    assert audit.auditor_route.backend_identity == "endpoint:https://api.openai.com/v1"
    assert "credential" not in json.dumps(audit.to_dict())

    alternate = CampaignAuditor(
        _config(
            provider="openai-compatible",
            model="shared-model",
            auditor_route=CampaignAuditRoute.resolved(
                "openai-compatible",
                "shared-model",
                base_url="https://independent.example.invalid/v1",
            ),
            proposer_routes=[independent_proposer],
        ),
        client=_FakeClient(),
        store=CampaignAuditStore(tmp_path / "alternate"),
    ).review(_packet())
    assert alternate is not None
    assert alternate.configuration_fingerprint != audit.configuration_fingerprint


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:password@example.invalid/v1",
        "https://example.invalid/v1?api_key=secret",
        "https://example.invalid/v1#secret",
    ],
)
def test_route_identity_rejects_credential_bearing_endpoint(endpoint: str) -> None:
    from autocontext.audit import CampaignAuditRoute

    with pytest.raises(ValueError, match="credentials, query, or fragment"):
        CampaignAuditRoute.resolved("openai-compatible", "model", base_url=endpoint)


def test_packet_builder_strips_signed_url_credentials_from_every_reference() -> None:
    from autocontext.audit.campaign_audit_boundary import validate_evidence_boundary

    secret_url = "https://user:password@example.invalid/evidence?access_token=secret&signature=sig#fragment"
    metric = _packet().metric_summaries[0].to_dict()
    metric["reconstruction_ref"] = secret_url
    gate = _packet().gate_decisions[0].to_dict()
    gate["artifact_ref"] = secret_url
    packet = _packet(
        metric_summaries=[metric],
        gate_decisions=[gate],
        negative_results=[
            {
                "result_id": "negative-1",
                "disposition": "failed",
                "reason": "provider error",
                "evidence_refs": [secret_url],
            }
        ],
        artifact_refs=[{"uri": secret_url, "digest": None, "summary": "secret URL"}],
    )

    validate_evidence_boundary(packet)
    serialized = json.dumps(packet.to_dict())
    assert "secret" not in serialized
    assert "signature" not in serialized
    assert "password" not in serialized
    assert packet.artifact_refs[0].uri == "https://example.invalid/evidence"
    assert packet.metric_summaries[0].reconstruction_ref == packet.artifact_refs[0].uri
    assert packet.gate_decisions[0].artifact_ref == packet.artifact_refs[0].uri
    assert packet.negative_results[0].evidence_refs == [packet.artifact_refs[0].uri]


def test_redacted_sensitive_identities_remain_distinct() -> None:
    first = _packet(_report=_report().model_copy(update={"campaign_id": "alice@example.com", "run_id": "alice@example.com"}))
    second = _packet(_report=_report().model_copy(update={"campaign_id": "bob@example.com", "run_id": "bob@example.com"}))

    assert first.campaign_id != second.campaign_id
    assert first.run_id != second.run_id
    assert "@" not in first.campaign_id + first.run_id + second.campaign_id + second.run_id
    assert first.fingerprint != second.fingerprint


def test_redacted_local_artifact_paths_retain_digest_backed_unique_identity() -> None:
    from autocontext.audit import CampaignAuditPacketIdentity, CampaignCheckpointPacketFactory

    def artifact_uri(email: str) -> str:
        packet = CampaignCheckpointPacketFactory(
            CampaignAuditPacketIdentity(
                campaign_id="campaign-1",
                run_id="run-1",
                scenario_name="othello",
                artifact_uri=f"/private/results/{email}/campaign.json?token=secret",
                artifact_digest="sha256:immutable-plan",
            )
        )("final_completion", {"campaign_id": "campaign-1", "status": "completed", "jobs": []})
        assert packet.artifact_refs[0].digest == "sha256:immutable-plan"
        return packet.artifact_refs[0].uri

    first = artifact_uri("alice@example.com")
    second = artifact_uri("bob@example.com")

    assert first.startswith("redacted-artifact:")
    assert second.startswith("redacted-artifact:")
    assert first != second
    assert "@" not in first + second


def test_truncated_packet_forces_high_severity_operator_review(tmp_path: Path) -> None:
    from autocontext.audit.campaign_auditor import CampaignAuditor, CampaignAuditStore

    template = _packet().metric_summaries[0].to_dict()
    metrics = [
        {
            **template,
            "trial_id": f"trial-{index}",
            "evaluator_epoch": "eval-8" if index == 100 else "eval-7",
        }
        for index in range(101)
    ]
    packet = _packet(metric_summaries=metrics, max_items_per_section=100)
    auditor = CampaignAuditor(
        _config(policy="review_required_on_high"),
        client=_FakeClient(),
        store=CampaignAuditStore(tmp_path),
    )

    audit = auditor.review(packet)

    assert audit is not None
    assert {"evidence_truncated", "evaluator_epoch_mismatch"} <= set(packet.integrity_alerts)
    assert len(packet.metric_summaries) == 100
    truncation = next(finding for finding in audit.findings if "bounded audit packet" in finding.summary)
    assert truncation.severity == "high"
    assert audit.policy_outcome == "review_required"


def test_checkpoint_factory_preserves_scheduler_provenance_and_terminal_classification() -> None:
    from autocontext.audit import CampaignAuditPacketIdentity, CampaignCheckpointPacketFactory
    from autocontext.audit.campaign_auditor import detect_campaign_integrity_findings

    factory = CampaignCheckpointPacketFactory(
        CampaignAuditPacketIdentity(
            campaign_id="campaign-1",
            run_id="run-1",
            scenario_name="othello",
            artifact_uri="https://example.invalid/campaign?access_token=secret",
            evaluator_epoch="epoch-default",
            verifier_contract_ref="contract-default",
        ),
        max_items_per_section=2,
    )
    packet = factory(
        "final_completion",
        {
            "campaign_id": "campaign-1",
            "status": "canceled",
            "jobs": [
                {
                    "job_id": "job-canceled",
                    "branch_id": "branch-a",
                    "cohort_id": "cohort-a",
                    "lane_id": "lane-a",
                    "fixture_digest": "fixture-a",
                    "evaluator_epoch": "epoch-1",
                    "verifier_contract_ref": "contract-a",
                    "seeds": ["1"],
                    "status": "canceled",
                },
                {
                    "job_id": "job-budget",
                    "branch_id": "branch-b",
                    "cohort_id": "cohort-b",
                    "lane_id": "lane-b",
                    "fixture_digest": "fixture-b",
                    "evaluator_epoch": "epoch-2",
                    "verifier_contract_ref": "contract-b",
                    "seeds": ["2"],
                    "status": "budget_exhausted",
                },
            ],
        },
    )

    assert [metric.classification for metric in packet.metric_summaries] == ["canceled", "budget_exhausted"]
    assert {metric.fixture_digest for metric in packet.metric_summaries} == {"fixture-a", "fixture-b"}
    assert {lane.lane_id for lane in packet.protocol_lanes} == {"lane-a", "lane-b"}
    assert {"evaluator_epoch_mismatch", "non_comparable_cohorts"} <= set(packet.integrity_alerts)
    assert packet.artifact_refs[0].uri == "https://example.invalid/campaign"
    allowed = {reference.uri for reference in packet.artifact_refs}
    assert all(set(finding.evidence_refs) <= allowed for finding in detect_campaign_integrity_findings(packet))


def test_process_client_construction_failure_proves_submission_never_started() -> None:
    from autocontext.audit.campaign_audit_clients import ProcessCancellableAuditorModelClient
    from autocontext.audit.campaign_audit_transport import execute_auditor_call

    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class BrokenContext:
        def __init__(self) -> None:
            self.parent = FakeConnection()
            self.child = FakeConnection()

        def Pipe(self, *, duplex: bool) -> tuple[FakeConnection, FakeConnection]:
            assert duplex in (False, True)
            return self.parent, self.child

        def Process(self, **kwargs: Any) -> Any:
            del kwargs
            raise OSError("process construction failed")

    client = ProcessCancellableAuditorModelClient(_FakeClient())
    context = BrokenContext()
    client._context = context

    outcome = execute_auditor_call(
        client,
        model="auditor-model",
        prompt="bounded evidence",
        max_tokens=64,
        timeout_seconds=1.0,
        cancellation_event=None,
    )

    assert outcome.failure_status == "failed"
    assert outcome.model_call_attempted is False
    assert context.parent.closed and context.child.closed


@pytest.mark.parametrize("failure_stage", ["pipe", "start"])
def test_process_client_other_startup_failures_prove_submission_never_started(
    failure_stage: str,
) -> None:
    from autocontext.audit.campaign_audit_clients import ProcessCancellableAuditorModelClient
    from autocontext.audit.campaign_audit_transport import execute_auditor_call

    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class BrokenProcess:
        pid = None

        def start(self) -> None:
            raise OSError("process start failed")

    class BrokenContext:
        def __init__(self) -> None:
            self.connections: list[FakeConnection] = []
            self.pipe_calls = 0

        def Pipe(self, *, duplex: bool) -> tuple[FakeConnection, FakeConnection]:
            del duplex
            self.pipe_calls += 1
            if failure_stage == "pipe" and self.pipe_calls == 2:
                raise OSError("control pipe construction failed")
            pair = (FakeConnection(), FakeConnection())
            self.connections.extend(pair)
            return pair

        def Process(self, **kwargs: Any) -> BrokenProcess:
            del kwargs
            return BrokenProcess()

    client = ProcessCancellableAuditorModelClient(_FakeClient())
    context = BrokenContext()
    client._context = context

    outcome = execute_auditor_call(
        client,
        model="auditor-model",
        prompt="bounded evidence",
        max_tokens=64,
        timeout_seconds=1.0,
        cancellation_event=None,
    )

    assert outcome.failure_status == "failed"
    assert outcome.model_call_attempted is False
    assert all(connection.closed for connection in context.connections)
