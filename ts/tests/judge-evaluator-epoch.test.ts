import { describe, it, expect } from "vitest";
import { LLMJudge } from "../src/judge/index.js";
import { computeEvaluatorEpoch } from "../src/judge/evaluator-epoch.js";
import { HookBus, HookEvents } from "../src/extensions/index.js";
import type { LLMProvider } from "../src/types/index.js";

function makeMockProvider(response: string): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock-model",
    complete: async () => ({ text: response, usage: {} }),
  };
}

function makeRecordingProvider(response: string, modelsUsed: string[]): LLMProvider {
  return {
    name: "recording",
    defaultModel: () => "ctor-model",
    complete: async (opts) => {
      modelsUsed.push(opts.model);
      return { text: response, model: opts.model, usage: {} };
    },
  };
}

const RESPONSE =
  '<!-- JUDGE_RESULT_START -->\n{"score": 0.8, "reasoning": "ok"}\n<!-- JUDGE_RESULT_END -->';

describe("LLMJudge evaluatorEpoch", () => {
  it("stamps the judge result with the evaluator epoch", async () => {
    const provider = makeMockProvider(RESPONSE);
    const judge = new LLMJudge({
      provider,
      model: "claude-sonnet-4-5",
      rubric: "score correctness 0-1",
    });
    const result = await judge.evaluate({ taskPrompt: "task", agentOutput: "output" });
    const expected = computeEvaluatorEpoch(
      "score correctness 0-1",
      "mock",
      "claude-sonnet-4-5",
    ).epochId;
    expect(result.evaluatorEpoch).toBe(expected);
  });

  it("mints a different epoch when the rubric changes", async () => {
    const provider = makeMockProvider(RESPONSE);
    const j1 = new LLMJudge({ provider, model: "claude-sonnet-4-5", rubric: "rubric one" });
    const j2 = new LLMJudge({ provider, model: "claude-sonnet-4-5", rubric: "rubric two" });
    const r1 = await j1.evaluate({ taskPrompt: "t", agentOutput: "o" });
    const r2 = await j2.evaluate({ taskPrompt: "t", agentOutput: "o" });
    expect(r1.evaluatorEpoch).not.toBe(r2.evaluatorEpoch);
  });

  it("stamps the epoch of the model a BEFORE_JUDGE hook switched to", async () => {
    const modelsUsed: string[] = [];
    const provider = makeRecordingProvider(RESPONSE, modelsUsed);
    const bus = new HookBus();
    bus.on(HookEvents.BEFORE_JUDGE, () => ({ model: "hook-model" }));

    const judge = new LLMJudge({
      provider,
      model: "ctor-model",
      rubric: "score correctness 0-1",
      hookBus: bus,
    });
    const result = await judge.evaluate({ taskPrompt: "task", agentOutput: "output" });

    expect(modelsUsed).toEqual(["hook-model"]);
    const expected = computeEvaluatorEpoch(
      "score correctness 0-1",
      "recording",
      "hook-model",
    ).epochId;
    const ctorEpoch = computeEvaluatorEpoch(
      "score correctness 0-1",
      "recording",
      "ctor-model",
    ).epochId;
    expect(result.evaluatorEpoch).toBe(expected);
    expect(result.evaluatorEpoch).not.toBe(ctorEpoch);
  });

  it("stamps the ctor-model epoch when no hook changes the model", async () => {
    const modelsUsed: string[] = [];
    const provider = makeRecordingProvider(RESPONSE, modelsUsed);
    const judge = new LLMJudge({ provider, model: "ctor-model", rubric: "score correctness 0-1" });
    const result = await judge.evaluate({ taskPrompt: "task", agentOutput: "output" });

    expect(modelsUsed).toEqual(["ctor-model"]);
    const expected = computeEvaluatorEpoch(
      "score correctness 0-1",
      "recording",
      "ctor-model",
    ).epochId;
    expect(result.evaluatorEpoch).toBe(expected);
  });
});
