/**
 * AC-463: Privacy-aware trace export and submission workflow.
 *
 * Tests the export pipeline that packages autocontext sessions for
 * public sharing: select runs → export to public schema → redact →
 * validate → generate manifest + attestation → write artifact.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  TraceExportWorkflow,
  type ExportRequest,
  type ExportResult,
} from "../src/index.js";
import * as pkg from "../src/index.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-463-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// Helper: seed a fake run artifact
function seedRun(runId: string, scenario: string) {
  const runDir = join(tmpDir, "runs", runId, "generations", "gen_1");
  mkdirSync(runDir, { recursive: true });
  writeFileSync(join(runDir, "competitor_output.md"), "function solve() { return 42; }", "utf-8");
  writeFileSync(join(runDir, "competitor_prompt.md"), "Solve the problem", "utf-8");
  writeFileSync(join(runDir, "analyst.md"), "Analysis of the approach", "utf-8");
  writeFileSync(join(runDir, "trajectory.md"), `Score: 0.85\nGate: advance`, "utf-8");

  // Also seed the DB-like metadata
  const metaDir = join(tmpDir, "runs", runId);
  writeFileSync(join(metaDir, "run_meta.json"), JSON.stringify({
    run_id: runId,
    scenario,
    created_at: "2026-03-27T10:00:00Z",
    generations: 1,
  }), "utf-8");
}

// ---------------------------------------------------------------------------
// Core export workflow
// ---------------------------------------------------------------------------

describe("TraceExportWorkflow", () => {
  it("exports a run as a redacted public trace artifact", async () => {
    seedRun("run_001", "grid_ctf");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result = await workflow.export({
      runId: "run_001",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
    });

    expect(result.status).toBe("completed");
    expect(result.outputPath).toBeTruthy();
    expect(existsSync(result.outputPath!)).toBe(true);
  });

  it("exported artifact contains trace + manifest + attestation", async () => {
    seedRun("run_002", "grid_ctf");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result = await workflow.export({
      runId: "run_002",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: false,
    });

    const pkg = JSON.parse(readFileSync(result.outputPath!, "utf-8"));
    expect(pkg.trace).toBeDefined();
    expect(pkg.trace.schemaVersion).toBeTruthy();
    expect(pkg.trace.messages.length).toBeGreaterThan(0);
    expect(pkg.manifest).toBeDefined();
    expect(pkg.manifest.license).toBe("CC-BY-4.0");
    expect(pkg.attestation).toBeDefined();
    expect(pkg.attestation.consentGiven).toBe(true);
    expect(pkg.attestation.allowRedistribution).toBe(true);
    expect(pkg.attestation.allowTraining).toBe(false);
  });

  it("redacts sensitive data from trace messages", async () => {
    seedRun("run_secret", "grid_ctf");
    // Inject a secret into the run artifact
    const genDir = join(tmpDir, "runs", "run_secret", "generations", "gen_1");
    writeFileSync(join(genDir, "competitor_output.md"),
      "Use key sk-ant-api03-mysecretkey123 to authenticate", "utf-8");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result = await workflow.export({
      runId: "run_secret",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC0-1.0",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
    });

    const pkg = JSON.parse(readFileSync(result.outputPath!, "utf-8"));
    const allContent = pkg.trace.messages.map((m: { content: string }) => m.content).join(" ");
    expect(allContent).not.toContain("sk-ant-api03-mysecretkey123");
    expect(allContent).toContain("[REDACTED:");
    expect(result.redactionSummary.totalDetections).toBeGreaterThan(0);
  });

  it("includes redaction summary in result", async () => {
    seedRun("run_redact", "grid_ctf");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result = await workflow.export({
      runId: "run_redact",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
    });

    expect(result.redactionSummary).toBeDefined();
    expect(typeof result.redactionSummary.totalDetections).toBe("number");
    expect(typeof result.redactionSummary.totalRedactions).toBe("number");
    expect(typeof result.redactionSummary.blocked).toBe("boolean");
  });

  it("fails when run not found", async () => {
    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result = await workflow.export({
      runId: "nonexistent",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
    });

    expect(result.status).toBe("failed");
    expect(result.error).toContain("not found");
  });

  it("blocks export when redaction policy blocks", async () => {
    seedRun("run_blocked", "grid_ctf");
    const genDir = join(tmpDir, "runs", "run_blocked", "generations", "gen_1");
    writeFileSync(join(genDir, "competitor_output.md"),
      "My API key is sk-ant-api03-blockedkey12345", "utf-8");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
      policyOverrides: { api_key: "block" },
    });

    const result = await workflow.export({
      runId: "run_blocked",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
    });

    expect(result.status).toBe("blocked");
    expect(result.redactionSummary.blocked).toBe(true);
  });

  it("blocks export when consent is not given", async () => {
    seedRun("run_no_consent", "grid_ctf");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result = await workflow.export({
      runId: "run_no_consent",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: false,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
    });

    expect(result.status).toBe("blocked");
    expect(result.error).toContain("explicit consent");
  });

  it("blocks export when redistribution rights are absent", async () => {
    seedRun("run_no_redistribution", "grid_ctf");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result = await workflow.export({
      runId: "run_no_redistribution",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "third_party_authorized",
      allowRedistribution: false,
      allowTraining: false,
    });

    expect(result.status).toBe("blocked");
    expect(result.error).toContain("redistribution rights");
  });

  it("blocks overlapping secrets according to the strongest policy action", async () => {
    seedRun("run_overlap", "grid_ctf");
    const genDir = join(tmpDir, "runs", "run_overlap", "generations", "gen_1");
    writeFileSync(
      join(genDir, "competitor_output.md"),
      "API_KEY=sk-ant-api03-abc123def456ghi789",
      "utf-8",
    );

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
      policyOverrides: { api_key: "block", credential: "warn" },
    });

    const result = await workflow.export({
      runId: "run_overlap",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
    });

    expect(result.status).toBe("blocked");
    expect(result.redactionSummary.blocked).toBe(true);
    expect(result.redactionSummary.categoryCounts.api_key).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// ExportResult shape
// ---------------------------------------------------------------------------

describe("ExportResult shape", () => {
  it("has all required fields", async () => {
    seedRun("run_shape", "grid_ctf");

    const workflow = new TraceExportWorkflow({
      runsRoot: join(tmpDir, "runs"),
      outputDir: join(tmpDir, "exports"),
    });

    const result: ExportResult = await workflow.export({
      runId: "run_shape",
      scenario: "grid_ctf",
      submitterId: "user_test",
      license: "CC-BY-4.0",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
    });

    expect(result).toHaveProperty("status");
    expect(result).toHaveProperty("outputPath");
    expect(result).toHaveProperty("redactionSummary");
    expect(result).toHaveProperty("traceId");
  });
});

describe("package entrypoint exports", () => {
  it("exposes the trace export workflow through src/index", () => {
    expect(pkg.TraceExportWorkflow).toBeDefined();
  });
});
