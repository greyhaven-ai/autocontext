import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { SCHEMA_VERSION } from "../src/traces/public-schema.js";
import { ingestPublishedTraceFile, loadSeenTraceIds } from "../src/traces/trace-ingest-workflow.js";
import type { TraceArtifact } from "../src/traces/publishers-types.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-trace-ingest-workflow-"));
});

afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

function sampleArtifact(traceId = "trace_test_001"): TraceArtifact {
  return {
    trace: {
      schemaVersion: SCHEMA_VERSION,
      traceId,
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
      schemaVersion: SCHEMA_VERSION,
      submitterId: "user_test",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
      attestedAt: "2026-03-27T10:00:00Z",
    },
  };
}

describe("trace ingest workflow", () => {
  it("loads seen ids from cache and ingests non-duplicate trace artifacts", async () => {
    const cacheDir = join(tmpDir, "cache");
    mkdirSync(cacheDir, { recursive: true });
    writeFileSync(join(cacheDir, "existing.json"), "{}", "utf-8");

    const seenIds = loadSeenTraceIds(cacheDir);
    expect(seenIds.has("existing")).toBe(true);

    const publishedPath = join(tmpDir, "published.jsonl");
    writeFileSync(
      publishedPath,
      `${JSON.stringify(sampleArtifact())}\n${JSON.stringify(sampleArtifact("trace_test_002"))}\n`,
      "utf-8",
    );

    const result = await ingestPublishedTraceFile({
      filePath: publishedPath,
      cacheDir,
      seenIds,
    });

    expect(result).toMatchObject({ status: "ingested", tracesIngested: 2, duplicatesSkipped: 0, cacheDir });
    expect(existsSync(join(cacheDir, "trace_test_001.json"))).toBe(true);
    expect(JSON.parse(readFileSync(join(cacheDir, "trace_test_001.json"), "utf-8"))).toMatchObject({
      manifest: { license: "CC-BY-4.0" },
      attestation: { submitterId: "user_test" },
    });
  });

  it("skips duplicates and missing files with stable result semantics", async () => {
    const cacheDir = join(tmpDir, "cache");
    const seenIds = new Set<string>(["trace_test_001"]);
    const publishedPath = join(tmpDir, "published.jsonl");
    writeFileSync(publishedPath, `${JSON.stringify(sampleArtifact())}\nnot-json\n`, "utf-8");

    const result = await ingestPublishedTraceFile({ filePath: publishedPath, cacheDir, seenIds });
    expect(result).toMatchObject({ status: "ingested", tracesIngested: 0, duplicatesSkipped: 1, cacheDir });

    const missing = await ingestPublishedTraceFile({
      filePath: join(tmpDir, "missing.jsonl"),
      cacheDir,
      seenIds,
    });
    expect(missing).toMatchObject({ status: "failed", tracesIngested: 0, duplicatesSkipped: 0 });
    expect(missing.error).toContain("File not found");
  });
});
