import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runCli(args: string[]): { stdout: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 5000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });
    return { stdout, exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; status?: number };
    return { stdout: e.stdout ?? "", exitCode: e.status ?? 1 };
  }
}

describe("CLI", () => {
  it("shows help", () => {
    const { stdout, exitCode } = runCli(["--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("mts");
    expect(stdout).toContain("judge");
    expect(stdout).toContain("improve");
    expect(stdout).toContain("queue");
    expect(stdout).toContain("serve");
  });

  it("shows version", () => {
    const { stdout, exitCode } = runCli(["version"]);
    expect(exitCode).toBe(0);
    expect(stdout.trim()).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("judge requires args", () => {
    const { exitCode } = runCli(["judge"]);
    expect(exitCode).toBe(1);
  });

  it("unknown command fails", () => {
    const { exitCode } = runCli(["bogus"]);
    expect(exitCode).toBe(1);
  });

  it("status creates db and shows count", () => {
    const { stdout, exitCode } = runCli(["status"]);
    expect(exitCode).toBe(0);
    expect(JSON.parse(stdout).pendingCount).toBe(0);
  });

  it("improve --help shows verbose flag", () => {
    const { stdout, exitCode } = runCli(["improve", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("-v");
  });

  it("improve requires args", () => {
    const { exitCode } = runCli(["improve"]);
    expect(exitCode).toBe(1);
  });
});
