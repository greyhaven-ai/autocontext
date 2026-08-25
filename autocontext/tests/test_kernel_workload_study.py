from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

import autocontext.kernel_evolution.workload_study_validation as workload_study_validation
from autocontext.kernel_evolution import (
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkObservation,
    KernelBenchmarkReport,
    KernelCandidate,
    KernelGenerationBudgetState,
    KernelGenerationFailure,
    KernelGenerationUsage,
    KernelHardwareIdentity,
    KernelProtocolReservation,
    KernelTransferEvidence,
    KernelTransferProtocolReservation,
    KernelWorkloadPhaseEvidence,
    KernelWorkloadRunEvidence,
    KernelWorkloadSpec,
    KernelWorkloadStudyReport,
    build_kernel_workload_study_report,
    canonical_digest,
    content_digest,
)
from autocontext.kernel_evolution.generation import normalized_generation_usage
from autocontext.kernel_evolution.models import kernel_benchmark_report_digest
from autocontext.kernel_evolution.workload_study_index import validate_measured_evidence_index
from autocontext.kernel_evolution.workload_study_validation import (
    protocol_burns,
    validate_complete_study,
    validate_run_against_spec,
    workload_disposition,
)

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PACKAGE_ROOT.parent
_EXAMPLE = _REPO_ROOT / "examples" / "kernel_evolution" / "multi_workload" / "run.py"
_MISSING_MODULE = object()


@pytest.fixture()
def example_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """Load the script in-process while restoring its unqualified helper modules."""
    helper_names = ("contract", "contract_runtime", "evidence_runtime")
    previous = {name: sys.modules.get(name, _MISSING_MODULE) for name in helper_names}
    for name in helper_names:
        sys.modules.pop(name, None)
    module_name = "_autocontext_multi_workload_example_test"
    monkeypatch.syspath_prepend(str(_EXAMPLE.parent))
    spec = importlib.util.spec_from_file_location(module_name, _EXAMPLE)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load multi-workload example module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(module_name, None)
        for name, prior in previous.items():
            sys.modules.pop(name, None)
            if prior is not _MISSING_MODULE:
                sys.modules[name] = prior  # type: ignore[assignment]


@pytest.fixture(scope="module")
def study_bundle(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[KernelWorkloadStudyReport, Path]:
    output = tmp_path_factory.mktemp("kernel-workload-study")
    completed = subprocess.run(
        [sys.executable, str(_EXAMPLE), "--output", str(output), "--study-id", "test-study"],
        cwd=_PACKAGE_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "portable champions: 0" in completed.stdout
    study_root = output / "test-study"
    return (
        KernelWorkloadStudyReport.model_validate_json((study_root / "study_report.json").read_text(encoding="utf-8")),
        study_root,
    )


@pytest.fixture(scope="module")
def study_report(study_bundle: tuple[KernelWorkloadStudyReport, Path]) -> KernelWorkloadStudyReport:
    return study_bundle[0]


@pytest.fixture(scope="module")
def study_root(study_bundle: tuple[KernelWorkloadStudyReport, Path]) -> Path:
    return study_bundle[1]


def _payload(report: KernelWorkloadStudyReport) -> dict[str, Any]:
    return copy.deepcopy(report.model_dump(mode="json"))


def _make_ineligible(observation: dict[str, Any], reason: str = "adversarial_test") -> None:
    observation["eligible"] = False
    observation["rejection_reason"] = reason
    observation["derived_statistics_receipt"] = None


def _make_reportless(observation: dict[str, Any]) -> None:
    _make_ineligible(observation, "harness_modified")
    observation["report"] = None
    for name in (
        "hardware_scope_id",
        "baseline_id",
        "protocol_id",
        "protocol_compatibility_id",
        "derived_statistics_receipt",
        "candidate_median_ms",
        "incumbent_median_ms",
        "reference_median_ms",
        "speedup_vs_incumbent",
        "speedup_vs_reference",
        "speedup_lcb95",
        "speedup_lcb",
        "confidence_level",
        "all_case_no_regression_passed",
        "relative_improvement",
        "candidate_p95_ms",
        "incumbent_p95_ms",
        "environment_drift_ratio",
    ):
        observation[name] = None


def _replace_report_candidate_with_incumbent(report: dict[str, Any]) -> None:
    for suffix in ("artifact_digest", "source_digest", "source_suffix", "entrypoint"):
        report[f"candidate_{suffix}"] = report[f"incumbent_{suffix}"]
    report["resources"]["candidate_artifact_digest"] = report["incumbent_artifact_digest"]


def _make_conclusive_correctness_failure(observation: dict[str, Any]) -> None:
    _make_ineligible(observation, "correctness_failed")
    correctness = observation["report"]["correctness"]
    correctness["passed"] = False
    correctness["tests_passed"] = 0
    correctness["hidden_tests_passed"] = 0
    correctness["max_abs_error"] = 1.0
    correctness["max_rel_error"] = 1.0
    correctness["failures"] = ["injected correctness failure"]
    observation["report"]["evaluation_status"] = "candidate_error"
    observation["report"]["failure_kind"] = "correctness"
    observation["report"]["performance"] = None
    for slice_report in correctness["slices"]:
        slice_report["cases_passed"] = 0
        slice_report["passed"] = False


def _recompute_study_usage(payload: dict[str, Any]) -> None:
    transfer_wall = sum(
        float(item["primary"]["evaluation_wall_seconds"]) + float(item["confirmation"]["evaluation_wall_seconds"])
        for item in payload["transfers"]
    )
    transfer_cost = sum(
        float(item["primary"]["evaluation_cost_usd"]) + float(item["confirmation"]["evaluation_cost_usd"])
        for item in payload["transfers"]
    )
    payload["total_transfer_wall_seconds"] = transfer_wall
    payload["total_transfer_cost_usd"] = transfer_cost
    payload["total_wall_seconds"] = sum(float(item["total_wall_seconds"]) for item in payload["workload_runs"])
    payload["total_wall_seconds"] += transfer_wall
    payload["total_cost_usd"] = sum(float(item["total_cost_usd"]) for item in payload["workload_runs"])
    payload["total_cost_usd"] += transfer_cost


def _rebind_workload_specs(payload: dict[str, Any]) -> None:
    payload["provenance"]["workload_specs_digest"] = canonical_digest(
        {
            "schema_version": "autocontext.kernel-workload-spec-set/v1",
            "workload_specs": payload["workload_specs"],
        }
    )


def _rebind_generation_context(run: dict[str, Any]) -> None:
    run["generation_receipt_context_digest"] = canonical_digest(
        {
            "schema_version": "autocontext.kernel-generation-receipt-context/v1",
            "study_execution_id": run["study_execution_id"],
            "workload_spec_id": run["workload_spec_id"],
            "run_id": run["result"]["run_id"],
            "generation_budget_id": run["generation_budget_id"],
            "generation_results": run["generation_results"],
            "generation_failures": run["generation_failures"],
        }
    )
    for phase in (run["primary"], run["confirmation"]):
        observation = phase["observation"]
        report = observation.get("report")
        if report is None:
            continue
        report["metadata"]["generation_receipt_context_digest"] = run["generation_receipt_context_digest"]
        receipt = observation.get("derived_statistics_receipt")
        if receipt is not None:
            receipt["raw_report_digest"] = kernel_benchmark_report_digest(KernelBenchmarkReport.model_validate(report))


def _run_and_spec(
    study_report: KernelWorkloadStudyReport,
    *,
    disposition: str | None = None,
) -> tuple[KernelWorkloadRunEvidence, KernelWorkloadSpec]:
    run = next(item for item in study_report.workload_runs if disposition is None or item.disposition == disposition)
    spec = next(item for item in study_report.workload_specs if item.workload_id == run.workload_id)
    return run, spec


def _embedded_reports(study_report: KernelWorkloadStudyReport) -> tuple[KernelBenchmarkReport, ...]:
    reports: list[KernelBenchmarkReport] = []
    for run in study_report.workload_runs:
        for attempt in run.result.attempts:
            if attempt.observation.report is not None:
                reports.append(attempt.observation.report)
            if attempt.confirmation_observation is not None and attempt.confirmation_observation.report is not None:
                reports.append(attempt.confirmation_observation.report)
        for run_phase in (run.primary, run.confirmation):
            if run_phase.observation.report is not None:
                reports.append(run_phase.observation.report)
    for transfer in study_report.transfers:
        for transfer_phase in (transfer.primary, transfer.confirmation):
            if transfer_phase.observation.report is not None:
                reports.append(transfer_phase.observation.report)
    return tuple(reports)


def test_study_exposes_self_contained_independent_evidence(study_report: KernelWorkloadStudyReport) -> None:
    assert {spec.workload_family for spec in study_report.workload_specs} == {
        "matmul-generalization-v1",
        "fused-elementwise-reduction-v1",
        "causal-attention-v1",
    }
    assert study_report.provenance.evidence_kind == "synthetic"
    assert study_report.provenance.warning
    assert study_report.all_workloads_independently_verified is True
    assert len(study_report.protocol_burns) == len({item.protocol_id for item in study_report.protocol_burns})
    assert len(study_report.protocol_burns) == len({item.plan_commitment for item in study_report.protocol_burns})
    for spec, run in zip(study_report.workload_specs, study_report.workload_runs, strict=True):
        assert run.workload_spec_id == spec.spec_id
        assert run.primary.passed and run.confirmation.passed
        assert run.primary.protocol_id != run.confirmation.protocol_id
        assert set(run.primary.correctness_slices) == {"train", "holdout"}
        assert run.proposals_evaluated == len(run.generation_results)
        assert run.budget_state == KernelGenerationBudgetState.from_results(run.generation_results)
        assert float(run.total_wall_seconds) > 0.0


def test_reference_timings_stay_pinned_when_the_incumbent_changes(
    study_report: KernelWorkloadStudyReport,
) -> None:
    reports = _embedded_reports(study_report)
    for spec, run in zip(study_report.workload_specs, study_report.workload_runs, strict=True):
        baseline_report = run.result.attempts[0].observation.report
        assert baseline_report is not None and baseline_report.performance is not None
        assert all(case.candidate_median_ms == case.reference_median_ms for case in baseline_report.performance.cases)
        reference_profiles = {
            tuple((case.name, float(case.reference_median_ms)) for case in report.performance.cases)
            for report in reports
            if report.problem_id == spec.problem_id and report.performance is not None
        }
        assert len(reference_profiles) == 1


def test_shape_profile_excludes_workload_family_plan_and_split_labels(
    example_module: ModuleType,
) -> None:
    contract_module = sys.modules["contract"]
    workload = contract_module.load_manifest(example_module.MANIFEST)["workloads"][0]
    relabeled = copy.deepcopy(workload)
    relabeled["workload_id"] = "relabeled-workload"
    relabeled["workload_family"] = "relabeled-family"
    relabeled["reference_identity"] = "relabeled-reference"
    relabeled["primary_seed_commitment"] = "relabeled-primary-plan"
    relabeled["confirmation_seed_commitment"] = "relabeled-confirmation-plan"
    for index, case in enumerate(relabeled["cases"]):
        case["name"] = f"relabeled-{index}"
        case["split"] = "holdout" if case["split"] == "train" else "train"
    assert contract_module.shape_profile_id(workload) == contract_module.shape_profile_id(relabeled)

    relabeled["cases"][0]["shape_class"] = "different-public-shape"
    assert contract_module.shape_profile_id(workload) != contract_module.shape_profile_id(relabeled)


def test_evidence_index_and_artifact_manifest_bind_every_embedded_report(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    index_path = study_root / "evidence" / "index.json"
    artifacts_path = study_root / "study_artifacts.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    artifacts = json.loads(artifacts_path.read_text(encoding="utf-8"))
    manifest = json.loads((study_root / "manifest.json").read_text(encoding="utf-8"))
    provenance = study_report.provenance
    assert manifest["study_execution_id"] == provenance.study_execution_id
    assert index["study_execution_id"] == artifacts["study_execution_id"] == provenance.study_execution_id
    assert index["workload_specs_digest"] == artifacts["workload_specs_digest"] == provenance.workload_specs_digest
    assert index["evidence_origin"] == provenance.evidence_kind
    assert index["study_manifest_digest"] == provenance.manifest_digest
    assert index["study_contract_digest"] == provenance.contract_digest
    assert index["study_backend_identity"] == provenance.backend_identity
    assert index["evidence_warning"] == provenance.warning
    assert provenance.evidence_index_digest == content_digest(index_path.read_bytes())
    assert artifacts["study_id"] == study_report.study_id
    assert artifacts["study_manifest_digest"] == provenance.manifest_digest
    assert artifacts["study_contract_digest"] == provenance.contract_digest
    assert artifacts["evidence_index_digest"] == content_digest(index_path.read_bytes())
    assert artifacts["study_report_digest"] == content_digest((study_root / "study_report.json").read_bytes())

    runtime_manifest_path = study_root / artifacts["contract_runtime_manifest_path"]
    runtime_manifest = json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
    assert runtime_manifest["schema_version"] == "autocontext.synthetic-kernel-contract-runtime/v1"
    assert canonical_digest(runtime_manifest) == provenance.contract_digest
    assert artifacts["contract_runtime_manifest_file_digest"] == content_digest(runtime_manifest_path.read_bytes())
    runtime_root = runtime_manifest_path.parent.resolve()
    logical_paths = [item["path"] for item in runtime_manifest["files"]]
    assert logical_paths == sorted(logical_paths)
    assert len(logical_paths) == len(set(logical_paths))
    assert "example/contract_runtime.py" in logical_paths
    assert "package/src/autocontext/kernel_evolution/workload_study_validation.py" in logical_paths
    assert "environment/uv.lock" in logical_paths
    for item in runtime_manifest["files"]:
        source_path = (runtime_root / item["path"]).resolve()
        assert source_path.is_relative_to(runtime_root)
        assert source_path.is_file() and not source_path.is_symlink()
        content = source_path.read_bytes()
        assert len(content) == item["size_bytes"]
        assert content_digest(content) == item["digest"]
    assert not any(path.name == "__pycache__" for path in runtime_root.rglob("*"))

    sources = {item["workload_id"]: item for item in artifacts["reference_sources"]}
    assert set(sources) == {spec.workload_id for spec in study_report.workload_specs}
    for spec in study_report.workload_specs:
        item = sources[spec.workload_id]
        source_path = (study_root / item["path"]).resolve()
        assert source_path.is_relative_to((study_root / "sources").resolve())
        source = source_path.read_bytes()
        assert item["source_digest"] == spec.reference_source_digest == content_digest(source)
        assert item["artifact_digest"] == spec.reference_artifact_digest
        assert item["file_digest"] == content_digest(source)

    declared_files = {item["path"]: item for item in artifacts["portable_files"]}
    expected_files = {"manifest.json", "study_report.json"}
    for name in ("contract-runtime", "contracts", "evidence", "sources"):
        expected_files.update(
            path.relative_to(study_root).as_posix() for path in (study_root / name).rglob("*") if path.is_file()
        )
    assert set(declared_files) == expected_files
    assert all(not path.startswith("runs/") for path in declared_files)
    for relative, item in declared_files.items():
        path = (study_root / relative).resolve()
        assert path.is_relative_to(study_root.resolve()) and not path.is_symlink()
        content = path.read_bytes()
        assert item["size_bytes"] == len(content)
        assert item["digest"] == content_digest(content)

    embedded_reports = _embedded_reports(study_report)
    assert all(item.metadata["workload_specs_digest"] == provenance.workload_specs_digest for item in embedded_reports)
    assert all(item.metadata["study_execution_id"] == provenance.study_execution_id for item in embedded_reports)
    reports = {kernel_benchmark_report_digest(item): item for item in embedded_reports}
    indexed_digests = {item["report_digest"] for item in index["records"] if item["report_digest"] is not None}
    assert set(reports) <= indexed_digests
    for report_digest, report in reports.items():
        report_path = study_root / "evidence" / "reports" / f"{report_digest.removeprefix('sha256:')}.json"
        assert report_path.is_file()
        assert KernelBenchmarkReport.model_validate_json(report_path.read_text(encoding="utf-8")) == report

    expected_records = len(embedded_reports)
    assert len(index["records"]) == expected_records
    contract_root = (study_root / "contracts").resolve()
    for record in index["records"]:
        problem_path = (study_root / record["problem_contract_path"]).resolve()
        assert problem_path.is_relative_to(contract_root)
        assert record["problem_contract_digest"] == content_digest(problem_path.read_bytes())
        assert record["planned_protocol_id"]
        assert record["planned_plan_commitment"]
        assert record["execution_id"]
        assert record["record_kind"] in {"evaluation", "raw-evaluation", "derived-rejection"}
        if record["chargeable"]:
            assert record["derived_from_observation_digest"] is None
        else:
            assert record["record_kind"] == "derived-rejection"
            assert record["derived_from_observation_digest"]
            assert record["evaluation_wall_seconds"] == 0.0
        if record["report_digest"] is not None:
            assert record["protocol_id"] == record["planned_protocol_id"]
            assert record["plan_commitment"] == record["planned_plan_commitment"]

    protected_uses = Counter(
        (record["planned_protocol_id"], record["planned_plan_commitment"])
        for record in index["records"]
        if not record["stream_id"].endswith("/primary") or not record["stream_id"].startswith("campaign/")
    )
    for burn in study_report.protocol_burns:
        if burn.kind != "campaign-primary":
            assert protected_uses[(burn.protocol_id, burn.plan_commitment)] == 1


def test_external_evidence_index_replay_rejects_an_extra_protected_execution(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    index_path = study_root / "evidence" / "index.json"
    raw = index_path.read_bytes()
    validate_measured_evidence_index(
        study_report,
        {"evidence_index_bytes": raw, "evidence_index_digest": content_digest(raw)},
    )
    payload = json.loads(raw)
    protected = next(
        record
        for record in payload["records"]
        if not record["stream_id"].startswith("campaign/") or "/confirmation-" in record["stream_id"]
    )
    duplicate = copy.deepcopy(protected)
    duplicate["execution_id"] = content_digest(b"unreported-earlier-protected-look")
    payload["records"].append(duplicate)
    altered_raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    altered = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_index_digest": content_digest(altered_raw)}
            )
        }
    )
    with pytest.raises(ValueError, match="execution identity|omits, duplicates, or adds"):
        validate_measured_evidence_index(
            altered,
            {"evidence_index_bytes": altered_raw, "evidence_index_digest": content_digest(altered_raw)},
        )

    payload = json.loads(raw)
    extra_raw = copy.deepcopy(protected)
    extra_raw.update(
        {
            "record_kind": "raw-evaluation",
            "chargeable": True,
            "derived_from_observation_digest": None,
            "observation_digest": content_digest(b"unpaired-hidden-look"),
            "report_digest": content_digest(b"unpaired-hidden-report"),
        }
    )
    payload["records"].append(extra_raw)
    altered_raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    altered = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_index_digest": content_digest(altered_raw)}
            )
        }
    )
    with pytest.raises(ValueError, match="matching derived rejection"):
        validate_measured_evidence_index(altered, {"evidence_index_bytes": altered_raw})


def test_external_index_schema_must_match_report_provenance(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    raw = (study_root / "evidence" / "index.json").read_bytes()
    payload = json.loads(raw)
    payload["schema_version"] = "autocontext.kernel-study-evidence-index/v1"
    generic_raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    synthetic = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_index_digest": content_digest(generic_raw)}
            )
        }
    )
    with pytest.raises(ValueError, match="malformed"):
        validate_measured_evidence_index(synthetic, {"evidence_index_bytes": generic_raw})

    measured = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_kind": "measured", "backend_identity": "operator-evaluator/v1", "warning": None}
            )
        }
    )
    with pytest.raises(ValueError, match="malformed"):
        validate_measured_evidence_index(measured, {"evidence_index_bytes": raw})


def test_measured_external_index_rejects_a_synthetic_warning_marker(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    payload = json.loads((study_root / "evidence" / "index.json").read_bytes())
    payload.update(
        schema_version="autocontext.kernel-study-evidence-index/v1",
        evidence_origin="measured",
        study_backend_identity="operator-evaluator/v1",
        evidence_warning="synthetic marker",
    )
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    provenance = study_report.provenance.model_copy(
        update={
            "evidence_kind": "measured",
            "backend_identity": "operator-evaluator/v1",
            "warning": None,
            "evidence_index_digest": content_digest(raw),
        }
    )
    measured = study_report.model_copy(update={"provenance": provenance})

    with pytest.raises(ValueError, match="disagrees with report provenance"):
        validate_measured_evidence_index(measured, {"evidence_index_bytes": raw})


def test_external_index_cannot_relabel_a_report_backed_look_as_a_derived_rejection(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    payload = json.loads((study_root / "evidence" / "index.json").read_bytes())
    index = next(index for index, record in enumerate(payload["records"]) if record["report_digest"] is not None)
    original = payload["records"][index]
    raw_digest = content_digest(b"invented-earlier-raw-observation")
    retained_raw = copy.deepcopy(original)
    retained_raw.update(
        {
            "record_kind": "raw-evaluation",
            "observation_digest": raw_digest,
            "chargeable": True,
            "derived_from_observation_digest": None,
        }
    )
    relabeled = copy.deepcopy(original)
    relabeled.update(
        {
            "record_kind": "derived-rejection",
            "chargeable": False,
            "derived_from_observation_digest": raw_digest,
            "evaluation_wall_seconds": 0.0,
            "evaluation_cost_usd": 0.0,
        }
    )
    payload["records"][index] = relabeled
    payload["records"].append(retained_raw)
    altered_raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    altered = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_index_digest": content_digest(altered_raw)}
            )
        }
    )

    with pytest.raises(ValueError, match="zero-charge reportless deadline rejections"):
        validate_measured_evidence_index(altered, {"evidence_index_bytes": altered_raw})


def test_external_index_binds_final_phase_cost_accounting(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    raw = (study_root / "evidence" / "index.json").read_bytes()
    run = study_report.workload_runs[0]
    changed_primary = run.primary.model_copy(
        update={"evaluation_cost_usd": float(run.primary.evaluation_cost_usd) + 0.25}
    )
    changed_run = run.model_copy(update={"primary": changed_primary})
    runs = (changed_run, *study_report.workload_runs[1:])
    altered = study_report.model_copy(update={"workload_runs": runs})

    with pytest.raises(ValueError, match="phase accounting"):
        validate_measured_evidence_index(altered, {"evidence_index_bytes": raw})


@pytest.mark.parametrize("field", ["evaluation_wall_seconds", "evaluation_cost_usd"])
def test_external_index_usage_cannot_exceed_embedded_phase_accounting(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
    field: str,
) -> None:
    payload = json.loads((study_root / "evidence" / "index.json").read_bytes())
    record = next(record for record in payload["records"] if record["stream_id"].startswith("final/"))
    record[field] = float(record[field]) + 1_000_000.0
    altered_raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    altered = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_index_digest": content_digest(altered_raw)}
            )
        }
    )

    with pytest.raises(ValueError, match="phase accounting"):
        validate_measured_evidence_index(altered, {"evidence_index_bytes": altered_raw})


def test_external_index_execution_identity_cannot_cover_two_campaign_looks(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    payload = json.loads((study_root / "evidence" / "index.json").read_bytes())
    campaign = [
        record
        for record in payload["records"]
        if record["stream_id"].startswith("campaign/") and record["record_kind"] == "evaluation"
    ]
    assert len(campaign) >= 2
    campaign[1]["execution_id"] = campaign[0]["execution_id"]
    altered_raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    altered = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_index_digest": content_digest(altered_raw)}
            )
        }
    )

    with pytest.raises(ValueError, match="execution identity"):
        validate_measured_evidence_index(altered, {"evidence_index_bytes": altered_raw})


def test_external_index_cannot_swap_execution_identities_between_campaign_looks(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    payload = json.loads((study_root / "evidence" / "index.json").read_bytes())
    campaign = [
        record
        for record in payload["records"]
        if record["stream_id"].startswith("campaign/variable-shape-matmul/primary")
        and record["record_kind"] == "evaluation"
    ]
    assert len(campaign) >= 2
    campaign[0]["execution_id"], campaign[1]["execution_id"] = (
        campaign[1]["execution_id"],
        campaign[0]["execution_id"],
    )
    altered_raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    altered = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_index_digest": content_digest(altered_raw)}
            )
        }
    )

    with pytest.raises(ValueError, match="execution identity"):
        validate_measured_evidence_index(altered, {"evidence_index_bytes": altered_raw})


def test_external_index_cannot_reorder_campaign_sequences_and_recompute_ids(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    payload = json.loads((study_root / "evidence" / "index.json").read_bytes())
    campaign = [
        record
        for record in payload["records"]
        if record["stream_id"] == "campaign/variable-shape-matmul/primary"
        and record["record_kind"] == "evaluation"
    ]
    assert len(campaign) >= 2
    campaign[0]["sequence"], campaign[1]["sequence"] = campaign[1]["sequence"], campaign[0]["sequence"]
    for record in campaign[:2]:
        record["execution_id"] = canonical_digest(
            {
                "kind": "autocontext.synthetic-kernel-evaluation/v1",
                "study_execution_id": payload["study_execution_id"],
                "workload_specs_digest": payload["workload_specs_digest"],
                "stream_id": record["stream_id"],
                "execution_index": record["sequence"],
                "candidate_artifact_digest": record["candidate_artifact_digest"],
                "incumbent_artifact_digest": record["incumbent_artifact_digest"],
                "protocol_id": record["planned_protocol_id"],
                "plan_commitment": record["planned_plan_commitment"],
            }
        )
    altered_raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    altered = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_index_digest": content_digest(altered_raw)}
            )
        }
    )

    with pytest.raises(ValueError, match="omits, duplicates, or adds"):
        validate_measured_evidence_index(altered, {"evidence_index_bytes": altered_raw})


@pytest.mark.parametrize("sequence", [True, 1.0])
def test_external_index_sequence_requires_an_exact_positive_integer(
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
    sequence: object,
) -> None:
    payload = json.loads((study_root / "evidence" / "index.json").read_bytes())
    payload["records"][0]["sequence"] = sequence
    altered_raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    altered = study_report.model_copy(
        update={
            "provenance": study_report.provenance.model_copy(
                update={"evidence_index_digest": content_digest(altered_raw)}
            )
        }
    )

    with pytest.raises(ValueError, match="sequence must be a positive integer"):
        validate_measured_evidence_index(altered, {"evidence_index_bytes": altered_raw})


def test_fresh_studies_never_reuse_reserved_plans_or_execution_ids(
    tmp_path: Path,
    study_report: KernelWorkloadStudyReport,
    study_root: Path,
) -> None:
    output = tmp_path / "fresh"
    subprocess.run(
        [sys.executable, str(_EXAMPLE), "--output", str(output), "--study-id", "second-study"],
        cwd=_PACKAGE_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    second_root = output / "second-study"
    second = KernelWorkloadStudyReport.model_validate_json(
        (second_root / "study_report.json").read_text(encoding="utf-8")
    )
    assert second.provenance.study_execution_id != study_report.provenance.study_execution_id
    first_burns = {(item.protocol_id, item.plan_commitment) for item in study_report.protocol_burns}
    second_burns = {(item.protocol_id, item.plan_commitment) for item in second.protocol_burns}
    assert first_burns.isdisjoint(second_burns)
    first_index = json.loads((study_root / "evidence" / "index.json").read_text(encoding="utf-8"))
    second_index = json.loads((second_root / "evidence" / "index.json").read_text(encoding="utf-8"))
    assert {item["execution_id"] for item in first_index["records"]}.isdisjoint(
        item["execution_id"] for item in second_index["records"]
    )
    replay = _payload(second)
    replay["workload_runs"][0]["generation_results"] = [
        item.model_dump(mode="json") for item in study_report.workload_runs[0].generation_results
    ]
    replay["workload_runs"][0]["generation_failures"] = [
        item.model_dump(mode="json") for item in study_report.workload_runs[0].generation_failures
    ]
    with pytest.raises(ValidationError, match="study and run context commitment"):
        KernelWorkloadStudyReport.model_validate(replay)


@pytest.mark.parametrize("study_id_kind", ["parent", "absolute"])
def test_unsafe_study_ids_fail_before_creating_an_outside_directory(
    tmp_path: Path,
    study_id_kind: str,
) -> None:
    output = tmp_path / "output"
    outside = tmp_path / f"{study_id_kind}-escape"
    study_id = f"../{outside.name}" if study_id_kind == "parent" else str(outside)
    completed = subprocess.run(
        [sys.executable, str(_EXAMPLE), "--output", str(output), "--study-id", study_id],
        cwd=_PACKAGE_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 2
    assert "study ID" in completed.stderr
    assert not outside.exists()
    assert not output.exists()


def test_study_root_resolution_does_not_follow_the_final_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    requested = output / "requested"
    try:
        requested.symlink_to("other", target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "requested"])

    with pytest.raises(FileExistsError):
        example_module.main()

    assert example_module._resolve_study_root(output, "requested") == requested
    assert requested.is_symlink()
    assert not (output / "other").exists()


def test_unsafe_manifest_workload_id_fails_before_creating_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    manifest = json.loads(example_module.MANIFEST.read_text(encoding="utf-8"))
    manifest["workloads"][0]["workload_id"] = "../../outside"
    manifest_path = tmp_path / "malicious-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setattr(example_module, "MANIFEST", manifest_path)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "unsafe-manifest"])

    with pytest.raises(ValueError, match="lowercase safe path components"):
        example_module.main()

    assert not output.exists()
    assert not (tmp_path / "outside").exists()


def test_manifest_without_synthetic_warning_fails_before_creating_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    manifest = json.loads(example_module.MANIFEST.read_text(encoding="utf-8"))
    manifest.pop("warning")
    manifest_path = tmp_path / "missing-warning.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setattr(example_module, "MANIFEST", manifest_path)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "missing-warning"])

    with pytest.raises(ValueError, match="conspicuous warning"):
        example_module.main()

    assert not output.exists()


def test_contract_materialization_rejects_a_preexisting_root_symlink(
    tmp_path: Path,
    example_module: ModuleType,
) -> None:
    runtime_module = sys.modules["contract_runtime"]
    study_root = tmp_path / "study"
    outside = tmp_path / "outside"
    study_root.mkdir()
    outside.mkdir()
    try:
        (study_root / "contract-runtime").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    snapshot = runtime_module.ContractSnapshot(
        manifest={},
        sources=(runtime_module.ContractSource("escaped.txt", "test", b"escaped"),),
    )

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        runtime_module.materialize_contract_runtime(study_root, snapshot)

    assert not (outside / "escaped.txt").exists()


def test_problem_materialization_does_not_resolve_a_symlinked_contract_parent(
    tmp_path: Path,
    example_module: ModuleType,
) -> None:
    study_root = tmp_path / "study"
    outside = tmp_path / "outside"
    study_root.mkdir()
    outside.mkdir()
    try:
        (study_root / "contracts").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    contract_root = example_module._direct_child(study_root / "contracts", "workload")
    assert contract_root == (study_root / "contracts" / "workload").absolute()
    with pytest.raises(RuntimeError, match="parent"):
        example_module._materialize_problem(contract_root, "problem", {"value": 1})

    assert not (outside / "workload").exists()


def test_contract_materialization_does_not_resolve_a_symlinked_study_root(
    tmp_path: Path,
    example_module: ModuleType,
) -> None:
    runtime_module = sys.modules["contract_runtime"]
    outside = tmp_path / "outside"
    outside.mkdir()
    study_root = tmp_path / "study"
    try:
        study_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    snapshot = runtime_module.ContractSnapshot(
        manifest={},
        sources=(runtime_module.ContractSource("escaped.txt", "test", b"escaped"),),
    )

    with pytest.raises(RuntimeError, match="parent"):
        runtime_module.materialize_contract_runtime(study_root, snapshot)

    assert not (outside / "contract-runtime").exists()


def test_run_directory_creation_does_not_follow_a_symlinked_parent(
    tmp_path: Path,
    example_module: ModuleType,
) -> None:
    runtime_module = sys.modules["contract_runtime"]
    study_root = tmp_path / "study"
    outside = tmp_path / "outside"
    study_root.mkdir()
    outside.mkdir()
    try:
        (study_root / "runs").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    output_root = example_module._direct_child(study_root / "runs", "workload")
    with pytest.raises(RuntimeError, match="parent"):
        with runtime_module.retained_safe_directory(output_root):
            pass

    assert not (outside / "workload").exists()


def test_initial_study_creation_rejects_an_output_root_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    output = tmp_path / "output"
    moved = tmp_path / "output-real"
    outside = tmp_path / "outside"
    real_resolve = example_module._resolve_study_root
    swapped = False

    def swap_after_resolve(requested_output: Path, study_id: str) -> Path:
        nonlocal swapped
        study_root = real_resolve(requested_output, study_id)
        study_root.parent.mkdir(parents=True)
        study_root.parent.rename(moved)
        outside.mkdir()
        study_root.parent.symlink_to(outside, target_is_directory=True)
        swapped = True
        return study_root

    monkeypatch.setattr(example_module, "_resolve_study_root", swap_after_resolve)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "creation-race"])

    with pytest.raises(RuntimeError, match="parent"):
        example_module.main()

    assert swapped
    assert output.is_symlink()
    assert not (outside / "creation-race").exists()
    assert not (moved / "creation-race").exists()


def test_exact_writers_do_not_follow_fixed_temporary_symlinks(
    tmp_path: Path,
    example_module: ModuleType,
) -> None:
    del example_module
    runtime_module = sys.modules["contract_runtime"]
    evidence_module = sys.modules["evidence_runtime"]
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for target, writer in (
        (bundle / "contract.json", lambda path: runtime_module.write_exact_bytes(path, b"after")),
        (bundle / "evidence.json", lambda path: evidence_module._write_exact_text(path, "after")),
    ):
        sentinel = tmp_path / f"{target.stem}-outside"
        sentinel.write_bytes(b"before")
        try:
            target.with_suffix(".tmp").symlink_to(sentinel)
        except OSError as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
        writer(target)
        assert sentinel.read_bytes() == b"before"
        assert target.read_bytes() == b"after"


def test_exact_writer_rejects_a_fifo_without_blocking(
    tmp_path: Path,
    example_module: ModuleType,
) -> None:
    del example_module
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable")
    target = tmp_path / "bundle" / "manifest.json"
    target.parent.mkdir()
    os.mkfifo(target)
    program = (
        "import sys; from pathlib import Path; "
        "sys.path.insert(0, sys.argv[1]); "
        "from contract_runtime import write_exact_bytes; "
        "write_exact_bytes(Path(sys.argv[2]), b'after')"
    )

    completed = subprocess.run(
        [sys.executable, "-c", program, str(_EXAMPLE.parent), str(target)],
        cwd=_PACKAGE_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode != 0
    assert "regular file" in completed.stderr
    assert target.exists()


def test_exact_writer_rejects_a_post_link_fifo_swap_without_blocking(
    tmp_path: Path,
    example_module: ModuleType,
) -> None:
    del example_module
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    program = "\n".join(
        (
            "import os, sys",
            "from pathlib import Path",
            "sys.path.insert(0, sys.argv[1])",
            "import contract_runtime as runtime",
            "directory = Path(sys.argv[2])",
            "directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)",
            "real_link = runtime.os.link",
            "def swap(source, destination, **kwargs):",
            "    real_link(source, destination, **kwargs)",
            "    runtime.os.unlink(destination, dir_fd=kwargs['dst_dir_fd'])",
            "    runtime.os.mkfifo(destination, dir_fd=kwargs['dst_dir_fd'])",
            "runtime.os.link = swap",
            "try:",
            "    runtime._write_exact_bytes_at(directory_fd, Path('victim'), b'payload')",
            "except RuntimeError:",
            "    print('rejected')",
            "else:",
            "    raise SystemExit('FIFO swap was accepted')",
            "finally:",
            "    os.close(directory_fd)",
        )
    )

    completed = subprocess.run(
        [sys.executable, "-c", program, str(_EXAMPLE.parent), str(bundle)],
        cwd=_PACKAGE_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "rejected"
    assert not (bundle / "victim").exists()


def test_exact_writers_reject_ancestor_symlinks_before_creating_outside_directories(
    tmp_path: Path,
    example_module: ModuleType,
) -> None:
    del example_module
    runtime_module = sys.modules["contract_runtime"]
    evidence_module = sys.modules["evidence_runtime"]
    bundle = tmp_path / "bundle"
    outside = tmp_path / "outside"
    bundle.mkdir()
    outside.mkdir()
    try:
        (bundle / "linked").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    target = bundle / "linked" / "created-outside" / "artifact.json"

    for writer in (
        lambda: runtime_module.write_exact_bytes(target, b"escaped"),
        lambda: evidence_module._write_exact_text(target, "escaped"),
    ):
        with pytest.raises(RuntimeError, match="parent"):
            writer()
        assert not (outside / "created-outside").exists()


def test_contract_materialization_rejects_nested_ancestor_symlink_without_side_effects(
    tmp_path: Path,
    example_module: ModuleType,
) -> None:
    runtime_module = sys.modules["contract_runtime"]
    study_root = tmp_path / "study"
    runtime_root = study_root / "contract-runtime"
    outside = tmp_path / "outside"
    runtime_root.mkdir(parents=True)
    outside.mkdir()
    try:
        (runtime_root / "package").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    snapshot = runtime_module.ContractSnapshot(
        manifest={},
        sources=(
            runtime_module.ContractSource(
                "package/created-outside/escaped.txt",
                "test",
                b"escaped",
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="parent"):
        runtime_module.materialize_contract_runtime(study_root, snapshot)

    assert not (outside / "created-outside").exists()


def test_exact_writer_keeps_verified_parent_open_through_atomic_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    del example_module
    runtime_module = sys.modules["contract_runtime"]
    if (
        not hasattr(os, "O_NOFOLLOW")
        or any(function not in os.supports_dir_fd for function in (os.open, os.mkdir, os.link, os.unlink))
    ):
        pytest.skip("directory-relative no-follow operations are unavailable")
    bundle = tmp_path / "bundle"
    nested = bundle / "nested"
    moved = bundle / "nested-real"
    outside = tmp_path / "outside"
    nested.mkdir(parents=True)
    outside.mkdir()
    real_write = runtime_module._write_exact_bytes_at
    swapped = False

    def swap_parent_before_publish(directory_fd: int, path: Path, content: bytes) -> None:
        nonlocal swapped
        nested.rename(moved)
        try:
            nested.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
        swapped = True
        real_write(directory_fd, path, content)

    monkeypatch.setattr(runtime_module, "_write_exact_bytes_at", swap_parent_before_publish)
    runtime_module.write_exact_bytes(nested / "artifact.json", b"anchored")

    assert swapped
    assert (moved / "artifact.json").read_bytes() == b"anchored"
    assert not (outside / "artifact.json").exists()


def test_exact_writer_fails_closed_without_directory_relative_no_follow_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    del example_module
    runtime_module = sys.modules["contract_runtime"]
    target = tmp_path / "bundle" / "nested" / "artifact.json"
    monkeypatch.setattr(runtime_module.os, "supports_dir_fd", set())

    with pytest.raises(RuntimeError, match="directory-relative no-follow"):
        runtime_module.write_exact_bytes(target, b"must-not-publish")

    assert not target.exists()
    assert not (tmp_path / "bundle").exists()


def test_exact_writer_detects_a_swapped_temporary_directory_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    del example_module
    runtime_module = sys.modules["contract_runtime"]
    target = tmp_path / "bundle" / "artifact.json"
    real_link = runtime_module.os.link
    swapped = False

    def swap_then_link(source: str, destination: str, **kwargs: Any) -> None:
        nonlocal swapped
        source_fd = kwargs["src_dir_fd"]
        runtime_module.os.unlink(source, dir_fd=source_fd)
        attacker = runtime_module.os.open(
            source,
            runtime_module.os.O_WRONLY
            | runtime_module.os.O_CREAT
            | runtime_module.os.O_EXCL
            | runtime_module.os.O_NOFOLLOW,
            0o600,
            dir_fd=source_fd,
        )
        try:
            runtime_module.os.write(attacker, b"attacker")
        finally:
            runtime_module.os.close(attacker)
        swapped = True
        real_link(source, destination, **kwargs)

    supported = set(runtime_module.os.supports_dir_fd)
    supported.add(swap_then_link)
    monkeypatch.setattr(runtime_module.os, "link", swap_then_link)
    monkeypatch.setattr(runtime_module.os, "supports_dir_fd", supported)
    with pytest.raises(RuntimeError, match="changed during atomic publication"):
        runtime_module.write_exact_bytes(target, b"expected")

    assert swapped
    assert not target.exists()


def test_success_bundle_rejects_a_swapped_study_root_without_publishing_a_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    runtime_module = sys.modules["contract_runtime"]
    output = tmp_path / "output"
    study_root = output / "root-swap"
    moved = tmp_path / "moved-real"
    outside = tmp_path / "outside"
    real_write = runtime_module._write_exact_bytes_at
    swapped = False

    def swap_root_before_publication(directory_fd: int, path: Path, content: bytes) -> tuple[int, int] | None:
        nonlocal swapped
        if path.name == "study_report.json" and not swapped:
            study_root.rename(moved)
            outside.mkdir()
            study_root.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_write(directory_fd, path, content)

    monkeypatch.setattr(runtime_module, "_write_exact_bytes_at", swap_root_before_publication)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "root-swap"])

    with pytest.raises(RuntimeError):
        example_module.main()

    assert swapped
    assert study_root.is_symlink()
    assert not (outside / "study_report.json").exists()
    assert not (outside / "study_artifacts.json").exists()
    assert not (moved / "study_report.json").exists()
    assert not (moved / "study_artifacts.json").exists()
    failure = json.loads((moved / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["failed_stage"] == "success publication"
    assert failure["error_type"] == "RuntimeError"
    assert not (outside / "study_failure.json").exists()


def test_failure_evidence_stays_with_the_original_study_root_after_real_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    runtime_module = sys.modules["contract_runtime"]
    output = tmp_path / "output"
    study_root = output / "real-root-swap"
    moved = tmp_path / "moved-real"
    real_write = runtime_module._write_exact_bytes_at
    swapped = False
    original_manifest_digest: str | None = None
    evil_manifest = b"evil replacement manifest\n"

    def replace_root_after_report(directory_fd: int, path: Path, content: bytes) -> tuple[int, int] | None:
        nonlocal original_manifest_digest, swapped
        identity = real_write(directory_fd, path, content)
        if path.name == "study_report.json" and not swapped:
            original_manifest_digest = example_module.digest(
                (study_root / "contract-runtime" / "manifest.json").read_bytes()
            )
            study_root.rename(moved)
            (study_root / "contract-runtime").mkdir(parents=True)
            (study_root / "contract-runtime" / "manifest.json").write_bytes(evil_manifest)
            swapped = True
        return identity

    monkeypatch.setattr(runtime_module, "_write_exact_bytes_at", replace_root_after_report)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "real-root-swap"])

    with pytest.raises(RuntimeError, match="study root changed"):
        example_module.main()

    assert swapped
    failure = json.loads((moved / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["failed_stage"] == "success publication"
    assert failure["error_type"] == "RuntimeError"
    assert original_manifest_digest is not None
    assert failure["contract_runtime_manifest_file_digest"] == original_manifest_digest
    assert failure["contract_runtime_manifest_file_digest"] != example_module.digest(evil_manifest)
    assert failure["contract_runtime_manifest_read_error"] is None
    assert not (moved / "study_report.json").exists()
    assert not (moved / "study_artifacts.json").exists()
    assert not (study_root / "study_failure.json").exists()
    assert not (study_root / "evidence" / "index.json").exists()


def test_workload_runner_stays_bound_to_its_retained_diagnostic_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    output = tmp_path / "output"
    study_root = output / "runner-root-swap"
    output_root = study_root / "runs" / "variable-shape-matmul"
    moved = tmp_path / "moved-run"
    outside = tmp_path / "outside"
    real_runner = example_module.KernelEvolutionRunner
    swapped = False

    def replace_then_construct(*args: Any, **kwargs: Any) -> Any:
        nonlocal swapped
        if not swapped:
            output_root.rename(moved)
            outside.mkdir()
            output_root.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_runner(*args, **kwargs)

    monkeypatch.setattr(example_module, "KernelEvolutionRunner", replace_then_construct)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "runner-root-swap"])

    with pytest.raises(RuntimeError, match="diagnostic directory changed"):
        example_module.main()

    assert swapped
    assert output_root.is_symlink()
    assert not list(outside.rglob("*"))
    assert list(moved.rglob("*"))
    failure = json.loads((study_root / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["failed_stage"] == "workload campaigns"
    assert failure["error_type"] == "RuntimeError"


def test_workload_execution_rejects_a_transient_replacement_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    output = tmp_path / "output"
    study_root = output / "transient-runtime-swap"
    moved = tmp_path / "retained-study"
    replacement = tmp_path / "attacker-study"
    real_run_workload = example_module._run_workload
    attacked = False

    def replace_then_run(plan: Any, **kwargs: Any) -> Any:
        nonlocal attacked
        if attacked:
            return real_run_workload(plan, **kwargs)
        attacked = True
        study_root.rename(moved)
        shutil.copytree(moved, study_root)
        adapter = study_root / "contract-runtime" / "example" / "adapter.py"
        content = adapter.read_text(encoding="utf-8")
        needle = "candidate_latency=candidate_latencies[family],"
        assert needle in content
        adapter.write_text(
            content.replace(needle, "candidate_latency=candidate_latencies[family] * 0.01,"),
            encoding="utf-8",
        )
        workload_id = example_module.validated_workload_id(plan.workload["workload_id"])
        attacker_contract = study_root / "contracts" / workload_id / "campaign-primary.json"
        attacker_contract.parent.mkdir(parents=True)
        attacker_contract.write_text(
            example_module._encoded_json(plan.contracts.campaign_primary), encoding="utf-8"
        )
        try:
            return real_run_workload(plan, **kwargs)
        finally:
            study_root.rename(replacement)
            moved.rename(study_root)

    monkeypatch.setattr(example_module, "_run_workload", replace_then_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run.py", "--output", str(output), "--study-id", "transient-runtime-swap"],
    )

    with pytest.raises(ValueError, match="precommitted content"):
        example_module.main()

    assert attacked
    assert not (study_root / "study_report.json").exists()
    assert not (study_root / "study_artifacts.json").exists()
    failure = json.loads((study_root / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["failed_stage"] == "workload campaigns"
    assert failure["error_type"] == "ValueError"


def test_workload_status_replay_is_charged_to_the_end_to_end_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    output = tmp_path / "output"
    offset = [0.0]
    real_monotonic = time.monotonic
    fake_time = SimpleNamespace(monotonic=lambda: real_monotonic() + offset[0])
    monkeypatch.setattr(example_module, "time", fake_time)
    monkeypatch.setattr(sys.modules["evidence_runtime"], "time", fake_time)
    real_status = example_module.read_kernel_campaign_status

    def delayed_status(*args: Any, **kwargs: Any) -> Any:
        status = real_status(*args, **kwargs)
        offset[0] += 301.0
        return status

    monkeypatch.setattr(example_module, "read_kernel_campaign_status", delayed_status)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run.py", "--output", str(output), "--study-id", "status-deadline"],
    )

    with pytest.raises(RuntimeError, match="end-to-end wall budget"):
        example_module.main()

    study_root = output / "status-deadline"
    assert offset[0] == 301.0
    assert not (study_root / "study_report.json").exists()
    assert not (study_root / "study_artifacts.json").exists()
    failure = json.loads((study_root / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["failed_stage"] == "workload campaigns"


def test_transfer_setup_is_charged_to_the_source_workload_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    output = tmp_path / "output"
    offset = [0.0]
    in_transfer = [False]
    real_monotonic = time.monotonic
    fake_time = SimpleNamespace(monotonic=lambda: real_monotonic() + offset[0])
    monkeypatch.setattr(example_module, "time", fake_time)
    monkeypatch.setattr(sys.modules["evidence_runtime"], "time", fake_time)
    real_transfer = example_module._transfer
    real_materialize_pair = example_module._materialize_pair

    def tracked_transfer(*args: Any, **kwargs: Any) -> Any:
        in_transfer[0] = True
        try:
            return real_transfer(*args, **kwargs)
        finally:
            in_transfer[0] = False

    def delayed_materialization(*args: Any, **kwargs: Any) -> Any:
        materialized = real_materialize_pair(*args, **kwargs)
        if in_transfer[0] and offset[0] == 0.0:
            offset[0] += 301.0
        return materialized

    monkeypatch.setattr(example_module, "_transfer", tracked_transfer)
    monkeypatch.setattr(example_module, "_materialize_pair", delayed_materialization)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run.py", "--output", str(output), "--study-id", "transfer-deadline"],
    )

    with pytest.raises(RuntimeError, match="before transfer dispatch"):
        example_module.main()

    study_root = output / "transfer-deadline"
    assert offset[0] == 301.0
    assert not (study_root / "study_report.json").exists()
    assert not (study_root / "study_artifacts.json").exists()
    failure = json.loads((study_root / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["failed_stage"] == "transfer evaluations"


@pytest.mark.parametrize("replaced_name", ["study_report.json", "study_artifacts.json"])
def test_success_bundle_rejects_output_replacement_after_marker_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
    replaced_name: str,
) -> None:
    runtime_module = sys.modules["contract_runtime"]
    real_write = runtime_module._write_exact_bytes_at
    replaced = False

    def replace_after_marker(directory_fd: int, path: Path, content: bytes) -> tuple[int, int] | None:
        nonlocal replaced
        identity = real_write(directory_fd, path, content)
        if path.name == "study_artifacts.json" and not replaced:
            runtime_module.os.unlink(replaced_name, dir_fd=directory_fd)
            attacker = runtime_module.os.open(
                replaced_name,
                runtime_module.os.O_WRONLY
                | runtime_module.os.O_CREAT
                | runtime_module.os.O_EXCL
                | runtime_module.os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            try:
                runtime_module.os.write(attacker, b"attacker replacement\n")
                runtime_module.os.fsync(attacker)
            finally:
                runtime_module.os.close(attacker)
            replaced = True
        return identity

    output = tmp_path / "output"
    monkeypatch.setattr(runtime_module, "_write_exact_bytes_at", replace_after_marker)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "output-swap"])
    with pytest.raises(RuntimeError, match="identity changed|was replaced"):
        example_module.main()

    study_root = output / "output-swap"
    assert replaced
    assert not (study_root / "study_report.json").exists()
    assert not (study_root / "study_artifacts.json").exists()


def test_portable_inventory_stays_bound_to_the_retained_study_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    real_inventory = example_module.portable_file_inventory
    output = tmp_path / "output"
    study_root = output / "inventory-swap"
    moved = tmp_path / "moved-real"
    outside = tmp_path / "outside"
    swapped = False

    def swapped_inventory(
        root: Path,
        *,
        report_content: bytes,
        directory_fd: int,
    ) -> tuple[dict[str, Any], ...]:
        nonlocal swapped
        if swapped:
            return real_inventory(root, report_content=report_content, directory_fd=directory_fd)
        root.rename(moved)
        outside.mkdir()
        (outside / "manifest.json").write_bytes(b"attacker manifest\n")
        root.symlink_to(outside, target_is_directory=True)
        swapped = True
        try:
            return real_inventory(root, report_content=report_content, directory_fd=directory_fd)
        finally:
            root.unlink()
            moved.rename(root)

    monkeypatch.setattr(example_module, "portable_file_inventory", swapped_inventory)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "inventory-swap"])
    example_module.main()

    marker = json.loads((study_root / "study_artifacts.json").read_text(encoding="utf-8"))
    inventory = {item["path"]: item for item in marker["portable_files"]}
    assert swapped
    assert inventory["manifest.json"]["digest"] == content_digest((study_root / "manifest.json").read_bytes())
    assert "evidence/index.json" in inventory
    assert "contract-runtime/manifest.json" in inventory
    assert not (outside / "study_artifacts.json").exists()


def test_contract_snapshot_failure_writes_terminal_failure_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    def fail_snapshot() -> None:
        raise OSError("injected snapshot failure")

    output = tmp_path / "output"
    monkeypatch.setattr(example_module, "contract_snapshot", fail_snapshot)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "snapshot-failure"])
    with pytest.raises(OSError, match="injected snapshot failure"):
        example_module.main()

    failure = json.loads((output / "snapshot-failure" / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["failed_stage"] == "study setup"
    assert failure["study_contract_digest"] is None


def test_contract_bundle_setup_failure_writes_terminal_failure_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    def fail_reference_sources(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected contract bundle write failure")

    output = tmp_path / "output"
    monkeypatch.setattr(example_module, "materialize_reference_sources", fail_reference_sources)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "setup-failure"])
    with pytest.raises(OSError, match="injected contract bundle write failure"):
        example_module.main()

    study_root = output / "setup-failure"
    failure = json.loads((study_root / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["failed_stage"] == "study setup"
    assert failure["evidence_index_digest"] is None
    assert not (study_root / "study_report.json").exists()
    assert not (study_root / "study_artifacts.json").exists()


def test_campaign_failure_writes_manifest_bound_failure_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    def fail_campaign(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("injected baseline infrastructure failure")

    output = tmp_path / "output"
    monkeypatch.setattr(example_module, "_run_workload", fail_campaign)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run.py", "--output", str(output), "--study-id", "fail-closed"],
    )
    with pytest.raises(RuntimeError, match="injected baseline infrastructure failure"):
        example_module.main()

    study_root = output / "fail-closed"
    failure = json.loads((study_root / "study_failure.json").read_text(encoding="utf-8"))
    index_path = study_root / "evidence" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    manifest_digest = content_digest((study_root / "manifest.json").read_bytes())
    assert failure["failed_stage"] == "workload campaigns"
    assert failure["error_type"] == "RuntimeError"
    assert failure["study_manifest_digest"] == manifest_digest
    assert failure["study_contract_digest"] == example_module._contract_digest()
    assert failure["study_backend_identity"] == example_module.SYNTHETIC_BACKEND_IDENTITY
    assert failure["evidence_warning"]
    assert failure["evidence_index_digest"] == content_digest(index_path.read_bytes())
    assert index["study_manifest_digest"] == failure["study_manifest_digest"]
    assert index["study_contract_digest"] == failure["study_contract_digest"]
    assert index["study_backend_identity"] == failure["study_backend_identity"]
    assert index["evidence_warning"] == failure["evidence_warning"]
    assert not (study_root / "study_report.json").exists()


def test_failure_receipt_survives_a_missing_runtime_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    def corrupt_runtime(*_args: Any, **kwargs: Any) -> None:
        kwargs["binding"].runtime.manifest_path.unlink()
        raise RuntimeError("injected runtime corruption")

    output = tmp_path / "output"
    monkeypatch.setattr(example_module, "_run_workload", corrupt_runtime)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "runtime-corruption"])
    with pytest.raises(RuntimeError, match="injected runtime corruption"):
        example_module.main()

    failure = json.loads((output / "runtime-corruption" / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["error"] == "injected runtime corruption"
    assert failure["contract_runtime_manifest_file_digest"] is None
    assert "FileNotFoundError" in failure["contract_runtime_manifest_read_error"]


def test_artifact_manifest_failure_removes_partial_success_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    original_encode = example_module._encoded_json

    def fail_artifact_manifest(payload: dict[str, Any]) -> str:
        if payload.get("schema_version") == "autocontext.synthetic-kernel-study-artifacts/v2":
            raise OSError("injected artifact manifest failure")
        return original_encode(payload)

    output = tmp_path / "output"
    monkeypatch.setattr(example_module, "_encoded_json", fail_artifact_manifest)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "atomic-failure"])
    with pytest.raises(OSError, match="injected artifact manifest failure"):
        example_module.main()

    study_root = output / "atomic-failure"
    failure = json.loads((study_root / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["failed_stage"] == "artifact manifest"
    assert not (study_root / "study_report.json").exists()
    assert not (study_root / "study_artifacts.json").exists()


def test_interrupted_success_publication_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    runtime_module = sys.modules["contract_runtime"]
    original_write = runtime_module._write_exact_bytes_at

    def interrupt_publication(directory_fd: int, path: Path, content: bytes) -> tuple[int, int] | None:
        identity = original_write(directory_fd, path, content)
        if path.name == "study_artifacts.json":
            raise KeyboardInterrupt("injected publication interruption")
        return identity

    output = tmp_path / "output"
    monkeypatch.setattr(runtime_module, "_write_exact_bytes_at", interrupt_publication)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "interrupted"])
    with pytest.raises(KeyboardInterrupt, match="injected publication interruption"):
        example_module.main()

    study_root = output / "interrupted"
    failure = json.loads((study_root / "study_failure.json").read_text(encoding="utf-8"))
    assert failure["error_type"] == "KeyboardInterrupt"
    assert failure["failed_stage"] == "success publication"
    assert not (study_root / "study_report.json").exists()
    assert not (study_root / "study_artifacts.json").exists()
    assert not list(study_root.glob(".study_*.json"))


def test_post_commit_interruption_does_not_publish_conflicting_failure_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
) -> None:
    real_publish = example_module.publish_exact_bundle

    def publish_then_interrupt(*args: Any, **kwargs: Any) -> None:
        real_publish(*args, **kwargs)
        raise KeyboardInterrupt("after exact commit")

    output = tmp_path / "output"
    monkeypatch.setattr(example_module, "publish_exact_bundle", publish_then_interrupt)
    monkeypatch.setattr(sys, "argv", ["run.py", "--output", str(output), "--study-id", "post-commit"])

    with pytest.raises(KeyboardInterrupt, match="after exact commit"):
        example_module.main()

    study_root = output / "post-commit"
    assert (study_root / "study_report.json").is_file()
    assert (study_root / "study_artifacts.json").is_file()
    assert not (study_root / "study_failure.json").exists()


def test_failed_reserved_evaluation_returns_candidate_bound_rejection(
    example_module: ModuleType,
) -> None:
    baseline = KernelCandidate(
        source="def kernel_fn(value):\n    return value\n",
        source_suffix=".py",
        entrypoint="kernel_fn",
    )
    candidate = KernelCandidate(
        source="def kernel_fn(value):\n    return value + 1\n",
        source_suffix=".py",
        entrypoint="kernel_fn",
    )

    class FailingReservedEvaluator:
        def __init__(self) -> None:
            self.config = KernelBenchmarkEvaluatorConfig(problem_id="injected-scope-failure")
            self.calls = 0

        def evaluate_reserved(
            self,
            evaluated: KernelCandidate,
            incumbent: KernelCandidate,
        ) -> KernelBenchmarkObservation:
            self.calls += 1
            return KernelBenchmarkObservation(
                artifact_identity_version=evaluated.artifact_identity_version,
                candidate_artifact_digest=evaluated.artifact_digest,
                incumbent_artifact_digest=incumbent.artifact_digest,
                candidate_source_digest=evaluated.source_digest,
                incumbent_source_digest=incumbent.source_digest,
                eligible=False,
                rejection_reason="infrastructure_error",
                feedback="pinned baseline unavailable",
                statistics_policy=self.config.statistics_policy,
                stderr="injected diagnostic",
            )

    evaluator = FailingReservedEvaluator()
    look = example_module.evaluate_final(evaluator, candidate=candidate, baseline=baseline)
    assert evaluator.calls == 1
    assert look.wall_seconds > 0.0
    assert look.observation.report is None
    assert look.observation.eligible is False
    assert look.observation.rejection_reason == "infrastructure_error"
    assert look.observation.candidate_artifact_digest == candidate.artifact_digest
    assert look.observation.candidate_source_digest == candidate.source_digest
    assert look.observation.incumbent_artifact_digest == baseline.artifact_digest
    assert look.observation.stderr == "injected diagnostic"


def test_deadline_rejection_builds_valid_reportless_phase_evidence(example_module: ModuleType) -> None:
    baseline = KernelCandidate(source="class ModelNew:\n    pass\n")
    candidate = KernelCandidate(source="class ModelNew:\n    version = 2\n")
    observation = sys.modules["evidence_runtime"].rejected_observation(
        evaluator=SimpleNamespace(config=KernelBenchmarkEvaluatorConfig(problem_id="deadline-test")),
        candidate=candidate,
        incumbent=baseline,
        reason="study_wall_budget_exceeded",
        feedback="deadline",
    )
    phase = KernelWorkloadPhaseEvidence(
        role="primary",
        observation=observation,
        reservation=KernelProtocolReservation(
            protocol_id=content_digest(b"deadline-protocol"),
            plan_commitment=content_digest(b"deadline-plan"),
            hardware_scope_id=content_digest(b"deadline-hardware"),
            execution_environment_id=content_digest(b"deadline-environment"),
        ),
        evaluation_wall_seconds=0.1,
        evaluation_cost_usd=0.0,
    )

    assert phase.observation.report is None
    assert phase.observation.statistics_policy == KernelBenchmarkEvaluatorConfig(
        problem_id="deadline-test"
    ).statistics_policy


def test_protected_evaluator_refuses_a_second_look(example_module: ModuleType) -> None:
    baseline = KernelCandidate(source="def kernel_fn(value):\n    return value\n", source_suffix=".py", entrypoint="kernel_fn")
    candidate = KernelCandidate(
        source="def kernel_fn(value):\n    return value + 1\n", source_suffix=".py", entrypoint="kernel_fn"
    )

    class Harness:
        _reserved_consumed = False
        _single_use = True
        _expected_scope_id = "expected-scope"
        _expected_baseline_id = "expected-baseline"

        class _Protocol:
            protocol_id = "expected-protocol"

        _planned_protocol = _Protocol()

        def _evaluate_once(
            self,
            evaluated: KernelCandidate,
            incumbent: KernelCandidate,
            **_expected: Any,
        ) -> KernelBenchmarkObservation:
            return KernelBenchmarkObservation(
                artifact_identity_version=evaluated.artifact_identity_version,
                candidate_artifact_digest=evaluated.artifact_digest,
                incumbent_artifact_digest=incumbent.artifact_digest,
                candidate_source_digest=evaluated.source_digest,
                incumbent_source_digest=incumbent.source_digest,
                eligible=False,
                rejection_reason="injected_failure",
                feedback="injected failure",
            )

    evaluator = Harness()
    with pytest.raises(RuntimeError, match="evaluate_reserved"):
        example_module.StudyEvaluator.evaluate(evaluator, candidate, baseline)
    first = example_module.StudyEvaluator.evaluate_reserved(evaluator, candidate, baseline)
    assert first.candidate_artifact_digest == candidate.artifact_digest
    with pytest.raises(RuntimeError, match="already consumed"):
        example_module.StudyEvaluator.evaluate_reserved(evaluator, candidate, baseline)


def test_transfer_outcomes_only_credit_dimensions_that_passed(study_report: KernelWorkloadStudyReport) -> None:
    assert len(study_report.transfers) == 6
    assessments = {item.source_workload_id: item for item in study_report.champion_assessments}
    assert set(assessments["variable-shape-matmul"].covered_dimensions) == {
        "shape",
        "hardware",
        "workload-family",
    }
    assert set(assessments["fused-elementwise-reduction"].covered_dimensions) == {"hardware"}
    assert assessments["variable-shape-matmul"].disposition == "cross-workload"
    assert assessments["fused-elementwise-reduction"].disposition == "specialist"
    assert study_report.portable_champion_artifact_digests == ()


def test_embedded_runner_report_tamper_breaks_its_digest(study_report: KernelWorkloadStudyReport) -> None:
    payload = _payload(study_report)
    payload["workload_runs"][0]["result"]["attempts"][0]["observation"]["report"]["metadata"]["tampered"] = True
    with pytest.raises(ValidationError, match="report digest"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_ineligible_campaign_report_cannot_be_transplanted_from_another_artifact(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = _payload(study_report)
    run = next(
        item
        for item in payload["workload_runs"]
        if any(
            attempt["role"] == "candidate" and not attempt["observation"]["eligible"] for attempt in item["result"]["attempts"]
        )
    )
    baseline_report = copy.deepcopy(run["result"]["attempts"][0]["observation"]["report"])
    attempt = next(
        attempt
        for attempt in run["result"]["attempts"]
        if attempt["role"] == "candidate" and not attempt["observation"]["eligible"]
    )
    attempt["observation"]["report"] = baseline_report
    attempt["report_digest"] = kernel_benchmark_report_digest(KernelBenchmarkReport.model_validate(baseline_report))
    with pytest.raises(ValidationError, match="artifact identity does not match its raw report"):
        KernelWorkloadStudyReport.model_validate(payload)


@pytest.mark.parametrize("evidence_kind", ["final", "transfer"])
def test_ineligible_protected_report_must_match_its_observation_pair(
    study_report: KernelWorkloadStudyReport,
    evidence_kind: str,
) -> None:
    payload = _payload(study_report)
    phase = payload["workload_runs"][0]["primary"] if evidence_kind == "final" else payload["transfers"][0]["primary"]
    _make_ineligible(phase["observation"])
    _replace_report_candidate_with_incumbent(phase["observation"]["report"])
    with pytest.raises(ValidationError, match="artifact identity does not match its raw report"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_aggregate_verification_cannot_hide_raw_final_failure(study_report: KernelWorkloadStudyReport) -> None:
    payload = _payload(study_report)
    _make_ineligible(payload["workload_runs"][0]["primary"]["observation"])
    with pytest.raises(ValidationError, match="verification flag"):
        KernelWorkloadStudyReport.model_validate(payload)


@pytest.mark.parametrize("field", ["problem_id", "baseline_id", "protocol_id", "hardware_scope_id"])
def test_result_identity_must_match_its_pinned_contract(
    study_report: KernelWorkloadStudyReport,
    field: str,
) -> None:
    payload = _payload(study_report)
    payload["workload_runs"][0]["result"][field] = payload["workload_runs"][1]["result"][field]
    with pytest.raises(ValidationError):
        KernelWorkloadStudyReport.model_validate(payload)


def test_final_self_comparison_cannot_replace_the_reference_incumbent(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = _payload(study_report)
    for phase_name in ("primary", "confirmation"):
        observation = payload["workload_runs"][0][phase_name]["observation"]
        _make_ineligible(observation)
        benchmark = observation["report"]
        observation["incumbent_artifact_digest"] = observation["candidate_artifact_digest"]
        observation["incumbent_source_digest"] = observation["candidate_source_digest"]
        benchmark["incumbent_artifact_digest"] = benchmark["candidate_artifact_digest"]
        benchmark["incumbent_source_digest"] = benchmark["candidate_source_digest"]
        benchmark["incumbent_source_suffix"] = benchmark["candidate_source_suffix"]
        benchmark["incumbent_entrypoint"] = benchmark["candidate_entrypoint"]
        benchmark["resources"]["incumbent_artifact_digest"] = benchmark["candidate_artifact_digest"]
    with pytest.raises(ValidationError, match="wrong incumbent reference"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_final_and_transfer_protocol_reuse_is_rejected(study_report: KernelWorkloadStudyReport) -> None:
    final_reuse = _payload(study_report)
    final_reuse["workload_runs"][1]["primary"]["reservation"] = copy.deepcopy(
        final_reuse["workload_runs"][0]["primary"]["reservation"]
    )
    with pytest.raises(ValidationError, match="reserved protocol, plan, or hardware"):
        KernelWorkloadStudyReport.model_validate(final_reuse)

    duplicated = (study_report.transfers[0], study_report.transfers[0], *study_report.transfers[2:])
    with pytest.raises(ValueError, match="reused a burned protocol or private plan"):
        build_kernel_workload_study_report(
            study_name=study_report.study_name,
            provenance=study_report.provenance,
            specs=study_report.workload_specs,
            runs=study_report.workload_runs,
            transfers=duplicated,
            created_at=study_report.created_at,
        )


def test_campaign_confirmations_consume_reservations_in_precommitted_order(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run = next(
        item
        for item in study_report.workload_runs
        if any(attempt.confirmation_observation is not None for attempt in item.result.attempts)
    )
    spec = next(item for item in study_report.workload_specs if item.workload_id == run.workload_id)
    swapped_payload = spec.model_dump(mode="json")
    swapped_payload["confirmation_protocols"][:2] = reversed(swapped_payload["confirmation_protocols"][:2])
    swapped = KernelWorkloadSpec.model_validate(swapped_payload)
    rebound = run.model_copy(
        update={
            "workload_spec_id": swapped.spec_id,
            "budget_id": swapped.budget.budget_id,
            "generation_budget_id": swapped.budget.generation_budget.budget_id,
        }
    )
    with pytest.raises(ValueError, match="next precommitted reservation"):
        validate_run_against_spec(rebound, swapped)


def test_reportless_confirmation_attempt_still_burns_its_reserved_plan(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run = next(
        item
        for item in study_report.workload_runs
        if any(attempt.confirmation_decision is not None for attempt in item.result.attempts)
    )
    spec = next(item for item in study_report.workload_specs if item.workload_id == run.workload_id)
    attempt = next(item for item in run.result.attempts if item.confirmation_decision is not None)
    reportless_attempt = attempt.model_copy(update={"confirmation_observation": None})
    attempts = [reportless_attempt if item.attempt_id == attempt.attempt_id else item for item in run.result.attempts]
    reportless_run = run.model_copy(update={"result": run.result.model_copy(update={"attempts": attempts})})
    burns = protocol_burns(study_report.workload_specs, (reportless_run,), ())
    burn = next(item for item in burns if item.kind == "campaign-confirmation")
    assert (burn.protocol_id, burn.plan_commitment) == (
        spec.confirmation_protocols[0].protocol_id,
        spec.confirmation_protocols[0].plan_commitment,
    )


def test_reportless_campaign_observation_cannot_retain_report_derived_claims(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = _payload(study_report)
    attempt = next(
        attempt
        for run in payload["workload_runs"]
        for attempt in run["result"]["attempts"]
        if attempt["role"] == "candidate"
        and not attempt["observation"]["eligible"]
        and attempt["observation"]["report"] is not None
    )
    assert attempt["observation"]["report"] is not None
    attempt["observation"]["report"] = None
    attempt["report_digest"] = None
    with pytest.raises(ValidationError, match="reportless phase observations"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_reservation_models_reject_intrinsic_hardware_scope_drift(
    study_report: KernelWorkloadStudyReport,
) -> None:
    spec = study_report.workload_specs[0]
    spec_payload = spec.model_dump(mode="json")
    spec_payload["final_confirmation_protocol"]["hardware_scope_id"] = content_digest(b"different-scope")
    with pytest.raises(ValidationError, match="pinned hardware scope"):
        KernelWorkloadSpec.model_validate(spec_payload)

    route_payload = spec.transfer_protocols[0].model_dump(mode="json")
    route_payload["confirmation"]["hardware_scope_id"] = content_digest(b"different-transfer-scope")
    with pytest.raises(ValidationError, match="same target hardware scope"):
        KernelTransferProtocolReservation.model_validate(route_payload)


@pytest.mark.parametrize("field", ["protocol_id", "plan_commitment"])
def test_phase_pairs_require_protocol_and_plan_freshness_independently(
    study_report: KernelWorkloadStudyReport,
    field: str,
) -> None:
    payload = study_report.transfers[0].model_dump(mode="json")
    _make_reportless(payload["primary"]["observation"])
    _make_reportless(payload["confirmation"]["observation"])
    payload["confirmation"]["reservation"][field] = payload["primary"]["reservation"][field]
    with pytest.raises(ValidationError, match="fresh"):
        KernelTransferEvidence.model_validate(payload)


@pytest.mark.parametrize("route_kind", ["same-workload-hardware", "cross-workload-non-hardware"])
def test_unused_transfer_reservations_are_intrinsically_hardware_bound(
    study_report: KernelWorkloadStudyReport,
    route_kind: str,
) -> None:
    spec = study_report.workload_specs[0]
    payload = spec.model_dump(mode="json")
    if route_kind == "same-workload-hardware":
        route = next(item for item in payload["transfer_protocols"] if item["source_workload_id"] == spec.workload_id)
        for phase in ("primary", "confirmation"):
            route[phase]["hardware_scope_id"] = spec.primary_protocol.hardware_scope_id
            route[phase]["execution_environment_id"] = spec.execution_environment_id
        expected = "same-workload hardware transfer"
    else:
        route = next(item for item in payload["transfer_protocols"] if "hardware" not in item["dimensions"])
        for phase in ("primary", "confirmation"):
            route[phase]["hardware_scope_id"] = content_digest(b"unreserved-target-scope")
            route[phase]["execution_environment_id"] = content_digest(b"unreserved-target-environment")
        expected = "non-hardware transfer reservation"
    with pytest.raises(ValidationError, match=expected):
        KernelWorkloadSpec.model_validate(payload)


def test_transfer_dimensions_have_one_canonical_route_identity(study_report: KernelWorkloadStudyReport) -> None:
    route = next(item for item in study_report.workload_specs[0].transfer_protocols if len(item.dimensions) > 1)
    payload = route.model_dump(mode="json")
    payload["dimensions"] = list(reversed(payload["dimensions"]))
    with pytest.raises(ValidationError, match="canonical sorted order"):
        KernelTransferProtocolReservation.model_validate(payload)


@pytest.mark.parametrize("mutation", ["wrong-slices", "single-split"])
def test_workload_specs_require_satisfiable_train_and_holdout_coverage(
    study_report: KernelWorkloadStudyReport,
    mutation: str,
) -> None:
    payload = study_report.workload_specs[0].model_dump(mode="json")
    if mutation == "wrong-slices":
        payload["required_correctness_slices"] = ["public", "private"]
        expected = "exactly train and holdout"
    else:
        payload["required_benchmark_cases"] = [f"train:{case.split(':', 1)[1]}" for case in payload["required_benchmark_cases"]]
        expected = "cover both train and holdout"
    with pytest.raises(ValidationError, match=expected):
        KernelWorkloadSpec.model_validate(payload)


def test_family_labels_and_ids_are_one_to_one(study_report: KernelWorkloadStudyReport) -> None:
    payload = _payload(study_report)
    shared = payload["workload_specs"][0]["workload_family_id"]
    for spec in payload["workload_specs"]:
        spec["workload_family_id"] = shared
    _rebind_workload_specs(payload)
    with pytest.raises(ValidationError, match="one-to-one mapping"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_same_workload_hardware_transfer_cannot_claim_shape_coverage(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = _payload(study_report)
    transfer = next(item for item in payload["transfers"] if item["dimensions"] == ["hardware"])
    source_id = transfer["source_workload_id"]
    source_run = next(item for item in payload["workload_runs"] if item["workload_id"] == source_id)
    assert (
        transfer["primary"]["observation"]["report"]["hardware"]["workload_fingerprint"]
        == source_run["primary"]["observation"]["report"]["hardware"]["workload_fingerprint"]
    )
    transfer["dimensions"].append("shape")
    spec_payload = next(item for item in payload["workload_specs"] if item["workload_id"] == source_id)
    route = next(
        item
        for item in spec_payload["transfer_protocols"]
        if item["source_workload_id"] == source_id and item["dimensions"] == ["hardware"]
    )
    route["dimensions"].append("shape")
    with pytest.raises(ValidationError, match="hardware-only"):
        KernelWorkloadSpec.model_validate(spec_payload)


def test_hardware_transfer_must_cross_execution_environment(study_report: KernelWorkloadStudyReport) -> None:
    payload = _payload(study_report)
    transfer = next(item for item in payload["transfers"] if item["dimensions"] == ["hardware"])
    source_run = next(item for item in payload["workload_runs"] if item["workload_id"] == transfer["source_workload_id"])
    for phase_name in ("primary", "confirmation"):
        hardware = copy.deepcopy(source_run[phase_name]["observation"]["report"]["hardware"])
        scope_id = KernelHardwareIdentity.model_validate(hardware).scope_id
        transfer[phase_name]["observation"]["report"]["hardware"] = hardware
        transfer[phase_name]["observation"]["report"]["hardware_scope_id"] = scope_id
        transfer[phase_name]["observation"]["hardware_scope_id"] = scope_id
    with pytest.raises(ValidationError, match="reserved protocol, plan, or hardware"):
        KernelWorkloadStudyReport.model_validate(payload)


@pytest.mark.parametrize("mutation", ["wrong-floor", "missing-case"])
def test_ineligible_campaign_report_cannot_bypass_case_contract(
    study_report: KernelWorkloadStudyReport,
    mutation: str,
) -> None:
    run, spec = _run_and_spec(study_report)
    baseline = run.result.attempts[0]
    report = baseline.observation.report
    assert report is not None and report.performance is not None
    cases = list(report.performance.cases)
    if mutation == "wrong-floor":
        cases[0] = cases[0].model_copy(update={"minimum_speedup_vs_incumbent": 0.1})
        expected = "wrong per-case"
    else:
        cases.pop()
        expected = "performance case coverage"
    altered_report = report.model_copy(update={"performance": report.performance.model_copy(update={"cases": cases})})
    altered_observation = baseline.observation.model_copy(
        update={
            "eligible": False,
            "rejection_reason": "adversarial-policy-rejection",
            "derived_statistics_receipt": None,
            "report": altered_report,
        }
    )
    altered_attempt = baseline.model_copy(update={"observation": altered_observation})
    attempts = [altered_attempt, *run.result.attempts[1:]]
    altered = run.model_copy(update={"result": run.result.model_copy(update={"attempts": attempts})})
    with pytest.raises(ValueError, match=expected):
        validate_run_against_spec(altered, spec)


def test_final_report_must_cover_every_pinned_performance_case(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    report = run.primary.observation.report
    assert report is not None and report.performance is not None
    performance = report.performance.model_copy(update={"cases": report.performance.cases[:-1]})
    observation = run.primary.observation.model_copy(update={"report": report.model_copy(update={"performance": performance})})
    altered = run.model_copy(update={"primary": run.primary.model_copy(update={"observation": observation})})
    with pytest.raises(ValueError, match="performance case coverage"):
        validate_run_against_spec(altered, spec)


def test_source_final_failure_remains_authoritative_over_same_source_transfer(
    study_report: KernelWorkloadStudyReport,
) -> None:
    source_id = study_report.champion_assessments[0].source_workload_id
    source_run = next(item for item in study_report.workload_runs if item.workload_id == source_id)
    run_payload = source_run.model_dump(mode="json")
    _make_conclusive_correctness_failure(run_payload["primary"]["observation"])
    failed_run = KernelWorkloadRunEvidence.model_validate(run_payload)
    runs = tuple(failed_run if item.workload_id == source_id else item for item in study_report.workload_runs)
    rebuilt = build_kernel_workload_study_report(
        study_name=study_report.study_name,
        provenance=study_report.provenance,
        specs=study_report.workload_specs,
        runs=runs,
        transfers=study_report.transfers,
        created_at=study_report.created_at,
    )
    assessment = next(item for item in rebuilt.champion_assessments if item.source_workload_id == source_id)
    assert source_id in assessment.failed_workload_ids
    assert source_id not in assessment.passed_workload_ids
    assert assessment.disposition == "specialist"
    assert assessment.candidate_artifact_digest not in rebuilt.portable_champion_artifact_digests


def test_generation_budget_state_rejects_naked_forged_totals() -> None:
    with pytest.raises(ValidationError, match="total_tokens cannot be smaller"):
        KernelGenerationBudgetState(input_tokens=20, output_tokens=30, total_tokens=1)


def test_generation_source_bytes_replay_against_the_pinned_budget(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    generation_budget = spec.budget.generation_budget.model_copy(update={"max_source_bytes": 1})
    budget = spec.budget.model_copy(update={"generation_budget": generation_budget})
    bounded_spec = spec.model_copy(update={"budget": budget})
    rebound = run.model_copy(
        update={
            "workload_spec_id": bounded_spec.spec_id,
            "budget_id": budget.budget_id,
            "generation_budget_id": generation_budget.budget_id,
        }
    )
    with pytest.raises(ValueError, match="exact source-byte budget"):
        validate_run_against_spec(rebound, bounded_spec)


def test_generation_per_call_output_budget_replays_from_receipts(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    receipt = run.generation_results[0]
    output_tokens = spec.budget.generation_budget.max_output_tokens_per_call + 1
    usage = KernelGenerationUsage(
        input_tokens=1,
        output_tokens=output_tokens,
        total_tokens=output_tokens + 1,
        provider_usage={"input_tokens": 1, "output_tokens": output_tokens, "total_tokens": output_tokens + 1},
    )
    altered_receipt = receipt.model_copy(update={"usage": usage, "cost_source": "estimated-model-pricing-v1"})
    altered = run.model_copy(update={"generation_results": (altered_receipt, *run.generation_results[1:])})
    with pytest.raises(ValueError, match="per-call output-token budget"):
        validate_run_against_spec(altered, spec)


def test_generation_provider_usage_must_replay_directional_normalization(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    receipt = run.generation_results[0]
    usage = KernelGenerationUsage(provider_usage={"total_tokens": 10})
    altered_receipt = receipt.model_copy(update={"usage": usage, "cost_source": "estimated-model-pricing-v1"})
    altered = run.model_copy(update={"generation_results": (altered_receipt, *run.generation_results[1:])})
    with pytest.raises(ValueError, match="directional provider counters"):
        validate_run_against_spec(altered, spec)


def test_generation_provider_directional_aliases_cannot_contradict(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    receipt = run.generation_results[0]
    usage = KernelGenerationUsage(
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
        provider_usage={
            "input_tokens": 1,
            "output_tokens": 1,
            "prompt_tokens": 50_000,
            "completion_tokens": 50_000,
            "total_tokens": 2,
        },
    )
    altered_receipt = receipt.model_copy(update={"usage": usage, "cost_source": "estimated-model-pricing-v1"})
    altered = run.model_copy(update={"generation_results": (altered_receipt, *run.generation_results[1:])})
    with pytest.raises(ValueError, match="directional token aliases disagree"):
        validate_run_against_spec(altered, spec)


def test_generation_receipt_rejects_unsupported_nonzero_provider_counters(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    receipt = run.generation_results[0]
    usage = receipt.usage.model_copy(
        update={"provider_usage": {**receipt.usage.provider_usage, "cached_tokens": 100_000}}
    )
    altered_receipt = receipt.model_copy(
        update={"usage": usage, "cost_source": "estimated-model-pricing-v1"}
    )
    altered = run.model_copy(
        update={"generation_results": (altered_receipt, *run.generation_results[1:])}
    )

    with pytest.raises(ValueError, match="unsupported nonzero provider counter"):
        validate_run_against_spec(altered, spec)


def test_workload_generation_history_cannot_mix_providers(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    assert len(run.generation_results) >= 2
    second = run.generation_results[1].model_copy(update={"provider": "different-provider"})
    altered = run.model_copy(
        update={"generation_results": (run.generation_results[0], second, *run.generation_results[2:])}
    )

    with pytest.raises(ValueError, match="different provider"):
        validate_run_against_spec(altered, spec)


def test_workload_generation_replay_revalidates_unchecked_model_copies(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    receipt = run.generation_results[0]
    provider_usage = dict(receipt.usage.provider_usage)
    provider_usage["input_tokens"] = False  # type: ignore[assignment]
    forged_usage = receipt.usage.model_copy(
        update={"input_tokens": False, "provider_usage": provider_usage}
    )
    forged = receipt.model_copy(update={"usage": forged_usage})
    altered = run.model_copy(update={"generation_results": (forged, *run.generation_results[1:])})

    with pytest.raises(ValueError, match="valid integer"):
        validate_run_against_spec(altered, spec)


@pytest.mark.parametrize("alias", ["prompt_tokens", "completion_tokens"])
def test_generation_normalization_rejects_each_contradictory_directional_alias(alias: str) -> None:
    usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, alias: 50_000}
    with pytest.raises(ValueError, match="directional token aliases disagree"):
        normalized_generation_usage(usage)


def test_estimated_generation_cost_replays_from_model_and_usage(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    payload = run.model_dump(mode="json")
    receipt = payload["generation_results"][0]
    receipt["model"] = "gpt-5.6-sol"
    receipt["usage"] = {
        "input_tokens": 100,
        "output_tokens": 1,
        "total_tokens": 101,
        "provider_usage": {"input_tokens": 100, "output_tokens": 1, "total_tokens": 101},
    }
    receipt["cost_source"] = "estimated-model-pricing-v1"
    receipt["cost_usd"] = 0.0
    payload["budget_state"]["input_tokens"] += 100
    payload["budget_state"]["output_tokens"] += 1
    payload["budget_state"]["total_tokens"] += 101
    _rebind_generation_context(payload)
    altered = KernelWorkloadRunEvidence.model_validate(payload)

    with pytest.raises(ValueError, match="estimated cost does not replay"):
        validate_run_against_spec(altered, spec)


def test_workload_spec_precommits_the_campaign_decision_policy(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    changed_policy = spec.decision_policy.model_copy(
        update={"max_environment_drift": float(spec.decision_policy.max_environment_drift) + 0.01}
    )
    changed_spec = spec.model_copy(update={"decision_policy": changed_policy})
    payload = run.model_dump(mode="json")
    payload["workload_spec_id"] = changed_spec.spec_id
    _rebind_generation_context(payload)
    altered = KernelWorkloadRunEvidence.model_validate(payload)

    with pytest.raises(ValueError, match="precommitted decision policy"):
        validate_run_against_spec(altered, changed_spec)


def test_generation_retry_count_replays_against_the_pinned_budget(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    receipt = run.generation_results[0]
    failure = KernelGenerationFailure(
        proposal_index=receipt.proposal_index,
        call_index=1,
        provider=receipt.provider,
        model=receipt.model,
        outcome="provider_error",
        retryable=True,
        error_type="InjectedError",
        error="injected retry",
        usage=KernelGenerationUsage(),
        occurred_at=receipt.completed_at,
    )
    altered_receipt = receipt.model_copy(update={"retry_count": 1, "failures": (failure,)})
    altered = run.model_copy(update={"generation_results": (altered_receipt, *run.generation_results[1:])})
    with pytest.raises(ValueError, match="retry budget"):
        validate_run_against_spec(altered, spec)


@pytest.mark.parametrize(
    ("retryable", "retry_delay", "expected"),
    [(False, 1.0, "non-retryable"), (True, 0.0, "exponential backoff")],
)
def test_generation_retry_path_replays_retryability_and_backoff(
    study_report: KernelWorkloadStudyReport,
    retryable: bool,
    retry_delay: float,
    expected: str,
) -> None:
    run, spec = _run_and_spec(study_report)
    generation_budget = spec.budget.generation_budget.model_copy(
        update={"max_retries_per_proposal": 1, "retry_backoff_seconds": 1.0}
    )
    budget = spec.budget.model_copy(update={"generation_budget": generation_budget})
    bounded_spec = spec.model_copy(update={"budget": budget})
    receipt = run.generation_results[0]
    failure = KernelGenerationFailure(
        proposal_index=receipt.proposal_index,
        call_index=1,
        provider=receipt.provider,
        model=receipt.model,
        outcome="provider_error",
        retryable=retryable,
        error_type="InjectedError",
        error="injected retry path",
        usage=KernelGenerationUsage(
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            provider_usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        ),
        cost_source="provider-reported",
        retry_delay_seconds=retry_delay,
        occurred_at=receipt.completed_at,
    )
    altered_receipt = receipt.model_copy(update={"retry_count": 1, "failures": (failure,)})
    altered = run.model_copy(
        update={
            "workload_spec_id": bounded_spec.spec_id,
            "budget_id": budget.budget_id,
            "generation_budget_id": generation_budget.budget_id,
            "generation_results": (altered_receipt, *run.generation_results[1:]),
        }
    )
    with pytest.raises(ValueError, match=expected):
        validate_run_against_spec(altered, bounded_spec)


def test_generation_retry_rejects_total_only_paid_failure_usage(
    study_report: KernelWorkloadStudyReport,
) -> None:
    run, spec = _run_and_spec(study_report)
    generation_budget = spec.budget.generation_budget.model_copy(
        update={"max_retries_per_proposal": 1, "retry_backoff_seconds": 0.0}
    )
    budget = spec.budget.model_copy(update={"generation_budget": generation_budget})
    bounded_spec = spec.model_copy(update={"budget": budget})
    receipt = run.generation_results[0]
    failure = KernelGenerationFailure(
        proposal_index=receipt.proposal_index,
        call_index=1,
        provider=receipt.provider,
        model=receipt.model,
        outcome="provider_error",
        retryable=True,
        error_type="InjectedError",
        error="injected total-only paid retry",
        usage=KernelGenerationUsage(
            total_tokens=2,
            provider_usage={"total_tokens": 2},
        ),
        cost_source="provider-reported",
        occurred_at=receipt.completed_at,
    )
    altered_receipt = receipt.model_copy(update={"retry_count": 1, "failures": (failure,)})
    altered = run.model_copy(
        update={
            "workload_spec_id": bounded_spec.spec_id,
            "budget_id": budget.budget_id,
            "generation_budget_id": generation_budget.budget_id,
            "generation_results": (altered_receipt, *run.generation_results[1:]),
        }
    )

    with pytest.raises(ValueError, match="directional provider counters"):
        validate_run_against_spec(altered, bounded_spec)


@pytest.mark.parametrize("resource", ["wall", "cost"])
def test_outgoing_transfer_usage_is_charged_to_source_budget(
    study_report: KernelWorkloadStudyReport,
    resource: str,
) -> None:
    payload = _payload(study_report)
    transfer = payload["transfers"][0]
    source_id = transfer["source_workload_id"]
    spec = next(item for item in payload["workload_specs"] if item["workload_id"] == source_id)
    if resource == "wall":
        transfer["primary"]["evaluation_wall_seconds"] = spec["budget"]["max_workload_wall_seconds"] + 1.0
    else:
        transfer["primary"]["evaluation_cost_usd"] = spec["budget"]["max_workload_cost_usd"] + 1.0
    _recompute_study_usage(payload)
    with pytest.raises(ValidationError, match=rf"exceeded its {resource} budget including transfer evaluation"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_partial_campaign_cannot_claim_plateau(study_report: KernelWorkloadStudyReport) -> None:
    payload = _payload(study_report)
    run = next(item for item in payload["workload_runs"] if item["disposition"] == "plateau")
    spec_payload = next(item for item in payload["workload_specs"] if item["workload_id"] == run["workload_id"])
    spec_payload["budget"]["generation_budget"]["proposal_cap"] += 1
    updated_spec = KernelWorkloadSpec.model_validate(spec_payload)
    spec_index = next(index for index, item in enumerate(payload["workload_specs"]) if item["workload_id"] == run["workload_id"])
    payload["workload_specs"][spec_index] = updated_spec.model_dump(mode="json")
    run["workload_spec_id"] = updated_spec.spec_id
    run["budget_id"] = updated_spec.budget.budget_id
    run["generation_budget_id"] = updated_spec.budget.generation_budget.budget_id
    _rebind_generation_context(run)
    _rebind_workload_specs(payload)
    with pytest.raises(ValidationError, match="completed proposal exhaustion"):
        KernelWorkloadStudyReport.model_validate(payload)


@pytest.mark.parametrize("failure_kind", ["reportless", "infrastructure", "protocol-corruption"])
def test_exhausted_inconclusive_campaign_is_incomplete_not_plateau(
    study_report: KernelWorkloadStudyReport,
    failure_kind: str,
) -> None:
    run, spec = _run_and_spec(study_report, disposition="plateau")
    candidate = next(item for item in run.result.attempts if item.role == "candidate")
    if failure_kind == "reportless":
        observation = candidate.observation.model_copy(
            update={"eligible": False, "rejection_reason": "harness_modified", "report": None}
        )
    elif failure_kind == "infrastructure":
        report = candidate.observation.report
        assert report is not None and report.correctness is not None
        failed_correctness = report.correctness.model_copy(update={"passed": False})
        infrastructure = report.model_copy(
            update={
                "evaluation_status": "infrastructure_error",
                "failure_kind": "evaluator_crash",
                "correctness": failed_correctness,
            }
        )
        observation = candidate.observation.model_copy(
            update={
                "eligible": False,
                "rejection_reason": "infrastructure_error",
                "report": infrastructure,
            }
        )
    else:
        report = candidate.observation.report
        assert report is not None
        protocol_corruption = report.model_copy(
            update={
                "evaluation_status": "candidate_error",
                "failure_kind": "protocol_corruption",
                "performance": None,
            }
        )
        observation = candidate.observation.model_copy(
            update={
                "eligible": False,
                "rejection_reason": "contract_error",
                "derived_statistics_receipt": None,
                "report": protocol_corruption,
            }
        )
    altered_attempt = candidate.model_copy(update={"observation": observation})
    attempts = [altered_attempt if item.attempt_id == candidate.attempt_id else item for item in run.result.attempts]
    result = run.result.model_copy(update={"attempts": attempts})
    assert (
        workload_disposition(
            result,
            completed_proposals=len(run.generation_results),
            has_terminal_failures=False,
            proposal_cap=spec.budget.proposal_cap,
        )
        == "incomplete"
    )


def test_study_provenance_cannot_be_swapped_independently(study_report: KernelWorkloadStudyReport) -> None:
    payload = _payload(study_report)
    payload["provenance"]["manifest_digest"] = payload["provenance"]["contract_digest"]
    with pytest.raises(ValidationError, match="provenance disagrees"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_workload_budget_contract_is_bound_by_signed_report_metadata(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = _payload(study_report)
    spec_payload = payload["workload_specs"][0]
    spec_payload["budget"]["max_workload_wall_seconds"] += 1.0
    altered_spec = KernelWorkloadSpec.model_validate(spec_payload)
    payload["workload_specs"][0] = altered_spec.model_dump(mode="json")
    run = next(item for item in payload["workload_runs"] if item["workload_id"] == altered_spec.workload_id)
    run["workload_spec_id"] = altered_spec.spec_id
    run["budget_id"] = altered_spec.budget.budget_id
    _rebind_generation_context(run)
    with pytest.raises(ValidationError, match="workload specifications disagree with their provenance digest"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_measured_study_requires_an_external_evaluator_trust_root(
    study_report: KernelWorkloadStudyReport,
) -> None:
    provenance = study_report.provenance.model_copy(
        update={"evidence_kind": "measured", "backend_identity": "operator-evaluator/v1", "warning": None}
    )
    relabeled = study_report.model_copy(update={"provenance": provenance})
    with pytest.raises(ValueError, match="external evaluator trust root"):
        validate_complete_study(relabeled)
    with pytest.raises(ValueError, match="pin the exact evidence index digest"):
        build_kernel_workload_study_report(
            study_name=study_report.study_name,
            provenance=provenance,
            specs=study_report.workload_specs,
            runs=study_report.workload_runs,
            transfers=study_report.transfers,
            created_at=study_report.created_at,
            validation_context={"kernel_evaluator_trust": {}},
        )


@pytest.mark.parametrize("marker_location", ["report", "hardware"])
def test_measured_study_rejects_synthetic_warnings_in_embedded_metadata(
    study_report: KernelWorkloadStudyReport,
    monkeypatch: pytest.MonkeyPatch,
    marker_location: str,
) -> None:
    provenance = study_report.provenance.model_copy(
        update={"evidence_kind": "measured", "backend_identity": "operator-evaluator/v1", "warning": None}
    )
    observation = next(
        attempt.observation
        for run in study_report.workload_runs
        for attempt in run.result.attempts
        if attempt.observation.report is not None
    )
    benchmark_report = observation.report
    assert benchmark_report is not None
    shared = {
        "evidence_origin": "measured",
        "study_execution_id": provenance.study_execution_id,
        "study_manifest_digest": provenance.manifest_digest,
        "study_contract_digest": provenance.contract_digest,
        "study_backend_identity": provenance.backend_identity,
    }
    report_metadata = {**benchmark_report.metadata, **shared}
    hardware_metadata = {**benchmark_report.hardware.metadata, **shared}
    report_metadata.pop("evidence_warning", None)
    hardware_metadata.pop("evidence_warning", None)
    selected = report_metadata if marker_location == "report" else hardware_metadata
    selected["evidence_warning"] = "synthetic marker"
    hardware = benchmark_report.hardware.model_copy(update={"metadata": hardware_metadata})
    altered_report = benchmark_report.model_copy(update={"metadata": report_metadata, "hardware": hardware})
    altered_observation = observation.model_copy(update={"report": altered_report})
    monkeypatch.setattr(workload_study_validation, "_iter_observations", lambda _report: iter((altered_observation,)))
    monkeypatch.setattr(workload_study_validation, "validate_measured_evidence_index", lambda *_args: None)

    with pytest.raises(ValueError, match="embedded benchmark report provenance disagrees"):
        workload_study_validation._validate_provenance(
            study_report.model_copy(update={"provenance": provenance}),
            provenance,
            validation_context={
                "kernel_evaluator_trust": {"evidence_index_digest": provenance.evidence_index_digest}
            },
        )


def test_reportless_fail_closed_transfer_remains_visible(study_report: KernelWorkloadStudyReport) -> None:
    transfer_payload = study_report.transfers[1].model_dump(mode="json")
    _make_reportless(transfer_payload["primary"]["observation"])
    _make_reportless(transfer_payload["confirmation"]["observation"])
    rejected = KernelTransferEvidence.model_validate(transfer_payload)
    transfers = tuple(rejected if item == study_report.transfers[1] else item for item in study_report.transfers)
    rebuilt = build_kernel_workload_study_report(
        study_name=study_report.study_name,
        provenance=study_report.provenance,
        specs=study_report.workload_specs,
        runs=study_report.workload_runs,
        transfers=transfers,
        created_at=study_report.created_at,
    )
    assert rejected.primary.rejection_reason == "harness_modified"
    assessment = next(item for item in rebuilt.champion_assessments if item.source_workload_id == rejected.source_workload_id)
    assert rejected.target_workload_id in assessment.untested_workload_ids
    assert rejected.target_workload_id not in assessment.failed_workload_ids
    assert rebuilt.portable_champion_artifact_digests == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [("protocol_id", content_digest(b"fabricated-protocol")), ("candidate_median_ms", 1.0)],
)
def test_reportless_phase_cannot_retain_report_derived_claims(
    study_report: KernelWorkloadStudyReport,
    field: str,
    value: object,
) -> None:
    payload = study_report.transfers[1].model_dump(mode="json")
    _make_reportless(payload["primary"]["observation"])
    payload["primary"]["observation"][field] = value
    with pytest.raises(ValidationError, match="reportless phase observations"):
        KernelTransferEvidence.model_validate(payload)


def test_final_phase_statistics_replay_from_raw_timing_blocks(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = _payload(study_report)
    observation = payload["workload_runs"][0]["primary"]["observation"]
    observation["candidate_median_ms"] = 1e-6
    observation["speedup_vs_reference"] = 999_999_999.0
    with pytest.raises(ValidationError, match="does not replay from its raw report"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_phase_descriptive_metadata_is_bound_to_its_typed_route(
    study_report: KernelWorkloadStudyReport,
) -> None:
    final_payload = _payload(study_report)
    final_payload["workload_runs"][0]["primary"]["observation"]["report"]["metadata"]["workload_id"] = (
        study_report.workload_runs[1].workload_id
    )
    with pytest.raises(ValidationError, match="typed workload route"):
        KernelWorkloadStudyReport.model_validate(final_payload)

    transfer_payload = _payload(study_report)
    transfer_payload["transfers"][0]["primary"]["observation"]["report"]["metadata"][
        "source_workload_id"
    ] = study_report.workload_runs[-1].workload_id
    with pytest.raises(ValidationError, match="typed workload route"):
        KernelWorkloadStudyReport.model_validate(transfer_payload)


def test_final_and_transfer_phases_reject_incomparable_eligible_timing(
    study_report: KernelWorkloadStudyReport,
) -> None:
    final_payload = _payload(study_report)
    passing_transfer = next(item for item in study_report.transfers if item.passed)
    transfer_payload = passing_transfer.model_dump(mode="json")
    for payload, observation in (
        (final_payload, final_payload["workload_runs"][0]["primary"]["observation"]),
        (transfer_payload, transfer_payload["primary"]["observation"]),
    ):
        observation["report"]["metadata"]["timing_comparability"] = {
            "candidate_incumbent_comparable": False,
            "reference_comparable": False,
            "promotion_comparison": ["candidate_ms", "incumbent_ms"],
        }
        model = KernelWorkloadStudyReport if payload is final_payload else KernelTransferEvidence
        with pytest.raises(ValidationError, match="incomparable timing boundaries"):
            model.model_validate(payload)


def test_standalone_measured_phases_require_timing_comparability_evidence(
    study_report: KernelWorkloadStudyReport,
) -> None:
    phase_payload = study_report.workload_runs[0].primary.model_dump(mode="json")
    phase_payload["observation"]["report"]["metadata"]["evidence_origin"] = "measured"
    phase_payload["observation"]["report"]["metadata"].pop("timing_comparability", None)
    with pytest.raises(ValidationError, match="incomparable timing boundaries"):
        KernelWorkloadPhaseEvidence.model_validate(phase_payload)

    transfer = next(item for item in study_report.transfers if item.passed)
    transfer_payload = transfer.model_dump(mode="json")
    transfer_payload["primary"]["observation"]["report"]["metadata"]["evidence_origin"] = "measured"
    transfer_payload["primary"]["observation"]["report"]["metadata"].pop("timing_comparability", None)
    with pytest.raises(ValidationError, match="incomparable timing boundaries"):
        KernelTransferEvidence.model_validate(transfer_payload)


def test_infrastructure_transfer_is_unconfirmed_not_a_specialist_regression(
    study_report: KernelWorkloadStudyReport,
) -> None:
    transfer_payload = study_report.transfers[1].model_dump(mode="json")
    for phase_name in ("primary", "confirmation"):
        _make_ineligible(transfer_payload[phase_name]["observation"], "infrastructure_error")
    inconclusive = KernelTransferEvidence.model_validate(transfer_payload)
    transfers = tuple(inconclusive if item == study_report.transfers[1] else item for item in study_report.transfers)
    rebuilt = build_kernel_workload_study_report(
        study_name=study_report.study_name,
        provenance=study_report.provenance,
        specs=study_report.workload_specs,
        runs=study_report.workload_runs,
        transfers=transfers,
        created_at=study_report.created_at,
    )
    assessment = next(item for item in rebuilt.champion_assessments if item.source_workload_id == inconclusive.source_workload_id)
    assert inconclusive.target_workload_id in assessment.untested_workload_ids
    assert inconclusive.target_workload_id not in assessment.failed_workload_ids
    source_regressions = [
        regression for regression in rebuilt.regressions if regression.startswith(f"{inconclusive.source_workload_id} ")
    ]
    assert all(inconclusive.target_workload_id not in regression for regression in source_regressions)


def test_protocol_corruption_is_unconfirmed_not_a_specialist_regression(
    study_report: KernelWorkloadStudyReport,
) -> None:
    transfer_payload = study_report.transfers[1].model_dump(mode="json")
    for phase_name in ("primary", "confirmation"):
        observation = transfer_payload[phase_name]["observation"]
        _make_ineligible(observation, "contract_error")
        observation["report"]["evaluation_status"] = "candidate_error"
        observation["report"]["failure_kind"] = "protocol_corruption"
        observation["report"]["performance"] = None
    inconclusive = KernelTransferEvidence.model_validate(transfer_payload)
    transfers = tuple(inconclusive if item == study_report.transfers[1] else item for item in study_report.transfers)
    rebuilt = build_kernel_workload_study_report(
        study_name=study_report.study_name,
        provenance=study_report.provenance,
        specs=study_report.workload_specs,
        runs=study_report.workload_runs,
        transfers=transfers,
        created_at=study_report.created_at,
    )
    assessment = next(item for item in rebuilt.champion_assessments if item.source_workload_id == inconclusive.source_workload_id)
    assert inconclusive.target_workload_id in assessment.untested_workload_ids
    assert inconclusive.target_workload_id not in assessment.failed_workload_ids


def test_incomparable_timing_floor_failure_is_unconfirmed_not_specialist(
    study_report: KernelWorkloadStudyReport,
) -> None:
    original = next(
        item for item in study_report.transfers if item.passed and item.target_workload_id != item.source_workload_id
    )
    transfer_payload = original.model_dump(mode="json")
    for phase_name in ("primary", "confirmation"):
        observation = transfer_payload[phase_name]["observation"]
        _make_ineligible(observation, "timing_boundary_mismatch")
        observation["report"]["metadata"]["timing_comparability"] = {
            "candidate_incumbent_comparable": False,
            "reference_comparable": False,
            "promotion_comparison": ["candidate_ms", "incumbent_ms"],
        }
        case = observation["report"]["performance"]["cases"][0]
        case["candidate_median_ms"] = float(case["incumbent_median_ms"]) * 2.0
        case["passed_no_regression"] = False
    inconclusive = KernelTransferEvidence.model_validate(transfer_payload)
    transfers = tuple(inconclusive if item == original else item for item in study_report.transfers)
    rebuilt = build_kernel_workload_study_report(
        study_name=study_report.study_name,
        provenance=study_report.provenance,
        specs=study_report.workload_specs,
        runs=study_report.workload_runs,
        transfers=transfers,
        created_at=study_report.created_at,
    )
    assessment = next(item for item in rebuilt.champion_assessments if item.source_workload_id == original.source_workload_id)
    assert original.target_workload_id in assessment.untested_workload_ids
    assert original.target_workload_id not in assessment.failed_workload_ids


def test_study_identity_is_canonical_and_created_at_independent(study_report: KernelWorkloadStudyReport) -> None:
    payload = _payload(study_report)
    original_id = study_report.study_id
    payload["created_at"] = "2099-01-01T00:00:00+00:00"
    restored = KernelWorkloadStudyReport.model_validate(payload)
    assert restored.study_id == original_id
    assert json.loads(restored.model_dump_json())["schema_version"] == "autocontext.kernel-workload-study/v1"
