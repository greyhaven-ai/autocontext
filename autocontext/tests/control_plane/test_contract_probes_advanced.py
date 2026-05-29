"""AC-728 advanced contract probes (Python parity, slice 2) tests.

Mirrors the cleanup / distributed / media test surfaces from
``ts/tests/control-plane/contract-probes/contract-probes.test.ts``.
The missing-observation invariant gets specific pinning tests for
each probe (a declared expectation without its matching observation
must fail loudly).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from autocontext.control_plane.contract_probes import (
    CleanupContractProbeInputs,
    CleanupFileEntry,
    DistributedContractProbeInputs,
    DistributedRankReport,
    MediaContractProbeInputs,
    probe_cleanup_contract,
    probe_distributed_contract,
    probe_media_contract,
)

# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_cleanup_probe_passes_clean_workdir() -> None:
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(CleanupFileEntry(path="solution.txt"),),
        )
    )
    assert result.passed is True


def test_cleanup_probe_flags_broken_symlinks() -> None:
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(CleanupFileEntry(path="link", is_symlink=True, symlink_broken=True),),
        )
    )
    assert result.failures[0].kind == "broken-symlink"


def test_cleanup_probe_flags_forbidden_symlinks() -> None:
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(CleanupFileEntry(path="link", is_symlink=True, symlink_target="/tmp/x"),),
            forbid_symlinks=True,
        )
    )
    assert result.failures[0].kind == "stray-symlink"


def test_cleanup_probe_emits_missing_observation_for_symlink_without_target() -> None:
    """PR #985 review lesson, retrofitted: a declared allowlist
    expectation without its symlink_target observation must fail
    loudly, not pass silently against a broken extractor."""
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(CleanupFileEntry(path="link", is_symlink=True),),
            allowed_symlink_targets=("/var/run/known",),
        )
    )
    assert result.failures[0].kind == "missing-observation"


def test_cleanup_probe_flags_stale_lockfile_when_mtime_older_than_max_age() -> None:
    now = datetime(2026, 5, 29, tzinfo=UTC)
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(CleanupFileEntry(path="a.lock", mtime=now - timedelta(hours=1)),),
            now=now,
            max_lockfile_age_ms=60_000,
        )
    )
    assert result.failures[0].kind == "stale-lockfile"


def test_cleanup_probe_emits_missing_observation_for_lockfile_without_mtime() -> None:
    """PR #985 review lesson: a declared max_lockfile_age_ms contract
    against a lockfile entry without mtime fails loudly, not silently
    via a stat-failing extractor."""
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(CleanupFileEntry(path="a.lock"),),
            max_lockfile_age_ms=60_000,
        )
    )
    assert result.failures[0].kind == "missing-observation"


def test_cleanup_probe_flags_sidecars_and_backups() -> None:
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(
                CleanupFileEntry(path=".DS_Store"),
                CleanupFileEntry(path="notes.bak"),
            ),
        )
    )
    kinds = {f.kind for f in result.failures}
    assert {"stray-sidecar", "stray-backup"}.issubset(kinds)


def test_cleanup_probe_ignored_patterns_short_circuit_before_classification() -> None:
    result = probe_cleanup_contract(
        CleanupContractProbeInputs(
            entries=(CleanupFileEntry(path=".DS_Store"),),
            ignored_patterns=(re.compile(r"\.DS_Store$"),),
        )
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# distributed
# ---------------------------------------------------------------------------


def test_distributed_probe_passes_when_all_ranks_report() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(
                DistributedRankReport(rank=0, steps=10),
                DistributedRankReport(rank=1, steps=10),
            ),
            world_size=2,
            expected_steps=10,
        )
    )
    assert result.passed is True


def test_distributed_probe_flags_wrong_world_size() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(),
            world_size=4,
            expected_world_size=8,
        )
    )
    assert any(f.kind == "wrong-world-size" for f in result.failures)


def test_distributed_probe_flags_missing_rank_when_world_size_known() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(DistributedRankReport(rank=0),),
            world_size=2,
        )
    )
    assert any(f.kind == "missing-rank" and f.rank == 1 for f in result.failures)


def test_distributed_probe_flags_duplicate_rank() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(
                DistributedRankReport(rank=0),
                DistributedRankReport(rank=0),
            ),
        )
    )
    assert any(f.kind == "duplicate-rank" for f in result.failures)


def test_distributed_probe_flags_rank_divergence_with_distinct_values() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(
                DistributedRankReport(rank=0, observations={"loss": "0.1"}),
                DistributedRankReport(rank=1, observations={"loss": "0.2"}),
            ),
            must_match_across_ranks=("loss",),
        )
    )
    diverge = [f for f in result.failures if f.kind == "rank-divergence"]
    assert diverge and "0.1" in diverge[0].message and "0.2" in diverge[0].message


def test_distributed_probe_emits_missing_observation_for_steps_when_expected() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(DistributedRankReport(rank=0),),
            expected_steps=100,
        )
    )
    assert result.failures[0].kind == "missing-observation"


def test_distributed_probe_emits_missing_observation_for_must_match_key() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(DistributedRankReport(rank=0),),
            must_match_across_ranks=("hash",),
        )
    )
    assert result.failures[0].kind == "missing-observation"


def test_distributed_probe_emits_missing_observation_when_expected_steps_declared_without_any_ranks() -> None:
    """PR #1005 review (P2): a broken extractor that omits all rank
    reports must not be able to satisfy declared rank-scoped
    expectations by silence. `expected_steps` against zero rank
    reports fails loudly with `missing-observation`.
    """
    result = probe_distributed_contract(DistributedContractProbeInputs(ranks=(), expected_steps=100))
    assert result.passed is False
    assert any(f.kind == "missing-observation" for f in result.failures)


def test_distributed_probe_emits_missing_observation_when_must_match_declared_without_any_ranks() -> None:
    """PR #1005 review (P2): same shape for `must_match_across_ranks`.
    Each declared key surfaces its own `missing-observation` so a
    multi-key expectation does not collapse to a single failure."""
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(),
            must_match_across_ranks=("hash", "loss"),
        )
    )
    assert result.passed is False
    keys_with_missing = {f.key for f in result.failures if f.kind == "missing-observation"}
    assert keys_with_missing == {"hash", "loss"}


def test_distributed_probe_emits_missing_observation_for_world_size() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(),
            expected_world_size=4,
        )
    )
    assert result.failures[0].kind == "missing-observation"


def test_distributed_probe_passes_world_size_one_degenerate_case() -> None:
    result = probe_distributed_contract(
        DistributedContractProbeInputs(
            ranks=(DistributedRankReport(rank=0, steps=1),),
            world_size=1,
            expected_world_size=1,
            expected_steps=1,
        )
    )
    assert result.passed is True


# ---------------------------------------------------------------------------
# media
# ---------------------------------------------------------------------------


def test_media_probe_passes_when_all_expectations_match() -> None:
    result = probe_media_contract(
        MediaContractProbeInputs(
            path="img.png",
            header_bytes=(0x89, 0x50, 0x4E, 0x47),
            expected_magic_bytes=(0x89, 0x50, 0x4E, 0x47),
            width=10,
            height=10,
            expected_width=10,
            expected_height=10,
            byte_size=512,
            min_byte_size=1,
            max_byte_size=1024,
        )
    )
    assert result.passed is True


def test_media_probe_flags_wrong_magic_bytes() -> None:
    result = probe_media_contract(
        MediaContractProbeInputs(
            path="img.png",
            header_bytes=(0x00, 0x00, 0x00, 0x00),
            expected_magic_bytes=(0x89, 0x50, 0x4E, 0x47),
        )
    )
    assert result.failures[0].kind == "wrong-magic-bytes"


def test_media_probe_flags_byte_size_bounds_violation() -> None:
    too_small = probe_media_contract(
        MediaContractProbeInputs(
            path="x.bin",
            byte_size=10,
            min_byte_size=100,
        )
    )
    assert too_small.failures[0].kind == "wrong-byte-size"

    too_big = probe_media_contract(
        MediaContractProbeInputs(
            path="x.bin",
            byte_size=10_000,
            max_byte_size=100,
        )
    )
    assert too_big.failures[0].kind == "wrong-byte-size"


def test_media_probe_flags_missing_required_columns() -> None:
    result = probe_media_contract(
        MediaContractProbeInputs(
            path="data.csv",
            column_names=("id", "name"),
            required_column_names=("id", "missing"),
        )
    )
    missing = [f for f in result.failures if f.kind == "missing-column"]
    assert {f.path for f in missing} == {"missing"}


def test_media_probe_flags_wrong_line_count() -> None:
    result = probe_media_contract(
        MediaContractProbeInputs(
            path="data.jsonl",
            line_count=3,
            expected_line_count=5,
        )
    )
    assert result.failures[0].kind == "wrong-line-count"


def test_media_probe_emits_missing_observation_when_expectation_lacks_observation() -> None:
    """Every declared expectation without its observation must fail."""
    cases = [
        MediaContractProbeInputs(path="x", expected_magic_bytes=(0x89,)),
        MediaContractProbeInputs(path="x", expected_width=10),
        MediaContractProbeInputs(path="x", expected_height=10),
        MediaContractProbeInputs(path="x", min_byte_size=10),
        MediaContractProbeInputs(path="x", max_byte_size=10),
        MediaContractProbeInputs(path="x", expected_column_count=5),
        MediaContractProbeInputs(path="x", required_column_names=("id",)),
        MediaContractProbeInputs(path="x", expected_line_count=5),
    ]
    for inputs in cases:
        result = probe_media_contract(inputs)
        assert result.passed is False
        assert result.failures[0].kind == "missing-observation"


def test_media_probe_no_expectations_declared_passes() -> None:
    result = probe_media_contract(MediaContractProbeInputs(path="x"))
    assert result.passed is True
