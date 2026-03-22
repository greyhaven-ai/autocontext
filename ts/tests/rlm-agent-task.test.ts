import { describe, it, expect } from "vitest";
import type { LLMProvider } from "../src/types/index.js";
import { runAgentTaskRlmSession } from "../src/rlm/index.js";

function makeProvider(response: string): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock-model",
    complete: async () => ({
      text: response,
      model: "mock-model",
      usage: {},
    }),
  };
}

describe("runAgentTaskRlmSession", () => {
  it("runs a generate session and returns final content", async () => {
    const result = await runAgentTaskRlmSession({
      provider: makeProvider('<code>answer.ready = true;\nanswer.content = "RLM final answer";</code>'),
      model: "mock-model",
      config: {
        enabled: true,
        maxTurns: 2,
        maxTokensPerTurn: 512,
        temperature: 0.1,
        maxStdoutChars: 4096,
        codeTimeoutMs: 5000,
        memoryLimitMb: 64,
      },
      phase: "generate",
      taskPrompt: "Explain testing.",
      rubric: "Be clear.",
    });

    expect(result.error).toBeNull();
    expect(result.content).toBe("RLM final answer");
    expect(result.turnsUsed).toBe(1);
  });
});
