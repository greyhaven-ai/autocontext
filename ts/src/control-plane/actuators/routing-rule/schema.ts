// Payload schema for the routing-rule actuator.
//
// The payload directory contains exactly one file:
//   rule.json — an ordered list of (match, route) rules.
//
// Minimal v1 shape: { version: "1", rules: Array<{ match: unknown; route: string }> }
// The `match` shape is intentionally unknown in v1 — routers may evolve their
// own expression languages; v1 only standardizes the envelope and the `route`
// target, which must be a non-empty string.

import { z } from "zod";

export const RoutingRuleEntrySchema = z
  .object({
    match: z.unknown(),
    route: z.string().min(1),
  })
  .strict();

export const RoutingRulePayloadSchema = z
  .object({
    version: z.literal("1"),
    rules: z.array(RoutingRuleEntrySchema),
  })
  .strict()
  .superRefine((val, ctx) => {
    for (let i = 0; i < val.rules.length; i++) {
      const r = val.rules[i]!;
      if (r.match === undefined) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["rules", i, "match"],
          message: "match is required",
        });
      }
    }
  });

export type RoutingRuleEntry = z.infer<typeof RoutingRuleEntrySchema>;
export type RoutingRulePayload = z.infer<typeof RoutingRulePayloadSchema>;

export const ROUTING_RULE_FILENAME = "rule.json";
