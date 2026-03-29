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
  type DataPlaneConfig,
  type CurationPolicy,
  type CuratedDataset,
  type DataPlaneStatus,
} from "../src/traces/data-plane.js";
import { SCHEMA_VERSION } from "../src/traces/public-schema.js";

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
});

// ---------------------------------------------------------------------------
// Integration: traces → curation → output → verify
// ---------------------------------------------------------------------------

describe("end-to-end integration", () => {
  it("traces flow through curation to training-ready output", async () => {
    const traceDir = join(tmpDir, "integration");
    seedTraces(traceDir, 6, [0.3, 0.5, 0.6, 0.8, 0.9, 0.95]);

    // Step 1: Build curated dataset
    const plane = new DataPlane({
      traceDir,
      outputDir: join(tmpDir, "dataset"),
      curationPolicy: { minScore: 0.7, heldOutRatio: 0.33 },
    });
    const buildResult = await plane.build();
    expect(buildResult.status).toBe("completed");
    expect(buildResult.includedTraces).toBe(3); // 0.8, 0.9, 0.95
    expect(buildResult.trainSize).toBe(2);
    expect(buildResult.heldOutSize).toBe(1);

    // Step 2: Verify train.jsonl is valid ShareGPT
    
    const trainContent = readFileSync(join(tmpDir, "dataset", "train.jsonl"), "utf-8");
    const trainLines = trainContent.trim().split("\n");
    expect(trainLines.length).toBe(2);
    for (const line of trainLines) {
      const record = JSON.parse(line);
      expect(record.conversations).toBeDefined();
      expect(record.conversations[0].from).toBe("human");
    }

    // Step 3: Verify manifest has provenance
    const manifest = JSON.parse(readFileSync(join(tmpDir, "dataset", "manifest.json"), "utf-8"));
    expect(manifest.curationPolicy.minScore).toBe(0.7);
    expect(manifest.sources["autocontext"]).toBe(3);

    // Step 4: Verify status
    const status = plane.status();
    expect(status.built).toBe(true);
    expect(status.trainSize).toBe(2);
  });
});
