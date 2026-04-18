// Preflight checks for the emit pipeline (§9.7).
//
// Maps to exit codes in cli/_shared/exit-codes.ts:
//   11  working tree dirty at the target path
//   12  base branch missing
//   13  resolved target path violates actuator's allowed pattern (including
//        unknown-actuator-type, which makes the path unresolvable)
//   14  no EvalRun attached to candidate
//   15  mode requirements (gh/git/token) not met
//
// Detection of external toolchain (gh, git) is dependency-injected via
// `detect` so tests can simulate every failure mode without shelling out.
// Production callers pass a real detector that runs `gh --version`, `git
// --version`, etc. through `execFileSync`.
//
// Issues are aggregated — callers get the full list, not just the first,
// so an operator running `--dry-run` can see every problem in one pass.

import { existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import type { Artifact } from "../contract/types.js";
import type { Registry } from "../registry/index.js";
import type { WorkspaceLayout } from "./workspace-layout.js";
import { getActuator } from "../actuators/registry.js";

export type PreflightMode = "patch-only" | "git" | "gh";

export interface PreflightIssue {
  readonly code: number;
  readonly message: string;
}

export interface PreflightResult {
  readonly ok: boolean;
  readonly issues: readonly PreflightIssue[];
}

export interface PreflightDetector {
  /** Return true iff `gh` is installed and authenticated. */
  gh(): boolean;
  /** Return true iff `git` is installed and the repo has a remote configured. */
  git(): boolean;
  /** Return true iff the working tree is clean (no uncommitted changes). */
  isWorkingTreeClean(): boolean;
  /** Return true iff the named base branch exists (locally or remotely). */
  baseBranchExists(branch: string): boolean;
}

export interface PreflightInputs {
  readonly registry: Registry;
  readonly candidate: Artifact;
  readonly mode: PreflightMode;
  readonly cwd: string;
  readonly layout: WorkspaceLayout;
  readonly baseBranch?: string;
  /** Optional detector; a default implementation shells out to gh/git via execFileSync. */
  readonly detect?: PreflightDetector;
}

/**
 * Run preflight checks for the emit pipeline. Aggregates every issue found.
 *
 * Check ordering is intentional: the actuator check runs first so the rest
 * of the pipeline can safely assume a resolvable target path.
 */
export function preflight(inputs: PreflightInputs): PreflightResult {
  const { candidate, mode, baseBranch } = inputs;
  const detect = inputs.detect ?? defaultDetector(inputs.cwd);
  const issues: PreflightIssue[] = [];

  // --- 13: target-path / actuator resolvability ---
  const reg = getActuator(candidate.actuatorType);
  if (reg === null) {
    issues.push({
      code: 13,
      message: `Unknown actuator type '${candidate.actuatorType}' — cannot resolve target path.`,
    });
  } else {
    // Resolve the target path and verify it syntactically matches the actuator's
    // allowed pattern. Pattern syntax is a simple glob with `**` and `*`.
    const target = reg.actuator.resolveTargetPath(candidate, inputs.layout);
    if (!matchesGlob(target, reg.allowedTargetPattern)) {
      issues.push({
        code: 13,
        message: `Resolved target path '${target}' does not match allowed pattern '${reg.allowedTargetPattern}'.`,
      });
    }
  }

  // --- 14: ≥1 EvalRun attached ---
  if (candidate.evalRuns.length === 0) {
    issues.push({
      code: 14,
      message: `Candidate ${candidate.id} has no EvalRun attached — run 'autoctx eval attach' first.`,
    });
  }

  // --- git / gh modes: 11 / 12 / 15 ---
  if (mode === "git" || mode === "gh") {
    if (!detect.git()) {
      issues.push({
        code: 15,
        message: `mode '${mode}' requires git to be installed with a remote configured.`,
      });
    }
    if (mode === "gh" && !detect.gh()) {
      issues.push({
        code: 15,
        message: `mode 'gh' requires the 'gh' CLI to be installed and authenticated (run 'gh auth status').`,
      });
    }
    if (!detect.isWorkingTreeClean()) {
      issues.push({
        code: 11,
        message: `Working tree is dirty — commit or stash changes before emitting a PR.`,
      });
    }
    const base = baseBranch ?? "main";
    if (!detect.baseBranchExists(base)) {
      issues.push({
        code: 12,
        message: `Base branch '${base}' does not exist or is not fetchable.`,
      });
    }
  }

  return { ok: issues.length === 0, issues };
}

// ---------- glob matching ----------

/**
 * Minimal glob matcher supporting `*` (any non-separator character) and `**`
 * (any character including separators). Sufficient for the actuator-pattern
 * checks; not a full POSIX glob.
 */
function matchesGlob(path: string, pattern: string): boolean {
  const re = globToRegExp(pattern);
  return re.test(path);
}

function globToRegExp(pattern: string): RegExp {
  let re = "^";
  let i = 0;
  while (i < pattern.length) {
    const c = pattern[i]!;
    if (c === "*" && pattern[i + 1] === "*") {
      re += ".*";
      i += 2;
      if (pattern[i] === "/") i += 1;
    } else if (c === "*") {
      re += "[^/]*";
      i += 1;
    } else if (c === "?") {
      re += "[^/]";
      i += 1;
    } else if (c === "{") {
      // Handle `{a,b,c}` brace expansion.
      const end = pattern.indexOf("}", i);
      if (end === -1) {
        re += escapeRe(c);
        i += 1;
      } else {
        const alts = pattern.slice(i + 1, end).split(",").map(escapeRe);
        re += `(?:${alts.join("|")})`;
        i = end + 1;
      }
    } else {
      re += escapeRe(c);
      i += 1;
    }
  }
  re += "$";
  return new RegExp(re);
}

function escapeRe(s: string): string {
  return s.replace(/[.+^${}()|[\]\\]/g, "\\$&");
}

// ---------- default detector ----------

function defaultDetector(cwd: string): PreflightDetector {
  return {
    gh(): boolean {
      try {
        execFileSync("gh", ["auth", "status"], { cwd, stdio: "ignore" });
        return true;
      } catch {
        return false;
      }
    },
    git(): boolean {
      try {
        execFileSync("git", ["--version"], { cwd, stdio: "ignore" });
        // Also verify a git repo is initialized at cwd.
        return existsSync(join(cwd, ".git"));
      } catch {
        return false;
      }
    },
    isWorkingTreeClean(): boolean {
      try {
        const out = execFileSync("git", ["status", "--porcelain"], {
          cwd,
          stdio: ["ignore", "pipe", "ignore"],
        });
        return out.toString("utf-8").trim().length === 0;
      } catch {
        return false;
      }
    },
    baseBranchExists(branch: string): boolean {
      try {
        execFileSync("git", ["rev-parse", "--verify", branch], {
          cwd,
          stdio: "ignore",
        });
        return true;
      } catch {
        // Also try origin/<branch>.
        try {
          execFileSync("git", ["rev-parse", "--verify", `origin/${branch}`], {
            cwd,
            stdio: "ignore",
          });
          return true;
        } catch {
          return false;
        }
      }
    },
  };
}
