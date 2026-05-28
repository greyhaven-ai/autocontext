"""AC-697 slice 1: shared CLI contract loader (Python side).

The source of truth at ``docs/cli-contract.json`` describes every
canonical ``autoctx`` command across the Python and TypeScript
packages. This module loads the JSON into typed value objects and
introspects the live Typer app so parity tests can verify that
``runtime_support.python == "yes"`` claims hold.

DDD layout:

* :class:`Contract` — the loaded source of truth (read-only).
* :class:`CommandSpec` — one canonical command.
* :class:`Flag` — one canonical flag (name + aliases + type).
* :class:`RuntimeSupportPair` / :class:`RuntimeSupport` —
  per-runtime support status with optional reason.

DRY: the same JSON drives the TypeScript parity tests via
``ts/src/cli/cli-contract.ts``. A single edit to the contract
moves both sides.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer


class RuntimeStatus(enum.StrEnum):
    """Per-runtime support status for a canonical command.

    ``yes`` — the runtime ships the canonical command at the
    canonical path. Parity tests will fail if it doesn't.

    ``missing`` — the runtime ships the underlying capability but
    not at the canonical path yet; an AC-697 follow-up slice
    moves it.

    ``intentional_gap`` — the runtime deliberately does not ship
    the command. Requires a non-empty ``reason``.
    """

    YES = "yes"
    MISSING = "missing"
    INTENTIONAL_GAP = "intentional_gap"


@dataclass(frozen=True, slots=True)
class RuntimeSupport:
    """One runtime's support entry for a canonical command."""

    status: RuntimeStatus
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeSupportPair:
    """Per-runtime support pair."""

    python: RuntimeSupport
    typescript: RuntimeSupport


@dataclass(frozen=True, slots=True)
class Flag:
    """A canonical flag (long form, plus optional legacy aliases)."""

    name: str
    type: str
    aliases: tuple[str, ...] = ()
    required: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """One canonical command in the contract."""

    id: str
    path: tuple[str, ...]
    summary: str
    audience: str
    maturity: str
    domain_concept: str | None
    aliases: tuple[str, ...]
    runtime_support: RuntimeSupportPair
    flags: tuple[Flag, ...] = ()
    output_contract: str = "text"


@dataclass(frozen=True, slots=True)
class Contract:
    """The loaded source of truth."""

    schema_version: int
    commands: tuple[CommandSpec, ...]

    def by_id(self, id_: str) -> CommandSpec | None:
        for cmd in self.commands:
            if cmd.id == id_:
                return cmd
        return None


def load_contract(path: Path) -> Contract:
    """Load ``docs/cli-contract.json`` into a :class:`Contract`."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    commands: list[CommandSpec] = []
    for entry in raw.get("commands", []):
        flags = tuple(
            Flag(
                name=f["name"],
                type=f.get("type", "string"),
                aliases=tuple(f.get("aliases", [])),
                required=bool(f.get("required", False)),
                description=f.get("description", ""),
            )
            for f in entry.get("flags", [])
        )
        support_raw = entry.get("runtime_support", {})
        runtime_support = RuntimeSupportPair(
            python=_load_runtime_support(support_raw.get("python", {})),
            typescript=_load_runtime_support(support_raw.get("typescript", {})),
        )
        commands.append(
            CommandSpec(
                id=entry["id"],
                path=tuple(entry["path"]),
                summary=entry["summary"],
                audience=entry["audience"],
                maturity=entry.get("maturity", "stable"),
                domain_concept=entry.get("domain_concept"),
                aliases=tuple(entry.get("aliases", [])),
                runtime_support=runtime_support,
                flags=flags,
                output_contract=entry.get("output_contract", "text"),
            )
        )
    return Contract(
        schema_version=int(raw.get("schema_version", 1)),
        commands=tuple(commands),
    )


def _load_runtime_support(blob: dict[str, Any]) -> RuntimeSupport:
    status_str = blob.get("status", "missing")
    return RuntimeSupport(
        status=RuntimeStatus(status_str),
        reason=blob.get("reason", ""),
    )


# Paved-road command ids — the surface every operator should see first.
# Kept here as a constant so contract drift fails the matching test.
PAVED_ROAD: frozenset[str] = frozenset({"solve", "run", "status", "watch", "show", "export"})


def iter_python_command_paths(app: typer.Typer) -> Iterable[list[str]]:
    """Walk the Typer app and yield every registered command path.

    A ``CommandInfo`` with ``name == ""`` collapses to the parent
    path (Typer's no-name default). Sub-Typer groups recurse.
    """
    yield from _walk_typer(app, prefix=[])


def _walk_typer(app: typer.Typer, *, prefix: list[str]) -> Iterable[list[str]]:
    for cmd in app.registered_commands:
        name = cmd.name or (cmd.callback.__name__ if cmd.callback else "")
        if not name:
            continue
        yield [*prefix, name]
    for group in app.registered_groups:
        group_name = group.name or ""
        if group_name and group.typer_instance is not None:
            # AC-697 slice 3 review (P3): yield the group prefix only
            # when the group is itself invokable (`invoke_without_command`
            # is truthy on its TyperInfo). Bare groups without a
            # no-subcommand callback exit with `Missing command.` when
            # invoked at their top-level path, so listing them as
            # supported commands would weaken the parity guard for
            # future contract entries that legitimately pin bare-group
            # paths. typer's default sentinel for an unset value is a
            # `DefaultPlaceholder`, so check for explicit truthiness.
            invoke_without_command = group.typer_instance.info.invoke_without_command
            if invoke_without_command is True:
                yield [*prefix, group_name]
            yield from _walk_typer(group.typer_instance, prefix=[*prefix, group_name])


__all__ = [
    "PAVED_ROAD",
    "CommandSpec",
    "Contract",
    "Flag",
    "RuntimeStatus",
    "RuntimeSupport",
    "RuntimeSupportPair",
    "iter_python_command_paths",
    "load_contract",
]
