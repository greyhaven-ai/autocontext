"""Tests for the opt-in RepairGate and its trace events (AC-878)."""

from __future__ import annotations

from pathlib import Path

from autocontext.config.settings import AppSettings
from autocontext.control_plane.contract_probes._base import ArtifactContractProbeInputs
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.harness_optimization.contract.models import RepairResult
from autocontext.harness_optimization.repair_gate import (
    DEFAULT_REPAIRS,
    RepairContext,
    RepairGate,
    finish_guard_step,
    repair_artifact_landing_step,
    repair_gate_active_for,
    repair_tool_call_json_step,
)


def _settings(*, enabled: bool, scenarios: str) -> AppSettings:
    return AppSettings(
        harness_repair_gates_enabled=enabled,
        harness_repair_gate_scenarios=scenarios,
    )


# ---------------------------------------------------------------------------
# repair_gate_active_for: the opt-in truth table
# ---------------------------------------------------------------------------


def test_active_when_enabled_and_scenario_allowlisted() -> None:
    settings = _settings(enabled=True, scenarios="grid_ctf, othello")
    assert repair_gate_active_for(settings, "grid_ctf") is True
    assert repair_gate_active_for(settings, "othello") is True


def test_inactive_when_enabled_but_scenario_not_allowlisted() -> None:
    settings = _settings(enabled=True, scenarios="grid_ctf")
    assert repair_gate_active_for(settings, "othello") is False


def test_inactive_when_disabled_even_if_scenario_listed() -> None:
    settings = _settings(enabled=False, scenarios="grid_ctf")
    assert repair_gate_active_for(settings, "grid_ctf") is False


def test_inactive_when_allowlist_empty() -> None:
    settings = _settings(enabled=True, scenarios="")
    assert repair_gate_active_for(settings, "grid_ctf") is False


# ---------------------------------------------------------------------------
# RepairGate.run: events + schema-valid payloads
# ---------------------------------------------------------------------------


class _Capture:
    """Subscriber collecting (event, payload) tuples off the emitter."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def __call__(self, event: str, payload: dict[str, object]) -> None:
        self.events.append((event, payload))


def _gate(tmp_path: Path, *steps: object) -> tuple[RepairGate, _Capture]:
    emitter = EventStreamEmitter(tmp_path / "events.ndjson")
    capture = _Capture()
    emitter.subscribe(capture)
    repairs = tuple(steps) if steps else DEFAULT_REPAIRS
    return RepairGate(emitter=emitter, repairs=repairs), capture  # type: ignore[arg-type]


def test_run_emits_repair_applied_with_schema_valid_payload(tmp_path: Path) -> None:
    gate, capture = _gate(tmp_path, repair_tool_call_json_step)
    ctx = RepairContext(tool_call_json='{"a": 1,}')  # trailing comma -> repairable

    results = gate.run("grid_ctf", ctx)

    assert len(results) == 1
    assert results[0].status == "applied"
    assert ctx.repaired_tool_call_json == '{"a": 1}'
    assert len(capture.events) == 1
    event, payload = capture.events[0]
    assert event == "repair_applied"
    # The scenario rides alongside the RepairResult dump, which re-validates.
    assert payload["scenario"] == "grid_ctf"
    revalidated = RepairResult.model_validate(payload["result"])
    assert revalidated.status == "applied"
    assert revalidated.repair_name == "tool_call_json"


def test_run_emits_repair_skipped_for_ambiguous_input(tmp_path: Path) -> None:
    gate, capture = _gate(tmp_path, repair_tool_call_json_step)
    ctx = RepairContext(tool_call_json="{{{{ not json")  # unrecoverable

    results = gate.run("grid_ctf", ctx)

    assert results[0].status == "skipped"
    assert [e for e, _ in capture.events] == ["repair_skipped"]
    assert capture.events[0][1]["scenario"] == "grid_ctf"
    RepairResult.model_validate(capture.events[0][1]["result"])


def test_run_emits_repair_skipped_for_not_applicable_absent_input(tmp_path: Path) -> None:
    gate, capture = _gate(tmp_path, finish_guard_step)
    ctx = RepairContext()  # no finish claim present

    results = gate.run("grid_ctf", ctx)

    assert results[0].status == "not_applicable"
    assert [e for e, _ in capture.events] == ["repair_skipped"]
    RepairResult.model_validate(capture.events[0][1]["result"])


def test_run_applies_artifact_relocation_and_emits_applied(tmp_path: Path) -> None:
    expected = ArtifactContractProbeInputs(
        path="out/report.md",
        content="",  # nothing landed at the expected path
        required_substrings=("SUMMARY",),
    )
    gate, capture = _gate(tmp_path, repair_artifact_landing_step)
    ctx = RepairContext(
        artifact_expected=expected,
        artifact_produced={"tmp/report.md": "SUMMARY: all good"},
    )

    results = gate.run("grid_ctf", ctx)

    assert results[0].status == "applied"
    assert ctx.relocation_target == "tmp/report.md"
    assert [e for e, _ in capture.events] == ["repair_applied"]
    RepairResult.model_validate(capture.events[0][1]["result"])


def test_run_emits_one_event_per_enabled_repair(tmp_path: Path) -> None:
    gate, capture = _gate(tmp_path)  # DEFAULT_REPAIRS (three repairs)
    ctx = RepairContext(tool_call_json='{"a": 1}')  # only tool json present

    results = gate.run("grid_ctf", ctx)

    assert len(results) == len(DEFAULT_REPAIRS) == 3
    assert len(capture.events) == 3
    for _event, payload in capture.events:
        assert payload["scenario"] == "grid_ctf"
        RepairResult.model_validate(payload["result"])
