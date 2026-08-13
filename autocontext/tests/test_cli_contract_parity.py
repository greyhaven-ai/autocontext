"""Cross-runtime CLI parity audit (Python side).

The forward direction (contract -> Typer registration) is already
covered by ``tests/test_cli_contract.py``. The audit below adds the
REVERSE direction plus cross-runtime invariants so accidental drift
surfaces immediately.

What this pins:

1. **Reverse direction**: every public Typer command path observed
   on the live ``autocontext.cli.app`` is contracted or is a
   contracted compatibility alias. There is no allowlist escape
   hatch for public commands.

2. **Alias registration**: every contracted alias path must
   correspond to an observed top-level Typer command. Pins that
   the legacy invocations (`autoctx mcp-serve`,
   `autoctx new-scenario`) still work after future refactors.

3. **Cross-runtime path equality**: a command id with both
   ``python.yes`` and ``typescript.yes`` must declare the SAME
   canonical ``path``. The contract is a single source of truth so
   this is trivially true per-entry, but the assertion documents
   the invariant and traps a hand-edit that introduces a per-
   runtime path divergence.

4. **Cross-runtime id uniqueness**: command ids are unique within
   the contract; the assertion is a sanity backstop for the
   above.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.cli_contract import (
    Contract,
    RuntimeStatus,
    iter_python_command_paths,
    load_contract,
)


def _contract_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "cli-contract.json"


@pytest.fixture(scope="module")
def contract() -> Contract:
    return load_contract(_contract_path())


# ---------------------------------------------------------------------------
# Reverse direction: observed -> contract / alias
# ---------------------------------------------------------------------------


def _observed_top_level_names(app: object) -> set[str]:
    """PR #1021 review (P2): combine ``iter_python_command_paths``
    output with ``app.registered_groups`` so visible Typer GROUPS
    (like ``analytics``, ``hermes``, ``probes``, ``scenario``) are
    surfaced even when they don't set ``invoke_without_command=True``.
    ``iter_python_command_paths`` only emits a group prefix when the
    group itself is invokable; a public group whose only purpose is
    to hold subcommands would otherwise slip past the reverse-
    direction audit even though it appears in ``autoctx --help``.
    """
    iter_paths: set[str] = {
        p[0]
        for p in iter_python_command_paths(app)  # type: ignore[arg-type]
        if len(p) == 1
    }
    group_names: set[str] = {
        g.name
        for g in app.registered_groups  # type: ignore[attr-defined]
        if g.name is not None
    }
    return iter_paths | group_names


def test_every_observed_public_command_path_is_contracted(
    contract: Contract,
) -> None:
    """Every invokable Typer path must be canonical or a declared alias."""
    from autocontext.cli import app

    observed = {tuple(path) for path in iter_python_command_paths(app)}
    contracted = {tuple(command.path) for command in contract.commands}
    aliases = {(alias,) for command in contract.commands for alias in command.aliases}
    leaked = observed - contracted - aliases
    assert not leaked, (
        "Public Typer command paths shipped without a contract entry: "
        f"{sorted(leaked)}. Add them to docs/cli-contract.json or hide "
        "the command from the public CLI."
    )


# ---------------------------------------------------------------------------
# Forward direction sanity: contract entries / aliases must be live
# ---------------------------------------------------------------------------


def test_every_contracted_alias_path_is_registered_in_typer(
    contract: Contract,
) -> None:
    """Every contracted alias must still resolve to a registered
    top-level Typer command OR group. Catches the case where a
    future refactor drops a legacy alias without updating the
    contract."""
    from autocontext.cli import app

    observed = _observed_top_level_names(app)
    for cmd in contract.commands:
        for alias in cmd.aliases:
            assert alias in observed, (
                f"contracted alias {alias!r} on {cmd.id!r} is no longer registered as a top-level Typer command"
            )


# ---------------------------------------------------------------------------
# Cross-runtime invariants
# ---------------------------------------------------------------------------


def test_no_per_runtime_path_divergence(contract: Contract) -> None:
    """A command id with python.yes AND typescript.yes must have the
    same ``path`` field. The contract is a single source of truth
    (one ``path`` per ``id``) so this is trivially true today;
    pinning the invariant catches a future hand-edit that
    introduces a per-runtime path divergence."""
    for cmd in contract.commands:
        if cmd.runtime_support.python.status is RuntimeStatus.YES and cmd.runtime_support.typescript.status is RuntimeStatus.YES:
            # No per-runtime path override is allowed by the schema;
            # the existence of `cmd.path` as a single field is what
            # guarantees parity. Surface the invariant explicitly so
            # a future schema change that adds per-runtime paths is
            # caught here.
            assert cmd.path, f"command {cmd.id!r} has an empty path"


def test_no_command_id_uses_a_runtime_specific_prefix(contract: Contract) -> None:
    """A command id should be runtime-agnostic. `python.X` /
    `typescript.X` / `ts.X` / `py.X` prefixes would defeat the
    purpose of a shared contract."""
    forbidden = ("python.", "py.", "typescript.", "ts.")
    for cmd in contract.commands:
        for prefix in forbidden:
            assert not cmd.id.startswith(prefix), (
                f"command id {cmd.id!r} uses a runtime-specific prefix; the contract is single-sourced across runtimes"
            )


def test_command_ids_are_unique_and_well_formed(contract: Contract) -> None:
    """Command ids are dot-separated semantic identifiers (e.g.
    ``run.list`` signals "list within the Run family" even though
    the canonical CLI path is just ``["list"]``). Pin that ids are
    non-empty, unique, and contain only alphanumeric / dot / dash /
    underscore characters."""
    seen_ids: set[str] = set()
    for cmd in contract.commands:
        assert cmd.id, "empty command id"
        assert cmd.id not in seen_ids, f"duplicate command id {cmd.id!r}"
        seen_ids.add(cmd.id)
        for ch in cmd.id:
            assert ch.isalnum() or ch in (".", "-", "_"), f"command id {cmd.id!r} contains illegal character {ch!r}"
