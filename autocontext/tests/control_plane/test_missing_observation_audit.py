"""AC-728 missing-observation invariant audit (Python parity, slice 7).

Closes the AC-728 PY parity slice plan. Mirrors the TS PR #1000-area
close-out audit. Consolidates and broadens the pinning tests that
previous slices scattered across per-probe and per-extractor test
files into a single audit surface so any future refactor that
weakens the invariant surfaces immediately.

The invariant has three enforcement layers:

1. **Base probe shapes (slice 1, ``_base.py``)** -- every observation
   field is non-optional, so a declared expectation against an
   empty observation MUST surface a kind-specific failure
   (`unexpected-exit-code`, `missing-stdout-pattern`,
   `missing-file`, `missing-endpoint`, etc.) rather than silently
   pass. No ``missing-observation`` failure kind exists or is
   needed for these four.

2. **Advanced probe shapes (slice 2, ``_advanced.py``)** -- some
   observation fields are optional by design (cleanup
   ``symlink_target`` / ``mtime``; distributed per-rank ``steps``
   / ``observations`` / top-level ``world_size``; every media
   field). For each such field, the probe MUST emit a
   ``missing-observation`` failure when a declared expectation
   would otherwise be vacuously satisfied. Including the PR #1005
   review P2 fix: rank-scoped distributed expectations against
   zero rank reports must fail loudly.

3. **Trace extractor (slices 5 + 6, ``extract.py``)** -- every
   orphan expectation (declared without its matching observation)
   MUST reject at trace-validation time so a broken extractor
   cannot ship a vacuously-passing suite downstream.

The audit walks all three layers programmatically so any future
loosening of a field or removal of a guard surfaces a failing test
rather than silently weakening the contract.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.control_plane.contract_probes import (
    ArtifactContractProbeInputs,
    CleanupContractProbeInputs,
    CleanupFileEntry,
    DirectoryContractProbeInputs,
    DistributedContractProbeInputs,
    DistributedRankReport,
    MediaContractProbeInputs,
    ServiceContractProbeInputs,
    ServiceEndpointObservation,
    TerminalContractProbeInputs,
    probe_artifact_contract,
    probe_cleanup_contract,
    probe_directory_contract,
    probe_distributed_contract,
    probe_media_contract,
    probe_service_contract,
    probe_terminal_contract,
)
from autocontext.control_plane.contract_probes.extract import HarnessTraceSchema

# ---------------------------------------------------------------------------
# Layer 1: base probe shapes -- expectation against empty observation must
# surface a kind-specific failure (no `missing-observation` needed).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_cls", "probe_fn", "kwargs", "expected_failure_kinds"),
    [
        (
            DirectoryContractProbeInputs,
            probe_directory_contract,
            {
                "present_files": (),
                "required_files": ("solution.txt",),
                "allowed_files": (),
            },
            {"missing-file"},
        ),
        (
            TerminalContractProbeInputs,
            probe_terminal_contract,
            {
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "required_stdout_patterns": (re.compile(r"trace\."),),
            },
            {"missing-stdout-pattern"},
        ),
        (
            ServiceContractProbeInputs,
            probe_service_contract,
            {
                "observed": (),
                "required": (ServiceEndpointObservation(host="127.0.0.1", port=8000),),
            },
            {"missing-endpoint"},
        ),
        (
            ArtifactContractProbeInputs,
            probe_artifact_contract,
            {"path": "empty.txt", "content": "", "required_substrings": ("expected",)},
            {"missing-substring"},
        ),
    ],
)
def test_base_probe_expectation_against_empty_observation_fails_loudly(
    input_cls: type,
    probe_fn: Callable[..., Any],
    kwargs: dict[str, object],
    expected_failure_kinds: set[str],
) -> None:
    """Pin the slice-1 audit invariant: empty observations cannot satisfy
    a declared expectation silently. The failure kind is the
    probe-specific one (no ``missing-observation`` family member exists
    on these four)."""
    inputs = input_cls(**kwargs)
    result = probe_fn(inputs)
    assert result.passed is False
    observed = {f.kind for f in result.failures}
    assert expected_failure_kinds.issubset(observed)
    # Crucially, no spurious `missing-observation` kind appears on
    # these four — that kind belongs to the advanced probes only.
    assert "missing-observation" not in observed


# ---------------------------------------------------------------------------
# Layer 2: advanced probe shapes -- optional observation field gated by a
# declared expectation must emit a `missing-observation` failure.
# ---------------------------------------------------------------------------


def test_cleanup_symlink_target_missing_under_allowlist_emits_missing_observation() -> None:
    """``symlink_target`` is optional on ``CleanupFileEntry``; declaring
    ``allowed_symlink_targets`` against an entry that omits the target
    must surface ``missing-observation``."""
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(CleanupFileEntry(path="link", is_symlink=True),),
            allowed_symlink_targets=("/var/run/known",),
        )
    )
    assert result.passed is False
    assert any(f.kind == "missing-observation" for f in result.failures)


def test_cleanup_lockfile_mtime_missing_under_age_contract_emits_missing_observation() -> None:
    """``mtime`` is optional on ``CleanupFileEntry``; declaring
    ``max_lockfile_age_ms`` against an entry that omits ``mtime`` must
    surface ``missing-observation``."""
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(CleanupFileEntry(path="a.lock"),),
            max_lockfile_age_ms=60_000,
        )
    )
    assert result.passed is False
    assert any(f.kind == "missing-observation" for f in result.failures)


def test_distributed_steps_missing_under_expected_steps_emits_missing_observation() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(DistributedRankReport(rank=0),),
            expected_steps=100,
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "missing-observation"


def test_distributed_rank_observations_missing_under_must_match_emits_missing_observation() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(DistributedRankReport(rank=0),),
            must_match_across_ranks=("loss",),
        )
    )
    assert result.passed is False
    assert result.failures[0].kind == "missing-observation"


def test_distributed_world_size_missing_under_expected_world_size_emits_missing_observation() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(),
            expected_world_size=4,
        )
    )
    assert result.passed is False
    assert any(f.kind == "missing-observation" for f in result.failures)


def test_distributed_zero_rank_reports_under_rank_scoped_expectation_emits_missing_observation() -> None:
    """PR #1005 review (P2) fix: rank-scoped ``expected_steps`` /
    ``must_match_across_ranks`` declared against zero rank reports
    must surface ``missing-observation``. Without this guard a broken
    extractor that omits every rank report would silently satisfy
    declared rank-scoped expectations."""
    for input_kwargs in (
        {"ranks": (), "expected_steps": 100},
        {"ranks": (), "must_match_across_ranks": ("hash",)},
    ):
        result = probe_distributed_contract(DistributedContractProbeInputs(**input_kwargs))
        assert result.passed is False
        assert any(f.kind == "missing-observation" for f in result.failures)


@pytest.mark.parametrize(
    "expectation_kwargs",
    [
        {"expected_magic_bytes": (0x89,)},
        {"expected_width": 10},
        {"expected_height": 10},
        {"min_byte_size": 10},
        {"max_byte_size": 10},
        {"expected_column_count": 5},
        {"required_column_names": ("id",)},
        {"expected_line_count": 5},
    ],
)
def test_media_expectation_without_matching_observation_emits_missing_observation(
    expectation_kwargs: dict[str, Any],
) -> None:
    """Every media expectation has a matching observation field. When the
    expectation is declared but the observation is omitted the probe
    MUST emit ``missing-observation``. This consolidates the per-field
    pinning into one parametrised property."""
    result = probe_media_contract(MediaContractProbeInputs(path="x", **expectation_kwargs))
    assert result.passed is False
    assert result.failures[0].kind == "missing-observation"


# ---------------------------------------------------------------------------
# Layer 3: trace extractor -- orphan expectations reject at parse time.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expectations_section",
    [
        {"terminal": {"expectedExitCode": 0}},
        {"directory": {"requiredFiles": ["solution.txt"]}},
        {"services": {"required": [{"host": "127.0.0.1", "port": 8000}]}},
        {"artifacts": [{"path": "out.json"}]},
        {"cleanup": {"forbidSymlinks": True}},
        {"media": [{"path": "img.png", "expectedWidth": 100}]},
        {"distributed": {"expectedWorldSize": 2}},
    ],
)
def test_trace_orphan_expectation_rejects_at_parse_time(
    expectations_section: dict[str, object],
) -> None:
    """Every section in ``expectations`` without its matching
    observation rejects at parse time. Mirrors the
    ``superRefine`` behaviour of the TS extractor."""
    with pytest.raises(ValidationError):
        HarnessTraceSchema.model_validate(
            {
                "schema_version": 1,
                "observations": {},
                "expectations": expectations_section,
            }
        )


def test_seven_kind_coverage_matrix_is_exhaustive() -> None:
    """Sanity: the slice-7 audit covers every probe kind. If a new
    probe kind lands without a matching audit entry this test
    surfaces it via the registry walk."""
    from autocontext.control_plane.contract_probes import ContractProbeKind

    covered = {
        "directory",
        "terminal",
        "service",
        "artifact",
        "cleanup",
        "media",
        "distributed",
    }
    # ContractProbeKind is a Literal; collect its values.
    declared = set(ContractProbeKind.__args__)  # type: ignore[attr-defined]
    assert covered == declared
