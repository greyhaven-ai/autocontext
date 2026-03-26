/**
 * Tests for AC-409: Agent-as-judge pattern — decouple LLM calls from judging.
 *
 * - DelegatedJudge accepts externally-provided evaluations
 * - CallbackJudge calls a user-supplied function for scoring
 * - CLI `judge --from-stdin` accepts piped JSON results
 * - MCP tool accepts pre-computed evaluations
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { spawnSync } from "node:child_process";
import { join } from "node:path";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

// ---------------------------------------------------------------------------
// DelegatedJudge — pre-loaded evaluation results
// ---------------------------------------------------------------------------

describe("DelegatedJudge", () => {
  it("exports DelegatedJudge class", async () => {
    const { DelegatedJudge } = await import("../src/judge/delegated.js");
    expect(DelegatedJudge).toBeDefined();
  });

  it("evaluate returns the pre-loaded result", async () => {
    const { DelegatedJudge } = await import("../src/judge/delegated.js");
    const judge = new DelegatedJudge({
      score: 0.85,
      reasoning: "Clear and well-structured",
      dimensionScores: { clarity: 0.9, completeness: 0.8 },
    });

    const result = await judge.evaluate({
      taskPrompt: "Write a summary",
      agentOutput: "Some output",
    });

    expect(result.score).toBe(0.85);
    expect(result.reasoning).toBe("Clear and well-structured");
    expect(result.dimensionScores.clarity).toBe(0.9);
    expect(result.parseMethod).toBe("delegated");
  });

  it("can be updated with new results between evaluations", async () => {
    const { DelegatedJudge } = await import("../src/judge/delegated.js");
    const judge = new DelegatedJudge({ score: 0.5, reasoning: "initial" });

    const r1 = await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(r1.score).toBe(0.5);

    judge.setResult({ score: 0.9, reasoning: "improved" });
    const r2 = await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(r2.score).toBe(0.9);
    expect(r2.reasoning).toBe("improved");
  });

  it("has rubric property for interface compatibility", async () => {
    const { DelegatedJudge } = await import("../src/judge/delegated.js");
    const judge = new DelegatedJudge(
      { score: 0.5, reasoning: "ok" },
      "Custom rubric text",
    );
    expect(judge.rubric).toBe("Custom rubric text");
  });
});

// ---------------------------------------------------------------------------
// CallbackJudge — user-supplied evaluation function
// ---------------------------------------------------------------------------

describe("CallbackJudge", () => {
  it("exports CallbackJudge class", async () => {
    const { CallbackJudge } = await import("../src/judge/delegated.js");
    expect(CallbackJudge).toBeDefined();
  });

  it("calls the provided function for evaluation", async () => {
    const { CallbackJudge } = await import("../src/judge/delegated.js");

    const judge = new CallbackJudge(async (opts) => ({
      score: opts.agentOutput.length > 10 ? 0.8 : 0.3,
      reasoning: `Output length: ${opts.agentOutput.length}`,
      dimensionScores: { length: opts.agentOutput.length > 10 ? 1.0 : 0.0 },
    }));

    const short = await judge.evaluate({ taskPrompt: "t", agentOutput: "hi" });
    expect(short.score).toBe(0.3);

    const long = await judge.evaluate({ taskPrompt: "t", agentOutput: "this is a longer output" });
    expect(long.score).toBe(0.8);
  });

  it("parseMethod is 'callback'", async () => {
    const { CallbackJudge } = await import("../src/judge/delegated.js");
    const judge = new CallbackJudge(async () => ({
      score: 0.5,
      reasoning: "ok",
    }));
    const result = await judge.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(result.parseMethod).toBe("callback");
  });
});

// ---------------------------------------------------------------------------
// CLI: judge --from-stdin
// ---------------------------------------------------------------------------

describe("judge --from-stdin", () => {
  it("accepts piped JSON evaluation and outputs result", () => {
    const input = JSON.stringify({
      score: 0.75,
      reasoning: "Good but could improve",
      dimensions: { accuracy: 0.8, style: 0.7 },
    });

    const r = spawnSync("npx", ["tsx", CLI, "judge", "--from-stdin"], {
      input,
      encoding: "utf8",
      timeout: 15000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });

    expect(r.status).toBe(0);
    const parsed = JSON.parse(r.stdout);
    expect(parsed.score).toBe(0.75);
    expect(parsed.reasoning).toBe("Good but could improve");
    expect(parsed.source).toBe("delegated");
  });

  it("rejects invalid JSON from stdin", () => {
    const r = spawnSync("npx", ["tsx", CLI, "judge", "--from-stdin"], {
      input: "not valid json",
      encoding: "utf8",
      timeout: 15000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });

    expect(r.status).toBe(1);
    expect(r.stderr).toContain("Invalid");
  });

  it("rejects score outside 0-1 range", () => {
    const input = JSON.stringify({ score: 1.5, reasoning: "too high" });
    const r = spawnSync("npx", ["tsx", CLI, "judge", "--from-stdin"], {
      input,
      encoding: "utf8",
      timeout: 15000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });

    expect(r.status).toBe(1);
  });
});
