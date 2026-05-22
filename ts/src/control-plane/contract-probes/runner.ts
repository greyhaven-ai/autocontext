/**
 * AC-728: contract-probe suite runner.
 *
 * Library-level entry point that dispatches a JSON-defined probe suite
 * across all seven AC-728 probes (directory / terminal / service / artifact
 * / cleanup / media / distributed) and aggregates results. The runner is
 * pure: callers do the IO to populate the observations; the runner verifies.
 *
 * The probes themselves stay in `./index.js`. This module adds:
 *
 * - `ContractProbeSuiteSchema` (Zod): validates the JSON spec format.
 *   Every nested object is `.strict()` so a typo like `requiredStdoutPattern`
 *   (missing `s`) fails validation rather than silently being dropped --
 *   for a JSON harness contract, malformed expectations must reject, not
 *   disappear.
 * - `runContractProbeSuite(suite)`: dispatches every invocation through the
 *   matching probe and aggregates results. The result is a discriminated
 *   union over `kind`, so each variant preserves its probe's specific
 *   failure type (e.g. `DirectoryContractFailure` carries `path`,
 *   `DistributedContractFailure` carries optional `rank` / `key`); callers
 *   switch on `kind` and access the typed fields without casts.
 * - `loadContractProbeSuite(path)`: file loader mirroring the
 *   `loadContract` pattern from `cli-contract.ts`.
 *
 * JSON wire format notes:
 *
 * - RegExp values are serialised as either a bare string (`"^trace\\."`,
 *   treated as the pattern with no flags) or as `{ source, flags? }`.
 *   The schema transforms either form to a real RegExp before handing it
 *   to the probe. Construction errors (`new RegExp` throwing
 *   `SyntaxError`) are reported via Zod issues so `safeParse` returns
 *   `{ success: false }` rather than throwing.
 * - Date values (cleanup probe's `now` and per-entry `mtime`) are
 *   serialised as ISO-8601 strings and parsed into Date objects by the
 *   schema. Malformed dates are reported via Zod issues, not raw throws.
 */

import { readFileSync } from "node:fs";

import { z } from "zod";

import {
  probeArtifactContract,
  probeCleanupContract,
  probeDirectoryContract,
  probeDistributedContract,
  probeMediaContract,
  probeServiceContract,
  probeTerminalContract,
} from "./index.js";
import type {
  ArtifactContractFailure,
  CleanupContractFailure,
  DirectoryContractFailure,
  DistributedContractFailure,
  MediaContractFailure,
  ServiceContractFailure,
  TerminalContractFailure,
} from "./index.js";

// --- Reusable Zod helpers ---------------------------------------------------

// RegExp values arrive as a bare pattern string or as a `{ source, flags? }`
// object. Both forms transform into a real RegExp via a safeParse-friendly
// transform: construction errors land as Zod issues rather than raw throws,
// so callers can rely on `safeParse` returning `{ success: false }` instead
// of catching SyntaxError separately.
const RegExpJson = z
  .union([z.string(), z.object({ source: z.string(), flags: z.string().optional() }).strict()])
  .transform((value, ctx) => {
    try {
      return typeof value === "string" ? new RegExp(value) : new RegExp(value.source, value.flags);
    } catch (err) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: `invalid regular expression: ${err instanceof Error ? err.message : String(err)}`,
      });
      return z.NEVER;
    }
  });

// Date values arrive as either an existing Date or an ISO-8601 string.
// Same safeParse posture: invalid dates land as Zod issues.
const DateJson = z.union([z.string(), z.date()]).transform((value, ctx) => {
  if (value instanceof Date) {
    return value;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message: `invalid ISO-8601 date: ${JSON.stringify(value)}`,
    });
    return z.NEVER;
  }
  return parsed;
});

// --- Per-probe input schemas ------------------------------------------------
//
// Every object schema is `.strict()`: unknown keys fail validation rather
// than being silently stripped. This catches typos like
// `requiredStdoutPattern` (missing `s`) at parse time -- otherwise the
// missing-`s` field would disappear and the runner would silently skip the
// expectation, returning `passed: true` for what was supposed to be a
// stronger contract.

const DirectoryInputs = z
  .object({
    presentFiles: z.array(z.string()),
    requiredFiles: z.array(z.string()),
    allowedFiles: z.array(z.string()),
    ignoredPatterns: z.array(RegExpJson).optional(),
  })
  .strict();

const TerminalInputs = z
  .object({
    exitCode: z.number(),
    stdout: z.string(),
    stderr: z.string(),
    expectedExitCode: z.number().optional(),
    requiredStdoutPatterns: z.array(RegExpJson).optional(),
    forbiddenStdoutPatterns: z.array(RegExpJson).optional(),
    requiredStderrPatterns: z.array(RegExpJson).optional(),
    forbiddenStderrPatterns: z.array(RegExpJson).optional(),
  })
  .strict();

const ServiceEndpoint = z
  .object({
    host: z.string(),
    port: z.number(),
    protocol: z.enum(["tcp", "udp"]).optional(),
  })
  .strict();

const ServiceInputs = z
  .object({
    observed: z.array(ServiceEndpoint),
    required: z.array(ServiceEndpoint),
    allowed: z.array(ServiceEndpoint).optional(),
  })
  .strict();

const ArtifactInputs = z
  .object({
    path: z.string(),
    content: z.string(),
    expectedLineEnding: z.enum(["lf", "crlf"]).optional(),
    requiredSubstrings: z.array(z.string()).optional(),
    forbiddenSubstrings: z.array(z.string()).optional(),
    requiredJsonFields: z.array(z.string()).optional(),
  })
  .strict();

const CleanupEntry = z
  .object({
    path: z.string(),
    isSymlink: z.boolean().optional(),
    symlinkTarget: z.string().optional(),
    symlinkBroken: z.boolean().optional(),
    mtime: DateJson.optional(),
  })
  .strict();

const CleanupInputs = z
  .object({
    entries: z.array(CleanupEntry),
    now: DateJson.optional(),
    maxLockfileAgeMs: z.number().optional(),
    lockfilePatterns: z.array(RegExpJson).optional(),
    sidecarPatterns: z.array(RegExpJson).optional(),
    backupPatterns: z.array(RegExpJson).optional(),
    forbidSymlinks: z.boolean().optional(),
    allowedSymlinkTargets: z.array(z.string()).optional(),
    ignoredPatterns: z.array(RegExpJson).optional(),
  })
  .strict();

const MediaInputs = z
  .object({
    path: z.string(),
    headerBytes: z.array(z.number()).optional(),
    expectedMagicBytes: z.array(z.number()).optional(),
    width: z.number().optional(),
    height: z.number().optional(),
    expectedWidth: z.number().optional(),
    expectedHeight: z.number().optional(),
    byteSize: z.number().optional(),
    minByteSize: z.number().optional(),
    maxByteSize: z.number().optional(),
    columnCount: z.number().optional(),
    expectedColumnCount: z.number().optional(),
    columnNames: z.array(z.string()).optional(),
    requiredColumnNames: z.array(z.string()).optional(),
    lineCount: z.number().optional(),
    expectedLineCount: z.number().optional(),
  })
  .strict();

const DistributedRank = z
  .object({
    rank: z.number(),
    steps: z.number().optional(),
    observations: z.record(z.string(), z.string()).optional(),
  })
  .strict();

const DistributedInputs = z
  .object({
    ranks: z.array(DistributedRank),
    worldSize: z.number().optional(),
    expectedWorldSize: z.number().optional(),
    expectedSteps: z.number().optional(),
    mustMatchAcrossRanks: z.array(z.string()).optional(),
  })
  .strict();

// --- Suite envelope ---------------------------------------------------------

export const ContractProbeKindEnum = z.enum([
  "directory",
  "terminal",
  "service",
  "artifact",
  "cleanup",
  "media",
  "distributed",
]);

export type ContractProbeKind = z.infer<typeof ContractProbeKindEnum>;

const InvocationBase = { label: z.string().optional() };

// Every invocation envelope is `.strict()` too -- unknown keys at the
// invocation level (e.g. a misspelled `inputs2`) must fail validation.
const ContractProbeInvocationSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("directory"), ...InvocationBase, inputs: DirectoryInputs }).strict(),
  z.object({ kind: z.literal("terminal"), ...InvocationBase, inputs: TerminalInputs }).strict(),
  z.object({ kind: z.literal("service"), ...InvocationBase, inputs: ServiceInputs }).strict(),
  z.object({ kind: z.literal("artifact"), ...InvocationBase, inputs: ArtifactInputs }).strict(),
  z.object({ kind: z.literal("cleanup"), ...InvocationBase, inputs: CleanupInputs }).strict(),
  z.object({ kind: z.literal("media"), ...InvocationBase, inputs: MediaInputs }).strict(),
  z
    .object({ kind: z.literal("distributed"), ...InvocationBase, inputs: DistributedInputs })
    .strict(),
]);

export type ContractProbeInvocation = z.infer<typeof ContractProbeInvocationSchema>;

export const ContractProbeSuiteSchema = z
  .object({
    schema_version: z.literal(1),
    probes: z.array(ContractProbeInvocationSchema),
  })
  .strict();

export type ContractProbeSuite = z.infer<typeof ContractProbeSuiteSchema>;

// --- Results ----------------------------------------------------------------
//
// `ContractProbeRunResult` is a discriminated union over `kind` so each
// variant preserves the probe's specific failure type. TypeScript callers
// can switch on `result.kind` and access `path` / `rank` / `key` /
// `endpoint` etc. directly, without casting through a generic `unknown`.

interface RunResultBase {
  readonly label?: string;
  readonly passed: boolean;
}

export type ContractProbeRunResult =
  | (RunResultBase & {
      readonly kind: "directory";
      readonly failures: readonly DirectoryContractFailure[];
    })
  | (RunResultBase & {
      readonly kind: "terminal";
      readonly failures: readonly TerminalContractFailure[];
    })
  | (RunResultBase & {
      readonly kind: "service";
      readonly failures: readonly ServiceContractFailure[];
    })
  | (RunResultBase & {
      readonly kind: "artifact";
      readonly failures: readonly ArtifactContractFailure[];
    })
  | (RunResultBase & {
      readonly kind: "cleanup";
      readonly failures: readonly CleanupContractFailure[];
    })
  | (RunResultBase & { readonly kind: "media"; readonly failures: readonly MediaContractFailure[] })
  | (RunResultBase & {
      readonly kind: "distributed";
      readonly failures: readonly DistributedContractFailure[];
    });

/** Cross-kind failure surface for callers that iterate failures without
 * switching on `kind` (e.g. rendering a flat error report). Every probe
 * failure has at minimum `kind` and `message`. */
export interface ContractProbeFailure {
  readonly kind: string;
  readonly message: string;
}

export interface ContractProbeSuiteResult {
  readonly passed: boolean;
  readonly results: readonly ContractProbeRunResult[];
}

// --- Runner -----------------------------------------------------------------

export function runContractProbeSuite(suite: ContractProbeSuite): ContractProbeSuiteResult {
  const results: ContractProbeRunResult[] = [];

  for (const invocation of suite.probes) {
    // Dispatch is exhaustive over ContractProbeKind; the discriminated union
    // ensures that adding a new probe kind without updating this switch is a
    // compile-time error. Each branch preserves the probe's typed failure
    // shape -- no cross-kind narrowing happens here -- so callers iterating
    // `result.results` recover the full typed surface by switching on
    // `result.kind`.
    switch (invocation.kind) {
      case "directory": {
        const r = probeDirectoryContract(invocation.inputs);
        results.push({
          kind: "directory",
          label: invocation.label,
          passed: r.passed,
          failures: r.failures,
        });
        break;
      }
      case "terminal": {
        const r = probeTerminalContract(invocation.inputs);
        results.push({
          kind: "terminal",
          label: invocation.label,
          passed: r.passed,
          failures: r.failures,
        });
        break;
      }
      case "service": {
        const r = probeServiceContract(invocation.inputs);
        results.push({
          kind: "service",
          label: invocation.label,
          passed: r.passed,
          failures: r.failures,
        });
        break;
      }
      case "artifact": {
        const r = probeArtifactContract(invocation.inputs);
        results.push({
          kind: "artifact",
          label: invocation.label,
          passed: r.passed,
          failures: r.failures,
        });
        break;
      }
      case "cleanup": {
        const r = probeCleanupContract(invocation.inputs);
        results.push({
          kind: "cleanup",
          label: invocation.label,
          passed: r.passed,
          failures: r.failures,
        });
        break;
      }
      case "media": {
        const r = probeMediaContract(invocation.inputs);
        results.push({
          kind: "media",
          label: invocation.label,
          passed: r.passed,
          failures: r.failures,
        });
        break;
      }
      case "distributed": {
        const r = probeDistributedContract(invocation.inputs);
        results.push({
          kind: "distributed",
          label: invocation.label,
          passed: r.passed,
          failures: r.failures,
        });
        break;
      }
    }
  }

  return {
    passed: results.every((r) => r.passed),
    results,
  };
}

export function loadContractProbeSuite(path: string): ContractProbeSuite {
  const raw = readFileSync(path, "utf-8");
  const parsed: unknown = JSON.parse(raw);
  return ContractProbeSuiteSchema.parse(parsed);
}
