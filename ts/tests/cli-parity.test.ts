/**
 * Tests for AC-363: CLI/package workflow parity — new commands.
 */

import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runCli(args: string[]): { stdout: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 10000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });
    return { stdout, exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; status?: number };
    return { stdout: e.stdout ?? "", exitCode: e.status ?? 1 };
  }
}

// ---------------------------------------------------------------------------
// Help output includes all new commands
// ---------------------------------------------------------------------------

describe("CLI parity — help output", () => {
  it("help includes list command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("list");
  });

  it("help includes replay command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("replay");
  });

  it("help includes export command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("export");
  });

  it("help includes import-package command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("import-package");
  });

  it("help includes new-scenario command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("new-scenario");
  });

  it("help includes benchmark command", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("benchmark");
  });
});

// ---------------------------------------------------------------------------
// list command
// ---------------------------------------------------------------------------

describe("CLI list command", () => {
  it("list returns JSON array", () => {
    const { stdout, exitCode } = runCli(["list", "--json"]);
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout);
    expect(Array.isArray(parsed)).toBe(true);
  });

  it("list --help shows options", () => {
    const { stdout, exitCode } = runCli(["list", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("--json");
  });
});

// ---------------------------------------------------------------------------
// replay command
// ---------------------------------------------------------------------------

describe("CLI replay command", () => {
  it("replay --help shows usage", () => {
    const { stdout, exitCode } = runCli(["replay", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("run-id");
    expect(stdout).toContain("generation");
  });

  it("replay requires run-id", () => {
    const { exitCode } = runCli(["replay"]);
    expect(exitCode).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// export command
// ---------------------------------------------------------------------------

describe("CLI export command", () => {
  it("export --help shows options", () => {
    const { stdout, exitCode } = runCli(["export", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("scenario");
  });

  it("export requires scenario", () => {
    const { exitCode } = runCli(["export"]);
    expect(exitCode).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// import-package command
// ---------------------------------------------------------------------------

describe("CLI import-package command", () => {
  it("import-package --help shows options", () => {
    const { stdout, exitCode } = runCli(["import-package", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("file");
  });

  it("import-package requires file", () => {
    const { exitCode } = runCli(["import-package"]);
    expect(exitCode).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// new-scenario command
// ---------------------------------------------------------------------------

describe("CLI new-scenario command", () => {
  it("new-scenario --help shows options", () => {
    const { stdout, exitCode } = runCli(["new-scenario", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("description");
  });

  it("new-scenario requires description", () => {
    const { exitCode } = runCli(["new-scenario"]);
    expect(exitCode).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// benchmark command
// ---------------------------------------------------------------------------

describe("CLI benchmark command", () => {
  it("benchmark --help shows options", () => {
    const { stdout, exitCode } = runCli(["benchmark", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("scenario");
    expect(stdout).toContain("runs");
  });
});
