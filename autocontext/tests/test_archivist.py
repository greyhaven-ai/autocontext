"""Tests for ArchivistRunner output parser and spot-pull."""
from __future__ import annotations

from pathlib import Path

from autocontext.agents.archivist import parse_archivist_output, spot_pull_sections


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_archivist_no_decisions() -> None:
    content = (
        "<!-- SYNTHESIS_START -->\n"
        "No issues found.\n"
        "<!-- SYNTHESIS_END -->\n"
        "\n"
        "<!-- DECISIONS_START -->\n"
        "<!-- DECISIONS_END -->\n"
    )
    out = parse_archivist_output(content)
    assert out.synthesis == "No issues found."
    assert out.decisions == []
    assert out.parse_success is True


def test_parse_archivist_with_decisions() -> None:
    content = (
        "<!-- SYNTHESIS_START -->\n"
        "One concern identified.\n"
        "<!-- SYNTHESIS_END -->\n"
        "\n"
        "<!-- DECISIONS_START -->\n"
        "## Decision: [source: librarian_clean_arch] [verdict: soft_flag]\n"
        "**Book:** Clean Architecture\n"
        "**Reasoning:** The principle applies here.\n"
        "**Passage:** \"Depend on abstractions, not concretions.\"\n"
        "<!-- DECISIONS_END -->\n"
    )
    out = parse_archivist_output(content)
    assert len(out.decisions) == 1
    assert out.decisions[0].verdict == "soft_flag"
    assert out.decisions[0].flag_source == "librarian_clean_arch"
    assert out.decisions[0].book_name == "Clean Architecture"
    assert "abstractions" in out.decisions[0].cited_passage


def test_parse_archivist_multiple_decisions() -> None:
    content = (
        "<!-- SYNTHESIS_START -->\nMixed.\n<!-- SYNTHESIS_END -->\n"
        "<!-- DECISIONS_START -->\n"
        "## Decision: [source: librarian_a] [verdict: dismissed]\n"
        "**Book:** Book A\n"
        "**Reasoning:** Not applicable.\n"
        "**Passage:** \"Quote A.\"\n"
        "\n"
        "## Decision: [source: librarian_b] [verdict: hard_gate]\n"
        "**Book:** Book B\n"
        "**Reasoning:** Critical violation.\n"
        "**Passage:** \"Quote B.\"\n"
        "<!-- DECISIONS_END -->\n"
    )
    out = parse_archivist_output(content)
    assert len(out.decisions) == 2
    assert out.decisions[0].verdict == "dismissed"
    assert out.decisions[1].verdict == "hard_gate"


def test_parse_archivist_malformed_fallback() -> None:
    content = "Unstructured archivist response."
    out = parse_archivist_output(content)
    assert out.parse_success is False
    assert out.decisions == []
    assert out.synthesis == content


# ---------------------------------------------------------------------------
# Spot-pull
# ---------------------------------------------------------------------------


def test_spot_pull_sections(tmp_path: Path) -> None:
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch03-s02-dependency-inversion.md").write_text(
        "---\ntitle: Dependency Inversion\n---\n\nDepend on abstractions.\n"
    )
    (chapters_dir / "ch05-s01-srp.md").write_text(
        "---\ntitle: SRP\n---\n\nSingle responsibility.\n"
    )

    result = spot_pull_sections(tmp_path, ["ch03-s02-dependency-inversion"])
    assert len(result) == 1
    assert "Depend on abstractions" in result["ch03-s02-dependency-inversion"]


def test_spot_pull_sections_missing(tmp_path: Path) -> None:
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()

    result = spot_pull_sections(tmp_path, ["ch99-nonexistent"])
    assert result == {}
