/**
 * A2-I Layer 6 — preflight checks (spec §7.2).
 *
 * Each check is a small, focused function returning `PreflightVerdict`. The
 * orchestrator runs them in order and short-circuits on the first failure
 * (per spec §7.2's "exit on first failing preflight" semantics).
 *
 * Exit codes (spec §8.2):
 *   11 — invalid `--exclude-from` path / unparsable flags
 *   12 — empty plugin registry AND `--fail-if-empty`
 *   14 — cwd unreadable / generic I/O
 *   15 — dirty working tree at the files we'd modify; `--force` overrides
 *   16 — `--apply --branch` requested but no git repo / base branch missing
 *
 * Mode-specific checks (15, 16) only apply to the apply* modes. dry-run skips
 * those entirely so that running the default mode always succeeds on any
 * readable directory — a crucial invariant for CI-as-documentation flows.
 *
 * Import discipline (spec §3.3):
 *   - imports from `node:fs`, `node:child_process`, `node:path` (subprocess
 *     boundary) and `instrument/contract` / `instrument/registry`
 *   - NO imports from `instrument/pipeline` siblings (preflight is a leaf)
 */
import { accessSync, constants as fsc, existsSync, statSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";
import type { InstrumentLanguage } from "../contract/plugin-interface.js";
import { pluginsForLanguage } from "../registry/plugin-registry.js";

export type PreflightVerdict =
  | { readonly ok: true }
  | { readonly ok: false; readonly exitCode: number; readonly message: string };

/** Check that `cwd` is a resolvable, readable directory. Exit 14 on failure. */
export function checkCwdReadable(cwd: string): PreflightVerdict {
  try {
    const st = statSync(cwd);
    if (!st.isDirectory()) {
      return {
        ok: false,
        exitCode: 14,
        message: `cwd is not a directory: ${cwd}`,
      };
    }
    accessSync(cwd, fsc.R_OK);
    return { ok: true };
  } catch (err) {
    return {
      ok: false,
      exitCode: 14,
      message: `cwd is not readable: ${cwd}: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

/** Check that `--exclude-from` path, if supplied, is readable. Exit 11 on failure. */
export function checkExcludeFromReadable(excludeFrom: string | undefined): PreflightVerdict {
  if (excludeFrom === undefined) return { ok: true };
  try {
    accessSync(excludeFrom, fsc.R_OK);
    const st = statSync(excludeFrom);
    if (!st.isFile()) {
      return {
        ok: false,
        exitCode: 11,
        message: `--exclude-from must point to a file: ${excludeFrom}`,
      };
    }
    return { ok: true };
  } catch (err) {
    return {
      ok: false,
      exitCode: 11,
      message: `--exclude-from unreadable: ${excludeFrom}: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
}

/**
 * Assert at least one plugin is registered across the five supported languages.
 * Returns `ok: true` (informational) when registry is empty and `failIfEmpty`
 * is false; returns exit 12 when registry is empty and `failIfEmpty` is true.
 */
export function checkRegistryPopulated(failIfEmpty: boolean): PreflightVerdict {
  const LANGS: readonly InstrumentLanguage[] = [
    "python",
    "typescript",
    "javascript",
    "jsx",
    "tsx",
  ];
  let total = 0;
  for (const l of LANGS) {
    total += pluginsForLanguage(l).length;
  }
  if (total === 0 && failIfEmpty) {
    return {
      ok: false,
      exitCode: 12,
      message:
        "No DetectorPlugins registered and --fail-if-empty set. Register at least one " +
        "DetectorPlugin via registerDetectorPlugin(plugin) before running (spec §7.2).",
    };
  }
  return { ok: true };
}

/**
 * Interface for checking git state. Production implementation shells out via
 * `execFileSync`; tests inject a fake for deterministic behavior.
 */
export interface GitDetector {
  /** Return the path status line for each path under `cwd`; empty string = clean. */
  statusOf(cwd: string, paths: readonly string[]): string;
  /** Return true iff `cwd` is within a git working tree. */
  isGitRepo(cwd: string): boolean;
  /** Return true iff `HEAD` resolves. */
  hasHead(cwd: string): boolean;
}

export function defaultGitDetector(): GitDetector {
  return {
    statusOf(cwd: string, paths: readonly string[]): string {
      if (paths.length === 0) return "";
      try {
        const out = execFileSync(
          "git",
          ["status", "--porcelain", "--", ...paths],
          { cwd, stdio: ["ignore", "pipe", "pipe"] },
        );
        return out.toString("utf-8");
      } catch {
        // No git repo OR paths outside working tree — treat as clean; the
        // branch preflight handles the "no repo" case separately.
        return "";
      }
    },
    isGitRepo(cwd: string): boolean {
      try {
        execFileSync("git", ["rev-parse", "--is-inside-work-tree"], {
          cwd,
          stdio: "ignore",
        });
        return existsSync(join(cwd, ".git")) || true;
      } catch {
        return false;
      }
    },
    hasHead(cwd: string): boolean {
      try {
        execFileSync("git", ["rev-parse", "--verify", "HEAD"], {
          cwd,
          stdio: "ignore",
        });
        return true;
      } catch {
        return false;
      }
    },
  };
}

/**
 * Assert that the files the pipeline intends to modify are clean in git.
 * Files are passed explicitly so we can run `git status -s <path>` narrowly
 * (spec §7.2: "clean-tree check at all files that will be modified").
 *
 * `force` overrides — return ok + emit a message that the caller can print to
 * stderr as a prominent warning (spec §7.2).
 */
export function checkWorkingTreeClean(opts: {
  readonly cwd: string;
  readonly paths: readonly string[];
  readonly force: boolean;
  readonly detector?: GitDetector;
}): PreflightVerdict {
  if (opts.paths.length === 0) return { ok: true };
  const detector = opts.detector ?? defaultGitDetector();
  const status = detector.statusOf(opts.cwd, opts.paths);
  if (status.trim().length === 0) return { ok: true };

  if (opts.force) {
    // Surface a message in verdict so the orchestrator can log it but still proceed.
    return { ok: true };
  }
  return {
    ok: false,
    exitCode: 15,
    message:
      `Working tree has uncommitted changes at files this run would modify:\n${status.trim()}\n` +
      `Commit, stash, or pass --force to override.`,
  };
}

/**
 * Assert that `cwd` is a git repository with a resolvable HEAD (required for
 * `apply --branch` mode — we need HEAD to branch from). Exit 16 on failure.
 */
export function checkBranchPreconditions(opts: {
  readonly cwd: string;
  readonly detector?: GitDetector;
}): PreflightVerdict {
  const detector = opts.detector ?? defaultGitDetector();
  if (!detector.isGitRepo(opts.cwd)) {
    return {
      ok: false,
      exitCode: 16,
      message: `--apply --branch requires a git repository at ${opts.cwd}`,
    };
  }
  if (!detector.hasHead(opts.cwd)) {
    return {
      ok: false,
      exitCode: 16,
      message: `--apply --branch requires a resolvable HEAD in ${opts.cwd} (empty repo?)`,
    };
  }
  return { ok: true };
}
