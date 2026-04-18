import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  saveArtifact,
  loadArtifact,
  updateArtifactMetadata,
} from "../../../src/control-plane/registry/artifact-store.js";
import { createArtifact, createPromotionEvent } from "../../../src/control-plane/contract/factories.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { appendPromotionEvent } from "../../../src/control-plane/promotion/append.js";
import type { Provenance } from "../../../src/control-plane/contract/types.js";
import type { ContentHash } from "../../../src/control-plane/contract/branded-ids.js";

const aProvenance: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

function tempPayload(parent: string): { dir: string; hash: ContentHash } {
  const dir = join(parent, "src-" + Math.random().toString(36).slice(2));
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "f.txt"), "x");
  return { dir, hash: hashDirectory(dir) };
}

describe("updateArtifactMetadata", () => {
  let registryRoot: string;

  beforeEach(() => {
    registryRoot = mkdtempSync(join(tmpdir(), "autocontext-update-"));
  });

  afterEach(() => {
    rmSync(registryRoot, { recursive: true, force: true });
  });

  test("rewrites metadata.json without touching payload", () => {
    const { dir, hash } = tempPayload(registryRoot);
    const original = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: aProvenance,
    });
    saveArtifact(registryRoot, original, dir);

    const next = appendPromotionEvent(
      original,
      createPromotionEvent({
        from: "candidate",
        to: "shadow",
        reason: "test",
        timestamp: "2026-04-17T12:30:00.000Z",
      }),
    );
    updateArtifactMetadata(registryRoot, next);

    const reloaded = loadArtifact(registryRoot, original.id);
    expect(reloaded.activationState).toBe("shadow");
    expect(reloaded.promotionHistory).toHaveLength(1);
  });

  test("refuses if the new metadata's payloadHash does not match the on-disk payload", () => {
    const { dir, hash } = tempPayload(registryRoot);
    const original = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: aProvenance,
    });
    saveArtifact(registryRoot, original, dir);

    const tampered = { ...original, payloadHash: ("sha256:" + "0".repeat(64)) as ContentHash };
    expect(() => updateArtifactMetadata(registryRoot, tampered)).toThrow(/payload.*hash/i);
  });

  test("throws when the artifact directory does not exist", () => {
    const { hash } = tempPayload(registryRoot);
    const orphan = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: aProvenance,
    });
    expect(() => updateArtifactMetadata(registryRoot, orphan)).toThrow(/not found/i);
  });

  test("refuses to update with an Artifact whose id changed", () => {
    const { dir, hash } = tempPayload(registryRoot);
    const original = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: aProvenance,
    });
    saveArtifact(registryRoot, original, dir);

    const wrongId = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: aProvenance,
    });
    expect(() => updateArtifactMetadata(registryRoot, wrongId)).toThrow();
  });
});
