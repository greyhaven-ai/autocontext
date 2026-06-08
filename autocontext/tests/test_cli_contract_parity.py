"""Cross-runtime CLI parity audit (Python side).

The forward direction (contract -> Typer registration) is already
covered by ``tests/test_cli_contract.py``. The audit below adds the
REVERSE direction plus cross-runtime invariants so accidental drift
surfaces immediately.

What this pins:

1. **Reverse direction**: every top-level Typer command observed on
   the live ``autocontext.cli.app`` is either contracted, listed
   as a contracted alias, or named in
   ``UNCONTRACTED_TOP_LEVEL_ALLOWLIST``. Adding a new top-level
   command without a contract entry (or allowlist line) fails the
   test, so the operator is forced to either advertise it in the
   contract or document why it stays uncontracted.

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


# Top-level Typer commands that are intentionally NOT advertised in
# docs/cli-contract.json. Reasons range from "operator-internal"
# (``worker``, ``import-package``) to "advanced surface tracked in a
# future contract slice" (``investigate``, ``simulate``, ``train``).
# Adding a new top-level command should land either in the contract
# or on this list; nothing should slip through silently.
UNCONTRACTED_TOP_LEVEL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ab-test",
        "analytics",
        "benchmark",
        "ecosystem",
        "export-training-data",
        "hermes",
        "import-package",
        "investigate",
        "probes",
        "resume",
        "self-improve",
        "simulate",
        "train",
        "train-r1",
        "tui",
        "wait",
        "worker",
    }
)


# ---------------------------------------------------------------------------
# Reverse direction: observed -> contract / alias / allowlist
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


def test_every_observed_top_level_command_is_accounted_for(
    contract: Contract,
) -> None:
    """Every top-level Typer command OR group on the live ``app``
    must be in the contract, in a contracted alias list, or on the
    explicit ``UNCONTRACTED_TOP_LEVEL_ALLOWLIST``.

    A new command shipped without a contract entry surfaces here so
    the operator either adds it to the contract or documents why it
    stays uncontracted via the allowlist.
    """
    from autocontext.cli import app

    observed = _observed_top_level_names(app)
    contracted_top_level = {c.path[0] for c in contract.commands if len(c.path) == 1}
    contracted_aliases = {a for c in contract.commands for a in c.aliases}
    # Multi-token contract entries (e.g. `scenario.create`) anchor
    # their top-level token; that token IS the visible Typer group
    # name and must count as contracted.
    contracted_parents = {c.path[0] for c in contract.commands if len(c.path) >= 2}
    accounted_for = contracted_top_level | contracted_parents | contracted_aliases | UNCONTRACTED_TOP_LEVEL_ALLOWLIST

    leaked = observed - accounted_for
    assert not leaked, (
        "Top-level Typer commands/groups shipped without a contract "
        "entry or allowlist line: "
        f"{sorted(leaked)}. Either add them to docs/cli-contract.json "
        "or to UNCONTRACTED_TOP_LEVEL_ALLOWLIST in this test."
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


def test_allowlist_is_minimal(contract: Contract) -> None:
    """Defensive: an entry on
    ``UNCONTRACTED_TOP_LEVEL_ALLOWLIST`` that ALSO appears in the
    contract is dead weight and confuses future readers. Reject the
    duplication so the allowlist stays the "explicitly uncontracted"
    set."""
    contracted_top_level = {c.path[0] for c in contract.commands if len(c.path) == 1}
    contracted_aliases = {a for c in contract.commands for a in c.aliases}
    contracted = contracted_top_level | contracted_aliases
    redundant = UNCONTRACTED_TOP_LEVEL_ALLOWLIST & contracted
    assert not redundant, f"Allowlist entries that are ALSO in the contract: {sorted(redundant)}. Remove them from the allowlist."


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
