import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runCli(
  args: string[],
  envOverrides: Record<string, string> = {},
): { stdout: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 5000,
      env: { ...process.env, NODE_NO_WARNINGS: "1", ...envOverrides },
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
    expect(stdout).toContain("autoctx");
    expect(stdout).toContain("init");
    expect(stdout).toContain("judge");
    expect(stdout).toContain("improve");
    expect(stdout).toContain("repl");
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
    expect(stdout).toContain("--rlm");
  });

  it("improve requires args", () => {
    const { exitCode } = runCli(["improve"]);
    expect(exitCode).toBe(1);
  });

  it("improve generates an initial draft when prompt and rubric are provided without output", () => {
    const { stdout, exitCode } = runCli(
      [
        "improve",
        "-p",
        "Write a haiku about distributed systems",
        "-r",
        "Score syllable_accuracy_5_7_5, technical_relevance, and imagery_creativity 0-1 each.",
        "-n",
        "2",
        "-t",
        "0.8",
      ],
      { AUTOCONTEXT_AGENT_PROVIDER: "deterministic" },
    );
    expect(exitCode).toBe(0);
    expect(JSON.parse(stdout)).toMatchObject({
      bestOutput: expect.any(String),
      totalRounds: expect.any(Number),
    });
  });

  it("queue --help shows RLM flags", () => {
    const { stdout, exitCode } = runCli(["queue", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("--rlm");
  });

  it("repl --help shows phase option", () => {
    const { stdout, exitCode } = runCli(["repl", "--help"]);
    expect(exitCode).toBe(0);
    expect(stdout).toContain("--phase");
    expect(stdout).toContain("--reference-context");
  });

  it("repl requires prompt and rubric", () => {
    const { exitCode } = runCli(["repl"]);
    expect(exitCode).toBe(1);
  });

  it("repl revise requires current output", () => {
    const { exitCode } = runCli(["repl", "-p", "Task", "-r", "Rubric", "--phase", "revise"]);
    expect(exitCode).toBe(1);
  });
});
