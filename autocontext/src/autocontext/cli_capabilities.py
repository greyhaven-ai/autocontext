"""AC-697 slice 5: `autoctx capabilities` command (Python side).

Loads the canonical contract at ``docs/cli-contract.json`` and emits
a structured JSON payload advertising the canonical command surface,
their aliases, and per-runtime support. Mirrors the TypeScript
:func:`buildCapabilitiesPayload` so both runtimes return the same
shape against the same JSON source of truth.

Flips the slice-1 contract's Python ``capabilities`` entry from
``intentional_gap`` to ``yes``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from autocontext.cli_contract import Contract, load_contract

if TYPE_CHECKING:
    from rich.console import Console


def _default_contract_path() -> Path:
    """Locate ``cli-contract.json`` for the running install context.

    PR #1000 review (P2): the wheel-installed package can't reach the
    repo's ``docs/`` directory because it lives outside the source
    tree. Hatch's ``force-include`` (see ``pyproject.toml``) packages
    ``docs/cli-contract.json`` as ``autocontext/cli_contract.json``
    inside the wheel, so the packaged copy is reachable via
    :mod:`importlib.resources`. The dev tree (editable installs,
    direct execution from ``src/``) still resolves via the
    repo-relative walk so contract edits land in the next invocation
    without rebuilding the wheel.
    """
    # Try the packaged copy first (wheel installs).
    try:
        from importlib.resources import files

        packaged = files("autocontext").joinpath("cli_contract.json")
        if packaged.is_file():
            return Path(str(packaged))
    except Exception:
        # importlib.resources isn't available or the package isn't
        # installed; fall through to the dev-tree fallback.
        pass
    # Fall back to the repo-relative path for the source tree.
    here = Path(__file__).resolve()
    # cli_capabilities.py -> autocontext -> src -> autocontext (pkg root) -> repo root
    repo_root = here.parents[3]
    return repo_root / "docs" / "cli-contract.json"


def _contract_command_to_payload(cmd: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": cmd.id,
        "path": list(cmd.path),
        "summary": cmd.summary,
        "audience": cmd.audience,
        "maturity": cmd.maturity,
        "aliases": list(cmd.aliases),
        "runtime_support": {
            "python": {"status": cmd.runtime_support.python.status.value},
            "typescript": {"status": cmd.runtime_support.typescript.status.value},
        },
    }
    if cmd.runtime_support.python.reason:
        payload["runtime_support"]["python"]["reason"] = cmd.runtime_support.python.reason
    if cmd.runtime_support.typescript.reason:
        payload["runtime_support"]["typescript"]["reason"] = cmd.runtime_support.typescript.reason
    return payload


def build_capabilities_payload(contract_path: Path | None = None) -> dict[str, Any]:
    """Build the JSON capabilities payload from the contract.

    The shape mirrors the TS :func:`buildCapabilitiesPayload`'s
    ``contract`` field; the Python CLI emits this as the top-level
    payload since Python does not (yet) have the legacy commands /
    features fields the TS payload carries.
    """
    path = contract_path or _default_contract_path()
    contract: Contract = load_contract(path)
    return {
        "schema_version": contract.schema_version,
        "commands": [_contract_command_to_payload(cmd) for cmd in contract.commands],
    }


def register_capabilities_command(
    app: typer.Typer,
    *,
    console: Console,
    contract_path_override: Path | None = None,
) -> None:
    """Mount ``autoctx capabilities`` on ``app``.

    Reads the contract at module-load time of each invocation (not
    once at import) so a follow-up contract edit lands in the next
    invocation without restarting the process.
    """

    @app.command()
    def capabilities(
        json_output: bool = typer.Option(False, "--json", help="Output structured JSON"),
    ) -> None:
        """Show canonical commands, aliases, and per-runtime support."""
        payload = build_capabilities_payload(contract_path_override)
        if json_output:
            # Plain stdout (no rich coloring) so JSON consumers can
            # parse the output without stripping ANSI escapes.
            print(json.dumps(payload, indent=2))
            return
        # Human-readable summary: list canonical commands + their support.
        console.print(f"autoctx CLI contract (schema_version={payload['schema_version']}):\n")
        for cmd in payload["commands"]:
            path = ".".join(cmd["path"])
            py = cmd["runtime_support"]["python"]["status"]
            ts = cmd["runtime_support"]["typescript"]["status"]
            console.print(f"  {path:<24} py={py:<16} ts={ts}")
        console.print("\nRun `autoctx capabilities --json` for the full structured payload.")


__all__ = [
    "build_capabilities_payload",
    "register_capabilities_command",
]
