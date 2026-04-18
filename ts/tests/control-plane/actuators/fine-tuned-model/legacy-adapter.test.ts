// Layer 11 — Legacy ModelRecord → fine-tuned-model Artifact adapter tests.
//
// Per spec §7.5: "the registry becomes the single source of truth; existing
// ModelRecord callers keep working via a read-only shim."
//
// The training-layer ModelRegistry (src/training/promotion.ts) is purely
// in-memory with no persistence. The v1 legacy adapter therefore imports from
// an explicit-path JSON file containing an array of ModelRecord-shaped
// documents (with optional `checkpointHash`, `runId`, `environmentTag`
// enrichments). The CLI surfaces this as `autoctx registry migrate --from <path>`.
// When --from is omitted, the adapter looks for the default discovery path
// `<cwd>/.autocontext/legacy-model-records.json`.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  writeFileSync,
  existsSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ulid } from "ulid";
import { importLegacyModelRecords } from "../../../../src/control-plane/actuators/fine-tuned-model/legacy-adapter.js";
import { openRegistry } from "../../../../src/control-plane/registry/index.js";
import type { Artifact } from "../../../../src/control-plane/contract/types.js";
import type { ArtifactId } from "../../../../src/control-plane/contract/branded-ids.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-legacy-adapter-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

/** Write a JSON file at <tmp>/<rel> containing the given records array. */
function writeLegacyFile(rel: string, records: unknown[]): string {
  const path = join(tmp, rel);
  const parent = path.split("/").slice(0, -1).join("/");
  if (parent.length > 0 && !existsSync(parent)) {
    mkdirSync(parent, { recursive: true });
  }
  writeFileSync(path, JSON.stringify(records, null, 2), "utf-8");
  return path;
}

/** Construct a minimal "legacy record" (ModelRecord shape, plus optional hash). */
function legacyRecord(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    artifactId: ulid(),
    scenario: "grid_ctf",
    family: "llama-3",
    backend: "mlx",
    checkpointDir: "/mnt/models/grid_ctf-v1",
    // checkpointHash is an enrichment — if present, the adapter uses it
    // directly; otherwise it attempts to hashDirectory(checkpointDir).
    checkpointHash: "sha256:" + "a".repeat(64),
    activationState: "candidate",
    promotionHistory: [],
    registeredAt: "2026-04-17T12:00:00.000Z",
    ...overrides,
  };
}

describe("importLegacyModelRecords — source discovery", () => {
  test("reads from explicit --from path when provided", async () => {
    const rec = legacyRecord({});
    const fromPath = writeLegacyFile("elsewhere/records.json", [rec]);

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.imported).toBe(1);
    expect(result.skipped).toBe(0);
    expect(result.errors).toEqual([]);
  });

  test("reads from default path .autocontext/legacy-model-records.json when --from omitted", async () => {
    const rec = legacyRecord({});
    writeLegacyFile(".autocontext/legacy-model-records.json", [rec]);

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry);

    expect(result.imported).toBe(1);
    expect(result.skipped).toBe(0);
    expect(result.errors).toEqual([]);
  });

  test("returns empty result when no source file exists (graceful no-op)", async () => {
    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry);

    expect(result.imported).toBe(0);
    expect(result.skipped).toBe(0);
    expect(result.errors).toEqual([]);
  });

  test("returns error (not throw) when source file is malformed JSON", async () => {
    const fromPath = join(tmp, "bad.json");
    writeFileSync(fromPath, "{not json", "utf-8");

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.imported).toBe(0);
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]?.reason.toLowerCase()).toMatch(/json|parse/);
  });

  test("returns error when source file is not a JSON array", async () => {
    const fromPath = join(tmp, "not-array.json");
    writeFileSync(fromPath, JSON.stringify({ oops: true }), "utf-8");

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]?.reason.toLowerCase()).toMatch(/array/);
  });
});

describe("importLegacyModelRecords — happy path mapping", () => {
  test("maps a valid ModelRecord to an Artifact with type=fine-tuned-model and pointer.json payload", async () => {
    const recId = ulid();
    const rec = legacyRecord({
      artifactId: recId,
      scenario: "grid_ctf",
      family: "llama-3",
      backend: "mlx",
      checkpointDir: "/mnt/models/grid_ctf-v1",
      checkpointHash: "sha256:" + "b".repeat(64),
      activationState: "candidate",
    });
    const fromPath = writeLegacyFile("records.json", [rec]);

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.imported).toBe(1);
    expect(result.errors).toEqual([]);

    // Verify the artifact was persisted with the expected shape.
    const artifact: Artifact = registry.loadArtifact(recId as ArtifactId);
    expect(artifact.actuatorType).toBe("fine-tuned-model");
    expect(artifact.scenario).toBe("grid_ctf");
    expect(artifact.activationState).toBe("candidate");
    expect(artifact.environmentTag).toBe("production");

    // Verify the pointer.json payload on disk carries the mapped fields.
    const pointerPath = join(tmp, ".autocontext", "candidates", recId, "payload", "pointer.json");
    const pointer = JSON.parse(readFileSync(pointerPath, "utf-8")) as Record<string, unknown>;
    expect(pointer.kind).toBe("model-checkpoint");
    expect(pointer.externalPath).toBe("/mnt/models/grid_ctf-v1");
    expect(pointer.checkpointHash).toBe("sha256:" + "b".repeat(64));
    expect(pointer.family).toBe("llama-3");
    expect(pointer.backend).toBe("mlx");
  });

  test("preserves the legacy artifactId when it is a valid ULID", async () => {
    const id = ulid();
    const rec = legacyRecord({ artifactId: id });
    const fromPath = writeLegacyFile("records.json", [rec]);

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.imported).toBe(1);
    const artifact = registry.loadArtifact(id as ArtifactId);
    expect(artifact.id).toBe(id);
  });

  test("generates a fresh id when legacy artifactId is not a ULID and records the old id in provenance.authorId", async () => {
    const oldId = "legacy_abc123";
    const rec = legacyRecord({ artifactId: oldId });
    const fromPath = writeLegacyFile("records.json", [rec]);

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.imported).toBe(1);
    const ids = readdirSync(join(tmp, ".autocontext", "candidates"));
    expect(ids).toHaveLength(1);
    const newId = ids[0]!;
    expect(newId).not.toBe(oldId);
    expect(newId).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    const artifact = registry.loadArtifact(newId as ArtifactId);
    // Old id is preserved in provenance.authorId (no runId hint here, so authorType=external-agent).
    expect(artifact.provenance.authorId).toContain(oldId);
  });
});

describe("importLegacyModelRecords — promotionHistory replay", () => {
  test("replays each PromotionEvent so the final activationState matches the record", async () => {
    const id = ulid();
    const rec = legacyRecord({
      artifactId: id,
      activationState: "active",
      promotionHistory: [
        {
          from: "candidate",
          to: "shadow",
          reason: "shadow promoted",
          timestamp: "2026-04-17T12:01:00.000Z",
        },
        {
          from: "shadow",
          to: "active",
          reason: "active promoted",
          timestamp: "2026-04-17T12:02:00.000Z",
        },
      ],
    });
    const fromPath = writeLegacyFile("records.json", [rec]);

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.imported).toBe(1);
    expect(result.errors).toEqual([]);

    const artifact = registry.loadArtifact(id as ArtifactId);
    expect(artifact.activationState).toBe("active");
    expect(artifact.promotionHistory).toHaveLength(2);
    expect(artifact.promotionHistory[0]?.to).toBe("shadow");
    expect(artifact.promotionHistory[1]?.to).toBe("active");
  });
});

describe("importLegacyModelRecords — idempotence", () => {
  test("re-running import on an already-migrated registry skips existing ids", async () => {
    const id = ulid();
    const rec = legacyRecord({ artifactId: id });
    const fromPath = writeLegacyFile("records.json", [rec]);

    const registry = openRegistry(tmp);
    const first = await importLegacyModelRecords(tmp, registry, { fromPath });
    expect(first.imported).toBe(1);
    expect(first.skipped).toBe(0);

    const second = await importLegacyModelRecords(tmp, registry, { fromPath });
    expect(second.imported).toBe(0);
    expect(second.skipped).toBe(1);
    expect(second.errors).toEqual([]);
  });

  test("batch idempotence across mixed records (one new, one already imported)", async () => {
    const idA = ulid();
    const idB = ulid();
    const recA = legacyRecord({ artifactId: idA });
    const recB = legacyRecord({ artifactId: idB });
    const fromPath = writeLegacyFile("records.json", [recA]);

    const registry = openRegistry(tmp);
    const first = await importLegacyModelRecords(tmp, registry, { fromPath });
    expect(first.imported).toBe(1);

    // Add a second record and re-run.
    writeLegacyFile("records.json", [recA, recB]);
    const second = await importLegacyModelRecords(tmp, registry, { fromPath });
    expect(second.imported).toBe(1);  // only recB
    expect(second.skipped).toBe(1);   // recA already present
    expect(second.errors).toEqual([]);
  });
});

describe("importLegacyModelRecords — error collection (never throws on bad records)", () => {
  test("invalid scenario produces an error entry without aborting the batch", async () => {
    const goodId = ulid();
    const bad = legacyRecord({ artifactId: ulid(), scenario: "INVALID SLUG!" });
    const good = legacyRecord({ artifactId: goodId });
    const fromPath = writeLegacyFile("records.json", [bad, good]);

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.imported).toBe(1);
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]?.reason.toLowerCase()).toMatch(/scenario/);

    // The good record is present; the bad one is not.
    expect(() => registry.loadArtifact(goodId as ArtifactId)).not.toThrow();
  });

  test("missing checkpointHash AND unreadable checkpointDir produces an error without aborting", async () => {
    const goodId = ulid();
    const bad = legacyRecord({
      artifactId: ulid(),
      checkpointDir: "/nonexistent/path/to/checkpoint-xyz",
    });
    // Remove the checkpointHash key entirely.
    delete (bad as { checkpointHash?: unknown }).checkpointHash;
    const good = legacyRecord({ artifactId: goodId });
    const fromPath = writeLegacyFile("records.json", [bad, good]);

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.imported).toBe(1);
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]?.reason.toLowerCase()).toMatch(/checkpoint|hash/);
  });

  test("malformed promotionHistory entry produces an error and skips the record", async () => {
    const goodId = ulid();
    const bad = legacyRecord({
      artifactId: ulid(),
      activationState: "active",
      promotionHistory: [
        // Illegal transition: candidate → deprecated is not in the allow-list.
        {
          from: "candidate",
          to: "deprecated",
          reason: "oops",
          timestamp: "2026-04-17T12:01:00.000Z",
        },
      ],
    });
    const good = legacyRecord({ artifactId: goodId });
    const fromPath = writeLegacyFile("records.json", [bad, good]);

    const registry = openRegistry(tmp);
    const result = await importLegacyModelRecords(tmp, registry, { fromPath });

    expect(result.imported).toBe(1);
    expect(result.errors).toHaveLength(1);
  });
});

describe("importLegacyModelRecords — provenance", () => {
  test("uses 'autocontext-run' authorType when a runId is present on the record", async () => {
    const id = ulid();
    const rec = legacyRecord({ artifactId: id, runId: "run_abc123" });
    const fromPath = writeLegacyFile("records.json", [rec]);

    const registry = openRegistry(tmp);
    await importLegacyModelRecords(tmp, registry, { fromPath });

    const artifact = registry.loadArtifact(id as ArtifactId);
    expect(artifact.provenance.authorType).toBe("autocontext-run");
    expect(artifact.provenance.authorId).toBe("run_abc123");
    expect(artifact.provenance.parentArtifactIds).toEqual([]);
    expect(artifact.provenance.createdAt).toBe("2026-04-17T12:00:00.000Z");
  });

  test("uses 'external-agent' authorType when no runId is present", async () => {
    const id = ulid();
    const rec = legacyRecord({ artifactId: id });
    const fromPath = writeLegacyFile("records.json", [rec]);

    const registry = openRegistry(tmp);
    await importLegacyModelRecords(tmp, registry, { fromPath });

    const artifact = registry.loadArtifact(id as ArtifactId);
    expect(artifact.provenance.authorType).toBe("external-agent");
  });
});

describe("importLegacyModelRecords — environmentTag", () => {
  test("defaults environmentTag to 'production' when the record carries none", async () => {
    const id = ulid();
    const rec = legacyRecord({ artifactId: id });
    const fromPath = writeLegacyFile("records.json", [rec]);

    const registry = openRegistry(tmp);
    await importLegacyModelRecords(tmp, registry, { fromPath });

    const artifact = registry.loadArtifact(id as ArtifactId);
    expect(artifact.environmentTag).toBe("production");
  });

  test("honors an explicit environmentTag on the record", async () => {
    const id = ulid();
    const rec = legacyRecord({ artifactId: id, environmentTag: "staging" });
    const fromPath = writeLegacyFile("records.json", [rec]);

    const registry = openRegistry(tmp);
    await importLegacyModelRecords(tmp, registry, { fromPath });

    const artifact = registry.loadArtifact(id as ArtifactId);
    expect(artifact.environmentTag).toBe("staging");
  });
});
