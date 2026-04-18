// gh mode — wraps git mode + pushes + invokes `gh pr create`.
//
// The gh CLI resolves the remote/owner/repo from the local git config; this
// module only passes `--base`, `--head`, `--title`, and `--body-file`. The
// returned PR URL is the first non-empty stdout line from `gh pr create`.
//
// Tests drive a PATH-shimmed `gh` binary (see `tests/.../modes/gh.test.ts`)
// that records every invocation to a JSONL file for assertions.

import { execFileSync } from "node:child_process";
import type { ArtifactId } from "../../contract/branded-ids.js";
import type { Patch } from "../../contract/types.js";
import { runGitMode } from "./git.js";

export interface GhModeInputs {
  readonly cwd: string;
  readonly branchName: string;
  readonly baseBranch: string;
  readonly patches: readonly Patch[];
  readonly prBody: string;
  readonly prTitle: string;
  readonly candidateId: ArtifactId;
  readonly decisionBand: string;
  readonly env?: NodeJS.ProcessEnv;
}

export interface GhModeResult {
  readonly branchName: string;
  readonly prUrl: string;
  readonly prBodyPath: string;
}

/**
 * Run git mode (create branch, apply, commit), then push the branch and invoke
 * `gh pr create` with the pre-rendered PR body.
 */
export async function runGhMode(inputs: GhModeInputs): Promise<GhModeResult> {
  const env = inputs.env ?? process.env;

  // 1. git mode: create branch, apply patches, commit. Persists the PR body.
  const gitResult = await runGitMode({
    cwd: inputs.cwd,
    branchName: inputs.branchName,
    baseBranch: inputs.baseBranch,
    patches: inputs.patches,
    prBody: inputs.prBody,
    candidateId: inputs.candidateId,
    decisionBand: inputs.decisionBand,
    env,
  });

  // 2. Push the branch. Uses `-u origin <branch>` to set upstream tracking.
  execFileSync("git", ["push", "-u", "origin", inputs.branchName], {
    cwd: inputs.cwd,
    env,
    stdio: "ignore",
  });

  // 3. Invoke `gh pr create`. --body-file is preferred over --body to avoid
  // argv-quoting snafus for multi-line markdown.
  const out = execFileSync(
    "gh",
    [
      "pr",
      "create",
      "--base", inputs.baseBranch,
      "--head", inputs.branchName,
      "--title", inputs.prTitle,
      "--body-file", gitResult.prBodyPath,
    ],
    { cwd: inputs.cwd, env, encoding: "utf-8" },
  );

  const prUrl = firstNonEmptyLine(out).trim();

  return {
    branchName: inputs.branchName,
    prUrl,
    prBodyPath: gitResult.prBodyPath,
  };
}

function firstNonEmptyLine(s: string): string {
  for (const line of s.split("\n")) {
    if (line.trim().length > 0) return line;
  }
  return "";
}
