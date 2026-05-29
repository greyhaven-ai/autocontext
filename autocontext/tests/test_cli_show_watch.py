"""AC-697 slice 7: Python `autoctx show` + `autoctx watch` parity tests.

Slice 1 (PR #981) pinned `show` and `watch` as canonical paved-road
commands; TS shipped them; Python had stub gaps. This slice adds the
Python equivalents that compose the existing `store.get_run` and
`store.run_status` read surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.cli_contract import iter_python_command_paths


def test_show_and_watch_are_registered_at_canonical_paths() -> None:
    """Slice 7 closes the slice-1 `show`/`watch` Python
    intentional_gap entries."""
    observed = {tuple(path) for path in iter_python_command_paths(app)}
    assert ("show",) in observed
    assert ("watch",) in observed


def test_contract_show_and_watch_are_yes_on_both_runtimes() -> None:
    contract = json.loads((Path(__file__).resolve().parents[2] / "docs" / "cli-contract.json").read_text(encoding="utf-8"))
    by_id = {cmd["id"]: cmd for cmd in contract["commands"]}
    for cmd_id in ("show", "watch"):
        assert by_id[cmd_id]["runtime_support"]["python"]["status"] == "yes", cmd_id
        assert by_id[cmd_id]["runtime_support"]["typescript"]["status"] == "yes", cmd_id


def _make_store_stub(run_row: dict[str, Any] | None, generations: list[dict[str, Any]]):
    """Build a minimal store stub the show/watch commands can use."""

    class _StubStore:
        def get_run(self, _run_id: str) -> dict[str, Any] | None:
            return run_row

        def run_status(self, _run_id: str) -> list[dict[str, Any]]:
            return generations

        def close(self) -> None:
            pass

    return _StubStore()


def test_show_missing_run_emits_actionable_error(tmp_path, monkeypatch) -> None:
    """`autoctx show <run-id>` for an unknown run must exit non-zero
    with a clear message naming the run id, not crash."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("autocontext.cli._sqlite_from_settings", lambda _: _make_store_stub(None, []))
    result = CliRunner().invoke(app, ["show", "nonexistent-run-id"])
    assert result.exit_code != 0
    assert "nonexistent-run-id" in result.output


def test_show_with_best_filters_to_top_scoring_generation(tmp_path, monkeypatch) -> None:
    """`--best` reduces the rendered generation list to the single
    row with the highest `best_score`."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))

    rows: list[dict[str, Any]] = [
        {
            "generation_index": 1,
            "mean_score": 0.5,
            "best_score": 0.6,
            "elo": 1500.0,
            "wins": 1,
            "losses": 0,
            "gate_decision": "advance",
            "status": "completed",
        },
        {
            "generation_index": 2,
            "mean_score": 0.8,
            "best_score": 0.92,
            "elo": 1620.0,
            "wins": 3,
            "losses": 0,
            "gate_decision": "advance",
            "status": "completed",
        },
        {
            "generation_index": 3,
            "mean_score": 0.7,
            "best_score": 0.85,
            "elo": 1580.0,
            "wins": 2,
            "losses": 1,
            "gate_decision": "advance",
            "status": "completed",
        },
    ]
    run = {"scenario": "grid_ctf", "status": "completed"}
    monkeypatch.setattr("autocontext.cli._sqlite_from_settings", lambda _: _make_store_stub(run, rows))

    result = CliRunner().invoke(app, ["show", "abc123", "--best", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["run_id"] == "abc123"
    assert len(payload["generations"]) == 1
    assert payload["generations"][0]["generation"] == 2
    assert payload["generations"][0]["best_score"] == pytest.approx(0.92)


def test_show_with_explicit_generation_filters_to_that_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))

    rows: list[dict[str, Any]] = [
        {
            "generation_index": 1,
            "mean_score": 0.5,
            "best_score": 0.6,
            "elo": 1500.0,
            "wins": 1,
            "losses": 0,
            "gate_decision": "advance",
            "status": "completed",
        },
        {
            "generation_index": 2,
            "mean_score": 0.8,
            "best_score": 0.92,
            "elo": 1620.0,
            "wins": 3,
            "losses": 0,
            "gate_decision": "advance",
            "status": "completed",
        },
    ]
    monkeypatch.setattr(
        "autocontext.cli._sqlite_from_settings",
        lambda _: _make_store_stub({"scenario": "x", "status": "done"}, rows),
    )

    result = CliRunner().invoke(app, ["show", "abc123", "--generation", "1", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert len(payload["generations"]) == 1
    assert payload["generations"][0]["generation"] == 1


def test_watch_breaks_immediately_on_a_terminal_generation(tmp_path, monkeypatch) -> None:
    """`watch` polls until the latest generation status is terminal
    (completed / failed / etc). When the latest generation is already
    terminal, the loop emits one line and returns without sleeping."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))

    rows: list[dict[str, Any]] = [
        {
            "generation_index": 1,
            "mean_score": 0.5,
            "best_score": 0.7,
            "elo": 1550.0,
            "wins": 1,
            "losses": 0,
            "gate_decision": "advance",
            "status": "completed",
        }
    ]
    monkeypatch.setattr(
        "autocontext.cli._sqlite_from_settings",
        lambda _: _make_store_stub({"scenario": "x", "status": "completed"}, rows),
    )
    # Patch time.sleep to assert no actual sleeping happens. The loop
    # should return on the very first poll because the latest row is
    # already in a terminal status.
    sleep_calls: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

    result = CliRunner().invoke(app, ["watch", "abc123", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload == {
        "run_id": "abc123",
        "generation": 1,
        "status": "completed",
        "best_score": 0.7,
        "gate_decision": "advance",
    }
    # Terminal on first poll -> no sleep needed.
    assert sleep_calls == []


def test_watch_terminal_run_with_no_generations_exits(tmp_path, monkeypatch) -> None:
    """PR #1002 review (P2): a failed run with `run_status=[]` used
    to loop forever because the previous design only checked the
    latest generation's status to break the loop. The fix polls the
    run row each iteration and exits when the run row itself is in a
    terminal status, even without generation rows."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))

    monkeypatch.setattr(
        "autocontext.cli._sqlite_from_settings",
        lambda _: _make_store_stub(
            {"scenario": "x", "status": "failed"},  # terminal run row
            [],  # no generations recorded
        ),
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

    result = CliRunner().invoke(app, ["watch", "abc123", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    # Run-level terminal status surfaced with `generation: null`.
    assert payload == {"run_id": "abc123", "generation": None, "status": "failed"}
    # No sleep needed: terminal run is detected on the first poll.
    assert sleep_calls == []


def test_watch_missing_run_exits_non_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("autocontext.cli._sqlite_from_settings", lambda _: _make_store_stub(None, []))
    result = CliRunner().invoke(app, ["watch", "nonexistent"])
    assert result.exit_code != 0
    assert "nonexistent" in result.output
