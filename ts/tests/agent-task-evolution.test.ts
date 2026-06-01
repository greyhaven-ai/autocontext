import { describe, it, expect } from "vitest";
import {
  accumulateLessons,
  buildEnrichedPrompt,
  AgentTaskEvolutionRunner,
  type AgentTaskGenerationEvaluation,
} from "../src/execution/agent-task-evolution.js";
import type { AgentTaskResult } from "../src/types/index.js";

function judge(
  score: number,
  reasoning: string,
  dimensionScores: Record<string, number> = {},
): AgentTaskResult {
  return { score, reasoning, dimensionScores, internalRetries: 0 };
}

describe("accumulateLessons (parity with Python accumulate_lessons)", () => {
  it("formats generation header, feedback, and strong dimensions", () => {
    const lesson = accumulateLessons(judge(0.95, "valid cap, |A|=224", { size: 0.95 }), 3);
    expect(lesson).toContain("Generation 3 (score: 0.95):");
    expect(lesson).toContain("  Feedback: valid cap, |A|=224");
    expect(lesson).toContain("Strong dimensions: size (0.95)");
  });

  it("lists weak dimensions (score < 0.7) ascending", () => {
    const lesson = accumulateLessons(judge(0.5, "needs work", { depth: 0.4, structure: 0.6 }), 1);
    expect(lesson).toContain("Weak dimensions: depth (0.40), structure (0.60)");
  });
});

describe("buildEnrichedPrompt (parity with Python build_enriched_prompt)", () => {
  it("returns the bare task prompt when no playbook or best output", () => {
    const out = buildEnrichedPrompt({
      taskPrompt: "TASK",
      playbook: "",
      generation: 1,
      bestOutput: "",
      bestScore: 0,
    });
    expect(out).toBe("TASK");
  });

  it("includes playbook and best-output sections when present", () => {
    const out = buildEnrichedPrompt({
      taskPrompt: "TASK",
      playbook: "- lesson one",
      generation: 2,
      bestOutput: "PRIOR",
      bestScore: 0.8,
    });
    expect(out).toContain("## Accumulated Lessons (Generation 2)");
    expect(out).toContain("Previous best score: 0.80");
    expect(out).toContain("- lesson one");
    expect(out).toContain("## Best Previous Output (score 0.80)");
    expect(out).toContain("PRIOR");
  });
});

describe("AgentTaskEvolutionRunner (parity with Python runner)", () => {
  it("uses initialOutput for the cold-start generation", () => {
    const seen: string[] = [];
    const runner = new AgentTaskEvolutionRunner({
      taskPrompt: "make a thing",
      generateFn: (_prompt, _gen) => {
        seen.push("called");
        return "generated";
      },
      evaluateFn: (output, _gen): AgentTaskGenerationEvaluation => ({
        output,
        score: output === "SEED" ? 0.9 : 0.5,
        reasoning: `scored ${output}`,
        dimensionScores: {},
      }),
      initialOutput: "SEED",
      taskName: "t",
    });
    const traj = runner.run(1);
    expect(seen.length).toBe(0); // gen 0 used the seed, never called generateFn
    expect(traj.scoreHistory).toEqual([0.9]);
    expect(traj.metadata.bestOutput).toBe("SEED");
  });

  it("climbs across generations and reports a trajectory", () => {
    let g = 0;
    const runner = new AgentTaskEvolutionRunner({
      taskPrompt: "improve",
      generateFn: (_prompt, _gen) => `cand${g}`,
      evaluateFn: (output, _gen): AgentTaskGenerationEvaluation => {
        g += 1;
        return { output, score: 0.3 + 0.2 * g, reasoning: "ok", dimensionScores: {} };
      },
      initialOutput: "",
      taskName: "climb",
    });
    const traj = runner.run(3);
    expect(traj.totalGenerations).toBe(3);
    expect(traj.scoreHistory.length).toBe(3);
    expect(traj.finalScore).toBeGreaterThan(traj.coldStartScore);
    expect(traj.improvementDelta).toBeCloseTo(0.4, 5);
  });
});
