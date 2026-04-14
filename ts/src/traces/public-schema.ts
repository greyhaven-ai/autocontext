/**
 * Public trace schema — open interchange format for coding agent traces (AC-462).
 *
 * Defines the versioned public contract for exporting, sharing, and ingesting
 * agent traces across harnesses. Enables a privacy-aware commons of real-world
 * coding agent sessions for community training.
 *
 * Three core contracts:
 * 1. PublicTrace — the session data itself
 * 2. ProvenanceManifest — where it came from, how it was collected, licensing
 * 3. SubmissionAttestation — consent, rights, and redistribution terms
 */

import {
  createProvenanceManifest,
  createSubmissionAttestation,
  validatePublicTrace,
  type ValidationResult,
} from "./public-schema-factories.js";
import {
  exportRunTraceToPublicTrace,
} from "./public-trace-export-workflow.js";

export {
  SCHEMA_VERSION,
  SchemaVersionSchema,
  ToolCallSchema,
  TraceMessageSchema,
  TraceOutcomeSchema,
  PublicTraceSchema,
  RedactionPolicySchema,
  ProvenanceManifestSchema,
  SubmissionAttestationSchema,
} from "./public-schema-contracts.js";
export type {
  ToolCall,
  TraceMessage,
  TraceOutcome,
  PublicTrace,
  RedactionPolicy,
  ProvenanceManifest,
  SubmissionAttestation,
} from "./public-schema-contracts.js";
export type { ValidationResult } from "./public-schema-factories.js";

export {
  createProvenanceManifest,
  createSubmissionAttestation,
  validatePublicTrace,
};

export function exportToPublicTrace(
  trace: import("../analytics/run-trace.js").RunTrace,
  opts: {
    sourceHarness: string;
    model?: string;
    provider?: string;
  },
): import("./public-schema-contracts.js").PublicTrace {
  return exportRunTraceToPublicTrace(trace, opts);
}
