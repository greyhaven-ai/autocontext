"""The default configuration keeps every artifact root relative to the working directory.

These tests run the real CLI with no root overrides, so artifacts are written and
read back through relative paths exactly as a fresh installation does.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autocontext.cli import app


@pytest.fixture
def default_config_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in [key for key in os.environ if key.startswith("AUTOCONTEXT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "deterministic")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.slow
def test_run_then_replay_round_trips_on_default_relative_roots(default_config_cwd: Path) -> None:
    runner = CliRunner()

    run = runner.invoke(app, ["run", "grid_ctf", "--iterations", "1", "--run-id", "smoke"])
    assert run.exit_code == 0, run.output

    replay = runner.invoke(app, ["replay", "smoke", "--generation", "1"])
    assert replay.exit_code == 0, replay.output

    gen_dir = default_config_cwd / "runs" / "smoke" / "generations" / "gen_1"
    assert json.loads((gen_dir / "metrics.json").read_text(encoding="utf-8"))
    assert not (gen_dir / "runs").exists()
    assert (default_config_cwd / "knowledge" / "analytics" / "traces" / "trace-smoke.json").is_file()
