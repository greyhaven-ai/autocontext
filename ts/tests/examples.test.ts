import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const EXAMPLE = join(import.meta.dirname, "..", "examples", "run-repl-session.mjs");

function runExample(args: string[]): { stdout: string; stderr: string; exitCode: number } {
  try {
    const stdout = execFileSync("node", [EXAMPLE, ...args], {
      encoding: "utf8",
      timeout: 5000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { stdout, stderr: "", exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; stderr?: string; status?: number };
    return {
      stdout: e.stdout ?? "",
      stderr: e.stderr ?? "",
      exitCode: e.status ?? 1,
    };
  }
}

describe("example MCP client", () => {
  it("shows help without trying to connect", () => {
    const result = runExample(["--help"]);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("run-repl-session.mjs");
    expect(result.stdout).toContain("run_repl_session");
    expect(result.stdout).toContain("--phase");
  });
});
