import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { renderPatches } from "../../../src/control-plane/emit/patch-renderer.js";
import { defaultWorkspaceLayout } from "../../../src/control-plane/emit/workspace-layout.js";
import { createArtifact } from "../../../src/control-plane/contract/factories.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import "../../../src/control-plane/actuators/index.js";
import type { Artifact, Provenance } from "../../../src/control-plane/contract/types.js";
import type { Scenario } from "../../../src/control-plane/contract/branded-ids.js";

const prov: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-patch-renderer-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

function writePromptPayload(dir: string, content: string): string {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "prompt.txt"), content, "utf-8");
  return dir;
}

describe("renderPatches — prompt-patch", () => {
  test("returns one Patch per affected file (v1: typically one)", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = writePromptPayload(join(tmp, "cand"), "new prompt\n");
    const artifact: Artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hashDirectory(payloadDir),
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    const patches = renderPatches({
      candidate: artifact,
      baseline: null,
      candidatePayloadDir: payloadDir,
      workingTreeRoot: wt,
      layout,
    });

    expect(patches).toHaveLength(1);
    expect(patches[0]!.operation).toBe("create");
    expect(patches[0]!.filePath).toMatch(/agents\/grid_ctf\/prompts\//);
    expect(patches[0]!.afterContent).toBe("new prompt\n");
  });

  test("null-baseline case: patch represents create against empty working tree", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = writePromptPayload(join(tmp, "cand"), "body\n");
    const artifact: Artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hashDirectory(payloadDir),
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    const patches = renderPatches({
      candidate: artifact,
      baseline: null,
      candidatePayloadDir: payloadDir,
      workingTreeRoot: wt,
      layout,
    });
    expect(patches[0]!.operation).toBe("create");
  });

  test("throws a clear error for an unregistered actuator type", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = writePromptPayload(join(tmp, "cand"), "body\n");
    const artifact: Artifact = createArtifact({
      actuatorType: "fine-tuned-model",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hashDirectory(payloadDir),
      provenance: prov,
    });
    // Mutate the type to a value never registered. Safe: the renderer should
    // detect this at getActuator() and throw a descriptive error.
    const mutated = { ...artifact, actuatorType: "nonexistent-type" } as unknown as Artifact;
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    expect(() =>
      renderPatches({
        candidate: mutated,
        baseline: null,
        candidatePayloadDir: payloadDir,
        workingTreeRoot: wt,
        layout,
      }),
    ).toThrow(/actuator|nonexistent-type/i);
  });
});
