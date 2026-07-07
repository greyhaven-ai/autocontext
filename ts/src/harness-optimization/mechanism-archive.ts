import type { FrontierMechanism, OrphanMechanism } from "./contract/generated-types.js";

/**
 * Pure in-memory archive of promoted (frontier) and orphaned mechanisms.
 *
 * TypeScript half of the AC-880 parity pair: the same operations live here and
 * in `autocontext/src/autocontext/harness_optimization/mechanism_archive.py`,
 * and a shared fixture
 * (`fixtures/harness-optimization/mechanism-archive/archive-cases.json`) proves
 * both languages compute identical outcomes.
 *
 * The archive keeps two immutable lists: `frontier` mechanisms that were
 * promoted and `orphans` that were gated out. Every operation returns a new
 * archive; nothing is mutated in place and there is no IO. Rescue is explicit:
 * adding a frontier never removes an orphan, and rescuing an orphan leaves it
 * in `orphans` (with `rescued_into_frontier_id` set) so history is preserved.
 */

export interface MechanismArchive {
  frontier: readonly FrontierMechanism[];
  orphans: readonly OrphanMechanism[];
}

export interface QueryResult {
  frontier: readonly FrontierMechanism[];
  orphans: readonly OrphanMechanism[];
}

export interface QueryOptions {
  mechanismType?: string;
  targetSurface?: string;
  failureFamily?: string;
}

/** Return a new archive with `mechanism` appended to the frontier. */
export function addFrontier(
  archive: MechanismArchive,
  mechanism: FrontierMechanism,
): MechanismArchive {
  return { frontier: [...archive.frontier, mechanism], orphans: archive.orphans };
}

/** Return a new archive with `mechanism` appended to the orphans. */
export function addOrphan(archive: MechanismArchive, mechanism: OrphanMechanism): MechanismArchive {
  return { frontier: archive.frontier, orphans: [...archive.orphans, mechanism] };
}

/**
 * Mark the orphan `orphanId` as rescued into `intoFrontierId`.
 *
 * The orphan stays in `orphans` with `rescued_into_frontier_id` set so the
 * rescue is auditable. An unknown id returns the archive unchanged.
 */
export function rescueOrphan(
  archive: MechanismArchive,
  orphanId: string,
  intoFrontierId: string,
): MechanismArchive {
  if (!archive.orphans.some((m) => m.mechanism_id === orphanId)) {
    return archive;
  }
  const rescued = archive.orphans.map((m) =>
    m.mechanism_id === orphanId ? { ...m, rescued_into_frontier_id: intoFrontierId } : m,
  );
  return { frontier: archive.frontier, orphans: rescued };
}

/**
 * Filter both lists by any provided facet.
 *
 * `mechanismType` and `targetSurface` filter frontier and orphans alike.
 * `failureFamily` filters orphans only (frontier has no failure family), so the
 * frontier list is left unfiltered by that facet but still narrowed by any
 * other provided facet. An `undefined` facet applies no filter.
 */
export function query(archive: MechanismArchive, opts: QueryOptions = {}): QueryResult {
  const { mechanismType, targetSurface, failureFamily } = opts;
  const frontier = archive.frontier.filter(
    (m) =>
      (mechanismType === undefined || m.mechanism_type === mechanismType) &&
      (targetSurface === undefined || m.target_surface === targetSurface),
  );
  const orphans = archive.orphans.filter(
    (m) =>
      (mechanismType === undefined || m.mechanism_type === mechanismType) &&
      (targetSurface === undefined || m.target_surface === targetSurface) &&
      (failureFamily === undefined || m.failure_family === failureFamily),
  );
  return { frontier, orphans };
}

function isRescued(mechanism: OrphanMechanism): boolean {
  return Boolean(mechanism.rescued_into_frontier_id);
}

function rankKey(mechanism: OrphanMechanism): [number, number, number, string] {
  const support = mechanism.support_count ?? 0;
  return [isRescued(mechanism) ? 1 : 0, -support, mechanism.retry_count, mechanism.mechanism_id];
}

/**
 * Order orphans most-reusable first.
 *
 * Ascending sort over: not-rescued before rescued, then support_count
 * descending, then retry_count ascending, then mechanism_id ascending as a
 * total tiebreak. Missing support_count counts as 0 and a missing or empty
 * rescued_into_frontier_id counts as not-rescued.
 */
export function rankOrphans(orphans: readonly OrphanMechanism[]): readonly OrphanMechanism[] {
  return [...orphans].sort((a, b) => {
    const ka = rankKey(a);
    const kb = rankKey(b);
    for (let i = 0; i < 3; i += 1) {
      if (ka[i] !== kb[i]) {
        return (ka[i] as number) - (kb[i] as number);
      }
    }
    // mechanism_id ascending as a total tiebreak.
    if (ka[3] < kb[3]) {
      return -1;
    }
    if (ka[3] > kb[3]) {
      return 1;
    }
    return 0;
  });
}

/** Keep the top `maxOrphans` orphans by reuse rank, in rank order. */
export function pruneOrphans(
  orphans: readonly OrphanMechanism[],
  maxOrphans: number,
): readonly OrphanMechanism[] {
  const cap = maxOrphans < 0 ? 0 : maxOrphans;
  return rankOrphans(orphans).slice(0, cap);
}

export interface DigestOptions {
  maxEntries: number;
}

/**
 * Render a bounded, ranked digest of the archive for proposer prompts.
 *
 * The digest has a fixed shape reproduced character-for-character from the
 * Python port: a header line, a `frontier:` section listing up to `maxEntries`
 * frontier mechanisms in frontier order, then an `orphans (reusable):` section
 * listing up to `maxEntries` not-rescued orphans in `rankOrphans` order.
 * Rescued orphans are excluded from the reusable section. A non-positive
 * `maxEntries` emits the section headers with no items. Missing support_count
 * counts as 0.
 */
export function renderArchiveDigest(archive: MechanismArchive, opts: DigestOptions): string {
  const cap = Math.max(opts.maxEntries, 0);

  const lines = ["mechanism archive digest", "frontier:"];
  for (const front of archive.frontier.slice(0, cap)) {
    lines.push(
      `  - ${front.mechanism_name} [${front.target_surface}] support=${front.support_count}`,
    );
  }

  lines.push("orphans (reusable):");
  const reusable = rankOrphans(archive.orphans).filter((m) => !isRescued(m));
  for (const orphan of reusable.slice(0, cap)) {
    const support = orphan.support_count ?? 0;
    lines.push(`  - ${orphan.mechanism_name} [${orphan.failure_family}] support=${support}`);
  }

  return lines.join("\n");
}
