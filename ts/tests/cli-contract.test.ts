/**
 * Tests for AC-369: CLI contract alignment with Python package.
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
// serve / mcp-serve naming alignment
// ---------------------------------------------------------------------------

describe("serve / mcp-serve contract alignment", () => {
  it("help lists mcp-serve (matching Python)", () => {
    const { stdout } = runCli(["--help"]);
    expect(stdout).toContain("mcp-serve");
  });

  it("help lists serve as HTTP API server (AC-467: dashboard removed)", () => {
    const { stdout } = runCli(["--help"]);
    const serveLines = stdout.split("\n").filter((l) => l.includes("serve") && !l.includes("mcp-serve"));
    expect(serveLines.some((l) => l.includes("HTTP") || l.includes("API") || l.includes("server"))).toBe(true);
  });

  it("mcp-serve --help shows MCP stdio description", () => {
    const { stdout, exitCode } = runCli(["mcp-serve", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("MCP");
  });

  it("serve --help shows HTTP API description (AC-467: no dashboard)", () => {
    const { stdout, exitCode } = runCli(["serve", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout.toLowerCase()).toMatch(/http|api|port/);
  });
});

// ---------------------------------------------------------------------------
// Intentional exclusions documented
// ---------------------------------------------------------------------------

describe("Intentional command exclusions", () => {
  it("help documents unsupported Python commands", () => {
    const { stdout } = runCli(["--help"]);
    // Should mention that some commands are Python-only
    expect(stdout.toLowerCase()).toMatch(/python.only|not.supported|unsupported/i);
  });
});

// ---------------------------------------------------------------------------
// Full command contract verification
// ---------------------------------------------------------------------------

describe("Full CLI command contract", () => {
  it("help lists all expected commands", () => {
    const { stdout } = runCli(["--help"]);
    const expected = [
      "init",
      "run",
      "list",
      "replay",
      "benchmark",
      "export",
      "export-training-data",
      "import-package",
      "new-scenario",
      "tui",
      "judge",
      "improve",
      "repl",
      "queue",
      "status",
      "serve",
      "mcp-serve",
      "version",
    ];
    for (const cmd of expected) {
      expect(stdout).toContain(cmd);
    }
  });
});
