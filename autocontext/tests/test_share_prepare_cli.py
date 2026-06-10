"""End-to-end tests for `autoctx share prepare` (tier-0 CLI)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app

runner = CliRunner()


def _make_run(tmp_path: Path, report_body: str) -> tuple[Path, str]:
    runs_root = tmp_path / "runs"
    run_id = "run_test01"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "session_report.md").write_text(report_body, encoding="utf-8")
    return runs_root, run_id


def test_dry_run_clean_writes_report_not_bundle(tmp_path: Path) -> None:
    runs_root, run_id = _make_run(tmp_path, "# clean report\n\nThe loop cited the clause before escalating.\n")
    output = tmp_path / "out"

    result = runner.invoke(
        app,
        ["share", "prepare", run_id, "--runs-root", str(runs_root), "--output", str(output), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    report = json.loads((output / "prepare-report.json").read_text())
    assert report["overall_verdict"] == "needs_human_review"
    assert report["dry_run"] is True
    assert not (output / "bundle.manifest.json").exists()


def test_clean_run_writes_redacted_bundle(tmp_path: Path) -> None:
    runs_root, run_id = _make_run(tmp_path, "# report\n\nResolution time fell from 21d to 8d.\n")
    output = tmp_path / "out"

    result = runner.invoke(
        app,
        ["share", "prepare", run_id, "--runs-root", str(runs_root), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    manifest = json.loads((output / "bundle.manifest.json").read_text())
    assert manifest["schema_version"] == "trace-exchange.v1"
    assert manifest["ruleset_version"] == "trace-exchange-rules.v1"
    assert manifest["files"]
    assert all("sha256" in entry for entry in manifest["files"])


def test_reject_severity_refuses_bundle(tmp_path: Path) -> None:
    # A report quoting a real credential -> redactable; embed an encoded payload -> reject.
    body = "# postmortem\n\nleaked key AKIAIOSFODNN7EXAMPLE\npayload: " + ("Qby" * 40) + "\n"
    runs_root, run_id = _make_run(tmp_path, body)
    output = tmp_path / "out"

    result = runner.invoke(
        app,
        ["share", "prepare", run_id, "--runs-root", str(runs_root), "--output", str(output)],
    )

    assert result.exit_code == 2, result.output
    report = json.loads((output / "prepare-report.json").read_text())
    assert report["overall_verdict"] == "rejected"
    assert report["refused"] is True
    assert not (output / "bundle.manifest.json").exists()


def test_no_shareable_files_is_graceful(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    (runs_root / "run_empty").mkdir(parents=True)
    output = tmp_path / "out"

    result = runner.invoke(
        app,
        ["share", "prepare", "run_empty", "--runs-root", str(runs_root), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    report = json.loads((output / "prepare-report.json").read_text())
    assert report["files"] == []
