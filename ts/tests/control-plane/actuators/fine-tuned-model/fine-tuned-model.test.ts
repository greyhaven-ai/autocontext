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
import { fineTunedModelActuator } from "../../../../src/control-plane/actuators/fine-tuned-model/applicator.js";
import { fineTunedModelRegistration } from "../../../../src/control-plane/actuators/fine-tuned-model/index.js";
import { importLegacyModelRecords } from "../../../../src/control-plane/actuators/fine-tuned-model/legacy-adapter.js";
import { hashDirectory } from "../../../../src/control-plane/registry/content-address.js";
import { createArtifact } from "../../../../src/control-plane/contract/factories.js";
import { defaultWorkspaceLayout } from "../../../../src/control-plane/emit/workspace-layout.js";
import { parseContentHash } from "../../../../src/control-plane/contract/branded-ids.js";
import type { Artifact, Provenance } from "../../../../src/control-plane/contract/types.js";

const prov: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

const VALID_POINTER = {
  kind: "model-checkpoint",
  externalPath: "s3://ckpts/grid_ctf-v5.safetensors",
  checkpointHash: "sha256:" + "a".repeat(64),
  family: "llama-3",
  backend: "mlx",
};

function mkPayload(dir: string, pointer: object): string {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "pointer.json"), JSON.stringify(pointer, null, 2), "utf-8");
  return dir;
}

function mkArtifact(payloadDir: string): Artifact {
  return createArtifact({
    actuatorType: "fine-tuned-model",
    scenario: "grid_ctf",
    payloadHash: hashDirectory(payloadDir),
    provenance: prov,
  });
}

describe("fine-tuned-model actuator registration", () => {
  test("declares pointer-flip rollback and a models/active path pattern", () => {
    expect(fineTunedModelRegistration.type).toBe("fine-tuned-model");
    expect(fineTunedModelRegistration.rollback).toEqual({ kind: "pointer-flip" });
    expect(fineTunedModelRegistration.allowedTargetPattern).toMatch(/models\/active/);
  });
});

describe("fine-tuned-model actuator", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "autocontext-ftm-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  test("parsePayload accepts a valid pointer and rejects malformed ones", () => {
    expect(fineTunedModelActuator.parsePayload(VALID_POINTER)).toBeTruthy();
    expect(() =>
      fineTunedModelActuator.parsePayload({ ...VALID_POINTER, kind: "other" }),
    ).toThrow();
    expect(() =>
      fineTunedModelActuator.parsePayload({ ...VALID_POINTER, checkpointHash: "not-a-hash" }),
    ).toThrow();
    expect(() => fineTunedModelActuator.parsePayload({ externalPath: "x" })).toThrow();
  });

  test("resolveTargetPath places the pointer file under <scenarioDir>/<modelPointerSubdir>/ with .json", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_POINTER);
    const artifact = mkArtifact(payloadDir);
    const target = fineTunedModelActuator.resolveTargetPath(artifact, layout);
    expect(target).toMatch(/models\/active\//);
    expect(target).toMatch(/\.json$/);
    expect(target).toContain(artifact.id);
  });

  test("apply writes the pointer.json payload to the resolved target", async () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_POINTER);
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    await fineTunedModelActuator.apply({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });
    const target = join(wt, fineTunedModelActuator.resolveTargetPath(artifact, layout));
    expect(existsSync(target)).toBe(true);
    expect(JSON.parse(readFileSync(target, "utf-8"))).toEqual(VALID_POINTER);
  });

  test("emitPatch roundtrips via diff.applyPatch", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_POINTER);
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    const patch = fineTunedModelActuator.emitPatch({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });
    expect(patch.operation).toBe("create");
    expect(applyPatch("", patch.unifiedDiff)).toBe(patch.afterContent);
  });

  test("rollback returns a pointer-diff Patch (no bulk content — the diff is just the pointer JSON)", async () => {
    const layout = defaultWorkspaceLayout();
    const candPayload = { ...VALID_POINTER, externalPath: "s3://ckpts/new.safetensors" };
    const basePayload = VALID_POINTER;
    const candDir = mkPayload(join(tmp, "cand"), candPayload);
    const baseDir = mkPayload(join(tmp, "base"), basePayload);
    const candidate = mkArtifact(candDir);
    const baseline = createArtifact({
      actuatorType: "fine-tuned-model",
      scenario: "grid_ctf",
      payloadHash: hashDirectory(baseDir),
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });
    await fineTunedModelActuator.apply({
      artifact: candidate,
      payloadDir: candDir,
      workingTreeRoot: wt,
      layout,
    });

    const patches = await fineTunedModelActuator.rollback({
      candidate,
      baseline,
      candidatePayloadDir: candDir,
      baselinePayloadDir: baseDir,
      workingTreeRoot: wt,
      layout,
    });
    const patch = Array.isArray(patches) ? patches[0]! : patches;
    // The patch body should be small — just the JSON pointer, not bulk content.
    expect(patch.afterContent).toBe(readFileSync(join(baseDir, "pointer.json"), "utf-8"));
    // It is still a valid unified diff.
    expect(patch.unifiedDiff).toMatch(/@@/);
    // Rollback does NOT mutate the working tree — only describes the flip.
    expect(JSON.parse(readFileSync(join(wt, fineTunedModelActuator.resolveTargetPath(candidate, layout)), "utf-8"))).toEqual(candPayload);
  });

  test("apply rejects a pointer with content-hash mismatch (payload tree hash wrong)", async () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_POINTER);
    const artifact = createArtifact({
      actuatorType: "fine-tuned-model",
      scenario: "grid_ctf",
      payloadHash: parseContentHash("sha256:" + "0".repeat(64))!,
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });
    await expect(
      fineTunedModelActuator.apply({
        artifact,
        payloadDir,
        workingTreeRoot: wt,
        layout,
      }),
    ).rejects.toThrow(/hash.*mismatch/i);
  });
});

describe("importLegacyModelRecords (export + arity)", () => {
  // Behavioral coverage lives in legacy-adapter.test.ts (Layer 11). This
  // sanity-check ensures the symbol is still exported and the signature has
  // not drifted.
  test("is exported as an async function with (cwd, registry[, opts]) signature", () => {
    expect(typeof importLegacyModelRecords).toBe("function");
    // At least (cwd, registry); may accept an optional opts object.
    expect(importLegacyModelRecords.length).toBeGreaterThanOrEqual(2);
  });
});
