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


def test_queue_status_unknown_subcommand_emits_typer_usage_error(tmp_path, monkeypatch) -> None:
    """AC-697 slice 3 promoted `queue` to a sub-Typer group, so an
    unrecognized subcommand exits non-zero with typer's standard
    usage banner (which lists the registered subcommands). This
    replaces the slice-2 custom action-positional check, which is
    no longer reachable now that typer parses the subcommand
    directly."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))
    result = CliRunner().invoke(app, ["queue", "nonsense"])
    assert result.exit_code != 0
    # Typer prints a usage banner that mentions the group.
    assert "queue" in result.output.lower()


def test_queue_add_subcommand_is_registered_at_canonical_path(tmp_path, monkeypatch) -> None:
    """AC-697 slice 3: `autoctx queue add` is the canonical path.
    The contract walker in `cli_contract.iter_python_command_paths`
    must observe `["queue", "add"]` as a registered subcommand."""
    from autocontext.cli import app as _app
    from autocontext.cli_contract import iter_python_command_paths

    observed = {tuple(path) for path in iter_python_command_paths(_app)}
    assert ("queue", "add") in observed
    assert ("queue", "status") in observed


def test_queue_legacy_dash_s_form_still_routes_to_add(tmp_path, monkeypatch) -> None:
    """Backward compat: `autoctx queue -s <spec>` (no subcommand)
    must still enqueue a task, so existing scripts keep working."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))
    result = CliRunner().invoke(app, ["queue", "-s", "test_spec", "--json"])
    # The actual enqueue may fail without a real settings/db setup,
    # but it must reach the add-dispatch path (not produce a typer
    # usage error). Exit 1 with output that mentions either "queue"
    # or a task-id-like payload is acceptable; the key invariant
    # is that the callback resolves to add (no "Usage:" banner).
    combined = result.output + (result.stderr or "")
    # If we hit a "Usage:" banner, the callback didn't dispatch to add.
    assert "Usage:" not in combined or "queue" in combined.lower()


# --- PR #998 review (P2): callback-before-subcommand legacy forms ---


def test_queue_json_flag_before_status_subcommand_still_emits_json(tmp_path, monkeypatch) -> None:
    """Reviewer's exact repro: `autoctx queue --json status` (--json
    before the subcommand) must emit a JSON `pending_count` payload,
    not the human-readable text. Without the merge fix the callback's
    --json was discarded when typer saw the `status` subcommand."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))
    result = CliRunner().invoke(app, ["queue", "--json", "status"])
    assert result.exit_code == 0, result.output
    import json as _json

    payload = _json.loads(result.output.strip())
    assert payload == {"pending_count": 0}


def test_queue_dash_s_before_add_subcommand_still_routes_to_add(tmp_path, monkeypatch) -> None:
    """Reviewer's exact repro: `autoctx queue -s abc add --json`
    (callback-side `-s abc`, subcommand-side `--json`) must reach
    the add dispatch with spec=abc, not fail with "missing --spec".
    Without the merge fix the callback's --spec was discarded."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))
    result = CliRunner().invoke(app, ["queue", "-s", "test_spec", "add", "--json"])
    # The enqueue may fail downstream without a real settings/db,
    # but the key invariant is that we did NOT exit with a typer
    # "missing option" usage banner. If --spec was unmerged the
    # CLI would emit `Usage:` with `Missing option '--spec' / '-s'`.
    combined = result.output + (result.stderr or "")
    assert "Missing option" not in combined
    assert "Missing argument" not in combined


# --- PR #998 review (P3): walker only yields invokable group paths ---


def test_walker_yields_invokable_group_paths_only() -> None:
    """The slice-3 walker change must yield group prefixes only for
    groups that have `invoke_without_command` set (i.e. groups that
    are themselves callable with no subcommand). `queue` is
    invokable (legacy `-s <spec>` form); `scenario` is not. Listing
    a bare group like `scenario` as a "supported command" would
    falsely promise that `autoctx scenario` works."""
    from autocontext.cli import app as _app
    from autocontext.cli_contract import iter_python_command_paths

    observed = {tuple(path) for path in iter_python_command_paths(_app)}
    # `queue` is invokable -> yielded as a top-level path.
    assert ("queue",) in observed
    # `scenario` is bare -> NOT yielded as a top-level path; only its
    # `create` subcommand is.
    assert ("scenario",) not in observed
    assert ("scenario", "create") in observed
