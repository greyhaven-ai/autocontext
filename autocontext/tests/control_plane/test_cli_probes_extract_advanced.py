"""AC-728 `autoctx probes extract` advanced kinds parity tests (slice 6).

Mirrors the slice-6/TS PR #993 test surface. Covers
cleanup / media / distributed observation+expectation join, orphan
rejection, duplicate-per-path rejection (media), and the slice-2 P2
parity rank-scoped empty-ranks rejection.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocontext.control_plane.contract_probes import (
    ContractProbeSuiteSchema,
    run_contract_probe_suite,
)
from autocontext.control_plane.contract_probes.extract import (
    HarnessTraceSchema,
    extract_contract_probe_suite,
)

# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_cleanup_observation_only_emits_default_probe() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {"cleanup": {"entries": [{"path": "src/main.py"}]}},
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    assert suite_dict["probes"][0]["kind"] == "cleanup"
    # No expectation-side fields injected.
    inputs = suite_dict["probes"][0]["inputs"]
    assert "maxLockfileAgeMs" not in inputs
    assert "forbidSymlinks" not in inputs


def test_cleanup_expectation_without_observation_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {},
                "expectations": {"cleanup": {"forbidSymlinks": True}},
            }
        )
    assert "expectations.cleanup" in str(excinfo.value)


def test_cleanup_full_join_round_trip_through_runner() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {
                "cleanup": {
                    "entries": [
                        {"path": "data/run.lock", "mtime": "2026-01-01T00:00:00Z"},
                        {"path": "solution.txt"},
                    ]
                }
            },
            "expectations": {
                "cleanup": {
                    "now": "2026-01-01T00:00:30Z",
                    "maxLockfileAgeMs": 60_000,
                    "forbidSymlinks": False,
                }
            },
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    result = run_contract_probe_suite(suite)
    assert result.passed is True


# ---------------------------------------------------------------------------
# media
# ---------------------------------------------------------------------------


def test_media_observation_only_emits_no_op_probe() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {
                "media": [{"path": "img.png", "width": 100, "height": 50}],
            },
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    inputs = suite_dict["probes"][0]["inputs"]
    assert inputs["path"] == "img.png"
    assert inputs["width"] == 100
    # No expectation fields populated.
    assert "expectedWidth" not in inputs
    assert "minByteSize" not in inputs


def test_media_expectation_without_observation_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {},
                "expectations": {"media": [{"path": "img.png", "expectedWidth": 100}]},
            }
        )
    assert "expectations.media" in str(excinfo.value)


def test_media_expectation_with_unknown_path_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {"media": [{"path": "img.png"}]},
                "expectations": {"media": [{"path": "other.png", "expectedWidth": 10}]},
            }
        )
    assert "other.png" in str(excinfo.value)


def test_duplicate_per_media_expectation_rejected() -> None:
    """Same Map-keyed-by-path collision as artifacts; reject at parse
    time rather than silently overwriting an earlier entry."""
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {"media": [{"path": "img.png"}]},
                "expectations": {
                    "media": [
                        {"path": "img.png", "expectedWidth": 100},
                        {"path": "img.png", "expectedWidth": 200},
                    ],
                },
            }
        )
    assert "duplicate" in str(excinfo.value).lower()


def test_media_full_join_round_trip_through_runner() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {
                "media": [
                    {
                        "path": "img.png",
                        "headerBytes": [137, 80, 78, 71],
                        "width": 100,
                        "height": 50,
                        "byteSize": 4096,
                    }
                ]
            },
            "expectations": {
                "media": [
                    {
                        "path": "img.png",
                        "expectedMagicBytes": [137, 80, 78, 71],
                        "expectedWidth": 100,
                        "expectedHeight": 50,
                        "minByteSize": 1,
                        "maxByteSize": 8192,
                    }
                ]
            },
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    result = run_contract_probe_suite(suite)
    assert result.passed is True


# ---------------------------------------------------------------------------
# distributed
# ---------------------------------------------------------------------------


def test_distributed_observation_only_emits_probe_with_ranks() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {
                "distributed": {"worldSize": 2, "ranks": [{"rank": 0}, {"rank": 1}]},
            },
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    inputs = suite_dict["probes"][0]["inputs"]
    assert inputs["worldSize"] == 2
    assert [r["rank"] for r in inputs["ranks"]] == [0, 1]
    assert "expectedSteps" not in inputs


def test_distributed_expectation_without_observation_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {},
                "expectations": {"distributed": {"expectedWorldSize": 2}},
            }
        )
    assert "expectations.distributed" in str(excinfo.value)


def test_rank_scoped_expected_steps_with_zero_ranks_rejected() -> None:
    """PR #1005 review (P2) parity at the trace-extraction layer.

    A rank-scoped expectation with zero rank reports would otherwise
    pass vacuously when run; the extractor surfaces this at parse
    time so a broken extractor cannot silently weaken the contract.
    """
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {"distributed": {"ranks": []}},
                "expectations": {"distributed": {"expectedSteps": 100}},
            }
        )
    assert "expectedSteps" in str(excinfo.value)


def test_rank_scoped_must_match_with_zero_ranks_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {"distributed": {"ranks": []}},
                "expectations": {"distributed": {"mustMatchAcrossRanks": ["hash"]}},
            }
        )
    assert "mustMatchAcrossRanks" in str(excinfo.value)


def test_non_rank_scoped_expected_world_size_with_zero_ranks_accepted_at_extraction() -> None:
    """The extractor accepts `expectedWorldSize` without ranks (the
    non-rank-scoped expectation does not require rank reports). The
    resulting suite still verifies rank coverage at run time; this
    test only asserts the trace-validation layer does not reject the
    extraction.
    """
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {"distributed": {"worldSize": 4, "ranks": []}},
            "expectations": {"distributed": {"expectedWorldSize": 4}},
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    # The suite parses cleanly: the rank-scoped expectations
    # (`expectedSteps`, `mustMatchAcrossRanks`) are absent so the
    # slice-1 audit invariant is satisfied at extraction.
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    assert suite.probes[0].kind == "distributed"


def test_distributed_full_join_round_trip_through_runner() -> None:
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "observations": {
                "distributed": {
                    "worldSize": 2,
                    "ranks": [
                        {"rank": 0, "steps": 10, "observations": {"loss": "0.1"}},
                        {"rank": 1, "steps": 10, "observations": {"loss": "0.1"}},
                    ],
                }
            },
            "expectations": {
                "distributed": {
                    "expectedWorldSize": 2,
                    "expectedSteps": 10,
                    "mustMatchAcrossRanks": ["loss"],
                }
            },
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    result = run_contract_probe_suite(suite)
    assert result.passed is True


# ---------------------------------------------------------------------------
# end-to-end seven-kind round-trip
# ---------------------------------------------------------------------------


def test_seven_kind_extraction_round_trip_passes_runner() -> None:
    """Slice-6 exit invariant: every probe kind extractable end-to-end
    in one trace."""
    trace = HarnessTraceSchema.model_validate(
        {
            "schema_version": 1,
            "label": "demo",
            "observations": {
                "terminal": {"exitCode": 0, "stdout": "solution.txt", "stderr": ""},
                "workdir": {"presentFiles": ["solution.txt"]},
                "services": [{"host": "127.0.0.1", "port": 8000}],
                "artifacts": [{"path": "solution.txt", "content": "answer\n"}],
                "cleanup": {"entries": [{"path": "solution.txt"}]},
                "media": [{"path": "img.png", "width": 100}],
                "distributed": {"worldSize": 1, "ranks": [{"rank": 0, "steps": 1}]},
            },
            "expectations": {
                "terminal": {"requiredStdoutPatterns": [r"solution\.txt"]},
                "directory": {
                    "requiredFiles": ["solution.txt"],
                    "allowedFiles": ["solution.txt"],
                },
                "services": {"required": [{"host": "127.0.0.1", "port": 8000}]},
                "artifacts": [{"path": "solution.txt", "requiredSubstrings": ["answer"]}],
                "cleanup": {"maxLockfileAgeMs": 1000},
                "media": [{"path": "img.png", "expectedWidth": 100}],
                "distributed": {"expectedWorldSize": 1, "expectedSteps": 1},
            },
        }
    )
    suite_dict = extract_contract_probe_suite(trace)
    assert [p["kind"] for p in suite_dict["probes"]] == [
        "terminal",
        "directory",
        "service",
        "artifact",
        "cleanup",
        "media",
        "distributed",
    ]
    suite = ContractProbeSuiteSchema.model_validate(suite_dict)
    result = run_contract_probe_suite(suite)
    assert result.passed is True
