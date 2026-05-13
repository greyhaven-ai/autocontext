import { describe, expect, test } from "vitest";
import type { Artifact } from "../../../src/control-plane/contract/types.js";
import {
  parseArtifactId,
  parseContentHash,
  type ArtifactId,
  type ContentHash,
} from "../../../src/control-plane/contract/branded-ids.js";
import {
  buildStrategyComponentsFromTree,
  buildStrategyIdentity,
  detectStrategyDuplicate,
  strategyFingerprintForArtifact,
} from "../../../src/control-plane/contract/strategy-identity.js";

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

function artifact(
  overrides: Partial<Artifact> & Pick<Artifact, "id" | "payloadHash">,
): Artifact {
  return {
    schemaVersion: "1.0",
    id: overrides.id,
    actuatorType: overrides.actuatorType ?? "prompt-patch",
    scenario: overrides.scenario ?? "grid_ctf",
    environmentTag: overrides.environmentTag ?? "production",
    activationState: overrides.activationState ?? "candidate",
    payloadHash: overrides.payloadHash,
    provenance: overrides.provenance ?? {
      authorType: "autocontext-run",
      authorId: "test",
      parentArtifactIds: [],
      createdAt: "2026-04-17T12:00:00.000Z",
    },
    promotionHistory: overrides.promotionHistory ?? [],
    evalRuns: overrides.evalRuns ?? [],
    ...(overrides.strategyIdentity !== undefined
      ? { strategyIdentity: overrides.strategyIdentity }
      : {}),
  };
}

describe("strategy identity domain", () => {
  test("canonicalizes component order before computing fingerprints", () => {
    const left = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("a"),
      components: [
        { name: "config.json", fingerprint: hash("b") },
        { name: "prompt.txt", fingerprint: hash("c") },
      ],
      parentFingerprints: [],
    });
    const right = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("a"),
      components: [
        { name: "prompt.txt", fingerprint: hash("c") },
        { name: "config.json", fingerprint: hash("b") },
      ],
      parentFingerprints: [],
    });

    expect(right.fingerprint).toBe(left.fingerprint);
    expect(right.components.map((c) => c.name)).toEqual(["config.json", "prompt.txt"]);
  });

  test("prompt, tool, and config changes alter the strategy fingerprint", () => {
    const base = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("a"),
      components: [{ name: "prompt.txt", fingerprint: hash("b") }],
      parentFingerprints: [],
    });
    const promptChanged = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("c"),
      components: [{ name: "prompt.txt", fingerprint: hash("d") }],
      parentFingerprints: [],
    });
    const toolChanged = buildStrategyIdentity({
      actuatorType: "tool-policy",
      scenario: "grid_ctf",
      payloadHash: hash("a"),
      components: [{ name: "policy.json", fingerprint: hash("b") }],
      parentFingerprints: [],
    });

    expect(promptChanged.fingerprint).not.toBe(base.fingerprint);
    expect(toolChanged.fingerprint).not.toBe(base.fingerprint);
  });

  test("records sorted unique parent fingerprints as lineage", () => {
    const identity = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("a"),
      components: [],
      parentFingerprints: [hash("c"), hash("b"), hash("c")],
    });

    expect(identity.payloadHash).toBe(hash("a"));
    expect(identity.lineage.parentFingerprints).toEqual([hash("b"), hash("c")]);
  });

  test("detects legacy exact duplicates by payload hash when prior identity metadata is absent", () => {
    const candidate = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("a"),
      components: [{ name: "prompt.txt", fingerprint: hash("b") }],
      parentFingerprints: [],
    });
    const prior = artifact({
      id: id("01KPEYB3BQNFDEYRS8KH538PF5"),
      payloadHash: hash("a"),
    });

    expect(detectStrategyDuplicate(candidate, "prompt-patch", "grid_ctf", [prior])).toEqual({
      kind: "exact",
      artifactId: prior.id,
      fingerprint: prior.payloadHash,
      similarity: 1,
    });
  });

  test("derives component fingerprints from payload tree files", () => {
    const components = buildStrategyComponentsFromTree([
      { path: "prompt.txt", content: Buffer.from("alpha") },
      { path: "nested/config.json", content: Buffer.from('{"b":2,"a":1}') },
    ]);

    expect(components.map((c) => c.name)).toEqual(["nested/config.json", "prompt.txt"]);
    expect(components.every((c) => /^sha256:[0-9a-f]{64}$/.test(c.fingerprint))).toBe(true);
  });

  test("detects exact and near duplicates within the same strategy surface", () => {
    const originalIdentity = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("a"),
      components: [
        { name: "config.json", fingerprint: hash("b") },
        { name: "prompt.txt", fingerprint: hash("c") },
      ],
      parentFingerprints: [],
    });
    const exact = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("a"),
      components: [
        { name: "prompt.txt", fingerprint: hash("c") },
        { name: "config.json", fingerprint: hash("b") },
      ],
      parentFingerprints: [],
    });
    const near = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("d"),
      components: [
        { name: "config.json", fingerprint: hash("b") },
        { name: "prompt.txt", fingerprint: hash("e") },
      ],
      parentFingerprints: [],
    });

    const prior = artifact({
      id: id("01KPEYB3BQNFDEYRS8KH538PF5"),
      payloadHash: hash("a"),
      strategyIdentity: originalIdentity,
    });

    expect(detectStrategyDuplicate(exact, "prompt-patch", "grid_ctf", [prior])).toEqual({
      kind: "exact",
      artifactId: prior.id,
      fingerprint: originalIdentity.fingerprint,
      similarity: 1,
    });

    expect(detectStrategyDuplicate(near, "prompt-patch", "grid_ctf", [prior])).toMatchObject({
      kind: "near",
      artifactId: prior.id,
      fingerprint: originalIdentity.fingerprint,
    });
  });

  test("does not flag duplicates across different actuators or scenarios", () => {
    const identity = buildStrategyIdentity({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash("a"),
      components: [{ name: "prompt.txt", fingerprint: hash("b") }],
      parentFingerprints: [],
    });
    const prior = artifact({
      id: id("01KPEYB3BQNFDEYRS8KH538PF5"),
      scenario: "othello",
      payloadHash: hash("a"),
      strategyIdentity: identity,
    });

    expect(detectStrategyDuplicate(identity, "prompt-patch", "grid_ctf", [prior])).toBeNull();
  });

  test("falls back to a deterministic legacy fingerprint for older artifacts", () => {
    const prior = artifact({
      id: id("01KPEYB3BQNFDEYRS8KH538PF5"),
      payloadHash: hash("a"),
    });

    expect(strategyFingerprintForArtifact(prior)).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(strategyFingerprintForArtifact(prior)).toBe(strategyFingerprintForArtifact(prior));
  });
});
