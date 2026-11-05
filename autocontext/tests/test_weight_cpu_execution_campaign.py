from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_execution_campaign as campaign
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity
from autocontext.weight_evolution.cpu_execution_campaign import (
    build_cpu_execution_campaign,
    report_cpu_execution_campaign,
    run_cpu_execution_campaign,
    validate_cpu_execution_campaign,
)
from autocontext.weight_evolution.cpu_execution_campaign_models import CpuExecutionCampaignSpec
from autocontext.weight_evolution.session import _immutable_json, _read_record, _same


@dataclass
class Fixture:
    bundle: Path
    output: Path
    spec: CpuExecutionCampaignSpec
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    failures: dict[tuple[str, str], str] = field(default_factory=dict)
    expected: dict[str, dict[str, Any]] = field(default_factory=dict)


def _proof_path(root: Path, row: dict[str, Any]) -> Path:
    return root / row["profile"] / ("searches" if row["stage"] == "search" else "references") / row["run_id"] / "proof.json"


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    # These tests exercise the coordinator only. No native/measured worker is
    # launched; separate adapter tests and the declared run cover real inference.
    runtime = campaign.execution_runtime_identity()
    monkeypatch.setattr(campaign, "execution_runtime_identity", lambda: runtime)
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    spec = build_cpu_execution_campaign(bundle)
    item = Fixture(bundle, output, spec)

    def dispatch(directory: Path, root: Path, row: dict[str, Any], child: Any, templates: Any,
                 plan: Any, execution: Any) -> None:
        item.calls.append((row["profile"], row["stage"], row["run_id"]))
        behavior = item.failures.get((row["profile"], row["run_id"]))
        if behavior == "prebootstrap":
            raise RuntimeError("known parent bootstrap error")
        if child is not None:
            campaign._derived_bundle(root / row["profile"] / "bundles" / row["run_id"],
                campaign._bundle_bytes(directory / "campaign", row["task_id"], child), write=True)
            summary = {"study_digest": child.digest, "runtime_digest": spec.native_runtime_digest,
                "policy": child.policy.to_dict(), "selection_digest": canonical_digest({"study": child.digest}),
                "histories": {"C": [], "W": [], "D": []}, "proposal_history": [],
                "search_usage": child.required_usage, "selected_options": {"C": None, "W": None, "D": None},
                "restoration_verified": {"C": True, "W": True, "D": True},
                "confirmation": {"status": "vetoed", "outcomes": {"C": False, "W": False, "D": False}},
                "audit_status": "withheld", "reserved_usage": child.required_usage,
                "receipt_digests": {"practice-A": canonical_digest({"study": child.digest, "receipt": "fixture"})}}
        else:
            assert plan is not None
            assert all(c.role == "practice" for c in campaign._reference_args(directory, root, row, templates, plan)[1]["cases"])
            summary = {"runtime_digest": spec.native_runtime_digest, "diagnostic_digest": plan[0].digest,
                "scores": {"A": {"accuracy": 0.5, "correct": 2, "total": 4}},
                "charged_usage": row["reserved_capacity"], "checkpoint_seals": {"base": "a" * 64},
                "receipt_digests": {"A": canonical_digest({"reference": row["run_id"]})}}
        summary["semantic_digest"] = canonical_digest(summary)
        prefix = {"status": behavior or "completed", "summary": summary if behavior is None else None,
                  "terminal_claim": None, "terminal_result": None}
        if row["profile"] == "measured-v2":
            prefix["observation_inventory"] = [{"phase": row["stage"], "outcome_present": behavior != "uncertain",
                                                "process_observation": None}]
        path = _proof_path(root, row)
        path.parent.mkdir(parents=True, exist_ok=True)
        _immutable_json(path, prefix)
        item.expected[str(path)] = prefix

    def prefix(root: Path, row: dict[str, Any]) -> dict[str, Any]:
        path = _proof_path(root, row)
        if not path.exists():
            return campaign._empty()
        raw = _read_record(path)
        _same(raw, item.expected[str(path)], "fixture child semantic replay rejected retained evidence")
        return raw

    monkeypatch.setattr(campaign, "_dispatch", dispatch)
    monkeypatch.setattr(campaign, "_search_prefix", lambda directory, root, row, child, execution: prefix(root, row))
    monkeypatch.setattr(campaign, "_reference_prefix", lambda directory, root, row, templates, plan, execution:
                        prefix(root, row) if plan is not None else campaign._empty())
    return item


def _snapshot(root: Path) -> dict[str, Any]:
    return {str(p.relative_to(root)): file_identity(p) for p in sorted(root.rglob("*")) if p.is_file()}


def _replace(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps({"payload": value, "digest": canonical_digest(value)}))


def test_exact_fixed_capacity_and_practice_only_plans(fixture: Fixture) -> None:
    validation = validate_cpu_execution_campaign(fixture.bundle)
    assert validation["searches"] == 18 and validation["conditional_references"] == 6
    assert validation["model_execution_performed"] is False
    assert len(validation["schedule"]) == 24
    report = run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == 24
    assert report["status_counts"]["search"]["completed"] == 18
    assert report["status_counts"]["reference"]["completed"] == 6
    assert report["acceptance_passed"] is True
    assert len(report["pairs"]) == 12 and all(pair["matches"] is True for pair in report["pairs"])
    assert report["unknown_usage_rows"] == {"search": 0, "reference": 0}
    for phase, budget in (("search", fixture.spec.search_budget), ("reference", fixture.spec.reference_budget)):
        expected = {m: getattr(budget, f"max_{m}") for m in campaign.METERS}
        assert report["charged_capacity"][phase] == report["completed_verified_usage"][phase] == expected
    for profile in campaign.EXECUTION_PROFILES:
        plan = _read_record(fixture.output / "plans" / f"{profile}.json")
        assert len(plan["diagnostic"]["references"]) == 3
        for ref in plan["diagnostic"]["references"]:
            assert len(ref["practice"]) == 4
            assert all(set(case) == {"case_id", "prompt"} for case in ref["practice"])
            assert len(ref["source_runs"]) == 3
        assert plan["binding"]["profile"] == profile
        assert plan["diagnostic"]["implementation_digest"] == fixture.spec.implementation_digest
    measured_reference_claim = _read_record(fixture.output / "claims" / "00000021.json")
    assert (measured_reference_claim["execution"]["workflow_implementation_digest"]
            == campaign.diagnostic_implementation_digest())
    assert (measured_reference_claim["execution"]["workflow_implementation_digest"]
            != fixture.spec.implementation_digest)
    assert report["family_pooling_performed"] is False
    assert report["historical_failure_cause_recovered"] is False


def test_complete_replay_is_read_only_with_no_dispatch_or_publication(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    before = _snapshot(fixture.output)
    monkeypatch.setattr(campaign, "_dispatch", lambda *a, **k: pytest.fail("retained child dispatched"))
    assert run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output) == report
    monkeypatch.setattr(campaign, "_immutable_json", lambda *a, **k: pytest.fail("readonly report published"))
    monkeypatch.setattr(campaign, "_clear_staging", lambda *a, **k: pytest.fail("readonly report changed staging"))
    assert report_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output) == report
    assert _snapshot(fixture.output) == before


@pytest.mark.parametrize("behavior", ["failed", "uncertain", "prebootstrap"])
def test_one_failed_profile_blocks_only_its_references_and_keeps_full_search_charge(fixture: Fixture,
    behavior: str,
) -> None:
    fixture.failures[("legacy-v1", "task-01.repeat-01.adaptive-v1")] = behavior
    report = run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == 21
    assert report["status_counts"]["reference"]["blocked"] == 3
    assert report["status_counts"]["reference"]["completed"] == 3
    assert report["unknown_usage_rows"] == {"search": 1, "reference": 0}
    assert report["charged_capacity"]["search"] == {
        m: getattr(fixture.spec.search_budget, f"max_{m}") for m in campaign.METERS}
    assert report["charged_capacity"]["reference"] == {
        m: getattr(fixture.spec.reference_budget, f"max_{m}") // 2 for m in campaign.METERS}
    assert not (fixture.output / "plans" / "legacy-v1.json").exists()
    assert all(r["charged_capacity"] == campaign._zero() for r in report["rows"] if r["status"] == "blocked")
    assert report["acceptance_passed"] is False
    if behavior == "prebootstrap":
        row = report["rows"][0]
        assert row["parent_error_type"] == "RuntimeError"
        assert row["observation_coverage"]["inference_operations"] == 0
    assert report_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output) == report


@pytest.mark.parametrize("location,after", [("claims", True), ("outcomes", False), ("outcomes", True),
                                           ("results", False), ("results", True)])
def test_parent_publication_interruptions_reconcile_without_redispatch(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch, location: str, after: bool,
) -> None:
    original = campaign._immutable_json

    def interrupted(path: Path, value: dict[str, Any]) -> None:
        if path == fixture.output / location / "00000000.json":
            if after:
                original(path, value)
            raise OSError("publication interrupted")
        original(path, value)

    monkeypatch.setattr(campaign, "_immutable_json", interrupted)
    with pytest.raises(OSError, match="publication interrupted"):
        run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    pending = report_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    assert pending["rows"][0]["status"] == ("uncertain" if location == "claims" else "completed")
    before = _snapshot(fixture.output)
    assert report_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output) == pending
    assert _snapshot(fixture.output) == before
    monkeypatch.setattr(campaign, "_immutable_json", original)
    report = run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == (20 if location == "claims" else 24)
    assert len(set(fixture.calls)) == len(fixture.calls)
    assert report["unknown_usage_rows"]["search"] == (1 if location == "claims" else 0)
    assert report["status_counts"]["reference"]["blocked"] == (3 if location == "claims" else 0)


def test_rehashed_prior_child_corruption_during_dispatch_blocks_next_child(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = campaign._dispatch

    def mutate(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        if len(fixture.calls) == 2:
            path = fixture.output / "legacy-v1/searches/task-01.repeat-01.adaptive-v1/proof.json"
            value = _read_record(path)
            value["summary"]["receipt_digests"]["practice-A"] = "0" * 64
            _replace(path, value)

    monkeypatch.setattr(campaign, "_dispatch", mutate)
    with pytest.raises(ValueError, match="semantic replay"):
        run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == 2
    assert not (fixture.output / "claims" / "00000002.json").exists()


@pytest.mark.parametrize("mutation", ["claim", "outcome", "result", "plan", "child", "report"])
def test_rehashed_terminal_artifact_corruption_rejected(fixture: Fixture, mutation: str) -> None:
    run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    if mutation == "plan":
        path = fixture.output / "plans/legacy-v1.json"
        value = _read_record(path)
        value["diagnostic"]["references"][0]["practice"][0]["prompt"] += " token0"
    elif mutation == "child":
        path = fixture.output / "measured-v2/searches/task-01.repeat-01.adaptive-v1/proof.json"
        value = _read_record(path)
        value["status"] = "uncertain"
    elif mutation == "report":
        path = fixture.output / "report.json"
        value = _read_record(path)
        value["acceptance_passed"] = False
    else:
        path = fixture.output / f"{mutation}s/00000000.json"
        value = _read_record(path)
        if mutation == "claim":
            value["claimed_capacity"]["inference_calls"] -= 1
        elif mutation == "outcome":
            value["projection"]["receipt_digests"]["practice-A"] = "f" * 64
        else:
            value["charged_capacity"]["inference_calls"] -= 1
    _replace(path, value)
    with pytest.raises(ValueError):
        report_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    with pytest.raises(ValueError):
        run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == 24


def test_reference_failure_keeps_source_pairs_and_other_reference_slots(fixture: Fixture) -> None:
    fixture.failures[("measured-v2", "task-01.repeat-01")] = "failed"
    report = run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == 24
    assert report["status_counts"]["search"]["completed"] == 18
    assert report["status_counts"]["reference"]["completed"] == 5
    assert report["status_counts"]["reference"]["failed"] == 1
    assert report["unknown_usage_rows"] == {"search": 0, "reference": 1}
    assert all(pair["matches"] is True for pair in report["pairs"] if pair["stage"] == "search")
    assert report["acceptance_passed"] is False


def test_native_phase_coverage_distinguishes_restoration_and_references(tmp_path: Path) -> None:
    (tmp_path / "claims").mkdir()
    (tmp_path / "results").mkdir()
    for index, label in enumerate(("C-baseline", "W-trial-1", "D-restored", "practice-CW", "confirmation-CW", "audit-A")):
        name = f"{index:08d}.json"
        _immutable_json(tmp_path / "claims" / name, {"kind": "inference", "label": label})
        _immutable_json(tmp_path / "results" / name, {"status": "completed"})
    coverage = campaign._native_coverage(tmp_path, "search")
    assert {phase: counts["completed"] for phase, counts in coverage["phases"].items()} == {
        "practice": 3, "restoration": 1, "confirmation": 1, "audit": 1}
    assert campaign._native_coverage(tmp_path, "reference")["phases"] == {
        "reference": {"completed": 6, "failed": 0, "uncertain": 0}}


def test_unknown_output_and_source_drift_fail_before_dispatch(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture.output.mkdir()
    (fixture.output / "unowned.json").write_text("{}")
    with pytest.raises(ValueError, match="unowned"):
        run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    assert not (fixture.output / campaign._LOCK).exists()
    (fixture.output / "unowned.json").unlink()
    runtime = campaign.execution_runtime_identity()
    monkeypatch.setattr(campaign, "execution_runtime_identity", lambda: {**runtime, "source-drift": True})
    with pytest.raises(ValueError, match="source inventory"):
        run_cpu_execution_campaign(fixture.bundle, output_dir=fixture.output)
    assert not (fixture.output / campaign._LOCK).exists()
    assert fixture.calls == []


def test_bundle_and_output_must_be_disjoint_and_not_symlinked(fixture: Fixture, tmp_path: Path) -> None:
    for output in (fixture.bundle, fixture.bundle / "nested", tmp_path):
        with pytest.raises(ValueError, match="overlap"):
            run_cpu_execution_campaign(fixture.bundle, output_dir=output)
    alias = tmp_path / "alias"
    alias.symlink_to(fixture.bundle, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        validate_cpu_execution_campaign(alias)
    assert fixture.calls == []
