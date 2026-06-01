"""AC-728 contract-probe suite runner (Python parity, slice 3) tests.

Mirrors the test surface of
``ts/tests/control-plane/contract-probes/contract-probes.test.ts``
for the runner / schema split shipped in TS PR #990.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.control_plane.contract_probes import (
    ContractProbeSuite,
    ContractProbeSuiteSchema,
    load_contract_probe_suite,
    run_contract_probe_suite,
)


def _suite(*probes: dict) -> ContractProbeSuite:
    return ContractProbeSuiteSchema.model_validate({"schema_version": 1, "probes": probes})


# ---------------------------------------------------------------------------
# schema validation
# ---------------------------------------------------------------------------


def test_empty_suite_passes() -> None:
    result = run_contract_probe_suite(_suite())
    assert result.passed is True
    assert result.results == ()


def test_unknown_probe_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        _suite({"kind": "unknown", "inputs": {}})


def test_schema_version_must_be_one() -> None:
    with pytest.raises(ValidationError):
        ContractProbeSuiteSchema.model_validate({"schema_version": 2, "probes": []})


def test_extra_keys_at_invocation_level_rejected() -> None:
    """Mirrors TS `.strict()`: a typo at the invocation envelope (e.g.
    `inputs2`) must fail validation rather than be silently dropped."""
    with pytest.raises(ValidationError):
        _suite({"kind": "terminal", "inputs": {"exitCode": 0, "stdout": "", "stderr": ""}, "inputs2": {}})


def test_extra_keys_inside_probe_inputs_rejected() -> None:
    """`requiredStdoutPattern` (missing the trailing `s`) must reject,
    not silently disappear with an `passed: true` outcome."""
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "terminal",
                "inputs": {
                    "exitCode": 0,
                    "stdout": "",
                    "stderr": "",
                    "requiredStdoutPattern": "x",  # typo
                },
            }
        )


def test_regexp_string_form_compiles() -> None:
    suite = _suite(
        {
            "kind": "terminal",
            "inputs": {
                "exitCode": 0,
                "stdout": "trace.foo",
                "stderr": "",
                "requiredStdoutPatterns": [r"^trace\."],
            },
        }
    )
    result = run_contract_probe_suite(suite)
    assert result.passed is True


def test_regexp_object_form_with_flags_compiles() -> None:
    suite = _suite(
        {
            "kind": "terminal",
            "inputs": {
                "exitCode": 0,
                "stdout": "TRACE.foo",
                "stderr": "",
                "requiredStdoutPatterns": [{"source": "trace", "flags": "i"}],
            },
        }
    )
    result = run_contract_probe_suite(suite)
    assert result.passed is True


def test_invalid_regexp_surfaces_validation_error() -> None:
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "terminal",
                "inputs": {
                    "exitCode": 0,
                    "stdout": "",
                    "stderr": "",
                    "requiredStdoutPatterns": ["[invalid"],
                },
            }
        )


def test_iso_date_string_parses_to_datetime() -> None:
    suite = _suite(
        {
            "kind": "cleanup",
            "inputs": {
                "entries": [{"path": "a.lock", "mtime": "2026-01-01T00:00:00Z"}],
                "now": "2026-06-01T00:00:00Z",
                "maxLockfileAgeMs": 1_000,
            },
        }
    )
    result = run_contract_probe_suite(suite)
    assert result.passed is False
    assert result.results[0].kind == "cleanup"
    assert any(f.kind == "stale-lockfile" for f in result.results[0].failures)


def test_malformed_date_rejected() -> None:
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "cleanup",
                "inputs": {
                    "entries": [{"path": "a.lock", "mtime": "not-a-date"}],
                },
            }
        )


# ---------------------------------------------------------------------------
# exhaustive 7-kind dispatch
# ---------------------------------------------------------------------------


def test_exhaustive_seven_kind_dispatch_all_pass() -> None:
    suite = _suite(
        {
            "kind": "directory",
            "label": "d",
            "inputs": {"presentFiles": ["a"], "requiredFiles": ["a"], "allowedFiles": ["a"]},
        },
        {
            "kind": "terminal",
            "label": "t",
            "inputs": {"exitCode": 0, "stdout": "ok", "stderr": ""},
        },
        {
            "kind": "service",
            "label": "svc",
            "inputs": {
                "observed": [{"host": "127.0.0.1", "port": 8000}],
                "required": [{"host": "127.0.0.1", "port": 8000}],
            },
        },
        {
            "kind": "artifact",
            "label": "art",
            "inputs": {"path": "out.json", "content": '{"ok": true}'},
        },
        {"kind": "cleanup", "label": "cl", "inputs": {"entries": [{"path": "a.txt"}]}},
        {"kind": "media", "label": "m", "inputs": {"path": "x.bin"}},
        {
            "kind": "distributed",
            "label": "dist",
            "inputs": {"ranks": [{"rank": 0}], "worldSize": 1},
        },
    )
    result = run_contract_probe_suite(suite)
    assert result.passed is True
    kinds = [r.kind for r in result.results]
    assert kinds == [
        "directory",
        "terminal",
        "service",
        "artifact",
        "cleanup",
        "media",
        "distributed",
    ]
    # Labels round-trip into the result envelope.
    assert [r.label for r in result.results] == ["d", "t", "svc", "art", "cl", "m", "dist"]


def test_suite_passed_is_and_across_probes() -> None:
    suite = _suite(
        {
            "kind": "terminal",
            "inputs": {"exitCode": 0, "stdout": "", "stderr": ""},
        },
        {
            "kind": "terminal",
            "inputs": {"exitCode": 1, "stdout": "", "stderr": ""},  # fails
        },
    )
    result = run_contract_probe_suite(suite)
    assert result.passed is False
    assert result.results[0].passed is True
    assert result.results[1].passed is False


def test_failure_entries_preserve_kind_specific_typed_fields() -> None:
    """Each result variant preserves the probe's typed failure fields.
    Mirrors the TS discriminated-union shape."""
    suite = _suite(
        {
            "kind": "distributed",
            "label": "x",
            "inputs": {
                "ranks": [
                    {"rank": 0, "observations": {"loss": "0.1"}},
                    {"rank": 1, "observations": {"loss": "0.2"}},
                ],
                "mustMatchAcrossRanks": ["loss"],
            },
        }
    )
    result = run_contract_probe_suite(suite)
    distributed = result.results[0]
    assert distributed.kind == "distributed"
    diverge = [f for f in distributed.failures if f.kind == "rank-divergence"]
    assert diverge and diverge[0].key == "loss"


# ---------------------------------------------------------------------------
# file loader
# ---------------------------------------------------------------------------


def test_load_contract_probe_suite_round_trips_a_json_file(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "probes": [
                    {
                        "kind": "terminal",
                        "inputs": {"exitCode": 0, "stdout": "ok", "stderr": ""},
                    }
                ],
            }
        )
    )
    suite = load_contract_probe_suite(suite_path)
    result = run_contract_probe_suite(suite)
    assert result.passed is True


def test_load_contract_probe_suite_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_contract_probe_suite(tmp_path / "nope.json")


def test_load_contract_probe_suite_malformed_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        load_contract_probe_suite(bad)


# ---------------------------------------------------------------------------
# helpers anchor
# ---------------------------------------------------------------------------


def test_utc_datetime_round_trips() -> None:
    """Sanity: cleanup mtime parsing actually puts a tz-aware datetime
    into the underlying probe inputs."""
    suite = _suite(
        {
            "kind": "cleanup",
            "inputs": {
                "entries": [{"path": "a.lock", "mtime": "2026-01-01T00:00:00Z"}],
                "now": "2026-01-01T00:00:00Z",
                "maxLockfileAgeMs": 60_000,
            },
        }
    )
    result = run_contract_probe_suite(suite)
    # mtime equals now -> no stale-lockfile failure.
    assert result.passed is True
    # And the round-trip preserved tz-awareness.
    assert datetime(2026, 1, 1, tzinfo=UTC).tzinfo is not None
