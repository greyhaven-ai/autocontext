"""AC-728 contract probes Python parity tests (slice 1).

Mirrors the failure-kind shape and pinning structure of
``ts/tests/control-plane/contract-probes/contract-probes.test.ts``
for the four base probes (directory, terminal, service, artifact).
The slice-2 cleanup / media / distributed probes land in a follow-up
slice; their ``missing-observation`` invariant tests do not apply to
this surface (every observation field on the four base probes is
non-optional by construction, mirroring the TS audit conclusion).
"""

from __future__ import annotations

import re

import pytest

from autocontext.control_plane.contract_probes import (
    ArtifactContractProbeInputs,
    DirectoryContractProbeInputs,
    ServiceContractProbeInputs,
    ServiceEndpointObservation,
    TerminalContractProbeInputs,
    probe_artifact_contract,
    probe_directory_contract,
    probe_service_contract,
    probe_terminal_contract,
)

# ---------------------------------------------------------------------------
# directory
# ---------------------------------------------------------------------------


def test_directory_probe_passes_when_present_files_match_allowed_and_required() -> None:
    result = probe_directory_contract(
        DirectoryContractProbeInputs(
            present_files=("solution.txt", "notes.md"),
            required_files=("solution.txt",),
            allowed_files=("solution.txt", "notes.md"),
        )
    )
    assert result.passed is True
    assert result.failures == ()


def test_directory_probe_flags_unexpected_files_not_in_allowed_list() -> None:
    result = probe_directory_contract(
        DirectoryContractProbeInputs(
            present_files=("solution.txt", "leak.tmp"),
            required_files=("solution.txt",),
            allowed_files=("solution.txt",),
        )
    )
    assert result.passed is False
    kinds = {(f.kind, f.path) for f in result.failures}
    assert ("unexpected-file", "leak.tmp") in kinds


def test_directory_probe_flags_missing_required_files() -> None:
    result = probe_directory_contract(
        DirectoryContractProbeInputs(
            present_files=(),
            required_files=("solution.txt",),
            allowed_files=("solution.txt",),
        )
    )
    assert result.passed is False
    assert any(f.kind == "missing-file" and f.path == "solution.txt" for f in result.failures)


def test_directory_probe_honours_ignored_patterns() -> None:
    result = probe_directory_contract(
        DirectoryContractProbeInputs(
            present_files=("solution.txt", ".DS_Store"),
            required_files=("solution.txt",),
            allowed_files=("solution.txt",),
            ignored_patterns=(re.compile(r"\.DS_Store$"),),
        )
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# terminal
# ---------------------------------------------------------------------------


def test_terminal_probe_defaults_expected_exit_code_to_zero() -> None:
    ok = probe_terminal_contract(TerminalContractProbeInputs(exit_code=0, stdout="", stderr=""))
    assert ok.passed is True

    bad = probe_terminal_contract(TerminalContractProbeInputs(exit_code=1, stdout="", stderr=""))
    assert bad.passed is False
    assert bad.failures[0].kind == "unexpected-exit-code"


def test_terminal_probe_emits_required_and_forbidden_pattern_failures() -> None:
    result = probe_terminal_contract(
        TerminalContractProbeInputs(
            exit_code=0,
            stdout="hello world",
            stderr="oops boom",
            required_stdout_patterns=(re.compile(r"goodbye"),),
            forbidden_stdout_patterns=(re.compile(r"hello"),),
            required_stderr_patterns=(re.compile(r"finished"),),
            forbidden_stderr_patterns=(re.compile(r"boom"),),
        )
    )
    kinds = {f.kind for f in result.failures}
    assert {
        "missing-stdout-pattern",
        "forbidden-stdout-pattern",
        "missing-stderr-pattern",
        "forbidden-stderr-pattern",
    }.issubset(kinds)


def test_terminal_probe_passes_when_required_pattern_present_and_forbidden_absent() -> None:
    result = probe_terminal_contract(
        TerminalContractProbeInputs(
            exit_code=0,
            stdout="ran solve scenario X",
            stderr="",
            required_stdout_patterns=(re.compile(r"solve scenario \w+"),),
            forbidden_stdout_patterns=(re.compile(r"traceback"),),
        )
    )
    assert result.passed is True


def test_terminal_probe_required_stdout_pattern_against_empty_stdout_fails_loudly() -> None:
    """Pinning test for the slice-1 missing-observation invariant.

    Mirrors the TS close-out audit (PR #1000): observation fields are
    non-optional so an expectation against an empty observation MUST
    surface a loud failure, not a silent pass.
    """
    result = probe_terminal_contract(
        TerminalContractProbeInputs(
            exit_code=0,
            stdout="",
            stderr="",
            required_stdout_patterns=(re.compile(r"trace\."),),
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "missing-stdout-pattern"


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------


def test_service_probe_passes_when_all_required_endpoints_observed() -> None:
    result = probe_service_contract(
        ServiceContractProbeInputs(
            observed=(
                ServiceEndpointObservation(host="127.0.0.1", port=8000),
                ServiceEndpointObservation(host="127.0.0.1", port=8001),
            ),
            required=(ServiceEndpointObservation(host="127.0.0.1", port=8000),),
        )
    )
    assert result.passed is True


def test_service_probe_flags_missing_endpoint() -> None:
    result = probe_service_contract(
        ServiceContractProbeInputs(
            observed=(),
            required=(ServiceEndpointObservation(host="127.0.0.1", port=8000),),
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "missing-endpoint"


def test_service_probe_distinguishes_wrong_interface_from_missing() -> None:
    result = probe_service_contract(
        ServiceContractProbeInputs(
            observed=(ServiceEndpointObservation(host="0.0.0.0", port=8000),),
            required=(ServiceEndpointObservation(host="127.0.0.1", port=8000),),
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "wrong-interface"


def test_service_probe_flags_unexpected_endpoint_when_allowed_list_set() -> None:
    result = probe_service_contract(
        ServiceContractProbeInputs(
            observed=(
                ServiceEndpointObservation(host="127.0.0.1", port=8000),
                ServiceEndpointObservation(host="127.0.0.1", port=9999),
            ),
            required=(ServiceEndpointObservation(host="127.0.0.1", port=8000),),
            allowed=(ServiceEndpointObservation(host="127.0.0.1", port=8000),),
        )
    )
    assert result.passed is False
    assert any(f.kind == "unexpected-endpoint" and f.endpoint.port == 9999 for f in result.failures)


def test_service_probe_protocol_defaults_to_tcp_for_key_equality() -> None:
    result = probe_service_contract(
        ServiceContractProbeInputs(
            observed=(ServiceEndpointObservation(host="127.0.0.1", port=8000),),
            required=(ServiceEndpointObservation(host="127.0.0.1", port=8000, protocol="tcp"),),
        )
    )
    assert result.passed is True


def test_service_probe_required_against_empty_observed_fails_loudly() -> None:
    """Pinning test mirroring the TS close-out audit."""
    result = probe_service_contract(
        ServiceContractProbeInputs(
            observed=(),
            required=(ServiceEndpointObservation(host="127.0.0.1", port=8000),),
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "missing-endpoint"


# ---------------------------------------------------------------------------
# artifact
# ---------------------------------------------------------------------------


def test_artifact_probe_passes_clean_content() -> None:
    result = probe_artifact_contract(
        ArtifactContractProbeInputs(
            path="out/run.json",
            content='{"status": "ok"}',
            required_substrings=("status",),
            required_json_fields=("status",),
        )
    )
    assert result.passed is True


def test_artifact_probe_flags_missing_and_forbidden_substrings() -> None:
    result = probe_artifact_contract(
        ArtifactContractProbeInputs(
            path="report.txt",
            content="hello world\ntoken=AKIA123",
            required_substrings=("goodbye",),
            forbidden_substrings=("AKIA",),
        )
    )
    kinds = {f.kind for f in result.failures}
    assert {"missing-substring", "forbidden-substring"}.issubset(kinds)


def test_artifact_probe_flags_lf_violation_when_crlf_required() -> None:
    result = probe_artifact_contract(
        ArtifactContractProbeInputs(
            path="out.bat",
            content="echo hello\necho world\n",
            expected_line_ending="crlf",
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "wrong-line-ending"


def test_artifact_probe_flags_crlf_violation_when_lf_required() -> None:
    result = probe_artifact_contract(
        ArtifactContractProbeInputs(
            path="out.sh",
            content="echo hello\r\necho world\r\n",
            expected_line_ending="lf",
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "wrong-line-ending"


def test_artifact_probe_short_circuits_on_invalid_json_when_json_fields_required() -> None:
    result = probe_artifact_contract(
        ArtifactContractProbeInputs(
            path="out.json",
            content="{not-json",
            required_json_fields=("status",),
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "invalid-json"
    # Once JSON parsing fails the probe must not also emit missing-json-field
    # failures (they would be misleading: the parser stopped, not the value
    # absence). This mirrors the TS probe's early-return shape.
    assert all(f.kind != "missing-json-field" for f in result.failures)


def test_artifact_probe_json_null_value_satisfies_required_field() -> None:
    """PR #1004 review (P2): a key present with JSON null is NOT missing.

    TS probe treats only `undefined` as missing; the Python port used
    to return `None` from both "key absent" and "key present with
    null", causing `{"status": null}` to fail `required_json_fields:
    ["status"]`. Sentinel-based dot-path lookup keeps the present-but-
    null case as a pass to match the TS semantics.
    """
    result = probe_artifact_contract(
        ArtifactContractProbeInputs(
            path="out.json",
            content='{"status": null}',
            required_json_fields=("status",),
        )
    )
    assert result.passed is True


def test_artifact_probe_dotted_json_field_paths_check_nested_keys() -> None:
    result = probe_artifact_contract(
        ArtifactContractProbeInputs(
            path="out.json",
            content='{"meta": {"version": 1}}',
            required_json_fields=("meta.version", "meta.missing"),
        )
    )
    assert result.passed is False
    missing = [f for f in result.failures if f.kind == "missing-json-field"]
    assert {f.path for f in missing} == {"meta.missing"}


def test_artifact_probe_required_substring_against_empty_content_fails_loudly() -> None:
    """Pinning test mirroring the TS close-out audit."""
    result = probe_artifact_contract(
        ArtifactContractProbeInputs(
            path="empty.txt",
            content="",
            required_substrings=("expected",),
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "missing-substring"


# ---------------------------------------------------------------------------
# missing-observation invariant audit (slice 1 pinning)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_cls", "kwargs"),
    [
        (
            DirectoryContractProbeInputs,
            {"present_files": (), "required_files": ("solution.txt",), "allowed_files": ()},
        ),
        (
            TerminalContractProbeInputs,
            {
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "required_stdout_patterns": (re.compile(r"trace\."),),
            },
        ),
        (
            ServiceContractProbeInputs,
            {
                "observed": (),
                "required": (ServiceEndpointObservation(host="127.0.0.1", port=8000),),
            },
        ),
        (
            ArtifactContractProbeInputs,
            {"path": "empty.txt", "content": "", "required_substrings": ("expected",)},
        ),
    ],
)
def test_expectation_against_minimum_observation_always_fails_loudly(
    input_cls: type,
    kwargs: dict[str, object],
) -> None:
    """One expectation + the corresponding empty observation MUST fail.

    Pinning the slice-1 invariant: the four base probes' observation
    fields are non-optional, so the silent-pass shape cannot arise.
    This test consolidates the per-probe pinning tests above into a
    single parametrised property.
    """
    from collections.abc import Callable
    from typing import Any

    dispatch: dict[type, Callable[..., Any]] = {
        DirectoryContractProbeInputs: probe_directory_contract,
        TerminalContractProbeInputs: probe_terminal_contract,
        ServiceContractProbeInputs: probe_service_contract,
        ArtifactContractProbeInputs: probe_artifact_contract,
    }
    inputs = input_cls(**kwargs)
    result = dispatch[input_cls](inputs)
    assert result.passed is False
    assert len(result.failures) >= 1
