from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_weight_cpu_search import builder, files, forbid, rehash, repin

from autocontext.weight_evolution import cpu_execution_journal as journal
from autocontext.weight_evolution import cpu_execution_search as search
from autocontext.weight_evolution.cpu_execution_models import CpuExecutionUsage
from autocontext.weight_evolution.cpu_execution_outcomes import build_cpu_execution_contract
from autocontext.weight_evolution.cpu_failure_v2_record import build_cpu_failure_record_v2
from autocontext.weight_evolution.cpu_measured_models import CpuMeasuredBatchOutcome
from autocontext.weight_evolution.cpu_measured_transport import CpuProcessObservation
from autocontext.weight_evolution.cpu_runner import CpuRunnerError, _batch_digest
from autocontext.weight_evolution.cpu_search import load_cpu_search
from autocontext.weight_evolution.session import _read_record


def failed_measurement(requests: Any, execution: Any) -> CpuMeasuredBatchOutcome:
    batch = _batch_digest(requests, execution.native_runtime_digest)
    failure = build_cpu_failure_record_v2(execution.failure_contract, supervisor_digest=execution.measured_profile_digest,
        run_id="measured-batch", study_digest=batch, runtime_digest=execution.native_runtime_digest,
        terminal_claim=None, terminal_result=None, exception=CpuRunnerError("fixture measured preflight failure"),
        redact=tuple(request.prompt for request in requests))
    return CpuMeasuredBatchOutcome(contract=execution.failure_contract, profile_digest=execution.measured_profile_digest,
        native_runtime_digest=execution.native_runtime_digest, batch_digest=batch,
        request_digests=tuple(request.digest for request in requests), checkpoint_sha256=requests[0].checkpoint_sha256,
        checkpoint_size_bytes=requests[0].checkpoint_size_bytes, model_digest=requests[0].model.digest,
        status="failed", failure_phase="preflight", process=CpuProcessObservation(trigger="preflight-failed", dispatched=False),
        failure=failure)


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, ...]:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    builder().build_bundle(bundle)
    spec = load_cpu_search(bundle)
    execution = build_cpu_execution_contract(scope="search", design_digest=spec.digest, workflow_digest=spec.digest,
        workflow_implementation_digest=spec.implementation_digest, capacity=CpuExecutionUsage(**spec.required_usage))
    monkeypatch.setattr(search, "build_cpu_execution_contract", lambda **kwargs: execution)
    monkeypatch.setattr(journal.ExecutionJournal, "_current", lambda self: None)
    calls: list[Any] = []

    def measured(requests: Any, **kwargs: Any) -> CpuMeasuredBatchOutcome:
        calls.append(requests)
        return failed_measurement(requests, execution)

    monkeypatch.setattr(journal, "run_measured_cpu_batch", measured)
    return bundle, output, spec, execution, calls


def test_failed_boundary_retains_measurement_full_claim_and_native_projection(context: Any) -> None:
    bundle, output, spec, execution, calls = context
    report = search.run_cpu_execution_search(bundle, output_dir=output, execution=execution)
    assert len(calls) == 1 and report["status"] == "failed"
    assert report["terminal_claim"]["label"] == "C-baseline" and report["terminal_outcome"]["status"] == "failed"
    assert report["charged_usage"]["materializations"] == len(spec.candidates)
    assert report["charged_usage"]["inference_calls"] == len(calls[0])
    assert report["reserved_capacity"] == spec.required_usage and report["summary"] is None
    assert report["model_execution_performed"] is False
    row = report["observation_inventory"][-1]
    assert row["phase"] == "practice" and row["outcome_present"] and row["process_observation"]["dispatched"] is False
    assert (output / "projection" / "seals").is_dir() and not (output / "complete.json").exists()
    assert _read_record(output / "manifest.json")["kind"] == "measured-cpu-search-journal-v1"


def test_failed_search_resume_and_report_do_not_dispatch_or_write(context: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, output, _, execution, _ = context
    original = search.run_cpu_execution_search(bundle, output_dir=output, execution=execution)
    before = files(output)
    monkeypatch.setattr(journal, "run_measured_cpu_batch", forbid)
    monkeypatch.setattr(journal, "_immutable_json", forbid)
    monkeypatch.setattr(search, "_immutable_json", forbid)
    assert search.report_cpu_execution_search(bundle, output_dir=output, execution=execution) == original
    assert search.run_cpu_execution_search(bundle, output_dir=output, execution=execution) == original
    assert files(output) == before


def test_unreturned_boundary_stays_uncertain_and_fully_charged(context: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, output, _, execution, _ = context
    calls: list[bool] = []

    def lost(*args: Any, **kwargs: Any) -> Any:
        calls.append(True)
        raise OSError("fixture worker boundary returned no owned outcome")

    monkeypatch.setattr(journal, "run_measured_cpu_batch", lost)
    report = search.run_cpu_execution_search(bundle, output_dir=output, execution=execution)
    assert report["status"] == "uncertain" and report["terminal_outcome"] is report["terminal_result"] is None
    assert report["model_execution_performed"] is None
    assert report["charged_usage"]["inference_calls"] > 0
    assert report["observation_inventory"][-1]["process_observation"] is None
    before = files(output)
    monkeypatch.setattr(journal, "run_measured_cpu_batch", forbid)
    assert search.report_cpu_execution_search(bundle, output_dir=output, execution=execution) == report
    assert search.run_cpu_execution_search(bundle, output_dir=output, execution=execution) == report
    assert calls == [True] and files(output) == before


def test_saved_failed_outcome_reconciles_missing_result_without_rerun(context: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, output, _, execution, calls = context
    original = journal._immutable_json

    def interrupted(path: Path, value: dict[str, Any]) -> None:
        original(path, value)
        if path.parent.name == "outcomes" and value["operation_kind"] == "inference":
            raise KeyboardInterrupt("fixture after durable measured outcome")

    with monkeypatch.context() as patch:
        patch.setattr(journal, "_immutable_json", interrupted)
        with pytest.raises(KeyboardInterrupt):
            search.run_cpu_execution_search(bundle, output_dir=output, execution=execution)
    before = files(output)
    monkeypatch.setattr(journal, "run_measured_cpu_batch", forbid)
    report = search.report_cpu_execution_search(bundle, output_dir=output, execution=execution)
    assert report["status"] == "uncertain" and report["reconciliation_needed"]
    assert report["terminal_outcome"] is not None and report["terminal_result"] is None
    assert files(output) == before
    resumed = search.run_cpu_execution_search(bundle, output_dir=output, execution=execution)
    assert resumed["status"] == "failed" and resumed["terminal_result"] is not None and len(calls) == 1


def test_registry_corruption_blocks_runner_before_dispatch(context: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, output, _, execution, calls = context
    original = journal._immutable_json
    baseline = bundle / "base.safetensors"
    baseline_bytes = baseline.read_bytes()

    def corrupt_after_claim(path: Path, value: dict[str, Any]) -> None:
        original(path, value)
        if path.parent.name == "claims" and value["kind"] == "inference":
            baseline.write_bytes(baseline_bytes[:-1] + bytes([baseline_bytes[-1] ^ 1]))

    with monkeypatch.context() as patch:
        patch.setattr(journal, "_immutable_json", corrupt_after_claim)
        report = search.run_cpu_execution_search(bundle, output_dir=output, execution=execution)
    assert calls == [] and report["status"] == "uncertain" and report["terminal_outcome"] is None
    baseline.write_bytes(baseline_bytes)
    monkeypatch.setattr(journal, "run_measured_cpu_batch", forbid)
    assert search.report_cpu_execution_search(bundle, output_dir=output, execution=execution)["status"] == "uncertain"


@pytest.mark.parametrize("target", ["manifest", "claim", "outcome", "result"])
def test_prefix_tampering_is_rejected_before_later_work(context: Any, target: str) -> None:
    bundle, output, _, execution, calls = context
    search.run_cpu_execution_search(bundle, output_dir=output, execution=execution)
    path = output / "manifest.json" if target == "manifest" else output / (target + "s") / "00000000.json"
    raw = _read_record(path)
    raw["contract_digest"] = "0" * 64
    rehash(path, raw)
    with pytest.raises(ValueError):
        search.run_cpu_execution_search(bundle, output_dir=output, execution=execution)
    assert len(calls) == 1


def test_readonly_absent_report_creates_nothing(context: Any) -> None:
    bundle, output, _, execution, calls = context
    report = search.report_cpu_execution_search(bundle, output_dir=output, execution=execution)
    assert report["status"] == "absent" and not output.exists() and not calls


def test_alias_output_overlap_is_rejected_before_publication(context: Any, tmp_path: Path) -> None:
    bundle, _, _, execution, _ = context
    sibling = tmp_path / "alias"
    sibling.mkdir()
    before = files(bundle)
    with pytest.raises(ValueError, match="overlaps"):
        search.run_cpu_execution_search(bundle, output_dir=sibling / ".." / bundle.name, execution=execution)
    assert files(bundle) == before


@pytest.fixture(scope="module", params=["pass", "veto"])
def real_search(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, ...]:
    import json

    root = tmp_path_factory.mktemp("measured-search-" + request.param)
    bundle, output = root / "bundle", root / "output"
    builder().build_bundle(bundle)
    if request.param == "veto":
        raw = json.loads((bundle / "study.json").read_bytes())
        next(case for case in raw["cases"] if case["case_id"] == "practice-3")["expected"] = "one"
        repin(bundle, raw)
    result = search.run_cpu_execution_search(bundle, output_dir=output)
    return request.param, bundle, output, result


def test_real_measured_search_preserves_pass_and_veto_gates(real_search: Any) -> None:
    condition, _, output, report = real_search
    assert report["status"] == "completed" and report["model_execution_performed"] is True
    assert report["gpu_execution_deferred"] and not report["promotion_eligible"]
    summary = report["summary"]
    if condition == "pass":
        assert summary["selected_options"] == {"C": "c-hint-1", "W": "w-row-two-1", "D": "d-temperature-1"}
        assert summary["confirmation"]["status"] == "confirmed" and summary["audit_status"] == "completed"
        assert (output / "projection" / "reports" / "audit.json").exists()
        assert "audit" in {row["phase"] for row in report["observation_inventory"]}
    else:
        assert summary["selected_options"]["W"] is None and summary["confirmation"]["status"] == "vetoed"
        assert summary["audit_status"] == "withheld" and not (output / "projection" / "reports" / "audit.json").exists()
        assert "audit" not in {row["phase"] for row in report["observation_inventory"]}
    assert summary["restoration_verified"] == {"C": True, "W": True, "D": True}
    assert "restoration" in {row["phase"] for row in report["observation_inventory"]}


def test_real_completed_search_readonly_and_resume_preserve_all_bytes(real_search: Any,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    _, bundle, output, report = real_search
    before = files(output)
    monkeypatch.setattr(journal, "run_measured_cpu_batch", forbid)
    monkeypatch.setattr(journal.TensorCandidateRegistry, "materialize", forbid)
    monkeypatch.setattr(journal, "_immutable_json", forbid)
    monkeypatch.setattr(search, "_immutable_json", forbid)
    assert search.report_cpu_execution_search(bundle, output_dir=output) == report
    assert search.run_cpu_execution_search(bundle, output_dir=output) == report
    assert files(output) == before
