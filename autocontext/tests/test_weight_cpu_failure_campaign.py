"""Real CPU supervisor evidence plus deterministic publication and tamper cases."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_diagnostic_reference, cpu_failure_campaign, cpu_failure_diagnostic, cpu_search
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_campaign import _inventory
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignRepeat
from autocontext.weight_evolution.cpu_control import control_implementation_digest
from autocontext.weight_evolution.cpu_failure_campaign import (
    build_cpu_failure_campaign,
    failure_implementation_digest,
    report_cpu_failure_campaign,
    run_cpu_failure_campaign,
    validate_cpu_failure_campaign,
)
from autocontext.weight_evolution.cpu_failure_diagnostic import (
    build_cpu_failure_diagnostic_plan,
    report_cpu_failure_diagnostic,
    run_cpu_failure_diagnostic,
    validate_cpu_failure_diagnostic,
)
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
from autocontext.weight_evolution.cpu_runner import CpuRunnerError, runtime_identity
from autocontext.weight_evolution.cpu_search import load_cpu_search, search_implementation_digest
from autocontext.weight_evolution.session import _read_record

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU supervisor requires POSIX limits")


@dataclass(frozen=True)
class Source:
    bundle: Path
    output: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class Diagnostic:
    source: Source
    plan: Path
    output: Path
    report: dict[str, Any]


# The diagnostic module imports these fixtures. Cache actual retained runs even
# when pytest creates separate module-local FixtureDefs for an imported fixture.
_sources: dict[str, Source] = {}
_diagnostics: dict[str, Diagnostic] = {}


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("retained supervisor evidence dispatched, materialized, or wrote new work")


def rehash(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload)}))


def small_bundle(root: Path) -> Path:
    bundle = root / "bundle"
    build_cpu_failure_campaign(
        bundle, preset="fixture", campaign_id="cpu-failure-integration-v1", instances_per_family=1, cases_per_role=4,
        repeats=(CpuCampaignRepeat(repeat_id="r0", inference_seed=406, search_order_seed=701),),
    )
    return bundle


def copied(source: Source, root: Path) -> Source:
    return Source(Path(shutil.copytree(source.bundle, root / "bundle")),
                  Path(shutil.copytree(source.output, root / "campaign")), source.report)


@pytest.fixture(scope="session")
def source(tmp_path_factory: pytest.TempPathFactory) -> Source:
    if "complete" in _sources:
        return _sources["complete"]
    root = tmp_path_factory.mktemp("cpu-failure-source")
    bundle, output = small_bundle(root), root / "output"
    before = files(bundle)
    result = run_cpu_failure_campaign(bundle, output_dir=output)
    assert files(bundle) == before
    _sources["complete"] = Source(bundle, output, result)
    return _sources["complete"]


@pytest.fixture(scope="session")
def failed_source(tmp_path_factory: pytest.TempPathFactory) -> Source:
    if "failed" in _sources:
        return _sources["failed"]
    root = tmp_path_factory.mktemp("cpu-failure-observed-source")
    bundle, output = small_bundle(root), root / "output"
    original = cpu_search.run_cpu_batch
    failures = 0

    def fail_one_confirmation(requests: tuple[CpuInferenceRequest, ...], **kwargs: Any) -> Any:
        nonlocal failures
        if failures == 0 and requests[0].request_id.startswith("confirmation-CW."):
            failures += 1
            try:
                raise RuntimeError("deterministic underlying diagnostic")
            except RuntimeError as cause:
                raise CpuRunnerError("deterministic confirmation worker error") from cause
        return original(requests, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cpu_search, "run_cpu_batch", fail_one_confirmation)
        result = run_cpu_failure_campaign(bundle, output_dir=output)
    assert failures == 1
    _sources["failed"] = Source(bundle, output, result)
    return _sources["failed"]


@pytest.fixture(scope="session")
def completed(source: Source, tmp_path_factory: pytest.TempPathFactory) -> Diagnostic:
    if "complete" in _diagnostics:
        return _diagnostics["complete"]
    root = tmp_path_factory.mktemp("cpu-failure-diagnostic")
    before = files(source.bundle), files(source.output)
    plan, output = root / "plan", root / "output"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
        patch.setattr(cpu_search, "run_cpu_batch", forbid)
        patch.setattr(TensorCandidateRegistry, "materialize", forbid)
        build_cpu_failure_diagnostic_plan(source.bundle, source.output, output=plan)
        result = run_cpu_failure_diagnostic(plan, campaign_bundle=source.bundle,
                                            campaign_output=source.output, output_dir=output)
    assert before == (files(source.bundle), files(source.output))
    _diagnostics["complete"] = Diagnostic(source, plan, output, result)
    return _diagnostics["complete"]


def test_supervisor_preserves_native_child_runtime_and_family_gate(source: Source) -> None:
    campaign = source.report["campaign"]
    assert len(campaign["rows"]) == 9
    assert campaign["status_counts"] == {"scheduled": 9, "completed": 9, "failed": 0, "pending": 0,
                                          "not-started": 0, "confirmed": 0, "vetoed": 9}
    assert source.report["failures"] == []
    assert source.report["supervisor_digest"] != campaign["campaign_digest"]
    assert campaign["runtime_digest"] == canonical_digest(runtime_identity())
    for row in campaign["rows"]:
        spec = load_cpu_search(source.output / "bundles" / row["run_id"])
        assert spec.implementation_digest == search_implementation_digest()
        assert spec.minimum_confirmation_gain == 0.1 and spec.rounds == 3
        assert row["audit_scores"] is None
        summary = _read_record(source.output / "searches" / row["run_id"] / "complete.json")
        assert summary["confirmation"]["status"] == "vetoed" and summary["audit_status"] == "withheld"
    assert campaign["family_pooling_performed"] is False
    assert source.report["gpu_execution_deferred"] is True


def test_failure_observation_retains_live_chain_without_inventing_process_telemetry(failed_source: Source) -> None:
    report = failed_source.report
    campaign = report["campaign"]
    assert [row["status"] for row in campaign["rows"]] == ["failed", *(["completed"] * 8)]
    row = campaign["rows"][0]
    assert row["charged_usage"] is None and row["confirmation_scores"] is None
    assert campaign["costs"]["failed_or_pending_reserved_capacity"] == row["reserved_capacity"]
    assert campaign["costs"]["unknown_charged_usage_run_ids"] == [row["run_id"]]
    failure, = report["failures"]
    assert failure["run_id"] == row["run_id"]
    observation = failure["record"]
    assert observation["run_id"] == row["run_id"]
    assert observation["observation_kind"] == "live-exception"
    assert observation["operation_label"] == "confirmation-CW" and observation["terminal_status"] == "failed"
    assert observation["retained_error_type"] == "CpuRunnerError"
    messages = [node["message"]["text"] for node in observation["exceptions"]]
    assert "deterministic confirmation worker error" in messages
    assert "deterministic underlying diagnostic" in messages
    assert all(value is None for value in observation["observations"].values())
    assert len(observation["requests"]) == 4
    assert all(request["declared_limits"]["wall_seconds"] == 5.0 for request in observation["requests"])
    assert observation["attribution"] == "terminal-binding-does-not-attribute-failure-to-operation"
    assert report["failure_coverage"]["recorded_observations"] == 1
    assert report["failure_coverage"]["lost_observations"] == 0
    assert report["failure_coverage"]["historical_failure_cause_recovered"] is False
    result = _read_record(failed_source.output / "results" / "00000000.json")
    assert result["failure_digest"] == canonical_digest(observation)


@pytest.mark.parametrize("failed", [False, True])
def test_completed_or_failed_supervisor_resume_never_dispatches_claimed_children(
    source: Source, failed_source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool,
) -> None:
    retained = copied(failed_source if failed else source, tmp_path)
    before = files(retained.bundle), files(retained.output)
    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    assert run_cpu_failure_campaign(retained.bundle, output_dir=retained.output) == retained.report
    assert report_cpu_failure_campaign(retained.bundle, output_dir=retained.output) == retained.report
    assert before == (files(retained.bundle), files(retained.output))


def test_failed_source_blocks_new_reference_plan_without_writes_or_execution(
    failed_source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = files(failed_source.output)
    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        build_cpu_failure_diagnostic_plan(failed_source.bundle, failed_source.output, output=tmp_path / "plan")
    assert files(failed_source.output) == before and not (tmp_path / "plan").exists()


def test_reference_plan_binds_actual_supervisor_and_preserves_practice_only_reports(completed: Diagnostic) -> None:
    report = completed.report
    generic, transfer = report["diagnostics"], report["transfer"]
    binding = json.loads((completed.plan / "binding.json").read_text())
    plan = json.loads((completed.plan / "diagnostic.json").read_text())
    contract = json.loads((completed.source.bundle / "supervisor.json").read_text())
    assert binding["supervisor_digest"] == completed.source.report["supervisor_digest"]
    assert binding["source_report_digest"] == canonical_digest(completed.source.report)
    assert binding["source_ledger_digest"] == completed.source.report["ledger_digest"]
    assert binding["implementation_digest"] == plan["implementation_digest"] == failure_implementation_digest()
    assert plan["implementation_digest"] != control_implementation_digest()
    assert plan["budget"] == contract["reference_budget"]
    assert generic["reference_coverage"] == {"scheduled": 3, "completed": 3, "failed": 0,
                                             "pending": 0, "not-started": 0}
    assert len(generic["search_rows"]) == 9 and "policy_summary" not in generic
    assert generic["reference_costs"]["known_charged_usage"] == {
        "proposals": 0, "materializations": 15, "inference_calls": 156,
        "input_tokens": generic["reference_costs"]["known_charged_usage"]["input_tokens"], "output_tokens": 156,
    }
    assert report["failures"] == []
    assert transfer["confirmation_rule_changed"] is False and transfer["family_pooling_performed"] is False
    assert all(family["calibration"]["unselected_heldout_options_evaluated"] is False
               for family in transfer["family_results"].values())


def test_validation_and_reports_are_read_only_even_for_failed_sources(
    completed: Diagnostic, failed_source: Source, monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = [completed.source.bundle, completed.source.output, completed.plan, completed.output, failed_source.output]
    before = [files(root) for root in roots]
    original_open = os.open

    def read_only_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", read_only_open)
    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    monkeypatch.setattr(Path, "mkdir", forbid)
    monkeypatch.setattr(Path, "unlink", forbid)
    assert validate_cpu_failure_campaign(completed.source.bundle)["model_execution_performed"] is False
    assert validate_cpu_failure_diagnostic(completed.plan, campaign_bundle=completed.source.bundle,
                                            campaign_output=completed.source.output)["model_execution_performed"] is False
    assert report_cpu_failure_campaign(failed_source.bundle, output_dir=failed_source.output) == failed_source.report
    diagnostic = report_cpu_failure_diagnostic(completed.plan, campaign_bundle=completed.source.bundle,
                                                campaign_output=completed.source.output, output_dir=completed.output)
    assert diagnostic == completed.report
    assert before == [files(root) for root in roots]


def test_complete_reference_resume_performs_no_dispatch(completed: Diagnostic, monkeypatch: pytest.MonkeyPatch) -> None:
    before = files(completed.source.output), files(completed.output)
    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_failure_diagnostic, "run_cpu_diagnostic_reference", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    assert run_cpu_failure_diagnostic(completed.plan, campaign_bundle=completed.source.bundle,
                                       campaign_output=completed.source.output, output_dir=completed.output) == completed.report
    assert before == (files(completed.source.output), files(completed.output))


def test_prior_control_source_closure_stays_frozen() -> None:
    examples = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution"
    record = json.loads((examples / "cpu_controls" / "MILESTONE13_RESULTS.json").read_text())
    assert control_implementation_digest() == record["implementation_digest"]


@pytest.mark.parametrize("target", ["manifest", "parent-result", "native-result", "native-selection", "observation"])
def test_tampered_evidence_is_rejected_before_any_child_dispatch(
    failed_source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    retained = copied(failed_source, tmp_path)
    first = retained.report["campaign"]["rows"][0]
    child = retained.output / "searches" / first["run_id"]
    path = {
        "manifest": retained.output / "manifest.json",
        "parent-result": retained.output / "results" / "00000000.json",
        "native-result": child / "results" / "00000000.json",
        "native-selection": child / "selection.json",
        "observation": retained.output / "failures" / "00000000.json",
    }[target]
    raw = _read_record(path)
    raw["unowned"] = True
    rehash(path, raw)
    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_failure_campaign(retained.bundle, output_dir=retained.output)


def test_rehashed_corrupt_failed_prefix_cannot_be_laundered_through_parent_inventory(
    failed_source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = copied(failed_source, tmp_path)
    first = retained.report["campaign"]["rows"][0]
    child = retained.output / "searches" / first["run_id"]
    claim_path = child / "claims" / "00000000.json"
    claim = _read_record(claim_path)
    claim["reserved"]["proposals"] += 1
    rehash(claim_path, claim)
    parent_path = retained.output / "results" / "00000000.json"
    parent = _read_record(parent_path)
    parent["evidence_digest"] = canonical_digest(_inventory(child))
    rehash(parent_path, parent)
    (retained.output / "report.json").unlink()
    # Drop later work to ensure validation cannot rely on its old ledger link.
    for index, row in enumerate(retained.report["campaign"]["rows"][1:], start=1):
        for folder in ("claims", "results"):
            (retained.output / folder / f"{index:08d}.json").unlink()
        for folder in ("bundles", "searches"):
            shutil.rmtree(retained.output / folder / row["run_id"])
    before = files(retained.output)
    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_failure_campaign(retained.bundle, output_dir=retained.output)
    assert files(retained.output) == before


@pytest.mark.parametrize("folder", ["failures", "results"])
@pytest.mark.parametrize("after_write", [False, True])
def test_interrupted_failure_publication_never_redispatches_or_backfills_live_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: str, after_write: bool,
) -> None:
    bundle, output = small_bundle(tmp_path), tmp_path / "output"
    original_write = cpu_failure_campaign._immutable_json

    def fail_worker(*args: Any, **kwargs: Any) -> Any:
        raise CpuRunnerError("known publication-fixture worker failure")

    def interrupted_write(path: Path, value: dict[str, Any]) -> Any:
        if path.parent == output / folder and path.name == "00000000.json":
            if after_write:
                original_write(path, value)
            raise OSError("deterministic publication interruption")
        return original_write(path, value)

    monkeypatch.setattr(cpu_search, "run_cpu_batch", fail_worker)
    monkeypatch.setattr(cpu_failure_campaign, "_immutable_json", interrupted_write)
    with pytest.raises(OSError, match="publication interruption"):
        run_cpu_failure_campaign(bundle, output_dir=output)
    monkeypatch.setattr(cpu_failure_campaign, "_immutable_json", original_write)
    first_id = _read_record(output / "claims" / "00000000.json")["run"]["run_id"]
    child_before = files(output / "searches" / first_id)
    observations_before = files(output / "failures")
    next_calls = []

    def stop_before_next_child(child_bundle: Path, **kwargs: Any) -> Any:
        assert child_bundle.name != first_id
        next_calls.append(child_bundle.name)
        raise KeyboardInterrupt("stop after recovering retained parent evidence")

    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", stop_before_next_child)
    with pytest.raises(KeyboardInterrupt):
        run_cpu_failure_campaign(bundle, output_dir=output)
    assert len(next_calls) == 1
    result = report_cpu_failure_campaign(bundle, output_dir=output)
    assert [row["status"] for row in result["campaign"]["rows"]] == ["failed", "pending", *(["not-started"] * 7)]
    observed = folder == "results" or after_write
    assert result["failure_coverage"]["recorded_observations"] == int(observed)
    assert result["failure_coverage"]["lost_observations"] == int(not observed)
    assert (result["failures"][0]["record"] is not None) is observed
    assert files(output / "searches" / first_id) == child_before
    assert files(output / "failures") == observations_before
    first_result = _read_record(output / "results" / "00000000.json")
    assert (first_result["failure_digest"] is not None) is observed
    assert result["campaign"]["rows"][0]["charged_usage"] is None


def test_interrupted_child_claim_is_retained_as_unknown_without_reexecution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output = small_bundle(tmp_path), tmp_path / "output"

    def uncertain_worker(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt("uncertain native inference")

    monkeypatch.setattr(cpu_search, "run_cpu_batch", uncertain_worker)
    with pytest.raises(KeyboardInterrupt):
        run_cpu_failure_campaign(bundle, output_dir=output)
    first_id = _read_record(output / "claims" / "00000000.json")["run"]["run_id"]
    before = files(output / "searches" / first_id)
    pending = report_cpu_failure_campaign(bundle, output_dir=output)
    assert pending["campaign"]["rows"][0]["status"] == "pending"

    def stop_next(child_bundle: Path, **kwargs: Any) -> Any:
        assert child_bundle.name != first_id
        raise KeyboardInterrupt("stop before unclaimed child")

    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", stop_next)
    with pytest.raises(KeyboardInterrupt):
        run_cpu_failure_campaign(bundle, output_dir=output)
    result = report_cpu_failure_campaign(bundle, output_dir=output)
    assert result["campaign"]["rows"][0]["status"] == "failed"
    assert result["failure_coverage"]["lost_observations"] == 1
    assert result["failures"] == [{"run_id": first_id, "record": None}]
    assert files(output / "failures") == {} and files(output / "searches" / first_id) == before


def test_complete_child_recovers_missing_parent_result_by_read_only_proof(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained = copied(source, tmp_path)
    first_id = retained.report["campaign"]["rows"][0]["run_id"]
    (retained.output / "report.json").unlink()
    (retained.output / "results" / "00000000.json").unlink()
    for index, row in enumerate(retained.report["campaign"]["rows"][1:], start=1):
        for folder in ("claims", "results"):
            (retained.output / folder / f"{index:08d}.json").unlink()
        for folder in ("bundles", "searches"):
            shutil.rmtree(retained.output / folder / row["run_id"])
    before = files(retained.output / "searches" / first_id)

    def stop_next(child_bundle: Path, **kwargs: Any) -> Any:
        assert child_bundle.name != first_id
        raise KeyboardInterrupt("stop before unclaimed child")

    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", stop_next)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    with pytest.raises(KeyboardInterrupt):
        run_cpu_failure_campaign(retained.bundle, output_dir=retained.output)
    result = report_cpu_failure_campaign(retained.bundle, output_dir=retained.output)
    assert result["campaign"]["rows"][0] == retained.report["campaign"]["rows"][0]
    assert result["failures"] == []
    assert _read_record(retained.output / "results" / "00000000.json")["failure_digest"] is None
    assert files(retained.output / "searches" / first_id) == before


def test_parent_claim_before_child_bootstrap_is_not_retried_or_refunded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output = small_bundle(tmp_path), tmp_path / "output"
    original = cpu_failure_campaign._derived_bundle

    def interrupted_bootstrap(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt("stop after parent reservation before child bootstrap")

    monkeypatch.setattr(cpu_failure_campaign, "_derived_bundle", interrupted_bootstrap)
    with pytest.raises(KeyboardInterrupt):
        run_cpu_failure_campaign(bundle, output_dir=output)
    monkeypatch.setattr(cpu_failure_campaign, "_derived_bundle", original)
    first_id = _read_record(output / "claims" / "00000000.json")["run"]["run_id"]

    def stop_next(child_bundle: Path, **kwargs: Any) -> Any:
        assert child_bundle.name != first_id
        raise KeyboardInterrupt("stop before next child")

    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", stop_next)
    with pytest.raises(KeyboardInterrupt):
        run_cpu_failure_campaign(bundle, output_dir=output)
    report = report_cpu_failure_campaign(bundle, output_dir=output)
    row = report["campaign"]["rows"][0]
    assert row["status"] == "failed" and row["charged_usage"] is None
    assert report["failure_coverage"]["lost_observations"] == 1
    assert report["failures"] == [{"run_id": first_id, "record": None}]
    assert not (output / "bundles" / first_id).exists() and not (output / "searches" / first_id).exists()
