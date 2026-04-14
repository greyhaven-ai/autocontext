import {
  type ProvenanceManifest,
  type PublicTrace,
  PublicTraceSchema,
  type RedactionPolicy,
  SCHEMA_VERSION,
  type SubmissionAttestation,
} from "./public-schema-contracts.js";

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

export function validatePublicTrace(trace: PublicTrace): ValidationResult {
  const result = PublicTraceSchema.safeParse(trace);
  if (result.success) {
    return { valid: true, errors: [] };
  }
  return {
    valid: false,
    errors: result.error.issues.map((issue) => `${issue.path.join(".")}: ${issue.message}`),
  };
}

export function createProvenanceManifest(opts: {
  sourceHarness: string;
  sourceVersion?: string;
  collectionMethod: string;
  license: string;
  traceCount: number;
  redactionPolicy?: RedactionPolicy;
  datasetLineage?: string[];
  metadata?: Record<string, unknown>;
}): ProvenanceManifest {
  return {
    schemaVersion: SCHEMA_VERSION,
    sourceHarness: opts.sourceHarness,
    sourceVersion: opts.sourceVersion,
    collectionMethod: opts.collectionMethod,
    license: opts.license,
    traceCount: opts.traceCount,
    createdAt: new Date().toISOString(),
    redactionPolicy: opts.redactionPolicy,
    datasetLineage: opts.datasetLineage,
    metadata: opts.metadata,
  };
}

export function createSubmissionAttestation(opts: {
  submitterId: string;
  consentGiven: boolean;
  dataOrigin: string;
  allowRedistribution: boolean;
  allowTraining: boolean;
  notes?: string;
}): SubmissionAttestation {
  return {
    schemaVersion: SCHEMA_VERSION,
    submitterId: opts.submitterId,
    consentGiven: opts.consentGiven,
    dataOrigin: opts.dataOrigin,
    allowRedistribution: opts.allowRedistribution,
    allowTraining: opts.allowTraining,
    attestedAt: new Date().toISOString(),
    notes: opts.notes,
  };
}
