from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.kernel_evolution import (
    KernelTransferEvidence,
    KernelWorkloadStudyReport,
    build_kernel_workload_study_report,
)

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PACKAGE_ROOT.parent
_EXAMPLE = _REPO_ROOT / "examples" / "kernel_evolution" / "multi_workload" / "run.py"


@pytest.fixture(scope="module")
def study_report(tmp_path_factory: pytest.TempPathFactory) -> KernelWorkloadStudyReport:
    output = tmp_path_factory.mktemp("kernel-workload-study")
    completed = subprocess.run(
        [
            sys.executable,
            str(_EXAMPLE),
            "--output",
            str(output),
            "--study-id",
            "test-study",
        ],
        cwd=_PACKAGE_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "portable champions: 0" in completed.stdout
    return KernelWorkloadStudyReport.model_validate_json(
        (output / "test-study" / "study_report.json").read_text(encoding="utf-8")
    )


def test_three_workloads_share_independent_primary_and_confirmation_contracts(
    study_report: KernelWorkloadStudyReport,
) -> None:
    assert {spec.workload_family for spec in study_report.workload_specs} == {
        "matmul-generalization-v1",
        "fused-elementwise-reduction-v1",
        "causal-attention-v1",
    }
    assert study_report.all_workloads_independently_verified is True
    assert len(study_report.workload_runs) == 3
    for run in study_report.workload_runs:
        assert run.primary.passed is True
        assert run.confirmation.passed is True
        assert run.primary.protocol_id != run.confirmation.protocol_id
        assert run.primary.plan_commitment != run.confirmation.plan_commitment
        assert set(run.primary.correctness_slices) == {"train", "holdout"}
        assert set(run.confirmation.correctness_slices) == {"train", "holdout"}
        assert run.proposals_evaluated <= 2
        assert float(run.budget_state.cost_usd) == 0.0


def test_study_keeps_promotions_specialists_regressions_and_plateaus_visible(
    study_report: KernelWorkloadStudyReport,
) -> None:
    dispositions = {run.workload_id: run.disposition for run in study_report.workload_runs}
    assert dispositions == {
        "variable-shape-matmul": "promoted",
        "fused-elementwise-reduction": "promoted",
        "causal-attention": "plateau",
    }
    assessments = {item.source_workload_id: item.disposition for item in study_report.champion_assessments}
    assert assessments == {
        "variable-shape-matmul": "cross-workload",
        "fused-elementwise-reduction": "specialist",
    }
    assert study_report.portable_champion_artifact_digests == ()
    assert study_report.transferable_lessons == ("variable-shape-matmul champion transferred to fused-elementwise-reduction",)
    assert any("causal-attention" in item for item in study_report.regressions)
    assert study_report.plateaus == ("causal-attention exhausted its bounded proposals without a promotion",)


def test_transfer_evidence_covers_hardware_shapes_and_workload_families(
    study_report: KernelWorkloadStudyReport,
) -> None:
    assert len(study_report.transfers) == 6
    for assessment in study_report.champion_assessments:
        assert set(assessment.covered_dimensions) == {"shape", "hardware", "workload-family"}
    hardware = [item for item in study_report.transfers if item.dimensions == ("hardware",)]
    assert len(hardware) == 2
    runs = {run.workload_id: run for run in study_report.workload_runs}
    for transfer in hardware:
        source = runs[transfer.source_workload_id]
        assert transfer.primary.execution_environment_id != source.primary.execution_environment_id
        assert transfer.confirmation.execution_environment_id != source.confirmation.execution_environment_id


def test_failed_target_evidence_cannot_be_hidden_by_an_aggregate_promotion(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = study_report.model_dump(mode="json")
    payload["portable_champion_artifact_digests"] = [study_report.champion_assessments[0].candidate_artifact_digest]
    with pytest.raises(ValidationError, match="portable champion list"):
        KernelWorkloadStudyReport.model_validate(payload)

    forged_assessment = study_report.model_dump(mode="json")
    forged_assessment["champion_assessments"][0]["disposition"] = "portable"
    with pytest.raises(ValidationError, match="champion assessments disagree"):
        KernelWorkloadStudyReport.model_validate(forged_assessment)


def test_required_slices_and_budgets_are_replayed_from_each_workload_spec(
    study_report: KernelWorkloadStudyReport,
) -> None:
    missing_slice = study_report.model_dump(mode="json")
    missing_slice["workload_runs"][0]["primary"]["correctness_slices"] = ["train"]
    with pytest.raises(ValidationError, match="omitted required correctness slices"):
        KernelWorkloadStudyReport.model_validate(missing_slice)

    overspent = study_report.model_dump(mode="json")
    overspent["workload_runs"][0]["budget_state"]["cost_usd"] = 2.0
    with pytest.raises(ValidationError, match="exceeded its cost budget"):
        KernelWorkloadStudyReport.model_validate(overspent)

    wrong_run_floor = study_report.model_dump(mode="json")
    wrong_run_floor["workload_runs"][0]["primary"]["minimum_case_speedup_vs_incumbent"] = 0.97
    with pytest.raises(ValidationError, match="wrong case floor"):
        KernelWorkloadStudyReport.model_validate(wrong_run_floor)

    wrong_transfer_floor = study_report.model_dump(mode="json")
    wrong_transfer_floor["transfers"][0]["primary"]["minimum_case_speedup_vs_incumbent"] = 0.97
    with pytest.raises(ValidationError, match="wrong target case floor"):
        KernelWorkloadStudyReport.model_validate(wrong_transfer_floor)

    wrong_proposal_count = study_report.model_dump(mode="json")
    wrong_proposal_count["workload_runs"][0]["budget_state"]["completed_proposals"] = 1
    with pytest.raises(ValidationError, match="proposal count disagrees"):
        KernelWorkloadStudyReport.model_validate(wrong_proposal_count)


def test_reportless_fail_closed_transfer_rejection_remains_visible(
    study_report: KernelWorkloadStudyReport,
) -> None:
    transfer_index = next(
        index for index, transfer in enumerate(study_report.transfers) if "workload-family" in transfer.dimensions
    )
    transfer_payload = study_report.transfers[transfer_index].model_dump(mode="json")
    report_fields = (
        "report_digest",
        "hardware_scope_id",
        "execution_environment_id",
        "workload_family_id",
        "workload_fingerprint",
        "baseline_id",
        "protocol_id",
        "protocol_compatibility_id",
        "plan_commitment",
        "minimum_case_speedup_vs_incumbent",
    )
    for phase in (transfer_payload["primary"], transfer_payload["confirmation"]):
        phase.update(
            eligible=False,
            rejection_reason="harness_modified",
            correctness_slices=[],
            all_correctness_slices_passed=False,
            all_case_floors_passed=False,
        )
        for field in report_fields:
            phase[field] = None
    transfers = list(study_report.transfers)
    transfers[transfer_index] = KernelTransferEvidence.model_validate(transfer_payload)
    rebuilt = build_kernel_workload_study_report(
        study_name=study_report.study_name,
        specs=study_report.workload_specs,
        runs=study_report.workload_runs,
        transfers=tuple(transfers),
        created_at=study_report.created_at,
    )
    assert rebuilt.transfers[transfer_index].primary.rejection_reason == "harness_modified"
    assert rebuilt.portable_champion_artifact_digests == ()


def test_hardware_transfer_must_really_cross_execution_environments(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = study_report.model_dump(mode="json")
    hardware_index = next(index for index, transfer in enumerate(study_report.transfers) if transfer.dimensions == ("hardware",))
    transfer = payload["transfers"][hardware_index]
    source_id = transfer["source_workload_id"]
    source_run = next(run for run in payload["workload_runs"] if run["workload_id"] == source_id)
    transfer["primary"]["execution_environment_id"] = source_run["primary"]["execution_environment_id"]
    with pytest.raises(ValidationError, match="hardware transfer must cross"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_shape_transfer_must_really_cross_workload_fingerprints(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = study_report.model_dump(mode="json")
    shape_index = next(index for index, transfer in enumerate(study_report.transfers) if "shape" in transfer.dimensions)
    transfer = payload["transfers"][shape_index]
    source_id = transfer["source_workload_id"]
    source_run = next(run for run in payload["workload_runs"] if run["workload_id"] == source_id)
    transfer["confirmation"]["workload_fingerprint"] = source_run["confirmation"]["workload_fingerprint"]
    with pytest.raises(ValidationError, match="shape transfer must cross"):
        KernelWorkloadStudyReport.model_validate(payload)


def test_study_identity_is_canonical_and_created_at_independent(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = study_report.model_dump(mode="json")
    original_id = study_report.study_id
    payload["created_at"] = "2099-01-01T00:00:00+00:00"
    restored = KernelWorkloadStudyReport.model_validate(payload)
    assert restored.study_id == original_id
    assert json.loads(restored.model_dump_json())["schema_version"] == ("autocontext.kernel-workload-study/v1")


def test_study_requires_three_distinct_workload_families(
    study_report: KernelWorkloadStudyReport,
) -> None:
    payload = copy.deepcopy(study_report.model_dump(mode="json"))
    payload["workload_specs"] = payload["workload_specs"][:2]
    payload["workload_runs"] = payload["workload_runs"][:2]
    payload["transfers"] = []
    payload["champion_assessments"] = []
    payload["portable_champion_artifact_digests"] = []
    payload["transferable_lessons"] = []
    payload["regressions"] = []
    payload["plateaus"] = []
    with pytest.raises(ValidationError, match="at least 3 items"):
        KernelWorkloadStudyReport.model_validate(payload)
