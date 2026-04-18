import { describe, test, expect } from "vitest";
import { branchNameFor } from "../../../src/control-plane/emit/branch-namer.js";
import { createArtifact } from "../../../src/control-plane/contract/factories.js";
import type { ArtifactId, ContentHash, Scenario } from "../../../src/control-plane/contract/branded-ids.js";
import type { Artifact, Provenance } from "../../../src/control-plane/contract/types.js";

const prov: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

function mk(id: ArtifactId, scenario: Scenario): Artifact {
  return createArtifact({
    id,
    actuatorType: "prompt-patch",
    scenario,
    payloadHash: "sha256:00" as ContentHash,
    provenance: prov,
  });
}

describe("branchNameFor", () => {
  test("follows the autocontext/<scenario>/<actuatorType>/<short-id> format", () => {
    const a = mk("01HZABCDEFGHJKMNPQRSTVWXYZ" as ArtifactId, "grid_ctf" as Scenario);
    expect(branchNameFor(a)).toBe("autocontext/grid_ctf/prompt-patch/01HZABCD");
  });

  test("uses the first 8 characters of the ULID", () => {
    const a = mk("01HZABCDEFGHJKMNPQRSTVWXYZ" as ArtifactId, "grid_ctf" as Scenario);
    const b = mk("01HZABCDEFXXXXXXXXXXXXXXXX" as ArtifactId, "grid_ctf" as Scenario);
    // First 8 chars match → branch names collide (by design — the spec calls
    // them "collision-safe" but bases that on full ULID random component; the
    // 8-char prefix is for greppability, not uniqueness).
    expect(branchNameFor(a).endsWith("01HZABCD")).toBe(true);
    expect(branchNameFor(b).endsWith("01HZABCD")).toBe(true);
  });

  test("includes actuatorType from the artifact", () => {
    const a = createArtifact({
      id: "01HZABCDEFGHJKMNPQRSTVWXYZ" as ArtifactId,
      actuatorType: "tool-policy",
      scenario: "grid_ctf" as Scenario,
      payloadHash: "sha256:00" as ContentHash,
      provenance: prov,
    });
    expect(branchNameFor(a)).toBe("autocontext/grid_ctf/tool-policy/01HZABCD");
  });

  test("handles different scenarios", () => {
    const a = mk("01HZABCDEFGHJKMNPQRSTVWXYZ" as ArtifactId, "othello" as Scenario);
    expect(branchNameFor(a)).toBe("autocontext/othello/prompt-patch/01HZABCD");
  });

  test("deterministic — same input yields same output", () => {
    const a = mk("01HZABCDEFGHJKMNPQRSTVWXYZ" as ArtifactId, "grid_ctf" as Scenario);
    expect(branchNameFor(a)).toBe(branchNameFor(a));
  });

  test("different artifacts with different ids and scenarios produce different branches", () => {
    const a = mk("01HZAAAAAAAAAAAAAAAAAAAAAA" as ArtifactId, "grid_ctf" as Scenario);
    const b = mk("01HZBBBBBBBBBBBBBBBBBBBBBB" as ArtifactId, "othello" as Scenario);
    expect(branchNameFor(a)).not.toBe(branchNameFor(b));
  });
});
