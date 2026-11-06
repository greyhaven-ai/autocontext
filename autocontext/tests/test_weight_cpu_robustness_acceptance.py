"""Offline acceptance must require declared search contrasts and preserve failed attempts."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def passing_report() -> dict[str, Any]:
    rows = []
    variants = [(index, order, 3, "order-comparison") for index in range(1, 7)
                for order in ("canonical", "reverse-lanes")]
    variants += [(index, "canonical", 6, "full-budget") for index in (1, 3, 5)]
    families = ("informative-improvement-v1", "misleading-improvement-v1", "neutral-v1")
    for stage in ("search", "reference"):
        for index, order, rounds, panel in variants:
            task = f"task-{index:02}.{order}.r{rounds}"
            for policy in (("adaptive-v1", "fixed-order-v1", "seeded-random-v1") if stage == "search" else (None,)):
                run_id = f"{task}.repeat-01" + (f".{policy}" if policy else "")
                rows.append({"run_id": run_id, "task_id": task, "base_task_id": f"task-{index:02}",
                    "order_id": order, "rounds": rounds, "panel": panel, "family": families[(index - 1) // 2],
                    "instance_index": (index - 1) % 2 + 1, "repeat_id": "repeat-01", "stage": stage,
                    "policy": policy, "status": "completed", "result_published": True, "observation_coverage": [],
                    "summary": {"audit_status": "withheld", "confirmation": {"status": "vetoed", "minimum_gain": 0.1,
                        "outcomes": {"C": False, "W": True, "D": False}}}})
    curves = [{**{key: row[key] for key in ("run_id", "base_task_id", "order_id", "rounds", "panel")},
               "mechanism": mechanism, "available": True, "additional_inference_performed": False,
               "points": [{"trial": trial, "practice_case_evaluations": (trial + 1) * 8}
                          for trial in range(row["rounds"] + 1)]}
              for row in rows[:45] for mechanism in ("C", "W", "D")]
    checks = [{"check": kind, "identity": f"task-{index:02}", "matches": True} for index in range(1, 7)
              for kind in ("reference-score-tables", "seeded-random-declaration-order")]
    for row in rows[:45]:
        kinds = ["neutral-context-decoding-anchors"]
        if row["family"] == "neutral-v1":
            kinds.append("neutral-weight-null")
        if row["panel"] == "full-budget":
            kinds.extend(("full-budget-zero-practice-regret", "short-full-budget-history-prefix"))
        checks.extend({"check": kind, "identity": row["run_id"], "matches": True} for kind in kinds)
    invariants = dict.fromkeys(("score_tables_invariant", "random_order_invariant", "full_budget_zero_regret",
                              "budget_prefixes", "neutral_null", "all_passed"), True)
    return {"evidence_integrity_verified": True, "campaign_completed": True, "acceptance_passed": True,
            "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
            "rows": rows, "status_counts": {
                stage: {"completed": count, "failed": 0, "uncertain": 0, "blocked": 0, "not-started": 0}
                for stage, count in (("search", 45), ("reference", 15))},
            "calibration": {"scheduled_checks": 915, "matches": 915, "mismatches": 0,
                            "unavailable_or_nondesignated": 0, "checks": [{"matches": True} for _ in range(915)]},
            "audit_routing": {"scheduled": 45, "verified": 45, "mismatches": 0, "unavailable": 0,
                              "positive_count": 0, "veto_count": 45},
            "robustness_checks": {"scheduled_checks": 90, "matches": 90, "mismatches": 0, "unavailable": 0,
                "aggregate_adaptive_win_required": False, "references_used_for_selection": False,
                "checks": checks},
            "offline_expectations": {"scope": "finite-construction-expectations-not-measured-evidence",
                "measured_evidence": False, "verified": True, "legal_order_count": 90, "trajectory_count": 6480,
                "base_task_count": 6, "budgets": [1, 3, 6], "invariants": invariants,
                "policy_seeds": [{"policy": policy, "order_seed": seed} for policy, seed in (
                    ("adaptive-v1", 0), ("fixed-order-v1", 0), ("seeded-random-v1", 0), ("seeded-random-v1", 1))],
                "used_for_policy_selection": False, "population_generalization_claimed": False,
                "task_summaries": [{"base_task_id": f"task-{index:02}", "legal_order_count": 90,
                    "trajectory_count": 1080, "invariants": invariants.copy()} for index in range(1, 7)]},
            "practice_prefix_curves": curves,
            "process_coverage": {"scheduled_rows": 60, "verified_rows": 60, "mismatches": 0, "unavailable": 0,
                "expected_inference_batches": 1311, "observed_inference_batches": 1311, "reaped_inference_batches": 1311,
                "rows": [{"run_id": row["run_id"], "matches": True} for row in rows]}}


@pytest.fixture
def acceptance() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "cpu_search_robustness" / "acceptance.py"
    spec = importlib.util.spec_from_file_location("cpu_robustness_campaign_acceptance_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_campaign(acceptance: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, list[str], dict[str, Any]]:
    calls: list[str] = []
    report = passing_report()
    monkeypatch.setattr(acceptance, "_environment", lambda _: {"system": "Linux", "machine": "x86_64"})
    monkeypatch.setattr(acceptance, "_source_inventory", lambda: {"source.py": {"sha256": "a" * 64, "size_bytes": 1}})

    def build(bundle: Path, **_: Any) -> None:
        bundle.mkdir()
        (bundle / "contract.json").write_text("{}")

    def run(bundle: Path, *, output_dir: Path) -> dict[str, Any]:
        calls.append("run")
        output_dir.mkdir()
        (output_dir / "report.json").write_text(json.dumps(report))
        return report

    monkeypatch.setattr(acceptance, "build_cpu_robustness_campaign_bundle", build)
    monkeypatch.setattr(acceptance, "validate_cpu_robustness_campaign_bundle",
                        lambda _: {"searches": 45, "conditional_references": 15})
    monkeypatch.setattr(acceptance.cpu_robustness_campaign, "run_cpu_robustness_campaign", run)
    monkeypatch.setattr(acceptance, "_verify_replay", lambda *args: {"files_unchanged": True})
    return acceptance, calls, report


def test_acceptance_executes_once_and_refuses_existing_attempt(fake_campaign: Any, tmp_path: Path) -> None:
    acceptance, calls, _ = fake_campaign
    output = tmp_path / "attempt"
    receipt = acceptance.run_acceptance(output, campaign_id="test-search-robustness", execution_context="local-linux-amd64")
    assert receipt["acceptance_passed"] is True and calls == ["run"]
    before = acceptance._inventory(output)
    with pytest.raises(ValueError, match="fresh directory"):
        acceptance.run_acceptance(output, campaign_id="test-search-robustness", execution_context="local-linux-amd64")
    assert calls == ["run"] and acceptance._inventory(output) == before


def test_partial_campaign_is_nonpassing_but_preserves_report_and_replay(fake_campaign: Any, tmp_path: Path) -> None:
    acceptance, calls, report = fake_campaign
    report.update(campaign_completed=False, acceptance_passed=False)
    output = tmp_path / "attempt"
    with pytest.raises(acceptance.RobustnessAcceptanceError, match="partial"):
        acceptance.run_acceptance(output, campaign_id="test-search-robustness", execution_context="local-linux-amd64")
    assert calls == ["run"]
    assert json.loads((output / "report.json").read_text()) == report
    assert json.loads((output / "acceptance.json").read_text())["evidence_integrity_verified"] is True
    assert json.loads((output / "acceptance.json").read_text())["acceptance_passed"] is False
    assert (output / "replay.json").is_file()
    assert json.loads((output / "attempt-failure.json").read_text())["retry_performed"] is False


@pytest.mark.parametrize("field", ["evidence_integrity_verified", "campaign_completed", "acceptance_passed"])
def test_missing_acceptance_requirement_cannot_pass(fake_campaign: Any, tmp_path: Path, field: str) -> None:
    acceptance, calls, report = fake_campaign
    report.pop(field)
    with pytest.raises(acceptance.RobustnessAcceptanceError):
        acceptance.run_acceptance(tmp_path / "attempt", campaign_id="test-search-robustness",
                                  execution_context="local-linux-amd64")
    assert calls == ["run"]


@pytest.mark.parametrize("corruption", [
    "empty-calibration", "missing-calibration", "short-calibration", "mismatched-calibration", "missing-routing",
    "missing-search", "missing-reference", "partial-reference", "veto-audit-launch", "reference-audit-launch",
    "lowered-threshold", "false-veto", "unexpected-positive", "promotion", "benchmark", "gpu",
    "no-robustness", "short-checks", "duplicate-check", "mismatched-check", "aggregate-win",
    "no-offline", "incomplete-offline", "false-invariant", "unknown-invariant", "missing-invariant",
    "offline-measured", "wrong-budgets",
    "missing-seed", "missing-offline-task", "unknown-check", "offline-selection", "generalization",
    "duplicate-variant", "wrong-rounds", "wrong-base-task", "short-full-budget", "duplicate-process",
    "missing-processes", "incomplete-processes", "unreaped-process",
    "reference-selection", "no-curves", "duplicate-curve", "short-curve", "extra-inference", "wrong-prefix-cost",
])
def test_incomplete_or_inconsistent_control_evidence_cannot_pass(
    fake_campaign: Any, tmp_path: Path, corruption: str,
) -> None:
    acceptance, calls, report = fake_campaign
    if corruption == "empty-calibration":
        report["calibration"].update(scheduled_checks=0, matches=0, checks=[])
    elif corruption == "missing-calibration":
        report.pop("calibration")
    elif corruption == "short-calibration":
        report["calibration"].update(scheduled_checks=1, matches=1, checks=[{"matches": True}])
    elif corruption == "mismatched-calibration":
        report["calibration"]["checks"][0]["matches"] = False
    elif corruption == "missing-routing":
        report["audit_routing"]["verified"] = 44
    elif corruption == "missing-search":
        report["rows"].pop(0)
    elif corruption == "missing-reference":
        report["rows"].pop()
    elif corruption == "partial-reference":
        report["status_counts"]["reference"].update(completed=14, failed=1)
    elif corruption in {"veto-audit-launch", "reference-audit-launch"}:
        report["rows"][0 if corruption == "veto-audit-launch" else 45]["observation_coverage"] = [{"phase": "audit"}]
    elif corruption == "lowered-threshold":
        report["rows"][0]["summary"]["confirmation"]["minimum_gain"] = 0
    elif corruption == "false-veto":
        report["rows"][0]["summary"]["confirmation"]["outcomes"] = {"C": True, "W": True, "D": True}
    elif corruption == "unexpected-positive":
        report["audit_routing"].update(positive_count=1, veto_count=44)
    elif corruption == "promotion":
        report["promotion_eligible"] = True
    elif corruption == "benchmark":
        report["benchmark_result_claimed"] = True
    elif corruption == "gpu":
        report["gpu_execution_deferred"] = False
    elif corruption == "no-robustness":
        report.pop("robustness_checks")
    elif corruption == "short-checks":
        report["robustness_checks"]["checks"].pop()
    elif corruption == "duplicate-check":
        report["robustness_checks"]["checks"][1] = report["robustness_checks"]["checks"][0]
    elif corruption == "mismatched-check":
        report["robustness_checks"]["checks"][0]["matches"] = False
    elif corruption == "aggregate-win":
        report["robustness_checks"]["aggregate_adaptive_win_required"] = True
    elif corruption == "reference-selection":
        report["robustness_checks"]["references_used_for_selection"] = True
    elif corruption == "no-offline":
        report.pop("offline_expectations")
    elif corruption == "incomplete-offline":
        report["offline_expectations"]["trajectory_count"] = 6479
    elif corruption == "false-invariant":
        report["offline_expectations"]["invariants"]["budget_prefixes"] = False
    elif corruption == "unknown-invariant":
        report["offline_expectations"]["invariants"]["unrelated"] = True
    elif corruption == "missing-invariant":
        report["offline_expectations"]["invariants"].pop("budget_prefixes")
    elif corruption == "missing-seed":
        report["offline_expectations"]["policy_seeds"].pop()
    elif corruption == "missing-offline-task":
        report["offline_expectations"]["task_summaries"].pop()
    elif corruption == "unknown-check":
        report["robustness_checks"]["checks"][0]["check"] = "unrelated"
    elif corruption == "offline-selection":
        report["offline_expectations"]["used_for_policy_selection"] = True
    elif corruption == "generalization":
        report["offline_expectations"]["population_generalization_claimed"] = True
    elif corruption == "offline-measured":
        report["offline_expectations"]["measured_evidence"] = True
    elif corruption == "wrong-budgets":
        report["offline_expectations"]["budgets"] = [1, 3]
    elif corruption == "duplicate-variant":
        report["rows"][1] = report["rows"][0]
    elif corruption == "wrong-rounds":
        report["rows"][0]["rounds"] = 6
    elif corruption == "wrong-base-task":
        report["rows"][0]["base_task_id"] = "task-02"
    elif corruption == "short-full-budget":
        report["practice_prefix_curves"][-1]["points"] = report["practice_prefix_curves"][-1]["points"][:4]
    elif corruption == "duplicate-process":
        report["process_coverage"]["rows"][1] = report["process_coverage"]["rows"][0]
    elif corruption == "missing-processes":
        report.pop("process_coverage")
    elif corruption == "incomplete-processes":
        report["process_coverage"]["verified_rows"] = 59
    elif corruption == "unreaped-process":
        report["process_coverage"]["rows"][0]["matches"] = False
    elif corruption == "no-curves":
        report.pop("practice_prefix_curves")
    elif corruption == "duplicate-curve":
        report["practice_prefix_curves"][1] = report["practice_prefix_curves"][0]
    elif corruption == "short-curve":
        report["practice_prefix_curves"][0]["points"].pop()
    elif corruption == "extra-inference":
        report["practice_prefix_curves"][0]["additional_inference_performed"] = True
    elif corruption == "wrong-prefix-cost":
        report["practice_prefix_curves"][0]["points"][1]["practice_case_evaluations"] = 8
    output = tmp_path / "attempt"
    with pytest.raises(acceptance.RobustnessAcceptanceError):
        acceptance.run_acceptance(output, campaign_id="test-search-robustness", execution_context="local-linux-amd64")
    assert calls == ["run"]
    receipt = json.loads((output / "acceptance.json").read_text())
    assert receipt["acceptance_passed"] is False and not all(receipt["requirements"].values())
    assert json.loads((output / "attempt-failure.json").read_text())["retry_performed"] is False


def test_exception_keeps_partial_child_evidence(fake_campaign: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    acceptance, calls, _ = fake_campaign

    def fail(bundle: Path, *, output_dir: Path) -> None:
        calls.append("failed")
        output_dir.mkdir()
        (output_dir / "claim.json").write_text("{}")
        raise RuntimeError("worker-boundary-unavailable")

    monkeypatch.setattr(acceptance.cpu_robustness_campaign, "run_cpu_robustness_campaign", fail)
    output = tmp_path / "attempt"
    with pytest.raises(RuntimeError, match="worker-boundary"):
        acceptance.run_acceptance(output, campaign_id="test-search-robustness", execution_context="local-linux-amd64")
    assert calls == ["failed"] and (output / "campaign" / "claim.json").read_text() == "{}"
    assert (output / "attempt.json").is_file() and (output / "validation.json").is_file()
    assert not (output / "acceptance.json").exists()
    failure = json.loads((output / "attempt-failure.json").read_text())
    assert failure["exception_type"] == "RuntimeError" and failure["retry_performed"] is False


def test_source_drift_fails_acceptance(fake_campaign: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    acceptance, calls, _ = fake_campaign
    sources = iter(({"source": "before"}, {"source": "after"}))
    monkeypatch.setattr(acceptance, "_source_inventory", lambda: next(sources))
    output = tmp_path / "attempt"
    with pytest.raises(acceptance.RobustnessAcceptanceError, match="source snapshot"):
        acceptance.run_acceptance(output, campaign_id="test-search-robustness", execution_context="local-linux-amd64")
    assert calls == ["run"] and (output / "report.json").is_file()
    assert not (output / "acceptance.json").exists()


@pytest.mark.parametrize(("system", "machine"), [("Darwin", "arm64"), ("Linux", "aarch64"), ("Windows", "AMD64")])
def test_platform_guard_precedes_attempt_creation(acceptance: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path, system: str, machine: str) -> None:
    monkeypatch.setattr(acceptance.platform, "system", lambda: system)
    monkeypatch.setattr(acceptance.platform, "machine", lambda: machine)
    output = tmp_path / "attempt"
    with pytest.raises(ValueError, match="Linux/amd64"):
        acceptance.run_acceptance(output, campaign_id="test-search-robustness", execution_context="local-linux-amd64")
    assert not output.exists()


def test_ci_context_rejects_missing_runner_metadata(acceptance: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance.platform, "system", lambda: "Linux")
    monkeypatch.setattr(acceptance.platform, "machine", lambda: "x86_64")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(ValueError, match="runner metadata"):
        acceptance._environment("github-actions-linux-amd64")


def test_replay_guards_deny_dispatch_and_mutating_open(acceptance: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text("{}")
    with acceptance._readonly_guards():
        assert path.read_text() == "{}"
        with pytest.raises(AssertionError, match="mutation"):
            path.write_text("changed")
        with pytest.raises(AssertionError, match="mutation"):
            os.open(path, os.O_RDWR)
        with pytest.raises(AssertionError, match="mutation"):
            path.unlink()
        with pytest.raises(AssertionError, match="dispatch"):
            subprocess.Popen(["unused"])
        with pytest.raises(AssertionError, match="dispatch"):
            acceptance.cpu_execution_journal.run_measured_cpu_batch(())
    assert path.read_text() == "{}"


def test_guarded_replay_verifies_both_entrypoints_and_preserves_bytes(acceptance: ModuleType,
                                                                   monkeypatch: pytest.MonkeyPatch,
                                                                   tmp_path: Path,
                                                                   capsys: pytest.CaptureFixture[str]) -> None:
    bundle, campaign = tmp_path / "bundle", tmp_path / "campaign"
    bundle.mkdir()
    campaign.mkdir()
    (bundle / "contract.json").write_text("{}")
    report = {"acceptance_passed": False, "campaign_completed": False, "evidence_integrity_verified": True}
    (campaign / "report.json").write_text(json.dumps(report))
    calls = []

    def read(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("read")
        return report.copy()

    monkeypatch.setattr(acceptance.cpu_robustness_campaign, "report_cpu_robustness_campaign", read)
    monkeypatch.setattr(acceptance.cpu_robustness_campaign, "run_cpu_robustness_campaign", read)
    replay = acceptance._verify_replay(bundle, campaign, report)
    assert calls == ["read", "read"]
    assert replay["files_unchanged"] and replay["terminal_run_replay_verified"]
    messages = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [(message["stage"], message["event"]) for message in messages] == [
        ("report-replay", "started"), ("report-replay", "finished"),
        ("terminal-replay", "started"), ("terminal-replay", "finished"),
    ]


def test_guarded_replay_rejects_changed_report(acceptance: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                             tmp_path: Path) -> None:
    bundle, campaign = tmp_path / "bundle", tmp_path / "campaign"
    bundle.mkdir()
    campaign.mkdir()
    monkeypatch.setattr(acceptance.cpu_robustness_campaign, "report_cpu_robustness_campaign",
                        lambda *args, **kwargs: {"changed": True})
    with pytest.raises(acceptance.RobustnessAcceptanceError, match="report changed"):
        acceptance._verify_replay(bundle, campaign, {})


def test_symlink_attempt_parent_is_rejected(fake_campaign: Any, tmp_path: Path) -> None:
    acceptance, calls, _ = fake_campaign
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        acceptance.run_acceptance(alias / "attempt", campaign_id="test-search-robustness", execution_context="local-linux-amd64")
    assert calls == [] and not (real / "attempt").exists()


def test_progress_counts_only_bounded_regular_parent_results(acceptance: ModuleType, tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    assert acceptance._progress_counts(campaign) == {"count_status": "not-created"}
    results = campaign / "results"
    results.mkdir(parents=True)
    # Deliberately invalid payloads prove progress does not interpret evidence.
    for name in ("00000000.json", "00000044.json", "00000045.json", "00000059.json", "00000060.json",
                 ".session-publish-scratch", "unowned.json"):
        (results / name).write_text("unverified")
    (results / "00000001.json").mkdir()
    (results / "00000002.json").symlink_to(results / "00000000.json")
    before = {path.name: path.lstat() for path in results.iterdir()}
    assert acceptance._progress_counts(campaign) == {
        "count_status": "observed", "published_parent_results": 4,
        "published_search_results": 2, "published_reference_results": 2,
    }
    assert {path.name: path.lstat() for path in results.iterdir()} == before


def test_progress_inventory_stops_at_entry_bound(acceptance: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    visited = []

    class Entries:
        def __enter__(self) -> Entries:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def __iter__(self) -> Any:
            for index in range(acceptance._PROGRESS_MAX_ENTRIES + 1):
                visited.append(index)
                yield type("Entry", (), {"name": "ignored"})()
            pytest.fail("progress exceeded its bounded scan")

    monkeypatch.setattr(acceptance.os, "scandir", lambda _: Entries())
    assert acceptance._progress_counts(tmp_path) == {"count_status": "entry-bound-exceeded"}
    assert len(visited) == acceptance._PROGRESS_MAX_ENTRIES + 1


def test_progress_io_failure_cannot_change_acceptance(fake_campaign: Any, monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
    acceptance, calls, _ = fake_campaign
    original = acceptance.os.scandir

    def unavailable(path: Any) -> Any:
        if Path(path).name == "results":
            raise PermissionError("progress directory unavailable")
        return original(path)

    monkeypatch.setattr(acceptance.os, "scandir", unavailable)
    assert acceptance._progress_counts(tmp_path) == {"count_status": "unavailable"}
    assert acceptance.run_acceptance(tmp_path / "attempt", campaign_id="test-search-robustness",
                                     execution_context="local-linux-amd64")["acceptance_passed"]
    assert calls == ["run"]


def test_progress_is_flushed_advisory_stderr_and_never_acceptance_data(acceptance: ModuleType,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = []
    monkeypatch.setattr(acceptance.builtins, "print", lambda *args, **kwargs: calls.append((args, kwargs)))
    acceptance._emit_progress("execute", "heartbeat", campaign=tmp_path, elapsed=12.5)
    args, kwargs = calls[0]
    assert kwargs == {"file": acceptance.sys.stderr, "flush": True}
    message = json.loads(args[0])
    assert message["stage"] == "execute" and message["event"] == "heartbeat"
    assert message["authoritative"] is False and message["used_for_acceptance"] is False
    assert message["counts_are_unverified_directory_observations"] is True
    assert message["elapsed_seconds"] == 12.5


@pytest.mark.parametrize("interrupted", [False, True])
def test_periodic_progress_is_readonly_under_replay_guards_and_stops(acceptance: ModuleType,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, interrupted: bool,
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    (results / "00000045.json").write_text("unverified")
    before = acceptance._inventory(tmp_path)
    observed = threading.Event()
    messages = []
    original = acceptance._emit_progress

    def emit(stage: str, event: str, **kwargs: Any) -> None:
        original(stage, event, **kwargs)
        messages.append((stage, event))
        if event == "heartbeat":
            observed.set()

    monkeypatch.setattr(acceptance, "_emit_progress", emit)
    monkeypatch.setattr(acceptance, "_PROGRESS_INTERVAL_SECONDS", 0.01)
    with acceptance._readonly_guards():
        try:
            with acceptance._progress("report-replay", campaign=tmp_path):
                assert observed.wait(timeout=5), "progress heartbeat did not arrive"
                with pytest.raises(AssertionError, match="mutation"):
                    (tmp_path / "new.json").write_text("forbidden")
                if interrupted:
                    raise RuntimeError("replay interrupted")
        except RuntimeError as error:
            assert interrupted and str(error) == "replay interrupted"
    assert messages[0] == ("report-replay", "started")
    assert messages[-1] == ("report-replay", "interrupted" if interrupted else "finished")
    assert not any(thread.name == "cpu-search-robustness-advisory-progress" for thread in threading.enumerate())
    assert acceptance._inventory(tmp_path) == before


def test_successful_acceptance_emits_stages_without_progress_artifacts(
    fake_campaign: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    module, calls, _ = fake_campaign
    output = tmp_path / "attempt"
    module.run_acceptance(output, campaign_id="test-search-robustness", execution_context="local-linux-amd64")
    captured = capsys.readouterr()
    messages = [json.loads(line) for line in captured.err.splitlines()]
    assert [message["stage"] for message in messages if message["event"] == "started"] == ["build", "execute"]
    assert messages[-1]["stage"] == "complete" and messages[-1]["event"] == "finished"
    assert captured.out == "" and calls == ["run"]
    assert not any("progress" in path.name for path in output.rglob("*"))
