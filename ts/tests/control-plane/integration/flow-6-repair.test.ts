// Flow 6 (spec §10.3) — registry repair.
//
// Sequence:
//   1. Build a registry with several artifacts in mixed states (active /
//      deprecated / candidate / shadow), with attached EvalRuns and
//      promotion histories.
//   2. Snapshot the .autocontext/state/active/ pointer tree as ground truth.
//   3. Wipe `.autocontext/state/` (and any cache layer if/when one lands).
//   4. Call registry.repair().
//   5. Assert the reconstructed pointers exactly match the snapshot.
//   6. Run registry.validate() — assert ok: true.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  listStatePointers,
  type StatePointerEntry,
} from "../../../src/control-plane/registry/state-pointer.js";
import {
  buildArtifactWithPassingEval,
  openTestRegistry,
  promoteArtifact,
} from "./_helpers/fixtures.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-int-flow6-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

/** Stable serialization of state pointers for snapshot comparison. */
function snapshotPointers(root: string): string[] {
  const entries: StatePointerEntry[] = listStatePointers(root);
  return entries
    .map(
      (e) =>
        `${e.scenario}|${e.actuatorType}|${e.environmentTag} -> ${e.pointer.artifactId}`,
    )
    .sort();
}

describe("Flow 6 — registry repair end-to-end", () => {
  test(
    "repair() reconstructs state pointers after .autocontext/state/ is wiped; validate() passes",
    async () => {
      const registry = openTestRegistry(tmp);

      // ---- Build a registry with 5 artifacts across different (scenario,
      //      actuatorType, env) tuples and activation states. ----
      // 1. grid_ctf / prompt-patch / production / active
      const a1 = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "prompt-patch",
        payloadSuffix: "a1",
        runId: "run_a1",
        ingestedAt: "2026-04-17T12:00:00.000Z",
      });
      promoteArtifact({
        registry,
        artifactId: a1.artifact.id,
        to: "active",
        timestamp: "2026-04-17T12:01:00.000Z",
      });

      // 2. grid_ctf / prompt-patch / production / displaces a1 → a1 becomes deprecated
      const a2 = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "prompt-patch",
        payloadSuffix: "a2",
        runId: "run_a2",
        ingestedAt: "2026-04-17T12:05:00.000Z",
      });
      promoteArtifact({
        registry,
        artifactId: a2.artifact.id,
        to: "active",
        timestamp: "2026-04-17T12:06:00.000Z",
      });

      // 3. grid_ctf / tool-policy / production / active (independent group)
      const a3 = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "tool-policy",
        payloadSuffix: "a3",
        runId: "run_a3",
        ingestedAt: "2026-04-17T12:10:00.000Z",
      });
      promoteArtifact({
        registry,
        artifactId: a3.artifact.id,
        to: "active",
        timestamp: "2026-04-17T12:11:00.000Z",
      });

      // 4. grid_ctf / prompt-patch / production / shadow (separate artifact, doesn't displace a2)
      const a4 = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "prompt-patch",
        payloadSuffix: "a4",
        runId: "run_a4",
        ingestedAt: "2026-04-17T12:15:00.000Z",
      });
      promoteArtifact({
        registry,
        artifactId: a4.artifact.id,
        to: "shadow",
        timestamp: "2026-04-17T12:16:00.000Z",
      });

      // 5. othello / prompt-patch / production / candidate (no promotion)
      const a5 = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "othello",
        actuatorType: "prompt-patch",
        payloadSuffix: "a5",
        runId: "run_a5",
        ingestedAt: "2026-04-17T12:20:00.000Z",
      });
      // a5 stays in candidate state.

      // Sanity: state pointers exist for the two active groups (a2 + a3).
      const before = snapshotPointers(tmp);
      expect(before).toHaveLength(2);
      expect(before).toEqual(
        expect.arrayContaining([
          `grid_ctf|prompt-patch|production -> ${a2.artifact.id}`,
          `grid_ctf|tool-policy|production -> ${a3.artifact.id}`,
        ]),
      );

      // ---- 2. Wipe state/ (simulate a corrupted registry state index). ----
      const stateRoot = join(tmp, ".autocontext", "state");
      expect(existsSync(stateRoot)).toBe(true);
      rmSync(stateRoot, { recursive: true, force: true });
      expect(existsSync(stateRoot)).toBe(false);

      // ---- 3. registry.repair() ----
      registry.repair();

      // ---- 4. Reconstructed pointers exactly match the snapshot. ----
      const after = snapshotPointers(tmp);
      expect(after).toEqual(before);

      // ---- 5. registry.validate() reports ok: true. ----
      const report = registry.validate();
      // Filter out informational signature-missing notes — they are not
      // hard failures (validate.ts treats them as informational in v1) but
      // they would otherwise dominate the issue list.
      const hardIssues = report.issues.filter(
        (i) => i.kind !== "signature-missing" && i.kind !== "signature-present",
      );
      expect(hardIssues).toEqual([]);
      expect(report.ok).toBe(true);

      // Touch the unused artifact aliases so eslint/ts noUnusedLocals
      // tracking doesn't complain in the future.
      void a4;
      void a5;
    },
  );
});
