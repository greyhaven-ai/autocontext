/**
 * Tests for AC-400: benchmark command --provider flag.
 */

import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runCli(args: string[]): { stdout: string; stderr: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 15000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });
    return { stdout, stderr: "", exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; stderr?: string; status?: number };
    return { stdout: e.stdout ?? "", stderr: e.stderr ?? "", exitCode: e.status ?? 1 };
  }
}

describe("benchmark --provider flag", () => {
  it("benchmark --help mentions --provider", () => {
    const { stdout, exitCode } = runCli(["benchmark", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("--provider");
  });

  it("benchmark --provider deterministic does not throw ERR_PARSE_ARGS_UNKNOWN_OPTION", () => {
    const { stderr, exitCode } = runCli([
      "benchmark",
      "--scenario", "grid_ctf",
      "--provider", "deterministic",
      "--runs", "1",
      "--gens", "1",
      "--json",
    ]);
    // Should NOT contain the parse args error
    expect(stderr).not.toContain("ERR_PARSE_ARGS_UNKNOWN_OPTION");
    expect(exitCode).toBe(0);
  });

  it("benchmark --provider deterministic --json returns valid results", () => {
    const { stdout, exitCode } = runCli([
      "benchmark",
      "--scenario", "grid_ctf",
      "--provider", "deterministic",
      "--runs", "1",
      "--gens", "1",
      "--json",
    ]);
    expect(exitCode).toBe(0);
    const result = JSON.parse(stdout);
    expect(result.scenario).toBe("grid_ctf");
    expect(result.runs).toBe(1);
    expect(typeof result.meanBestScore).toBe("number");
  });
});
