"""AC-728 advanced contract probes (Python parity, slice 2).

Mirrors the cleanup / distributed / media surfaces from
``ts/src/control-plane/contract-probes/index.ts`` (TS PRs #983 / #985
/ #987). All three probes carry explicit ``missing-observation``
failure kinds: a declared expectation without its matching
observation must fail loudly rather than silently pass.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from ._base import _Frozen

__all__ = [
    "CleanupContractFailure",
    "CleanupContractFailureKind",
    "CleanupContractProbeInputs",
    "CleanupContractProbeResult",
    "CleanupFileEntry",
    "DistributedContractFailure",
    "DistributedContractFailureKind",
    "DistributedContractProbeInputs",
    "DistributedContractProbeResult",
    "DistributedRankReport",
    "MediaContractFailure",
    "MediaContractFailureKind",
    "MediaContractProbeInputs",
    "MediaContractProbeResult",
    "probe_cleanup_contract",
    "probe_distributed_contract",
    "probe_media_contract",
]


def _is_ignored(path: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(path) is not None for pattern in patterns)


def _matches_any(path: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(path) is not None for pattern in patterns)


# ---------------------------------------------------------------------------
# cleanup contract probe
# ---------------------------------------------------------------------------

CleanupContractFailureKind = Literal[
    "stray-symlink",
    "broken-symlink",
    "stale-lockfile",
    "stray-sidecar",
    "stray-backup",
    "missing-observation",
]


class CleanupContractFailure(_Frozen):
    kind: CleanupContractFailureKind
    path: str
    message: str


class CleanupFileEntry(_Frozen):
    path: str
    is_symlink: bool = False
    symlink_target: str | None = None
    symlink_broken: bool = False
    mtime: datetime | None = None


class CleanupContractProbeInputs(_Frozen):
    entries: tuple[CleanupFileEntry, ...] = Field(default=())
    now: datetime | None = None
    max_lockfile_age_ms: int | None = None
    lockfile_patterns: tuple[re.Pattern[str], ...] | None = None
    sidecar_patterns: tuple[re.Pattern[str], ...] | None = None
    backup_patterns: tuple[re.Pattern[str], ...] | None = None
    forbid_symlinks: bool = False
    allowed_symlink_targets: tuple[str, ...] | None = None
    ignored_patterns: tuple[re.Pattern[str], ...] = Field(default=())


class CleanupContractProbeResult(_Frozen):
    passed: bool
    failures: tuple[CleanupContractFailure, ...]


_DEFAULT_LOCKFILE_PATTERNS: tuple[re.Pattern[str], ...] = (re.compile(r"\.(lock|lck|pid)$", re.IGNORECASE),)
_DEFAULT_SIDECAR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.sw[op]$", re.IGNORECASE),
    re.compile(r"~$"),
    re.compile(r"(^|/)\.DS_Store$"),
    re.compile(r"(^|/)\.~lock\..*#$"),
)
_DEFAULT_BACKUP_PATTERNS: tuple[re.Pattern[str], ...] = (re.compile(r"\.(bak|orig)$", re.IGNORECASE),)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def probe_cleanup_contract(
    inputs: CleanupContractProbeInputs,
) -> CleanupContractProbeResult:
    ignored = inputs.ignored_patterns
    lockfile_patterns = inputs.lockfile_patterns if inputs.lockfile_patterns is not None else _DEFAULT_LOCKFILE_PATTERNS
    sidecar_patterns = inputs.sidecar_patterns if inputs.sidecar_patterns is not None else _DEFAULT_SIDECAR_PATTERNS
    backup_patterns = inputs.backup_patterns if inputs.backup_patterns is not None else _DEFAULT_BACKUP_PATTERNS
    allowed_symlink_targets = inputs.allowed_symlink_targets
    now = inputs.now if inputs.now is not None else _utc_now()
    max_lockfile_age_ms = inputs.max_lockfile_age_ms
    failures: list[CleanupContractFailure] = []

    for entry in inputs.entries:
        if _is_ignored(entry.path, ignored):
            continue

        if entry.is_symlink:
            if entry.symlink_broken:
                failures.append(
                    CleanupContractFailure(
                        kind="broken-symlink",
                        path=entry.path,
                        message=f"{entry.path} is a broken symlink (target missing)",
                    )
                )
                continue
            if inputs.forbid_symlinks:
                target = entry.symlink_target if entry.symlink_target is not None else "<unknown>"
                failures.append(
                    CleanupContractFailure(
                        kind="stray-symlink",
                        path=entry.path,
                        message=(f"{entry.path} is a symlink (target {target}); symlinks are forbidden by contract"),
                    )
                )
                continue
            if allowed_symlink_targets is not None:
                if entry.symlink_target is None:
                    failures.append(
                        CleanupContractFailure(
                            kind="missing-observation",
                            path=entry.path,
                            message=(
                                f"{entry.path} is a symlink but no symlink_target was supplied; "
                                "cannot evaluate allowed_symlink_targets contract"
                            ),
                        )
                    )
                elif entry.symlink_target not in allowed_symlink_targets:
                    failures.append(
                        CleanupContractFailure(
                            kind="stray-symlink",
                            path=entry.path,
                            message=(f"{entry.path} is a symlink to {entry.symlink_target}; target is not in the allowlist"),
                        )
                    )
            continue

        if _matches_any(entry.path, lockfile_patterns):
            if max_lockfile_age_ms is None:
                failures.append(
                    CleanupContractFailure(
                        kind="stale-lockfile",
                        path=entry.path,
                        message=f"{entry.path} is a leftover lockfile",
                    )
                )
            elif entry.mtime is None:
                failures.append(
                    CleanupContractFailure(
                        kind="missing-observation",
                        path=entry.path,
                        message=(
                            f"{entry.path} matched a lockfile pattern but no mtime was supplied; "
                            "cannot evaluate max_lockfile_age_ms contract"
                        ),
                    )
                )
            elif _epoch_ms(now) - _epoch_ms(entry.mtime) > max_lockfile_age_ms:
                failures.append(
                    CleanupContractFailure(
                        kind="stale-lockfile",
                        path=entry.path,
                        message=f"{entry.path} is a lockfile older than {max_lockfile_age_ms}ms",
                    )
                )
            continue

        if _matches_any(entry.path, sidecar_patterns):
            failures.append(
                CleanupContractFailure(
                    kind="stray-sidecar",
                    path=entry.path,
                    message=f"{entry.path} is an editor/OS sidecar leftover",
                )
            )
            continue

        if _matches_any(entry.path, backup_patterns):
            failures.append(
                CleanupContractFailure(
                    kind="stray-backup",
                    path=entry.path,
                    message=f"{entry.path} is a backup copy leftover",
                )
            )
            continue

    return CleanupContractProbeResult(passed=not failures, failures=tuple(failures))


# ---------------------------------------------------------------------------
# distributed contract probe
# ---------------------------------------------------------------------------

DistributedContractFailureKind = Literal[
    "wrong-world-size",
    "missing-rank",
    "duplicate-rank",
    "rank-divergence",
    "wrong-step-count",
    "missing-observation",
]


class DistributedContractFailure(_Frozen):
    kind: DistributedContractFailureKind
    message: str
    rank: int | None = None
    key: str | None = None


class DistributedRankReport(_Frozen):
    rank: int
    steps: int | None = None
    observations: dict[str, str] | None = None


class DistributedContractProbeInputs(_Frozen):
    ranks: tuple[DistributedRankReport, ...] = Field(default=())
    world_size: int | None = None
    expected_world_size: int | None = None
    expected_steps: int | None = None
    must_match_across_ranks: tuple[str, ...] | None = None


class DistributedContractProbeResult(_Frozen):
    passed: bool
    failures: tuple[DistributedContractFailure, ...]


def probe_distributed_contract(
    inputs: DistributedContractProbeInputs,
) -> DistributedContractProbeResult:
    failures: list[DistributedContractFailure] = []

    if inputs.expected_world_size is not None:
        if inputs.world_size is None:
            failures.append(
                DistributedContractFailure(
                    kind="missing-observation",
                    message="declared expectation on world_size but no observation was supplied",
                )
            )
        elif inputs.world_size != inputs.expected_world_size:
            failures.append(
                DistributedContractFailure(
                    kind="wrong-world-size",
                    message=(f"observed world size {inputs.world_size} does not match expected {inputs.expected_world_size}"),
                )
            )

    seen_ranks: dict[int, DistributedRankReport] = {}
    for report in inputs.ranks:
        if report.rank in seen_ranks:
            failures.append(
                DistributedContractFailure(
                    kind="duplicate-rank",
                    rank=report.rank,
                    message=f"rank {report.rank} reported more than once",
                )
            )
            continue
        seen_ranks[report.rank] = report

    if inputs.world_size is not None:
        for r in range(inputs.world_size):
            if r not in seen_ranks:
                failures.append(
                    DistributedContractFailure(
                        kind="missing-rank",
                        rank=r,
                        message=f"rank {r} did not report (world size {inputs.world_size})",
                    )
                )

    if inputs.expected_steps is not None:
        # PR #1005 review (P2): a declared rank-scoped expectation
        # without any rank reports must fail loudly. Iterating
        # `seen_ranks.values()` alone would let a broken extractor
        # satisfy `expected_steps` by omitting every rank report.
        if not seen_ranks:
            failures.append(
                DistributedContractFailure(
                    kind="missing-observation",
                    message=("declared expectation on expected_steps but no rank reports were supplied"),
                )
            )
        for report in seen_ranks.values():
            if report.steps is None:
                failures.append(
                    DistributedContractFailure(
                        kind="missing-observation",
                        rank=report.rank,
                        message=(f"rank {report.rank} declared step-count expectation but no steps observation was supplied"),
                    )
                )
            elif report.steps != inputs.expected_steps:
                failures.append(
                    DistributedContractFailure(
                        kind="wrong-step-count",
                        rank=report.rank,
                        message=(f"rank {report.rank} ran {report.steps} steps; expected {inputs.expected_steps}"),
                    )
                )

    if inputs.must_match_across_ranks is not None and not seen_ranks:
        # PR #1005 review (P2): same shape as expected_steps above.
        # Declared cross-rank expectations with zero rank reports must
        # fail loudly, not pass silently.
        for key in inputs.must_match_across_ranks:
            failures.append(
                DistributedContractFailure(
                    kind="missing-observation",
                    key=key,
                    message=(f"declared must_match_across_ranks expectation on '{key}' but no rank reports were supplied"),
                )
            )

    if inputs.must_match_across_ranks is not None and seen_ranks:
        for key in inputs.must_match_across_ranks:
            values_by_rank: dict[int, str] = {}
            any_missing = False
            for report in seen_ranks.values():
                observations = report.observations or {}
                value = observations.get(key)
                if value is None:
                    failures.append(
                        DistributedContractFailure(
                            kind="missing-observation",
                            rank=report.rank,
                            key=key,
                            message=f"rank {report.rank} did not report observation '{key}'",
                        )
                    )
                    any_missing = True
                    continue
                values_by_rank[report.rank] = value
            if any_missing:
                continue
            distinct_values = set(values_by_rank.values())
            if len(distinct_values) > 1:
                rendered = ", ".join(json.dumps(v) for v in sorted(distinct_values))
                failures.append(
                    DistributedContractFailure(
                        kind="rank-divergence",
                        key=key,
                        message=f"ranks disagree on '{key}': observed distinct values {rendered}",
                    )
                )

    return DistributedContractProbeResult(passed=not failures, failures=tuple(failures))


# ---------------------------------------------------------------------------
# media / tabular contract probe
# ---------------------------------------------------------------------------

MediaContractFailureKind = Literal[
    "wrong-magic-bytes",
    "wrong-dimensions",
    "wrong-byte-size",
    "wrong-column-count",
    "missing-column",
    "wrong-line-count",
    "missing-observation",
]


class MediaContractFailure(_Frozen):
    kind: MediaContractFailureKind
    path: str
    message: str


class MediaContractProbeInputs(_Frozen):
    path: str
    header_bytes: tuple[int, ...] | None = None
    expected_magic_bytes: tuple[int, ...] | None = None
    width: int | None = None
    height: int | None = None
    expected_width: int | None = None
    expected_height: int | None = None
    byte_size: int | None = None
    min_byte_size: int | None = None
    max_byte_size: int | None = None
    column_count: int | None = None
    expected_column_count: int | None = None
    column_names: tuple[str, ...] | None = None
    required_column_names: tuple[str, ...] | None = None
    line_count: int | None = None
    expected_line_count: int | None = None


class MediaContractProbeResult(_Frozen):
    passed: bool
    failures: tuple[MediaContractFailure, ...]


def _format_bytes(bytes_seq: tuple[int, ...]) -> str:
    return " ".join(f"{b:02x}" for b in bytes_seq)


def probe_media_contract(inputs: MediaContractProbeInputs) -> MediaContractProbeResult:
    failures: list[MediaContractFailure] = []

    def missing_observation(field: str) -> None:
        failures.append(
            MediaContractFailure(
                kind="missing-observation",
                path=inputs.path,
                message=f"{inputs.path} declared expectation on {field} but no observation was supplied",
            )
        )

    if inputs.expected_magic_bytes is not None:
        if inputs.header_bytes is None:
            missing_observation("header_bytes")
        else:
            expected = inputs.expected_magic_bytes
            header = inputs.header_bytes
            matched = len(header) >= len(expected) and all(header[i] == byte for i, byte in enumerate(expected))
            if not matched:
                failures.append(
                    MediaContractFailure(
                        kind="wrong-magic-bytes",
                        path=inputs.path,
                        message=(
                            f"{inputs.path} header {_format_bytes(header[: len(expected)])} "
                            f"does not match expected magic {_format_bytes(expected)}"
                        ),
                    )
                )

    if inputs.expected_width is not None:
        if inputs.width is None:
            missing_observation("width")
        elif inputs.width != inputs.expected_width:
            failures.append(
                MediaContractFailure(
                    kind="wrong-dimensions",
                    path=inputs.path,
                    message=f"{inputs.path} width {inputs.width} does not match expected {inputs.expected_width}",
                )
            )

    if inputs.expected_height is not None:
        if inputs.height is None:
            missing_observation("height")
        elif inputs.height != inputs.expected_height:
            failures.append(
                MediaContractFailure(
                    kind="wrong-dimensions",
                    path=inputs.path,
                    message=f"{inputs.path} height {inputs.height} does not match expected {inputs.expected_height}",
                )
            )

    if inputs.min_byte_size is not None or inputs.max_byte_size is not None:
        if inputs.byte_size is None:
            missing_observation("byte_size")
        else:
            if inputs.min_byte_size is not None and inputs.byte_size < inputs.min_byte_size:
                failures.append(
                    MediaContractFailure(
                        kind="wrong-byte-size",
                        path=inputs.path,
                        message=(f"{inputs.path} byte size {inputs.byte_size} is below minimum {inputs.min_byte_size}"),
                    )
                )
            if inputs.max_byte_size is not None and inputs.byte_size > inputs.max_byte_size:
                failures.append(
                    MediaContractFailure(
                        kind="wrong-byte-size",
                        path=inputs.path,
                        message=(f"{inputs.path} byte size {inputs.byte_size} is above maximum {inputs.max_byte_size}"),
                    )
                )

    if inputs.expected_column_count is not None:
        if inputs.column_count is None:
            missing_observation("column_count")
        elif inputs.column_count != inputs.expected_column_count:
            failures.append(
                MediaContractFailure(
                    kind="wrong-column-count",
                    path=inputs.path,
                    message=(f"{inputs.path} has {inputs.column_count} columns; expected {inputs.expected_column_count}"),
                )
            )

    if inputs.required_column_names is not None:
        if inputs.column_names is None:
            missing_observation("column_names")
        else:
            observed = set(inputs.column_names)
            for required in inputs.required_column_names:
                if required not in observed:
                    failures.append(
                        MediaContractFailure(
                            kind="missing-column",
                            path=required,
                            message=f"{inputs.path} is missing required column {json.dumps(required)}",
                        )
                    )

    if inputs.expected_line_count is not None:
        if inputs.line_count is None:
            missing_observation("line_count")
        elif inputs.line_count != inputs.expected_line_count:
            failures.append(
                MediaContractFailure(
                    kind="wrong-line-count",
                    path=inputs.path,
                    message=(f"{inputs.path} has {inputs.line_count} lines; expected {inputs.expected_line_count}"),
                )
            )

    return MediaContractProbeResult(passed=not failures, failures=tuple(failures))
