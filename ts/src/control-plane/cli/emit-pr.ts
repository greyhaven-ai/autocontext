// `autoctx emit-pr <candidateId> ...` top-level command.
//
// Produces a PR (or dry-run bundle) that promotes a candidate artifact from
// the registry into the repo's working tree. Modes: auto | gh | git | patch-only.

import { parseArtifactId } from "../contract/branded-ids.js";
import { openRegistry } from "../registry/index.js";
import { emitPr, EmitPreflightError, type EmitMode } from "../emit/index.js";
import { EXIT } from "./_shared/exit-codes.js";
import { formatOutput, type OutputMode } from "./_shared/output-formatters.js";
import type { CliContext, CliResult } from "./types.js";

export const EMIT_PR_HELP_TEXT = `autoctx emit-pr — generate a promotion PR (or dry-run bundle) for a candidate

Usage:
  autoctx emit-pr <candidateId> [--base main] [--branch <name>] [--title <str>] \\
                                [--dry-run] [--mode auto|gh|git|patch-only] \\
                                [--baseline <id|auto|none>] [--output json|pretty]

Flags:
  --mode      auto | gh | git | patch-only (default: auto)
  --dry-run   alias for --mode patch-only
  --base      git base branch (default: main)
  --branch    override auto-generated branch name
  --title     override auto-generated PR title
  --baseline  explicit baseline artifact id, "auto", or "none"
  --output    json | pretty (default: pretty)

Exit codes:
  0   success (PR opened / branch created / patches written)
  11  working tree dirty
  12  base branch missing
  13  resolved target path violates actuator pattern
  14  candidate has no EvalRun attached
  15  mode requirements not met (gh/git/token)
  17  other I/O failure
`;

export async function runEmitPr(
  args: readonly string[],
  ctx: CliContext,
): Promise<CliResult> {
  if (args[0] === "--help" || args[0] === "-h" || args.length === 0) {
    return { stdout: EMIT_PR_HELP_TEXT, stderr: "", exitCode: 0 };
  }

  const id = args[0]!;
  if (id.startsWith("--")) {
    return { stdout: "", stderr: EMIT_PR_HELP_TEXT, exitCode: EXIT.HARD_FAIL };
  }
  const candidateId = parseArtifactId(id);
  if (candidateId === null) {
    return { stdout: "", stderr: `Invalid candidate id: ${id}`, exitCode: EXIT.INVALID_ARTIFACT };
  }

  const flags = parseFlags(args.slice(1));
  if ("error" in flags) {
    return { stdout: "", stderr: flags.error, exitCode: EXIT.HARD_FAIL };
  }

  const mode = (flags.value.mode ?? "auto") as EmitMode;
  const dryRun = flags.value["dry-run"] === "true";
  if (!["auto", "gh", "git", "patch-only"].includes(mode)) {
    return { stdout: "", stderr: `Invalid --mode: ${mode}`, exitCode: EXIT.HARD_FAIL };
  }

  const output = (flags.value.output ?? "pretty") as OutputMode;
  const version = process.env.npm_package_version ?? "0.0.0-dev";
  const timestamp = ctx.now();

  const registry = openRegistry(ctx.cwd);

  let baseline: Parameters<typeof emitPr>[2]["baseline"] = "auto";
  const bflag = flags.value.baseline;
  if (bflag === "none") baseline = null;
  else if (bflag === undefined || bflag === "auto") baseline = "auto";
  else {
    const parsed = parseArtifactId(bflag);
    if (parsed === null) {
      return { stdout: "", stderr: `Invalid --baseline artifact id: ${bflag}`, exitCode: EXIT.INVALID_ARTIFACT };
    }
    baseline = parsed;
  }

  try {
    const result = await emitPr(registry, candidateId, {
      mode,
      dryRun,
      baseline,
      ...(flags.value.base ? { baseBranch: flags.value.base } : {}),
      ...(flags.value.branch ? { branchName: flags.value.branch } : {}),
      ...(flags.value.title ? { prTitle: flags.value.title } : {}),
      timestamp,
      autocontextVersion: version,
    });
    const payload = {
      mode: result.mode,
      resolvedMode: result.resolvedMode,
      branchName: result.branchName,
      location: result.location,
      timestamp: result.timestamp,
      patches: result.patches.map((p) => ({ filePath: p.filePath, operation: p.operation })),
    };
    return {
      stdout: formatOutput(payload, output),
      stderr: "",
      exitCode: EXIT.PASS_STRONG_OR_MODERATE,
    };
  } catch (err) {
    if (err instanceof EmitPreflightError) {
      // Map the highest-priority preflight issue to an exit code. Order
      // matches spec §9.7 — the first listed code wins for tiebreaking.
      const priority = [11, 12, 13, 14, 15];
      let code: number = EXIT.HARD_FAIL;
      for (const p of priority) {
        if (err.issues.some((i) => i.code === p)) {
          code = p;
          break;
        }
      }
      return {
        stdout: "",
        stderr: err.issues.map((i) => `[${i.code}] ${i.message}`).join("\n"),
        exitCode: code,
      };
    }
    return {
      stdout: "",
      stderr: err instanceof Error ? err.message : String(err),
      exitCode: EXIT.IO_ERROR,
    };
  }
}

// ---- Flag parser ----

interface ParsedFlags {
  [key: string]: string | undefined;
}

type FlagsResult = { value: ParsedFlags } | { error: string };

const KNOWN = ["mode", "dry-run", "base", "branch", "title", "baseline", "output"];

function parseFlags(args: readonly string[]): FlagsResult {
  const parsed: ParsedFlags = {};
  for (let i = 0; i < args.length; i++) {
    const a = args[i]!;
    if (!a.startsWith("--")) continue;
    const name = a.slice(2);
    if (!KNOWN.includes(name)) return { error: `Unknown flag: --${name}` };
    if (name === "dry-run") {
      parsed[name] = "true";
      continue;
    }
    const next = args[i + 1];
    if (next === undefined || next.startsWith("--")) {
      return { error: `Flag --${name} requires a value` };
    }
    parsed[name] = next;
    i += 1;
  }
  return { value: parsed };
}
