"""Tests for the opt-in repair stage wired before staged validation (AC-878).

Two properties are pinned:

- flag OFF (the default): the stage is a byte-unchanged no-op. It emits no
  events and mutates nothing on the GenerationContext.
- flag ON (scenario allowlisted): the RepairGate runs at the seam, emits a
  ``repair`` channel event per repair, and a structurally repairable tool-call
  JSON is repaired in place on the strategy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autocontext.config.settings import AppSettings
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.loop.stage_repair import stage_repair
from autocontext.loop.stage_types import GenerationContext


class _FakeScenario:
    name = "grid_ctf"


def _make_ctx(
    *,
    enabled: bool,
    scenarios: str,
    strategy: dict[str, Any] | None = None,
) -> GenerationContext:
    settings = AppSettings(
        harness_repair_gates_enabled=enabled,
        harness_repair_gate_scenarios=scenarios,
    )
    return GenerationContext(
        run_id="test-run",
        scenario_name="grid_ctf",
        scenario=_FakeScenario(),
        generation=1,
        settings=settings,
        previous_best=0.0,
        challenger_elo=1000.0,
        score_history=[],
        gate_decision_history=[],
        coach_competitor_hints="",
        replay_narrative="",
        current_strategy=strategy if strategy is not None else {"action": "move"},
    )


def _capture(tmp_path: Path) -> tuple[EventStreamEmitter, list[tuple[str, dict[str, object]]]]:
    emitter = EventStreamEmitter(tmp_path / "events.ndjson")
    events: list[tuple[str, dict[str, object]]] = []
    emitter.subscribe(lambda event, payload: events.append((event, payload)))
    return emitter, events


def test_flag_off_is_byte_unchanged_no_op(tmp_path: Path) -> None:
    # Golden: with the gate disabled the stage returns the identical context,
    # mutates nothing, and emits nothing (byte-unchanged default path).
    strategy = {"action": "move", "__tool_call_json__": '{"a": 1,}'}
    ctx = _make_ctx(enabled=False, scenarios="grid_ctf", strategy=strategy)
    before = dict(ctx.current_strategy)
    emitter, events = _capture(tmp_path)

    result = stage_repair(ctx, events=emitter)

    assert result is ctx
    assert ctx.current_strategy == before
    assert events == []
    # No events file is written when the stage is a no-op.
    assert not (tmp_path / "events.ndjson").exists()


def test_flag_off_when_scenario_not_allowlisted(tmp_path: Path) -> None:
    ctx = _make_ctx(enabled=True, scenarios="othello", strategy={"__tool_call_json__": '{"a": 1,}'})
    before = dict(ctx.current_strategy)
    emitter, events = _capture(tmp_path)

    stage_repair(ctx, events=emitter)

    assert ctx.current_strategy == before
    assert events == []


def test_flag_on_runs_gate_and_emits_at_the_seam(tmp_path: Path) -> None:
    # A repairable tool-call JSON is repaired in place, and one event per repair
    # is emitted on the repair channel carrying the scenario alongside a
    # schema-valid RepairResult.
    ctx = _make_ctx(enabled=True, scenarios="grid_ctf", strategy={"__tool_call_json__": '{"a": 1,}'})
    emitter, events = _capture(tmp_path)

    stage_repair(ctx, events=emitter)

    # The trailing-comma JSON was structurally repaired back onto the strategy.
    assert ctx.current_strategy["__tool_call_json__"] == '{"a": 1}'

    # One event per default repair (three), the first is the applied tool-call.
    assert len(events) == 3
    applied = [payload for event, payload in events if event == "repair_applied"]
    assert len(applied) == 1
    assert applied[0]["scenario"] == "grid_ctf"
    assert applied[0]["result"]["repair_name"] == "tool_call_json"
    assert applied[0]["result"]["status"] == "applied"


def test_flag_on_without_repairable_input_still_emits_skips(tmp_path: Path) -> None:
    ctx = _make_ctx(enabled=True, scenarios="grid_ctf", strategy={"action": "move"})
    emitter, events = _capture(tmp_path)

    stage_repair(ctx, events=emitter)

    # Nothing to repair, but the seam still ran the gate and emitted skips.
    assert [event for event, _ in events] == ["repair_skipped", "repair_skipped", "repair_skipped"]
    assert ctx.current_strategy == {"action": "move"}
