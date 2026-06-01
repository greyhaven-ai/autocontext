"""AC-728 `autoctx probes extract` parity tests (slice 5).

Mirrors the slice-5/TS PR #992 test surface from
``ts/tests/control-plane/contract-probes/contract-probes.test.ts``.
Covers the four base probe kinds (terminal, directory, service,
artifact). Cleanup / media / distributed land in slice 6.

The in-process handler ``run_probes_extract`` returns
``{stdout, stderr, exit_code}`` so the tests consume it directly
without spawning a subprocess.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.cli_probes import (
    EXTRACT_HELP_TEXT,
    ProbesExtractResult,
    run_probes_check,
    run_probes_extract,
)
from autocontext.control_plane.contract_probes import (
    ContractProbeSuiteSchema,
    run_contract_probe_suite,
)
from autocontext.control_plane.contract_probes.extract import (
    HarnessTraceSchema,
    extract_contract_probe_suite,
)


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload))
    return path


def _terminal_trace() -> dict:
    return {
        "schema_version": 1,
        "observations": {"terminal": {"exitCode": 0, "stdout": "solution.txt", "stderr": ""}},
        "expectations": {
            "terminal": {"requiredStdoutPatterns": [r"solution\.txt"]},
        },
    }


# ---------------------------------------------------------------------------
# argv parsing
# ---------------------------------------------------------------------------


def test_help_flag_emits_help_text() -> None:
    result = run_probes_extract(["--help"])
    assert result.exit_code == 0
    assert "autoctx probes extract" in result.stdout


def test_missing_trace_arg_rejected() -> None:
    result = run_probes_extract([])
    assert result.exit_code == 1
    assert "--trace" in result.stderr


def test_unknown_flag_rejected() -> None:
    result = run_probes_extract(["--nope"])
    assert result.exit_code == 1
    assert "unknown argument" in result.stderr


def test_trace_equal_form_accepted(tmp_path: Path) -> None:
    trace_path = _write(tmp_path / "t.json", _terminal_trace())
    result = run_probes_extract([f"--trace={trace_path}"])
    assert result.exit_code == 0, result.stderr


# ---------------------------------------------------------------------------
# load + parse errors
# ---------------------------------------------------------------------------


def test_missing_file_surfaces_load_error(tmp_path: Path) -> None:
    result = run_probes_extract(["--trace", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "failed to read trace" in result.stderr


def test_passing_a_directory_surfaces_load_error_not_traceback(tmp_path: Path) -> None:
    """PR #1010 review (P2): the previous `except FileNotFoundError`
    only handled the missing-file case. Passing a directory raised
    `IsADirectoryError`, which escaped as a Rich traceback instead of
    a friendly stderr message. Catching `OSError` covers the family.
    """
    result = run_probes_extract(["--trace", str(tmp_path)])
    assert result.exit_code == 1
    assert "failed to read trace" in result.stderr


def test_non_utf8_trace_file_surfaces_load_error(tmp_path: Path) -> None:
    """PR #1010 review (P2): a non-UTF8 trace file raised
    `UnicodeDecodeError` from `read_text(encoding="utf-8")`. Catch it
    too so the error returns through the same `failed to read trace`
    stderr path.
    """
    bad = tmp_path / "binary.json"
    bad.write_bytes(b"\xff\xfe\x00\x00not valid utf-8")
    result = run_probes_extract(["--trace", str(bad)])
    assert result.exit_code == 1
    assert "failed to read trace" in result.stderr


def test_malformed_json_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    result = run_probes_extract(["--trace", str(bad)])
    assert result.exit_code == 1
    assert "invalid JSON" in result.stderr


def test_schema_invalid_trace_surfaces_validation_issues(tmp_path: Path) -> None:
    """A typo at the trace envelope must reject with the dotted path."""
    trace = tmp_path / "bad.json"
    _write(trace, {"schema_version": 1, "observations": {"terminalx": {}}})
    result = run_probes_extract(["--trace", str(trace)])
    assert result.exit_code == 1
    assert "trace validation failed" in result.stderr


# ---------------------------------------------------------------------------
# orphan-expectation rejection (slice-1 audit invariant)
# ---------------------------------------------------------------------------


def test_terminal_expectation_without_observation_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {},
                "expectations": {"terminal": {"expectedExitCode": 0}},
            }
        )
    assert "expectations.terminal" in str(excinfo.value)


def test_directory_expectation_without_workdir_observation_rejected() -> None:
    with pytest.raises(ValidationError):
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {},
                "expectations": {"directory": {"requiredFiles": ["solution.txt"]}},
            }
        )


def test_services_expectation_without_observation_rejected() -> None:
    with pytest.raises(ValidationError):
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {},
                "expectations": {"services": {"required": [{"host": "127.0.0.1", "port": 8000}]}},
            }
        )


def test_artifact_expectation_without_observation_rejected() -> None:
    with pytest.raises(ValidationError):
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {},
                "expectations": {"artifacts": [{"path": "out.json"}]},
            }
        )


def test_artifact_expectation_with_unknown_path_rejected() -> None:
    """Per-artifact expectations must reference an observed path."""
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {
                    "artifacts": [{"path": "out.json", "content": "{}"}],
                },
                "expectations": {
                    "artifacts": [{"path": "missing.json"}],
                },
            }
        )
    assert "missing.json" in str(excinfo.value)


def test_duplicate_per_artifact_expectation_rejected() -> None:
    """PR #993 review (P2): duplicate per-path expectations silently
    lost assertions because the extractor stored them in a Map keyed
    by path. Reject duplicates at parse time."""
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {
                    "artifacts": [{"path": "out.json", "content": "{}"}],
                },
                "expectations": {
                    "artifacts": [
                        {"path": "out.json", "requiredSubstrings": ["a"]},
                        {"path": "out.json", "requiredSubstrings": ["b"]},
                    ],
                },
            }
        )
    assert "duplicate" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# extractor join shape
# ---------------------------------------------------------------------------


def test_observation_only_terminal_passes_default() -> None:
    """A terminal observation with no expectations produces a probe
    that checks the default exit-code-0 contract (no patterns)."""
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {"terminal": {"exitCode": 0, "stdout": "", "stderr": ""}},
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    assert suite_dict["probes"][0]["kind"] == "terminal"
    # No expectation-side fields injected.
    assert "expectedExitCode" not in suite_dict["probes"][0]["inputs"]


def test_observation_only_workdir_passes_with_empty_required_and_allowed() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {"workdir": {"presentFiles": ["solution.txt"]}},
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    inputs = suite_dict["probes"][0]["inputs"]
    assert inputs["presentFiles"] == ["solution.txt"]
    # No expectations -> empty required/allowed lists; the suite-runner
    # schema requires these fields, so the extractor populates them.
    assert inputs["requiredFiles"] == []
    assert inputs["allowedFiles"] == []


def test_terminal_join_with_required_pattern_succeeds() -> None:
    trace = HarnessTraceSchema.model_validate(_terminal_trace())
    suite_dict = extract_contract_probe_suite(trace)
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    result = run_contract_probe_suite(suite)
    assert result.passed is True


def test_directory_join_required_and_allowed_files_succeeds() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {"workdir": {"presentFiles": ["solution.txt"]}},
            "expectations": {
                "directory": {
                    "requiredFiles": ["solution.txt"],
                    "allowedFiles": ["solution.txt"],
                }
            },
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    result = run_contract_probe_suite(suite)
    assert result.passed is True


def test_service_join_required_and_allowed_succeeds() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {"services": [{"host": "127.0.0.1", "port": 8000}]},
            "expectations": {
                "services": {
                    "required": [{"host": "127.0.0.1", "port": 8000}],
                    "allowed": [{"host": "127.0.0.1", "port": 8000}],
                }
            },
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    result = run_contract_probe_suite(suite)
    assert result.passed is True


def test_artifact_join_by_path_and_no_expectation_path_emits_no_op() -> None:
    """An artifact observation with no matching expectation is still
    encoded as a probe (path + content). Mirrors TS."""
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {
                "artifacts": [
                    {"path": "out.json", "content": '{"ok": true}'},
                    {"path": "noexp.txt", "content": "hi"},
                ]
            },
            "expectations": {
                "artifacts": [{"path": "out.json", "requiredSubstrings": ["ok"]}],
            },
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    paths = [p["inputs"]["path"] for p in suite_dict["probes"]]
    assert paths == ["out.json", "noexp.txt"]
    # First probe has the substring assertion; second has no assertions.
    assert suite_dict["probes"][0]["inputs"]["requiredSubstrings"] == ["ok"]
    assert "requiredSubstrings" not in suite_dict["probes"][1]["inputs"]


def test_extracted_suite_passes_runner_validation_round_trip(tmp_path: Path) -> None:
    """Every extracted suite must parse cleanly against the runner
    schema. This is the slice-1 round-trip invariant."""
    trace_path = _write(tmp_path / "t.json", _terminal_trace())
    result = run_probes_extract(["--trace", str(trace_path)])
    assert result.exit_code == 0
    suite_dict = json.loads(result.stdout)
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    run = run_contract_probe_suite(suite)
    assert run.passed is True


# ---------------------------------------------------------------------------
# label propagation
# ---------------------------------------------------------------------------


def test_label_propagates_to_emitted_probes() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "label": "demo",
            "observations": {"terminal": {"exitCode": 0}},
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    assert suite_dict["probes"][0]["label"] == "demo"


def test_per_artifact_label_overrides_trace_label() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "label": "trace-label",
            "observations": {"artifacts": [{"path": "x", "content": ""}]},
            "expectations": {"artifacts": [{"path": "x", "label": "per-artifact"}]},
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    assert suite_dict["probes"][0]["label"] == "per-artifact"


# ---------------------------------------------------------------------------
# --output writes a file
# ---------------------------------------------------------------------------


def test_output_writes_suite_to_file_and_creates_parents(tmp_path: Path) -> None:
    trace_path = _write(tmp_path / "t.json", _terminal_trace())
    out_path = tmp_path / "nested" / "dir" / "suite.json"
    result = run_probes_extract(["--trace", str(trace_path), "--output", str(out_path)])
    assert result.exit_code == 0, result.stderr
    assert "wrote suite" in result.stdout
    assert out_path.exists()
    # The written file is a valid suite.
    suite_dict = json.loads(out_path.read_text())
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    assert suite.schema_version == 1


# ---------------------------------------------------------------------------
# extract | check pipe round-trip
# ---------------------------------------------------------------------------


def test_extract_then_check_pipe_round_trip(tmp_path: Path) -> None:
    trace_path = _write(tmp_path / "t.json", _terminal_trace())
    extract_result = run_probes_extract(["--trace", str(trace_path)])
    assert extract_result.exit_code == 0
    check_result = run_probes_check(["--suite", "-"], stdin_text=extract_result.stdout)
    assert check_result.exit_code == 0
    assert "probes check: PASS" in check_result.stdout


# ---------------------------------------------------------------------------
# dataclass + help text invariants
# ---------------------------------------------------------------------------


def test_help_text_documents_slice_5_scope() -> None:
    assert "terminal" in EXTRACT_HELP_TEXT
    assert "artifact" in EXTRACT_HELP_TEXT


def test_result_dataclass_is_frozen() -> None:
    result = ProbesExtractResult(stdout="x", stderr="y", exit_code=0)
    with pytest.raises(AttributeError):
        result.stdout = "z"  # type: ignore[misc]
