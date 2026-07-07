"""Tests for the opt-in leakage stage wired into the generation pipeline (AC-879).

Mirrors the AC-878 repair-gate seam. Two properties are pinned:

- flag OFF (the default): the stage is a byte-unchanged no-op. It returns the
  identical context, mutates nothing, and reads nothing.
- flag ON (scenario allowlisted): the stage reads declared integrity metadata
  plus access records off the strategy, runs the leakage audit + gate, records
  the decision onto ``ctx.exploration_metadata`` and telemetry, and appends
  ``leakage_blocked`` to the gate decision history when the gate fails closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autocontext.config.settings import AppSettings
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.loop.stage_leakage import stage_leakage
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
        harness_leakage_gate_enabled=enabled,
        harness_leakage_gate_scenarios=scenarios,
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


def _integrity_meta(*, mode: str, forbidden: list[str], status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": "test-run",
        "mode": mode,
        "allowed_sources": [],
        "forbidden_sources": forbidden,
        "required_sources": [],
        "web_policy": "open",
        "web_allowlist": None,
        "split_ids": [],
        "prompt_provenance": "repo",
        "adapter_capabilities": [],
        "leakage_status": status,
        "contamination_reasons": [],
    }


def test_flag_off_is_byte_unchanged_no_op(tmp_path: Path) -> None:
    # Golden: with the gate disabled the stage returns the identical context,
    # mutates nothing, and emits nothing (byte-unchanged default path).
    strategy = {
        "action": "move",
        "__integrity_metadata__": _integrity_meta(mode="verified", forbidden=["holdout"], status="contaminated"),
        "__access_records__": [{"resource": "holdout.txt", "source_id": "holdout", "kind": "file"}],
    }
    ctx = _make_ctx(enabled=False, scenarios="grid_ctf", strategy=strategy)
    before = dict(ctx.current_strategy)
    emitter, events = _capture(tmp_path)

    result = stage_leakage(ctx, events=emitter)

    assert result is ctx
    assert ctx.current_strategy == before
    assert "leakage_gate" not in ctx.exploration_metadata
    assert ctx.gate_decision_history == []
    assert events == []
    assert not (tmp_path / "events.ndjson").exists()


def test_flag_off_when_scenario_not_allowlisted(tmp_path: Path) -> None:
    ctx = _make_ctx(enabled=True, scenarios="othello", strategy={"action": "move"})
    emitter, events = _capture(tmp_path)

    result = stage_leakage(ctx, events=emitter)

    assert result is ctx
    assert "leakage_gate" not in ctx.exploration_metadata
    assert events == []


def test_active_without_integrity_metadata_emits_skip(tmp_path: Path) -> None:
    ctx = _make_ctx(enabled=True, scenarios="grid_ctf", strategy={"action": "move"})
    emitter, events = _capture(tmp_path)

    result = stage_leakage(ctx, events=emitter)

    assert result is ctx
    assert "leakage_gate" not in ctx.exploration_metadata
    assert [event for event, _ in events] == ["leakage_skipped"]
    payload = events[0][1]
    assert payload["scenario"] == "grid_ctf"
    assert payload["reason"] == "no integrity metadata"


def test_active_contaminated_blocks(tmp_path: Path) -> None:
    strategy = {
        "__integrity_metadata__": _integrity_meta(mode="verified", forbidden=["holdout"], status="contaminated"),
        "__access_records__": [{"resource": "holdout.txt", "source_id": "holdout", "kind": "file"}],
    }
    ctx = _make_ctx(enabled=True, scenarios="grid_ctf", strategy=strategy)
    emitter, events = _capture(tmp_path)

    stage_leakage(ctx, events=emitter)

    decision = ctx.exploration_metadata["leakage_gate"]
    assert decision["advance"] is False
    assert decision["status"] == "contaminated"
    assert ctx.gate_decision_history == ["leakage_blocked"]
    assert [event for event, _ in events] == ["leakage_blocked"]


def test_active_clean_advances(tmp_path: Path) -> None:
    strategy = {
        "__integrity_metadata__": _integrity_meta(mode="verified", forbidden=[], status="clean"),
        "__access_records__": [],
    }
    ctx = _make_ctx(enabled=True, scenarios="grid_ctf", strategy=strategy)
    emitter, events = _capture(tmp_path)

    stage_leakage(ctx, events=emitter)

    decision = ctx.exploration_metadata["leakage_gate"]
    assert decision["advance"] is True
    assert decision["status"] == "clean"
    assert ctx.gate_decision_history == []
    assert [event for event, _ in events] == ["leakage_clean"]


def test_active_without_events_emitter_is_safe(tmp_path: Path) -> None:
    strategy = {
        "__integrity_metadata__": _integrity_meta(mode="verified", forbidden=[], status="clean"),
        "__access_records__": [],
    }
    ctx = _make_ctx(enabled=True, scenarios="grid_ctf", strategy=strategy)

    result = stage_leakage(ctx)

    assert result is ctx
    assert ctx.exploration_metadata["leakage_gate"]["advance"] is True
