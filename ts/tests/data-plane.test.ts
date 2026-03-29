/**
 * AC-466: Trace-to-disposable-model data plane.
 *
 * Tests the DataPlane orchestrator that ties trace export, redaction,
 * curation, dataset construction, and training inputs together.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  DataPlane,
  DatasetCurator,
  TraceExportWorkflow,
  HuggingFacePublisher,
  TraceIngester,
  type DataPlaneStatus,
} from "../src/index.js";
import { SCHEMA_VERSION } from "../src/traces/public-schema.js";
import * as pkg from "../src/index.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-466-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// Helper: seed trace artifacts
function seedTraces(dir: string, count: number, scores?: number[]) {
  mkdirSync(dir, { recursive: true });
  for (let i = 0; i < count; i++) {
    const artifact = {
      trace: {
        schemaVersion: SCHEMA_VERSION,
        traceId: `trace_${i}`,
        sourceHarness: "autocontext",
        collectedAt: "2026-03-27T10:00:00Z",
        messages: [
          { role: "user", content: `Task ${i}`, timestamp: "2026-03-27T10:00:01Z" },
          { role: "assistant", content: `Solution ${i}`, timestamp: "2026-03-27T10:00:02Z" },
        ],
        outcome: { score: scores?.[i] ?? 0.5 + i * 0.1, reasoning: "ok", dimensions: {} },
      },
      manifest: {
        schemaVersion: SCHEMA_VERSION,
        sourceHarness: "autocontext",
        collectionMethod: "automated",
        license: "CC-BY-4.0",
        traceCount: 1,
        createdAt: "2026-03-27T10:00:00Z",
      },
      attestation: {
        submitterId: "user",
        consentGiven: true,
        dataOrigin: "own_work",
        allowRedistribution: true,
        allowTraining: true,
        attestedAt: "2026-03-27T10:00:00Z",
      },
    };
    writeFileSync(join(dir, `trace_${i}.json`), JSON.stringify(artifact), "utf-8");
  }
}

function sampleArtifact(id = "trace_sample", allowTraining = true) {
  return {
    trace: {
      schemaVersion: SCHEMA_VERSION,
      traceId: id,
      sourceHarness: "autocontext",
      collectedAt: "2026-03-27T10:00:00Z",
      messages: [
        { role: "user", content: "Fix the bug", timestamp: "2026-03-27T10:00:01Z" },
        { role: "assistant", content: "I checked the code", timestamp: "2026-03-27T10:00:02Z" },
      ],
      outcome: { score: 0.9, reasoning: "ok", dimensions: {} },
    },
    manifest: {
      schemaVersion: SCHEMA_VERSION,
      sourceHarness: "autocontext",
      collectionMethod: "automated_harness_run",
      license: "CC-BY-4.0",
      traceCount: 1,
      createdAt: "2026-03-27T10:00:00Z",
    },
    attestation: {
      schemaVersion: SCHEMA_VERSION,
      submitterId: "user_test",
      consentGiven: true,
      dataOrigin: "licensed_dataset",
      allowRedistribution: true,
      allowTraining,
      attestedAt: "2026-03-27T10:00:00Z",
    },
    redactionSummary: {
      totalDetections: 0,
      totalRedactions: 0,
      blocked: false,
      blockReasons: [],
      categoryCounts: {},
    },
  };
}

// ---------------------------------------------------------------------------
// DatasetCurator
// ---------------------------------------------------------------------------

describe("DatasetCurator", () => {
  it("filters traces by minimum score threshold", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, 5, [0.3, 0.5, 0.7, 0.9, 0.95]);

    const curator = new DatasetCurator({
      minScore: 0.6,
    });
    const dataset = curator.curate(traceDir);

    expect(dataset.included.length).toBe(3); // 0.7, 0.9, 0.95
    expect(dataset.excluded.length).toBe(2); // 0.3, 0.5
  });

  it("splits held-out evaluation set", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, 10);

    const curator = new DatasetCurator({
      heldOutRatio: 0.2,
    });
    const dataset = curator.curate(traceDir);

    expect(dataset.train.length).toBeGreaterThan(0);
    expect(dataset.heldOut.length).toBeGreaterThan(0);
    expect(dataset.train.length + dataset.heldOut.length).toBe(dataset.included.length);
  });

  it("preserves provenance in curated dataset", () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, 3);

    const curator = new DatasetCurator();
    const dataset = curator.curate(traceDir);

    for (const entry of dataset.included) {
      expect(entry.manifest.sourceHarness).toBe("autocontext");
      expect(entry.attestation.consentGiven).toBe(true);
    }
  });

  it("only includes traces with training consent", () => {
    const traceDir = join(tmpDir, "traces");
    mkdirSync(traceDir, { recursive: true });

    // One with consent, one without
    const withConsent = {
      trace: { schemaVersion: SCHEMA_VERSION, traceId: "t_yes", sourceHarness: "test", collectedAt: "2026-01-01T00:00:00Z", messages: [{ role: "user", content: "hi", timestamp: "2026-01-01T00:00:00Z" }] },
      manifest: { schemaVersion: SCHEMA_VERSION, sourceHarness: "test", collectionMethod: "manual", license: "CC0", traceCount: 1, createdAt: "2026-01-01T00:00:00Z" },
      attestation: { submitterId: "u", consentGiven: true, dataOrigin: "own_work", allowRedistribution: true, allowTraining: true, attestedAt: "2026-01-01T00:00:00Z" },
    };
    const noConsent = {
      ...withConsent,
      trace: { ...withConsent.trace, traceId: "t_no" },
      attestation: { ...withConsent.attestation, allowTraining: false },
    };

    writeFileSync(join(traceDir, "t_yes.json"), JSON.stringify(withConsent), "utf-8");
    writeFileSync(join(traceDir, "t_no.json"), JSON.stringify(noConsent), "utf-8");

    const curator = new DatasetCurator();
    const dataset = curator.curate(traceDir);

    expect(dataset.included.length).toBe(1);
    expect(dataset.included[0].trace.traceId).toBe("t_yes");
  });
});

// ---------------------------------------------------------------------------
// DataPlane orchestrator
// ---------------------------------------------------------------------------

describe("DataPlane", () => {
  it("runs the full pipeline: ingest → curate → output", async () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, 5, [0.4, 0.6, 0.8, 0.85, 0.9]);

    const plane = new DataPlane({
      traceDir,
      outputDir: join(tmpDir, "dataset"),
      curationPolicy: { minScore: 0.7, heldOutRatio: 0.2 },
    });

    const result = await plane.build();

    expect(result.status).toBe("completed");
    expect(result.totalTraces).toBe(5);
    expect(result.includedTraces).toBe(3); // 0.8, 0.85, 0.9
    expect(result.trainSize).toBeGreaterThan(0);
    expect(result.heldOutSize).toBeGreaterThanOrEqual(0);
    expect(existsSync(join(tmpDir, "dataset", "train.jsonl"))).toBe(true);
  });

  it("outputs training JSONL in ShareGPT format", async () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, 3);

    const plane = new DataPlane({
      traceDir,
      outputDir: join(tmpDir, "dataset"),
    });

    await plane.build();

    const content = readFileSync(join(tmpDir, "dataset", "train.jsonl"), "utf-8");
    const lines = content.trim().split("\n");
    expect(lines.length).toBeGreaterThan(0);

    const first = JSON.parse(lines[0]);
    expect(first.conversations).toBeDefined();
    expect(first.conversations[0]).toHaveProperty("from");
    expect(first.conversations[0]).toHaveProperty("value");
  });

  it("writes dataset manifest with provenance summary", async () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, 3);

    const plane = new DataPlane({
      traceDir,
      outputDir: join(tmpDir, "dataset"),
    });

    await plane.build();

    expect(existsSync(join(tmpDir, "dataset", "manifest.json"))).toBe(true);
    const manifest = JSON.parse(readFileSync(join(tmpDir, "dataset", "manifest.json"), "utf-8"));
    expect(manifest.totalTraces).toBe(3);
    expect(manifest.sources).toBeDefined();
    expect(manifest.curationPolicy).toBeDefined();
  });

  it("reports status", async () => {
    const traceDir = join(tmpDir, "traces");
    seedTraces(traceDir, 2);

    const plane = new DataPlane({
      traceDir,
      outputDir: join(tmpDir, "dataset"),
    });

    await plane.build();
    const status: DataPlaneStatus = plane.status();

    expect(status).toHaveProperty("totalTraces");
    expect(status).toHaveProperty("includedTraces");
    expect(status).toHaveProperty("trainSize");
    expect(status).toHaveProperty("outputDir");
  });

  it("exports the data-plane surface through the package entrypoint", () => {
    expect(pkg.DataPlane).toBe(DataPlane);
    expect(pkg.DatasetCurator).toBe(DatasetCurator);
    expect(pkg.HuggingFacePublisher).toBe(HuggingFacePublisher);
    expect(pkg.TraceIngester).toBe(TraceIngester);
    expect(pkg.TraceExportWorkflow).toBe(TraceExportWorkflow);
  });

  it("requires explicit consent before exporting traces", async () => {
    const genDir = join(tmpDir, "runs", "run_1", "generations", "gen_0001");
    mkdirSync(genDir, { recursive: true });
    writeFileSync(join(genDir, "competitor_output.md"), "hello", "utf-8");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result = await workflow.export({
      runId: "run_1",
      scenario: "test",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: false,
      dataOrigin: "licensed_dataset",
      allowRedistribution: true,
      allowTraining: false,
    });

    expect(result.status).toBe("blocked");
    expect(result.error).toContain("explicit consent");
  });

  it("preserves explicit attestation values in exported trace artifacts", async () => {
    const genDir = join(tmpDir, "runs", "run_2", "generations", "gen_0001");
    mkdirSync(genDir, { recursive: true });
    writeFileSync(join(genDir, "competitor_output.md"), "hello", "utf-8");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result = await workflow.export({
      runId: "run_2",
      scenario: "test",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "licensed_dataset",
      allowRedistribution: true,
      allowTraining: false,
      consentNotes: "limited to evaluation and non-training release",
    });

    expect(result.status).toBe("completed");
    const exported = JSON.parse(readFileSync(result.outputPath!, "utf-8"));
    expect(exported.attestation.dataOrigin).toBe("licensed_dataset");
    expect(exported.attestation.allowTraining).toBe(false);
    expect(exported.attestation.allowRedistribution).toBe(true);
  });

  it("preserves provenance and attestation in Hugging Face dataset rows", async () => {
    const publisher = new HuggingFacePublisher({ token: "test_token", repoId: "user/traces" });
    const result = await publisher.publish(sampleArtifact(), { dryRun: true });

    expect(result.status).toBe("dry_run");
    const row = JSON.parse(result.payload!.content as string);
    expect(row.provenance.license).toBe("CC-BY-4.0");
    expect(row.provenance.sourceHarness).toBe("autocontext");
    expect(row.attestation.dataOrigin).toBe("licensed_dataset");
    expect(row.attestation.allowTraining).toBe(true);
  });

  it("reloads seen ids from disk across ingester restarts", async () => {
    const publishedDir = join(tmpDir, "published");
    mkdirSync(publishedDir, { recursive: true });
    writeFileSync(
      join(publishedDir, "traces.jsonl"),
      `${JSON.stringify(sampleArtifact("trace_restart"))}\n`,
      "utf-8",
    );

    const firstIngester = new TraceIngester(join(tmpDir, "cache"));
    const first = await firstIngester.ingestFromFile(join(publishedDir, "traces.jsonl"));

    const restartedIngester = new TraceIngester(join(tmpDir, "cache"));
    const second = await restartedIngester.ingestFromFile(join(publishedDir, "traces.jsonl"));

    expect(first.tracesIngested).toBe(1);
    expect(second.tracesIngested).toBe(0);
    expect(second.duplicatesSkipped).toBe(1);
  });
});
