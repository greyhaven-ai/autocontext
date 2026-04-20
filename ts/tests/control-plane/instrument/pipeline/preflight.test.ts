/**
 * A2-I Layer 6 - preflight unit tests (spec §7.2).
 *
 * Each preflight check tested in isolation:
 *   - checkCwdReadable      -> exit 14
 *   - checkExcludeFromReadable -> exit 11
 *   - checkRegistryPopulated -> exit 12 when empty + failIfEmpty
 *   - checkWorkingTreeClean -> exit 15 (overridable via --force)
 *   - checkBranchPreconditions -> exit 16
 */
import { describe, test, expect, beforeEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  checkCwdReadable,
  checkExcludeFromReadable,
  checkRegistryPopulated,
  checkWorkingTreeClean,
  checkBranchPreconditions,
  type GitDetector,
} from "../../../../src/control-plane/instrument/pipeline/preflight.js";
import {
  registerDetectorPlugin,
  resetRegistryForTests,
} from "../../../../src/control-plane/instrument/registry/plugin-registry.js";
import { mockOpenAiPythonPlugin } from "../../../_fixtures/plugins/mock-openai-python.js";

const scratchRoots: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-preflight-"));
  scratchRoots.push(d);
  return d;
}

beforeEach(() => {
  resetRegistryForTests();
  while (scratchRoots.length > 0) {
    const d = scratchRoots.pop()!;
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      // ignore
    }
  }
});

describe("checkCwdReadable", () => {
  test("accepts an existing directory", () => {
    const d = scratch();
    expect(checkCwdReadable(d)).toEqual({ ok: true });
  });

  test("rejects a nonexistent path with exit 14", () => {
    const result = checkCwdReadable(join(scratch(), "nope"));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.exitCode).toBe(14);
  });

  test("rejects a file (not a directory) with exit 14", () => {
    const d = scratch();
    const f = join(d, "file.txt");
    writeFileSync(f, "hello", "utf-8");
    const result = checkCwdReadable(f);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.exitCode).toBe(14);
  });
});

describe("checkExcludeFromReadable", () => {
  test("passes when excludeFrom is undefined", () => {
    expect(checkExcludeFromReadable(undefined)).toEqual({ ok: true });
  });

  test("passes when path is readable", () => {
    const d = scratch();
    const f = join(d, "excludes.txt");
    writeFileSync(f, "node_modules\n*.log\n", "utf-8");
    expect(checkExcludeFromReadable(f)).toEqual({ ok: true });
  });

  test("rejects unreadable path with exit 11", () => {
    const result = checkExcludeFromReadable(join(scratch(), "missing.txt"));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.exitCode).toBe(11);
  });

  test("rejects a directory (not a file) with exit 11", () => {
    const d = scratch();
    const sub = join(d, "sub");
    mkdirSync(sub);
    const result = checkExcludeFromReadable(sub);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.exitCode).toBe(11);
  });
});

describe("checkRegistryPopulated", () => {
  test("empty registry + failIfEmpty=false passes (informational)", () => {
    expect(checkRegistryPopulated(false)).toEqual({ ok: true });
  });

  test("empty registry + failIfEmpty=true fails with exit 12", () => {
    const result = checkRegistryPopulated(true);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.exitCode).toBe(12);
  });

  test("non-empty registry + failIfEmpty=true passes", () => {
    registerDetectorPlugin(mockOpenAiPythonPlugin);
    expect(checkRegistryPopulated(true)).toEqual({ ok: true });
  });
});

describe("checkWorkingTreeClean", () => {
  const fakeClean: GitDetector = {
    statusOf: () => "",
    isGitRepo: () => true,
    hasHead: () => true,
  };
  const fakeDirty: GitDetector = {
    statusOf: () => " M src/main.py\n",
    isGitRepo: () => true,
    hasHead: () => true,
  };

  test("returns ok when paths list is empty", () => {
    expect(
      checkWorkingTreeClean({ cwd: "/", paths: [], force: false, detector: fakeDirty }),
    ).toEqual({ ok: true });
  });

  test("returns ok when detector reports a clean tree", () => {
    expect(
      checkWorkingTreeClean({
        cwd: "/",
        paths: ["src/main.py"],
        force: false,
        detector: fakeClean,
      }),
    ).toEqual({ ok: true });
  });

  test("returns exit 15 when detector reports dirty and --force is off", () => {
    const result = checkWorkingTreeClean({
      cwd: "/",
      paths: ["src/main.py"],
      force: false,
      detector: fakeDirty,
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.exitCode).toBe(15);
      expect(result.message).toContain("src/main.py");
    }
  });

  test("returns ok when --force overrides dirty state", () => {
    expect(
      checkWorkingTreeClean({
        cwd: "/",
        paths: ["src/main.py"],
        force: true,
        detector: fakeDirty,
      }),
    ).toEqual({ ok: true });
  });
});

describe("checkBranchPreconditions", () => {
  test("rejects when not a git repo (exit 16)", () => {
    const result = checkBranchPreconditions({
      cwd: "/",
      detector: { statusOf: () => "", isGitRepo: () => false, hasHead: () => false },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.exitCode).toBe(16);
  });

  test("rejects when HEAD is unresolvable (exit 16)", () => {
    const result = checkBranchPreconditions({
      cwd: "/",
      detector: { statusOf: () => "", isGitRepo: () => true, hasHead: () => false },
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.exitCode).toBe(16);
  });

  test("passes when both git-repo and HEAD are resolvable", () => {
    expect(
      checkBranchPreconditions({
        cwd: "/",
        detector: { statusOf: () => "", isGitRepo: () => true, hasHead: () => true },
      }),
    ).toEqual({ ok: true });
  });
});
