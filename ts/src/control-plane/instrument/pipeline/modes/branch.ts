/**
 * A2-I Layer 6 — apply-branch mode (spec §7.5).
 *
 * Composition of apply mode + git branch + commit. No push (the customer
 * pushes manually or via a follow-up `gh pr create --body-file ...`).
 *
 * Steps:
 *   1. `git checkout -b <branchName>`  (branches from current HEAD)
 *   2. Apply patches — reuses `runApplyMode` internals
 *   3. `git add -A -- <affected paths>`
 *   4. `git commit -m <commitMessage>`
 *   5. Write `apply-log.json` with branch name + commit SHA
 *
 * Git shim: we extend the `GitDetector` used by preflight with an `execGit`
 * surface so tests can inject a fake (same pattern Foundation B's emit-pr gh
 * mode uses). In production we shell out via `execFileSync` — the surface is
 * small enough that a single `BranchGitExecutor` interface covers it.
 */
import { execFileSync } from "node:child_process";
import { writeApplyLog, runApplyMode } from "./apply.js";
import type { GitDetector } from "../preflight.js";

export interface BranchModeInputs {
  readonly cwd: string;
  readonly sessionDir: string;
  readonly patches: readonly { readonly filePath: string; readonly afterContent: string }[];
  readonly branchName: string;
  readonly commitMessage: string;
  readonly sessionUlid: string;
  readonly nowIso: string;
  readonly detector?: GitDetector;
  /** Advanced: git command executor for test injection. */
  readonly executor?: BranchGitExecutor;
  /** Optional environment for git subprocesses (isolated GIT_CONFIG_*). */
  readonly env?: NodeJS.ProcessEnv;
}

export interface BranchModeResult {
  readonly filesWritten: readonly string[];
  readonly branchName: string;
  readonly commitSha?: string;
}

/**
 * Abstraction over the git command surface. Tests pass a fake; production
 * gets a real subprocess executor via `defaultBranchGitExecutor()`.
 *
 * Separate from `GitDetector` so test fakes can implement the "read" surface
 * (detector) without wiring the "write" surface (executor) — most branch-mode
 * tests only care about the git invocations, not about branch preconditions.
 */
export interface BranchGitExecutor {
  checkoutNewBranch(args: { cwd: string; branch: string; env?: NodeJS.ProcessEnv }): void;
  addAll(args: { cwd: string; paths: readonly string[]; env?: NodeJS.ProcessEnv }): void;
  commit(args: { cwd: string; message: string; env?: NodeJS.ProcessEnv }): void;
  headSha(args: { cwd: string; env?: NodeJS.ProcessEnv }): string | undefined;
}

export function defaultBranchGitExecutor(): BranchGitExecutor {
  return {
    checkoutNewBranch(args) {
      execFileSync("git", ["checkout", "-b", args.branch], {
        cwd: args.cwd,
        stdio: "ignore",
        ...(args.env !== undefined ? { env: args.env } : {}),
      });
    },
    addAll(args) {
      if (args.paths.length === 0) return;
      execFileSync("git", ["add", "-A", "--", ...args.paths], {
        cwd: args.cwd,
        stdio: "ignore",
        ...(args.env !== undefined ? { env: args.env } : {}),
      });
    },
    commit(args) {
      execFileSync("git", ["commit", "-m", args.message], {
        cwd: args.cwd,
        stdio: "ignore",
        ...(args.env !== undefined ? { env: args.env } : {}),
      });
    },
    headSha(args) {
      try {
        const out = execFileSync("git", ["rev-parse", "HEAD"], {
          cwd: args.cwd,
          stdio: ["ignore", "pipe", "ignore"],
          ...(args.env !== undefined ? { env: args.env } : {}),
        });
        return out.toString("utf-8").trim();
      } catch {
        return undefined;
      }
    },
  };
}

export function runBranchMode(inputs: BranchModeInputs): BranchModeResult {
  const executor = inputs.executor ?? defaultBranchGitExecutor();

  // 1. Branch off current HEAD.
  executor.checkoutNewBranch({
    cwd: inputs.cwd,
    branch: inputs.branchName,
    ...(inputs.env !== undefined ? { env: inputs.env } : {}),
  });

  // 2. Apply patches. Reuse apply mode's writer logic (DRY) — but since
  //    `runApplyMode` itself writes apply-log.json, we call it directly and
  //    then overwrite the log with branch metadata included.
  const applyResult = runApplyMode({
    cwd: inputs.cwd,
    sessionDir: inputs.sessionDir,
    patches: inputs.patches,
    sessionUlid: inputs.sessionUlid,
    nowIso: inputs.nowIso,
  });

  // 3. Stage + commit - skip entirely when nothing was written (avoids empty commits).
  let commitSha: string | undefined = undefined;
  if (applyResult.filesWritten.length > 0) {
    executor.addAll({
      cwd: inputs.cwd,
      paths: [...applyResult.filesWritten],
      ...(inputs.env !== undefined ? { env: inputs.env } : {}),
    });
    executor.commit({
      cwd: inputs.cwd,
      message: inputs.commitMessage,
      ...(inputs.env !== undefined ? { env: inputs.env } : {}),
    });
    commitSha = executor.headSha({
      cwd: inputs.cwd,
      ...(inputs.env !== undefined ? { env: inputs.env } : {}),
    });
  }

  // 4. Re-write apply-log with branch metadata.
  writeApplyLog({
    sessionDir: inputs.sessionDir,
    sessionUlid: inputs.sessionUlid,
    nowIso: inputs.nowIso,
    filesWritten: applyResult.filesWritten,
    mode: "apply-branch",
    branchName: inputs.branchName,
    ...(commitSha !== undefined ? { commitSha } : {}),
  });

  return {
    filesWritten: applyResult.filesWritten,
    branchName: inputs.branchName,
    ...(commitSha !== undefined ? { commitSha } : {}),
  };
}
