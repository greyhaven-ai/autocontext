"""End-to-end tests for `autoctx share prepare` (tier-0 CLI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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


def test_ndjson_trace_is_accepted(tmp_path: Path) -> None:
    # Regression: `.ndjson` (the collector's trace format) was inferred as a
    # trace type but omitted from the intake allowlist, rejecting clean runs.
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "run_nd"
    run_dir.mkdir(parents=True)
    (run_dir / "events.ndjson").write_text('{"step": 1, "tool": "search"}\n{"step": 2, "tool": "read"}\n', encoding="utf-8")
    output = tmp_path / "out"

    result = runner.invoke(app, ["share", "prepare", "run_nd", "--runs-root", str(runs_root), "--output", str(output)])

    assert result.exit_code == 0, result.output
    report = json.loads((output / "prepare-report.json").read_text())
    nd = [f for f in report["files"] if f["path"].endswith("events.ndjson")]
    assert nd and nd[0]["intake_rejected"] is None
    assert report["overall_verdict"] != "rejected"


def test_nested_same_basename_no_collision(tmp_path: Path) -> None:
    # Regression: bundle paths built from basename only -> nested artifacts with
    # the same name collided, silently dropping all but the last.
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "run_nest"
    (run_dir / "a").mkdir(parents=True)
    (run_dir / "b").mkdir(parents=True)
    (run_dir / "a" / "foo_output.txt").write_text("alpha body\n", encoding="utf-8")
    (run_dir / "b" / "foo_output.txt").write_text("beta body\n", encoding="utf-8")
    output = tmp_path / "out"

    result = runner.invoke(app, ["share", "prepare", "run_nest", "--runs-root", str(runs_root), "--output", str(output)])

    assert result.exit_code == 0, result.output
    manifest = json.loads((output / "bundle.manifest.json").read_text())
    paths = sorted(entry["path"] for entry in manifest["files"])
    assert paths == ["runs/run_nest/a/foo_output.txt", "runs/run_nest/b/foo_output.txt"]
    assert (output / "runs/run_nest/a/foo_output.txt").read_text() == "alpha body\n"
    assert (output / "runs/run_nest/b/foo_output.txt").read_text() == "beta body\n"


def test_local_scan_flagged_when_review_finding(tmp_path: Path) -> None:
    # Regression: local_scan was derived from the verdict, so a review-level
    # finding (verdict needs_human_review) was mislabeled local_scan="passed".
    runs_root, run_id = _make_run(tmp_path, "# report\n\nObserved callback to 10.1.2.3 during the run.\n")
    output = tmp_path / "out"

    result = runner.invoke(app, ["share", "prepare", run_id, "--runs-root", str(runs_root), "--output", str(output)])

    assert result.exit_code == 0, result.output
    manifest = json.loads((output / "bundle.manifest.json").read_text())
    assert manifest["prepare"]["local_scan"] == "flagged"


def test_non_dry_run_without_output_writes_bundle_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: omitting --output on a non-dry-run wrote only the report, no
    # bundle. It should default the bundle output to cwd.
    runs_root, run_id = _make_run(tmp_path, "# report\n\nResolution time fell from 21d to 8d.\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["share", "prepare", run_id, "--runs-root", str(runs_root)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "bundle.manifest.json").exists()


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
