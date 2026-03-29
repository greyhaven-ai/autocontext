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

import { z } from "zod";
import type { RunTrace } from "../analytics/run-trace.js";

export const SCHEMA_VERSION = "1.0.0";
const SchemaVersionSchema = z.literal(SCHEMA_VERSION);

// ---------------------------------------------------------------------------
// Tool call
// ---------------------------------------------------------------------------

export const ToolCallSchema = z.object({
  toolName: z.string().min(1),
  args: z.record(z.unknown()).default({}),
  result: z.unknown().optional(),
  durationMs: z.number().optional(),
  error: z.string().optional(),
});

export type ToolCall = z.infer<typeof ToolCallSchema>;

// ---------------------------------------------------------------------------
// Message
// ---------------------------------------------------------------------------

export const TraceMessageSchema = z.object({
  role: z.enum(["user", "assistant", "system", "tool"]),
  content: z.string(),
  timestamp: z.string().datetime({ message: "timestamp must be ISO 8601 format" }),
  toolCalls: z.array(ToolCallSchema).optional(),
  metadata: z.record(z.unknown()).optional(),
});

export type TraceMessage = z.infer<typeof TraceMessageSchema>;

// ---------------------------------------------------------------------------
// Outcome
// ---------------------------------------------------------------------------

export const TraceOutcomeSchema = z.object({
  score: z.number().min(0).max(1),
  reasoning: z.string(),
  dimensions: z.record(z.number()).default({}),
});

export type TraceOutcome = z.infer<typeof TraceOutcomeSchema>;

// ---------------------------------------------------------------------------
// PublicTrace
// ---------------------------------------------------------------------------

export const PublicTraceSchema = z.object({
  schemaVersion: SchemaVersionSchema,
  traceId: z.string().min(1),
  sessionId: z.string().optional(),
  sourceHarness: z.string().min(1),
  collectedAt: z.string().datetime({ message: "collectedAt must be ISO 8601 format" }),
  messages: z.array(TraceMessageSchema).min(1),
  outcome: TraceOutcomeSchema.optional(),
  metadata: z.record(z.unknown()).optional(),
  fileReferences: z.array(z.object({
    path: z.string(),
    action: z.enum(["read", "write", "edit", "delete"]).optional(),
    diff: z.string().optional(),
  })).optional(),
  redactions: z.array(z.object({
    field: z.string(),
    reason: z.string(),
    method: z.string().optional(),
  })).optional(),
});

export type PublicTrace = z.infer<typeof PublicTraceSchema>;

// ---------------------------------------------------------------------------
// Redaction policy
// ---------------------------------------------------------------------------

export const RedactionPolicySchema = z.object({
  applied: z.boolean(),
  methods: z.array(z.string()).default([]),
  categories: z.array(z.string()).default([]),
});

export type RedactionPolicy = z.infer<typeof RedactionPolicySchema>;

// ---------------------------------------------------------------------------
// ProvenanceManifest
// ---------------------------------------------------------------------------

export const ProvenanceManifestSchema = z.object({
  schemaVersion: SchemaVersionSchema,
  sourceHarness: z.string().min(1),
  sourceVersion: z.string().optional(),
  collectionMethod: z.string().min(1),
  license: z.string().min(1),
  traceCount: z.number().int().nonnegative(),
  createdAt: z.string().datetime({ message: "createdAt must be ISO 8601 format" }),
  redactionPolicy: RedactionPolicySchema.optional(),
  datasetLineage: z.array(z.string()).optional(),
  metadata: z.record(z.unknown()).optional(),
});

export type ProvenanceManifest = z.infer<typeof ProvenanceManifestSchema>;

// ---------------------------------------------------------------------------
// SubmissionAttestation
// ---------------------------------------------------------------------------

export const SubmissionAttestationSchema = z.object({
  schemaVersion: SchemaVersionSchema,
  submitterId: z.string().min(1),
  consentGiven: z.boolean(),
  dataOrigin: z.string().min(1),
  allowRedistribution: z.boolean(),
  allowTraining: z.boolean(),
  attestedAt: z.string().datetime({ message: "attestedAt must be ISO 8601 format" }),
  notes: z.string().optional(),
});

export type SubmissionAttestation = z.infer<typeof SubmissionAttestationSchema>;

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

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
    errors: result.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`),
  };
}

// ---------------------------------------------------------------------------
// Factory functions
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Export from internal model
// ---------------------------------------------------------------------------

/**
 * Convert an internal RunTrace to the public schema.
 *
 * Maps internal trace events to public messages, preserving
 * provenance and timing. This bridges the internal event model
 * to the open interchange format.
 */
export function exportToPublicTrace(
  trace: RunTrace,
  opts: {
    sourceHarness: string;
    model?: string;
    provider?: string;
  },
): PublicTrace {
  const messages: TraceMessage[] = [];

  // Explicit role mapping (AC-468 fix 3)
  const SYSTEM_EVENTS = new Set([
    "generation_started", "generation_completed",
    "tournament_started", "tournament_completed",
    "gate_decided", "run_started", "run_completed",
  ]);
  const AGENT_ROLES = new Set([
    "competitor", "analyst", "coach", "architect", "curator", "translator",
  ]);

  for (const event of trace.events) {
    const actorRole = event.actor.actorName || event.actor.actorId;
    let role: TraceMessage["role"];

    if (SYSTEM_EVENTS.has(event.eventType)) {
      role = "system";
    } else if (AGENT_ROLES.has(actorRole) || event.eventType === "role_completed") {
      role = "assistant";
    } else if (event.eventType.includes("user") || actorRole === "user") {
      role = "user";
    } else {
      role = "system";
    }

    messages.push({
      role,
      content: String(event.payload.output ?? event.payload.description ?? event.eventType),
      timestamp: event.timestamp,
      metadata: {
        eventType: event.eventType,
        internalRole: actorRole,
        actor: event.actor.toDict(),
        ...event.payload,
      },
    });
  }

  // Ensure at least one message
  if (messages.length === 0) {
    messages.push({
      role: "system",
      content: `Trace ${trace.runId} for ${trace.scenarioType}`,
      timestamp: trace.createdAt,
    });
  }

  return {
    schemaVersion: SCHEMA_VERSION,
    traceId: `trace_${trace.runId}`,
    sessionId: trace.runId,
    sourceHarness: opts.sourceHarness,
    collectedAt: trace.createdAt,
    messages,
    metadata: {
      model: opts.model,
      provider: opts.provider,
      scenarioType: trace.scenarioType,
      eventCount: trace.events.length,
    },
  };
}
