import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  writeFileSync,
  mkdirSync,
  readFileSync,
  existsSync,
  readdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { emitPr } from "../../../src/control-plane/emit/pipeline.js";
import { createArtifact, createEvalRun } from "../../../src/control-plane/contract/factories.js";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { updateArtifactMetadata } from "../../../src/control-plane/registry/artifact-store.js";
import "../../../src/control-plane/actuators/index.js";
import type { Artifact, EvalRun, Provenance } from "../../../src/control-plane/contract/types.js";
import type { ArtifactId, ContentHash, Scenario, SuiteId } from "../../../src/control-plane/contract/branded-ids.js";

const CFG_HASH = ("sha256:" + "9".repeat(64)) as ContentHash;
const SLICE_HASH = ("sha256:" + "a".repeat(64)) as ContentHash;
const TIMESTAMP = "2026-04-17T12:00:00.000Z";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-pipeline-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

function writePromptPayload(dir: string, content: string): string {
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "prompt.txt"), content, "utf-8");
  return dir;
}

const provHuman: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: TIMESTAMP,
};

function registerArtifactWithEvalRun(scenario: string, content: string, runId: string): ArtifactId {
  const payloadDir = writePromptPayload(join(tmp, `payload-${runId}`), content);
  const artifact: Artifact = createArtifact({
    actuatorType: "prompt-patch",
    scenario: scenario as Scenario,
    payloadHash: hashDirectory(payloadDir),
    provenance: provHuman,
  });
  const registry = openRegistry(tmp);
  registry.saveArtifact(artifact, payloadDir);

  const evalRun: EvalRun = createEvalRun({
    runId,
    artifactId: artifact.id,
    suiteId: "suite-x" as SuiteId,
    metrics: {
      quality: { score: 0.9, sampleSize: 500 },
      cost: { tokensIn: 10, tokensOut: 20 },
      latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
      safety: { regressions: [] },
      evalRunnerIdentity: { name: "ev", version: "1", configHash: CFG_HASH },
    },
    datasetProvenance: { datasetId: "ds", sliceHash: SLICE_HASH, sampleCount: 500 },
    ingestedAt: TIMESTAMP,
  });
  registry.attachEvalRun(evalRun);
  const loaded = registry.loadArtifact(artifact.id);
  const updated: Artifact = {
    ...loaded,
    evalRuns: [...loaded.evalRuns, { evalRunId: runId, suiteId: evalRun.suiteId, ingestedAt: evalRun.ingestedAt }],
  };
  updateArtifactMetadata(tmp, updated);
  return artifact.id;
}

describe("emitPr — patch-only mode", () => {
  test("loads candidate + eval run, runs preflight, and writes a dry-run bundle", async () => {
    const candId = registerArtifactWithEvalRun("grid_ctf", "new body\n", "run-1");

    const result = await emitPr(openRegistry(tmp), candId, {
      mode: "patch-only",
      timestamp: TIMESTAMP,
      autocontextVersion: "0.4.3",
    });

    expect(result.mode).toBe("patch-only");
    expect(result.branchName).toMatch(/^autocontext\/grid_ctf\/prompt-patch\//);
    expect(result.location.kind).toBe("local-path");
    expect(existsSync(result.location.value)).toBe(true);
    expect(existsSync(join(result.location.value, "pr-body.md"))).toBe(true);
    expect(existsSync(join(result.location.value, "patches"))).toBe(true);
    expect(result.patches).toHaveLength(1);
    expect(result.prBody).toContain("### Metric deltas");
    expect(result.timestamp).toBe(TIMESTAMP);
  });

  test("bails with preflight issues when candidate has no EvalRun", async () => {
    // Register an artifact without an EvalRun.
    const payloadDir = writePromptPayload(join(tmp, "p-bare"), "x\n");
    const artifact: Artifact = createArtifact({
      actuatorType: "prompt-patch",
      scenario: "grid_ctf" as Scenario,
      payloadHash: hashDirectory(payloadDir),
      provenance: provHuman,
    });
    const registry = openRegistry(tmp);
    registry.saveArtifact(artifact, payloadDir);

    await expect(
      emitPr(registry, artifact.id, {
        mode: "patch-only",
        timestamp: TIMESTAMP,
        autocontextVersion: "0.4.3",
      }),
    ).rejects.toThrow(/preflight|EvalRun/i);
  });
});

describe("emitPr — idempotence (property test)", () => {
  test("two invocations with identical inputs produce byte-identical EmitResult and files", async () => {
    const candId = registerArtifactWithEvalRun("grid_ctf", "idempotent body\n", "run-idempotent");

    const common = {
      mode: "patch-only" as const,
      timestamp: TIMESTAMP,
      autocontextVersion: "0.4.3",
    };
    const r1 = await emitPr(openRegistry(tmp), candId, common);
    const r2 = await emitPr(openRegistry(tmp), candId, common);

    // Same output directory (timestamp-addressed).
    expect(r1.location).toEqual(r2.location);

    // PR body, patches, decision.json, plan.json are byte-identical.
    for (const name of ["pr-body.md", "decision.json", "plan.json", "resolved-layout.json"]) {
      expect(readFileSync(join(r1.location.value, name), "utf-8")).toBe(
        readFileSync(join(r2.location.value, name), "utf-8"),
      );
    }
    // Same EmitResult shape (excluding the Patch objects, which are fresh each run
    // but carry byte-identical content).
    expect(r1.branchName).toBe(r2.branchName);
    expect(r1.prBody).toBe(r2.prBody);
    expect(r1.patches.length).toBe(r2.patches.length);
    for (let i = 0; i < r1.patches.length; i++) {
      expect(r1.patches[i]!.unifiedDiff).toBe(r2.patches[i]!.unifiedDiff);
      expect(r1.patches[i]!.filePath).toBe(r2.patches[i]!.filePath);
      expect(r1.patches[i]!.afterContent).toBe(r2.patches[i]!.afterContent);
    }
  });
});

describe("emitPr — mode=auto echoes resolved mode", () => {
  test("auto with all-off detector picks patch-only and surfaces it in the result", async () => {
    const candId = registerArtifactWithEvalRun("grid_ctf", "auto body\n", "run-auto");
    const result = await emitPr(openRegistry(tmp), candId, {
      mode: "auto",
      timestamp: TIMESTAMP,
      autocontextVersion: "0.4.3",
      autoDetect: { gh: () => false, git: () => false },
    });
    expect(result.mode).toBe("patch-only");
    expect(result.resolvedMode).toBe("patch-only");
  });
});

describe("emitPr — dry-run alias", () => {
  test("--dry-run produces identical output to explicit --mode=patch-only", async () => {
    const candId = registerArtifactWithEvalRun("grid_ctf", "dry body\n", "run-dry");

    const rDry = await emitPr(openRegistry(tmp), candId, {
      dryRun: true,
      timestamp: TIMESTAMP,
      autocontextVersion: "0.4.3",
    });
    const rPatch = await emitPr(openRegistry(tmp), candId, {
      mode: "patch-only",
      timestamp: TIMESTAMP,
      autocontextVersion: "0.4.3",
    });
    expect(rDry.mode).toBe("patch-only");
    expect(rPatch.mode).toBe("patch-only");

    // Same directory, same contents.
    expect(rDry.location).toEqual(rPatch.location);
    for (const name of readdirSync(rDry.location.value)) {
      const a = join(rDry.location.value, name);
      const b = join(rPatch.location.value, name);
      const stat = await import("node:fs");
      if (stat.statSync(a).isFile()) {
        expect(readFileSync(a)).toStrictEqual(readFileSync(b));
      }
    }
  });
});
