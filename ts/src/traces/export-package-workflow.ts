import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import {
  SCHEMA_VERSION,
  createProvenanceManifest,
  createSubmissionAttestation,
  validatePublicTrace,
  type PublicTrace,
  type TraceMessage,
} from "./public-schema.js";
import type {
  ExportRequest,
  ExportResult,
  RedactionSummary,
} from "./export-workflow-types.js";

export function blockExportResult(opts: {
  traceId: string;
  redactionSummary: RedactionSummary;
  warnings: string[];
  error: string;
}): ExportResult {
  return {
    status: "blocked",
    traceId: opts.traceId,
    redactionSummary: opts.redactionSummary,
    warnings: opts.warnings,
    error: opts.error,
  };
}

export function failExportResult(opts: {
  traceId: string;
  redactionSummary: RedactionSummary;
  warnings: string[];
  error: string;
}): ExportResult {
  return {
    status: "failed",
    traceId: opts.traceId,
    redactionSummary: opts.redactionSummary,
    warnings: opts.warnings,
    error: opts.error,
  };
}

export function buildPublicTracePackage(opts: {
  traceId: string;
  request: ExportRequest;
  messages: TraceMessage[];
  redactionSummary: RedactionSummary;
}): {
  trace: PublicTrace;
  manifest: ReturnType<typeof createProvenanceManifest>;
  attestation: ReturnType<typeof createSubmissionAttestation>;
  redactionSummary: RedactionSummary;
} {
  const trace: PublicTrace = {
    schemaVersion: SCHEMA_VERSION,
    traceId: opts.traceId,
    sessionId: opts.request.runId,
    sourceHarness: "autocontext",
    collectedAt: new Date().toISOString(),
    messages: opts.messages,
    metadata: {
      scenario: opts.request.scenario,
      exportedAt: new Date().toISOString(),
    },
  };

  const manifest = createProvenanceManifest({
    sourceHarness: "autocontext",
    collectionMethod: "automated_harness_run",
    license: opts.request.license,
    traceCount: 1,
    redactionPolicy: {
      applied: opts.redactionSummary.totalRedactions > 0,
      methods: ["regex_pattern"],
      categories: Object.keys(opts.redactionSummary.categoryCounts),
    },
  });

  const attestation = createSubmissionAttestation({
    submitterId: opts.request.submitterId,
    consentGiven: opts.request.consentGiven,
    dataOrigin: opts.request.dataOrigin,
    allowRedistribution: opts.request.allowRedistribution,
    allowTraining: opts.request.allowTraining,
    notes: opts.request.consentNotes,
  });

  return { trace, manifest, attestation, redactionSummary: opts.redactionSummary };
}

export function validateExportTrace(trace: PublicTrace): { valid: boolean; error?: string } {
  const validation = validatePublicTrace(trace);
  if (validation.valid) {
    return { valid: true };
  }
  return {
    valid: false,
    error: `Trace validation failed: ${validation.errors.join("; ")}`,
  };
}

export function writeExportArtifact(outputDir: string, traceId: string, pkg: unknown): string {
  if (!existsSync(outputDir)) {
    mkdirSync(outputDir, { recursive: true });
  }
  const outputPath = join(outputDir, `${traceId}.json`);
  writeFileSync(outputPath, JSON.stringify(pkg, null, 2), "utf-8");
  return outputPath;
}
