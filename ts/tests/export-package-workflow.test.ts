import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  blockExportResult,
  buildPublicTracePackage,
  failExportResult,
  validateExportTrace,
  writeExportArtifact,
} from "../src/traces/export-package-workflow.js";
import { emptyRedactionSummary, redactTraceMessages } from "../src/traces/export-redaction-workflow.js";
import { RedactionPolicy, SensitiveDataDetector } from "../src/traces/redaction.js";
import type { ExportRequest } from "../src/traces/export-workflow-types.js";

describe("export package workflow", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "ac-export-package-"));
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("builds blocked/failed results and aggregates redaction summaries", () => {
    const summary = emptyRedactionSummary();
    expect(blockExportResult({
      traceId: "trace_1",
      redactionSummary: summary,
      warnings: [],
      error: "consent missing",
    })).toMatchObject({ status: "blocked", traceId: "trace_1", error: "consent missing" });
    expect(failExportResult({
      traceId: "trace_2",
      redactionSummary: summary,
      warnings: [],
      error: "run missing",
    })).toMatchObject({ status: "failed", traceId: "trace_2", error: "run missing" });

    const redacted = redactTraceMessages({
      messages: [{ role: "assistant", content: "Key sk-ant-api03-secret123456", timestamp: "2026-03-27T10:00:00Z" }],
      detector: new SensitiveDataDetector(),
      policy: new RedactionPolicy(),
    });
    expect(redacted.redactionSummary.totalDetections).toBeGreaterThan(0);
    expect(redacted.redactedMessages[0]?.content).toContain("[REDACTED:api_key]");
  });

  it("builds, validates, and writes export packages with trace, manifest, and attestation", () => {
    const request: ExportRequest = {
      runId: "run_001",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: false,
    };
    const pkg = buildPublicTracePackage({
      traceId: "trace_run_001_abc",
      request,
      messages: [{ role: "assistant", content: "function solve() { return 42; }", timestamp: "2026-03-27T10:00:00Z" }],
      redactionSummary: {
        totalDetections: 1,
        totalRedactions: 1,
        blocked: false,
        blockReasons: [],
        categoryCounts: { api_key: 1 },
      },
    });

    expect(validateExportTrace(pkg.trace)).toEqual({ valid: true });
    expect(pkg.manifest.license).toBe("CC-BY-4.0");
    expect(pkg.attestation.allowTraining).toBe(false);
    expect(pkg.redactionSummary.categoryCounts.api_key).toBe(1);

    const outputPath = writeExportArtifact(tmpDir, "trace_run_001_abc", pkg);
    expect(existsSync(outputPath)).toBe(true);
    const persisted = JSON.parse(readFileSync(outputPath, "utf-8")) as {
      trace: { traceId: string };
      manifest: { license: string };
      attestation: { submitterId: string };
    };
    expect(persisted.trace.traceId).toBe("trace_run_001_abc");
    expect(persisted.manifest.license).toBe("CC-BY-4.0");
    expect(persisted.attestation.submitterId).toBe("user_test");
  });
});
