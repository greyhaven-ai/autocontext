/**
 * AC-468: Trace pipeline audit fixes.
 *
 * Tests for: expanded redaction patterns, timestamp validation,
 * explicit role mapping, export warnings, HF format, ESM consistency.
 */

import { describe, it, expect } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SensitiveDataDetector, applyRedactionPolicy } from "../src/traces/redaction.js";
import { PublicTraceSchema, exportToPublicTrace, SCHEMA_VERSION } from "../src/traces/public-schema.js";
import { TraceExportWorkflow } from "../src/index.js";

// ---------------------------------------------------------------------------
// 1. Expanded redaction patterns
// ---------------------------------------------------------------------------

describe("redaction: expanded secret patterns (AC-468 fix 1)", () => {
  const detector = new SensitiveDataDetector();

  // Build test tokens programmatically to avoid GitHub secret scanning
  const slackPrefix = ["xox", "b"].join("");
  const stripePrefix = ["sk", "_", "live", "_"].join("");
  const npmPrefix = ["npm", "_"].join("");
  const sgPrefix = ["SG", "."].join("");

  it("detects Slack tokens", () => {
    const text = `Bot token: ${slackPrefix}-AAABBBCCCDDD-EEEFFFGGGHHH`;
    expect(detector.scan(text).some((f) => f.category === "api_key")).toBe(true);
  });

  it("detects Stripe keys", () => {
    const text = `Stripe key: ${stripePrefix}AABBCCDDEE00112233445566`;
    expect(detector.scan(text).some((f) => f.category === "api_key")).toBe(true);
  });

  it("detects npm tokens", () => {
    const text = `Token: ${npmPrefix}AABBCCDDEE00112233445566`;
    expect(detector.scan(text).some((f) => f.category === "api_key")).toBe(true);
  });

  it("detects SSH private keys", () => {
    const marker = ["-----BEGIN", " RSA", " PRIVATE", " KEY-----"].join("");
    const text = `${marker}\nMIIEpAIBAAKCAQEA...`;
    expect(detector.scan(text).some((f) => f.category === "credential")).toBe(true);
  });

  it("detects SendGrid keys", () => {
    const text = `Key: ${sgPrefix}AABBCCDDEE00112233445566`;
    expect(detector.scan(text).some((f) => f.category === "api_key")).toBe(true);
  });

  it("detects generic long hex tokens", () => {
    const text = "Token: a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4";
    expect(detector.scan(text).some((f) => f.category === "credential")).toBe(true);
  });

  it("does not flag short hex strings", () => {
    const text = "Color: #ff0000 and id: abc123";
    const hexFindings = detector.scan(text).filter((f) => f.category === "credential" && f.label.includes("hex"));
    expect(hexFindings.length).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// 2. Timestamp validation
// ---------------------------------------------------------------------------

describe("schema: ISO 8601 timestamp validation (AC-468 fix 2)", () => {
  it("accepts valid ISO 8601 timestamps", () => {
    const data = {
      schemaVersion: SCHEMA_VERSION,
      traceId: "t1",
      sourceHarness: "test",
      collectedAt: "2026-03-27T10:00:00Z",
      messages: [{ role: "user", content: "hi", timestamp: "2026-03-27T10:00:01Z" }],
    };
    expect(() => PublicTraceSchema.parse(data)).not.toThrow();
  });

  it("rejects invalid timestamps", () => {
    const data = {
      schemaVersion: SCHEMA_VERSION,
      traceId: "t1",
      sourceHarness: "test",
      collectedAt: "yesterday",
      messages: [{ role: "user", content: "hi", timestamp: "2026-03-27T10:00:01Z" }],
    };
    const result = PublicTraceSchema.safeParse(data);
    expect(result.success).toBe(false);
  });

  it("rejects non-ISO message timestamps", () => {
    const data = {
      schemaVersion: SCHEMA_VERSION,
      traceId: "t1",
      sourceHarness: "test",
      collectedAt: "2026-03-27T10:00:00Z",
      messages: [{ role: "user", content: "hi", timestamp: "not a date" }],
    };
    const result = PublicTraceSchema.safeParse(data);
    expect(result.success).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 3. Explicit role mapping in exportToPublicTrace
// ---------------------------------------------------------------------------

describe("exportToPublicTrace: explicit role mapping (AC-468 fix 3)", () => {
  it("maps generation_started to system", async () => {
    const { RunTrace, TraceEvent, ActorRef } = await import("../src/analytics/run-trace.js");
    const trace = new RunTrace("run_1", "grid_ctf");
    trace.addEvent(new TraceEvent({
      eventType: "generation_started",
      actor: new ActorRef("system", "harness", "autocontext"),
      payload: { generation: 1 },
    }));
    const result = exportToPublicTrace(trace, { sourceHarness: "autocontext" });
    expect(result.messages[0].role).toBe("system");
  });

  it("maps competitor role_completed to assistant", async () => {
    const { RunTrace, TraceEvent, ActorRef } = await import("../src/analytics/run-trace.js");
    const trace = new RunTrace("run_2", "grid_ctf");
    trace.addEvent(new TraceEvent({
      eventType: "role_completed",
      actor: new ActorRef("agent", "competitor", "competitor"),
      payload: { output: "strategy" },
    }));
    const result = exportToPublicTrace(trace, { sourceHarness: "autocontext" });
    expect(result.messages[0].role).toBe("assistant");
    expect(result.messages[0].metadata?.internalRole).toBe("competitor");
  });

  it("maps analyst role to assistant with role metadata", async () => {
    const { RunTrace, TraceEvent, ActorRef } = await import("../src/analytics/run-trace.js");
    const trace = new RunTrace("run_3", "grid_ctf");
    trace.addEvent(new TraceEvent({
      eventType: "role_completed",
      actor: new ActorRef("agent", "analyst", "analyst"),
      payload: { output: "analysis" },
    }));
    const result = exportToPublicTrace(trace, { sourceHarness: "autocontext" });
    expect(result.messages[0].role).toBe("assistant");
    expect(result.messages[0].metadata?.internalRole).toBe("analyst");
  });

  it("maps assistant roles from actorName when actorId is an opaque runtime id", async () => {
    const { RunTrace, TraceEvent, ActorRef } = await import("../src/analytics/run-trace.js");
    const trace = new RunTrace("run_3b", "grid_ctf");
    trace.addEvent(new TraceEvent({
      eventType: "analysis_ready",
      actor: new ActorRef("agent", "agent_123", "analyst"),
      payload: { output: "analysis" },
    }));

    const result = exportToPublicTrace(trace, { sourceHarness: "autocontext" });

    expect(result.messages[0].role).toBe("assistant");
    expect(result.messages[0].metadata?.internalRole).toBe("analyst");
  });

  it("maps tournament events to system", async () => {
    const { RunTrace, TraceEvent, ActorRef } = await import("../src/analytics/run-trace.js");
    const trace = new RunTrace("run_4", "grid_ctf");
    trace.addEvent(new TraceEvent({
      eventType: "tournament_completed",
      actor: new ActorRef("system", "harness", "autocontext"),
      payload: { mean_score: 0.85 },
    }));
    const result = exportToPublicTrace(trace, { sourceHarness: "autocontext" });
    expect(result.messages[0].role).toBe("system");
  });
});

// ---------------------------------------------------------------------------
// 4. Export workflow warnings (tested via export-workflow if present)
// ---------------------------------------------------------------------------

describe("TraceExportWorkflow warnings (AC-468 fix 4)", () => {
  it("reports unreadable artifacts instead of silently skipping them", async () => {
    const root = mkdtempSync(join(tmpdir(), "ac-468-warnings-"));
    try {
      const runDir = join(root, "runs", "run_warn");
      const genDir = join(runDir, "generations", "gen_1");
      mkdirSync(genDir, { recursive: true });

      writeFileSync(join(runDir, "run_meta.json"), "{not valid json", "utf-8");
      mkdirSync(join(genDir, "competitor_output.md"));
      writeFileSync(join(genDir, "analyst.md"), "usable analysis", "utf-8");

      const workflow = new TraceExportWorkflow({
        runsRoot: join(root, "runs"),
        outputDir: join(root, "exports"),
      });

      const result = await workflow.export({
        runId: "run_warn",
        scenario: "grid_ctf",
        submitterId: "user_test",
        license: "CC-BY-4.0",
        consentGiven: true,
        dataOrigin: "own_work",
        allowRedistribution: true,
        allowTraining: true,
      });

      expect(result.status).toBe("completed");
      expect(result.warnings.length).toBeGreaterThanOrEqual(2);
      expect(result.warnings.some((warning) => warning.includes("run_meta.json"))).toBe(true);
      expect(result.warnings.some((warning) => warning.includes("competitor_output.md"))).toBe(true);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------------------
// 5. HF format fix — covered in publishers.test.ts update
// ---------------------------------------------------------------------------

// Covered by updating HuggingFacePublisher

// ---------------------------------------------------------------------------
// 6. No require() in ESM — static check
// ---------------------------------------------------------------------------

describe("ESM consistency (AC-468 fix 6)", () => {
  it("publishers.ts does not use require()", async () => {
    const { readFileSync } = await import("node:fs");
    const { join, dirname } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const __dirname = dirname(fileURLToPath(import.meta.url));
    const source = readFileSync(join(__dirname, "..", "src", "traces", "publishers.ts"), "utf-8");
    // Should not have bare require() calls (dynamic import is fine)
    const requireMatches = source.match(/\brequire\s*\(/g);
    expect(requireMatches).toBeNull();
  });
});
