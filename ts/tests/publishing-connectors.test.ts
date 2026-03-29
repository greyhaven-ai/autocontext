/**
 * AC-465: Public-host publishing and ingestion connectors.
 *
 * Tests the publisher adapters that push reviewed trace artifacts
 * to open hosts and pull them back for curation.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, existsSync, readFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  LocalPublisher,
  GistPublisher,
  HuggingFacePublisher,
  TraceIngester,
  type PublishResult,
  type IngestResult,
  type TraceArtifact,
} from "../src/traces/publishers.js";
import { SCHEMA_VERSION } from "../src/traces/public-schema.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-465-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

function sampleArtifact(): TraceArtifact {
  return {
    trace: {
      schemaVersion: SCHEMA_VERSION,
      traceId: "trace_test_001",
      sourceHarness: "autocontext",
      collectedAt: "2026-03-27T10:00:00Z",
      messages: [
        { role: "user", content: "Fix the bug", timestamp: "2026-03-27T10:00:01Z" },
        { role: "assistant", content: "I'll check the code", timestamp: "2026-03-27T10:00:02Z" },
      ],
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
      submitterId: "user_test",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
      attestedAt: "2026-03-27T10:00:00Z",
    },
  };
}

// ---------------------------------------------------------------------------
// LocalPublisher
// ---------------------------------------------------------------------------

describe("LocalPublisher", () => {
  it("publishes artifact as JSONL to local directory", async () => {
    const publisher = new LocalPublisher(join(tmpDir, "published"));
    const result = await publisher.publish(sampleArtifact());

    expect(result.status).toBe("published");
    expect(result.location).toBeTruthy();
    expect(existsSync(result.location!)).toBe(true);
  });

  it("published JSONL is valid and parseable", async () => {
    const publisher = new LocalPublisher(join(tmpDir, "published"));
    const result = await publisher.publish(sampleArtifact());

    const content = readFileSync(result.location!, "utf-8");
    const lines = content.trim().split("\n");
    expect(lines.length).toBeGreaterThan(0);
    // Each line should be valid JSON
    for (const line of lines) {
      expect(() => JSON.parse(line)).not.toThrow();
    }
  });

  it("appends multiple artifacts to the same file", async () => {
    const publisher = new LocalPublisher(join(tmpDir, "published"));
    await publisher.publish(sampleArtifact());
    await publisher.publish({ ...sampleArtifact(), trace: { ...sampleArtifact().trace, traceId: "trace_002" } });

    const content = readFileSync(
      join(tmpDir, "published", "traces.jsonl"),
      "utf-8",
    );
    const lines = content.trim().split("\n");
    expect(lines.length).toBe(2);
  });
});

// ---------------------------------------------------------------------------
// GistPublisher (mock — no real API calls)
// ---------------------------------------------------------------------------

describe("GistPublisher", () => {
  it("formats artifact for gist upload", async () => {
    const publisher = new GistPublisher({ token: "test_token" });
    // Without a real token, publish returns a dry-run result
    const result = await publisher.publish(sampleArtifact(), { dryRun: true });

    expect(result.status).toBe("dry_run");
    expect(result.payload).toBeDefined();
    expect(result.payload!.files).toBeDefined();
    expect(result.payload!.description).toContain("autocontext");
  });
});

// ---------------------------------------------------------------------------
// HuggingFacePublisher (mock — no real API calls)
// ---------------------------------------------------------------------------

describe("HuggingFacePublisher", () => {
  it("formats artifact for HF dataset upload", async () => {
    const publisher = new HuggingFacePublisher({ token: "test_token", repoId: "user/traces" });
    const result = await publisher.publish(sampleArtifact(), { dryRun: true });

    expect(result.status).toBe("dry_run");
    expect(result.payload).toBeDefined();
    expect(result.payload!.repoId).toBe("user/traces");
    expect(result.payload!.content).toBeTruthy();
  });

  it("formats as ShareGPT-compatible JSONL", async () => {
    const publisher = new HuggingFacePublisher({ token: "test_token", repoId: "user/traces" });
    const result = await publisher.publish(sampleArtifact(), { dryRun: true });

    const content = result.payload!.content as string;
    const parsed = JSON.parse(content);
    expect(parsed.conversations).toBeDefined();
    expect(Array.isArray(parsed.conversations)).toBe(true);
    expect(parsed.conversations[0]).toHaveProperty("from");
    expect(parsed.conversations[0]).toHaveProperty("value");
  });
});

// ---------------------------------------------------------------------------
// TraceIngester
// ---------------------------------------------------------------------------

describe("TraceIngester", () => {
  it("ingests a published artifact from local JSONL", async () => {
    // First publish
    const publisher = new LocalPublisher(join(tmpDir, "published"));
    await publisher.publish(sampleArtifact());

    // Then ingest
    const ingester = new TraceIngester(join(tmpDir, "cache"));
    const result = await ingester.ingestFromFile(join(tmpDir, "published", "traces.jsonl"));

    expect(result.status).toBe("ingested");
    expect(result.tracesIngested).toBeGreaterThan(0);
    expect(result.cacheDir).toBeTruthy();
    expect(existsSync(result.cacheDir!)).toBe(true);
  });

  it("preserves provenance on ingest", async () => {
    const publisher = new LocalPublisher(join(tmpDir, "published"));
    await publisher.publish(sampleArtifact());

    const ingester = new TraceIngester(join(tmpDir, "cache"));
    const result = await ingester.ingestFromFile(join(tmpDir, "published", "traces.jsonl"));

    // Check cached artifact has provenance
    const cached = JSON.parse(
      readFileSync(join(result.cacheDir!, "trace_test_001.json"), "utf-8"),
    );
    expect(cached.manifest.sourceHarness).toBe("autocontext");
    expect(cached.manifest.license).toBe("CC-BY-4.0");
    expect(cached.attestation.consentGiven).toBe(true);
  });

  it("deduplicates on re-ingest", async () => {
    const publisher = new LocalPublisher(join(tmpDir, "published"));
    await publisher.publish(sampleArtifact());

    const ingester = new TraceIngester(join(tmpDir, "cache"));
    const r1 = await ingester.ingestFromFile(join(tmpDir, "published", "traces.jsonl"));
    const r2 = await ingester.ingestFromFile(join(tmpDir, "published", "traces.jsonl"));

    expect(r1.tracesIngested).toBe(1);
    expect(r2.tracesIngested).toBe(0); // deduplicated
    expect(r2.duplicatesSkipped).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// PublishResult shape
// ---------------------------------------------------------------------------

describe("PublishResult shape", () => {
  it("has required fields", async () => {
    const publisher = new LocalPublisher(join(tmpDir, "pub"));
    const result: PublishResult = await publisher.publish(sampleArtifact());

    expect(result).toHaveProperty("status");
    expect(result).toHaveProperty("location");
    expect(result).toHaveProperty("host");
  });
});
