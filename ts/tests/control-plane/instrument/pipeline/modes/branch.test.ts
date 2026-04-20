/**
 * A2-I Layer 6 - apply-branch mode unit tests (spec §7.5).
 *
 * Uses an injected `BranchGitExecutor` fake (same pattern Foundation B's emit-pr
 * gh-shim uses) to drive + assert the git command sequence deterministically.
 */
import { describe, test, expect, beforeEach } from "vitest";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  runBranchMode,
  type BranchGitExecutor,
} from "../../../../../src/control-plane/instrument/pipeline/modes/branch.js";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-branch-"));
  scratches.push(d);
  return d;
}

beforeEach(() => {
  while (scratches.length > 0) {
    const d = scratches.pop()!;
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      // ignore
    }
  }
});

function recordingExecutor(): { calls: string[]; executor: BranchGitExecutor } {
  const calls: string[] = [];
  const executor: BranchGitExecutor = {
    checkoutNewBranch({ branch }) {
      calls.push(`checkout -b ${branch}`);
    },
    addAll({ paths }) {
      calls.push(`add ${paths.join(",")}`);
    },
    commit({ message }) {
      calls.push(`commit ${message}`);
    },
    headSha() {
      return "deadbeef1234";
    },
  };
  return { calls, executor };
}

describe("runBranchMode - git sequence", () => {
  test("checkout -> apply -> add -> commit, in order", () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(join(cwd, "src", "main.py"), "before\n", "utf-8");
    const sessionDir = join(cwd, "sess");
    mkdirSync(sessionDir, { recursive: true });
    const { calls, executor } = recordingExecutor();

    const res = runBranchMode({
      cwd,
      sessionDir,
      patches: [{ filePath: "src/main.py", afterContent: "after\n" }],
      branchName: "autocontext-instrument-2026",
      commitMessage: "Instrument LLM clients",
      sessionUlid: "01HN00",
      nowIso: "2026-04-17T12:00:00.000Z",
      executor,
    });

    expect(calls[0]).toBe("checkout -b autocontext-instrument-2026");
    expect(calls[1]).toBe("add src/main.py");
    expect(calls[2]).toBe("commit Instrument LLM clients");
    expect(res.branchName).toBe("autocontext-instrument-2026");
    expect(res.commitSha).toBe("deadbeef1234");
    expect(res.filesWritten).toEqual(["src/main.py"]);
    // Verify working tree was actually modified.
    expect(readFileSync(join(cwd, "src", "main.py"), "utf-8")).toBe("after\n");
    // apply-log.json has branchName + commitSha.
    const log = JSON.parse(readFileSync(join(sessionDir, "apply-log.json"), "utf-8"));
    expect(log).toMatchObject({
      mode: "apply-branch",
      branchName: "autocontext-instrument-2026",
      commitSha: "deadbeef1234",
    });
  });

  test("skips commit when no files were written (avoids empty commits)", () => {
    const cwd = scratch();
    const sessionDir = join(cwd, "sess");
    mkdirSync(sessionDir, { recursive: true });
    const { calls, executor } = recordingExecutor();
    const res = runBranchMode({
      cwd,
      sessionDir,
      patches: [],
      branchName: "empty-branch",
      commitMessage: "nothing",
      sessionUlid: "01HN00",
      nowIso: "2026-04-17T12:00:00.000Z",
      executor,
    });
    // Only checkout should be invoked. add + commit are skipped.
    expect(calls).toEqual(["checkout -b empty-branch"]);
    expect(res.commitSha).toBeUndefined();
    expect(res.filesWritten).toEqual([]);
  });
});
