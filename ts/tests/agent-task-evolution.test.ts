import { describe, it, expect } from "vitest";
import {
  accumulateLessons,
  buildEnrichedPrompt,
  AgentTaskEvolutionRunner,
  FunctionSlot,
  type AgentTaskGenerationEvaluation,
  type LessonSignal,
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

describe("FunctionSlot (AC-776, parity with Python)", () => {
  it("assemble prepends the slot to the harness", () => {
    const slot = new FunctionSlot("def build():\n    return priority");
    const assembled = slot.assemble("def priority(v):\n    return 1.0");
    expect(assembled).toContain("def priority(v):");
    expect(assembled).toContain("def build():");
    expect(assembled.indexOf("def priority(v):")).toBeLessThan(assembled.indexOf("def build():"));
  });
});

describe("AgentTaskEvolutionRunner slot mode (AC-776)", () => {
  it("carries the slot, evaluates the assembled program", () => {
    const slot = new FunctionSlot("def build():\n    return priority  # HARNESS_MARKER");
    const evalInputs: string[] = [];
    const runner = new AgentTaskEvolutionRunner({
      taskPrompt: "t",
      generateFn: (_p, _g) => "def priority(v):\n    return 2.0",
      evaluateFn: (output, _g): AgentTaskGenerationEvaluation => {
        evalInputs.push(output);
        return { output, score: 0.5, reasoning: "ok", dimensionScores: {} };
      },
      initialOutput: "def priority(v):\n    return 0.0",
      slot,
    });
    const { state } = runner.runWithState(2);
    // evaluate received the ASSEMBLED program (harness present)
    expect(evalInputs.some((i) => i.includes("HARNESS_MARKER"))).toBe(true);
    // but best_output is only the SLOT (no harness)
    expect(state.bestOutput).not.toContain("HARNESS_MARKER");
    expect(state.bestOutput).toContain("def priority(v):");
  });

  it("includes a Fixed Harness section in the enriched prompt", () => {
    const out = buildEnrichedPrompt({
      taskPrompt: "t",
      playbook: "",
      generation: 1,
      bestOutput: "",
      bestScore: 0,
      harness: "HARNESS_CODE_HERE",
    });
    expect(out).toContain("HARNESS_CODE_HERE");
    expect(out).toContain("Fixed Harness");
  });
});

describe("domain-aware lesson accumulation (parity with Python)", () => {
  it("renders hint, plateau guidance, and metrics from a signal", () => {
    const signal: LessonSignal = {
      hint: "demote some members to admit new points",
      plateau: true,
      metrics: { size: 224, delta: 0 },
    };
    const lesson = accumulateLessons(judge(0.95, "valid"), 3, signal);
    expect(lesson).toContain("Hint: demote some members to admit new points");
    expect(lesson.toLowerCase()).toContain("plateau");
    expect(lesson).toContain("size=224");
    expect(lesson).toContain("delta=0");
  });

  it("matches legacy behavior with no signal", () => {
    const lesson = accumulateLessons(judge(0.6, "needs work"), 1);
    expect(lesson).toContain("Generation 1 (score: 0.60):");
    expect(lesson).not.toContain("Hint:");
    expect(lesson.toLowerCase()).not.toContain("plateau");
  });

  it("runner threads the evaluation's signal into the playbook", () => {
    const runner = new AgentTaskEvolutionRunner({
      taskPrompt: "t",
      generateFn: (_p, _g) => "candidate",
      evaluateFn: (output, _g): AgentTaskGenerationEvaluation => ({
        output,
        score: 0.5,
        reasoning: "ok",
        dimensionScores: {},
        lessonSignal: { hint: "try a different family" },
      }),
    });
    const { state } = runner.runWithState(1);
    expect(state.playbook).toContain("Hint: try a different family");
  });
});
