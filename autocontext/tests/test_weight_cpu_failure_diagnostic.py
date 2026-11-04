"""Reference supervision retains bounded failures without retrying native work."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from test_weight_cpu_failure_campaign import (
    Diagnostic,
    Source,
    copied,
    files,
    forbid,
    rehash,
)
from test_weight_cpu_failure_campaign import (
    completed as completed,
)
from test_weight_cpu_failure_campaign import (
    source as source,
)

from autocontext.weight_evolution import cpu_diagnostic_reference, cpu_failure_diagnostic
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_campaign import _inventory
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticReference, CpuDiagnosticSpec
from autocontext.weight_evolution.cpu_failure_diagnostic import (
    build_cpu_failure_diagnostic_plan,
    report_cpu_failure_diagnostic,
    run_cpu_failure_diagnostic,
    validate_cpu_failure_diagnostic,
)
from autocontext.weight_evolution.cpu_failure_prefix import inspect_cpu_reference_prefix
from autocontext.weight_evolution.cpu_runner import CpuRunnerError
from autocontext.weight_evolution.cpu_search import load_cpu_search
from autocontext.weight_evolution.session import _read_record

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU references require POSIX limits")


def run(evidence: Diagnostic) -> dict[str, Any]:
    return run_cpu_failure_diagnostic(evidence.plan, campaign_bundle=evidence.source.bundle,
                                       campaign_output=evidence.source.output, output_dir=evidence.output)


def report(evidence: Diagnostic) -> dict[str, Any]:
    return report_cpu_failure_diagnostic(evidence.plan, campaign_bundle=evidence.source.bundle,
                                          campaign_output=evidence.source.output, output_dir=evidence.output)


def fresh(source: Source, root: Path) -> Diagnostic:
    plan, output = root / "plan", root / "diagnostic"
    build_cpu_failure_diagnostic_plan(source.bundle, source.output, output=plan)
    return Diagnostic(source, plan, output, {})


def clone(evidence: Diagnostic, root: Path) -> Diagnostic:
    return Diagnostic(copied(evidence.source, root), Path(shutil.copytree(evidence.plan, root / "plan")),
                      Path(shutil.copytree(evidence.output, root / "diagnostic")), evidence.report)


def prefix_inputs(evidence: Diagnostic) -> tuple[CpuDiagnosticSpec, CpuDiagnosticReference, dict[str, Any]]:
    spec = CpuDiagnosticSpec.model_validate_json((evidence.plan / "diagnostic.json").read_text())
    reference = spec.references[0]
    child = load_cpu_search(evidence.source.output / "bundles" / reference.source_runs[0].run_id)
    return spec, reference, {
        "checkpoint_path": evidence.source.bundle / "campaign" / "tasks" / reference.task_id / "base.safetensors",
        "materializations": evidence.source.output / "searches" / reference.source_runs[0].run_id / "materializations",
        "cases": tuple(case for case in child.cases if case.role == "practice"),
        "output_dir": evidence.output / "references" / reference.reference_id,
    }


@pytest.fixture(scope="module")
def failed_reference(source: Source, tmp_path_factory: pytest.TempPathFactory) -> Diagnostic:
    evidence = fresh(source, tmp_path_factory.mktemp("observed-reference-failure"))
    original = cpu_diagnostic_reference.run_cpu_batch
    calls = 0

    def fail_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                raise RuntimeError("known reference underlying error")
            except RuntimeError as cause:
                raise CpuRunnerError("known reference worker failure") from cause
        return original(*args, **kwargs)

    before = files(source.bundle), files(source.output)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cpu_diagnostic_reference, "run_cpu_batch", fail_once)
        result = run(evidence)
    assert calls == 27 and before == (files(source.bundle), files(source.output))
    return Diagnostic(source, evidence.plan, evidence.output, result)


def test_reference_plan_binds_full_supervisor_evidence_beyond_family_projection(completed: Diagnostic) -> None:
    binding = json.loads((completed.plan / "binding.json").read_text())
    spec = json.loads((completed.plan / "diagnostic.json").read_text())
    assert binding["source_report_digest"] == canonical_digest(completed.source.report)
    assert binding["source_ledger_digest"] == completed.source.report["ledger_digest"]
    assert binding["supervisor_digest"] == completed.source.report["supervisor_digest"]
    assert spec["campaign_report_digest"] == canonical_digest(completed.source.report["campaign"])
    assert binding["source_report_digest"] != spec["campaign_report_digest"]
    assert completed.report["source_report_digest"] == binding["source_report_digest"]


def test_repinning_plan_to_pure_projection_cannot_drop_supervisor_evidence(
    completed: Diagnostic, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = clone(completed, tmp_path)
    binding = json.loads((evidence.plan / "binding.json").read_text())
    binding["source_report_digest"] = canonical_digest(evidence.source.report["campaign"])
    raw = (json.dumps(binding, sort_keys=True, indent=2) + "\n").encode()
    (evidence.plan / "binding.json").write_bytes(raw)
    lock = json.loads((evidence.plan / "bundle.lock.json").read_text())
    lock.update(binding_sha256=hashlib.sha256(raw).hexdigest(), binding_size_bytes=len(raw))
    (evidence.plan / "bundle.lock.json").write_text(json.dumps(lock))
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="binding changed"):
        validate_cpu_failure_diagnostic(evidence.plan, campaign_bundle=evidence.source.bundle,
                                          campaign_output=evidence.source.output)


def test_reference_failure_keeps_live_chain_unknown_usage_and_all_policy_rows(failed_reference: Diagnostic) -> None:
    result = failed_reference.report
    rows = result["diagnostics"]["reference_rows"]
    assert [row["status"] for row in rows] == ["failed", "completed", "completed"]
    assert rows[0]["charged_usage"] is None
    assert result["diagnostics"]["reference_costs"]["failed_or_pending_reserved_capacity"] == rows[0]["reserved_capacity"]
    assert len(result["diagnostics"]["search_rows"]) == 9
    assert sum(row["reference_status"] == "failed" for row in result["diagnostics"]["search_rows"]) == 3
    failure, = result["failures"]
    observation = failure["record"]
    assert failure["run_id"] == rows[0]["reference_id"]
    assert observation["native_journal_kind"] == "reference"
    assert observation["operation_kind"] == "inference" and observation["operation_label"] == "A"
    assert observation["operation_index"] == 5 and observation["terminal_status"] == "failed"
    assert observation["retained_error_type"] == "CpuRunnerError"
    assert observation["observation_kind"] == "live-exception"
    assert len(observation["requests"]) == 4 and len(observation["checkpoints"]) == 1
    assert all(value is None for value in observation["observations"].values())
    messages = [node["message"]["text"] for node in observation["exceptions"]]
    assert "known reference worker failure" in messages and "known reference underlying error" in messages
    assert result["failure_coverage"]["recorded_observations"] == 1
    assert result["failure_coverage"]["lost_observations"] == 0
    parent = _read_record(failed_reference.output / "results" / "00000000.json")
    assert parent["failure_digest"] == canonical_digest(observation)


def test_failed_reference_reporting_and_resume_never_mutate_or_dispatch(
    failed_reference: Diagnostic, monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = files(failed_reference.source.output), files(failed_reference.output)
    monkeypatch.setattr(cpu_failure_diagnostic, "run_cpu_diagnostic_reference", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    assert run(failed_reference) == failed_reference.report
    original_open = os.open

    def readonly(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", readonly)
    monkeypatch.setattr(Path, "mkdir", forbid)
    monkeypatch.setattr(Path, "unlink", forbid)
    assert report(failed_reference) == failed_reference.report
    assert before == (files(failed_reference.source.output), files(failed_reference.output))


@pytest.mark.parametrize("target", ["observation", "missing-observation", "parent-result", "native-result"])
def test_reference_evidence_tamper_rejects_before_dispatch(
    failed_reference: Diagnostic, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    evidence = clone(failed_reference, tmp_path)
    ref_id = evidence.report["diagnostics"]["reference_rows"][0]["reference_id"]
    path = {"observation": evidence.output / "failures" / "00000000.json",
            "missing-observation": evidence.output / "failures" / "00000000.json",
            "parent-result": evidence.output / "results" / "00000000.json",
            "native-result": evidence.output / "references" / ref_id / "results" / "00000000.json"}[target]
    if target == "missing-observation":
        path.unlink()
    else:
        value = _read_record(path)
        value["unowned"] = True
        rehash(path, value)
    before = files(evidence.output)
    monkeypatch.setattr(cpu_failure_diagnostic, "run_cpu_diagnostic_reference", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run(evidence)
    assert files(evidence.output) == before


def trim_after_first(evidence: Diagnostic, *, remove_first_result: bool) -> str:
    rows = evidence.report["diagnostics"]["reference_rows"]
    (evidence.output / "report.json").unlink()
    if remove_first_result:
        (evidence.output / "results" / "00000000.json").unlink()
    for index, row in enumerate(rows[1:], start=1):
        for folder in ("claims", "results"):
            (evidence.output / folder / f"{index:08d}.json").unlink()
        shutil.rmtree(evidence.output / "references" / row["reference_id"])
    return rows[0]["reference_id"]


def test_rehashed_parent_inventory_cannot_hide_corrupt_reference_prefix(
    failed_reference: Diagnostic, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = clone(failed_reference, tmp_path)
    first = trim_after_first(evidence, remove_first_result=False)
    child = evidence.output / "references" / first
    claim_path = child / "claims" / "00000000.json"
    claim = _read_record(claim_path)
    claim["reserved"]["materializations"] += 1
    rehash(claim_path, claim)
    parent_path = evidence.output / "results" / "00000000.json"
    parent = _read_record(parent_path)
    parent["evidence_digest"] = canonical_digest(_inventory(child))
    rehash(parent_path, parent)
    before = files(evidence.output)
    monkeypatch.setattr(cpu_failure_diagnostic, "run_cpu_diagnostic_reference", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run(evidence)
    assert files(evidence.output) == before


@pytest.mark.parametrize("folder", ["failures", "results"])
@pytest.mark.parametrize("after_write", [False, True])
def test_interrupted_reference_publication_retains_available_observation_without_retry(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: str, after_write: bool,
) -> None:
    evidence = fresh(source, tmp_path)
    original_write = cpu_failure_diagnostic._immutable_json

    def fail_worker(*args: Any, **kwargs: Any) -> Any:
        raise CpuRunnerError("known reference publication fixture failure")

    def interrupt(path: Path, value: dict[str, Any]) -> Any:
        if path.parent == evidence.output / folder and path.name == "00000000.json":
            if after_write:
                original_write(path, value)
            raise OSError("reference parent publication interrupted")
        return original_write(path, value)

    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", fail_worker)
    monkeypatch.setattr(cpu_failure_diagnostic, "_immutable_json", interrupt)
    with pytest.raises(OSError, match="publication interrupted"):
        run(evidence)
    monkeypatch.setattr(cpu_failure_diagnostic, "_immutable_json", original_write)
    first = _read_record(evidence.output / "claims" / "00000000.json")["reference_id"]
    child_before = files(evidence.output / "references" / first)
    observed_before = files(evidence.output / "failures")

    def stop_next(spec: Any, ref: Any, **kwargs: Any) -> Any:
        assert ref.reference_id != first
        raise KeyboardInterrupt("stop before unclaimed reference")

    monkeypatch.setattr(cpu_failure_diagnostic, "run_cpu_diagnostic_reference", stop_next)
    with pytest.raises(KeyboardInterrupt):
        run(evidence)
    result = report(evidence)
    assert [row["status"] for row in result["diagnostics"]["reference_rows"]] == ["failed", "pending", "not-started"]
    available = folder == "results" or after_write
    assert result["failure_coverage"]["recorded_observations"] == int(available)
    assert result["failure_coverage"]["lost_observations"] == int(not available)
    assert (result["failures"][0]["record"] is not None) is available
    assert files(evidence.output / "references" / first) == child_before
    assert files(evidence.output / "failures") == observed_before
    assert result["diagnostics"]["reference_rows"][0]["charged_usage"] is None


@pytest.mark.parametrize("bootstrap", [False, True])
def test_uncertain_or_prebootstrap_reference_is_not_retried_or_refunded(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bootstrap: bool,
) -> None:
    evidence = fresh(source, tmp_path)

    def uncertain(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt("uncertain reference fixture")

    monkeypatch.setattr(cpu_failure_diagnostic if bootstrap else cpu_diagnostic_reference,
                        "run_cpu_diagnostic_reference" if bootstrap else "run_cpu_batch", uncertain)
    with pytest.raises(KeyboardInterrupt):
        run(evidence)
    first = _read_record(evidence.output / "claims" / "00000000.json")["reference_id"]
    child = evidence.output / "references" / first
    before = files(child) if child.exists() else {}
    assert report(evidence)["diagnostics"]["reference_rows"][0]["status"] == "pending"

    def stop_next(spec: Any, ref: Any, **kwargs: Any) -> Any:
        assert ref.reference_id != first
        raise KeyboardInterrupt("stop before unclaimed reference")

    monkeypatch.setattr(cpu_failure_diagnostic, "run_cpu_diagnostic_reference", stop_next)
    with pytest.raises(KeyboardInterrupt):
        run(evidence)
    result = report(evidence)
    first_row = result["diagnostics"]["reference_rows"][0]
    assert first_row["status"] == "failed" and first_row["charged_usage"] is None
    assert result["failures"] == [{"run_id": first, "record": None}]
    assert result["failure_coverage"]["lost_observations"] == 1
    assert files(evidence.output / "failures") == {}
    assert (files(child) if child.exists() else {}) == before
    assert result["diagnostics"]["reference_costs"]["failed_or_pending_reserved_capacity"]["materializations"] == 10


def test_complete_native_reference_recovers_missing_parent_result_without_work(
    completed: Diagnostic, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = clone(completed, tmp_path)
    first = trim_after_first(evidence, remove_first_result=True)
    before = files(evidence.output / "references" / first)

    def stop_next(spec: Any, ref: Any, **kwargs: Any) -> Any:
        assert ref.reference_id != first
        raise KeyboardInterrupt("stop before unclaimed reference")

    monkeypatch.setattr(cpu_failure_diagnostic, "run_cpu_diagnostic_reference", stop_next)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    with pytest.raises(KeyboardInterrupt):
        run(evidence)
    result = report(evidence)
    assert result["diagnostics"]["reference_rows"][0] == completed.report["diagnostics"]["reference_rows"][0]
    assert result["failures"] == []
    assert _read_record(evidence.output / "results" / "00000000.json")["failure_digest"] is None
    assert files(evidence.output / "references" / first) == before


@pytest.mark.parametrize("target", ["complete-marker", "last-result"])
def test_missing_completed_native_evidence_is_corruption_not_new_failure(
    completed: Diagnostic, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    evidence = clone(completed, tmp_path)
    first = evidence.report["diagnostics"]["reference_rows"][0]["reference_id"]
    child = evidence.output / "references" / first
    path = child / "complete.json" if target == "complete-marker" else child / "results" / "00000017.json"
    path.unlink()
    before = files(evidence.output)
    monkeypatch.setattr(cpu_failure_diagnostic, "run_cpu_diagnostic_reference", forbid)
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run(evidence)
    assert files(evidence.output) == before and files(evidence.output / "failures") == {}


@pytest.mark.parametrize("mismatch", ["gold", "role", "runtime", "foreign-reference"])
def test_prefix_requires_actual_practice_and_runtime_bindings_before_trust(
    completed: Diagnostic, monkeypatch: pytest.MonkeyPatch, mismatch: str,
) -> None:
    spec, reference, inputs = prefix_inputs(completed)
    if mismatch in {"gold", "role"}:
        first, *remaining = inputs["cases"]
        inputs["cases"] = (first.model_copy(update={"expected": "incorrect"} if mismatch == "gold"
                                                   else {"role": "confirmation"}), *remaining)
    elif mismatch == "runtime":
        spec = spec.model_copy(update={"runtime_digest": "0" * 64})
    else:
        reference = reference.model_copy(update={"reference_id": "foreign-reference"})
    monkeypatch.setattr(cpu_diagnostic_reference, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="practice cases|runtime"):
        inspect_cpu_reference_prefix(spec, reference, **inputs)


def test_empty_reference_bootstrap_has_no_invented_terminal_claim(completed: Diagnostic, tmp_path: Path) -> None:
    spec, reference, inputs = prefix_inputs(completed)
    inputs["output_dir"] = tmp_path / "bootstrap"
    expected = {"status": "absent", "summary": None, "terminal_claim": None, "terminal_result": None}
    assert inspect_cpu_reference_prefix(spec, reference, **inputs) == expected
    assert not inputs["output_dir"].exists()
    inputs["output_dir"].mkdir()
    (inputs["output_dir"] / ".cpu-reference.lock").touch()
    (inputs["output_dir"] / "claims").mkdir()
    (inputs["output_dir"] / "results").mkdir()
    before = files(inputs["output_dir"])
    assert inspect_cpu_reference_prefix(spec, reference, **inputs) == expected
    assert files(inputs["output_dir"]) == before
    (inputs["output_dir"] / "claims" / "00000000.json").write_text("{}")
    with pytest.raises(ValueError):
        inspect_cpu_reference_prefix(spec, reference, **inputs)


def test_blocked_reference_cannot_retain_later_claims(failed_reference: Diagnostic, tmp_path: Path) -> None:
    evidence = clone(failed_reference, tmp_path)
    spec, reference, inputs = prefix_inputs(evidence)
    child = inputs["output_dir"]
    last = _read_record(child / "claims" / "00000005.json")
    last["index"] = 6
    rehash(child / "claims" / "00000006.json", last)
    with pytest.raises(ValueError):
        inspect_cpu_reference_prefix(spec, reference, **inputs)
