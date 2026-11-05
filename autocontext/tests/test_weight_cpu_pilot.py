from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_pilot as pilot
from autocontext.weight_evolution import cpu_pilot_bundle as bundle_module
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity
from autocontext.weight_evolution.cpu_campaign import METERS
from autocontext.weight_evolution.cpu_execution_campaign import _empty
from autocontext.weight_evolution.cpu_pilot_models import CpuPilotSpec
from autocontext.weight_evolution.session import _immutable_json, _read_record, _same


@dataclass
class Fixture:
    bundle: Path
    output: Path
    spec: CpuPilotSpec
    calls: list[tuple[str, str]] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    expected: dict[str, dict[str, Any]] = field(default_factory=dict)


def _proof_path(root: Path, row: dict[str, Any]) -> Path:
    return root / row["profile"] / ("searches" if row["stage"] == "search" else "references") / row["run_id"] / "proof.json"


def _report(spec: Any, control: Any, templates: Any, schedule: list[dict[str, Any]],
            prefix: list[dict[str, Any]]) -> dict[str, Any]:
    """Coordinator tests isolate reporting/scoring, which has its own fixtures."""
    rows = []
    for index, row in enumerate(schedule):
        entry = prefix[index] if index < len(prefix) else None
        rows.append({**row, "status": entry["outcome"]["status"] if entry else "not-started",
            "published": entry["published"] if entry else False,
            "charged_capacity": entry["claim"]["claimed_capacity"] if entry else pilot._zero(),
            "verified_usage": entry["result"]["verified_usage"] if entry else None,
            "parent_error_type": entry["outcome"]["parent_error_type"] if entry else None})
    return {"rows": rows, "campaign_digest": spec.digest, "ledger_digest": canonical_digest(prefix),
            "completed": len(prefix) == 96 and all(e["published"] and e["outcome"]["status"] == "completed" for e in prefix)}


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    # No process runs in these fixtures. Real M16 adapter acceptance and M17's
    # declared pilot are separate evidence, never replaced by these fake results.
    runtime = bundle_module.execution_runtime_identity()
    implementation = bundle_module.pilot_implementation_digest()
    monkeypatch.setattr(bundle_module, "execution_runtime_identity", lambda: runtime)
    monkeypatch.setattr(bundle_module, "pilot_implementation_digest", lambda: implementation)
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    spec = bundle_module.build_cpu_pilot_bundle(bundle)
    item = Fixture(bundle, output, spec)

    # Regeneration/input pinning is exercised separately; retain its result for
    # unchanged assets to keep96slot ledger fault fixtures inexpensive. Tests
    # which exercise input drift restore the real loader explicitly.
    loaded = bundle_module.load_cpu_pilot_bundle(bundle)
    monkeypatch.setattr(pilot, "load_cpu_pilot_bundle", lambda path: loaded)

    def dispatch(directory: Path, root: Path, row: dict[str, Any], child: Any, templates: Any,
                 plan: Any, execution: Any) -> None:
        item.calls.append((row["stage"], row["run_id"]))
        behavior = item.failures.get(row["run_id"])
        if behavior == "prebootstrap":
            raise RuntimeError("known parent bootstrap error")
        assert execution.implementation_digest == spec.execution_implementation_digest
        if child is not None:
            pilot._derived_bundle(root / row["profile"] / "bundles" / row["run_id"],
                pilot._bundle_bytes(directory / "campaign", row["task_id"], child), write=True)
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
            ref, args = pilot._reference_args(directory, root, row, templates, plan)
            assert all(c.role == "practice" for c in args["cases"])
            assert ref.required_usage == row["reserved_capacity"]
            assert execution.workflow_implementation_digest == pilot.diagnostic_implementation_digest()
            summary = {"runtime_digest": spec.native_runtime_digest, "diagnostic_digest": plan[0].digest,
                "scores": {"A": {"accuracy": 0.5, "correct": 4, "total": 8}},
                "charged_usage": row["reserved_capacity"], "checkpoint_seals": {"base": "a" * 64},
                "receipt_digests": {"A": canonical_digest({"reference": row["run_id"]})}}
        summary["semantic_digest"] = canonical_digest(summary)
        prefix = {"status": behavior or "completed", "summary": summary if behavior is None else None,
                  "terminal_claim": None, "terminal_result": None,
                  "observation_inventory": [{"phase": row["stage"], "outcome_present": behavior != "uncertain",
                                              "process_observation": None}]}
        path = _proof_path(root, row)
        path.parent.mkdir(parents=True, exist_ok=True)
        _immutable_json(path, prefix)
        item.expected[str(path)] = prefix

    def prefix(root: Path, row: dict[str, Any]) -> dict[str, Any]:
        path = _proof_path(root, row)
        if not path.exists():
            return _empty()
        raw = _read_record(path)
        _same(raw, item.expected[str(path)], "fixture child semantic replay rejected retained evidence")
        return raw

    monkeypatch.setattr(pilot, "_dispatch", dispatch)
    monkeypatch.setattr(pilot, "_search_prefix", lambda directory, root, row, child, execution: prefix(root, row))
    monkeypatch.setattr(pilot, "_reference_prefix", lambda directory, root, row, templates, plan, execution:
                        prefix(root, row) if plan is not None else _empty())
    monkeypatch.setattr(pilot, "_details", lambda output, outcome: None)
    monkeypatch.setattr(pilot, "build_cpu_pilot_report", _report)
    return item


def _snapshot(root: Path) -> dict[str, Any]:
    return {str(p.relative_to(root)): file_identity(p) for p in sorted(root.rglob("*")) if p.is_file()}


def _replace(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps({"payload": value, "digest": canonical_digest(value)}))


def test_complete96slot_pilot_uses24_practice_references_and_separate_exact_budgets(fixture: Fixture) -> None:
    report = pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert report["completed"] is True and len(fixture.calls) == 96
    assert len(report["rows"]) == 96
    for stage, budget in (("search", fixture.spec.search_budget), ("reference", fixture.spec.reference_budget)):
        rows = [row for row in report["rows"] if row["stage"] == stage]
        for meter in METERS:
            assert sum(row["charged_capacity"][meter] for row in rows) == getattr(budget, f"max_{meter}")
            assert sum(row["verified_usage"][meter] for row in rows) == getattr(budget, f"max_{meter}")
    plan = _read_record(fixture.output / "plans/measured-v2.json")
    assert len(plan["diagnostic"]["references"]) == 24
    assert plan["binding"]["source_search_count"] == 72
    assert plan["diagnostic"]["implementation_digest"] == fixture.spec.implementation_digest
    seen = []
    for ref in plan["diagnostic"]["references"]:
        assert len(ref["practice"]) == 8
        assert all(set(case) == {"case_id", "prompt"} for case in ref["practice"])
        seen.extend(run["run_id"] for run in ref["source_runs"])
    assert len(set(seen)) == 72
    claim = _read_record(fixture.output / "claims/00000072.json")
    assert claim["execution"]["implementation_digest"] == fixture.spec.execution_implementation_digest
    assert claim["execution"]["workflow_implementation_digest"] == pilot.diagnostic_implementation_digest()


def test_complete_replay_verifies_once_without_any_write_or_dispatch(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    staging = fixture.output / ".session-publish-completed-report"
    staging.write_bytes(b" " * (1024 * 1024 + 1))
    before = _snapshot(fixture.output)
    original = pilot._inspect
    calls = []

    def inspect(*args: Any, **kwargs: Any) -> Any:
        calls.append(True)
        return original(*args, **kwargs)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("completed replay attempted a side effect")

    monkeypatch.setattr(pilot, "_inspect", inspect)
    monkeypatch.setattr(pilot, "_dispatch", forbidden)
    monkeypatch.setattr(pilot, "_immutable_json", forbidden)
    monkeypatch.setattr(pilot, "_clear_staging", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    assert pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output) == report
    assert len(calls) == 1
    assert pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output) == report
    assert len(calls) == 2
    assert _snapshot(fixture.output) == before


@pytest.mark.parametrize("behavior", ["failed", "uncertain", "prebootstrap"])
def test_failed_source_blocks_all24references_and_retains_full_search_charges(fixture: Fixture,
    behavior: str,
) -> None:
    fixture.failures["task-01.repeat-01.adaptive-v1"] = behavior
    report = pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == 72 and not report["completed"]
    assert report["rows"][0]["verified_usage"] is None
    assert report["rows"][0]["status"] == ("failed" if behavior == "prebootstrap" else behavior)
    assert report["rows"][0]["parent_error_type"] == ("RuntimeError" if behavior == "prebootstrap" else None)
    assert all(row["status"] == "blocked" and row["charged_capacity"] == pilot._zero() for row in report["rows"][72:])
    assert not (fixture.output / "plans/measured-v2.json").exists()
    for meter in METERS:
        assert sum(row["charged_capacity"][meter] for row in report["rows"][:72]) == getattr(
            fixture.spec.search_budget, f"max_{meter}")
    assert pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output) == report
    if behavior == "failed":
        unowned = fixture.output / "measured-v2/references/task-01.repeat-01"
        unowned.mkdir()
        with pytest.raises(ValueError, match="unauthorized child evidence"):
            pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output)
        assert len(fixture.calls) == 72


@pytest.mark.parametrize("location,after", [("claims", True), ("outcomes", False), ("outcomes", True),
                                           ("results", False), ("results", True)])
def test_parent_publication_interruption_never_redispatches_claimed_child(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch, location: str, after: bool,
) -> None:
    original = pilot._immutable_json

    def interrupted(path: Path, value: dict[str, Any]) -> None:
        if path == fixture.output / location / "00000000.json":
            if after:
                original(path, value)
            raise OSError("publication interrupted")
        original(path, value)

    monkeypatch.setattr(pilot, "_immutable_json", interrupted)
    with pytest.raises(OSError, match="publication interrupted"):
        pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    pending = pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert pending["rows"][0]["status"] == ("uncertain" if location == "claims" else "completed")
    before = _snapshot(fixture.output)
    assert pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output) == pending
    assert _snapshot(fixture.output) == before
    monkeypatch.setattr(pilot, "_immutable_json", original)
    report = pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == (71 if location == "claims" else 96)
    assert len(set(fixture.calls)) == len(fixture.calls)
    assert report["completed"] is (location != "claims")


def test_rehashed_prior_child_corruption_during_dispatch_blocks_next_child(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = pilot._dispatch

    def mutate(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        if len(fixture.calls) == 2:
            path = fixture.output / "measured-v2/searches/task-01.repeat-01.adaptive-v1/proof.json"
            value = _read_record(path)
            value["summary"]["receipt_digests"]["practice-A"] = "0" * 64
            _replace(path, value)

    monkeypatch.setattr(pilot, "_dispatch", mutate)
    with pytest.raises(ValueError, match="semantic replay"):
        pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == 2
    assert not (fixture.output / "claims/00000002.json").exists()


@pytest.mark.parametrize("mutation", ["claim", "outcome", "result", "plan", "child", "report"])
def test_rehashed_retained_artifacts_cannot_be_adopted(fixture: Fixture, mutation: str) -> None:
    pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    if mutation == "plan":
        path = fixture.output / "plans/measured-v2.json"
        value = _read_record(path)
        value["diagnostic"]["references"][0]["practice"][0]["prompt"] += " token0"
    elif mutation == "child":
        path = fixture.output / "measured-v2/searches/task-01.repeat-01.adaptive-v1/proof.json"
        value = _read_record(path)
        value["status"] = "uncertain"
    elif mutation == "report":
        path = fixture.output / "report.json"
        value = _read_record(path)
        value["completed"] = False
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
        pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    with pytest.raises(ValueError):
        pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == 96


def test_reference_failure_keeps_all_other_slots_and_full_capacity(fixture: Fixture) -> None:
    fixture.failures["task-01.repeat-01"] = "failed"
    report = pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert len(fixture.calls) == 96 and not report["completed"]
    assert report["rows"][72]["status"] == "failed" and report["rows"][72]["verified_usage"] is None
    assert all(row["status"] == "completed" for row in report["rows"][:72] + report["rows"][73:])
    for meter in METERS:
        assert sum(row["charged_capacity"][meter] for row in report["rows"][72:]) == getattr(
            fixture.spec.reference_budget, f"max_{meter}")


def test_unowned_output_and_runtime_drift_fail_before_dispatch(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture.output.mkdir()
    (fixture.output / "unowned.json").write_text("{}")
    with pytest.raises(ValueError, match="unowned"):
        pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert not (fixture.output / pilot._LOCK).exists()
    (fixture.output / "unowned.json").unlink()
    monkeypatch.setattr(pilot, "load_cpu_pilot_bundle", bundle_module.load_cpu_pilot_bundle)
    runtime = bundle_module.execution_runtime_identity()
    monkeypatch.setattr(bundle_module, "execution_runtime_identity", lambda: {**runtime, "source-drift": True})
    with pytest.raises(ValueError):
        pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert not (fixture.output / pilot._LOCK).exists() and not fixture.calls


def test_absent_readonly_report_creates_nothing(fixture: Fixture) -> None:
    with pytest.raises(ValueError, match="manifest missing"):
        pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert not fixture.output.exists()


def test_bundle_output_disjoint_and_unclaimed_evidence_rejected(fixture: Fixture, tmp_path: Path) -> None:
    for output in (fixture.bundle, fixture.bundle / "nested", tmp_path):
        with pytest.raises(ValueError, match="overlap"):
            pilot.run_cpu_pilot(fixture.bundle, output_dir=output)
    assert not fixture.calls


def test_unclaimed_child_is_rejected_before_later_work(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    original = pilot._immutable_json

    def interrupted(path: Path, value: dict[str, Any]) -> None:
        original(path, value)
        if path == fixture.output / "claims/00000000.json":
            raise OSError("claimed but not dispatched")

    monkeypatch.setattr(pilot, "_immutable_json", interrupted)
    with pytest.raises(OSError):
        pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    stray = fixture.output / "measured-v2/searches/task-12.repeat-02.seeded-random-v1"
    stray.mkdir(parents=True)
    with pytest.raises(ValueError, match="unclaimed child"):
        pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    monkeypatch.setattr(pilot, "_immutable_json", original)
    with pytest.raises(ValueError, match="unclaimed child"):
        pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert not fixture.calls


def test_real_dispatch_preserves_three_source_searches_and_practice_reference(tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real child integration, not a substitute for the declared72-search pilot.

    Only the first task/repeat's three sources and its reference execute. Other
    source-plan entries are explicit synthetic bindings; coordinator eligibility
    and complete-source verification are covered by the96slot ledger fixtures.
    """
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    bundle_module.build_cpu_pilot_bundle(bundle)
    spec, control, templates, _ = bundle_module.load_cpu_pilot_bundle(bundle)
    schedule = bundle_module.pilot_schedule(control, templates)
    children = {row["run_id"]: child for row, child in pilot._control_schedule(control, templates)}
    for folder in ("bundles", "searches", "references"):
        (output / "measured-v2" / folder).mkdir(parents=True)
    source = []
    for index, row in enumerate(schedule[:72]):
        child = children[row["run_id"]]
        if index < 3:
            execution = pilot._execution(spec, row, child, None)
            pilot._dispatch(bundle, output, row, child, templates, None, execution)
            prefix = pilot._search_prefix(bundle, output, row, child, execution)
            assert prefix["status"] == "completed"
            summary = prefix["summary"]
            details = pilot._details(output / row["profile"] / "searches" / row["run_id"],
                                     {"status": "completed", "summary": summary})
            assert details is not None and details["audit"] is None
        else:
            summary = {"selection_digest": canonical_digest({"synthetic_selection": row["run_id"]}),
                       "semantic_digest": canonical_digest({"synthetic_summary": row["run_id"]})}
        source.append({"row": row, "outcome": {"status": "completed", "summary": summary}, "published": True,
                       "result": {"synthetic_parent_binding": row["run_id"]}})
    plan = pilot._plan(spec, control, templates, source, children=children)
    assert plan is not None and len(plan[0].references) == 24
    row = schedule[72]
    execution = pilot._execution(spec, row, None, plan)
    pilot._dispatch(bundle, output, row, None, templates, plan, execution)
    result = pilot._reference_prefix(bundle, output, row, templates, plan, execution)
    assert result["status"] == "completed"
    assert result["summary"]["charged_usage"] == row["reserved_capacity"]
    assert len(result["summary"]["scores"]) == 13
    assert {observation["phase"] for observation in result["observation_inventory"]
            if observation["kind"] == "inference"} == {"reference"}
    before = _snapshot(output)
    monkeypatch.setattr(pilot, "run_cpu_execution_search", lambda *a, **k: pytest.fail("replay dispatched search"))
    monkeypatch.setattr(pilot, "run_cpu_execution_reference", lambda *a, **k: pytest.fail("replay dispatched reference"))
    assert pilot._reference_prefix(bundle, output, row, templates, plan, execution) == result
    assert _snapshot(output) == before


@pytest.mark.parametrize("size", [1024 * 1024 + 1, pilot.MAX_JOURNAL_RECORD_BYTES])
def test_publication_scratch_accepts_writer_byte_limit_without_adopting_bytes(tmp_path: Path, size: int) -> None:
    staging = tmp_path / ".session-publish-bounded-report"
    # Interrupted scratch need not be complete JSON and is never authoritative.
    with staging.open("wb") as stream:
        stream.truncate(size)
    before = file_identity(staging)
    assert pilot._entries_clean(tmp_path, 0) == set()
    assert file_identity(staging) == before


def test_oversize_publication_scratch_fails_before_effects(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture.output.mkdir()
    staging = fixture.output / ".session-publish-oversize-report"
    with staging.open("wb") as stream:
        stream.truncate(pilot.MAX_JOURNAL_RECORD_BYTES + 1)
    before = _snapshot(fixture.output)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("oversize scratch reached a side effect")

    monkeypatch.setattr(pilot, "_dispatch", forbidden)
    monkeypatch.setattr(pilot, "_immutable_json", forbidden)
    monkeypatch.setattr(pilot, "_clear_staging", forbidden)
    monkeypatch.setattr(pilot, "advisory_path_lock", forbidden)
    with pytest.raises(ValueError, match="byte budget"):
        pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    with pytest.raises(ValueError, match="byte budget"):
        pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert _snapshot(fixture.output) == before
    assert not (fixture.output / pilot._LOCK).exists()


def test_pending_resume_discards_large_scratch_without_fabricating_an_outcome(fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = pilot._immutable_json

    def interrupted(path: Path, value: dict[str, Any]) -> None:
        original(path, value)
        if path == fixture.output / "claims/00000000.json":
            raise OSError("claimed but never dispatched")

    monkeypatch.setattr(pilot, "_immutable_json", interrupted)
    with pytest.raises(OSError):
        pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    staging = fixture.output / ".session-publish-unreturned-report"
    staging.write_text(json.dumps({"status": "completed", "padding": "x" * (1024 * 1024)}))
    before = _snapshot(fixture.output)
    pending = pilot.report_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert pending["rows"][0]["status"] == "uncertain"
    assert _snapshot(fixture.output) == before
    monkeypatch.setattr(pilot, "_immutable_json", original)
    resumed = pilot.run_cpu_pilot(fixture.bundle, output_dir=fixture.output)
    assert not staging.exists()
    assert resumed["rows"][0]["status"] == "uncertain"
    assert resumed["rows"][0]["verified_usage"] is None
    assert resumed["rows"][0]["charged_capacity"] == resumed["rows"][0]["reserved_capacity"]
    assert len(fixture.calls) == 71
    assert all(row["status"] == "blocked" for row in resumed["rows"][72:])


def test_publication_scratch_keeps_entry_counts_and_regular_file_checks(tmp_path: Path) -> None:
    for index in range(17):
        (tmp_path / f".session-publish-count-{index:08d}").write_text("{}")
    with pytest.raises(ValueError, match="entry bound"):
        pilot._entries_clean(tmp_path, 0)
    for path in tmp_path.iterdir():
        path.unlink()
    target = tmp_path / "ordinary.json"
    target.write_text("{}")
    (tmp_path / ".session-publish-alias-target").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        pilot._entries_clean(tmp_path, 1)
