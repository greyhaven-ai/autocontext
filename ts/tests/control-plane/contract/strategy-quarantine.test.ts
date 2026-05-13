import { describe, expect, test } from "vitest";
import {
  parseArtifactId,
  parseContentHash,
  type ArtifactId,
  type ContentHash,
} from "../../../src/control-plane/contract/branded-ids.js";
import type { Artifact } from "../../../src/control-plane/contract/types.js";
import { buildStrategyIdentity } from "../../../src/control-plane/contract/strategy-identity.js";
import {
  assessStrategyQuarantine,
  describeStrategyQuarantine,
} from "../../../src/control-plane/contract/strategy-quarantine.js";

function hash(fill: string): ContentHash {
  const parsed = parseContentHash(`sha256:${fill.repeat(64)}`);
  if (parsed === null) throw new Error(`invalid test hash fill: ${fill}`);
  return parsed;
}

function id(value: string): ArtifactId {
  const parsed = parseArtifactId(value);
  if (parsed === null) throw new Error(`invalid test artifact id: ${value}`);
  return parsed;
}

function identity(fill: string) {
  return buildStrategyIdentity({
    actuatorType: "prompt-patch",
    scenario: "grid_ctf",
    payloadHash: hash(fill),
    components: [
      { name: "prompt.txt", fingerprint: hash(fill) },
      { name: "notes.md", fingerprint: hash("9") },
    ],
    parentFingerprints: [],
  });
}

function artifact(overrides: Pick<Artifact, "id"> & Partial<Artifact>): Artifact {
  return {
    schemaVersion: "1.0",
    id: overrides.id,
    actuatorType: overrides.actuatorType ?? "prompt-patch",
    scenario: overrides.scenario ?? "grid_ctf",
    environmentTag: overrides.environmentTag ?? "production",
    activationState: overrides.activationState ?? "candidate",
    payloadHash: overrides.payloadHash ?? hash("a"),
    provenance: overrides.provenance ?? {
      authorType: "autocontext-run",
      authorId: "test",
      parentArtifactIds: [],
      createdAt: "2026-04-17T12:00:00.000Z",
    },
    ...(overrides.strategyIdentity !== undefined
      ? { strategyIdentity: overrides.strategyIdentity }
      : {}),
    promotionHistory: overrides.promotionHistory ?? [],
    evalRuns: overrides.evalRuns ?? [],
    ...(overrides.strategyQuarantine !== undefined
      ? { strategyQuarantine: overrides.strategyQuarantine }
      : {}),
  };
}

describe("strategy quarantine domain", () => {
  test("does not quarantine unique strategies", () => {
    const candidate = identity("a");
    const unrelated = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("b"),
      components: [{ name: "unrelated.txt", fingerprint: hash("b") }],
      parentFingerprints: [],
    });
    const prior = artifact({
      id: id("01KPEYB3BQNFDEYRS8KH538PF5"),
      strategyIdentity: unrelated,
      activationState: "disabled",
    });

    expect(assessStrategyQuarantine(candidate, "prompt-patch", "grid_ctf", [prior])).toBeNull();
  });

  test("quarantines repeated exact matches of disabled strategies", () => {
    const invalidIdentity = identity("a");
    const prior = artifact({
      id: id("01KPEYB3BQNFDEYRS8KH538PF5"),
      strategyIdentity: invalidIdentity,
      activationState: "disabled",
    });

    expect(assessStrategyQuarantine(invalidIdentity, "prompt-patch", "grid_ctf", [prior])).toEqual({
      status: "quarantined",
      reason: "repeated-invalid-strategy",
      sourceArtifactIds: [prior.id],
      sourceFingerprints: [invalidIdentity.fingerprint],
      detail: `exact duplicate of disabled/quarantined artifact ${prior.id}`,
    });
  });

  test("quarantines legacy disabled artifacts that only have a matching payload hash", () => {
    const candidate = identity("a");
    const legacyDisabled = artifact({
      id: id("01KPEYB3BQNFDEYRS8KH538PF5"),
      payloadHash: hash("a"),
      activationState: "disabled",
    });

    expect(assessStrategyQuarantine(candidate, "prompt-patch", "grid_ctf", [legacyDisabled])).toEqual({
      status: "quarantined",
      reason: "repeated-invalid-strategy",
      sourceArtifactIds: [legacyDisabled.id],
      sourceFingerprints: [legacyDisabled.payloadHash],
      detail: `exact duplicate of disabled/quarantined artifact ${legacyDisabled.id}`,
    });
  });

  test("quarantines near matches of already quarantined strategies", () => {
    const priorIdentity = identity("a");
    const prior = artifact({
      id: id("01KPEYB3BQNFDEYRS8KH538PF5"),
      strategyIdentity: priorIdentity,
      strategyQuarantine: {
        status: "quarantined",
        reason: "repeated-invalid-strategy",
        sourceArtifactIds: [],
        sourceFingerprints: [priorIdentity.fingerprint],
      },
    });
    const near = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("b"),
      components: [
        { name: "prompt.txt", fingerprint: hash("b") },
        { name: "notes.md", fingerprint: hash("9") },
      ],
      parentFingerprints: [],
    });

    const quarantine = assessStrategyQuarantine(near, "prompt-patch", "grid_ctf", [prior]);

    expect(quarantine).toMatchObject({
      status: "quarantined",
      reason: "repeated-invalid-strategy",
      sourceArtifactIds: [prior.id],
      sourceFingerprints: [priorIdentity.fingerprint],
    });
    expect(quarantine?.detail).toContain("near duplicate");
  });

  test("describes quarantined artifacts as non-promotion evidence", () => {
    const priorIdentity = identity("a");
    const quarantined = artifact({
      id: id("01KPEYB3BQNFDEYRS8KH538PF5"),
      strategyIdentity: priorIdentity,
      strategyQuarantine: {
        status: "quarantined",
        reason: "contaminated-finding",
        sourceArtifactIds: [],
        sourceFingerprints: [priorIdentity.fingerprint],
        detail: "memory finding came from contaminated evidence",
      },
    });

    expect(describeStrategyQuarantine(quarantined, "candidate")).toBe(
      "candidate strategy is quarantined (contaminated-finding)",
    );
  });
});
