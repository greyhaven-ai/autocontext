// Payload schema for the model-routing actuator (AC-545, spec §4).
//
// The payload directory contains exactly one file:
//   models.json — a top-level declarative config with a `default` model target,
//                 ordered `routes[]`, and a `fallback[]` chain with guardrails.
//
// The canonical schema lives in
//   control-plane/contract/json-schemas/model-routing-payload.schema.json
// — this Zod schema is the TS echo used for type ergonomics (parsePayload and
// the runtime helper both ingest the already-Zod-parsed tree). DDD note: field
// names are taken verbatim from the spec (`default`, `routes`, `fallback`,
// `match`, `rollout`, `budget`, `latency`, `confidence`, `cohortKey`).

import { z } from "zod";

// ---- Primitives ----

export const ModelTargetSchema = z
  .object({
    provider: z.string().min(1),
    model: z.string().min(1),
    endpoint: z.string().nullable().optional(),
  })
  .strict();

/**
 * A per-field operator. The JSON Schema requires each operator object contain
 * exactly one of { equals, contains, default:true }. The Zod echo enforces the
 * same rule so invalid configs fail before registration/runtime.
 */
export const MatchOperatorSchema = z
  .object({
    equals: z.unknown().optional(),
    contains: z.union([z.string(), z.array(z.unknown())]).optional(),
    default: z.literal(true).optional(),
  })
  .strict()
  .superRefine((op, ctx) => {
    const set = [
      op.equals !== undefined,
      op.contains !== undefined,
      op.default === true,
    ].filter(Boolean).length;
    if (set !== 1) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "match operator must set exactly one of equals, contains, or default:true",
      });
    }
  });

export const MatchExpressionSchema = z
  .record(z.string(), MatchOperatorSchema)
  .refine((match) => Object.keys(match).length > 0, {
    message: "match expression must include at least one condition",
  });

export const RolloutSchema = z
  .object({
    percent: z.number().min(0).max(100),
    cohortKey: z.string().min(1),
  })
  .strict();

export const BudgetGuardrailSchema = z
  .object({
    maxCostUsdPerCall: z.number().min(0),
  })
  .strict();

export const LatencyGuardrailSchema = z
  .object({
    maxP95Ms: z.number().min(0),
  })
  .strict();

export const ConfidenceGuardrailSchema = z
  .object({
    minScore: z.number().min(0).max(1),
  })
  .strict();

// ---- Aggregates ----

export const RouteSchema = z
  .object({
    id: z.string().min(1),
    match: MatchExpressionSchema,
    target: ModelTargetSchema,
    rollout: RolloutSchema.optional(),
    budget: BudgetGuardrailSchema.optional(),
    latency: LatencyGuardrailSchema.optional(),
    confidence: ConfidenceGuardrailSchema.optional(),
  })
  .strict();

export const FallbackReasonSchema = z.enum([
  "budget-exceeded",
  "latency-breached",
  "provider-error",
  "no-match",
]);

export const FallbackEntrySchema = z
  .object({
    provider: z.string().min(1),
    model: z.string().min(1),
    endpoint: z.string().nullable().optional(),
    when: z.array(FallbackReasonSchema).optional(),
  })
  .strict();

export const ModelRoutingPayloadSchema = z
  .object({
    schemaVersion: z.literal("1.0"),
    default: ModelTargetSchema,
    routes: z.array(RouteSchema),
    fallback: z.array(FallbackEntrySchema),
  })
  .strict();

// ---- Types ----

export type ModelTarget = z.infer<typeof ModelTargetSchema>;
export type MatchOperator = z.infer<typeof MatchOperatorSchema>;
export type MatchExpression = z.infer<typeof MatchExpressionSchema>;
export type Rollout = z.infer<typeof RolloutSchema>;
export type BudgetGuardrail = z.infer<typeof BudgetGuardrailSchema>;
export type LatencyGuardrail = z.infer<typeof LatencyGuardrailSchema>;
export type ConfidenceGuardrail = z.infer<typeof ConfidenceGuardrailSchema>;
export type Route = z.infer<typeof RouteSchema>;
export type FallbackReason = z.infer<typeof FallbackReasonSchema>;
export type FallbackEntry = z.infer<typeof FallbackEntrySchema>;
export type ModelRoutingPayload = z.infer<typeof ModelRoutingPayloadSchema>;

export const MODEL_ROUTING_FILENAME = "models.json";
