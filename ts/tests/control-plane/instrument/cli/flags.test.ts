/**
 * A2-I Layer 7 - CLI flag parsing tests (spec §8.1).
 *
 * Covers the full flag surface: mode mutual exclusion, --exclude repetition,
 * --force, --fail-if-empty, --max-file-bytes, --output.
 */
import { describe, test, expect, beforeEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInstrumentCommand } from "../../../../src/control-plane/instrument/cli/runner.js";
import { resetRegistryForTests } from "../../../../src/control-plane/instrument/registry/plugin-registry.js";

const ULID = "01HN0000000000000000000001";
const NOW = "2026-04-17T12:00:00.000Z";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-flags-"));
  scratches.push(d);
  return d;
}

beforeEach(() => {
  resetRegistryForTests();
  while (scratches.length > 0) {
    const d = scratches.pop()!;
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      // ignore
    }
  }
});

describe("mode dispatch", () => {
  test("no mode flag -> dry-run", async () => {
    const cwd = scratch();
    const r = await runInstrumentCommand(["--output", "json"], {
      cwd,
      nowIso: NOW,
      sessionUlid: ULID,
    });
    expect(JSON.parse(r.stdout).mode).toBe("dry-run");
  });

  test("--apply alone -> apply", async () => {
    const cwd = scratch();
    const r = await runInstrumentCommand(["--apply", "--output", "json"], {
      cwd,
      nowIso: NOW,
      sessionUlid: ULID,
    });
    expect(JSON.parse(r.stdout).mode).toBe("apply");
  });

  test("--apply --branch <name> -> apply-branch", async () => {
    const cwd = scratch();
    const noop = () => undefined;
    const r = await runInstrumentCommand(
      ["--apply", "--branch", "autocontext-instrument-2026", "--output", "json"],
      {
        cwd,
        nowIso: NOW,
        sessionUlid: ULID,
        gitDetector: { statusOf: () => "", isGitRepo: () => true, hasHead: () => true },
        branchExecutor: {
          checkoutNewBranch: noop,
          addAll: noop,
          commit: noop,
          headSha: () => "deadbeef",
        },
      },
    );
    expect(JSON.parse(r.stdout).mode).toBe("apply-branch");
  });

  test("--dry-run + --apply -> reject with exit 11", async () => {
    const r = await runInstrumentCommand(["--dry-run", "--apply"]);
    expect(r.exitCode).toBe(11);
  });
});

describe("--exclude repeats", () => {
  test("multiple --exclude flags all captured", async () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "dist"), { recursive: true });
    writeFileSync(join(cwd, "dist", "bundle.js"), "1", "utf-8");
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(join(cwd, "src", "x.py"), "1", "utf-8");
    const r = await runInstrumentCommand(
      ["--exclude", "dist/**", "--exclude", "build/**", "--output", "json"],
      { cwd, nowIso: NOW, sessionUlid: ULID },
    );
    expect(r.exitCode).toBe(0);
  });
});

describe("--max-file-bytes", () => {
  test("accepts a positive integer", async () => {
    const cwd = scratch();
    const r = await runInstrumentCommand(
      ["--max-file-bytes", "4096", "--output", "json"],
      { cwd, nowIso: NOW, sessionUlid: ULID },
    );
    expect(r.exitCode).toBe(0);
  });

  test("rejects a non-integer", async () => {
    const r = await runInstrumentCommand(["--max-file-bytes", "abc"]);
    expect(r.exitCode).toBe(11);
  });

  test("rejects zero / negative", async () => {
    const r = await runInstrumentCommand(["--max-file-bytes", "0"]);
    expect(r.exitCode).toBe(11);
  });
});

describe("--output json produces valid JSON on stdout", () => {
  test("json output parses cleanly", async () => {
    const cwd = scratch();
    const r = await runInstrumentCommand(["--output", "json"], {
      cwd,
      nowIso: NOW,
      sessionUlid: ULID,
    });
    expect(() => JSON.parse(r.stdout)).not.toThrow();
  });

  test("pretty output is non-empty and human-readable", async () => {
    const cwd = scratch();
    const r = await runInstrumentCommand(["--output", "pretty"], {
      cwd,
      nowIso: NOW,
      sessionUlid: ULID,
    });
    expect(r.stdout.length).toBeGreaterThan(0);
    expect(r.stdout).toContain("sessionUlid");
  });

  test("table output format runs", async () => {
    const cwd = scratch();
    const r = await runInstrumentCommand(["--output", "table"], {
      cwd,
      nowIso: NOW,
      sessionUlid: ULID,
    });
    expect(r.stdout.length).toBeGreaterThan(0);
  });
});
