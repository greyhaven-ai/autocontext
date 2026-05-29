"""AC-697 slice 6: `autoctx serve mcp` canonical path (Python side).

Slice 1 (PR #981) pinned the canonical contract: `serve mcp` is the
canonical path for the MCP server; `mcp-serve` stays as a top-level
alias for backward compatibility with existing Claude Code MCP
configs.

This slice promotes Python `serve` from a single typer command to a
sub-Typer group with three entry points:

- `autoctx serve [--host ...] [--port ...]` (legacy form, via
  `invoke_without_command` callback) -> HTTP API.
- `autoctx serve http [--host ...] [--port ...]` (canonical
  subcommand) -> HTTP API.
- `autoctx serve mcp` (canonical subcommand) -> MCP server.

`mcp-serve` stays registered as the top-level alias so existing
Claude Code configurations pointing at it continue to work.
"""

from __future__ import annotations

from autocontext.cli import app
from autocontext.cli_contract import iter_python_command_paths


def test_serve_subcommands_are_registered_at_canonical_paths() -> None:
    """`serve`, `serve http`, `serve mcp`, and the legacy `mcp-serve`
    alias must all appear in the contract walker's observed paths.
    The slice-1 contract pins `serve.http` at `["serve"]` and
    `serve.mcp` at `["serve", "mcp"]`."""
    observed = {tuple(path) for path in iter_python_command_paths(app)}
    # Canonical paths.
    assert ("serve",) in observed
    assert ("serve", "http") in observed
    assert ("serve", "mcp") in observed
    # Legacy alias kept for backward compat.
    assert ("mcp-serve",) in observed


def test_serve_typer_app_is_invokable_without_subcommand() -> None:
    """The `serve` group has `invoke_without_command=True` so the
    legacy form `autoctx serve [--host ...] [--port ...]` continues
    to start the HTTP API. The slice-3 walker change yields the
    group prefix only when this flag is truthy."""
    # Find the serve group on the registered_groups list.
    serve_group = next((g for g in app.registered_groups if g.name == "serve"), None)
    assert serve_group is not None
    assert serve_group.typer_instance is not None
    assert serve_group.typer_instance.info.invoke_without_command is True


def test_serve_callback_options_merge_into_http_subcommand(monkeypatch) -> None:
    """PR #1001 review (P2): the bare callback used to discard its
    parsed --host/--port options whenever typer saw a subcommand,
    breaking `autoctx serve --host 0.0.0.0 --port 9001 http`.
    The merge fix stashes callback values on ctx.obj and the http
    subcommand uses them when its own values are at default."""
    captured: dict[str, object] = {}

    def _fake_run_http_serve(host: str, port: int) -> None:
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("autocontext.cli._run_http_serve", _fake_run_http_serve)

    from typer.testing import CliRunner

    runner = CliRunner()
    # Callback-side options (before subcommand) must reach http.
    captured.clear()
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9001", "http"])
    assert result.exit_code == 0, result.output
    assert captured == {"host": "0.0.0.0", "port": 9001}, captured

    # Subcommand-side options (after subcommand) override callback.
    captured.clear()
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "9001", "http", "--port", "9002"])
    assert result.exit_code == 0, result.output
    assert captured == {"host": "0.0.0.0", "port": 9002}, captured


def test_contract_serve_mcp_is_yes_on_both_runtimes() -> None:
    """The slice-1 `serve.mcp` entry flipped from `intentional_gap`
    to `yes` on both Python and TypeScript with this slice."""
    import json
    from pathlib import Path

    contract = json.loads((Path(__file__).resolve().parents[2] / "docs" / "cli-contract.json").read_text(encoding="utf-8"))
    serve_mcp = next(c for c in contract["commands"] if c["id"] == "serve.mcp")
    assert serve_mcp["runtime_support"]["python"]["status"] == "yes"
    assert serve_mcp["runtime_support"]["typescript"]["status"] == "yes"
    # The alias the slice-1 contract pinned must still be present.
    assert "mcp-serve" in serve_mcp["aliases"]
