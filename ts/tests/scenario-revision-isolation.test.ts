import { describe, expect, it, vi } from "vitest";

import { reviseSpec } from "../src/scenarios/scenario-revision.js";
import type { LLMProvider } from "../src/types/index.js";

describe("scenario revision provider isolation", () => {
  it("uses and closes a no-tools provider for private structured task revisions", async () => {
    const sharedComplete = vi.fn(async () => ({ text: "must not run", usage: {} }));
    const isolatedClose = vi.fn();
    const isolatedComplete = vi.fn(async (opts: { userPrompt: string }) => {
      expect(opts.userPrompt).not.toContain("PRIVATE_EVALUATOR_SENTINEL");
      return {
        text: JSON.stringify({
          description: "Revised public description",
          judgeRubric: "Attempted replacement rubric",
        }),
        usage: {},
      };
    });
    const provider: LLMProvider = {
      name: "shared-tool-capable-provider",
      defaultModel: () => "model",
      complete: sharedComplete,
      createIsolatedProvider: (policy) => {
        expect(policy).toEqual({ noTools: true });
        return {
          name: "isolated-no-tools-provider",
          defaultModel: () => "model",
          complete: isolatedComplete,
          close: isolatedClose,
        };
      },
    };

    const result = await reviseSpec({
      family: "agent_task",
      feedback: "Clarify the public description",
      provider,
      currentSpec: {
        improvementTaskContractVersion: 1,
        description: "Original description",
        taskPrompt: "Immutable prompt",
        judgeRubric: "Immutable rubric",
        outputFormat: "free_text",
        judgeModel: "",
        maxRounds: 1,
        qualityThreshold: 0.9,
        evaluationContext: "PRIVATE_EVALUATOR_SENTINEL",
      },
    });

    expect(result.changesApplied).toBe(true);
    expect(result.revised.description).toBe("Revised public description");
    expect(sharedComplete).not.toHaveBeenCalled();
    expect(isolatedComplete).toHaveBeenCalledOnce();
    expect(isolatedClose).toHaveBeenCalledOnce();
  });

  it("fails before shared-provider execution when isolation is unavailable", async () => {
    const complete = vi.fn(async () => ({ text: "must not run", usage: {} }));
    await expect(
      reviseSpec({
        family: "agent_task",
        feedback: "Revise",
        provider: {
          name: "shared-tool-capable-provider",
          defaultModel: () => "model",
          complete,
        },
        currentSpec: {
          improvement_task_contract_version: 1,
          task_prompt: "Immutable prompt",
          judge_rubric: "Immutable rubric",
          output_format: "free_text",
          evaluation_context_ref: `sha256:${"a".repeat(64)}`,
        },
      }),
    ).rejects.toThrow(/cannot guarantee no-tools isolation/i);
    expect(complete).not.toHaveBeenCalled();
  });
});
