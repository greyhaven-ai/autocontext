/**
 * AC-697 slice 1: shared CLI contract loader (TypeScript side).
 *
 * Loads the canonical contract at `docs/cli-contract.json` (the same
 * JSON the Python `cli_contract.py` loader consumes) and validates
 * it via Zod. Parity tests in `ts/tests/cli-contract.test.ts` use
 * this loader to assert that every command marked
 * `runtime_support.typescript === "yes"` is actually registered in
 * `command-registry.ts`.
 *
 * DRY: a single edit to the JSON file moves both runtimes' parity
 * targets.
 */

import { z } from "zod";

export const RuntimeStatus = z.enum(["yes", "missing", "intentional_gap"]);
export type RuntimeStatus = z.infer<typeof RuntimeStatus>;

export const RuntimeSupportSchema = z.object({
  status: RuntimeStatus,
  reason: z.string().optional().default(""),
});

export const RuntimeSupportPairSchema = z.object({
  python: RuntimeSupportSchema,
  typescript: RuntimeSupportSchema,
});

export const FlagSchema = z.object({
  name: z.string(),
  type: z.string().default("string"),
  aliases: z.array(z.string()).default([]),
  required: z.boolean().default(false),
  description: z.string().default(""),
});

export const CommandSpecSchema = z.object({
  id: z.string(),
  path: z.array(z.string()).nonempty(),
  summary: z.string(),
  audience: z.enum(["paved_road", "advanced", "internal"]),
  maturity: z.string().default("stable"),
  domain_concept: z
    .enum(["Scenario", "Task", "Mission", "Run", "Artifact", "Knowledge"])
    .nullable()
    .default(null),
  aliases: z.array(z.string()).default([]),
  runtime_support: RuntimeSupportPairSchema,
  flags: z.array(FlagSchema).default([]),
  output_contract: z.enum(["json", "text", "none"]).default("text"),
});

export const ContractSchema = z.object({
  schema_version: z.number().int().positive(),
  commands: z.array(CommandSpecSchema),
  description: z.string().optional(),
});

export type CommandSpec = z.infer<typeof CommandSpecSchema>;
export type Contract = z.infer<typeof ContractSchema>;

/** Paved-road command ids. Must match the constant in
 * `autocontext.cli_contract.PAVED_ROAD`. */
export const PAVED_ROAD: ReadonlySet<string> = new Set([
  "solve",
  "run",
  "status",
  "watch",
  "show",
  "export",
]);

import { readFileSync } from "node:fs";

export function loadContract(path: string): Contract {
  const raw = readFileSync(path, "utf-8");
  const parsed: unknown = JSON.parse(raw);
  return ContractSchema.parse(parsed);
}

/** Return the canonical command id every alias resolves to, or
 * `undefined` if the alias is not registered in the contract. */
export function resolveAlias(contract: Contract, alias: string): string | undefined {
  for (const cmd of contract.commands) {
    if (cmd.aliases.includes(alias)) {
      return cmd.id;
    }
  }
  return undefined;
}
