from __future__ import annotations

import json
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import autocontext.weight_evolution.adaptive_confirmation as confirmation
from autocontext.weight_evolution.adaptive_confirmation import (
    ConfirmationPolicy,
    ConfirmationSelection,
    build_confirmation_selection,
    confirm_adaptive_selection,
)
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.comparison_evaluation import FixtureEvidence, fixture_observation
from autocontext.weight_evolution.comparison_io import load_comparison_fixture
from autocontext.weight_evolution.comparison_models import (
    ComparisonArtifacts,
    ComparisonPlan,
    build_comparison_plan,
)
from autocontext.weight_evolution.session import EditingSession, _immutable_json, _read_record
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_fixtures import ScriptedSessionAdapter
from autocontext.weight_evolution.session_workflow import load_session_workflow

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution"


@dataclass
class Prepared:
    root: Path
    plan: ComparisonPlan
    data: SessionDataBundle
    selection_path: Path
    completion_paths: tuple[Path, ...]

    def run(self, evidence: FixtureEvidence | None = None, minimum_mean_gain: float = 0.1) -> dict[str, Any]:
        return confirm_adaptive_selection(
            self.plan, evidence or responses(self.plan), data=self.data, selection_path=self.selection_path,
            policy=ConfirmationPolicy(minimum_mean_gain=minimum_mean_gain), output_dir=self.root / "confirmation",
        )


def responses(plan: ComparisonPlan, *, regression: bool = False) -> FixtureEvidence:
    literal = {"confirmation.sum": r"\boxed{14}", "confirmation.coins": r"\boxed{7}",
               "confirmation.words": "</think>coral reef"}
    rows = []
    for cell in plan.cells:
        text = literal[cell.problem_id]
        if cell.condition_id == "A" and cell.problem_id == "confirmation.sum":
            text = r"\boxed{13}"
        if regression and cell.condition_id == "D" and cell.problem_id == "confirmation.coins":
            text = r"\boxed{8}"
        rows.append(fixture_observation(cell, text))
    return FixtureEvidence(plan_digest=plan.digest, observations=tuple(rows))


@pytest.fixture
def prepared(tmp_path: Path) -> Prepared:
    workflow, data = load_session_workflow(EXAMPLES / "qwen_sessions" / "fixtures")
    source, _ = load_comparison_fixture(EXAMPLES / "qwen_comparison" / "fixtures")
    paths = []
    artifacts = source.artifacts.to_dict()
    for program, name in zip(workflow.programs, ("learned_context", "edited_checkpoint", "selected_decoding"), strict=True):
        root = tmp_path / "searches" / program.spec.mechanism
        session = EditingSession(spec=program.spec, data=data, output_dir=root / "session",
                                 adapter=ScriptedSessionAdapter(program.script))
        for operation in program.operations:
            session.run(operation)
        artifact = session.export_candidate()
        snapshot = session.snapshot()
        # Independent gate fixtures retain genuine M6 sealed exports and small
        # linked adaptive receipt examples; the integrated controller has its
        # own practice-driven search tests.
        selected = {"selected_candidate": snapshot["candidate"],
                    "selected_candidate_digest": snapshot["candidate_digest"],
                    "selected_state_digest": canonical_digest(snapshot["candidate"]["state"]),
                    "spec_digest": program.spec.digest, "confirmation_used": False, "final_audit_used": False}
        complete = {"schema_version": 1, "execution_kind": "fixture", "status": "sealed",
                    "model_execution_performed": False, "gpu_execution_deferred": True,
                    "spec_digest": program.spec.digest, "selection_receipt_digest": canonical_digest(selected),
                    "selected_candidate_digest": selected["selected_candidate_digest"],
                    "selected_state_digest": selected["selected_state_digest"], "candidate": snapshot["candidate"],
                    "candidate_digest": snapshot["candidate_digest"], "artifact": artifact.to_dict(),
                    "artifact_digest": artifact.digest, "history_digest": snapshot["history_digest"],
                    "reserved_usage": snapshot["reserved_usage"], "observed_usage": snapshot["observed_usage"],
                    "confirmation_used": False, "final_audit_used": False}
        _immutable_json(root / "manifest.json", {"spec": program.spec.to_dict(), "spec_digest": program.spec.digest})
        _immutable_json(root / "selection.json", selected)
        _immutable_json(root / "complete.json", complete)
        paths.append(root / "complete.json")
        artifacts[name] = artifact.to_dict()
    plan = build_comparison_plan(study_id="confirmation-fixture-v1", cases=data.confirmation,
                                 artifacts=ComparisonArtifacts.model_validate_json(json.dumps(artifacts)),
                                 protocol=source.protocol, budgets=workflow.budgets, development=source.development)
    path = tmp_path / "selection.json"
    binding = build_confirmation_selection(data, completion_paths=tuple(paths), selection_path=path)
    path.write_text(json.dumps(binding.to_dict(), sort_keys=True))
    return Prepared(tmp_path, plan, data, path, tuple(paths))


def rewrite_record(path: Path, transform: Any) -> None:
    payload = _read_record(path)
    transform(payload)
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload)}))


def test_fixed_confirmation_passes_with_fixture_only_claims(prepared: Prepared) -> None:
    result = prepared.run()
    assert result["status"] == "confirmed" and result["look"] == 1
    assert result["promotion_eligible"] is result["benchmark_result_claimed"] is False
    assert result["statistical_significance_claimed"] is result["model_execution_performed"] is False
    assert result["gpu_execution_deferred"] is True
    assert all(row["status"] == "confirmed" and row["mean_gain"] == pytest.approx(1 / 3)
               for row in result["outcomes"])
    assert all(row["n_problem_blocks"] == 3 for row in result["outcomes"])
    assert {row["condition_id"] for row in result["comparison"]["samples"]} == {"A", "C", "W", "CW", "D"}


def test_mean_threshold_vetoes_without_candidate_fallback(prepared: Prepared) -> None:
    result = prepared.run(minimum_mean_gain=0.5)
    assert result["status"] == "vetoed"
    assert all(row["status"] == "vetoed" and not row["minimum_gain_met"] for row in result["outcomes"])
    with pytest.raises(ValueError, match="immutable journal record"):
        prepared.run(minimum_mean_gain=0.0)


def test_dataset_regression_vetoes_even_when_mean_threshold_is_met(prepared: Prepared) -> None:
    result = prepared.run(responses(prepared.plan, regression=True), minimum_mean_gain=0.0)
    assert result["status"] == "vetoed"
    assert [row["status"] for row in result["outcomes"]] == ["confirmed", "confirmed", "vetoed"]
    row = result["outcomes"][-1]
    assert row["mean_gain"] == 0.0 and row["minimum_gain_met"] is True
    assert row["dataset_gains"]["gsm8k"] == -1.0 and row["no_dataset_regression"] is False


def test_exact_resume_is_byte_identical(prepared: Prepared) -> None:
    first = prepared.run()
    root = prepared.root / "confirmation"
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    assert prepared.run() == first
    assert before == {path.name: path.read_bytes() for path in root.iterdir()}


def test_veto_cannot_be_replaced_by_new_evidence(prepared: Prepared) -> None:
    assert prepared.run(responses(prepared.plan, regression=True))["status"] == "vetoed"
    with pytest.raises(ValueError, match="immutable journal record"):
        prepared.run(responses(prepared.plan))


def test_missing_confirmation_rows_rejected_before_claim(prepared: Prepared) -> None:
    evidence = responses(prepared.plan)
    changed = FixtureEvidence(plan_digest=prepared.plan.digest, observations=evidence.observations[:-1])
    with pytest.raises(ValueError, match="cover every planned"):
        prepared.run(changed)
    assert not (prepared.root / "confirmation").exists()


def test_wrong_evidence_plan_rejected_before_claim(prepared: Prepared) -> None:
    evidence = responses(prepared.plan)
    with pytest.raises(ValueError, match="different comparison plan"):
        prepared.run(evidence.model_copy(update={"plan_digest": "f" * 64}))
    assert not (prepared.root / "confirmation").exists()


def test_audit_data_cannot_be_used_as_confirmation(prepared: Prepared) -> None:
    source, evidence = load_comparison_fixture(EXAMPLES / "qwen_comparison" / "fixtures")
    with pytest.raises(ValueError, match="exactly the pinned confirmation role"):
        confirm_adaptive_selection(source, evidence, data=prepared.data, selection_path=prepared.selection_path,
                                   policy=ConfirmationPolicy(), output_dir=prepared.root / "confirmation")


@pytest.mark.parametrize("filename", ["claim.json", "report.json", "complete.json"])
def test_tampered_records_are_not_adopted(prepared: Prepared, filename: str) -> None:
    prepared.run()
    path = prepared.root / "confirmation" / filename
    rewrite_record(path, lambda value: value.update(schema_version=True))
    with pytest.raises(ValueError, match="immutable journal record"):
        prepared.run()


def test_missing_selection_commitment_is_not_recreated(prepared: Prepared) -> None:
    prepared.selection_path.unlink()
    with pytest.raises(FileNotFoundError):
        prepared.run()
    assert not (prepared.root / "confirmation").exists()


@pytest.mark.parametrize("field", ["candidate_digest", "artifact_digest", "history_digest"])
def test_linked_export_changes_are_rejected(prepared: Prepared, field: str) -> None:
    export = prepared.completion_paths[0].parent / "session" / "export.json"
    rewrite_record(export, lambda value: value.update({field: "f" * 64}))
    with pytest.raises(ValueError, match="differs from sealed export"):
        prepared.run()


@pytest.mark.parametrize("field", ["confirmation_used", "final_audit_used"])
def test_previously_exposed_search_is_rejected(prepared: Prepared, field: str) -> None:
    rewrite_record(prepared.completion_paths[0], lambda value: value.update({field: True}))
    with pytest.raises(ValueError, match="precede any confirmation"):
        prepared.run()


def test_changed_original_winner_is_not_replaced_by_final_candidate(prepared: Prepared) -> None:
    rewrite_record(prepared.completion_paths[0], lambda value: value.update(selected_state_digest="f" * 64))
    with pytest.raises(ValueError, match="selected revision"):
        prepared.run()


def test_claim_precedes_grading_and_identical_local_grading_can_resume(
    prepared: Prepared, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = confirmation.evaluate_comparison_fixture

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        assert (prepared.root / "confirmation" / "claim.json").is_file()
        raise KeyboardInterrupt("simulated local grading interruption")

    monkeypatch.setattr(confirmation, "evaluate_comparison_fixture", interrupted)
    with pytest.raises(KeyboardInterrupt):
        prepared.run()
    assert not (prepared.root / "confirmation" / "complete.json").exists()
    monkeypatch.setattr(confirmation, "evaluate_comparison_fixture", original)
    assert prepared.run()["status"] == "confirmed"


def test_incomplete_claim_does_not_allow_swapping_evidence(
    prepared: Prepared, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = confirmation.evaluate_comparison_fixture

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt("simulated interruption")

    monkeypatch.setattr(confirmation, "evaluate_comparison_fixture", interrupted)
    with pytest.raises(KeyboardInterrupt):
        prepared.run(responses(prepared.plan, regression=True))
    monkeypatch.setattr(confirmation, "evaluate_comparison_fixture", original)
    with pytest.raises(ValueError, match="immutable journal record"):
        prepared.run()


def test_changed_controller_source_pin_stops_resume(prepared: Prepared, monkeypatch: pytest.MonkeyPatch) -> None:
    prepared.run()
    monkeypatch.setattr(confirmation, "_source_identity", lambda: {"changed": "source"})
    with pytest.raises(ValueError, match="immutable journal record"):
        prepared.run()


def test_no_process_or_network_execution(prepared: Prepared, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("confirmation attempted external execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert prepared.run()["status"] == "confirmed"


@pytest.mark.parametrize("value", [-0.1, 1.1, float("inf"), float("nan")])
def test_invalid_threshold_is_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        ConfirmationPolicy(minimum_mean_gain=value)


def test_path_escape_in_selection_is_rejected(prepared: Prepared) -> None:
    value = json.loads(prepared.selection_path.read_bytes())
    value["selections"][0]["completion_path"] = "../escape/complete.json"
    with pytest.raises(ValueError, match="normalized relative"):
        ConfirmationSelection.model_validate_json(json.dumps(value))


def test_all_searches_must_have_distinct_ordered_commitments(prepared: Prepared) -> None:
    with pytest.raises(ValueError, match="mechanism or data"):
        build_confirmation_selection(prepared.data, completion_paths=tuple(reversed(prepared.completion_paths)),
                                     selection_path=prepared.selection_path)


def test_confirmation_directory_symlink_rejected(prepared: Prepared, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (prepared.root / "confirmation").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        prepared.run()


@pytest.mark.parametrize("name,field,replacement", [
    ("base_checkpoint", "content", "A different synthetic baseline descriptor."),
    ("base_context", "text", "A different baseline context."),
    ("base_decoding", "temperature", 0.4),
])
def test_re_pinned_changed_baseline_rejected_before_claim(
    prepared: Prepared, name: str, field: str, replacement: Any,
) -> None:
    artifacts = prepared.plan.artifacts.to_dict()
    artifacts[name][field] = replacement
    old = prepared.plan
    prepared.plan = build_comparison_plan(
        study_id=old.study_id, cases=old.cases, protocol=old.protocol, budgets=old.budgets,
        development=old.development, artifacts=ComparisonArtifacts.model_validate_json(json.dumps(artifacts)),
    )
    with pytest.raises(ValueError, match="baseline or scorer differs"):
        prepared.run(responses(prepared.plan))
    assert not (prepared.root / "confirmation").exists()


def test_re_pinned_changed_search_scorer_rejected_before_claim(prepared: Prepared) -> None:
    old = prepared.plan
    prepared.plan = build_comparison_plan(
        study_id=old.study_id, cases=old.cases, protocol=old.protocol.model_copy(update={"scorer_digest": "f" * 64}),
        budgets=old.budgets, development=old.development, artifacts=old.artifacts,
    )
    with pytest.raises(ValueError, match="baseline or scorer differs"):
        prepared.run(responses(prepared.plan))
    assert not (prepared.root / "confirmation").exists()


def test_completed_report_cannot_be_silently_recreated(prepared: Prepared) -> None:
    prepared.run()
    path = prepared.root / "confirmation" / "report.json"
    path.unlink()
    with pytest.raises(ValueError, match="completed confirmation report is missing"):
        prepared.run()
    assert not path.exists()
