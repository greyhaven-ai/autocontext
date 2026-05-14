"""AC-702: Hermes skill references for curator alignment + workflows.

Tests cover:

* the four reference names ship in the canonical order
  (`hermes-curator`, `cli-workflows`, `mcp-workflows`, `local-training`),
* each reference is non-empty and answers a concrete agent question,
* the rendered SKILL.md cross-links every reference,
* `autoctx hermes export-skill --with-references` writes all four
  files alongside SKILL.md and rejects overwrites without --force,
* the skill remains useful on its own (a complete SKILL.md is emitted
  even when --with-references is not passed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.hermes.references import list_references, render_reference
from autocontext.hermes.skill import render_autocontext_skill

_EXPECTED_REFERENCES = ("hermes-curator", "cli-workflows", "mcp-workflows", "local-training")


def test_list_references_returns_canonical_order() -> None:
    assert list_references() == _EXPECTED_REFERENCES


def test_each_reference_is_non_empty_and_has_h1_heading() -> None:
    for name in _EXPECTED_REFERENCES:
        body = render_reference(name)
        assert body.strip(), f"reference {name!r} is empty"
        assert body.startswith("# "), f"reference {name!r} missing H1 heading"


def test_unknown_reference_raises() -> None:
    with pytest.raises(KeyError, match="not-a-reference"):
        render_reference("not-a-reference")


def test_hermes_curator_reference_pins_read_only_rule() -> None:
    body = render_reference("hermes-curator")
    # The load-bearing rule: autocontext is read-only against ~/.hermes
    # until the trained-advisor path is proven.
    assert "read-only" in body.lower()
    assert "Curator" in body and "mutation owner" in body


def test_cli_workflows_reference_includes_concrete_commands() -> None:
    body = render_reference("cli-workflows")
    # Each main CLI workflow has at least one literal command block.
    for cmd in (
        "autoctx hermes inspect",
        "autoctx hermes export-skill",
        "autoctx hermes ingest-curator",
        "autoctx hermes export-dataset",
        "autoctx judge",
        "autoctx replay",
    ):
        assert cmd in body, f"cli-workflows reference missing {cmd!r}"


def test_mcp_workflows_reference_maps_cli_to_tool_names() -> None:
    body = render_reference("mcp-workflows")
    assert "autoctx mcp-serve" in body
    assert "autocontext_judge" in body
    assert "autocontext_improve" in body
    # Explicit guidance on CLI-vs-MCP preference.
    assert "When to prefer CLI" in body or "prefer CLI" in body.lower()


def test_local_training_reference_warns_on_small_datasets() -> None:
    body = render_reference("local-training")
    assert "narrow advisor" in body.lower()
    # Pinned by the AC-705 acceptance criteria: small personal Hermes
    # homes may not produce frontier-quality models.
    assert "small personal" in body.lower() or "small user datasets" in body.lower() or "small personal hermes" in body.lower()


def test_skill_markdown_cross_links_every_reference() -> None:
    skill = render_autocontext_skill()
    assert "## References" in skill
    for name in _EXPECTED_REFERENCES:
        assert f"references/{name}.md" in skill, f"SKILL.md does not cross-link references/{name}.md"


def test_export_skill_writes_references_when_flag_is_set(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "skill" / "SKILL.md"
    result = runner.invoke(
        app,
        [
            "hermes",
            "export-skill",
            "--output",
            str(output),
            "--with-references",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["references_dir"] == str(tmp_path / "skill" / "references")
    assert {ref["name"] for ref in payload["references"]} == set(_EXPECTED_REFERENCES)
    references_dir = Path(payload["references_dir"])
    for name in _EXPECTED_REFERENCES:
        ref_path = references_dir / f"{name}.md"
        assert ref_path.exists(), f"reference {name!r} not written"
        assert ref_path.read_text(encoding="utf-8").startswith("# ")


def test_export_skill_without_flag_omits_references_section_from_payload(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "skill" / "SKILL.md"
    result = runner.invoke(
        app,
        ["hermes", "export-skill", "--output", str(output), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "references" not in payload
    assert not (tmp_path / "skill" / "references").exists()


def test_export_skill_refuses_to_overwrite_references_without_force(tmp_path: Path) -> None:
    runner = CliRunner()
    output = tmp_path / "skill" / "SKILL.md"
    # First write succeeds.
    runner.invoke(app, ["hermes", "export-skill", "--output", str(output), "--with-references"])
    # Second write without --force fails on the first reference collision.
    result = runner.invoke(app, ["hermes", "export-skill", "--output", str(output), "--force", "--with-references"])
    # --force on SKILL.md must propagate to references too.
    assert result.exit_code == 0, result.output

    # Without --force, refuses even when SKILL.md doesn't exist but a reference does.
    output_b = tmp_path / "skill-b" / "SKILL.md"
    runner.invoke(app, ["hermes", "export-skill", "--output", str(output_b), "--with-references"])
    # Pre-existing reference at output_b should block re-run without --force.
    result = runner.invoke(app, ["hermes", "export-skill", "--output", str(output_b), "--with-references"])
    assert result.exit_code != 0
