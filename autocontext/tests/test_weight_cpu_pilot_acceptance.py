"""Offline acceptance-driver failures must preserve evidence without another pilot."""

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


@pytest.fixture
def acceptance() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "cpu_pilot" / "acceptance.py"
    spec = importlib.util.spec_from_file_location("cpu_pilot_acceptance_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_pilot(acceptance: ModuleType, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, list[str], dict[str, Any]]:
    calls: list[str] = []
    report = {"evidence_integrity_verified": True, "campaign_completed": True, "acceptance_passed": True}
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

    monkeypatch.setattr(acceptance, "build_cpu_pilot_bundle", build)
    monkeypatch.setattr(acceptance, "validate_cpu_pilot_bundle", lambda _: {"searches": 72, "conditional_references": 24})
    monkeypatch.setattr(acceptance.cpu_pilot, "run_cpu_pilot", run)
    monkeypatch.setattr(acceptance, "_verify_replay", lambda *args: {"files_unchanged": True})
    return acceptance, calls, report


def test_acceptance_executes_once_and_refuses_existing_attempt(fake_pilot: Any, tmp_path: Path) -> None:
    acceptance, calls, _ = fake_pilot
    output = tmp_path / "attempt"
    receipt = acceptance.run_acceptance(output, campaign_id="test-pilot", execution_context="local-linux-amd64")
    assert receipt["acceptance_passed"] is True and calls == ["run"]
    before = acceptance._inventory(output)
    with pytest.raises(ValueError, match="fresh directory"):
        acceptance.run_acceptance(output, campaign_id="test-pilot", execution_context="local-linux-amd64")
    assert calls == ["run"] and acceptance._inventory(output) == before


def test_partial_pilot_is_nonpassing_but_preserves_report_and_replay(fake_pilot: Any, tmp_path: Path) -> None:
    acceptance, calls, report = fake_pilot
    report.update(campaign_completed=False, acceptance_passed=False)
    output = tmp_path / "attempt"
    with pytest.raises(acceptance.PilotAcceptanceError, match="partial"):
        acceptance.run_acceptance(output, campaign_id="test-pilot", execution_context="local-linux-amd64")
    assert calls == ["run"]
    assert json.loads((output / "report.json").read_text()) == report
    assert json.loads((output / "acceptance.json").read_text())["evidence_integrity_verified"] is True
    assert json.loads((output / "acceptance.json").read_text())["acceptance_passed"] is False
    assert (output / "replay.json").is_file()
    assert json.loads((output / "attempt-failure.json").read_text())["retry_performed"] is False


@pytest.mark.parametrize("field", ["evidence_integrity_verified", "campaign_completed", "acceptance_passed"])
def test_missing_acceptance_requirement_cannot_pass(fake_pilot: Any, tmp_path: Path, field: str) -> None:
    acceptance, calls, report = fake_pilot
    report.pop(field)
    with pytest.raises(acceptance.PilotAcceptanceError):
        acceptance.run_acceptance(tmp_path / "attempt", campaign_id="test-pilot", execution_context="local-linux-amd64")
    assert calls == ["run"]


def test_exception_keeps_partial_child_evidence(fake_pilot: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    acceptance, calls, _ = fake_pilot

    def fail(bundle: Path, *, output_dir: Path) -> None:
        calls.append("failed")
        output_dir.mkdir()
        (output_dir / "claim.json").write_text("{}")
        raise RuntimeError("worker-boundary-unavailable")

    monkeypatch.setattr(acceptance.cpu_pilot, "run_cpu_pilot", fail)
    output = tmp_path / "attempt"
    with pytest.raises(RuntimeError, match="worker-boundary"):
        acceptance.run_acceptance(output, campaign_id="test-pilot", execution_context="local-linux-amd64")
    assert calls == ["failed"] and (output / "campaign" / "claim.json").read_text() == "{}"
    assert (output / "attempt.json").is_file() and (output / "validation.json").is_file()
    assert not (output / "acceptance.json").exists()
    failure = json.loads((output / "attempt-failure.json").read_text())
    assert failure["exception_type"] == "RuntimeError" and failure["retry_performed"] is False


def test_source_drift_fails_acceptance(fake_pilot: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    acceptance, calls, _ = fake_pilot
    sources = iter(({"source": "before"}, {"source": "after"}))
    monkeypatch.setattr(acceptance, "_source_inventory", lambda: next(sources))
    output = tmp_path / "attempt"
    with pytest.raises(acceptance.PilotAcceptanceError, match="source snapshot"):
        acceptance.run_acceptance(output, campaign_id="test-pilot", execution_context="local-linux-amd64")
    assert calls == ["run"] and (output / "report.json").is_file()
    assert not (output / "acceptance.json").exists()


@pytest.mark.parametrize(("system", "machine"), [("Darwin", "arm64"), ("Linux", "aarch64"), ("Windows", "AMD64")])
def test_platform_guard_precedes_attempt_creation(acceptance: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path, system: str, machine: str) -> None:
    monkeypatch.setattr(acceptance.platform, "system", lambda: system)
    monkeypatch.setattr(acceptance.platform, "machine", lambda: machine)
    output = tmp_path / "attempt"
    with pytest.raises(ValueError, match="Linux/amd64"):
        acceptance.run_acceptance(output, campaign_id="test-pilot", execution_context="local-linux-amd64")
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

    monkeypatch.setattr(acceptance.cpu_pilot, "report_cpu_pilot", read)
    monkeypatch.setattr(acceptance.cpu_pilot, "run_cpu_pilot", read)
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
    monkeypatch.setattr(acceptance.cpu_pilot, "report_cpu_pilot", lambda *args, **kwargs: {"changed": True})
    with pytest.raises(acceptance.PilotAcceptanceError, match="report changed"):
        acceptance._verify_replay(bundle, campaign, {})


def test_symlink_attempt_parent_is_rejected(fake_pilot: Any, tmp_path: Path) -> None:
    acceptance, calls, _ = fake_pilot
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        acceptance.run_acceptance(alias / "attempt", campaign_id="test-pilot", execution_context="local-linux-amd64")
    assert calls == [] and not (real / "attempt").exists()


def test_progress_counts_only_bounded_regular_parent_results(acceptance: ModuleType, tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    assert acceptance._progress_counts(campaign) == {"count_status": "not-created"}
    results = campaign / "results"
    results.mkdir(parents=True)
    # Deliberately invalid payloads prove progress does not interpret evidence.
    for name in ("00000000.json", "00000071.json", "00000072.json", "00000095.json", "00000096.json",
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


def test_progress_io_failure_cannot_change_acceptance(fake_pilot: Any, monkeypatch: pytest.MonkeyPatch,
                                                    tmp_path: Path) -> None:
    acceptance, calls, _ = fake_pilot
    original = acceptance.os.scandir

    def unavailable(path: Any) -> Any:
        if Path(path).name == "results":
            raise PermissionError("progress directory unavailable")
        return original(path)

    monkeypatch.setattr(acceptance.os, "scandir", unavailable)
    assert acceptance._progress_counts(tmp_path) == {"count_status": "unavailable"}
    assert acceptance.run_acceptance(tmp_path / "attempt", campaign_id="test-pilot",
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
    (results / "00000072.json").write_text("unverified")
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
    assert not any(thread.name == "cpu-pilot-advisory-progress" for thread in threading.enumerate())
    assert acceptance._inventory(tmp_path) == before


def test_successful_acceptance_emits_stages_without_progress_artifacts(
    fake_pilot: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    module, calls, _ = fake_pilot
    output = tmp_path / "attempt"
    module.run_acceptance(output, campaign_id="test-pilot", execution_context="local-linux-amd64")
    captured = capsys.readouterr()
    messages = [json.loads(line) for line in captured.err.splitlines()]
    assert [message["stage"] for message in messages if message["event"] == "started"] == ["build", "execute"]
    assert messages[-1]["stage"] == "complete" and messages[-1]["event"] == "finished"
    assert captured.out == "" and calls == ["run"]
    assert not any("progress" in path.name for path in output.rglob("*"))
