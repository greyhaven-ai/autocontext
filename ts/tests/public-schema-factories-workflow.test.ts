import { describe, expect, it } from "vitest";

import {
  createProvenanceManifest,
  createSubmissionAttestation,
  validatePublicTrace,
} from "../src/traces/public-schema-factories.js";
import { SCHEMA_VERSION } from "../src/traces/public-schema-contracts.js";

describe("public schema factories workflow", () => {
  it("validates traces and creates manifest/attestation payloads with schema version", () => {
    expect(validatePublicTrace({
      schemaVersion: SCHEMA_VERSION,
      traceId: "trace_1",
      sessionId: "session_1",
      sourceHarness: "autocontext",
      collectedAt: "2026-01-01T00:00:00Z",
      messages: [{ role: "user", content: "hi", timestamp: "2026-01-01T00:00:00Z" }],
    })).toEqual({ valid: true, errors: [] });

    expect(validatePublicTrace({
      schemaVersion: SCHEMA_VERSION,
      traceId: "",
      sourceHarness: "autocontext",
      collectedAt: "2026-01-01T00:00:00Z",
      messages: [],
    } as never).valid).toBe(false);

    const manifest = createProvenanceManifest({
      sourceHarness: "autocontext",
      sourceVersion: "0.2.4",
      collectionMethod: "automated_harness_run",
      license: "CC-BY-4.0",
      traceCount: 10,
    });
    expect(manifest).toMatchObject({
      schemaVersion: SCHEMA_VERSION,
      sourceHarness: "autocontext",
      traceCount: 10,
    });

    const attestation = createSubmissionAttestation({
      submitterId: "user_123",
      consentGiven: true,
      dataOrigin: "own_work",
      allowRedistribution: true,
      allowTraining: false,
      notes: "evaluation only",
    });
    expect(attestation).toMatchObject({
      schemaVersion: SCHEMA_VERSION,
      submitterId: "user_123",
      consentGiven: true,
      allowTraining: false,
      notes: "evaluation only",
    });
  });
});
