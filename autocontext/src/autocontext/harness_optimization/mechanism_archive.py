"""Pure in-memory archive of promoted (frontier) and orphaned mechanisms.

The archive keeps two immutable lists: `frontier` mechanisms that were
promoted and `orphans` that were gated out. Every operation returns a new
`MechanismArchive`; nothing is mutated in place and there is no IO. Rescue is
explicit: adding a frontier never removes an orphan, and rescuing an orphan
leaves it in `orphans` (with `rescued_into_frontier_id` set) so history is
preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from autocontext.harness_optimization.contract.models import (
    FrontierMechanism,
    OrphanMechanism,
)


class QueryResult(TypedDict):
    frontier: tuple[FrontierMechanism, ...]
    orphans: tuple[OrphanMechanism, ...]


@dataclass(frozen=True, slots=True)
class MechanismArchive:
    frontier: tuple[FrontierMechanism, ...] = ()
    orphans: tuple[OrphanMechanism, ...] = ()


def add_frontier(archive: MechanismArchive, mechanism: FrontierMechanism) -> MechanismArchive:
    """Return a new archive with `mechanism` appended to the frontier."""

    return MechanismArchive(frontier=(*archive.frontier, mechanism), orphans=archive.orphans)


def add_orphan(archive: MechanismArchive, mechanism: OrphanMechanism) -> MechanismArchive:
    """Return a new archive with `mechanism` appended to the orphans."""

    return MechanismArchive(frontier=archive.frontier, orphans=(*archive.orphans, mechanism))


def rescue_orphan(
    archive: MechanismArchive,
    orphan_id: str,
    into_frontier_id: str,
) -> MechanismArchive:
    """Mark the orphan `orphan_id` as rescued into `into_frontier_id`.

    The orphan stays in `orphans` with `rescued_into_frontier_id` set so the
    rescue is auditable. An unknown id returns the archive unchanged.
    """

    if not any(m.mechanism_id == orphan_id for m in archive.orphans):
        return archive
    rescued = tuple(
        m.model_copy(update={"rescued_into_frontier_id": into_frontier_id}) if m.mechanism_id == orphan_id else m
        for m in archive.orphans
    )
    return MechanismArchive(frontier=archive.frontier, orphans=rescued)


def query(
    archive: MechanismArchive,
    *,
    mechanism_type: str | None = None,
    target_surface: str | None = None,
    failure_family: str | None = None,
) -> QueryResult:
    """Filter both lists by any provided facet.

    `mechanism_type` and `target_surface` filter frontier and orphans alike.
    `failure_family` filters orphans only (frontier has no failure family), so
    the frontier list is left unfiltered by that facet but still narrowed by
    any other provided facet. A `None` facet applies no filter.
    """

    frontier = tuple(
        m
        for m in archive.frontier
        if (mechanism_type is None or m.mechanism_type == mechanism_type)
        and (target_surface is None or m.target_surface == target_surface)
    )
    orphans = tuple(
        m
        for m in archive.orphans
        if (mechanism_type is None or m.mechanism_type == mechanism_type)
        and (target_surface is None or m.target_surface == target_surface)
        and (failure_family is None or m.failure_family == failure_family)
    )
    return {"frontier": frontier, "orphans": orphans}


def _is_rescued(mechanism: OrphanMechanism) -> bool:
    return bool(mechanism.rescued_into_frontier_id)


def _rank_key(mechanism: OrphanMechanism) -> tuple[int, int, int, str]:
    support = mechanism.support_count or 0
    return (
        1 if _is_rescued(mechanism) else 0,
        -support,
        mechanism.retry_count,
        mechanism.mechanism_id,
    )


def rank_orphans(orphans: tuple[OrphanMechanism, ...]) -> tuple[OrphanMechanism, ...]:
    """Order orphans most-reusable first.

    Ascending sort over: not-rescued before rescued, then support_count
    descending, then retry_count ascending, then mechanism_id ascending as a
    stable tiebreak. Missing support_count counts as 0 and a missing or empty
    rescued_into_frontier_id counts as not-rescued.
    """

    return tuple(sorted(orphans, key=_rank_key))


def prune_orphans(orphans: tuple[OrphanMechanism, ...], max_orphans: int) -> tuple[OrphanMechanism, ...]:
    """Keep the top `max_orphans` orphans by reuse rank, in rank order."""

    if max_orphans < 0:
        max_orphans = 0
    return rank_orphans(orphans)[:max_orphans]


def render_archive_digest(archive: MechanismArchive, *, max_entries: int) -> str:
    """Render a bounded, ranked digest of the archive for proposer prompts.

    The digest has a fixed shape so a TypeScript port can reproduce it
    character-for-character: a header line, a `frontier:` section listing up to
    `max_entries` frontier mechanisms in frontier order, then an
    `orphans (reusable):` section listing up to `max_entries` not-rescued
    orphans in `rank_orphans` order. Rescued orphans are excluded from the
    reusable section. A non-positive `max_entries` emits the section headers
    with no items. Missing support_count counts as 0.
    """

    cap = max(max_entries, 0)

    lines = ["mechanism archive digest", "frontier:"]
    for front in archive.frontier[:cap]:
        lines.append(f"  - {front.mechanism_name} [{front.target_surface}] support={front.support_count}")

    lines.append("orphans (reusable):")
    reusable = tuple(m for m in rank_orphans(archive.orphans) if not _is_rescued(m))
    for orphan in reusable[:cap]:
        support = orphan.support_count or 0
        lines.append(f"  - {orphan.mechanism_name} [{orphan.failure_family}] support={support}")

    return "\n".join(lines)


__all__ = [
    "MechanismArchive",
    "QueryResult",
    "add_frontier",
    "add_orphan",
    "prune_orphans",
    "query",
    "rank_orphans",
    "render_archive_digest",
    "rescue_orphan",
]
