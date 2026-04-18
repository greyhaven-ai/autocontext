import { describe, test, expect, beforeEach, afterEach } from "vitest";
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
import { applySingleFile } from "../../../../src/control-plane/actuators/_shared/single-file-applicator.js";
import { hashDirectory } from "../../../../src/control-plane/registry/content-address.js";
import { createArtifact } from "../../../../src/control-plane/contract/factories.js";
import { parseContentHash } from "../../../../src/control-plane/contract/branded-ids.js";
import type { Provenance } from "../../../../src/control-plane/contract/types.js";

const prov: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

describe("applySingleFile", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "autocontext-single-file-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  test("writes the payload file to the target path in the working tree", () => {
    const payloadDir = join(tmp, "payload");
    mkdirSync(payloadDir, { recursive: true });
    writeFileSync(join(payloadDir, "prompt.txt"), "hello world\n", "utf-8");
    const hash = hashDirectory(payloadDir);
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });
    const target = join(wt, "agents", "grid_ctf", "prompts", "out.txt");

    applySingleFile({
      artifact,
      payloadDir,
      payloadFileName: "prompt.txt",
      resolvedTargetPath: target,
    });

    expect(existsSync(target)).toBe(true);
    expect(readFileSync(target, "utf-8")).toBe("hello world\n");
  });

  test("creates intermediate directories for the target path if needed", () => {
    const payloadDir = join(tmp, "payload");
    mkdirSync(payloadDir, { recursive: true });
    writeFileSync(join(payloadDir, "policy.json"), '{"version":"1","tools":{}}');
    const hash = hashDirectory(payloadDir);
    const artifact = createArtifact({
      actuatorType: "tool-policy",
      scenario: "othello",
      payloadHash: hash,
      provenance: prov,
    });
    const target = join(tmp, "root", "deep", "nested", "dir", "policy.json");

    applySingleFile({
      artifact,
      payloadDir,
      payloadFileName: "policy.json",
      resolvedTargetPath: target,
    });

    expect(existsSync(target)).toBe(true);
  });

  test("refuses to write when the on-disk payload tree hash does not match artifact.payloadHash", () => {
    const payloadDir = join(tmp, "payload");
    mkdirSync(payloadDir, { recursive: true });
    writeFileSync(join(payloadDir, "prompt.txt"), "old", "utf-8");
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      // Deliberately wrong hash — claims the payload has some other content.
      payloadHash: parseContentHash(
        "sha256:" + "0".repeat(64),
      )!,
      provenance: prov,
    });
    const target = join(tmp, "wt", "target.txt");
    expect(() =>
      applySingleFile({
        artifact,
        payloadDir,
        payloadFileName: "prompt.txt",
        resolvedTargetPath: target,
      }),
    ).toThrow(/hash.*mismatch/i);
    expect(existsSync(target)).toBe(false);
  });

  test("throws when the named payload file is missing from the payload directory", () => {
    const payloadDir = join(tmp, "payload");
    mkdirSync(payloadDir, { recursive: true });
    writeFileSync(join(payloadDir, "other.txt"), "nothing", "utf-8");
    const hash = hashDirectory(payloadDir);
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: prov,
    });
    expect(() =>
      applySingleFile({
        artifact,
        payloadDir,
        payloadFileName: "missing.txt",
        resolvedTargetPath: join(tmp, "wt", "x.txt"),
      }),
    ).toThrow(/payload file.*missing/i);
  });

  test("overwrites an existing file at the target path", () => {
    const payloadDir = join(tmp, "payload");
    mkdirSync(payloadDir, { recursive: true });
    writeFileSync(join(payloadDir, "prompt.txt"), "new content", "utf-8");
    const hash = hashDirectory(payloadDir);
    const artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf",
      payloadHash: hash,
      provenance: prov,
    });
    const target = join(tmp, "wt", "target.txt");
    mkdirSync(join(tmp, "wt"), { recursive: true });
    writeFileSync(target, "old content", "utf-8");

    applySingleFile({
      artifact,
      payloadDir,
      payloadFileName: "prompt.txt",
      resolvedTargetPath: target,
    });

    expect(readFileSync(target, "utf-8")).toBe("new content");
  });
});
