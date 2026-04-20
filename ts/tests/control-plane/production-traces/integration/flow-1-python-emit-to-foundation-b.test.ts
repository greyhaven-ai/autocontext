// Flow 1 (spec §10.3) — Python-emit → TS-ingest → build-dataset → Foundation B eval attach.
//
// End-to-end interop proof for AC-541 → Foundation B receiver. The flow:
//
//   1. Python subprocess: build_trace() x N + write_jsonl() → .autocontext/
//      production-traces/incoming/<date>/<batch>.jsonl
//   2. runProductionTracesCommand(["ingest"])                → .../ingested/...
//   3. runProductionTracesCommand(["build-dataset", ...])   → manifest.json
//   4. Register a Foundation B Artifact (prereq for `eval attach`)
//   5. runControlPlaneCommand(["eval","attach",...,"--dataset-provenance"
//      <path>]) → success; EvalRun attached to registry
//
// The Foundation B receiver ingests the dataset-provenance JSON directly
// from the Foundation A manifest. This proves the two packages compose at
// the documented interop boundary (spec §8.6).
//
// Skips gracefully when `uv` is not on PATH so CI without the Python
// toolchain still finishes.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";
import { runControlPlaneCommand } from "../../../../src/control-plane/cli/index.js";
import { openRegistry } from "../../../../src/control-plane/registry/index.js";
import { createArtifact } from "../../../../src/control-plane/contract/factories.js";
import { computeTreeHash, type TreeFile } from "../../../../src/control-plane/contract/invariants.js";
import {
  parseScenario,
  defaultEnvironmentTag,
} from "../../../../src/control-plane/contract/branded-ids.js";
import type { Provenance } from "../../../../src/control-plane/contract/types.js";
import { isUvAvailable, runPythonEmit } from "./_helpers/python-runner.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-flow1-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

const maybeDescribe = isUvAvailable() ? describe : describe.skip;

maybeDescribe("Flow 1 — Python emit → TS ingest → build-dataset → Foundation B eval attach", () => {
  test(
    "100-trace python emit, full pipeline lands a valid manifest and Foundation B eval attach accepts it",
    async () => {
      // Scaffold the production-traces workspace (salt + default policies).
      const init = await runProductionTracesCommand(["init"], { cwd: tmp });
      expect(init.exitCode).toBe(0);

      // --- Step 1: Python-side emission of 100 traces ---
      const emit = runPythonEmit({
        registryPath: tmp,
        count: 100,
        taskType: "checkout",
        batchId: "flow1-batch",
      });
      expect(emit.status).toBe(0);
      expect(emit.batchPath).toMatch(/\.autocontext\/production-traces\/incoming\/.*\/flow1-batch\.jsonl$/);
      expect(existsSync(emit.batchPath)).toBe(true);

      // --- Step 2: TS ingest ---
      const ingest = await runProductionTracesCommand(
        ["ingest", "--output", "json"],
        { cwd: tmp },
      );
      expect(ingest.exitCode).toBe(0);
      const ingestReport = JSON.parse(ingest.stdout) as {
        tracesIngested: number;
        batchesSucceeded: number;
        linesRejected: number;
      };
      expect(ingestReport.tracesIngested).toBe(100);
      expect(ingestReport.batchesSucceeded).toBe(1);
      expect(ingestReport.linesRejected).toBe(0);

      // --- Step 3: build-dataset ---
      // Provide an inline rubric for the `checkout` cluster so nothing is
      // skipped. Layer 5 pipeline writes manifest.json under
      // .autocontext/datasets/<datasetId>/.
      const rubricsConfig = {
        rubricsByCluster: {
          checkout: {
            source: "inline",
            rubric: { rubricId: "checkout-rubric", dimensions: ["accuracy"] },
          },
        },
      };
      const rubricsPath = join(tmp, "rubrics.json");
      writeFileSync(rubricsPath, JSON.stringify(rubricsConfig), "utf-8");

      const build = await runProductionTracesCommand(
        [
          "build-dataset",
          "--name", "flow1-dataset",
          "--description", "python-emit-to-eval-attach",
          "--rubrics", rubricsPath,
          "--output", "json",
        ],
        { cwd: tmp },
      );
      if (build.exitCode !== 0) {
        throw new Error(`build-dataset failed: ${build.stderr}`);
      }
      const buildResult = JSON.parse(build.stdout) as {
        datasetId: string;
        writePath: string;
        stats: {
          traceCount: number;
          clusterCount: number;
          clustersSkipped: number;
          splitSizes: { train: number; eval: number; holdout: number };
        };
      };
      expect(buildResult.stats.traceCount).toBe(100);
      expect(buildResult.stats.clusterCount).toBe(1);
      expect(buildResult.datasetId).toMatch(/^ds_[0-9A-HJKMNP-TV-Z]{26}$/);
      const manifestPath = join(buildResult.writePath, "manifest.json");
      expect(existsSync(manifestPath)).toBe(true);

      const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as {
        readonly datasetId: string;
        readonly schemaVersion: string;
        readonly splits: { readonly train: { readonly rowCount: number; readonly fileHash: string } };
        readonly source: { readonly traceCount: number };
      };
      expect(manifest.schemaVersion).toBe("1.0");
      expect(manifest.datasetId).toBe(buildResult.datasetId);
      expect(manifest.source.traceCount).toBe(100);

      // --- Step 4: Register a Foundation B Artifact as the target of eval attach ---
      const registry = openRegistry(tmp);
      const scenario = parseScenario("grid_ctf");
      if (scenario === null) throw new Error("parseScenario('grid_ctf') unexpectedly failed");
      const payloadDir = join(tmp, "payload");
      mkdirSync(payloadDir, { recursive: true });
      const payloadBody = "You are helpful.\n";
      writeFileSync(join(payloadDir, "prompt.txt"), payloadBody, "utf-8");
      const tree: TreeFile[] = [{ path: "prompt.txt", content: Buffer.from(payloadBody, "utf-8") }];
      const payloadHash = computeTreeHash(tree);

      const provenance: Provenance = {
        authorType: "human",
        authorId: "jay@greyhaven.ai",
        parentArtifactIds: [],
        createdAt: "2026-04-17T12:00:00.000Z",
      };
      const artifact = createArtifact({
        actuatorType: "prompt-patch",
        scenario,
        environmentTag: defaultEnvironmentTag(),
        payloadHash,
        provenance,
      });
      registry.saveArtifact(artifact, payloadDir);

      // --- Step 5: eval attach with --dataset-provenance derived from the AC-541 manifest ---
      // Foundation B expects datasetProvenance = {datasetId, sliceHash, sampleCount}.
      const dpPath = join(tmp, "ac541-provenance.json");
      const dp = {
        datasetId: manifest.datasetId,
        sliceHash: manifest.splits.train.fileHash,
        sampleCount: manifest.splits.train.rowCount,
      };
      writeFileSync(dpPath, JSON.stringify(dp), "utf-8");

      // Passing metrics bundle (matches defaultThresholds gates).
      const metricsPath = join(tmp, "metrics.json");
      const metrics = {
        quality: { score: 0.95, sampleSize: Math.max(dp.sampleCount, 1) },
        cost: { tokensIn: 100, tokensOut: 50 },
        latency: { p50Ms: 10, p95Ms: 20, p99Ms: 30 },
        safety: { regressions: [] },
        evalRunnerIdentity: {
          name: "integration-test",
          version: "1.0.0",
          configHash: "sha256:" + "9".repeat(64),
        },
      };
      writeFileSync(metricsPath, JSON.stringify(metrics), "utf-8");

      const attach = await runControlPlaneCommand(
        [
          "eval", "attach", artifact.id,
          "--suite", "prod-eval",
          "--metrics", metricsPath,
          "--dataset-provenance", dpPath,
          "--run-id", "run_flow1",
          "--output", "json",
        ],
        { cwd: tmp, now: () => "2026-04-17T12:30:00.000Z" },
      );
      if (attach.exitCode !== 0) {
        throw new Error(`eval attach failed (code ${attach.exitCode}): ${attach.stderr}`);
      }
      const attachResp = JSON.parse(attach.stdout) as {
        artifactId: string;
        runId: string;
        evalRunCount: number;
      };
      expect(attachResp.artifactId).toBe(artifact.id);
      expect(attachResp.runId).toBe("run_flow1");
      expect(attachResp.evalRunCount).toBe(1);

      // Verify through the registry for good measure.
      const reloaded = registry.loadArtifact(artifact.id);
      expect(reloaded.evalRuns).toHaveLength(1);
      const loadedEval = registry.loadEvalRun(artifact.id, "run_flow1");
      expect(loadedEval.datasetProvenance.datasetId).toBe(manifest.datasetId);
      expect(loadedEval.datasetProvenance.sliceHash as string).toBe(manifest.splits.train.fileHash);
      expect(loadedEval.datasetProvenance.sampleCount).toBe(manifest.splits.train.rowCount);
    },
    120_000,
  );
});
