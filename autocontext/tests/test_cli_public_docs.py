from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.cli_contract import iter_python_command_paths, load_contract

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DOCS = (
    "README.md",
    "docs/README.md",
    "autocontext/README.md",
    "ts/README.md",
    "examples/README.md",
    "autocontext/docs/agent-integration.md",
    "CONTRIBUTING.md",
)
CANONICAL_PATHS = ("scenario create", "serve mcp")
IMPLEMENTATION_HISTORY = re.compile(
    r"\bAC-\d+\b|\bPR\s*#?\d+\b|\bslice(?:s|[- ]\d+[a-z]?)?\b|\binternal[- ]layer\b",
    re.IGNORECASE,
)


def test_public_docs_teach_canonical_nested_paths_from_the_contract() -> None:
    contract = load_contract(REPO_ROOT / "docs" / "cli-contract.json")
    contracted_paths = {" ".join(command.path) for command in contract.commands}

    for command_path in CANONICAL_PATHS:
        assert command_path in contracted_paths
    for relative_path in PUBLIC_DOCS:
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        for command_path in CANONICAL_PATHS:
            assert f"autoctx {command_path}" in text, f"{relative_path} omits {command_path}"


def test_legacy_paths_are_confined_to_the_compatibility_section() -> None:
    guide = (REPO_ROOT / "autocontext" / "docs" / "agent-integration.md").read_text(encoding="utf-8")
    heading = "### Discovery, Defaults, and Compatibility"
    compatibility = guide.split(heading, 1)[1].split("\n### ", 1)[0]

    assert "new-scenario" in compatibility
    assert "mcp-serve" in compatibility
    for relative_path in PUBLIC_DOCS:
        text = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        if relative_path == "autocontext/docs/agent-integration.md":
            text = text.replace(compatibility, "")
        assert "new-scenario" not in text
        assert "mcp-serve" not in text


def test_every_python_command_help_omits_implementation_history() -> None:
    runner = CliRunner()
    for path in iter_python_command_paths(app):
        result = runner.invoke(app, [*path, "--help"])
        output = f"{result.stdout}\n{result.stderr}"

        assert result.exit_code == 0, f"{' '.join(path)}: {output}"
        assert IMPLEMENTATION_HISTORY.search(output) is None, f"{' '.join(path)}: {output}"


def test_contract_summaries_omit_implementation_history() -> None:
    contract = load_contract(REPO_ROOT / "docs" / "cli-contract.json")

    for command in contract.commands:
        assert IMPLEMENTATION_HISTORY.search(command.summary) is None, f"{command.id}: {command.summary}"
