"""AC-697 slice 2: Python `autoctx queue status` parity tests.

Slice 1 (PR #981) pinned the canonical contract: top-level ``status``
means run-status; queue-pending count lives under ``queue status``.
Python's top-level ``status`` already required a ``<run-id>`` positional
(matching the canonical meaning), so this slice fills the parity gap
by adding ``status`` as an accepted action on the existing ``queue``
typer command.

The ``queue.status`` contract entry stays
``runtime_support.python: intentional_gap`` because the contract
walker reads Typer's registered subcommands and the current ``queue``
command uses an action-positional dispatch (``queue add`` /
``queue status``) rather than a registered sub-Typer group; a
follow-up AC-697 slice promotes ``queue`` to a sub-Typer group
without breaking the existing ``autoctx queue -s <spec>`` callers.
The behavior tested here is still reachable end-to-end via the CLI.
"""

from __future__ import annotations

from typer.testing import CliRunner

from autocontext.cli import app


def test_queue_status_action_reports_pending_count(tmp_path, monkeypatch) -> None:
    """`autoctx queue status` returns the queue-pending count.

    Uses a tmp Doppler-free environment so settings load against a
    throwaway SQLite path. Asserts the JSON shape pinned by the
    slice-2 contract: ``{"pending_count": <int>}``."""
    # Point the CLI at a tmp DB so no global state leaks.
    db_path = tmp_path / "autocontext.db"
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(db_path))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))

    result = CliRunner().invoke(app, ["queue", "status", "--json"])
    assert result.exit_code == 0, result.output
    # Output is a JSON object with `pending_count`. Empty DB => 0.
    import json as _json

    payload = _json.loads(result.output.strip())
    assert payload["pending_count"] == 0


def test_queue_status_unknown_action_emits_clear_error(tmp_path, monkeypatch) -> None:
    """An unrecognized queue action lists the supported set rather than
    accepting it silently. Pins the slice-2 action vocabulary at
    {add, status}."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))
    result = CliRunner().invoke(app, ["queue", "nonsense", "--json"])
    assert result.exit_code != 0
    assert "Supported actions" in result.output
    assert "status" in result.output
    assert "add" in result.output
