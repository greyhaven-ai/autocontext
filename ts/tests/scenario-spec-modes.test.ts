/**
 * Tests for AC-406: Scenario creation --from-spec, --from-stdin, --prompt-only modes.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runCli(args: string[], opts: { input?: string; env?: Record<string, string> } = {}): { stdout: string; stderr: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 15000,
      input: opts.input,
      env: { ...process.env, NODE_NO_WARNINGS: "1", ...opts.env },
    });
    return { stdout, stderr: "", exitCode: 0 };
  } catch (err: unknown) {
    const e = err as { stdout?: string; stderr?: string; status?: number };
    return { stdout: e.stdout ?? "", stderr: e.stderr ?? "", exitCode: e.status ?? 1 };
  }
}

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-spec-modes-"));
}

// ---------------------------------------------------------------------------
// --from-spec mode
// ---------------------------------------------------------------------------

describe("new-scenario --from-spec", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("--help mentions --from-spec", () => {
    const { stdout } = runCli(["new-scenario", "--help"]);
    expect(stdout).toContain("--from-spec");
  });

  it("accepts a spec file and registers without calling an LLM", () => {
    const specPath = join(dir, "spec.json");
    writeFileSync(specPath, JSON.stringify({
      name: "summarization_quality",
      description: "Evaluate summarization of documents",
      taskPrompt: "Given a source document, produce a summary under 200 words.",
      rubric: "Factual accuracy, coverage, conciseness",
    }), "utf-8");

    const { stdout, exitCode } = runCli(["new-scenario", "--from-spec", specPath, "--json"]);
    expect(exitCode).toBe(0);
    const result = JSON.parse(stdout);
    expect(result.name).toBe("summarization_quality");
    expect(result.spec.taskPrompt).toContain("summary");
  });

  it("rejects spec file with missing required fields", () => {
    const specPath = join(dir, "bad.json");
    writeFileSync(specPath, JSON.stringify({ name: "incomplete" }), "utf-8");

    const { exitCode, stderr } = runCli(["new-scenario", "--from-spec", specPath]);
    expect(exitCode).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// --from-stdin mode
// ---------------------------------------------------------------------------

describe("new-scenario --from-stdin", () => {
  it("--help mentions --from-stdin", () => {
    const { stdout } = runCli(["new-scenario", "--help"]);
    expect(stdout).toContain("--from-stdin");
  });

  it("reads spec from stdin", () => {
    const spec = JSON.stringify({
      name: "code_review",
      description: "Evaluate code review quality",
      taskPrompt: "Review this pull request diff.",
      rubric: "Thoroughness, accuracy, actionability",
    });

    const { stdout, exitCode } = runCli(
      ["new-scenario", "--from-stdin", "--json"],
      { input: spec },
    );
    expect(exitCode).toBe(0);
    const result = JSON.parse(stdout);
    expect(result.name).toBe("code_review");
  });
});

// ---------------------------------------------------------------------------
// --prompt-only mode
// ---------------------------------------------------------------------------

describe("new-scenario --prompt-only", () => {
  it("--help mentions --prompt-only", () => {
    const { stdout } = runCli(["new-scenario", "--help"]);
    expect(stdout).toContain("--prompt-only");
  });

  it("outputs the prompt without calling an LLM", () => {
    const { stdout, exitCode } = runCli([
      "new-scenario",
      "--description", "Test summarization quality",
      "--prompt-only",
    ]);
    expect(exitCode).toBe(0);
    // Should contain the system prompt for scenario generation
    expect(stdout).toContain("scenario");
    expect(stdout).toContain("taskPrompt");
    expect(stdout).toContain("rubric");
    // Should NOT contain a generated scenario (no LLM was called)
    expect(stdout).not.toContain('"name":');
  });
});
