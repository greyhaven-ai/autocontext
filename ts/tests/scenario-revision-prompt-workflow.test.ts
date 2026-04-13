import { describe, expect, it } from "vitest";

import {
  buildRevisionPrompt,
  buildWeakDimensionSection,
  reviseAgentTaskOutput,
} from "../src/scenarios/scenario-revision-prompt-workflow.js";

describe("scenario revision prompt workflow", () => {
  it("builds weak-dimension sections and family-aware revision prompts", () => {
    expect(buildWeakDimensionSection({ depth: 0.3, breadth: 0.8, clarity: 0.6 })).toBe(
      "\n## Weak Dimensions (need improvement)\n- depth: 0.30\n- clarity: 0.60",
    );
    expect(buildWeakDimensionSection({ clarity: 0.8 })).toBeNull();

    const prompt = buildRevisionPrompt({
      currentSpec: { description: "Old task", taskPrompt: "Do X", rubric: "Evaluate X" },
      feedback: "Make it harder and add edge cases",
      family: "agent_task",
      judgeResult: {
        score: 0.4,
        reasoning: "Too simple",
        dimensionScores: { depth: 0.3, breadth: 0.8 },
      },
    });

    expect(prompt).toContain("an agent task evaluated by an LLM judge");
    expect(prompt).toContain("Too simple");
    expect(prompt).toContain("depth");
    expect(prompt).toContain("Make it harder");
  });

  it("builds agent-task output revision prompts with rubric and revision instructions", () => {
    const prompt = reviseAgentTaskOutput({
      originalOutput: "Initial answer",
      judgeResult: {
        score: 0.55,
        reasoning: "Needs more detail",
        dimensionScores: { depth: 0.4, clarity: 0.8 },
      },
      taskPrompt: "Summarize the incident",
      revisionPrompt: "Add severity and owner assignment.",
      rubric: "Check completeness and clarity.",
    });

    expect(prompt).toContain("## Current Score");
    expect(prompt).toContain("Needs more detail");
    expect(prompt).toContain("depth");
    expect(prompt).toContain("## Rubric");
    expect(prompt).toContain("Add severity and owner assignment.");
    expect(prompt).toContain("Return ONLY the revised output");
  });
});
