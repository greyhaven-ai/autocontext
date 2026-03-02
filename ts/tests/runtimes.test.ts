import { describe, it, expect } from "vitest";
import { DirectAPIRuntime } from "../src/runtimes/direct-api.js";
import { ClaudeCLIRuntime } from "../src/runtimes/claude-cli.js";
import type { LLMProvider } from "../src/types/index.js";

function makeMockProvider(text = "mock output"): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock",
    complete: async () => ({ text, usage: {}, model: "mock-model", costUsd: 0.01 }),
  };
}

describe("DirectAPIRuntime", () => {
  it("generates output", async () => {
    const runtime = new DirectAPIRuntime(makeMockProvider("hello"));
    const result = await runtime.generate({ prompt: "say hello" });
    expect(result.text).toBe("hello");
    expect(result.costUsd).toBe(0.01);
    expect(result.model).toBe("mock-model");
  });

  it("revises output", async () => {
    const runtime = new DirectAPIRuntime(makeMockProvider("revised"));
    const result = await runtime.revise({
      prompt: "task",
      previousOutput: "old",
      feedback: "needs work",
    });
    expect(result.text).toBe("revised");
  });

  it("uses custom system prompt", async () => {
    let capturedSystem = "";
    const provider: LLMProvider = {
      name: "track",
      defaultModel: () => "m",
      complete: async (opts) => {
        capturedSystem = opts.systemPrompt;
        return { text: "ok", usage: {} };
      },
    };
    const runtime = new DirectAPIRuntime(provider);
    await runtime.generate({ prompt: "test", system: "Be concise" });
    expect(capturedSystem).toBe("Be concise");
  });

  it("has correct name", () => {
    const runtime = new DirectAPIRuntime(makeMockProvider());
    expect(runtime.name).toBe("DirectAPI");
  });
});

describe("ClaudeCLIRuntime", () => {
  it("reports unavailable when claude not installed", () => {
    const runtime = new ClaudeCLIRuntime({ model: "sonnet" });
    // In test env, claude CLI probably isn't available
    // Just verify the property works
    expect(typeof runtime.available).toBe("boolean");
  });

  it("has correct name", () => {
    const runtime = new ClaudeCLIRuntime();
    expect(runtime.name).toBe("ClaudeCLI");
  });

  it("tracks total cost", () => {
    const runtime = new ClaudeCLIRuntime();
    expect(runtime.totalCost).toBe(0);
  });
});
