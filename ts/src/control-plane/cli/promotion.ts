// `autoctx promotion ...` subcommand group.
//
// Responsibilities:
//   - decide: pure PromotionDecision computation (no state change).
//   - apply : transactional state change via registry.appendPromotionEvent.
//   - history: dump promotion-history.jsonl for an artifact.

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { ActivationState, Artifact, EvalRun, PromotionThresholds } from "../contract/types.js";
import { parseArtifactId } from "../contract/branded-ids.js";
import { createPromotionEvent } from "../contract/factories.js";
import { openRegistry } from "../registry/index.js";
import { artifactDirectory } from "../registry/artifact-store.js";
import { readHistory } from "../registry/history-store.js";
import { decidePromotion, defaultThresholds } from "../promotion/index.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import type { CliContext, CliResult } from "./types.js";

export const PROMOTION_HELP_TEXT = `autoctx promotion — promotion decisions and transitions

Subcommands:
  decide     Evaluate a candidate vs baseline and print a PromotionDecision
  apply      Transition an artifact to a new activation state
  history    Print promotion-history.jsonl for an artifact

Examples:
  autoctx promotion decide <candidateId> [--baseline <id>|auto] \\
      [--thresholds ./thresholds.json] [--output json]
  autoctx promotion apply <candidateId> --to <shadow|canary|active|disabled> \\
      --reason "..." [--dry-run]
  autoctx promotion history <artifactId>
`;

export async function runPromotion(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  const sub = args[0];
  if (!sub || sub === "--help" || sub === "-h") {
    return { stdout: PROMOTION_HELP_TEXT, stderr: "", exitCode: 0 };
  }
  switch (sub) {
    case "decide":
      return runDecide(args.slice(1), ctx);
    case "apply":
      return runApply(args.slice(1), ctx);
    case "history":
      return runHistory(args.slice(1), ctx);
    default:
      return { stdout: "", stderr: `Unknown promotion subcommand: ${sub}\n${PROMOTION_HELP_TEXT}`, exitCode: EXIT.HARD_FAIL };
  }
}

// ---- decide ----

async function runDecide(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx promotion decide <candidateId>", exitCode: EXIT.HARD_FAIL };
  }
  const candidateId = parseArtifactId(id);
  if (candidateId === null) {
    return { stdout: "", stderr: `Invalid candidate id: ${id}`, exitCode: EXIT.INVALID_ARTIFACT };
  }
  const flags = parseSimpleFlags(args.slice(1), ["baseline", "thresholds", "layout", "output"]);
  if ("error" in flags) return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };

  const registry = openRegistry(ctx.cwd);
  let candidateArt: Artifact;
  try {
    candidateArt = registry.loadArtifact(candidateId);
  } catch (err) {
    return { stdout: "", stderr: err instanceof Error ? err.message : String(err), exitCode: EXIT.INVALID_ARTIFACT };
  }
  if (candidateArt.evalRuns.length === 0) {
    return { stdout: "", stderr: `Candidate ${candidateId} has no EvalRuns to decide on`, exitCode: EXIT.MISSING_BASELINE };
  }
  // Use the latest EvalRun for the candidate.
  const candidateEvalRunRef = candidateArt.evalRuns[candidateArt.evalRuns.length - 1]!;
  let candidateEvalRun: EvalRun;
  try {
    candidateEvalRun = registry.loadEvalRun(candidateArt.id, candidateEvalRunRef.evalRunId);
  } catch (err) {
    return { stdout: "", stderr: err instanceof Error ? err.message : String(err), exitCode: EXIT.INVALID_ARTIFACT };
  }

  // Baseline resolution: --baseline <id|auto|none> default auto.
  let baseline: { artifact: Artifact; evalRun: EvalRun } | null = null;
  const baselineFlag = flags.value.baseline ?? "auto";
  if (baselineFlag !== "none") {
    const maybeBaselineArt =
      baselineFlag === "auto"
        ? registry.getActive(candidateArt.scenario, candidateArt.actuatorType, candidateArt.environmentTag)
        : (() => {
            const b = parseArtifactId(baselineFlag);
            if (b === null) return null;
            try {
              return registry.loadArtifact(b);
            } catch {
              return null;
            }
          })();
    if (maybeBaselineArt !== null && maybeBaselineArt.evalRuns.length > 0) {
      const ref = maybeBaselineArt.evalRuns[maybeBaselineArt.evalRuns.length - 1]!;
      try {
        const br = registry.loadEvalRun(maybeBaselineArt.id, ref.evalRunId);
        baseline = { artifact: maybeBaselineArt, evalRun: br };
      } catch {
        // baseline has no usable EvalRun; treat as no baseline.
      }
    }
  }

  // Thresholds.
  let thresholds: PromotionThresholds = defaultThresholds();
  if (flags.value.thresholds) {
    const p = ctx.resolve(flags.value.thresholds);
    if (!existsSync(p)) return { stdout: "", stderr: `thresholds file not found: ${p}`, exitCode: EXIT.IO_ERROR };
    try {
      const raw = JSON.parse(readFileSync(p, "utf-8"));
      thresholds = { ...thresholds, ...raw };
    } catch (err) {
      return { stdout: "", stderr: `thresholds JSON: ${err instanceof Error ? err.message : String(err)}`, exitCode: EXIT.HARD_FAIL };
    }
  }

  const decision = decidePromotion({
    candidate: { artifact: candidateArt, evalRun: candidateEvalRun },
    baseline,
    thresholds,
    evaluatedAt: ctx.now(),
  });

  // Exit code per spec §6.5: pass → 0 (strong or moderate); marginal → 2 (pass but shadow-only); hard fail → 1.
  const exitCode = exitCodeFromDecision(decision);
  const mode = (flags.value.output ?? "pretty") as OutputMode;
  return {
    stdout: formatOutput(decision, mode),
    stderr: "",
    exitCode,
  };
}

function exitCodeFromDecision(decision: {
  pass: boolean;
  recommendedTargetState: ActivationState;
}): number {
  if (!decision.pass) return EXIT.HARD_FAIL;
  // A passing decision that still recommends "shadow" is marginal — meaningful
  // for CI so a workflow can require strong/moderate (canary/active) before
  // merging.
  if (decision.recommendedTargetState === "shadow") return EXIT.MARGINAL;
  return EXIT.PASS_STRONG_OR_MODERATE;
}

// ---- apply ----

async function runApply(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx promotion apply <artifactId> --to <state> --reason \"...\" [--dry-run]", exitCode: EXIT.HARD_FAIL };
  }
  const artifactId = parseArtifactId(id);
  if (artifactId === null) {
    return { stdout: "", stderr: `Invalid artifact id: ${id}`, exitCode: EXIT.INVALID_ARTIFACT };
  }
  const flags = parseSimpleFlags(args.slice(1), ["to", "reason", "dry-run"]);
  if ("error" in flags) return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };

  const to = flags.value.to;
  const reason = flags.value.reason;
  const dryRun = args.includes("--dry-run");

  if (!to || !reason) {
    return { stdout: "", stderr: "--to and --reason are required", exitCode: EXIT.HARD_FAIL };
  }

  const registry = openRegistry(ctx.cwd);
  let current: Artifact;
  try {
    current = registry.loadArtifact(artifactId);
  } catch (err) {
    return { stdout: "", stderr: err instanceof Error ? err.message : String(err), exitCode: EXIT.INVALID_ARTIFACT };
  }

  const event = createPromotionEvent({
    from: current.activationState,
    to: to as ActivationState,
    reason,
    timestamp: ctx.now(),
  });

  if (dryRun) {
    return {
      stdout: `[dry-run] would transition ${artifactId}: ${current.activationState} → ${to}\n[dry-run] reason: ${reason}`,
      stderr: "",
      exitCode: EXIT.PASS_STRONG_OR_MODERATE,
    };
  }

  try {
    const updated = registry.appendPromotionEvent(artifactId, event);
    return {
      stdout: `${updated.id}: ${current.activationState} → ${updated.activationState}`,
      stderr: "",
      exitCode: EXIT.PASS_STRONG_OR_MODERATE,
    };
  } catch (err) {
    return { stdout: "", stderr: err instanceof Error ? err.message : String(err), exitCode: EXIT.HARD_FAIL };
  }
}

// ---- history ----

async function runHistory(args: readonly string[], ctx: CliContext): Promise<CliResult> {
  const id = args[0];
  if (!id || id.startsWith("--")) {
    return { stdout: "", stderr: "Usage: autoctx promotion history <artifactId>", exitCode: EXIT.HARD_FAIL };
  }
  const artifactId = parseArtifactId(id);
  if (artifactId === null) {
    return { stdout: "", stderr: `Invalid artifact id: ${id}`, exitCode: EXIT.INVALID_ARTIFACT };
  }
  const flags = parseSimpleFlags(args.slice(1), ["output"]);
  if ("error" in flags) return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  const mode = (flags.value.output ?? "pretty") as OutputMode;

  const dir = artifactDirectory(ctx.cwd, artifactId);
  const historyPath = join(dir, "promotion-history.jsonl");
  let history: ReturnType<typeof readHistory>;
  try {
    history = readHistory(historyPath);
  } catch (err) {
    return { stdout: "", stderr: err instanceof Error ? err.message : String(err), exitCode: EXIT.IO_ERROR };
  }
  return {
    stdout: formatOutput(history, mode),
    stderr: "",
    exitCode: EXIT.PASS_STRONG_OR_MODERATE,
  };
}

// ---- helpers ----

function parseSimpleFlags(
  args: readonly string[],
  known: readonly string[],
): { value: Record<string, string | undefined> } | { error: string } {
  const result: Record<string, string | undefined> = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i]!;
    if (!a.startsWith("--")) continue;
    const name = a.slice(2);
    if (!known.includes(name)) return { error: `Unknown flag: --${name}` };
    if (name === "dry-run") {
      result[name] = "true";
      continue;
    }
    const next = args[i + 1];
    if (next === undefined || next.startsWith("--")) return { error: `Flag --${name} requires a value` };
    result[name] = next;
    i += 1;
  }
  for (const k of known) {
    if (!(k in result)) result[k] = undefined;
  }
  return { value: result };
}
