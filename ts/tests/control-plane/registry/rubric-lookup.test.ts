import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { createArtifact, createPromotionEvent } from "../../../src/control-plane/contract/factories.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import { createRegistryRubricLookup } from "../../../src/control-plane/registry/rubric-lookup.js";
import { defaultEnvironmentTag, parseScenario } from "../../../src/production-traces/contract/branded-ids.js";

describe("createRegistryRubricLookup", () => {
  let root: string;

  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), "autocontext-rubric-lookup-"));
  });

  afterEach(() => {
    rmSync(root, { recursive: true, force: true });
  });

  test("adapts an active control-plane artifact without a reverse production-traces import", async () => {
    const payloadDir = join(root, "payload");
    mkdirSync(payloadDir, { recursive: true });
    writeFileSync(join(payloadDir, "prompt.txt"), "You are helpful.\n", "utf-8");

    const scenario = parseScenario("grid_ctf");
    if (scenario === null) throw new Error("invalid scenario fixture");

    const registry = openRegistry(root);
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario,
      environmentTag: defaultEnvironmentTag(),
      payloadHash: hashDirectory(payloadDir),
      provenance: {
        authorType: "human",
        authorId: "test",
        parentArtifactIds: [],
        createdAt: "2026-08-13T00:00:00.000Z",
      },
    });
    registry.saveArtifact(artifact, payloadDir);
    registry.appendPromotionEvent(
      artifact.id,
      createPromotionEvent({
        from: "candidate",
        to: "active",
        reason: "test fixture",
        timestamp: "2026-08-13T00:01:00.000Z",
      }),
    );

    const rubric = await createRegistryRubricLookup(root)(scenario);
    expect(rubric).toMatchObject({
      rubricId: artifact.id,
      dimensions: ["registry-active-artifact"],
    });
  });
});
