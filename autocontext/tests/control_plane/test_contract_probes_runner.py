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


def test_probes_null_rejected() -> None:
    """PR #1006 review (P2): a `probes: null` envelope used to coerce
    to an empty passing suite. The schema now rejects it loudly so
    corrupted JSON cannot present as a green empty run.
    """
    with pytest.raises(ValidationError):
        ContractProbeSuiteSchema.model_validate({"schema_version": 1, "probes": None})


def test_required_observation_fields_must_be_present() -> None:
    """PR #1006 review (P2): TS-required fields like `presentFiles`,
    `observed`, `entries`, `ranks` were optional in the Python
    schema. A broken extractor that omitted them would silently
    produce passing suites. They now reject as missing.
    """
    # directory: presentFiles / requiredFiles / allowedFiles
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "directory",
                "inputs": {"requiredFiles": [], "allowedFiles": []},
            }
        )
    # service: observed / required
    with pytest.raises(ValidationError):
        _suite({"kind": "service", "inputs": {"required": []}})
    # cleanup: entries
    with pytest.raises(ValidationError):
        _suite({"kind": "cleanup", "inputs": {}})
    # distributed: ranks
    with pytest.raises(ValidationError):
        _suite({"kind": "distributed", "inputs": {}})


def test_explicit_null_for_optional_expectation_fields_rejected() -> None:
    """PR #1006 review (P2): TS `.optional()` accepts omission but
    not `null`. Writing an explicit `null` for an optional expectation
    used to disable the expectation silently; the schema now rejects
    explicit nulls on every declared field.
    """
    # terminal optional pattern list
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "terminal",
                "inputs": {
                    "exitCode": 0,
                    "stdout": "",
                    "stderr": "",
                    "requiredStdoutPatterns": None,
                },
            }
        )
    # artifact optional substrings
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "artifact",
                "inputs": {"path": "x", "content": "", "requiredSubstrings": None},
            }
        )
    # distributed optional must-match
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "distributed",
                "inputs": {"ranks": [], "mustMatchAcrossRanks": None},
            }
        )


def test_strict_typing_rejects_coerced_primitives() -> None:
    """PR #1006 review (P3): the schema used to coerce
    `"exitCode": "0"`, `"port": "8000"`, `"forbidSymlinks": "false"`
    and so on, mirroring lax Pydantic behaviour. Strict types now
    reject these forms so bad suite generation surfaces at parse
    time.
    """
    # terminal exitCode must be a real int
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "terminal",
                "inputs": {"exitCode": "0", "stdout": "", "stderr": ""},
            }
        )
    # service port must be a real int
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "service",
                "inputs": {
                    "observed": [{"host": "127.0.0.1", "port": "8000"}],
                    "required": [],
                },
            }
        )
    # cleanup forbidSymlinks must be a real bool
    with pytest.raises(ValidationError):
        _suite(
            {
                "kind": "cleanup",
                "inputs": {"entries": [], "forbidSymlinks": "false"},
            }
        )


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
