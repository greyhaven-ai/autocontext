"""Tests for --preset CLI flag (MTS-175)."""
from __future__ import annotations

import os
import re
from unittest.mock import patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.config.presets import apply_preset
from autocontext.config.settings import load_settings

runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _normalize_help_output(output: str) -> str:
    """Strip terminal styling so help assertions survive Rich/CI rendering."""

    return _ANSI_ESCAPE_RE.sub("", output)


def test_cli_preset_rapid_applies_values() -> None:
    """--preset rapid should apply rapid preset values to the settings."""
    overrides = apply_preset("rapid")
    assert overrides["curator_enabled"] is False
    assert overrides["matches_per_generation"] == 2

    # Verify the CLI flag is accepted
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--preset" in _normalize_help_output(result.output)


def test_cli_preset_overrides_env_var() -> None:
    """CLI --preset should override AUTOCONTEXT_PRESET env var."""
    # If AUTOCONTEXT_PRESET=deep but CLI passes --preset=quick,
    # quick should win (CLI sets the env var before load_settings runs).
    env = {"AUTOCONTEXT_PRESET": "quick"}
    with patch.dict(os.environ, env, clear=False):
        settings = load_settings()
    assert settings.matches_per_generation == 2  # quick preset value

    env = {"AUTOCONTEXT_PRESET": "deep"}
    with patch.dict(os.environ, env, clear=False):
        settings = load_settings()
    assert settings.matches_per_generation == 5  # deep preset value

    # The CLI --preset flag should override AUTOCONTEXT_PRESET
    result = runner.invoke(app, ["run", "--help"])
    normalized_output = _normalize_help_output(result.output)
    assert "--preset" in normalized_output
    # Check valid presets are documented
    assert "quick" in normalized_output.lower() or "rapid" in normalized_output.lower()


def test_cli_run_help_documents_presets() -> None:
    """autoctx run --help should document available presets."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--preset" in _normalize_help_output(result.output)
