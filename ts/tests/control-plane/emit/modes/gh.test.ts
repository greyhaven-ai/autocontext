import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  writeFileSync,
  mkdirSync,
  readFileSync,
  chmodSync,
  existsSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { execFileSync } from "node:child_process";
import { runGhMode } from "../../../../src/control-plane/emit/modes/gh.js";
import type { Patch } from "../../../../src/control-plane/contract/types.js";
import type { ArtifactId } from "../../../../src/control-plane/contract/branded-ids.js";

let tmp: string;
let shimDir: string;
let shimLogPath: string;

function gitEnv(cwd: string, extraPath?: string): NodeJS.ProcessEnv {
  const basePath = extraPath ? `${extraPath}${delimiter}${process.env.PATH ?? ""}` : process.env.PATH;
  return {
    ...process.env,
    HOME: cwd,
    GIT_CONFIG_GLOBAL: join(cwd, ".gitconfig-test"),
    GIT_CONFIG_SYSTEM: "/dev/null",
    GIT_AUTHOR_NAME: "Test Author",
    GIT_AUTHOR_EMAIL: "test@example.com",
    GIT_COMMITTER_NAME: "Test Author",
    GIT_COMMITTER_EMAIL: "test@example.com",
    PATH: basePath,
  };
}

function initRepo(cwd: string): void {
  const env = gitEnv(cwd);
  writeFileSync(join(cwd, ".gitconfig-test"), "[init]\n  defaultBranch = main\n");
  execFileSync("git", ["init", "-b", "main"], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["config", "user.email", "test@example.com"], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["config", "user.name", "Test Author"], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["remote", "add", "origin", "/dev/null"], { cwd, env, stdio: "ignore" });
  writeFileSync(join(cwd, "README.md"), "# test\n");
  execFileSync("git", ["add", "."], { cwd, env, stdio: "ignore" });
  execFileSync("git", ["commit", "-m", "init"], { cwd, env, stdio: "ignore" });
}

function installShim(name: string, scriptBody: string): void {
  const p = join(shimDir, name);
  const script = `#!/usr/bin/env bash\n${scriptBody}\n`;
  writeFileSync(p, script, "utf-8");
  chmodSync(p, 0o755);
}

/** Bash function appended to every shim that writes a JSON array of the args
 *  to $LOG with a trailing newline. No node dependency; uses printf + a tiny
 *  awk/python-free escape. */
const SHIM_LOG_HELPER = `
log_args() {
  local j="["
  local first=1
  for a in "$@"; do
    local esc="\${a//\\\\/\\\\\\\\}"
    esc="\${esc//\\"/\\\\\\"}"
    if [ $first -eq 1 ]; then
      j="$j\\"$esc\\""
      first=0
    else
      j="$j,\\"$esc\\""
    fi
  done
  j="$j]"
  printf '%s\\n' "$j" >> "$LOG"
}
`;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-gh-mode-"));
  shimDir = mkdtempSync(join(tmpdir(), "autocontext-gh-shim-"));
  shimLogPath = join(shimDir, "invocations.jsonl");
  initRepo(tmp);

  // Install a `gh` shim that records every invocation to a JSONL file and
  // prints a fake PR URL on `gh pr create`.
  installShim(
    "gh",
    `set -e
LOG="${shimLogPath}"
${SHIM_LOG_HELPER}
log_args "$@"
case "$1" in
  auth)
    echo "logged in"
    exit 0
    ;;
  pr)
    shift
    case "$1" in
      create)
        echo "https://github.com/example/repo/pull/7"
        exit 0
        ;;
    esac
    ;;
esac
exit 0
`,
  );

  // Install a `git` shim that intercepts `push` only — other verbs delegate
  // to the real git binary so the test can still create branches and commit.
  installShim(
    "git",
    `set -e
LOG="${shimLogPath}"
${SHIM_LOG_HELPER}
if [ "$1" = "push" ]; then
  log_args "$@"
  echo "pushed (shim)"
  exit 0
fi
REAL_GIT=""
for candidate in /usr/bin/git /usr/local/bin/git /opt/homebrew/bin/git; do
  if [ -x "$candidate" ]; then
    REAL_GIT="$candidate"
    break
  fi
done
if [ -z "$REAL_GIT" ]; then
  echo "git shim: no real git found" >&2
  exit 127
fi
exec "$REAL_GIT" "$@"
`,
  );
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
  rmSync(shimDir, { recursive: true, force: true });
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

describe("runGhMode", () => {
  test("invokes `gh pr create` with --title + --body-file and captures the PR URL from stdout", async () => {
    const env = gitEnv(tmp, shimDir);
    const patches: Patch[] = [
      mkPatch("agents/grid_ctf/prompts/new.txt", "hello prompt\n"),
    ];
    const branchName = "autocontext/grid_ctf/prompt-patch/01HZCAND";

    const result = await runGhMode({
      cwd: tmp,
      branchName,
      baseBranch: "main",
      patches,
      prBody: "## body\n",
      prTitle: "Autocontext: promote prompt-patch",
      candidateId,
      decisionBand: "STRONG",
      env,
    });

    expect(result.prUrl).toBe("https://github.com/example/repo/pull/7");
    expect(result.branchName).toBe(branchName);

    expect(existsSync(shimLogPath)).toBe(true);
    const log = readFileSync(shimLogPath, "utf-8").trim().split("\n").map((l) => JSON.parse(l) as string[]);
    const ghPrCreate = log.find((args) => args[0] === "pr" && args[1] === "create");
    expect(ghPrCreate).toBeDefined();
    const titleIdx = ghPrCreate!.indexOf("--title");
    expect(titleIdx).toBeGreaterThanOrEqual(0);
    expect(ghPrCreate![titleIdx + 1]).toBe("Autocontext: promote prompt-patch");
    const bodyFileIdx = ghPrCreate!.indexOf("--body-file");
    expect(bodyFileIdx).toBeGreaterThanOrEqual(0);
    expect(ghPrCreate![bodyFileIdx + 1]).toBe(
      join(tmp, ".autocontext", "emit-pr", candidateId, "pr-body.md"),
    );
    expect(ghPrCreate).toContain("--base");
    expect(ghPrCreate).toContain("main");
    expect(ghPrCreate).toContain("--head");
    expect(ghPrCreate).toContain(branchName);
  });

  test("pushes the branch to origin before invoking gh pr create", async () => {
    const env = gitEnv(tmp, shimDir);
    const branchName = "autocontext/grid_ctf/prompt-patch/01HZCAND";
    await runGhMode({
      cwd: tmp,
      branchName,
      baseBranch: "main",
      patches: [mkPatch("agents/grid_ctf/prompts/p.txt", "x\n")],
      prBody: "b\n",
      prTitle: "t",
      candidateId,
      decisionBand: "MODERATE",
      env,
    });
    const log = readFileSync(shimLogPath, "utf-8").trim().split("\n").map((l) => JSON.parse(l) as string[]);
    const pushIdx = log.findIndex((args) => args[0] === "push");
    const prCreateIdx = log.findIndex((args) => args[0] === "pr" && args[1] === "create");
    expect(pushIdx).toBeGreaterThanOrEqual(0);
    expect(prCreateIdx).toBeGreaterThanOrEqual(0);
    expect(pushIdx).toBeLessThan(prCreateIdx);
  });
});

void mkdirSync; // keep import to satisfy ts when unused
