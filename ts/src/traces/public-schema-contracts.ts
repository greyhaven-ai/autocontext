import { z } from "zod";

export const SCHEMA_VERSION = "1.0.0";
export const SchemaVersionSchema = z.literal(SCHEMA_VERSION);

export const ToolCallSchema = z.object({
  toolName: z.string().min(1),
  args: z.record(z.unknown()).default({}),
  result: z.unknown().optional(),
  durationMs: z.number().optional(),
  error: z.string().optional(),
});

export type ToolCall = z.infer<typeof ToolCallSchema>;

export const TraceMessageSchema = z.object({
  role: z.enum(["user", "assistant", "system", "tool"]),
  content: z.string(),
  timestamp: z.string().datetime({ message: "timestamp must be ISO 8601 format" }),
  toolCalls: z.array(ToolCallSchema).optional(),
  metadata: z.record(z.unknown()).optional(),
});

export type TraceMessage = z.infer<typeof TraceMessageSchema>;

export const TraceOutcomeSchema = z.object({
  score: z.number().min(0).max(1),
  reasoning: z.string(),
  dimensions: z.record(z.number()).default({}),
});

export type TraceOutcome = z.infer<typeof TraceOutcomeSchema>;

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

export const RedactionPolicySchema = z.object({
  applied: z.boolean(),
  methods: z.array(z.string()).default([]),
  categories: z.array(z.string()).default([]),
});

export type RedactionPolicy = z.infer<typeof RedactionPolicySchema>;

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
