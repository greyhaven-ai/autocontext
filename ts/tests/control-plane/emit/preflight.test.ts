import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { preflight } from "../../../src/control-plane/emit/preflight.js";
import { defaultWorkspaceLayout } from "../../../src/control-plane/emit/workspace-layout.js";
import { createArtifact, createEvalRun } from "../../../src/control-plane/contract/factories.js";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { updateArtifactMetadata } from "../../../src/control-plane/registry/artifact-store.js";
import "../../../src/control-plane/actuators/index.js";
import type { Artifact, EvalRun, Provenance } from "../../../src/control-plane/contract/types.js";
import type { ContentHash, Scenario, SuiteId } from "../../../src/control-plane/contract/branded-ids.js";

const CFG_HASH = ("sha256:" + "9".repeat(64)) as ContentHash;
const SLICE_HASH = ("sha256:" + "a".repeat(64)) as ContentHash;

const prov: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T00:00:00.000Z",
};

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-preflight-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

function mkArtifactWithPayload(scenario: string): { artifact: Artifact; payloadDir: string } {
  const payloadDir = join(tmp, `payload-${Math.random().toString(36).slice(2, 8)}`);
  mkdirSync(payloadDir, { recursive: true });
  writeFileSync(join(payloadDir, "prompt.txt"), "body\n", "utf-8");
  const artifact = createArtifact({
    actuatorType: "prompt-patch",
    scenario: scenario as Scenario,
    payloadHash: hashDirectory(payloadDir),
    provenance: prov,
  });
  return { artifact, payloadDir };
}

function attachSimpleEvalRun(candidate: Artifact): void {
  const registry = openRegistry(tmp);
  const evalRun: EvalRun = createEvalRun({
    runId: "run-1",
    artifactId: candidate.id,
    suiteId: "suite-x" as SuiteId,
    metrics: {
      quality: { score: 0.9, sampleSize: 100 },
      cost: { tokensIn: 10, tokensOut: 20 },
      latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
      safety: { regressions: [] },
      evalRunnerIdentity: {
        name: "ev",
        version: "1",
        configHash: CFG_HASH,
      },
    },
    datasetProvenance: {
      datasetId: "ds",
      sliceHash: SLICE_HASH,
      sampleCount: 100,
    },
    ingestedAt: "2026-04-17T00:00:00.000Z",
  });
  registry.attachEvalRun(evalRun);
  const loaded = registry.loadArtifact(candidate.id);
  const updated: Artifact = {
    ...loaded,
    evalRuns: [
      ...loaded.evalRuns,
      { evalRunId: evalRun.runId, suiteId: evalRun.suiteId, ingestedAt: evalRun.ingestedAt },
    ],
  };
  updateArtifactMetadata(tmp, updated);
}

describe("preflight — missing EvalRun (exit 14)", () => {
  test("reports issue when candidate has no EvalRun attached", () => {
    const { artifact, payloadDir } = mkArtifactWithPayload("grid_ctf");
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);

    const result = preflight({
      registry,
      candidate: artifact,
      mode: "patch-only",
      cwd: tmp,
      layout: defaultWorkspaceLayout(),
    });
    expect(result.ok).toBe(false);
    const codes = result.issues.map((i) => i.code);
    expect(codes).toContain(14);
  });
});

describe("preflight — valid patch-only", () => {
  test("passes when EvalRun is present and target path is within allowed pattern", () => {
    const { artifact, payloadDir } = mkArtifactWithPayload("grid_ctf");
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);
    attachSimpleEvalRun(artifact);
    const reloaded = registry.loadArtifact(artifact.id);

    const result = preflight({
      registry,
      candidate: reloaded,
      mode: "patch-only",
      cwd: tmp,
      layout: defaultWorkspaceLayout(),
    });
    expect(result.ok).toBe(true);
    expect(result.issues).toHaveLength(0);
  });
});

describe("preflight — unknown actuator type (exit 13)", () => {
  test("reports target-path violation when actuator is not registered", () => {
    const { artifact, payloadDir } = mkArtifactWithPayload("grid_ctf");
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);
    const mutated = { ...artifact, actuatorType: "nonexistent-type" as Artifact["actuatorType"] };

    const result = preflight({
      registry,
      candidate: mutated,
      mode: "patch-only",
      cwd: tmp,
      layout: defaultWorkspaceLayout(),
    });
    expect(result.ok).toBe(false);
    const codes = result.issues.map((i) => i.code);
    expect(codes).toContain(13);
  });
});

describe("preflight — multiple issues aggregated", () => {
  test("returns every issue, not just the first", () => {
    const { artifact, payloadDir } = mkArtifactWithPayload("grid_ctf");
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);
    const mutated = { ...artifact, actuatorType: "nonexistent-type" as Artifact["actuatorType"] };

    const result = preflight({
      registry,
      candidate: mutated,
      mode: "patch-only",
      cwd: tmp,
      layout: defaultWorkspaceLayout(),
    });
    expect(result.ok).toBe(false);
    const codes = new Set(result.issues.map((i) => i.code));
    expect(codes.size).toBeGreaterThanOrEqual(2);
  });
});

describe("preflight — mode requirements (exit 15)", () => {
  test("gh mode reports mode-requirements-not-met when gh isn't resolvable", () => {
    const { artifact, payloadDir } = mkArtifactWithPayload("grid_ctf");
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);
    attachSimpleEvalRun(artifact);
    const reloaded = registry.loadArtifact(artifact.id);

    const result = preflight({
      registry,
      candidate: reloaded,
      mode: "gh",
      cwd: tmp,
      layout: defaultWorkspaceLayout(),
      detect: { gh: () => false, git: () => false, isWorkingTreeClean: () => true, baseBranchExists: () => true },
    });
    expect(result.ok).toBe(false);
    const codes = result.issues.map((i) => i.code);
    expect(codes).toContain(15);
  });

  test("git mode reports mode-requirements when git isn't installed", () => {
    const { artifact, payloadDir } = mkArtifactWithPayload("grid_ctf");
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);
    attachSimpleEvalRun(artifact);
    const reloaded = registry.loadArtifact(artifact.id);

    const result = preflight({
      registry,
      candidate: reloaded,
      mode: "git",
      cwd: tmp,
      layout: defaultWorkspaceLayout(),
      detect: { gh: () => false, git: () => false, isWorkingTreeClean: () => true, baseBranchExists: () => true },
    });
    expect(result.ok).toBe(false);
    const codes = result.issues.map((i) => i.code);
    expect(codes).toContain(15);
  });
});

describe("preflight — working tree dirty (exit 11)", () => {
  test("reports dirty working tree when git mode + detector says dirty", () => {
    const { artifact, payloadDir } = mkArtifactWithPayload("grid_ctf");
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);
    attachSimpleEvalRun(artifact);
    const reloaded = registry.loadArtifact(artifact.id);

    const result = preflight({
      registry,
      candidate: reloaded,
      mode: "git",
      cwd: tmp,
      layout: defaultWorkspaceLayout(),
      detect: { gh: () => true, git: () => true, isWorkingTreeClean: () => false, baseBranchExists: () => true },
      baseBranch: "main",
    });
    expect(result.ok).toBe(false);
    expect(result.issues.map((i) => i.code)).toContain(11);
  });
});

describe("preflight — base branch missing (exit 12)", () => {
  test("reports missing base branch for git/gh modes when detector says base branch is absent", () => {
    const { artifact, payloadDir } = mkArtifactWithPayload("grid_ctf");
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);
    attachSimpleEvalRun(artifact);
    const reloaded = registry.loadArtifact(artifact.id);

    const result = preflight({
      registry,
      candidate: reloaded,
      mode: "git",
      cwd: tmp,
      layout: defaultWorkspaceLayout(),
      detect: { gh: () => true, git: () => true, isWorkingTreeClean: () => true, baseBranchExists: () => false },
      baseBranch: "main",
    });
    expect(result.ok).toBe(false);
    expect(result.issues.map((i) => i.code)).toContain(12);
  });
});

describe("preflight — patch-only ignores git/gh checks", () => {
  test("does not report 11/12/15 for patch-only mode regardless of detector", () => {
    const { artifact, payloadDir } = mkArtifactWithPayload("grid_ctf");
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);
    attachSimpleEvalRun(artifact);
    const reloaded = registry.loadArtifact(artifact.id);

    const result = preflight({
      registry,
      candidate: reloaded,
      mode: "patch-only",
      cwd: tmp,
      layout: defaultWorkspaceLayout(),
      detect: { gh: () => false, git: () => false, isWorkingTreeClean: () => false, baseBranchExists: () => false },
    });
    expect(result.ok).toBe(true);
    expect(result.issues).toHaveLength(0);
  });
});
