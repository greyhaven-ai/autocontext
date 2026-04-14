import { describe, expect, it } from "vitest";
import { SCHEMA_VERSION } from "../src/traces/public-schema.js";
import {
  buildGistPayload,
  buildHuggingFacePayload,
  toPublishedDatasetRow,
  toShareGPTTrace,
} from "../src/traces/publishing-workflow.js";
import type { TraceArtifact } from "../src/traces/publishers-types.js";

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
      metadata: { family: "agent_task" },
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

describe("publishing workflow", () => {
  it("converts traces and published rows to ShareGPT-compatible payloads", () => {
    const shareGpt = toShareGPTTrace(sampleArtifact().trace);
    expect(shareGpt).toMatchObject({
      conversations: [
        { from: "human", value: "Fix the bug" },
        { from: "gpt", value: "I'll check the code" },
      ],
      metadata: {
        traceId: "trace_test_001",
        sourceHarness: "autocontext",
        schemaVersion: SCHEMA_VERSION,
        family: "agent_task",
      },
    });

    const row = toPublishedDatasetRow(sampleArtifact());
    expect(row).toMatchObject({
      provenance: { license: "CC-BY-4.0" },
      attestation: { submitterId: "user_test" },
    });
  });

  it("builds gist and Hugging Face payloads with stable contract fields", () => {
    const gistPayload = buildGistPayload(sampleArtifact()) as {
      description: string;
      files: Record<string, { content: string }>;
    };
    expect(gistPayload.description).toContain("autocontext trace: trace_test_001");
    expect(Object.keys(gistPayload.files)).toContain("trace_test_001.json");
    expect(Object.keys(gistPayload.files)).toContain("manifest.json");

    const hfPayload = buildHuggingFacePayload(sampleArtifact(), "user/traces") as {
      repoId: string;
      filename: string;
      content: string;
      license: string;
    };
    expect(hfPayload.repoId).toBe("user/traces");
    expect(hfPayload.filename).toBe("trace_test_001.json");
    expect(hfPayload.license).toBe("CC-BY-4.0");
    expect(JSON.parse(hfPayload.content)).toHaveProperty("conversations");
  });
});
