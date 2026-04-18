// Flow 2 (spec §10.3) — same as Flow 1, but in git mode.
//
// Setup: `git init` an isolated repository (GIT_CONFIG_GLOBAL/SYSTEM) with a
// dummy baseline commit so git has a HEAD to branch from. The .gitignore
// excludes the registry's .autocontext/ scratch state and per-test payload-*
// scratch dirs so the working tree stays clean while the registry mutates
// state under .autocontext/. Then run the same candidate→eval→promote→emit-pr
// pipeline with mode: "git" and assert that
//   - a branch was created with the spec name
//   - exactly one commit landed on that branch
//   - the patch is present at the resolved target path in the working tree
//   - no push occurred (no remote configured — push would fail and we never
//     attempt it)

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  writeFileSync,
  existsSync,
  readFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";
import { emitPr } from "../../../src/control-plane/emit/index.js";
import { branchNameFor } from "../../../src/control-plane/emit/branch-namer.js";
import {
  buildArtifactWithPassingEval,
  openTestRegistry,
} from "./_helpers/fixtures.js";

let tmp: string;

function isolatedGitEnv(cwd: string): NodeJS.ProcessEnv {
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

function gitInit(cwd: string): void {
  const env = isolatedGitEnv(cwd);
  writeFileSync(join(cwd, ".gitconfig-test"), "[init]\n  defaultBranch = main\n");
  execFileSync("git", ["init", "-b", "main"], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["config", "user.email", "test@example.com"], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["config", "user.name", "Test Author"], { cwd, env, stdio: "ignore" });
  // Ignore registry state + scratch payload dirs so the working tree stays
  // clean while the registry mutates .autocontext/.
  writeFileSync(
    join(cwd, ".gitignore"),
    [".autocontext/", "payload-*/", "*.tmp", ""].join("\n"),
    "utf-8",
  );
  // Initial baseline commit so HEAD exists.
  writeFileSync(join(cwd, "README.md"), "# integration\n");
  execFileSync("git", ["add", "."], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["commit", "-m", "init"], { cwd, env, stdio: "ignore" });
}

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-int-flow2-"));
  gitInit(tmp);
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("Flow 2 — git mode end-to-end", () => {
  test(
    "register → attach passing eval → promote → emit-pr git creates a branch + one commit, no push",
    async () => {
      const env = isolatedGitEnv(tmp);
      const registry = openTestRegistry(tmp);

      // 1. Register + attach passing eval.
      const built = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "prompt-patch",
        runId: "run_flow2",
      });
      const candidateId = built.artifact.id;

      // 2. promotion apply --to canary via the in-process CLI.
      const apply = await runControlPlaneCommand(
        [
          "promotion",
          "apply",
          candidateId,
          "--to",
          "canary",
          "--reason",
          "passing-eval-flow2",
        ],
        { cwd: tmp, now: () => "2026-04-17T12:32:00.000Z" },
      );
      expect(apply.exitCode).toBe(0);

      // 3. Snapshot pre-emit git state for after-comparisons.
      const headBefore = execFileSync("git", ["rev-parse", "HEAD"], {
        cwd: tmp,
        env,
        encoding: "utf-8",
      }).trim();
      const branchesBefore = execFileSync("git", ["branch", "--list"], {
        cwd: tmp,
        env,
        encoding: "utf-8",
      });

      // 4. emit-pr git mode (programmatic call so we can pass `env` to the
      //    git subprocess directly, which the CLI shell-out path also forwards).
      const result = await emitPr(registry, candidateId, {
        mode: "git",
        baseline: null,
        baseBranch: "main",
        timestamp: "2026-04-17T12:33:00.000Z",
        autocontextVersion: "0.0.0-test",
        env,
      });

      // 5. Assertions.
      // 5a. Branch name matches branchNameFor() and is greppable.
      const expectedBranch = branchNameFor(built.artifact);
      expect(result.branchName).toBe(expectedBranch);
      expect(result.location.kind).toBe("branch");
      expect(result.location.value).toBe(expectedBranch);

      // 5b. Branch exists locally.
      const branchesAfter = execFileSync("git", ["branch", "--list"], {
        cwd: tmp,
        env,
        encoding: "utf-8",
      });
      expect(branchesAfter).toContain(expectedBranch);
      expect(branchesBefore).not.toContain(expectedBranch);

      // 5c. Exactly one commit on the new branch since divergence from main.
      const log = execFileSync(
        "git",
        ["log", "main.." + expectedBranch, "--pretty=%s"],
        { cwd: tmp, env, encoding: "utf-8" },
      ).trim();
      const commitMessages = log.length === 0 ? [] : log.split("\n");
      expect(commitMessages).toHaveLength(1);
      expect(commitMessages[0]!.startsWith(`autocontext: promote ${candidateId}`)).toBe(true);

      // 5d. The patch landed at the expected target path with the expected content.
      const expectedTarget = join(
        tmp,
        "agents",
        "grid_ctf",
        "prompts",
        `${candidateId}-prompt-patch.txt`,
      );
      expect(existsSync(expectedTarget)).toBe(true);
      expect(readFileSync(expectedTarget, "utf-8")).toBe("You are a helpful agent.\n");

      // 5e. main HEAD is unchanged (we are on a branch).
      expect(
        execFileSync("git", ["rev-parse", "main"], { cwd: tmp, env, encoding: "utf-8" }).trim(),
      ).toBe(headBefore);

      // 5f. No remote configured — confirms no push was attempted (push would
      //     have errored loudly otherwise; the test passing here is the assertion).
      const remotes = execFileSync("git", ["remote"], { cwd: tmp, env, encoding: "utf-8" }).trim();
      expect(remotes).toBe("");
    },
  );
});
