// git mode — create a branch, apply patches into the working tree, and commit.
// Does NOT push; the caller prints the push command + the pre-rendered PR body path.
//
// Isolation note (§10.5): tests drive an isolated `GIT_CONFIG_GLOBAL` +
// `GIT_CONFIG_SYSTEM=/dev/null` via the `env` argument so real user config
// cannot leak into the test tree.

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { execFileSync } from "node:child_process";
import type { ArtifactId } from "../../contract/branded-ids.js";
import type { Patch } from "../../contract/types.js";

export interface GitModeInputs {
  readonly cwd: string;
  readonly branchName: string;
  readonly baseBranch: string;
  readonly patches: readonly Patch[];
  readonly prBody: string;
  readonly candidateId: ArtifactId;
  /** Used in the commit message: "autocontext: promote <id> (<decisionBand>)". */
  readonly decisionBand: string;
  /** Environment passed to `git` subprocesses; callers pass isolated git-config env vars here. */
  readonly env?: NodeJS.ProcessEnv;
}

export interface GitModeResult {
  readonly branchName: string;
  /** Absolute path to the pre-rendered PR body on disk. */
  readonly prBodyPath: string;
}

/**
 * Create the branch, apply patches, commit. No push.
 */
export async function runGitMode(inputs: GitModeInputs): Promise<GitModeResult> {
  const { cwd, branchName, patches, candidateId, prBody, decisionBand } = inputs;
  const env = inputs.env ?? process.env;

  // Create + check out the new branch from the base branch.
  execFileSync("git", ["checkout", "-b", branchName, inputs.baseBranch], {
    cwd,
    env,
    stdio: "ignore",
  });

  // Apply patches by writing afterContent to the working-tree path. The
  // unifiedDiff is for PR rendering only — we don't re-parse it here.
  for (const p of patches) {
    const abs = join(cwd, p.filePath);
    if (p.operation === "delete") {
      // Safe delete via git rm; ignore failure if the file is already absent.
      try {
        execFileSync("git", ["rm", "-f", p.filePath], { cwd, env, stdio: "ignore" });
      } catch {
        // tolerate absent files
      }
    } else {
      const content = p.afterContent ?? "";
      mkdirSync(dirname(abs), { recursive: true });
      writeFileSync(abs, content, "utf-8");
    }
  }

  // Stage everything the patches touched.
  execFileSync("git", ["add", "-A"], { cwd, env, stdio: "ignore" });

  // Commit with the spec-mandated message shape.
  const message = `autocontext: promote ${candidateId} (${decisionBand})`;
  execFileSync("git", ["commit", "-m", message], { cwd, env, stdio: "ignore" });

  // Persist the PR body to a stable location the caller (and human operator)
  // can reference for `gh pr create --body-file ...`.
  const prBodyPath = join(cwd, ".autocontext", "emit-pr", candidateId, "pr-body.md");
  mkdirSync(dirname(prBodyPath), { recursive: true });
  writeFileSync(prBodyPath, prBody, "utf-8");

  return { branchName, prBodyPath };
}
