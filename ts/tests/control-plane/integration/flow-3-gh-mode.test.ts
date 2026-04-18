// Flow 3 (spec §10.3) — same as Flow 2, but in gh mode.
//
// Setup:
//   - `git init` an isolated repo (GIT_CONFIG_GLOBAL/SYSTEM as in Flow 2).
//   - Add a fake `origin` remote so the gh-mode push has somewhere to "go".
//   - Install a PATH-prepended `gh` shim that records every invocation to a
//     JSONL file and prints a stub PR URL on `gh pr create`. The companion
//     `git` shim only intercepts `git push` (delegating other verbs to the
//     real binary) so branch creation + commit still work.
//
// Assertions:
//   - `gh pr create` was invoked with --title, --body-file, --base flags
//   - The returned EmitResult.location.kind === "pr-url"
//   - location.value matches the shim's stub URL
//   - The shim log records exactly one `gh pr create` invocation

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  writeFileSync,
  readFileSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";
import { emitPr } from "../../../src/control-plane/emit/index.js";
import {
  buildArtifactWithPassingEval,
  openTestRegistry,
} from "./_helpers/fixtures.js";
import { installGhShim, type GhShim } from "./_helpers/gh-shim.js";

let tmp: string;
let shim: GhShim;

function gitInit(cwd: string, env: NodeJS.ProcessEnv): void {
  writeFileSync(join(cwd, ".gitconfig-test"), "[init]\n  defaultBranch = main\n");
  execFileSync("git", ["init", "-b", "main"], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["config", "user.email", "test@example.com"], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["config", "user.name", "Test Author"], { cwd, env, stdio: "ignore" });
  // Fake remote so `git push -u origin <branch>` has something to point at;
  // the git shim intercepts push so the path is never read.
  execFileSync("git", ["remote", "add", "origin", "/dev/null"], { cwd, env, stdio: "ignore" });
  // Ignore registry scratch state.
  writeFileSync(
    join(cwd, ".gitignore"),
    [".autocontext/", "payload-*/", "*.tmp", ""].join("\n"),
    "utf-8",
  );
  writeFileSync(join(cwd, "README.md"), "# integration\n");
  execFileSync("git", ["add", "."], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["commit", "-m", "init"], { cwd, env, stdio: "ignore" });
}

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-int-flow3-"));
  shim = installGhShim({ prUrl: "https://github.com/example/repo/pull/123" });
  gitInit(tmp, shim.env(tmp));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
  shim.cleanup();
});

describe("Flow 3 — gh mode end-to-end", () => {
  test(
    "register → eval → promote → emit-pr gh: invokes `gh pr create` with the right flags and returns the PR URL",
    async () => {
      const env = shim.env(tmp);
      const registry = openTestRegistry(tmp);

      const built = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "prompt-patch",
        runId: "run_flow3",
      });
      const candidateId = built.artifact.id;

      const apply = await runControlPlaneCommand(
        [
          "promotion",
          "apply",
          candidateId,
          "--to",
          "canary",
          "--reason",
          "passing-eval-flow3",
        ],
        { cwd: tmp, now: () => "2026-04-17T12:32:00.000Z" },
      );
      expect(apply.exitCode).toBe(0);

      const result = await emitPr(registry, candidateId, {
        mode: "gh",
        baseline: null,
        baseBranch: "main",
        timestamp: "2026-04-17T12:33:00.000Z",
        autocontextVersion: "0.0.0-test",
        prTitle: "Autocontext: promote prompt-patch (flow-3)",
        env,
      });

      // 1. Returned PR URL == shim's stub.
      expect(result.location.kind).toBe("pr-url");
      expect(result.location.value).toBe(shim.prUrl);

      // 2. The shim recorded exactly one `gh pr create` invocation with the
      //    correct flag values.
      expect(existsSync(shim.logPath)).toBe(true);
      const lines = readFileSync(shim.logPath, "utf-8").trim().split("\n");
      const entries = lines.map((l) => JSON.parse(l) as string[]);
      const ghPrCreates = entries.filter((args) => args[0] === "pr" && args[1] === "create");
      expect(ghPrCreates).toHaveLength(1);
      const ghCmd = ghPrCreates[0]!;

      // --title <prTitle>
      const titleIdx = ghCmd.indexOf("--title");
      expect(titleIdx).toBeGreaterThanOrEqual(0);
      expect(ghCmd[titleIdx + 1]).toBe("Autocontext: promote prompt-patch (flow-3)");

      // --body-file <path-to-real-file>
      const bodyFileIdx = ghCmd.indexOf("--body-file");
      expect(bodyFileIdx).toBeGreaterThanOrEqual(0);
      const bodyFilePath = ghCmd[bodyFileIdx + 1]!;
      expect(existsSync(bodyFilePath)).toBe(true);
      const renderedBody = readFileSync(bodyFilePath, "utf-8");
      expect(renderedBody).toContain("### Metric deltas");

      // --base main
      const baseIdx = ghCmd.indexOf("--base");
      expect(baseIdx).toBeGreaterThanOrEqual(0);
      expect(ghCmd[baseIdx + 1]).toBe("main");

      // --head <branch>
      const headIdx = ghCmd.indexOf("--head");
      expect(headIdx).toBeGreaterThanOrEqual(0);
      expect(ghCmd[headIdx + 1]).toBe(result.branchName);

      // 3. Push happened exactly once and BEFORE pr create.
      const pushes = entries.filter((args) => args[0] === "push");
      expect(pushes).toHaveLength(1);
      const pushIdx = entries.findIndex((args) => args[0] === "push");
      const prCreateIdx = entries.findIndex(
        (args) => args[0] === "pr" && args[1] === "create",
      );
      expect(pushIdx).toBeLessThan(prCreateIdx);
    },
  );
});
