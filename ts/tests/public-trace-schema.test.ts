/**
 * AC-462: Public trace schema, provenance manifest, and submission attestation.
 *
 * Tests the open interchange format for coding agent traces.
 * This schema enables community sharing of traces for training
 * without coupling to any one harness.
 */

import { describe, it, expect } from "vitest";
import {
  ActorRef,
  PublicTraceSchema,
  ProvenanceManifestSchema,
  RunTrace,
  SubmissionAttestationSchema,
  TraceEvent,
  validatePublicTrace,
  createProvenanceManifest,
  createSubmissionAttestation,
  exportToPublicTrace,
  type PublicTrace,
  type ProvenanceManifest,
  type SubmissionAttestation,
  SCHEMA_VERSION,
} from "../src/index.js";

// ---------------------------------------------------------------------------
// Schema version
// ---------------------------------------------------------------------------

describe("schema version", () => {
  it("has a semantic version", () => {
    expect(SCHEMA_VERSION).toMatch(/^\d+\.\d+\.\d+$/);
  });
});

// ---------------------------------------------------------------------------
// PublicTrace schema
// ---------------------------------------------------------------------------

describe("PublicTraceSchema", () => {
  const validTrace: PublicTrace = {
    schemaVersion: SCHEMA_VERSION,
    traceId: "trace_abc123",
    sessionId: "session_001",
    sourceHarness: "autocontext",
    collectedAt: "2026-03-27T10:00:00Z",
    messages: [
      {
        role: "user",
        content: "Fix the login bug",
        timestamp: "2026-03-27T10:00:01Z",
      },
      {
        role: "assistant",
        content: "I'll investigate the auth module.",
        timestamp: "2026-03-27T10:00:02Z",
        toolCalls: [
          {
            toolName: "read",
            args: { path: "src/auth.ts" },
            result: "export function login() { ... }",
            durationMs: 45,
          },
        ],
      },
    ],
    outcome: {
      score: 0.85,
      reasoning: "Successfully identified and fixed the bug",
      dimensions: { accuracy: 0.9, completeness: 0.8 },
    },
    metadata: {
      model: "claude-sonnet-4-20250514",
      provider: "anthropic",
      totalTokens: 1500,
    },
  };

  it("validates a well-formed trace", () => {
    const result = validatePublicTrace(validTrace);
    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  it("requires schemaVersion", () => {
    const bad = { ...validTrace, schemaVersion: undefined };
    const result = validatePublicTrace(bad as unknown as PublicTrace);
    expect(result.valid).toBe(false);
  });

  it("rejects mismatched schemaVersion", () => {
    const bad = { ...validTrace, schemaVersion: "0.0.1" };
    const result = validatePublicTrace(bad as unknown as PublicTrace);
    expect(result.valid).toBe(false);
  });

  it("requires traceId", () => {
    const bad = { ...validTrace, traceId: "" };
    const result = validatePublicTrace(bad);
    expect(result.valid).toBe(false);
  });

  it("requires at least one message", () => {
    const bad = { ...validTrace, messages: [] };
    const result = validatePublicTrace(bad);
    expect(result.valid).toBe(false);
  });

  it("validates message role is user/assistant/system/tool", () => {
    const bad = {
      ...validTrace,
      messages: [{ role: "invalid" as never, content: "hi", timestamp: "2026-01-01T00:00:00Z" }],
    };
    const result = validatePublicTrace(bad);
    expect(result.valid).toBe(false);
  });

  it("allows optional outcome", () => {
    const noOutcome = { ...validTrace, outcome: undefined };
    const result = validatePublicTrace(noOutcome);
    expect(result.valid).toBe(true);
  });

  it("allows optional tool calls on messages", () => {
    const noTools = {
      ...validTrace,
      messages: [{ role: "user" as const, content: "hello", timestamp: "2026-01-01T00:00:00Z" }],
    };
    const result = validatePublicTrace(noTools);
    expect(result.valid).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// ProvenanceManifest
// ---------------------------------------------------------------------------

describe("ProvenanceManifest", () => {
  it("creates a valid manifest", () => {
    const manifest = createProvenanceManifest({
      sourceHarness: "autocontext",
      sourceVersion: "0.2.4",
      collectionMethod: "automated_harness_run",
      license: "CC-BY-4.0",
      traceCount: 10,
    });

    expect(manifest.sourceHarness).toBe("autocontext");
    expect(manifest.license).toBe("CC-BY-4.0");
    expect(manifest.traceCount).toBe(10);
    expect(manifest.createdAt).toBeTruthy();
    expect(manifest.schemaVersion).toBe(SCHEMA_VERSION);
  });

  it("includes redaction metadata", () => {
    const manifest = createProvenanceManifest({
      sourceHarness: "pi",
      collectionMethod: "user_shared",
      license: "CC0-1.0",
      traceCount: 1,
      redactionPolicy: {
        applied: true,
        methods: ["regex_pattern", "manual_review"],
        categories: ["api_keys", "file_paths", "personal_names"],
      },
    });

    expect(manifest.redactionPolicy?.applied).toBe(true);
    expect(manifest.redactionPolicy?.methods).toContain("regex_pattern");
  });

  it("rejects mismatched schemaVersion", () => {
    const bad = {
      schemaVersion: "0.0.1",
      sourceHarness: "test",
      collectionMethod: "manual",
      license: "MIT",
      traceCount: 1,
      createdAt: "2026-01-01T00:00:00Z",
    };
    expect(() => ProvenanceManifestSchema.parse(bad)).toThrow();
  });
});

// ---------------------------------------------------------------------------
// SubmissionAttestation
// ---------------------------------------------------------------------------

describe("SubmissionAttestation", () => {
  it("creates a valid attestation", () => {
    const attestation = createSubmissionAttestation({
      submitterId: "user_123",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
    });

    expect(attestation.schemaVersion).toBe(SCHEMA_VERSION);
    expect(attestation.consentGiven).toBe(true);
    expect(attestation.allowTraining).toBe(true);
    expect(attestation.attestedAt).toBeTruthy();
  });

  it("requires consent", () => {
    const noConsent = createSubmissionAttestation({
      submitterId: "user_456",
      consentGiven: false,
      dataOrigin: "own_work",
      allowRedistribution: false,
      allowTraining: false,
    });

    expect(noConsent.consentGiven).toBe(false);
  });

  it("requires schemaVersion on attestation payloads", () => {
    const bad = {
      submitterId: "u1",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
      attestedAt: "2026-01-01T00:00:00Z",
    };
    expect(() => SubmissionAttestationSchema.parse(bad)).toThrow();
  });
});

// ---------------------------------------------------------------------------
// Export from internal model
// ---------------------------------------------------------------------------

describe("exportToPublicTrace", () => {
  it("converts an internal RunTrace to public schema", async () => {
    const trace = new RunTrace("run_001", "grid_ctf");
    trace.addEvent(new TraceEvent({
      eventType: "generation_started",
      actor: new ActorRef("system", "harness", "autocontext"),
      payload: { generation: 1 },
    }));
    trace.addEvent(new TraceEvent({
      eventType: "role_completed",
      actor: new ActorRef("agent", "competitor", "competitor"),
      payload: { output: "my strategy", score: 0.8 },
    }));

    const publicTrace = exportToPublicTrace(trace, {
      sourceHarness: "autocontext",
      model: "claude-sonnet-4-20250514",
    });

    expect(publicTrace.schemaVersion).toBe(SCHEMA_VERSION);
    expect(publicTrace.sourceHarness).toBe("autocontext");
    expect(publicTrace.messages.length).toBeGreaterThan(0);
    expect(publicTrace.metadata?.model).toBe("claude-sonnet-4-20250514");

    const result = validatePublicTrace(publicTrace);
    expect(result.valid).toBe(true);
  });
});

describe("package entrypoint exports", () => {
  it("exposes the public trace surface through src/index", () => {
    expect(PublicTraceSchema).toBeDefined();
    expect(ProvenanceManifestSchema).toBeDefined();
    expect(SubmissionAttestationSchema).toBeDefined();
    expect(RunTrace).toBeDefined();
    expect(TraceEvent).toBeDefined();
    expect(ActorRef).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// Zod schemas parse correctly
// ---------------------------------------------------------------------------

describe("Zod schema parsing", () => {
  it("PublicTraceSchema parses valid data", () => {
    const data = {
      schemaVersion: SCHEMA_VERSION,
      traceId: "t1",
      sessionId: "s1",
      sourceHarness: "test",
      collectedAt: "2026-01-01T00:00:00Z",
      messages: [{ role: "user", content: "hi", timestamp: "2026-01-01T00:00:00Z" }],
    };
    expect(() => PublicTraceSchema.parse(data)).not.toThrow();
  });

  it("ProvenanceManifestSchema parses valid data", () => {
    const data = {
      schemaVersion: SCHEMA_VERSION,
      sourceHarness: "test",
      collectionMethod: "manual",
      license: "MIT",
      traceCount: 1,
      createdAt: "2026-01-01T00:00:00Z",
    };
    expect(() => ProvenanceManifestSchema.parse(data)).not.toThrow();
  });

  it("SubmissionAttestationSchema parses valid data", () => {
    const data = {
      schemaVersion: SCHEMA_VERSION,
      submitterId: "u1",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: true,
      attestedAt: "2026-01-01T00:00:00Z",
    };
    expect(() => SubmissionAttestationSchema.parse(data)).not.toThrow();
  });
});
