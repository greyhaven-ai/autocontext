import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { applyPatch } from "diff";
import {
  mkdtempSync,
  mkdirSync,
  rmSync,
  writeFileSync,
  readFileSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promptPatchActuator } from "../../../../src/control-plane/actuators/prompt-patch/applicator.js";
import { promptPatchRegistration } from "../../../../src/control-plane/actuators/prompt-patch/index.js";
import { hashDirectory } from "../../../../src/control-plane/registry/content-address.js";
import { createArtifact } from "../../../../src/control-plane/contract/factories.js";
import { defaultWorkspaceLayout } from "../../../../src/control-plane/emit/workspace-layout.js";
import type { Artifact, Provenance } from "../../../../src/control-plane/contract/types.js";

const prov: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

function mkPayload(dir: string, content: string): { dir: string } {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "prompt.txt"), content, "utf-8");
  return { dir };
}

function mkArtifact(payloadDir: string): Artifact {
  return createArtifact({
    actuatorType: "prompt-patch",
    scenario: "grid_ctf",
    payloadHash: hashDirectory(payloadDir),
    provenance: prov,
  });
}

describe("prompt-patch actuator registration", () => {
  test("declares content-revert rollback and a prompts-path allowedTargetPattern", () => {
    expect(promptPatchRegistration.type).toBe("prompt-patch");
    expect(promptPatchRegistration.rollback).toEqual({ kind: "content-revert" });
    expect(promptPatchRegistration.allowedTargetPattern).toMatch(/prompts/);
  });
});

describe("prompt-patch actuator", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "autocontext-prompt-patch-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  test("parsePayload accepts a string and rejects non-strings", () => {
    expect(promptPatchActuator.parsePayload("hello")).toBe("hello");
    expect(() => promptPatchActuator.parsePayload(42)).toThrow();
    expect(() => promptPatchActuator.parsePayload({ content: "x" })).toThrow();
  });

  test("resolveTargetPath places the file under <scenarioDir>/<promptSubdir>/ with .txt extension", () => {
    const layout = defaultWorkspaceLayout();
    const { dir: payloadDir } = mkPayload(join(tmp, "payload"), "body\n");
    const artifact = mkArtifact(payloadDir);

    const target = promptPatchActuator.resolveTargetPath(artifact, layout);
    expect(target).toMatch(/agents\/grid_ctf\/prompts\//);
    expect(target).toMatch(/\.txt$/);
    // Path includes the artifact id for uniqueness.
    expect(target).toContain(artifact.id);
  });

  test("apply writes the payload contents to the resolved target path in the working tree", async () => {
    const layout = defaultWorkspaceLayout();
    const { dir: payloadDir } = mkPayload(join(tmp, "payload"), "system prompt body\n");
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    await promptPatchActuator.apply({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });

    const target = join(wt, promptPatchActuator.resolveTargetPath(artifact, layout));
    expect(existsSync(target)).toBe(true);
    expect(readFileSync(target, "utf-8")).toBe("system prompt body\n");
  });

  test("emitPatch produces a Patch whose unifiedDiff roundtrips via diff.applyPatch", () => {
    const layout = defaultWorkspaceLayout();
    const { dir: payloadDir } = mkPayload(join(tmp, "payload"), "new\nprompt\n");
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    const patch = promptPatchActuator.emitPatch({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });
    expect(patch.operation).toBe("create");
    expect(patch.afterContent).toBe("new\nprompt\n");
    expect(applyPatch("", patch.unifiedDiff)).toBe("new\nprompt\n");
  });

  test("rollback with content-revert strategy returns a patch that reverts to baseline content", async () => {
    const layout = defaultWorkspaceLayout();
    const { dir: candDir } = mkPayload(join(tmp, "cand"), "candidate body\n");
    // baseline payload dir has its own prompt.txt
    const baseDir = join(tmp, "base");
    mkdirSync(baseDir, { recursive: true });
    writeFileSync(join(baseDir, "prompt.txt"), "baseline body\n", "utf-8");

    const candidate = mkArtifact(candDir);
    const baseline = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hashDirectory(baseDir),
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    // Simulate that the candidate has already been applied in the working tree.
    await promptPatchActuator.apply({
      artifact: candidate,
      payloadDir: candDir,
      workingTreeRoot: wt,
      layout,
    });

    const patches = await promptPatchActuator.rollback({
      candidate,
      baseline,
      candidatePayloadDir: candDir,
      baselinePayloadDir: baseDir,
      workingTreeRoot: wt,
      layout,
    });
    const patch = Array.isArray(patches) ? patches[0]! : patches;
    expect(patch.afterContent).toBe("baseline body\n");
    expect(applyPatch("candidate body\n", patch.unifiedDiff)).toBe("baseline body\n");
  });
});
