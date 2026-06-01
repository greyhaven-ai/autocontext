"""AC-728 `autoctx probes check` CLI parity tests (slice 4).

Mirrors the test surface of TS PR #991's `cli-check.test.ts`. The
in-process handler ``run_probes_check`` returns
``{stdout, stderr, exit_code}`` so the tests consume it directly
without spawning a subprocess.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.cli_probes import (
    CHECK_HELP_TEXT,
    ProbesCheckResult,
    run_probes_check,
)


def _passing_suite_dict() -> dict:
    return {
        "schema_version": 1,
        "probes": [
            {
                "kind": "terminal",
                "label": "demo",
                "inputs": {"exitCode": 0, "stdout": "ok", "stderr": ""},
            }
        ],
    }


def _failing_suite_dict() -> dict:
    return {
        "schema_version": 1,
        "probes": [
            {
                "kind": "terminal",
                "inputs": {"exitCode": 1, "stdout": "", "stderr": ""},
            }
        ],
    }


# ---------------------------------------------------------------------------
# argv parsing
# ---------------------------------------------------------------------------


def test_help_flag_emits_help_text_and_exits_zero() -> None:
    result = run_probes_check(["--help"])
    assert result.exit_code == 0
    assert "autoctx probes check" in result.stdout


def test_short_help_flag_works() -> None:
    result = run_probes_check(["-h"])
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_missing_suite_arg_emits_actionable_error_and_exits_one() -> None:
    result = run_probes_check([])
    assert result.exit_code == 1
    assert "--suite" in result.stderr


def test_unknown_argument_rejected_with_help_text() -> None:
    result = run_probes_check(["--bogus"])
    assert result.exit_code == 1
    assert "unknown argument" in result.stderr


def test_suite_equal_form_accepted(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_passing_suite_dict()))
    result = run_probes_check([f"--suite={suite_path}"])
    assert result.exit_code == 0, result.stderr


# ---------------------------------------------------------------------------
# file loading + parse errors
# ---------------------------------------------------------------------------


def test_missing_file_surfaces_load_error(tmp_path: Path) -> None:
    result = run_probes_check(["--suite", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "failed to load suite" in result.stderr


def test_malformed_json_surfaces_load_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    result = run_probes_check(["--suite", str(bad)])
    assert result.exit_code == 1
    assert "failed to load suite" in result.stderr


def test_schema_invalid_suite_surfaces_validation_issues(tmp_path: Path) -> None:
    """A typo like `requiredStdoutPattern` (missing `s`) must fail
    validation and print the path of the offending issue, not be
    silently dropped."""
    bad = tmp_path / "typo.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "probes": [
                    {
                        "kind": "terminal",
                        "inputs": {
                            "exitCode": 0,
                            "stdout": "",
                            "stderr": "",
                            "requiredStdoutPattern": "x",  # typo
                        },
                    }
                ],
            }
        )
    )
    result = run_probes_check(["--suite", str(bad)])
    assert result.exit_code == 1
    assert "suite validation failed" in result.stderr
    # path of the offending issue is included in the rendered list
    assert "requiredStdoutPattern" in result.stderr


# ---------------------------------------------------------------------------
# stdin pipe form
# ---------------------------------------------------------------------------


def test_suite_dash_reads_from_stdin_text() -> None:
    stdin_text = json.dumps(_passing_suite_dict())
    result = run_probes_check(["--suite", "-"], stdin_text=stdin_text)
    assert result.exit_code == 0
    assert "PASS" in result.stdout


# ---------------------------------------------------------------------------
# text report
# ---------------------------------------------------------------------------


def test_text_report_for_passing_suite(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_passing_suite_dict()))
    result = run_probes_check(["--suite", str(suite_path)])
    assert result.exit_code == 0
    assert "probes check: PASS" in result.stdout
    assert "terminal [demo]: pass" in result.stdout


def test_text_report_for_failing_suite_lists_failure_kinds(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_failing_suite_dict()))
    result = run_probes_check(["--suite", str(suite_path)])
    assert result.exit_code == 1
    assert "probes check: FAIL" in result.stdout
    assert "terminal: fail" in result.stdout
    assert "unexpected-exit-code" in result.stdout


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def test_json_report_round_trips(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_passing_suite_dict()))
    result = run_probes_check(["--suite", str(suite_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["results"][0]["kind"] == "terminal"
    assert payload["results"][0]["label"] == "demo"
    assert payload["results"][0]["passed"] is True


def test_json_report_for_failing_suite_carries_failures(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_failing_suite_dict()))
    result = run_probes_check(["--suite", str(suite_path), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    failures = payload["results"][0]["failures"]
    assert any(f["kind"] == "unexpected-exit-code" for f in failures)


# ---------------------------------------------------------------------------
# typer integration
# ---------------------------------------------------------------------------


def test_typer_probes_check_registered_at_canonical_path(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_passing_suite_dict()))
    result = CliRunner().invoke(app, ["probes", "check", "--suite", str(suite_path)])
    assert result.exit_code == 0, result.output
    assert "probes check: PASS" in result.output


def test_typer_probes_check_json_emits_parseable_payload(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_passing_suite_dict()))
    result = CliRunner().invoke(app, ["probes", "check", "--suite", str(suite_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["passed"] is True


def test_typer_probes_check_missing_suite_exits_nonzero() -> None:
    result = CliRunner().invoke(app, ["probes", "check"])
    assert result.exit_code == 1


def test_typer_probes_check_json_failure_leaves_stdout_parseable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PR #1008 review (P2): errors used to flow through the rich
    `console`, which writes to stdout by default. That contaminated
    `--json` output on load / parse / validation failures, so
    JSON-mode consumers could not safely parse stdout. The typer
    wrapper now routes errors to real stderr.
    """
    missing = tmp_path / "nope.json"
    # Use the in-process handler + capsys to assert the stdout / stderr
    # split directly; CliRunner across click versions disagrees on
    # `mix_stderr` so we exercise the typer wrapper via the typer app
    # but inspect the raw streams via capsys.
    import typer as _typer
    from rich.console import Console as _Console

    from autocontext.cli_probes import register_probes_command

    local_app = _typer.Typer()
    register_probes_command(local_app, console=_Console())
    # Run the typer app directly; it raises typer.Exit on completion.
    with pytest.raises(SystemExit):
        local_app(["probes", "check", "--suite", str(missing), "--json"], standalone_mode=True)
    captured = capsys.readouterr()
    # stdout must be empty so JSON consumers do not choke on a
    # human-readable error message.
    assert captured.out == ""
    # stderr carries the actionable error.
    assert "failed to load suite" in captured.err


# ---------------------------------------------------------------------------
# help text exposed for downstream consumers
# ---------------------------------------------------------------------------


def test_help_text_documents_canonical_pipe_form() -> None:
    assert "autoctx probes check --suite -" in CHECK_HELP_TEXT
    assert "probes extract" in CHECK_HELP_TEXT


def test_result_dataclass_is_frozen() -> None:
    result = ProbesCheckResult(stdout="x", stderr="y", exit_code=0)
    import pytest

    with pytest.raises(AttributeError):
        result.stdout = "z"  # type: ignore[misc]
