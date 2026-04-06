/**
 * Tests for AC-406: Scenario creation --from-spec, --from-stdin, --prompt-only modes.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runCli(
  args: string[],
  opts: { input?: string; env?: Record<string, string>; cwd?: string } = {},
): { stdout: string; stderr: string; exitCode: number } {
  try {
    const stdout = execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 15000,
      input: opts.input,
      cwd: opts.cwd,
      env: { ...process.env, NODE_NO_WARNINGS: "1", ...opts.env },
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

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-spec-modes-"));
}

// ---------------------------------------------------------------------------
// --from-spec mode
// ---------------------------------------------------------------------------

describe("new-scenario --from-spec", () => {
  let dir: string;

  beforeEach(() => {
    dir = makeTempDir();
  });
  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("--help mentions --from-spec", () => {
    const { stdout } = runCli(["new-scenario", "--help"]);
    expect(stdout).toContain("--from-spec");
  });

  it("accepts a spec file and registers without calling an LLM", () => {
    const specPath = join(dir, "spec.json");
    const knowledgeRoot = join(dir, "knowledge");
    writeFileSync(
      specPath,
      JSON.stringify({
        name: "summarization_quality",
        family: "investigation",
        description: "Evaluate summarization of documents",
        taskPrompt:
          "Given a source document, produce a summary under 200 words.",
        rubric: "Factual accuracy, coverage, conciseness",
        actions: [
          {
            name: "gather_logs",
            description: "Collect system logs",
            parameters: {},
            preconditions: [],
            effects: ["logs_gathered"],
          },
          {
            name: "analyze_data",
            description: "Analyze collected data",
            parameters: {},
            preconditions: ["gather_logs"],
            effects: ["analysis_done"],
          },
        ],
      }),
      "utf-8",
    );

    const { stdout, exitCode } = runCli(
      ["new-scenario", "--from-spec", specPath, "--json"],
      { env: { AUTOCONTEXT_KNOWLEDGE_ROOT: knowledgeRoot } },
    );
    expect(exitCode).toBe(0);
    const result = JSON.parse(stdout);
    expect(result.name).toBe("summarization_quality");
    expect(result.family).toBe("investigation");
    expect(result.spec.taskPrompt).toContain("summary");
    expect(result.persisted).toBe(true);
    expect(result.generatedSource).toBe(true);
    expect(result.scenarioDir).toBe(
      join(knowledgeRoot, "_custom_scenarios", "summarization_quality"),
    );
    expect(
      existsSync(
        join(
          knowledgeRoot,
          "_custom_scenarios",
          "summarization_quality",
          "spec.json",
        ),
      ),
    ).toBe(true);
    expect(
      existsSync(
        join(
          knowledgeRoot,
          "_custom_scenarios",
          "summarization_quality",
          "scenario.js",
        ),
      ),
    ).toBe(true);
  });

  it("rejects spec file with missing required fields", () => {
    const specPath = join(dir, "bad.json");
    writeFileSync(specPath, JSON.stringify({ name: "incomplete" }), "utf-8");

    const { exitCode } = runCli(["new-scenario", "--from-spec", specPath]);
    expect(exitCode).toBe(1);
  });

  it("derives family from the spec when family is omitted", () => {
    const specPath = join(dir, "derived.json");
    const knowledgeRoot = join(dir, "knowledge");
    writeFileSync(
      specPath,
      JSON.stringify({
        name: "incident_root_cause",
        description: "Investigate the root cause of a production outage",
        taskPrompt:
          "Investigate the root cause of the outage and explain the failure chain.",
        rubric: "Root cause accuracy, evidence, remediation quality",
        actions: [
          {
            name: "gather_logs",
            description: "Collect system logs",
            parameters: {},
            preconditions: [],
            effects: ["logs_gathered"],
          },
          {
            name: "analyze_data",
            description: "Analyze collected data",
            parameters: {},
            preconditions: ["gather_logs"],
            effects: ["analysis_done"],
          },
        ],
      }),
      "utf-8",
    );

    const { stdout, exitCode } = runCli(
      ["new-scenario", "--from-spec", specPath, "--json"],
      { env: { AUTOCONTEXT_KNOWLEDGE_ROOT: knowledgeRoot } },
    );
    expect(exitCode).toBe(0);
    const result = JSON.parse(stdout);
    expect(result.family).toBe("investigation");
  });

  it("fails fast for dead-end families instead of leaving a fake scaffold", () => {
    const specPath = join(dir, "game.json");
    const knowledgeRoot = join(dir, "knowledge");
    writeFileSync(
      specPath,
      JSON.stringify({
        name: "custom_board_game",
        family: "game",
        description: "A custom board game with turns and scoring.",
        taskPrompt: "Play a two-player board game with scoring and turns.",
        rubric: "Strategic depth and balance",
      }),
      "utf-8",
    );

    const { exitCode, stderr } = runCli(
      ["new-scenario", "--from-spec", specPath, "--json"],
      { env: { AUTOCONTEXT_KNOWLEDGE_ROOT: knowledgeRoot } },
    );

    expect(exitCode).toBe(1);
    expect(stderr).toContain("does not support family 'game'");
    expect(
      existsSync(join(knowledgeRoot, "_custom_scenarios", "custom_board_game")),
    ).toBe(false);
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
    const dir = makeTempDir();
    const knowledgeRoot = join(dir, "knowledge");
    try {
      const spec = JSON.stringify({
        name: "code_review",
        family: "workflow",
        description: "Evaluate code review quality",
        taskPrompt: "Review this pull request diff.",
        rubric: "Thoroughness, accuracy, actionability",
        actions: [
          {
            name: "review_diff",
            description: "Review the diff",
            parameters: {},
            preconditions: [],
            effects: ["diff_reviewed"],
          },
          {
            name: "write_feedback",
            description: "Write review feedback",
            parameters: {},
            preconditions: ["review_diff"],
            effects: ["feedback_written"],
          },
        ],
      });

      const { stdout, exitCode } = runCli(
        ["new-scenario", "--from-stdin", "--json"],
        {
          input: spec,
          env: { AUTOCONTEXT_KNOWLEDGE_ROOT: knowledgeRoot },
        },
      );
      expect(exitCode).toBe(0);
      const result = JSON.parse(stdout);
      expect(result.name).toBe("code_review");
      expect(result.family).toBe("workflow");
      expect(result.persisted).toBe(true);
      expect(
        existsSync(
          join(
            knowledgeRoot,
            "_custom_scenarios",
            "code_review",
            "scenario.js",
          ),
        ),
      ).toBe(true);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("rejects unsupported families from stdin without persisting artifacts", () => {
    const dir = makeTempDir();
    const knowledgeRoot = join(dir, "knowledge");
    try {
      const spec = JSON.stringify({
        name: "stdin_board_game",
        family: "game",
        description: "A board game imported through stdin.",
        taskPrompt: "Create a board game with scoring.",
        rubric: "Fairness and strategic depth",
      });

      const { exitCode, stderr } = runCli(
        ["new-scenario", "--from-stdin", "--json"],
        {
          input: spec,
          env: { AUTOCONTEXT_KNOWLEDGE_ROOT: knowledgeRoot },
        },
      );

      expect(exitCode).toBe(1);
      expect(stderr).toContain("does not support family 'game'");
      expect(
        existsSync(
          join(knowledgeRoot, "_custom_scenarios", "stdin_board_game"),
        ),
      ).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
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
      "--description",
      "Test summarization quality",
      "--prompt-only",
    ]);
    expect(exitCode).toBe(0);
    // Should contain the system prompt for scenario generation
    expect(stdout).toContain("scenario");
    expect(stdout).toContain("name");
    expect(stdout).toContain("family");
    expect(stdout).toContain("taskPrompt");
    expect(stdout).toContain("rubric");
    // Should NOT contain a generated scenario (no LLM was called)
    expect(stdout).not.toContain('"name":');
  });
});
