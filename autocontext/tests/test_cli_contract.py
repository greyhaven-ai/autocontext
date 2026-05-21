"""AC-697 slice 1: shared CLI contract + Python parity tests.

The contract at ``docs/cli-contract.json`` is the source of truth
for the canonical ``autoctx`` surface across the Python and
TypeScript packages. This test file pins:

* schema sanity (no duplicate command ids; every alias resolves to
  a canonical id; runtime-support enums valid),
* Python parity: every command marked
  ``runtime_support.python == "yes"`` exists as a Typer command in
  the shipped Python CLI,
* friction documentation: every command marked
  ``runtime_support.python == "intentional_gap"`` carries a
  non-empty ``reason`` so reviewers can tell why the gap is on
  purpose (matches the AC-697 follow-up plan rather than silently
  dropping a command).

The TypeScript parity tests live in
``ts/tests/cli-contract.test.ts``; both runtimes load the same
JSON so a single edit to the contract drives both sides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.cli_contract import (
    PAVED_ROAD,
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


# --- Schema sanity ---------------------------------------------------------


def test_contract_file_exists() -> None:
    assert _contract_path().is_file()


def test_contract_has_at_least_paved_road_commands(contract: Contract) -> None:
    """Per AC-697, the paved-road surface is ``solve``, ``run``,
    ``status``, ``watch``, ``show``, ``export``. Every one of those
    must be in the contract for slice 1 to claim coverage."""
    expected = {"solve", "run", "status", "watch", "show", "export"}
    by_id = {cmd.id for cmd in contract.commands}
    missing = expected - by_id
    assert not missing, f"contract missing paved-road command ids: {sorted(missing)}"


def test_no_duplicate_command_ids(contract: Contract) -> None:
    ids = [cmd.id for cmd in contract.commands]
    assert len(ids) == len(set(ids)), "duplicate command ids in contract"


def test_every_alias_resolves_to_a_canonical_id(contract: Contract) -> None:
    canonical_ids = {cmd.id for cmd in contract.commands}
    for cmd in contract.commands:
        for alias in cmd.aliases:
            # An alias entry is a legacy *path*, not a canonical id;
            # the resolution rule is that the alias maps to *this*
            # command's id. We pin the entry uniqueness here.
            assert alias not in canonical_ids, f"alias {alias!r} on {cmd.id!r} collides with a canonical command id"


def test_alias_paths_are_unique_across_commands(contract: Contract) -> None:
    """An alias path must not point at two different canonical
    commands. Catches the case where a copy-paste edit silently
    re-points a legacy alias."""
    seen: dict[str, str] = {}
    for cmd in contract.commands:
        for alias in cmd.aliases:
            assert alias not in seen, f"alias {alias!r} listed under both {seen[alias]!r} and {cmd.id!r}"
            seen[alias] = cmd.id


def test_runtime_support_uses_known_status_values(contract: Contract) -> None:
    allowed = {RuntimeStatus.YES, RuntimeStatus.MISSING, RuntimeStatus.INTENTIONAL_GAP}
    for cmd in contract.commands:
        assert cmd.runtime_support.python.status in allowed
        assert cmd.runtime_support.typescript.status in allowed


def test_intentional_gaps_carry_a_reason(contract: Contract) -> None:
    """Every ``intentional_gap`` entry must explain why so reviewers
    can tell apart "decided not to ship" from "forgot to implement"."""
    for cmd in contract.commands:
        for runtime, support in (
            ("python", cmd.runtime_support.python),
            ("typescript", cmd.runtime_support.typescript),
        ):
            if support.status is RuntimeStatus.INTENTIONAL_GAP:
                assert support.reason, f"{cmd.id}.{runtime} marked intentional_gap without reason"


def test_audience_uses_known_tiers(contract: Contract) -> None:
    allowed = {"paved_road", "advanced", "internal"}
    for cmd in contract.commands:
        assert cmd.audience in allowed, f"unknown audience {cmd.audience!r} on {cmd.id!r}"


def test_domain_concept_is_known_or_none(contract: Contract) -> None:
    """``Solve`` is an operation, not a domain noun (per the
    ticket). The contract may set domain_concept to null."""
    allowed = {"Scenario", "Task", "Mission", "Run", "Artifact", "Knowledge", None}
    for cmd in contract.commands:
        assert cmd.domain_concept in allowed, f"unknown domain concept {cmd.domain_concept!r} on {cmd.id!r}"


def test_paved_road_constant_matches_audience_filter(contract: Contract) -> None:
    """The :data:`PAVED_ROAD` constant must be the same set as the
    commands tagged ``audience == "paved_road"`` so callers can
    rely on either."""
    from_tag = {cmd.id for cmd in contract.commands if cmd.audience == "paved_road"}
    assert PAVED_ROAD == from_tag


# --- Python parity ---------------------------------------------------------


def test_every_python_supported_command_exists_in_typer(contract: Contract) -> None:
    """Every command claiming ``runtime_support.python == "yes"`` must
    actually be a registered Typer command (top-level or nested
    subcommand) on the shipped Python CLI app. If not, the contract
    is lying about what the Python package supports."""
    from autocontext.cli import app

    observed = iter_python_command_paths(app)
    observed_paths = {tuple(path) for path in observed}
    for cmd in contract.commands:
        if cmd.runtime_support.python.status is RuntimeStatus.YES:
            assert tuple(cmd.path) in observed_paths, (
                f"contract claims Python support for {cmd.id!r} at {cmd.path} but no matching Typer command was found"
            )


def test_aliases_are_paths_not_command_ids(contract: Contract) -> None:
    """Aliases are *paths* (e.g. ``["new-scenario"]``) — they exist
    so legacy invocations keep working. Catching a malformed alias
    early is cheap."""
    for cmd in contract.commands:
        for alias in cmd.aliases:
            assert isinstance(alias, str)
            assert alias, "empty alias"
            assert " " not in alias, f"alias {alias!r} contains whitespace"


# --- AC-697 friction-point invariants -------------------------------------


def test_status_canonical_meaning_is_run_status(contract: Contract) -> None:
    """AC-697 acceptance: ``autoctx status <run-id>`` must mean run
    status in both runtimes. The contract pins the canonical
    semantics — actual runtime parity (e.g. fixing the TypeScript
    side that today reports queue pending) is a follow-up slice."""
    cmd = next(c for c in contract.commands if c.id == "status")
    assert cmd.domain_concept == "Run"
    assert "run" in cmd.summary.lower()


def test_solve_is_not_a_domain_noun(contract: Contract) -> None:
    """Per the ticket's Domain Model section, ``solve`` is an
    operation, not a peer noun next to Scenario / Task / Mission.
    Pin that distinction in the contract."""
    cmd = next(c for c in contract.commands if c.id == "solve")
    assert cmd.domain_concept != "Mission"
    assert cmd.domain_concept != "Scenario"


def test_iterations_is_the_canonical_iteration_flag(contract: Contract) -> None:
    """``--iterations`` is the canonical name; ``--gens``,
    ``--rounds``, ``--max-iterations`` are aliases. Pin on a
    paved-road command that's known to take iteration controls."""
    cmd = next(c for c in contract.commands if c.id == "solve")
    iter_flag = next(
        (f for f in cmd.flags if f.name == "iterations"),
        None,
    )
    assert iter_flag is not None, "solve should expose canonical --iterations flag"
    # Legacy iteration-control names land as aliases on the same flag.
    assert "gens" in iter_flag.aliases or "rounds" in iter_flag.aliases


def test_queue_status_is_not_top_level_status(contract: Contract) -> None:
    """Per AC-697, queue status must live under its own path (e.g.
    ``task queue status`` or ``queue status``), never at the
    top-level ``status`` semantic. Contract pins this."""
    cmd = next(c for c in contract.commands if c.id == "status")
    # Top-level status is run-only; queue status is its own canonical
    # command and lives elsewhere in the contract.
    assert cmd.domain_concept == "Run"
    queue_status = next(
        (c for c in contract.commands if c.id == "queue.status"),
        None,
    )
    if queue_status is not None:
        assert queue_status.path != ["status"], "queue.status must not occupy the top-level `status` semantic"


def test_capabilities_command_documents_contract_link(contract: Contract) -> None:
    """``autoctx capabilities`` should advertise the canonical
    surface (per the ticket's documentation step). The contract
    entry pins that capabilities exists as paved-road or advanced
    audience — not internal — so it's discoverable."""
    cmd = next((c for c in contract.commands if c.id == "capabilities"), None)
    if cmd is not None:
        assert cmd.audience in {"paved_road", "advanced"}
