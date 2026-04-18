import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { runGitMode } from "../../../../src/control-plane/emit/modes/git.js";
import type { Patch } from "../../../../src/control-plane/contract/types.js";
import type { ArtifactId } from "../../../../src/control-plane/contract/branded-ids.js";

let tmp: string;

function isolatedEnv(cwd: string): NodeJS.ProcessEnv {
  return {
    ...process.env,
    HOME: cwd,
    GIT_CONFIG_GLOBAL: join(cwd, ".gitconfig-test"),
    GIT_CONFIG_SYSTEM: "/dev/null",
    GIT_AUTHOR_NAME: "Test Author",
    GIT_AUTHOR_EMAIL: "test@example.com",
    GIT_COMMITTER_NAME: "Test Author",
    GIT_COMMITTER_EMAIL: "test@example.com",
  };
}

function initRepo(cwd: string): void {
  const env = isolatedEnv(cwd);
  writeFileSync(join(cwd, ".gitconfig-test"), "[init]\n  defaultBranch = main\n");
  execFileSync("git", ["init", "-b", "main"], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["config", "user.email", "test@example.com"], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["config", "user.name", "Test Author"], { cwd, env, stdio: "ignore" });
  writeFileSync(join(cwd, "README.md"), "# test\n");
  execFileSync("git", ["add", "."], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["commit", "-m", "init"], { cwd, env, stdio: "ignore" });
}

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-git-mode-"));
  initRepo(tmp);
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

const candidateId = "01HZCANDIDATE00000000AAAAA" as ArtifactId;

function mkPatch(relPath: string, content: string): Patch {
  return {
    filePath: relPath,
    operation: "create",
    unifiedDiff: `--- a/${relPath}\n+++ b/${relPath}\n@@ @@\n+${content}\n`,
    afterContent: content,
  };
}

describe("runGitMode", () => {
  test("creates the branch, writes patches into the working tree, and commits", async () => {
    const env = isolatedEnv(tmp);
    const patches: Patch[] = [
      mkPatch("agents/grid_ctf/prompts/new.txt", "hello prompt\n"),
    ];
    const branchName = "autocontext/grid_ctf/prompt-patch/01HZCAND";

    const result = await runGitMode({
      cwd: tmp,
      branchName,
      baseBranch: "main",
      patches,
      prBody: "body\n",
      candidateId,
      decisionBand: "STRONG",
      env,
    });
    expect(result.branchName).toBe(branchName);
    expect(result.prBodyPath).toBe(join(tmp, ".autocontext", "emit-pr", candidateId, "pr-body.md"));

    // Branch was created.
    const branches = execFileSync("git", ["branch"], { cwd: tmp, env, encoding: "utf-8" });
    expect(branches).toContain(branchName);

    // Working-tree file exists with the patch content.
    const newFile = join(tmp, "agents/grid_ctf/prompts/new.txt");
    const { readFileSync, existsSync } = await import("node:fs");
    expect(existsSync(newFile)).toBe(true);
    expect(readFileSync(newFile, "utf-8")).toBe("hello prompt\n");

    // Commit message matches `autocontext: promote <id> (<decisionBand>)`.
    const lastMsg = execFileSync("git", ["log", "-1", "--pretty=%s"], { cwd: tmp, env, encoding: "utf-8" }).trim();
    expect(lastMsg).toBe(`autocontext: promote ${candidateId} (STRONG)`);
  });

  test("does NOT push — leaves branch local only", async () => {
    const env = isolatedEnv(tmp);
    const branchName = "autocontext/grid_ctf/prompt-patch/01HZCAND";
    await runGitMode({
      cwd: tmp,
      branchName,
      baseBranch: "main",
      patches: [mkPatch("agents/grid_ctf/prompts/new.txt", "a\n")],
      prBody: "b\n",
      candidateId,
      decisionBand: "MODERATE",
      env,
    });
    // No remote configured — so there's nothing to verify against; the check is
    // that runGitMode did NOT throw (push would fail in the absence of a remote).
    // Verify no remote was added.
    const remotes = execFileSync("git", ["remote"], { cwd: tmp, env, encoding: "utf-8" }).trim();
    expect(remotes).toBe("");
  });

  test("writes the PR body to <cwd>/.autocontext/emit-pr/<candidateId>/pr-body.md", async () => {
    const env = isolatedEnv(tmp);
    const prBody = "## Autocontext candidate promotion\n\nBody line\n";
    const result = await runGitMode({
      cwd: tmp,
      branchName: "autocontext/grid_ctf/prompt-patch/01HZCAND",
      baseBranch: "main",
      patches: [mkPatch("agents/grid_ctf/prompts/p.txt", "x\n")],
      prBody,
      candidateId,
      decisionBand: "STRONG",
      env,
    });
    mkdirSync; // satisfy ts

    const { readFileSync, existsSync } = await import("node:fs");
    expect(existsSync(result.prBodyPath)).toBe(true);
    expect(readFileSync(result.prBodyPath, "utf-8")).toBe(prBody);
  });
});
