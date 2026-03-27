/**
 * AC-441: Scenario revision flow — users can refine created scenarios with feedback.
 *
 * Tests the revision module that takes a current spec + user feedback
 * and produces an updated spec via the LLM designer.
 */

import { describe, it, expect } from "vitest";
import {
  buildRevisionPrompt,
  reviseSpec,
  reviseAgentTaskOutput,
  type RevisionResult,
} from "../src/scenarios/scenario-revision.js";
import type { AgentTaskSpec } from "../src/scenarios/agent-task-spec.js";

// ---------------------------------------------------------------------------
// Revision prompt building
// ---------------------------------------------------------------------------

describe("buildRevisionPrompt", () => {
  it("includes current spec, feedback, and instructions", () => {
    const prompt = buildRevisionPrompt({
      currentSpec: { description: "Old task", taskPrompt: "Do X", rubric: "Evaluate X" },
      feedback: "Make it harder and add edge cases",
      family: "agent_task",
    });

    expect(prompt).toContain("Old task");
    expect(prompt).toContain("Do X");
    expect(prompt).toContain("Make it harder");
    expect(prompt).toContain("Revise");
  });

  it("includes family context", () => {
    const prompt = buildRevisionPrompt({
      currentSpec: { description: "Sim", actions: [] },
      feedback: "Add more actions",
      family: "simulation",
    });

    expect(prompt).toContain("simulation");
  });

  it("includes weak dimension hints when judge result provided", () => {
    const prompt = buildRevisionPrompt({
      currentSpec: { description: "Task" },
      feedback: "Improve",
      family: "agent_task",
      judgeResult: {
        score: 0.4,
        reasoning: "Too simple",
        dimensionScores: { depth: 0.3, breadth: 0.8 },
      },
    });

    expect(prompt).toContain("0.4");
    expect(prompt).toContain("Too simple");
    expect(prompt).toContain("depth");
  });
});

// ---------------------------------------------------------------------------
// Spec revision via mock LLM
// ---------------------------------------------------------------------------

describe("reviseSpec", () => {
  it("produces an updated spec from feedback", async () => {
    const mockProvider = {
      complete: async (opts: { systemPrompt: string; userPrompt: string }) => ({
        text: JSON.stringify({
          description: "Improved task with edge cases",
          taskPrompt: "Do X with edge cases and error handling",
          rubric: "Evaluate X comprehensively",
          outputFormat: "free_text",
        }),
      }),
      defaultModel: () => "test-model",
    };

    const result = await reviseSpec({
      currentSpec: {
        description: "Old task",
        taskPrompt: "Do X",
        rubric: "Evaluate X",
      },
      feedback: "Add edge cases",
      family: "agent_task",
      provider: mockProvider as never,
    });

    expect(result.revised).toBeDefined();
    expect(result.revised.description).toContain("edge cases");
    expect(result.changesApplied).toBe(true);
  });

  it("preserves original spec on LLM failure", async () => {
    const mockProvider = {
      complete: async () => ({ text: "this is not valid json at all" }),
      defaultModel: () => "test-model",
    };

    const original = {
      description: "Original",
      taskPrompt: "Do Y",
      rubric: "Evaluate Y",
    };

    const result = await reviseSpec({
      currentSpec: original,
      feedback: "Change it",
      family: "agent_task",
      provider: mockProvider as never,
    });

    expect(result.changesApplied).toBe(false);
    expect(result.revised.description).toBe("Original");
    expect(result.error).toBeDefined();
  });

  it("works for simulation family specs", async () => {
    const mockProvider = {
      complete: async () => ({
        text: JSON.stringify({
          description: "Better simulation",
          environment_description: "Updated env",
          initial_state_description: "New initial state",
          success_criteria: ["all steps done"],
          failure_modes: ["timeout"],
          max_steps: 20,
          actions: [
            { name: "step1", description: "First step", parameters: {}, preconditions: [], effects: [] },
            { name: "step2", description: "Second step", parameters: {}, preconditions: ["step1"], effects: [] },
          ],
        }),
      }),
      defaultModel: () => "test-model",
    };

    const result = await reviseSpec({
      currentSpec: {
        description: "Old sim",
        actions: [{ name: "step1", description: "Step", parameters: {}, preconditions: [], effects: [] }],
      },
      feedback: "Add a second step with dependency",
      family: "simulation",
      provider: mockProvider as never,
    });

    expect(result.revised.description).toContain("Better simulation");
    expect(result.changesApplied).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Agent task output revision (build_revision_prompt for judge feedback)
// ---------------------------------------------------------------------------

describe("reviseAgentTaskOutput", () => {
  it("builds a revision prompt from judge feedback", () => {
    const prompt = reviseAgentTaskOutput({
      originalOutput: "My first attempt at the task",
      judgeResult: {
        score: 0.5,
        reasoning: "Missing key details",
        dimensionScores: { completeness: 0.3, accuracy: 0.8 },
      },
      taskPrompt: "Summarize the quarterly report",
      rubric: "Evaluate completeness and accuracy",
    });

    expect(prompt).toContain("My first attempt");
    expect(prompt).toContain("0.5");
    expect(prompt).toContain("Missing key details");
    expect(prompt).toContain("completeness");
    expect(prompt).toContain("Summarize the quarterly report");
    expect(prompt).toContain("revised");
  });

  it("includes revision instructions when provided", () => {
    const prompt = reviseAgentTaskOutput({
      originalOutput: "Output",
      judgeResult: { score: 0.6, reasoning: "Ok", dimensionScores: {} },
      taskPrompt: "Task",
      revisionPrompt: "Focus specifically on the data analysis section",
    });

    expect(prompt).toContain("Focus specifically on the data analysis");
  });

  it("highlights weak dimensions below threshold", () => {
    const prompt = reviseAgentTaskOutput({
      originalOutput: "Output",
      judgeResult: {
        score: 0.5,
        reasoning: "Mixed",
        dimensionScores: {
          depth: 0.2,
          clarity: 0.9,
          accuracy: 0.4,
        },
      },
      taskPrompt: "Task",
    });

    // Should highlight depth (0.2) and accuracy (0.4) as weak
    expect(prompt).toContain("depth");
    expect(prompt).toContain("accuracy");
    expect(prompt).toContain("Weak");
  });
});

// ---------------------------------------------------------------------------
// Revision result shape
// ---------------------------------------------------------------------------

describe("RevisionResult shape", () => {
  it("has required fields", async () => {
    const mockProvider = {
      complete: async () => ({
        text: JSON.stringify({ description: "Updated", taskPrompt: "New", rubric: "New rubric" }),
      }),
      defaultModel: () => "test-model",
    };

    const result: RevisionResult = await reviseSpec({
      currentSpec: { description: "Old" },
      feedback: "Change",
      family: "agent_task",
      provider: mockProvider as never,
    });

    expect(result).toHaveProperty("revised");
    expect(result).toHaveProperty("changesApplied");
    expect(result).toHaveProperty("original");
    expect(typeof result.changesApplied).toBe("boolean");
  });
});
