"""Tests for AC-354: Return rendered SKILL.md from export surfaces.

Verifies that ``export_skill`` returns both the structured dict and
the rendered skill markdown, and that the response shape is backward
compatible.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from autocontext.knowledge.export import SkillPackage

# ---------------------------------------------------------------------------
# SkillPackage.to_skill_markdown smoke
# ---------------------------------------------------------------------------

class TestSkillMarkdownRendering:
    def test_renders_game_scenario_markdown(self) -> None:
        """Game scenario skill packages should render markdown with frontmatter."""
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid CTF",
            description="Capture the flag on a 20x20 grid.",
            playbook="# Strategy\n\nBe aggressive but keep a defender.",
            lessons=["High aggression without defense drops win rate.", "Path bias above 0.5 stabilizes."],
            best_strategy={"aggression": 0.65, "defense": 0.50, "path_bias": 0.55},
            best_score=0.85,
            best_elo=1523.4,
            hints="Try flanking maneuvers.",
        )
        md = pkg.to_skill_markdown()
        assert "---" in md  # frontmatter
        assert "grid-ctf-knowledge" in md  # name in frontmatter
        assert "Grid CTF" in md
        assert "Operational Lessons" in md
        assert "High aggression" in md
        assert "0.8500" in md or "0.85" in md
        assert "Playbook" in md

    def test_renders_agent_task_markdown(self) -> None:
        """Agent task skill packages should render with task prompt and rubric."""
        pkg = SkillPackage(
            scenario_name="summarization",
            display_name="Summarization",
            description="Summarize technical documents.",
            playbook="",
            lessons=["Be concise."],
            best_strategy=None,
            best_score=0.90,
            best_elo=1000.0,
            hints="",
            task_prompt="Summarize the following document.",
            judge_rubric="Evaluate completeness and accuracy.",
        )
        md = pkg.to_skill_markdown()
        assert "---" in md
        assert "Summarize the following document" in md
        assert "Evaluate completeness" in md


# ---------------------------------------------------------------------------
# tools.export_skill returns skill_markdown
# ---------------------------------------------------------------------------

class TestExportSkillIncludesMarkdown:
    def test_export_skill_returns_skill_markdown_key(self) -> None:
        """The export_skill tool function should include a 'skill_markdown' key."""
        from autocontext.mcp.tools import MtsToolContext, export_skill

        mock_pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid CTF",
            description="CTF game.",
            playbook="# Playbook",
            lessons=["lesson 1"],
            best_strategy={"aggression": 0.6},
            best_score=0.80,
            best_elo=1200.0,
            hints="hint",
        )

        ctx = MagicMock(spec=MtsToolContext)
        with patch("autocontext.knowledge.export.export_skill_package", return_value=mock_pkg):
            result = export_skill(ctx, "grid_ctf")

        assert "skill_markdown" in result
        assert isinstance(result["skill_markdown"], str)
        assert "Grid CTF" in result["skill_markdown"]
        assert "---" in result["skill_markdown"]

    def test_export_skill_backward_compatible(self) -> None:
        """Existing dict keys should still be present (backward compatibility)."""
        from autocontext.mcp.tools import MtsToolContext, export_skill

        mock_pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid CTF",
            description="CTF game.",
            playbook="# Playbook",
            lessons=["lesson 1"],
            best_strategy={"aggression": 0.6},
            best_score=0.80,
            best_elo=1200.0,
            hints="hint",
        )

        ctx = MagicMock(spec=MtsToolContext)
        with patch("autocontext.knowledge.export.export_skill_package", return_value=mock_pkg):
            result = export_skill(ctx, "grid_ctf")

        # Original to_dict keys should still be present
        assert "scenario_name" in result
        assert "playbook" in result
        assert "lessons" in result
        assert "best_score" in result
        assert result["scenario_name"] == "grid_ctf"

    def test_export_skill_includes_suggested_filename(self) -> None:
        """Result should include a suggested filename for install workflows."""
        from autocontext.mcp.tools import MtsToolContext, export_skill

        mock_pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid CTF",
            description="CTF.",
            playbook="",
            lessons=[],
            best_strategy=None,
            best_score=0.0,
            best_elo=1000.0,
            hints="",
        )

        ctx = MagicMock(spec=MtsToolContext)
        with patch("autocontext.knowledge.export.export_skill_package", return_value=mock_pkg):
            result = export_skill(ctx, "grid_ctf")

        assert "suggested_filename" in result
        assert result["suggested_filename"].endswith(".md")
        assert "grid" in result["suggested_filename"]


# ---------------------------------------------------------------------------
# REST API export surface
# ---------------------------------------------------------------------------

class TestRestApiExportSkill:
    def test_rest_export_includes_skill_markdown(self) -> None:
        """The REST /api/knowledge/export endpoint should include skill_markdown."""
        from autocontext.server.knowledge_api import export_skill as rest_export

        mock_pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid CTF",
            description="CTF game.",
            playbook="# Playbook",
            lessons=["lesson"],
            best_strategy={"aggression": 0.6},
            best_score=0.80,
            best_elo=1200.0,
            hints="",
        )

        with (
            patch("autocontext.server.knowledge_api._get_ctx") as mock_ctx,
            patch("autocontext.server.knowledge_api.export_skill_package", return_value=mock_pkg),
        ):
            mock_ctx.return_value = MagicMock()
            result = rest_export("grid_ctf", format="skill")

        assert "skill_markdown" in result
        assert "scenario_name" in result
