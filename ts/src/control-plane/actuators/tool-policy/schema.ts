// Payload schema for the tool-policy actuator.
//
// The payload directory contains exactly one file:
//   policy.json — a tool-allow-list policy document.
//
// Minimal v1 shape: { version: "1", tools: Record<string, { allow?: boolean; parameters?: unknown }> }
// Additional top-level fields are currently rejected; the `parameters` inside a
// tool entry is passthrough `unknown` so future schemas can evolve without a
// v1 rewrite.

import { z } from "zod";

export const ToolPolicyEntrySchema = z.object({
  allow: z.boolean().optional(),
  parameters: z.unknown().optional(),
});

export const ToolPolicyPayloadSchema = z
  .object({
    version: z.literal("1"),
    tools: z.record(z.string(), ToolPolicyEntrySchema),
  })
  .strict();

export type ToolPolicyEntry = z.infer<typeof ToolPolicyEntrySchema>;
export type ToolPolicyPayload = z.infer<typeof ToolPolicyPayloadSchema>;

export const TOOL_POLICY_FILENAME = "policy.json";
