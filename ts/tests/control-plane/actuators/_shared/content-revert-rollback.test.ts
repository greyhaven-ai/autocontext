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
import { contentRevertRollback } from "../../../../src/control-plane/actuators/_shared/content-revert-rollback.js";
import { hashDirectory } from "../../../../src/control-plane/registry/content-address.js";
import { createArtifact } from "../../../../src/control-plane/contract/factories.js";
import type { Artifact, Provenance } from "../../../../src/control-plane/contract/types.js";

const prov: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

function makePayload(
  dir: string,
  files: Record<string, string>,
): string {
  mkdirSync(dir, { recursive: true });
  for (const [name, content] of Object.entries(files)) {
    writeFileSync(join(dir, name), content, "utf-8");
  }
  return dir;
}

function mkArtifact(payloadDir: string): Artifact {
  return createArtifact({
    actuatorType: "prompt-patch",
    scenario: "grid_ctf",
    payloadHash: hashDirectory(payloadDir),
    provenance: prov,
  });
}

describe("contentRevertRollback", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "autocontext-crr-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  test("emits a Patch that, applied to the candidate's working-tree content, restores baseline", () => {
    const candidatePayloadDir = makePayload(join(tmp, "candidate-payload"), {
      "prompt.txt": "candidate v2 body\n",
    });
    const baselinePayloadDir = makePayload(join(tmp, "baseline-payload"), {
      "prompt.txt": "baseline v1 body\n",
    });
    const candidate = mkArtifact(candidatePayloadDir);
    const baseline = mkArtifact(baselinePayloadDir);

    const targetPath = join(tmp, "wt", "prompt.txt");
    mkdirSync(join(tmp, "wt"), { recursive: true });
    writeFileSync(targetPath, "candidate v2 body\n", "utf-8");

    const patch = contentRevertRollback({
      candidate,
      baseline,
      baselinePayloadDir,
      payloadFileName: "prompt.txt",
      resolvedTargetPath: targetPath,
    });

    expect(patch.filePath).toBe(targetPath);
    expect(patch.operation).toBe("modify");
    expect(patch.afterContent).toBe("baseline v1 body\n");

    // Applying the patch to the candidate content yields the baseline content.
    const applied = applyPatch("candidate v2 body\n", patch.unifiedDiff);
    expect(applied).toBe("baseline v1 body\n");
  });

  test("treats a missing working-tree file as empty string (operation=create on revert)", () => {
    const baselinePayloadDir = makePayload(join(tmp, "baseline-payload"), {
      "prompt.txt": "baseline body\n",
    });
    const candidatePayloadDir = makePayload(join(tmp, "candidate-payload"), {
      "prompt.txt": "",
    });
    const candidate = mkArtifact(candidatePayloadDir);
    const baseline = mkArtifact(baselinePayloadDir);

    const targetPath = join(tmp, "wt", "absent.txt"); // file doesn't exist

    const patch = contentRevertRollback({
      candidate,
      baseline,
      baselinePayloadDir,
      payloadFileName: "prompt.txt",
      resolvedTargetPath: targetPath,
    });
    expect(patch.operation).toBe("create");
    expect(patch.afterContent).toBe("baseline body\n");
    // Sanity — did not write anything.
    expect(existsSync(targetPath)).toBe(false);
  });

  test("throws if the baseline payload file is missing", () => {
    const baselinePayloadDir = makePayload(join(tmp, "baseline-payload"), {
      "other.txt": "irrelevant",
    });
    const candidatePayloadDir = makePayload(join(tmp, "candidate-payload"), {
      "prompt.txt": "cand",
    });
    const candidate = mkArtifact(candidatePayloadDir);
    const baseline = mkArtifact(baselinePayloadDir);
    const targetPath = join(tmp, "wt", "prompt.txt");
    mkdirSync(join(tmp, "wt"), { recursive: true });
    writeFileSync(targetPath, "cand", "utf-8");

    expect(() =>
      contentRevertRollback({
        candidate,
        baseline,
        baselinePayloadDir,
        payloadFileName: "prompt.txt",
        resolvedTargetPath: targetPath,
      }),
    ).toThrow(/baseline payload.*prompt\.txt/);
  });

  test("when baseline content equals candidate content, operation is no-op modify", () => {
    const baselinePayloadDir = makePayload(join(tmp, "baseline-payload"), {
      "prompt.txt": "same\n",
    });
    const candidatePayloadDir = makePayload(join(tmp, "candidate-payload"), {
      "prompt.txt": "same\n",
    });
    const candidate = mkArtifact(candidatePayloadDir);
    const baseline = mkArtifact(baselinePayloadDir);
    const targetPath = join(tmp, "wt", "prompt.txt");
    mkdirSync(join(tmp, "wt"), { recursive: true });
    writeFileSync(targetPath, "same\n", "utf-8");

    const patch = contentRevertRollback({
      candidate,
      baseline,
      baselinePayloadDir,
      payloadFileName: "prompt.txt",
      resolvedTargetPath: targetPath,
    });
    expect(patch.operation).toBe("modify");
    expect(applyPatch("same\n", patch.unifiedDiff)).toBe("same\n");
    // Reading the target file (unchanged) to silence unused var hint.
    expect(readFileSync(targetPath, "utf-8")).toBe("same\n");
  });
});
