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

from autocontext.weight_evolution import cpu_diagnostic, cpu_diagnostic_reference, cpu_search
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_campaign import run_cpu_campaign
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignRepeat
from autocontext.weight_evolution.cpu_campaign_tasks import build_cpu_campaign
from autocontext.weight_evolution.cpu_diagnostic import (
    report_cpu_diagnostic,
    run_cpu_diagnostic,
    validate_cpu_diagnostic,
)
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticSpec
from autocontext.weight_evolution.cpu_diagnostic_plan import build_cpu_diagnostic_plan
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
from autocontext.weight_evolution.cpu_runner import CpuRunnerError
from autocontext.weight_evolution.session import _read_record

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU diagnostics require POSIX limits")


@dataclass(frozen=True)
class Source:
    bundle: Path
    output: Path
    report: dict[str, Any]


@dataclass(frozen=True)
class Evidence:
    source: Source
    plan: Path
    output: Path
    spec: CpuDiagnosticSpec
    report: dict[str, Any]


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("retained diagnostic dispatched, materialized, or wrote new work")


def rehash(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload)}))


def run(evidence: Evidence) -> dict[str, Any]:
    return run_cpu_diagnostic(evidence.plan, campaign_bundle=evidence.source.bundle,
                              campaign_output=evidence.source.output, output_dir=evidence.output)


def report(evidence: Evidence) -> dict[str, Any]:
    return report_cpu_diagnostic(evidence.plan, campaign_bundle=evidence.source.bundle,
                                 campaign_output=evidence.source.output, output_dir=evidence.output)


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Source:
    root = tmp_path_factory.mktemp("diagnostic-source")
    bundle, output = root / "bundle", root / "output"
    build_cpu_campaign(bundle, task_seeds=(31, 47), cases_per_role=2,
                       repeats=(CpuCampaignRepeat(repeat_id="r0", inference_seed=406, search_order_seed=701),))
    return Source(bundle, output, run_cpu_campaign(bundle, output_dir=output))


def request_identity(request: CpuInferenceRequest) -> str:
    value = request.to_dict()
    value.pop("request_id")
    return canonical_digest(value)


def expected_requests(spec: CpuDiagnosticSpec) -> Counter[str]:
    expected: Counter[str] = Counter()
    for reference in spec.references:
        candidates = {candidate.candidate_id: candidate for candidate in reference.candidates}
        for option in (None, *reference.options):
            candidate = candidates[option.candidate_id] if option and option.candidate_id else reference.baseline
            prefix = option.context if option and option.context else ""
            decoding = option.decoding if option and option.decoding else reference.baseline_decoding
            for case in reference.practice:
                request = CpuInferenceRequest(
                    request_id=case.case_id, model=reference.model, checkpoint_sha256=candidate.output_sha256,
                    checkpoint_size_bytes=candidate.output_size_bytes,
                    prompt=" ".join(filter(None, (prefix, case.prompt))), decoding=decoding, limits=reference.limits,
                )
                expected[request_identity(request)] += 1
    return expected


@pytest.fixture(scope="module")
def completed(source: Source, tmp_path_factory: pytest.TempPathFactory) -> Evidence:
    root = tmp_path_factory.mktemp("diagnostic-complete")
    before = files(source.bundle), files(source.output)
    spec = build_cpu_diagnostic_plan(source.bundle, source.output, output=root / "plan")
    evidence = Evidence(source, root / "plan", root / "output", spec, {})
    observed: Counter[str] = Counter()
    batches = 0
    original = cpu_diagnostic_reference.run_cpu_batch

    def practice_only(requests: tuple[CpuInferenceRequest, ...], **kwargs: Any) -> Any:
        nonlocal batches
        assert len(requests) == 2
        batches += 1
        observed.update(request_identity(request) for request in requests)
        return original(requests, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cpu_diagnostic_reference, "run_cpu_batch", practice_only)
        patch.setattr(TensorCandidateRegistry, "materialize", forbid)
        result = run(evidence)
    # Exact full request identities prove that no confirmation/audit input,
    # alternative seed, undeclared option or extra restoration probe ran.
    assert batches == 26
    assert observed == expected_requests(spec)
    assert before == (files(source.bundle), files(source.output))
    return Evidence(source, evidence.plan, evidence.output, spec, result)


@pytest.fixture
def retained(completed: Evidence, tmp_path: Path) -> Evidence:
    source = Source(Path(shutil.copytree(completed.source.bundle, tmp_path / "bundle")),
                    Path(shutil.copytree(completed.source.output, tmp_path / "source-output")),
                    completed.source.report)
    return Evidence(source, Path(shutil.copytree(completed.plan, tmp_path / "plan")),
                    Path(shutil.copytree(completed.output, tmp_path / "output")), completed.spec, completed.report)


def fresh(source: Source, root: Path) -> Evidence:
    plan = root / "plan"
    spec = build_cpu_diagnostic_plan(source.bundle, source.output, output=plan)
    return Evidence(source, plan, root / "output", spec, {})


def test_complete_diagnostic_exhausts_shared_practice_references_without_reselection(completed: Evidence) -> None:
    spec, result = completed.spec, completed.report
    assert len(spec.references) == 2 and sum(len(ref.source_runs) for ref in spec.references) == 6
    assert result["reference_coverage"] == {"scheduled": 2, "completed": 2, "failed": 0,
                                            "pending": 0, "not-started": 0}
    assert len(result["reference_rows"]) == 2 and len(result["search_rows"]) == 6
    assert {row["run_id"] for row in result["search_rows"]} == {row["run_id"] for row in completed.source.report["rows"]}
    assert result["reference_costs"]["known_charged_usage"] == {
        key: value for key, value in spec.required_usage.items() if key != "references"
    }
    assert result["reference_costs"]["known_charged_usage"]["inference_calls"] == 52
    assert result["reference_costs"]["known_charged_usage"]["proposals"] == 0
    for reference, row in zip(spec.references, result["reference_rows"], strict=True):
        assert row["reference_id"] == reference.reference_id and row["status"] == "completed"
        assert row["reserved_capacity"] == row["charged_usage"] == reference.required_usage
        child = completed.output / "references" / reference.reference_id
        claims = [_read_record(path) for path in sorted((child / "claims").iterdir())]
        assert len(claims) == 18
        assert sum(claim["kind"] == "checkpoint-verification" for claim in claims) == 5
        assert sum(claim["kind"] == "inference" for claim in claims) == 13
    assert result["gpu_execution_deferred"] is True
    assert result["promotion_eligible"] is False
    assert result["benchmark_result_claimed"] is False
    assert result["inferential_statistics_computed"] is False
    assert result["confirmation_rule_changed"] is False


def test_validate_and_report_require_no_writable_file_open(completed: Evidence, monkeypatch: pytest.MonkeyPatch) -> None:
    before = files(completed.output), files(completed.source.output), files(completed.plan)
    original_open = os.open

    def read_only_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", read_only_open)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    monkeypatch.setattr(Path, "mkdir", forbid)
    monkeypatch.setattr(Path, "unlink", forbid)
    validation = validate_cpu_diagnostic(completed.plan, campaign_bundle=completed.source.bundle,
                                         campaign_output=completed.source.output)
    assert validation["model_execution_performed"] is False
    assert report(completed) == completed.report
    assert before == (files(completed.output), files(completed.source.output), files(completed.plan))


def test_completed_resume_preserves_every_file_without_new_cpu_work(
    retained: Evidence, monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = files(retained.output), files(retained.source.output)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    monkeypatch.setattr(cpu_diagnostic, "run_cpu_diagnostic_reference", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    assert run(retained) == retained.report
    assert report(retained) == retained.report
    assert before == (files(retained.output), files(retained.source.output))


def test_missing_parent_report_is_only_republished_by_explicit_resume(
    retained: Evidence, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (retained.output / "report.json").unlink()
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    assert report(retained) == retained.report
    assert not (retained.output / "report.json").exists()
    assert run(retained) == retained.report
    assert _read_record(retained.output / "report.json") == retained.report


@pytest.mark.parametrize("uncertain", [False, True])
def test_failed_and_uncertain_references_remain_charged_without_retry(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    evidence = fresh(source, tmp_path)
    before = files(source.output)
    original = cpu_diagnostic_reference.run_cpu_batch
    calls = 0

    def fail_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            if uncertain:
                raise KeyboardInterrupt("unknown reference worker outcome")
            raise CpuRunnerError("known reference worker failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", fail_once)
    if uncertain:
        with pytest.raises(KeyboardInterrupt):
            run(evidence)
        partial = report(evidence)
        assert [row["status"] for row in partial["reference_rows"]] == ["pending", "not-started"]
    result = run(evidence)
    assert [row["status"] for row in result["reference_rows"]] == ["failed", "completed"]
    assert calls == 14
    failed = result["reference_rows"][0]
    assert failed["charged_usage"] is None and failed["baseline_score"] is None
    assert failed["mechanisms"] == {"C": None, "W": None, "D": None}
    assert result["reference_costs"]["failed_or_pending_reserved_capacity"] == evidence.spec.references[0].required_usage
    assert result["reference_costs"]["unknown_charged_usage_reference_ids"] == [failed["reference_id"]]
    assert len([row for row in result["search_rows"] if row["reference_status"] == "failed"]) == 3
    child = evidence.output / "references" / failed["reference_id"]
    claims = [_read_record(path) for path in sorted((child / "claims").iterdir())]
    assert sum(claim["kind"] == "inference" for claim in claims) == 1
    retained_files = files(evidence.output)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    assert run(evidence) == report(evidence) == result
    assert files(evidence.output) == retained_files and files(source.output) == before


def test_completed_reference_survives_interrupted_parent_publication(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = fresh(source, tmp_path)
    publish = cpu_diagnostic._immutable_json

    def interrupt(path: Path, payload: dict[str, Any]) -> None:
        if path == evidence.output / "results" / "00000000.json":
            raise KeyboardInterrupt("reference complete, parent unpublished")
        publish(path, payload)

    monkeypatch.setattr(cpu_diagnostic, "_immutable_json", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(evidence)
    first_id = evidence.spec.references[0].reference_id
    first = evidence.output / "references" / first_id
    assert (first / "complete.json").is_file()
    before = files(first)
    monkeypatch.setattr(cpu_diagnostic, "_immutable_json", publish)
    original = cpu_diagnostic_reference.run_cpu_batch
    calls = 0

    def protect_completed(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        assert not any(part.startswith(first_id + ".") for part in kwargs["checkpoint_path"].parts)
        return original(*args, **kwargs)

    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", protect_completed)
    result = run(evidence)
    assert result["reference_coverage"]["completed"] == 2 and calls == 13
    assert files(first) == before


@pytest.mark.parametrize("target", ["plan", "source-selection", "source-checkpoint", "source-report",
                                    "claim", "result", "reference-receipt", "reference-completion", "manifest", "lock"])
def test_tampered_source_plan_and_reference_evidence_reject_before_dispatch(
    retained: Evidence, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    ref = retained.spec.references[0]
    child = retained.output / "references" / ref.reference_id
    source_child = retained.source.output / "searches" / ref.source_runs[0].run_id
    if target == "plan":
        (retained.plan / "diagnostic.json").write_text("{}")
    elif target == "source-checkpoint":
        path = next((source_child / "materializations").glob("*/model.safetensors"))
        path.write_bytes(path.read_bytes()[:-1] + b"x")
    elif target == "lock":
        (child / ".cpu-reference.lock").unlink()
    else:
        paths = {"source-selection": source_child / "selection.json", "source-report": retained.source.output / "report.json",
                 "claim": retained.output / "claims" / "00000000.json",
                 "result": retained.output / "results" / "00000000.json",
                 "reference-receipt": sorted((child / "results").iterdir())[-1],
                 "reference-completion": child / "complete.json", "manifest": retained.output / "manifest.json"}
        path = paths[target]
        payload = _read_record(path)
        payload["unowned"] = True
        rehash(path, payload)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    monkeypatch.setattr(cpu_diagnostic, "run_cpu_diagnostic_reference", forbid)
    with pytest.raises(ValueError):
        run(retained)


@pytest.mark.parametrize("corrupt", ["earlier-result", "completed-marker"])
def test_pending_reference_checks_retained_prefix_before_any_later_dispatch(
    retained: Evidence, monkeypatch: pytest.MonkeyPatch, corrupt: str,
) -> None:
    first_id = retained.spec.references[0].reference_id
    child = retained.output / "references" / first_id
    (retained.output / "report.json").unlink()
    for folder in ("claims", "results"):
        for path in (retained.output / folder).iterdir():
            if folder == "results" or path.name != "00000000.json":
                path.unlink()
    for path in (retained.output / "references").iterdir():
        if path.name != first_id:
            shutil.rmtree(path)
    if corrupt == "completed-marker":
        sorted((child / "results").iterdir())[-1].unlink()
    else:
        # Five verified checkpoints followed by one unresolved inference claim.
        for folder in ("claims", "results"):
            for path in (child / folder).iterdir():
                if int(path.stem) > (5 if folder == "claims" else 4):
                    path.unlink()
        (child / "complete.json").unlink()
        path = child / "results" / "00000000.json"
        payload = _read_record(path)
        payload["unowned"] = True
        rehash(path, payload)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run(retained)
    assert not (retained.output / "results" / "00000000.json").exists()
    assert len(list((retained.output / "claims").iterdir())) == 1


def test_valid_reference_prefix_resumes_only_unclaimed_work(retained: Evidence, monkeypatch: pytest.MonkeyPatch) -> None:
    first_id = retained.spec.references[0].reference_id
    child = retained.output / "references" / first_id
    (retained.output / "report.json").unlink()
    for folder in ("claims", "results"):
        for path in (retained.output / folder).iterdir():
            if folder == "results" or path.name != "00000000.json":
                path.unlink()
    for path in (retained.output / "references").iterdir():
        if path.name != first_id:
            shutil.rmtree(path)
    (child / "complete.json").unlink()
    for folder in ("claims", "results"):
        for path in (child / folder).iterdir():
            if int(path.stem) > 7:  # Five checkpoints and three completed practice batches.
                path.unlink()
    prefix = files(child)
    original = cpu_diagnostic_reference.run_cpu_batch
    calls = 0

    def count(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", count)
    result = run(retained)
    assert result["reference_coverage"]["completed"] == 2 and calls == 23
    after = files(child)
    assert all(after[name] == data for name, data in prefix.items())
    assert result["semantic_digest"] == retained.report["semantic_digest"]


@pytest.mark.parametrize("target", ["source-bundle", "source-output", "plan", "ancestor"])
def test_output_cannot_overlap_any_source_or_plan(
    retained: Evidence, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    output = {"source-bundle": retained.source.bundle / "diagnostic",
              "source-output": retained.source.output / "diagnostic", "plan": retained.plan / "diagnostic",
              "ancestor": retained.plan.parent}[target]
    before = files(retained.plan.parent)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_diagnostic(retained.plan, campaign_bundle=retained.source.bundle,
                           campaign_output=retained.source.output, output_dir=output)
    assert files(retained.plan.parent) == before


@pytest.mark.parametrize("target", ["source-bundle", "source-output", "plan"])
@pytest.mark.parametrize("aliased_argument", ["output", "input"])
def test_parent_components_cannot_alias_source_or_plan_for_writable_output(
    retained: Evidence, monkeypatch: pytest.MonkeyPatch, target: str, aliased_argument: str,
) -> None:
    parent = retained.plan.parent
    anchor = parent / "existing-sibling"
    anchor.mkdir()
    sources = {"source-bundle": retained.source.bundle, "source-output": retained.source.output, "plan": retained.plan}
    canonical = sources[target]
    alias = anchor / ".." / canonical.name
    bundle, source_output, plan = retained.source.bundle, retained.source.output, retained.plan
    if aliased_argument == "input":
        if target == "source-bundle":
            bundle = alias
        elif target == "source-output":
            source_output = alias
        else:
            plan = alias
        output = canonical / "forbidden-output"
    else:
        output = alias / "forbidden-output"
    before = files(parent)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    monkeypatch.setattr(cpu_diagnostic, "_immutable_json", forbid)
    with pytest.raises(ValueError, match="overlap"):
        run_cpu_diagnostic(plan, campaign_bundle=bundle, campaign_output=source_output, output_dir=output)
    assert files(parent) == before
    assert not (canonical / "forbidden-output").exists()


def test_symlink_output_and_unclaimed_reference_reject_before_dispatch(
    retained: Evidence, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = tmp_path / "alias"
    link.symlink_to(retained.output, target_is_directory=True)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="symlink"):
        run_cpu_diagnostic(retained.plan, campaign_bundle=retained.source.bundle,
                           campaign_output=retained.source.output, output_dir=link)
    (retained.output / "references" / "unclaimed").mkdir()
    with pytest.raises(ValueError):
        run(retained)


def test_reference_inventory_is_bounded_before_staging_cleanup_or_root_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(1100):
        (tmp_path / f"entry-{index:04}").touch()
    monkeypatch.setattr(cpu_diagnostic_reference, "_clear_staging", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "_immutable_json", forbid)
    monkeypatch.setattr(Path, "iterdir", forbid)
    with pytest.raises(ValueError, match="bound"):
        cpu_diagnostic_reference._prepare(tmp_path, {}, write=True, operation_count=18)
