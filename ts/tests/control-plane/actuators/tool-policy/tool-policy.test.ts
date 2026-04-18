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
import { toolPolicyActuator } from "../../../../src/control-plane/actuators/tool-policy/applicator.js";
import { toolPolicyRegistration } from "../../../../src/control-plane/actuators/tool-policy/index.js";
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

function mkPayload(dir: string, policy: object): string {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "policy.json"), JSON.stringify(policy, null, 2), "utf-8");
  return dir;
}

function mkArtifact(payloadDir: string): Artifact {
  return createArtifact({
    actuatorType: "tool-policy",
    scenario: "grid_ctf",
    payloadHash: hashDirectory(payloadDir),
    provenance: prov,
  });
}

const VALID_POLICY = {
  version: "1",
  tools: {
    search: { allow: true },
    write: { allow: false, parameters: { maxSize: 1024 } },
  },
};

describe("tool-policy actuator registration", () => {
  test("declares content-revert rollback and a policies-path allowedTargetPattern", () => {
    expect(toolPolicyRegistration.type).toBe("tool-policy");
    expect(toolPolicyRegistration.rollback).toEqual({ kind: "content-revert" });
    expect(toolPolicyRegistration.allowedTargetPattern).toMatch(/policies\/tools/);
  });
});

describe("tool-policy actuator", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = mkdtempSync(join(tmpdir(), "autocontext-tool-policy-"));
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  test("parsePayload accepts a valid policy and rejects malformed ones", () => {
    expect(toolPolicyActuator.parsePayload(VALID_POLICY)).toBeTruthy();
    expect(() => toolPolicyActuator.parsePayload({ version: "2", tools: {} })).toThrow();
    expect(() => toolPolicyActuator.parsePayload({ tools: {} })).toThrow();
    expect(() => toolPolicyActuator.parsePayload("string")).toThrow();
  });

  test("resolveTargetPath places the file under <scenarioDir>/<policySubdir>/ with .json extension", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_POLICY);
    const artifact = mkArtifact(payloadDir);
    const target = toolPolicyActuator.resolveTargetPath(artifact, layout);
    expect(target).toMatch(/policies\/tools\//);
    expect(target).toMatch(/\.json$/);
    expect(target).toContain(artifact.id);
  });

  test("apply writes the policy payload to the resolved target", async () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_POLICY);
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    await toolPolicyActuator.apply({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });

    const target = join(wt, toolPolicyActuator.resolveTargetPath(artifact, layout));
    expect(existsSync(target)).toBe(true);
    expect(JSON.parse(readFileSync(target, "utf-8"))).toEqual(VALID_POLICY);
  });

  test("emitPatch roundtrips via diff.applyPatch", () => {
    const layout = defaultWorkspaceLayout();
    const payloadDir = mkPayload(join(tmp, "payload"), VALID_POLICY);
    const artifact = mkArtifact(payloadDir);
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    const patch = toolPolicyActuator.emitPatch({
      artifact,
      payloadDir,
      workingTreeRoot: wt,
      layout,
    });
    expect(patch.operation).toBe("create");
    expect(applyPatch("", patch.unifiedDiff)).toBe(patch.afterContent);
  });

  test("rollback reverts the working-tree policy file to the baseline policy content", async () => {
    const layout = defaultWorkspaceLayout();
    const candDir = mkPayload(join(tmp, "cand"), { ...VALID_POLICY, tools: { search: { allow: true } } });
    const baseDir = mkPayload(join(tmp, "base"), VALID_POLICY);
    const candidate = mkArtifact(candDir);
    const baseline = createArtifact({
      actuatorType: "tool-policy",
      scenario: "grid_ctf",
      payloadHash: hashDirectory(baseDir),
      provenance: prov,
    });
    const wt = join(tmp, "wt");
    mkdirSync(wt, { recursive: true });

    await toolPolicyActuator.apply({
      artifact: candidate,
      payloadDir: candDir,
      workingTreeRoot: wt,
      layout,
    });

    const patches = await toolPolicyActuator.rollback({
      candidate,
      baseline,
      candidatePayloadDir: candDir,
      baselinePayloadDir: baseDir,
      workingTreeRoot: wt,
      layout,
    });
    const patch = Array.isArray(patches) ? patches[0]! : patches;
    const candidateContent = readFileSync(join(wt, toolPolicyActuator.resolveTargetPath(candidate, layout)), "utf-8");
    const baselineContent = readFileSync(join(baseDir, "policy.json"), "utf-8");
    expect(patch.afterContent).toBe(baselineContent);
    expect(applyPatch(candidateContent, patch.unifiedDiff)).toBe(baselineContent);
  });
});
