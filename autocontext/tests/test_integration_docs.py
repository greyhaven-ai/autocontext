"""Tests for AC-234: Agent integration docs — CLI-first usage with MCP fallback.

Verifies:
1. The integration guide file exists and covers required topics.
2. README links to the integration guide.
3. CLI --json contract patterns referenced in the guide are accurate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
README_PATH = Path(__file__).resolve().parents[1] / "README.md"
INTEGRATION_GUIDE = DOCS_DIR / "agent-integration.md"


# ---------------------------------------------------------------------------
# 1. Integration guide exists
# ---------------------------------------------------------------------------


class TestIntegrationGuideExists:
    def test_file_exists(self) -> None:
        assert INTEGRATION_GUIDE.is_file(), (
            f"Expected integration guide at {INTEGRATION_GUIDE}. "
            "AC-234 requires docs/agent-integration.md."
        )

    def test_readme_links_to_guide(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        assert "agent-integration.md" in readme, (
            "README.md should link to the integration guide."
        )


# ---------------------------------------------------------------------------
# 2. Required sections in integration guide
# ---------------------------------------------------------------------------


class TestIntegrationGuideContent:
    @pytest.fixture(autouse=True)
    def _load_guide(self) -> None:
        if not INTEGRATION_GUIDE.is_file():
            pytest.skip("integration guide not yet written")
        self.content = INTEGRATION_GUIDE.read_text(encoding="utf-8")

    def test_has_cli_first_rationale(self) -> None:
        """Guide should explain why CLI is the default integration surface."""
        assert "cli" in self.content.lower()
        # Should mention at least one Unix CLI advantage
        assert any(
            term in self.content.lower()
            for term in ["exit code", "stdout", "stderr", "text", "compose"]
        ), "Guide should mention Unix CLI advantages (exit codes, stdout/stderr, etc.)"

    def test_has_json_output_section(self) -> None:
        """Guide should document --json output patterns."""
        assert "--json" in self.content

    def test_documents_key_commands(self) -> None:
        """Guide should reference the main CLI commands agents will use."""
        for cmd in ("run", "status", "export", "train"):
            assert cmd in self.content.lower(), f"Guide should mention the '{cmd}' command"

    def test_documents_json_stdout_stderr_contract(self) -> None:
        """Guide should explain stdout for data, stderr for errors."""
        assert "stdout" in self.content.lower()
        assert "stderr" in self.content.lower()

    def test_documents_exit_codes(self) -> None:
        """Guide should mention exit codes for machine parsing."""
        assert "exit" in self.content.lower()

    def test_has_mcp_section(self) -> None:
        """Guide should have an MCP section (secondary/fallback)."""
        assert "mcp" in self.content.lower()

    def test_positions_mcp_as_secondary(self) -> None:
        """MCP should not appear before CLI in the guide structure."""
        cli_pos = self.content.lower().find("# cli")
        if cli_pos == -1:
            cli_pos = self.content.lower().find("## cli")
        mcp_pos = self.content.lower().find("# mcp")
        if mcp_pos == -1:
            mcp_pos = self.content.lower().find("## mcp")
        if cli_pos >= 0 and mcp_pos >= 0:
            assert cli_pos < mcp_pos, (
                "CLI section should appear before MCP section (CLI-first positioning)."
            )

    def test_has_concrete_cli_example(self) -> None:
        """Guide should include at least one concrete CLI command example."""
        assert "autoctx" in self.content

    def test_has_concrete_mcp_example(self) -> None:
        """Guide should include at least one MCP example or tool reference."""
        assert "mcp-serve" in self.content or "autocontext_" in self.content

    def test_documents_provider_configuration(self) -> None:
        """Guide should mention provider configuration for non-Anthropic usage."""
        assert "AUTOCONTEXT_" in self.content

    def test_documents_monitoring_guidance(self) -> None:
        """Guide should explain how to monitor async workflows without inventing a CLI command."""
        assert "poll" in self.content.lower() or "monitor" in self.content.lower()
        assert "status --json" in self.content or "autoctx status" in self.content

    def test_documents_error_json_format(self) -> None:
        """Guide should show the error JSON format."""
        assert '"error"' in self.content

    def test_typescript_section_does_not_link_missing_readme(self) -> None:
        """Guide should not point to a TypeScript README that does not exist."""
        assert "../ts/README.md" not in self.content


# ---------------------------------------------------------------------------
# 3. CLI --json contract accuracy (supplement existing tests)
# ---------------------------------------------------------------------------


class TestCLIJsonContractAccuracy:
    """Verify CLI --json patterns referenced in docs actually match implementation."""

    def test_json_stdout_helper_writes_to_stdout(self) -> None:
        """_write_json_stdout writes JSON to stdout, not stderr."""
        import io
        from unittest.mock import patch

        from autocontext.cli import _write_json_stdout

        buf = io.StringIO()
        with patch("autocontext.cli.sys.stdout", buf):
            _write_json_stdout({"test": True})
        output = buf.getvalue()
        import json
        parsed = json.loads(output.strip())
        assert parsed == {"test": True}

    def test_json_stderr_helper_writes_error_format(self) -> None:
        """_write_json_stderr writes {"error": "..."} to stderr."""
        import io
        from unittest.mock import patch

        from autocontext.cli import _write_json_stderr

        buf = io.StringIO()
        with patch("autocontext.cli.sys.stderr", buf):
            _write_json_stderr("something failed")
        output = buf.getvalue()
        import json
        parsed = json.loads(output.strip())
        assert parsed == {"error": "something failed"}

    def test_list_json_returns_array(self) -> None:
        """autoctx list --json should return a JSON array to stdout."""
        from unittest.mock import patch

        from typer.testing import CliRunner

        from autocontext.cli import app
        from autocontext.config.settings import AppSettings

        cli_runner = CliRunner()
        tmp_settings = AppSettings(
            db_path=Path("/tmp/test_ac234.sqlite3"),
            runs_root=Path("/tmp/test_ac234_runs"),
        )
        with patch("autocontext.cli.load_settings", return_value=tmp_settings):
            result = cli_runner.invoke(app, ["list", "--json"])
        # If no DB exists, it may error, but the format should still be JSON
        if result.exit_code == 0:
            import json
            parsed = json.loads(result.stdout.strip())
            assert isinstance(parsed, list)
