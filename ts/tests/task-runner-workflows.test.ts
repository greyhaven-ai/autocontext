import { describe, expect, it, vi } from "vitest";

import {
  buildSimpleAgentTaskRevisionPrompt,
  evaluateSimpleAgentTaskOutput,
} from "../src/execution/simple-agent-task-workflow.js";
import {
  buildTaskRunnerModel,
  dequeueTaskBatch,
} from "../src/execution/task-runner-loop-workflow.js";

describe("task runner workflows", () => {
  it("builds task runner model defaults and dequeues up to the requested batch size", () => {
    expect(buildTaskRunnerModel("provider-default")).toBe("provider-default");
    expect(buildTaskRunnerModel("provider-default", "explicit-model")).toBe("explicit-model");

    const dequeued = [{ id: "t1" }, { id: "t2" }, null] as const;
    let index = 0;
    const store = {
      dequeueTask: vi.fn(() => dequeued[index++] ?? null),
    } as never;

    expect(dequeueTaskBatch(store, 5)).toEqual([{ id: "t1" }, { id: "t2" }]);
  });

  it("builds revision prompts and normalizes judge results", async () => {
    const prompt = buildSimpleAgentTaskRevisionPrompt({
      output: "Draft answer",
      judgeResult: { score: 0.45, reasoning: "Need more detail", dimensionScores: {}, internalRetries: 0 },
      taskPrompt: "Summarize the outage",
      revisionPrompt: "Add owner and severity.",
    });

    expect(prompt).toContain("Add owner and severity.");
    expect(prompt).toContain("## Judge Score: 0.45");
    expect(prompt).toContain("Summarize the outage");

    const result = await evaluateSimpleAgentTaskOutput({
      taskPrompt: "Summarize the outage",
      rubric: "Be complete",
      provider: {
        name: "unused",
        defaultModel: () => "unused",
        complete: vi.fn(),
      } as never,
      model: "mock-model",
      output: "Answer",
      judgeOverride: {
        evaluate: vi.fn(async () => ({
          score: 0.9,
          reasoning: "Great",
          dimensionScores: { quality: 0.9 },
          internalRetries: 0,
        })),
      } as never,
    });

    expect(result).toEqual({
      score: 0.9,
      reasoning: "Great",
      dimensionScores: { quality: 0.9 },
      internalRetries: 0,
    });
  });
});
