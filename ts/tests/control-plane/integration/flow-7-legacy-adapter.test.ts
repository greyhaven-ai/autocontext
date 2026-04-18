// Flow 7 (spec §10.3) — legacy-adapter migration.
//
// Sequence:
//   1. Seed a tmp directory with a legacy-model-records.json (array of
//      ModelRecord-shaped documents; Layer 11 v1 data source).
//   2. openRegistry(tmp)
//   3. Run `autoctx registry migrate` via the in-process CLI runner.
//   4. Assert CLI exit code is 0.
//   5. Assert the registry contains Artifacts equivalent to the seeded records
//      (type=fine-tuned-model, matching scenario, matching activationState,
//      payload pointer.json carries the expected fields).
//   6. Re-run migrate — assert idempotence: imported=0, skipped=N, errors=[].
//   7. Seed one malformed record, migrate — assert it's collected into `errors`
//      with a clear reason, exit code 1, but the well-formed records still
//      succeeded.
//   8. Use `registry validate` after migration — assert ok: true with no
//      schema/invariant violations on the imported artifacts.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ulid } from "ulid";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import type { ArtifactId } from "../../../src/control-plane/contract/branded-ids.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-int-flow7-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

function legacyRecord(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    artifactId: ulid(),
    scenario: "grid_ctf",
    family: "llama-3",
    backend: "mlx",
    checkpointDir: "/mnt/models/grid_ctf-v1",
    checkpointHash: "sha256:" + "a".repeat(64),
    activationState: "candidate",
    promotionHistory: [],
    registeredAt: "2026-04-17T12:00:00.000Z",
    ...overrides,
  };
}

describe("Flow 7 — legacy-adapter migration end-to-end", () => {
  test(
    "seed → migrate → re-migrate (idempotence) → malformed → validate",
    async () => {
      // ---- 1. Seed legacy records on disk. ----
      const idA = ulid();
      const idB = ulid();
      const recA = legacyRecord({
        artifactId: idA,
        scenario: "grid_ctf",
        family: "llama-3",
        backend: "mlx",
        checkpointDir: "/mnt/models/ckpt-a",
        checkpointHash: "sha256:" + "1".repeat(64),
        activationState: "candidate",
      });
      const recB = legacyRecord({
        artifactId: idB,
        scenario: "othello",
        family: "qwen-2",
        backend: "cuda",
        checkpointDir: "/mnt/models/ckpt-b",
        checkpointHash: "sha256:" + "2".repeat(64),
        activationState: "shadow",
        promotionHistory: [
          {
            from: "candidate",
            to: "shadow",
            reason: "shadow promoted by legacy engine",
            timestamp: "2026-04-17T12:01:00.000Z",
          },
        ],
      });
      const fromPath = join(tmp, "legacy.json");
      writeFileSync(fromPath, JSON.stringify([recA, recB]), "utf-8");

      // ---- 2. openRegistry (prep). ----
      const registry = openRegistry(tmp);

      // ---- 3. migrate via the CLI runner. ----
      const migrate1 = await runControlPlaneCommand(
        ["registry", "migrate", "--from", fromPath, "--output", "json"],
        { cwd: tmp },
      );

      // ---- 4. exit code 0. ----
      expect(migrate1.exitCode).toBe(0);
      const result1 = JSON.parse(migrate1.stdout);
      expect(result1.imported).toBe(2);
      expect(result1.skipped).toBe(0);
      expect(result1.errors).toEqual([]);

      // ---- 5. Both artifacts are in the registry with the right shape. ----
      const artA = registry.loadArtifact(idA as ArtifactId);
      expect(artA.actuatorType).toBe("fine-tuned-model");
      expect(artA.scenario).toBe("grid_ctf");
      expect(artA.activationState).toBe("candidate");

      const pointerA = JSON.parse(
        readFileSync(
          join(tmp, ".autocontext", "candidates", idA, "payload", "pointer.json"),
          "utf-8",
        ),
      ) as Record<string, unknown>;
      expect(pointerA.kind).toBe("model-checkpoint");
      expect(pointerA.externalPath).toBe("/mnt/models/ckpt-a");
      expect(pointerA.checkpointHash).toBe("sha256:" + "1".repeat(64));
      expect(pointerA.family).toBe("llama-3");
      expect(pointerA.backend).toBe("mlx");

      const artB = registry.loadArtifact(idB as ArtifactId);
      expect(artB.actuatorType).toBe("fine-tuned-model");
      expect(artB.scenario).toBe("othello");
      expect(artB.activationState).toBe("shadow");
      expect(artB.promotionHistory).toHaveLength(1);
      expect(artB.promotionHistory[0]?.to).toBe("shadow");

      // ---- 6. Re-run migrate: idempotence. ----
      const migrate2 = await runControlPlaneCommand(
        ["registry", "migrate", "--from", fromPath, "--output", "json"],
        { cwd: tmp },
      );
      expect(migrate2.exitCode).toBe(0);
      const result2 = JSON.parse(migrate2.stdout);
      expect(result2.imported).toBe(0);
      expect(result2.skipped).toBe(2);
      expect(result2.errors).toEqual([]);

      // ---- 7. Introduce a malformed record and re-run; assert exit 1 and
      //         well-formed records unaffected. ----
      const idC = ulid();
      const recC = legacyRecord({
        artifactId: idC,
        scenario: "grid_ctf",
        checkpointHash: "sha256:" + "3".repeat(64),
      });
      const badScenario = legacyRecord({
        artifactId: ulid(),
        scenario: "NOT A VALID SLUG!",
      });
      writeFileSync(
        fromPath,
        JSON.stringify([recA, recB, recC, badScenario]),
        "utf-8",
      );

      const migrate3 = await runControlPlaneCommand(
        ["registry", "migrate", "--from", fromPath, "--output", "json"],
        { cwd: tmp },
      );
      expect(migrate3.exitCode).toBe(1);
      const result3 = JSON.parse(migrate3.stdout);
      expect(result3.imported).toBe(1);   // recC is new and good
      expect(result3.skipped).toBe(2);    // recA and recB already present
      expect(result3.errors).toHaveLength(1);
      expect(String(result3.errors[0].reason).toLowerCase()).toMatch(/scenario/);

      // The good new record is present.
      const artC = registry.loadArtifact(idC as ArtifactId);
      expect(artC.actuatorType).toBe("fine-tuned-model");

      // ---- 8. registry validate reports ok. ----
      const validate = await runControlPlaneCommand(
        ["registry", "validate", "--output", "json"],
        { cwd: tmp },
      );
      expect(validate.exitCode).toBe(0);
      const report = JSON.parse(validate.stdout);
      const hardIssues = (
        report.issues as { kind: string }[]
      ).filter(
        (i) => i.kind !== "signature-missing" && i.kind !== "signature-present",
      );
      expect(hardIssues).toEqual([]);
      expect(report.ok).toBe(true);
    },
  );
});
