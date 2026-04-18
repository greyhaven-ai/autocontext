import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import { repair } from "../../../src/control-plane/registry/repair.js";
import {
  readStatePointer,
  listStatePointers,
} from "../../../src/control-plane/registry/state-pointer.js";
import { createArtifact, createPromotionEvent } from "../../../src/control-plane/contract/factories.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import type { ContentHash, EnvironmentTag, Scenario } from "../../../src/control-plane/contract/branded-ids.js";
import type { Provenance } from "../../../src/control-plane/contract/types.js";

const aProvenance: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

function tempPayload(parent: string, content: string): { dir: string; hash: ContentHash } {
  const dir = join(parent, "src-" + Math.random().toString(36).slice(2));
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "f.txt"), content);
  return { dir, hash: hashDirectory(dir) };
}

describe("repair", () => {
  let registryRoot: string;

  beforeEach(() => {
    registryRoot = mkdtempSync(join(tmpdir(), "autocontext-repair-"));
  });

  afterEach(() => {
    rmSync(registryRoot, { recursive: true, force: true });
  });

  test("rebuilds state pointers identical to pre-deletion when state/active/ is wiped", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(artifact, dir);
    reg.appendPromotionEvent(artifact.id, createPromotionEvent({
      from: "candidate", to: "active", reason: "go", timestamp: "2026-04-17T12:30:00.000Z",
    }));

    const before = readStatePointer(registryRoot, artifact.scenario, artifact.actuatorType, artifact.environmentTag);
    expect(before?.artifactId).toBe(artifact.id);

    rmSync(join(registryRoot, ".autocontext", "state"), { recursive: true, force: true });
    expect(readStatePointer(registryRoot, artifact.scenario, artifact.actuatorType, artifact.environmentTag)).toBeNull();

    repair(registryRoot);

    const after = readStatePointer(registryRoot, artifact.scenario, artifact.actuatorType, artifact.environmentTag);
    expect(after?.artifactId).toBe(artifact.id);
  });

  test("is idempotent: running repair twice produces the same pointers as running it once", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    reg.appendPromotionEvent(a.id, createPromotionEvent({
      from: "candidate", to: "active", reason: "go", timestamp: "2026-04-17T12:30:00.000Z",
    }));

    repair(registryRoot);
    const once = listStatePointers(registryRoot)
      .map((e) => `${e.scenario}|${e.actuatorType}|${e.environmentTag}|${e.pointer.artifactId}`)
      .sort();
    repair(registryRoot);
    const twice = listStatePointers(registryRoot)
      .map((e) => `${e.scenario}|${e.actuatorType}|${e.environmentTag}|${e.pointer.artifactId}`)
      .sort();
    expect(twice).toEqual(once);
  });

  test("does NOT create a pointer for an artifact whose final state is not active", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    reg.appendPromotionEvent(a.id, createPromotionEvent({
      from: "candidate", to: "shadow", reason: "shadow only", timestamp: "2026-04-17T12:30:00.000Z",
    }));

    rmSync(join(registryRoot, ".autocontext", "state"), { recursive: true, force: true });
    repair(registryRoot);

    expect(listStatePointers(registryRoot)).toEqual([]);
  });

  test("removes stale pointers that reference an artifact no longer in active state", () => {
    const reg = openRegistry(registryRoot);
    const { dir, hash } = tempPayload(registryRoot, "v1");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hash,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dir);
    reg.appendPromotionEvent(a.id, createPromotionEvent({
      from: "candidate", to: "active", reason: "go", timestamp: "2026-04-17T12:30:00.000Z",
    }));
    // Demote it directly via the registry.
    reg.appendPromotionEvent(a.id, createPromotionEvent({
      from: "active", to: "deprecated", reason: "step down", timestamp: "2026-04-17T13:00:00.000Z",
    }));
    // The facade flips the pointer when reaching active, but does NOT clear
    // it when leaving active (that's repair's job).
    repair(registryRoot);
    expect(listStatePointers(registryRoot)).toEqual([]);
  });

  test("when multiple artifacts have been active for the same tuple, the last (latest timestamp) wins", () => {
    const reg = openRegistry(registryRoot);

    const { dir: dirA, hash: hashA } = tempPayload(registryRoot, "vA");
    const a = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hashA,
      provenance: aProvenance,
    });
    reg.saveArtifact(a, dirA);
    reg.appendPromotionEvent(a.id, createPromotionEvent({
      from: "candidate", to: "active", reason: "first", timestamp: "2026-04-17T12:00:00.000Z",
    }));

    const { dir: dirB, hash: hashB } = tempPayload(registryRoot, "vB");
    const b = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hashB,
      provenance: aProvenance,
    });
    reg.saveArtifact(b, dirB);
    reg.appendPromotionEvent(b.id, createPromotionEvent({
      from: "candidate", to: "active", reason: "second", timestamp: "2026-04-17T13:00:00.000Z",
    }));

    rmSync(join(registryRoot, ".autocontext", "state"), { recursive: true, force: true });
    repair(registryRoot);

    const pointer = readStatePointer(registryRoot, a.scenario, a.actuatorType, a.environmentTag);
    expect(pointer?.artifactId).toBe(b.id);
  });

  test("creates the .autocontext/state/active directory if missing", () => {
    repair(registryRoot);
    // No artifacts → no pointers, but no crash.
    expect(existsSync(join(registryRoot, ".autocontext", "state", "active"))).toBe(true);
  });
});
