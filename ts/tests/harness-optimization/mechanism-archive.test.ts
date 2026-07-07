import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect } from "vitest";
import type {
  FrontierMechanism,
  OrphanMechanism,
} from "../../src/harness-optimization/contract/generated-types.js";
import {
  type MechanismArchive,
  addOrphan,
  pruneOrphans,
  query,
  rankOrphans,
  renderArchiveDigest,
  rescueOrphan,
} from "../../src/harness-optimization/mechanism-archive.js";
import {
  validateFrontierMechanism,
  validateOrphanMechanism,
} from "../../src/harness-optimization/contract/validators.js";

// Load the SAME repo-root fixture the Python suite loads. Matching it in both
// languages is the load-bearing parity proof.
// Walk up: ts/tests/harness-optimization/ -> ts/tests/ -> ts/ -> <repo root>.
const FIXTURE = JSON.parse(
  readFileSync(
    join(
      import.meta.dirname,
      "..",
      "..",
      "..",
      "fixtures",
      "harness-optimization",
      "mechanism-archive",
      "archive-cases.json",
    ),
    "utf8",
  ),
) as {
  seed: { frontier: FrontierMechanism[]; orphans: OrphanMechanism[] };
  cases: Record<string, unknown>[];
};

function seedArchive(): MechanismArchive {
  return {
    frontier: FIXTURE.seed.frontier,
    orphans: FIXTURE.seed.orphans,
  };
}

describe("mechanism archive parity", () => {
  it("validates every shared-fixture record against its schema", () => {
    // Python model_validates the seed on load; do the same in TS so a drifted
    // record in the shared fixture fails the TS suite too, not just Python.
    for (const record of FIXTURE.seed.frontier) {
      expect(validateFrontierMechanism(record)).toEqual({ valid: true });
    }
    const orphanRecords: OrphanMechanism[] = [...FIXTURE.seed.orphans];
    for (const c of FIXTURE.cases) {
      if (c.orphan) {
        orphanRecords.push(c.orphan as OrphanMechanism);
      }
    }
    for (const record of orphanRecords) {
      expect(validateOrphanMechanism(record)).toEqual({ valid: true });
    }
  });

  for (const c of FIXTURE.cases) {
    it(`${String(c.name)}: matches fixture expectations`, () => {
      const archive = seedArchive();
      const op = c.op as string;

      if (op === "add_orphan") {
        const added = addOrphan(archive, c.orphan as OrphanMechanism);
        expect(added.orphans.length).toBe(c.expected_orphan_count);
        expect(added.orphans[added.orphans.length - 1].mechanism_id).toBe(
          c.expected_added_orphan_id,
        );
        // Input archive is not mutated.
        expect(archive.orphans.length).toBe(FIXTURE.seed.orphans.length);
      } else if (op === "query_by_type") {
        const result = query(archive, { mechanismType: c.mechanism_type as string });
        expect(result.frontier.map((m) => m.mechanism_id)).toEqual(c.expected_frontier_ids);
        expect(result.orphans.map((m) => m.mechanism_id)).toEqual(c.expected_orphan_ids);
      } else if (op === "query_by_failure_family") {
        const result = query(archive, { failureFamily: c.failure_family as string });
        // failure_family narrows orphans only; the frontier stays whole.
        expect(result.frontier.map((m) => m.mechanism_id)).toEqual(c.expected_frontier_ids);
        expect(result.orphans.map((m) => m.mechanism_id)).toEqual(c.expected_orphan_ids);
      } else if (op === "query_by_surface") {
        const result = query(archive, { targetSurface: c.target_surface as string });
        expect(result.frontier.map((m) => m.mechanism_id)).toEqual(c.expected_frontier_ids);
        expect(result.orphans.map((m) => m.mechanism_id)).toEqual(c.expected_orphan_ids);
      } else if (op === "rescue_noop") {
        const rescued = rescueOrphan(archive, c.orphan_id as string, c.into_frontier_id as string);
        // An unknown orphan id leaves the archive unchanged: identical ids and no
        // new rescued_into_frontier_id set on anyone.
        expect(rescued.orphans.map((m) => m.mechanism_id)).toEqual(c.expected_orphan_ids);
        const before = archive.orphans.map((m) => m.rescued_into_frontier_id ?? "");
        const after = rescued.orphans.map((m) => m.rescued_into_frontier_id ?? "");
        expect(after).toEqual(before);
      } else if (op === "rank_orphans") {
        const ranked = rankOrphans(archive.orphans);
        expect(ranked.map((m) => m.mechanism_id)).toEqual(c.expected_order);
      } else if (op === "rescue") {
        const rescued = rescueOrphan(archive, c.orphan_id as string, c.into_frontier_id as string);
        const target = rescued.orphans.find((m) => m.mechanism_id === c.orphan_id);
        expect(target?.rescued_into_frontier_id).toBe(c.expected_rescued_into_frontier_id);
        const ranked = rankOrphans(rescued.orphans);
        expect(ranked.map((m) => m.mechanism_id)).toEqual(c.expected_order);
        expect(ranked[ranked.length - 1].mechanism_id).toBe(c.expected_last_orphan_id);
      } else if (op === "prune") {
        const pruned = pruneOrphans(archive.orphans, c.max_orphans as number);
        expect(pruned.map((m) => m.mechanism_id)).toEqual(c.expected_surviving_ids);
      } else if (op === "digest") {
        const maxEntries = c.max_entries as number;
        const digest = renderArchiveDigest(archive, { maxEntries });
        expect(typeof digest).toBe("string");
        expect(digest).toContain(c.expected_header as string);
        const lines = digest.split("\n");

        const frontierStart = lines.indexOf("frontier:");
        const orphanStart = lines.indexOf("orphans (reusable):");
        const frontierItems = lines
          .slice(frontierStart + 1, orphanStart)
          .filter((ln) => ln.startsWith("  - "));
        const orphanItems = lines.slice(orphanStart + 1).filter((ln) => ln.startsWith("  - "));

        // Boundedness: never more than maxEntries items per section.
        expect(frontierItems.length).toBeLessThanOrEqual(maxEntries);
        expect(orphanItems.length).toBeLessThanOrEqual(maxEntries);
        expect(frontierItems.length).toBe(c.expected_frontier_count);

        // The orphan section lists exactly the expected ids in rank order.
        const idToName = new Map(
          FIXTURE.seed.orphans.map((o) => [o.mechanism_id, o.mechanism_name]),
        );
        const expectedNames = (c.expected_orphan_ids_in_order as string[]).map((i) =>
          idToName.get(i),
        );
        const renderedOrphanNames = orphanItems.map((ln) =>
          ln.split(" [", 2)[0].replace(/^ {2}- /, ""),
        );
        expect(renderedOrphanNames).toEqual(expectedNames);

        // With maxEntries=1 each section holds at most one item and the orphan
        // section is exactly the top-ranked not-rescued orphan. This fails if the
        // per-section cap is ever dropped.
        if (maxEntries === 1) {
          expect(frontierItems.length).toBeLessThanOrEqual(1);
          expect(orphanItems.length).toBeLessThanOrEqual(1);
          expect(c.expected_orphan_ids_in_order).toEqual(["orphan-ac880-A"]);
          expect(renderedOrphanNames).toEqual([idToName.get("orphan-ac880-A")]);
        }

        // When the fixture pins the exact string, the render must match it
        // character-for-character (parity with the Python renderer).
        if (typeof c.expected_digest === "string") {
          expect(digest).toBe(c.expected_digest);
        }

        // Rescued orphans are excluded entirely from the reusable section.
        const orphanSection = lines.slice(orphanStart).join("\n");
        for (const excludedId of c.expected_excludes as string[]) {
          expect(orphanSection).not.toContain(idToName.get(excludedId));
        }
      } else {
        throw new Error(`unhandled op: ${op}`);
      }
    });
  }

  it("renders the seed digest character-for-character", () => {
    const digest = renderArchiveDigest(seedArchive(), { maxEntries: 3 });
    const expected = [
      "mechanism archive digest",
      "frontier:",
      "  - retry-on-truncated-output guard [harness_validator] support=4",
      "  - structured finish-guard playbook [prompt] support=3",
      "orphans (reusable):",
      "  - prompt hint for empty-tool-call retry [empty-tool-call] support=3",
      "  - tighter finish-guard prompt [empty-tool-call] support=3",
      "  - aggressive context pruning [context-loss] support=1",
    ].join("\n");
    expect(digest).toBe(expected);
  });

  it("emits headers with no items for a non-positive maxEntries", () => {
    const digest = renderArchiveDigest(seedArchive(), { maxEntries: 0 });
    expect(digest).toBe(
      ["mechanism archive digest", "frontier:", "orphans (reusable):"].join("\n"),
    );
  });
});
