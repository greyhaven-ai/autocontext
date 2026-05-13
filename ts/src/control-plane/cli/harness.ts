import { existsSync, readFileSync } from "node:fs";
import type {
  Artifact,
  EvalRun,
  HarnessExpectedImpact,
  HarnessValidationEvidence,
  Patch,
} from "../contract/types.js";
import {
  parseArtifactId,
  parseHarnessProposalId,
  parseSuiteId,
  type SuiteId,
} from "../contract/branded-ids.js";
import { createHarnessChangeProposal } from "../contract/factories.js";
import {
  isHarnessChangeSurface,
  isHarnessValidationMode,
  withHarnessChangeDecision,
} from "../contract/harness-change-proposal.js";
import { validateHarnessChangeProposal, validatePatch } from "../contract/validators.js";
import { openRegistry } from "../registry/index.js";
import { defaultThresholds, decideHarnessChangeProposal } from "../promotion/index.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import type { CliContext, CliResult } from "./types.js";

export const HARNESS_HELP_TEXT = `autoctx harness — evidence-gated harness/context proposals

Subcommands:
  proposal create    Create a HarnessChangeProposal from findings and patches
  proposal list      List harness proposals
  proposal show      Show a harness proposal
  proposal decide    Gate a proposal against baseline-vs-candidate validation evidence

Examples:
  autoctx harness proposal create --finding finding-1 --surface prompt \\
      --summary "tighten prompt" --patches ./patches.json \\
      --rollback "revert prompt patch" --output json
  autoctx harness proposal decide <proposalId> --candidate <artifactId> \\
      --baseline <artifactId>|auto|none --validation heldout --suite prod-heldout
`;

export async function runHarness(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const sub = args[0];
  if (!sub || sub === "--help" || sub === "-h") {
    return { stdout: HARNESS_HELP_TEXT, stderr: "", exitCode: 0 };
  }
  if (sub !== "proposal") {
    return {
      stdout: "",
      stderr: `Unknown harness subcommand: ${sub}\n${HARNESS_HELP_TEXT}`,
      exitCode: EXIT.HARD_FAIL,
    };
  }
  return runProposal(args.slice(1), ctx);
}

async function runProposal(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const sub = args[0];
  switch (sub) {
    case "create":
      return runCreate(args.slice(1), ctx);
    case "list":
      return runList(args.slice(1), ctx);
    case "show":
      return runShow(args.slice(1), ctx);
    case "decide":
      return runDecide(args.slice(1), ctx);
    default:
      return {
        stdout: "",
        stderr: `Unknown harness proposal subcommand: ${String(sub)}\n${HARNESS_HELP_TEXT}`,
        exitCode: EXIT.HARD_FAIL,
      };
  }
}

async function runCreate(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const flags = parseFlags(args, {
    finding: { type: "string-array", required: true },
    surface: { type: "string", required: true },
    summary: { type: "string", required: true },
    patches: { type: "string", required: true },
    "expected-impact": { type: "string" },
    rollback: { type: "string-array", required: true },
    author: { type: "string" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  const value = flags.value;

  const surface = value.surface;
  if (!isHarnessChangeSurface(surface)) {
    return { stdout: "", stderr: `Invalid harness surface: ${surface}`, exitCode: EXIT.HARD_FAIL };
  }
  const output = readOutputMode(value.output);
  if ("error" in output) return { stdout: "", stderr: output.error, exitCode: EXIT.HARD_FAIL };

  const patchesResult = readPatches(ctx.resolve(value.patches));
  if ("error" in patchesResult) return { stdout: "", stderr: patchesResult.error, exitCode: EXIT.VALIDATION_FAILED };

  const expectedImpactResult = readExpectedImpact(
    value["expected-impact"] === undefined ? undefined : ctx.resolve(value["expected-impact"]),
  );
  if ("error" in expectedImpactResult) {
    return { stdout: "", stderr: expectedImpactResult.error, exitCode: EXIT.VALIDATION_FAILED };
  }

  const proposal = createHarnessChangeProposal({
    findingIds: value.finding,
    targetSurface: surface,
    proposedEdit: {
      summary: value.summary,
      patches: patchesResult.value,
    },
    expectedImpact: expectedImpactResult.value,
    rollbackCriteria: value.rollback,
    provenance: {
      authorType: value.author !== undefined ? "human" : "autocontext-run",
      authorId: value.author ?? "cli",
      parentArtifactIds: [],
      createdAt: ctx.now(),
    },
  });
  const validation = validateHarnessChangeProposal(proposal);
  if (!validation.valid) {
    return {
      stdout: "",
      stderr: `invalid HarnessChangeProposal: ${validation.errors.join("; ")}`,
      exitCode: EXIT.VALIDATION_FAILED,
    };
  }

  const registry = openRegistry(ctx.cwd);
  try {
    registry.saveHarnessChangeProposal(proposal);
  } catch (err) {
    return { stdout: "", stderr: err instanceof Error ? err.message : String(err), exitCode: EXIT.IO_ERROR };
  }

  return {
    stdout: formatOutput(proposal, output.value),
    stderr: "",
    exitCode: EXIT.PASS_STRONG_OR_MODERATE,
  };
}

async function runList(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const flags = parseFlags(args, { output: { type: "string", default: "pretty" } });
  if ("error" in flags) return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  const output = readOutputMode(flags.value.output);
  if ("error" in output) return { stdout: "", stderr: output.error, exitCode: EXIT.HARD_FAIL };
  const registry = openRegistry(ctx.cwd);
  const rows = registry.listHarnessChangeProposals().map((proposal) => ({
    id: proposal.id,
    targetSurface: proposal.targetSurface,
    status: proposal.status,
    findings: proposal.findingIds.length,
    patches: proposal.proposedEdit.patches.length,
    decisionReason: proposal.decision?.reason,
  }));
  return {
    stdout: formatOutput(rows, output.value),
    stderr: "",
    exitCode: EXIT.PASS_STRONG_OR_MODERATE,
  };
}

async function runShow(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx harness proposal show <proposalId>", exitCode: EXIT.HARD_FAIL };
  }
  const proposalId = parseHarnessProposalId(id);
  if (proposalId === null) {
    return { stdout: "", stderr: `Invalid proposal id: ${id}`, exitCode: EXIT.HARD_FAIL };
  }
  const flags = parseFlags(args.slice(1), { output: { type: "string", default: "pretty" } });
  if ("error" in flags) return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  const output = readOutputMode(flags.value.output);
  if ("error" in output) return { stdout: "", stderr: output.error, exitCode: EXIT.HARD_FAIL };
  const registry = openRegistry(ctx.cwd);
  try {
    return {
      stdout: formatOutput(registry.loadHarnessChangeProposal(proposalId), output.value),
      stderr: "",
      exitCode: EXIT.PASS_STRONG_OR_MODERATE,
    };
  } catch (err) {
    return { stdout: "", stderr: err instanceof Error ? err.message : String(err), exitCode: EXIT.INVALID_ARTIFACT };
  }
}

async function runDecide(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx harness proposal decide <proposalId> --candidate <artifactId>", exitCode: EXIT.HARD_FAIL };
  }
  const proposalId = parseHarnessProposalId(id);
  if (proposalId === null) {
    return { stdout: "", stderr: `Invalid proposal id: ${id}`, exitCode: EXIT.HARD_FAIL };
  }

  const flags = parseFlags(args.slice(1), {
    candidate: { type: "string", required: true },
    baseline: { type: "string", default: "auto" },
    validation: { type: "string", required: true },
    suite: { type: "string", required: true },
    "evidence-ref": { type: "string-array" },
    output: { type: "string", default: "pretty" },
  });
  if ("error" in flags) return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  const value = flags.value;
  const output = readOutputMode(value.output);
  if ("error" in output) return { stdout: "", stderr: output.error, exitCode: EXIT.HARD_FAIL };
  const candidateId = parseArtifactId(value.candidate);
  if (candidateId === null) {
    return { stdout: "", stderr: `Invalid candidate id: ${value.candidate}`, exitCode: EXIT.INVALID_ARTIFACT };
  }
  const mode = value.validation;
  if (!isHarnessValidationMode(mode)) {
    return { stdout: "", stderr: `Invalid validation mode: ${mode}`, exitCode: EXIT.HARD_FAIL };
  }
  const suiteId = parseSuiteId(value.suite);
  if (suiteId === null) {
    return { stdout: "", stderr: `Invalid suite: ${value.suite}`, exitCode: EXIT.HARD_FAIL };
  }

  const registry = openRegistry(ctx.cwd);
  try {
    const proposal = registry.loadHarnessChangeProposal(proposalId);
    const candidateArtifact = registry.loadArtifact(candidateId);
    const candidateEvalRun = latestEvalRunForSuite(registry, candidateArtifact, suiteId);
    if (candidateEvalRun === null) {
      return {
        stdout: "",
        stderr: `Candidate ${candidateId} has no EvalRuns for suite ${suiteId}`,
        exitCode: EXIT.MISSING_BASELINE,
      };
    }
    const baseline = resolveBaseline(registry, candidateArtifact, value.baseline, suiteId);
    const validation: HarnessValidationEvidence = {
      mode,
      suiteId,
      evidenceRefs: value["evidence-ref"] ?? [],
    };
    const decision = decideHarnessChangeProposal({
      proposal,
      candidate: { artifact: candidateArtifact, evalRun: candidateEvalRun },
      baseline,
      thresholds: defaultThresholds(),
      validation,
      decidedAt: ctx.now(),
    });
    const updated = withHarnessChangeDecision(proposal, decision);
    registry.updateHarnessChangeProposal(updated);
    return {
      stdout: formatOutput(updated, output.value),
      stderr: "",
      exitCode: exitCodeFromHarnessDecision(decision.status),
    };
  } catch (err) {
    return { stdout: "", stderr: err instanceof Error ? err.message : String(err), exitCode: EXIT.INVALID_ARTIFACT };
  }
}

function latestEvalRunForSuite(
  registry: ReturnType<typeof openRegistry>,
  artifact: Artifact,
  suiteId: SuiteId,
): EvalRun | null {
  const ref = artifact.evalRuns
    .slice()
    .reverse()
    .find((run) => run.suiteId === suiteId);
  return ref === undefined ? null : registry.loadEvalRun(artifact.id, ref.evalRunId);
}

function resolveBaseline(
  registry: ReturnType<typeof openRegistry>,
  candidateArtifact: Artifact,
  baselineFlag: string,
  suiteId: SuiteId,
): { artifact: Artifact; evalRun: EvalRun } | null {
  if (baselineFlag === "none") return null;
  const artifact = baselineFlag === "auto"
    ? registry.getActive(candidateArtifact.scenario, candidateArtifact.actuatorType, candidateArtifact.environmentTag)
    : (() => {
        const id = parseArtifactId(baselineFlag);
        return id === null ? null : registry.loadArtifact(id);
      })();
  if (artifact === null) return null;
  const evalRun = latestEvalRunForSuite(registry, artifact, suiteId);
  return evalRun === null ? null : { artifact, evalRun };
}

function exitCodeFromHarnessDecision(status: "accepted" | "rejected" | "inconclusive"): number {
  if (status === "accepted") return EXIT.PASS_STRONG_OR_MODERATE;
  if (status === "inconclusive") return EXIT.MARGINAL;
  return EXIT.HARD_FAIL;
}

function readPatches(path: string): { value: readonly Patch[] } | { error: string } {
  if (!existsSync(path)) return { error: `patches file not found: ${path}` };
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, "utf-8"));
  } catch (err) {
    return { error: `patches JSON: ${err instanceof Error ? err.message : String(err)}` };
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    return { error: "patches file must contain a non-empty JSON array" };
  }
  const patches: Patch[] = [];
  for (const item of parsed) {
    const validation = validatePatch(item);
    if (!validation.valid) {
      return { error: `invalid patch: ${validation.errors.join("; ")}` };
    }
    patches.push(item as Patch);
  }
  return { value: patches };
}

function readExpectedImpact(
  path: string | undefined,
): { value: HarnessExpectedImpact } | { error: string } {
  if (path === undefined) return { value: {} };
  if (!existsSync(path)) return { error: `expected-impact file not found: ${path}` };
  try {
    return { value: JSON.parse(readFileSync(path, "utf-8")) as HarnessExpectedImpact };
  } catch (err) {
    return { error: `expected-impact JSON: ${err instanceof Error ? err.message : String(err)}` };
  }
}

function readOutputMode(value: string): { value: OutputMode } | { error: string } {
  if (value === "json" || value === "table" || value === "pretty") {
    return { value };
  }
  return { error: `Invalid output mode: ${value}` };
}

interface FlagSpec {
  readonly type: "string" | "string-array";
  readonly required?: boolean;
  readonly default?: string;
}

type FlagMap = Record<string, FlagSpec>;
type FlagValue<T extends FlagSpec> = T["type"] extends "string-array" ? string[] : string;
type ParsedFlags<T extends FlagMap> = {
  [K in keyof T]: T[K]["required"] extends true
    ? FlagValue<T[K]>
    : T[K]["default"] extends string
      ? FlagValue<T[K]>
      : FlagValue<T[K]> | undefined;
};

function parseFlags<const T extends FlagMap>(
  args: readonly string[],
  spec: T,
): { value: ParsedFlags<T> } | { error: string };
function parseFlags<const T extends FlagMap>(
  args: readonly string[],
  spec: T,
): { value: ParsedFlags<T> } | { error: string } {
  const parsed: Partial<Record<keyof T, string | string[]>> = {};
  for (let i = 0; i < args.length; i++) {
    const arg = args[i]!;
    if (!arg.startsWith("--")) continue;
    const name = arg.slice(2);
    if (!hasFlag(spec, name)) return { error: `Unknown flag: --${name}` };
    const next = args[i + 1];
    if (next === undefined || next.startsWith("--")) return { error: `Flag --${name} requires a value` };
    if (spec[name].type === "string-array") {
      const prior = parsed[name];
      parsed[name] = [...(Array.isArray(prior) ? prior : []), next];
    } else {
      parsed[name] = next;
    }
    i += 1;
  }
  for (const key in spec) {
    const flagSpec = spec[key];
    if (parsed[key] === undefined) {
      if (flagSpec.default !== undefined) parsed[key] = flagSpec.default;
      if (flagSpec.required && parsed[key] === undefined) return { error: `Missing required flag: --${key}` };
    }
  }
  return { value: parsed as ParsedFlags<T> };
}

function hasFlag<T extends FlagMap>(spec: T, name: string): name is Extract<keyof T, string> {
  return Object.prototype.hasOwnProperty.call(spec, name);
}
