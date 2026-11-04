from __future__ import annotations

import json
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_control, cpu_control_diagnostic, cpu_diagnostic_reference, cpu_search
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_campaign import validate_cpu_campaign
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignRepeat
from autocontext.weight_evolution.cpu_control import (
    report_cpu_control_campaign,
    run_cpu_control_campaign,
    validate_cpu_control_campaign,
)
from autocontext.weight_evolution.cpu_control_diagnostic import (
    build_cpu_control_diagnostic_plan,
    report_cpu_control_diagnostic,
    run_cpu_control_diagnostic,
    validate_cpu_control_diagnostic,
)
from autocontext.weight_evolution.cpu_control_models import CONTROL_FAMILIES, CpuControlCampaignSpec
from autocontext.weight_evolution.cpu_control_tasks import build_cpu_control_campaign
from autocontext.weight_evolution.cpu_diagnostic import diagnostic_implementation_digest
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
from autocontext.weight_evolution.cpu_runner import CpuRunnerError
from autocontext.weight_evolution.cpu_search import load_cpu_search, validate_cpu_search
from autocontext.weight_evolution.session import _read_record

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU controls require POSIX limits")


@dataclass(frozen=True)
class Source:
    bundle: Path
    output: Path
    spec: CpuControlCampaignSpec
    report: dict[str, Any]


@dataclass(frozen=True)
class Diagnostic:
    source: Source
    plan: Path
    output: Path
    report: dict[str, Any]


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("retained control dispatched, materialized, or wrote new work")


def rehash(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload)}))


def run_diagnostic(evidence: Diagnostic) -> dict[str, Any]:
    return run_cpu_control_diagnostic(evidence.plan, campaign_bundle=evidence.source.bundle,
                                      campaign_output=evidence.source.output, output_dir=evidence.output)


def report_diagnostic(evidence: Diagnostic) -> dict[str, Any]:
    return report_cpu_control_diagnostic(evidence.plan, campaign_bundle=evidence.source.bundle,
                                         campaign_output=evidence.source.output, output_dir=evidence.output)


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Source:
    root = tmp_path_factory.mktemp("cpu-control-source")
    bundle, output = root / "bundle", root / "output"
    spec = build_cpu_control_campaign(
        bundle, preset="fixture", instances_per_family=1, cases_per_role=4,
        repeats=(CpuCampaignRepeat(repeat_id="r0", inference_seed=406, search_order_seed=701),),
    )
    before = files(bundle)
    result = run_cpu_control_campaign(bundle, output_dir=output)
    assert files(bundle) == before
    return Source(bundle, output, spec, result)


@pytest.fixture
def retained_source(source: Source, tmp_path: Path) -> Source:
    return Source(Path(shutil.copytree(source.bundle, tmp_path / "bundle")),
                  Path(shutil.copytree(source.output, tmp_path / "campaign")), source.spec, source.report)


def request_identity(request: CpuInferenceRequest) -> str:
    value = request.to_dict()
    value.pop("request_id")
    return canonical_digest(value)


def expected_reference_requests(source: Source) -> Counter[str]:
    result: Counter[str] = Counter()
    for task in source.spec.tasks:
        row = next(row for row in source.report["rows"] if row["task_id"] == task.task_id)
        child = load_cpu_search(source.output / "bundles" / row["run_id"])
        candidates = {candidate.candidate_id: candidate for candidate in child.candidates}
        for option in (None, *child.options):
            candidate = candidates[option.candidate_id] if option and option.candidate_id else child.baseline
            prefix = option.context if option and option.context else ""
            decoding = option.decoding if option and option.decoding else child.baseline_decoding
            for case in child.cases:
                if case.role != "practice":
                    continue
                request = CpuInferenceRequest(
                    request_id=case.case_id, model=child.model, checkpoint_sha256=candidate.output_sha256,
                    checkpoint_size_bytes=candidate.output_size_bytes,
                    prompt=" ".join(filter(None, (prefix, case.prompt))), decoding=decoding, limits=child.limits,
                )
                result[request_identity(request)] += 1
    return result


@pytest.fixture(scope="module")
def completed(source: Source, tmp_path_factory: pytest.TempPathFactory) -> Diagnostic:
    root = tmp_path_factory.mktemp("cpu-control-diagnostic")
    before = files(source.bundle), files(source.output)
    observed: Counter[str] = Counter()
    calls = 0
    original = cpu_diagnostic_reference.run_cpu_batch

    def practice_only(requests: tuple[CpuInferenceRequest, ...], **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        assert len(requests) == 4
        observed.update(request_identity(request) for request in requests)
        return original(requests, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cpu_control, "run_cpu_search", forbid)
        patch.setattr(cpu_search, "run_cpu_batch", forbid)
        patch.setattr(TensorCandidateRegistry, "materialize", forbid)
        patch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
        build_cpu_control_diagnostic_plan(source.bundle, source.output, output=root / "plan")
        assert calls == 0
        patch.setattr(cpu_diagnostic_reference, "run_cpu_batch", practice_only)
        evidence = Diagnostic(source, root / "plan", root / "output", {})
        result = run_diagnostic(evidence)
    assert calls == 39 and observed == expected_reference_requests(source)
    assert before == (files(source.bundle), files(source.output))
    return Diagnostic(source, evidence.plan, evidence.output, result)


@pytest.fixture
def retained(completed: Diagnostic, tmp_path: Path) -> Diagnostic:
    source = Source(Path(shutil.copytree(completed.source.bundle, tmp_path / "bundle")),
                    Path(shutil.copytree(completed.source.output, tmp_path / "campaign")),
                    completed.source.spec, completed.source.report)
    return Diagnostic(source, Path(shutil.copytree(completed.plan, tmp_path / "plan")),
                      Path(shutil.copytree(completed.output, tmp_path / "diagnostic")), completed.report)


def test_control_families_exercise_weight_transfer_without_changing_joint_gate(source: Source) -> None:
    assert len(source.spec.tasks) == 3 and len(source.report["rows"]) == 9
    assert set(source.report["family_results"]) == set(CONTROL_FAMILIES)
    family_by_task = {task.task_id: task.family for task in source.spec.tasks}
    for row in source.report["rows"]:
        child = source.output / "searches" / row["run_id"]
        summary = _read_record(child / "complete.json")
        practice = _read_record(child / "reports" / "practice.json")["conditions"]
        confirmation = _read_record(child / "reports" / "confirmation.json")["conditions"]
        family = family_by_task[row["task_id"]]
        assert row["status"] == "completed"
        provenance = json.loads((source.bundle / "tasks" / row["task_id"] / "task.provenance.json").read_text())
        expected = provenance["expected_behavior"]
        assert practice["A"]["accuracy"] == expected["baseline_accuracy"]["practice"]
        assert confirmation["A"]["accuracy"] == expected["baseline_accuracy"]["confirmation"]
        if family == "correct-baseline-v1":
            assert all(value is None for value in summary["selected_options"].values())
            assert practice["W"]["accuracy"] == confirmation["W"]["accuracy"] == 1.0
        else:
            assert summary["selected_options"]["W"] in expected["designated_weight_option_ids"]
            assert practice["W"]["accuracy"] == expected["designated_weight_accuracy"]["practice"] == 1.0
            assert confirmation["W"]["accuracy"] == expected["designated_weight_accuracy"]["confirmation"]
        gate = summary["confirmation"]
        assert gate["look"] == 1
        assert gate["outcomes"]["W"] is (family == "transfer-repair-v1")
        assert gate["status"] == ("confirmed" if all(gate["outcomes"].values()) else "vetoed")
        assert summary["audit_status"] == ("completed" if gate["status"] == "confirmed" else "withheld")
        assert all(summary["search_usage"][mechanism]["proposals"] == 3 for mechanism in ("C", "W", "D"))
    for result in source.report["family_results"].values():
        assert result["task_count"] == 1 and result["status_counts"]["scheduled"] == 3
        assert all(coverage["denominator"] == 1 for coverage in result["policy_coverage"].values())
    assert source.report["family_pooling_performed"] is False
    assert source.report["inferential_statistics_computed"] is False
    assert source.report["confirmation_rule_changed"] is False


def test_search_policy_inputs_exclude_family_or_control_oracles(source: Source) -> None:
    expected_keys = {"mechanism", "policy", "policy_digest", "options", "history", "practice_receipt_digests"}
    proposals = 0
    for row in source.report["rows"]:
        template = load_cpu_search(source.output / "bundles" / row["run_id"])
        assert template.rounds == 3 and all(len(template.options_for(arm)) == 4 for arm in ("C", "W", "D"))
        for path in (source.output / "searches" / row["run_id"] / "claims").iterdir():
            claim = _read_record(path)
            if claim["kind"] == "proposal":
                proposals += 1
                assert set(claim["input"]) == expected_keys
                encoded = json.dumps(claim["input"])
                assert "expected_behavior" not in encoded and "designated_weight_option" not in encoded
                assert not any(family in encoded for family in CONTROL_FAMILIES)
    assert proposals == 81


def test_diagnostics_preserve_each_family_and_original_search_evidence(completed: Diagnostic) -> None:
    generic, transfer = completed.report["diagnostics"], completed.report["transfer"]
    assert generic["report_kind"] == "cpu_control_diagnostic_evidence"
    assert generic["family_pooling_performed"] is False and "policy_summary" not in generic
    assert len(generic["original_diagnostic_report_digest"]) == 64
    assert generic["reference_coverage"] == {"scheduled": 3, "completed": 3, "failed": 0,
                                             "pending": 0, "not-started": 0}
    assert len(generic["search_rows"]) == 9
    costs = generic["reference_costs"]["known_charged_usage"]
    assert costs["proposals"] == 0 and costs["materializations"] == 15
    assert costs["inference_calls"] == costs["output_tokens"] == 156
    assert set(transfer["family_results"]) == set(CONTROL_FAMILIES)
    for family in transfer["family_results"].values():
        assert family["task_count"] == 1 and family["reference_coverage"]["completed"] == 1
    assert transfer["family_pooling_performed"] is False
    assert generic["confirmation_rule_changed"] is False
    assert generic["inferential_statistics_computed"] is False
    assert completed.report["gpu_execution_deferred"] is True


def test_reports_and_validation_need_no_writable_file_open(completed: Diagnostic, monkeypatch: pytest.MonkeyPatch) -> None:
    source = completed.source
    before = files(source.bundle), files(source.output), files(completed.plan), files(completed.output)
    original_open = os.open

    def read_only_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", read_only_open)
    monkeypatch.setattr(cpu_control, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    monkeypatch.setattr(Path, "mkdir", forbid)
    monkeypatch.setattr(Path, "unlink", forbid)
    assert validate_cpu_control_campaign(source.bundle)["model_execution_performed"] is False
    assert validate_cpu_control_diagnostic(completed.plan, campaign_bundle=source.bundle,
                                          campaign_output=source.output)["model_execution_performed"] is False
    assert report_cpu_control_campaign(source.bundle, output_dir=source.output) == source.report
    assert report_diagnostic(completed) == completed.report
    assert before == (files(source.bundle), files(source.output), files(completed.plan), files(completed.output))


def test_completed_campaign_and_diagnostic_resume_without_dispatch(retained: Diagnostic, monkeypatch: pytest.MonkeyPatch) -> None:
    before = files(retained.source.output), files(retained.output)
    monkeypatch.setattr(cpu_control, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_control_diagnostic, "run_cpu_diagnostic_reference", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    assert run_cpu_control_campaign(retained.source.bundle, output_dir=retained.source.output) == retained.source.report
    assert run_diagnostic(retained) == retained.report
    assert before == (files(retained.source.output), files(retained.output))


def test_previous_bundles_and_diagnostic_source_pins_still_validate() -> None:
    examples = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution"
    assert validate_cpu_campaign(examples / "cpu_campaign" / "bundle")["model_execution_performed"] is False
    assert validate_cpu_search(examples / "cpu_adaptive" / "bundle")["model_execution_performed"] is False
    previous = json.loads((examples / "cpu_diagnostics" / "MILESTONE12_RESULTS.json").read_text())
    assert diagnostic_implementation_digest() == previous["implementation_digest"]


@pytest.mark.parametrize("target", ["task-provenance", "source-selection", "source-result", "plan", "reference-result"])
def test_source_and_diagnostic_tampering_reject_before_dispatch(
    retained: Diagnostic, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    first = retained.source.report["rows"][0]
    if target == "task-provenance":
        (retained.source.bundle / "tasks" / first["task_id"] / "task.provenance.json").write_text("{}")
    elif target == "plan":
        (retained.plan / "diagnostic.json").write_text("{}")
    else:
        paths = {"source-selection": retained.source.output / "searches" / first["run_id"] / "selection.json",
                 "source-result": retained.source.output / "results" / "00000000.json",
                 "reference-result": retained.output / "results" / "00000000.json"}
        path = paths[target]
        raw = _read_record(path)
        raw["unowned"] = True
        rehash(path, raw)
    monkeypatch.setattr(cpu_control, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_diagnostic(retained)


def test_partial_control_campaign_cannot_create_diagnostic_plan(
    retained_source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    last = retained_source.report["rows"][-1]
    (retained_source.output / "report.json").unlink()
    for folder in ("claims", "results"):
        sorted((retained_source.output / folder).iterdir())[-1].unlink()
    for folder in ("bundles", "searches"):
        shutil.rmtree(retained_source.output / folder / last["run_id"])
    monkeypatch.setattr(cpu_control, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        build_cpu_control_diagnostic_plan(retained_source.bundle, retained_source.output, output=tmp_path / "plan")
    assert not (tmp_path / "plan").exists()


def test_failed_search_preserves_family_denominators_and_cannot_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output = tmp_path / "bundle", tmp_path / "campaign"
    spec = build_cpu_control_campaign(
        bundle, preset="fixture", instances_per_family=1, cases_per_role=4,
        repeats=(CpuCampaignRepeat(repeat_id="r0", inference_seed=406, search_order_seed=701),),
    )
    original = cpu_search.run_cpu_batch
    calls = 0

    def fail_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CpuRunnerError("known control search worker failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(cpu_search, "run_cpu_batch", fail_once)
    result = run_cpu_control_campaign(bundle, output_dir=output)
    assert [row["status"] for row in result["rows"]] == ["failed", *(["completed"] * 8)]
    failed = result["rows"][0]
    assert failed["charged_usage"] is None and failed["confirmation_scores"] is None
    assert result["costs"]["failed_or_pending_reserved_capacity"] == failed["reserved_capacity"]
    failed_family = spec.tasks[0].family
    for name, family in result["family_results"].items():
        assert family["status_counts"]["scheduled"] == 3
        assert all(coverage["denominator"] == 1 for coverage in family["policy_coverage"].values())
        assert all(contrast["mean_available"] is (name != failed_family)
                   for contrast in family["confirmation_contrasts"].values())
    before = files(output)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    assert run_cpu_control_campaign(bundle, output_dir=output) == result
    assert report_cpu_control_campaign(bundle, output_dir=output) == result
    with pytest.raises(ValueError):
        build_cpu_control_diagnostic_plan(bundle, output, output=tmp_path / "plan")
    assert files(output) == before and not (tmp_path / "plan").exists()


@pytest.mark.parametrize("uncertain", [False, True])
def test_failed_reference_keeps_all_policy_rows_and_reservation_without_retry(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    build_cpu_control_diagnostic_plan(source.bundle, source.output, output=tmp_path / "plan")
    evidence = Diagnostic(source, tmp_path / "plan", tmp_path / "diagnostic", {})
    original = cpu_diagnostic_reference.run_cpu_batch
    calls = 0

    def fail_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            if uncertain:
                raise KeyboardInterrupt("uncertain control reference dispatch")
            raise CpuRunnerError("control reference worker failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", fail_once)
    if uncertain:
        with pytest.raises(KeyboardInterrupt):
            run_diagnostic(evidence)
        partial = report_diagnostic(evidence)["diagnostics"]
        assert [row["status"] for row in partial["reference_rows"]] == ["pending", "not-started", "not-started"]
    result = run_diagnostic(evidence)
    generic = result["diagnostics"]
    assert [row["status"] for row in generic["reference_rows"]] == ["failed", "completed", "completed"]
    assert calls == 27 and len(generic["search_rows"]) == 9
    failed = generic["reference_rows"][0]
    assert failed["charged_usage"] is None
    assert generic["reference_costs"]["failed_or_pending_reserved_capacity"] == failed["reserved_capacity"]
    assert len([row for row in generic["search_rows"] if row["reference_status"] == "failed"]) == 3
    before = files(evidence.output), files(source.output)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    assert run_diagnostic(evidence) == report_diagnostic(evidence) == result
    assert before == (files(evidence.output), files(source.output))


@pytest.mark.parametrize("target", ["bundle", "campaign", "plan"])
def test_parent_components_cannot_alias_source_or_plan_for_diagnostic_output(
    retained: Diagnostic, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    anchor = tmp_path / "existing-sibling"
    anchor.mkdir()
    path = {"bundle": retained.source.bundle, "campaign": retained.source.output, "plan": retained.plan}[target]
    output = anchor / ".." / path.name / "forbidden-output"
    before = files(tmp_path)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="overlap"):
        run_cpu_control_diagnostic(retained.plan, campaign_bundle=retained.source.bundle,
                                   campaign_output=retained.source.output, output_dir=output)
    assert files(tmp_path) == before and not (path / "forbidden-output").exists()
