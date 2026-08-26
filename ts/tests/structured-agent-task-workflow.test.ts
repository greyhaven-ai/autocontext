import { describe, expect, it } from "vitest";

import { createStructuredAgentTaskWorkflow } from "../src/execution/structured-agent-task-workflow.js";
import type { LLMProvider } from "../src/types/index.js";

describe("structured agent-task workflow privacy", () => {
  it("uses and closes a no-tools isolate for saved-task draft generation", async () => {
    const policies: unknown[] = [];
    const prompts: string[] = [];
    let closes = 0;
    const provider: LLMProvider = {
      name: "saved-tool-runtime",
      defaultModel: () => "model",
      complete: async () => {
        throw new Error("base provider must not generate an evaluator-bearing draft");
      },
      createIsolatedProvider: (policy) => {
        policies.push(policy);
        return {
          name: "saved-no-tools-isolate",
          defaultModel: () => "model",
          complete: async (request) => {
            prompts.push(request.userPrompt);
            return { text: "Saved isolated draft", usage: {}, model: "model" };
          },
          close: () => {
            closes += 1;
          },
        };
      },
    };
    const task = createStructuredAgentTaskWorkflow({
      name: "saved_private_task",
      provider,
      spec: {
        improvementTaskContractVersion: 1,
        taskPrompt: "Create the saved candidate draft.",
        judgeRubric: "Evaluate the saved draft.",
        outputFormat: "free_text",
        judgeModel: "",
        evaluationContext: "SAVED_PRIVATE_SENTINEL",
        maxRounds: 1,
        qualityThreshold: 0.9,
      },
    });

    await expect(task.generateOutput()).resolves.toBe("Saved isolated draft");
    expect(policies).toEqual([{ noTools: true }]);
    expect(closes).toBe(1);
    expect(prompts).toEqual(["Create the saved candidate draft."]);
    expect(prompts[0]).not.toContain("SAVED_PRIVATE_SENTINEL");
  });

  it("keeps the candidate model separate from a foreign authoritative judge model", async () => {
    const candidateModels: Array<string | undefined> = [];
    const judgeModels: Array<string | undefined> = [];
    const candidateProvider: LLMProvider = {
      name: "candidate-provider",
      defaultModel: () => "candidate-default",
      complete: async (request) => {
        candidateModels.push(request.model);
        if (request.model && !request.model.startsWith("candidate-")) {
          throw new Error(`foreign candidate model: ${request.model}`);
        }
        return { text: "candidate artifact", usage: {} };
      },
    };
    const evaluationProvider: LLMProvider = {
      name: "judge-provider",
      defaultModel: () => "judge-default",
      complete: async (request) => {
        judgeModels.push(request.model);
        if (request.model !== "judge-foreign") {
          throw new Error(`wrong authoritative model: ${String(request.model)}`);
        }
        return {
          text:
            "<!-- JUDGE_RESULT_START -->\n" +
            JSON.stringify({
              score: 0.7,
              reasoning: "Authoritative feedback.",
              dimensions: { quality: 0.7 },
            }) +
            "\n<!-- JUDGE_RESULT_END -->",
          usage: {},
        };
      },
    };
    const task = createStructuredAgentTaskWorkflow({
      name: "cross-provider",
      provider: candidateProvider,
      evaluationProvider,
      model: "candidate-explicit",
      spec: {
        improvementTaskContractVersion: 1,
        taskPrompt: "Produce an artifact.",
        judgeRubric: "Evaluate quality.",
        outputFormat: "free_text",
        judgeModel: "judge-foreign",
        revisionPrompt: "Improve it.",
        maxRounds: 2,
        qualityThreshold: 0.9,
      },
    });

    const draft = await task.generateOutput();
    const result = await task.evaluateOutput(draft, {});
    await task.reviseOutput?.(draft, result, {});

    expect(judgeModels).toEqual(["judge-foreign"]);
    expect(candidateModels).toEqual(["candidate-explicit", undefined]);
  });

  it("rejects invalid structured-v1 JSON immediately after initial generation", async () => {
    const provider: LLMProvider = {
      name: "candidate",
      defaultModel: () => "candidate-model",
      complete: async () => ({ text: "not valid JSON", usage: {} }),
    };
    const task = createStructuredAgentTaskWorkflow({
      name: "invalid-json-initial",
      provider,
      spec: {
        improvementTaskContractVersion: 1,
        taskPrompt: "Return JSON.",
        judgeRubric: "Evaluate JSON.",
        outputFormat: "json_schema",
        judgeModel: "",
        maxRounds: 1,
        qualityThreshold: 0.9,
      },
    });

    await expect(task.generateOutput()).rejects.toThrow(
      /must be valid JSON because output_format is json_schema/,
    );
  });
});
