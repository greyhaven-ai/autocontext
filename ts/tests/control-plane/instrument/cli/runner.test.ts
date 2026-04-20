/**
 * A2-I Layer 7 - CLI runner unit tests.
 *
 * Verifies runInstrumentCommand dispatches to runInstrument with parsed flags.
 */
import { describe, test, expect, beforeEach } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  runInstrumentCommand,
  INSTRUMENT_HELP_TEXT,
} from "../../../../src/control-plane/instrument/cli/runner.js";
import { resetRegistryForTests } from "../../../../src/control-plane/instrument/registry/plugin-registry.js";

const FIXED_ULID = "01HN0000000000000000000001";
const FIXED_NOW = "2026-04-17T12:00:00.000Z";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-runner-"));
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

describe("runInstrumentCommand - help", () => {
  test("--help returns help text and exit 0", async () => {
    const result = await runInstrumentCommand(["--help"]);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toBe(INSTRUMENT_HELP_TEXT);
  });
});

describe("runInstrumentCommand - dry-run default", () => {
  test("no flags -> dry-run mode, exit 0, JSON output when requested", async () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(join(cwd, "src", "main.py"), "pass\n", "utf-8");

    const result = await runInstrumentCommand(["--output", "json"], {
      cwd,
      nowIso: FIXED_NOW,
      sessionUlid: FIXED_ULID,
    });
    expect(result.exitCode).toBe(0);
    const parsed = JSON.parse(result.stdout);
    expect(parsed.mode).toBe("dry-run");
    expect(parsed.sessionUlid).toBe(FIXED_ULID);
    expect(parsed.exitCode).toBe(0);
  });
});

describe("runInstrumentCommand - flag errors map to exit 11", () => {
  test("unknown flag -> exit 11", async () => {
    const result = await runInstrumentCommand(["--bogus"]);
    expect(result.exitCode).toBe(11);
    expect(result.stderr).toContain("Unknown flag");
  });

  test("--dry-run + --apply mutually exclusive -> exit 11", async () => {
    const result = await runInstrumentCommand(["--dry-run", "--apply"]);
    expect(result.exitCode).toBe(11);
    expect(result.stderr).toContain("mutually exclusive");
  });

  test("--branch without --apply -> exit 11", async () => {
    const result = await runInstrumentCommand(["--branch", "foo"]);
    expect(result.exitCode).toBe(11);
    expect(result.stderr).toContain("--branch requires --apply");
  });

  test("--max-file-bytes non-integer -> exit 11", async () => {
    const result = await runInstrumentCommand(["--max-file-bytes", "abc"]);
    expect(result.exitCode).toBe(11);
  });

  test("--output invalid value -> exit 11", async () => {
    const result = await runInstrumentCommand(["--output", "yaml"]);
    expect(result.exitCode).toBe(11);
  });
});

describe("runInstrumentCommand - multi-value flags", () => {
  test("--exclude is repeatable", async () => {
    const cwd = scratch();
    const result = await runInstrumentCommand(
      ["--exclude", "dist/**", "--exclude", "build/**", "--output", "json"],
      { cwd, nowIso: FIXED_NOW, sessionUlid: FIXED_ULID },
    );
    expect(result.exitCode).toBe(0);
    const parsed = JSON.parse(result.stdout);
    expect(parsed.mode).toBe("dry-run");
  });
});

describe("runInstrumentCommand - --fail-if-empty wiring", () => {
  test("empty registry + --fail-if-empty -> exit 12", async () => {
    const cwd = scratch();
    const result = await runInstrumentCommand(
      ["--fail-if-empty", "--output", "json"],
      { cwd, nowIso: FIXED_NOW, sessionUlid: FIXED_ULID },
    );
    expect(result.exitCode).toBe(12);
  });
});

describe("runInstrumentCommand - --enhanced advisory", () => {
  test("--enhanced produces a stderr advisory referencing plan.json stability", async () => {
    const cwd = scratch();
    const result = await runInstrumentCommand(
      ["--enhanced", "--output", "json"],
      { cwd, nowIso: FIXED_NOW, sessionUlid: FIXED_ULID },
    );
    expect(result.exitCode).toBe(0);
    // Layer 8 wired the enhancer; advisory now notes that without a provider
    // enhancement falls back to defaults, and plan.json is unaffected either way.
    expect(result.stderr).toContain("--enhanced");
    expect(result.stderr).toContain("plan.json");
  });
});

describe("runInstrumentCommand - --force emits stderr warning", () => {
  test("--force surfaces a WARNING on stderr", async () => {
    const cwd = scratch();
    const result = await runInstrumentCommand(
      ["--force", "--output", "json"],
      { cwd, nowIso: FIXED_NOW, sessionUlid: FIXED_ULID },
    );
    expect(result.exitCode).toBe(0);
    expect(result.stderr).toContain("--force bypasses");
  });
});
