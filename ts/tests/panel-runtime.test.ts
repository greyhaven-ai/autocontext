import { describe, expect, it } from "vitest";

import type { LLMProvider } from "../src/types/index.js";
import {
  PanelProvider,
  comparePanelBenchmark,
  parsePanelConfigForRole,
} from "../src/providers/index.js";

function provider(name: string): LLMProvider {
  return {
    name,
    defaultModel: () => `${name}-default`,
    complete: async (opts) => ({
      text: `${name}:${opts.model ?? `${name}-default`}`,
      model: opts.model ?? `${name}-default`,
      usage: { input_tokens: 3, output_tokens: 2 },
      costUsd: 0.01,
    }),
  };
}

describe("panel runtime", () => {
  it("is opt-in and parses provider/model participants per role", () => {
    expect(parsePanelConfigForRole({}, "competitor")).toBeNull();

    const config = parsePanelConfigForRole(
      {
        panelRoles: "competitor,coach",
        panelParticipants: "competitor=openai:gpt-4.1,anthropic:claude-3;coach=ollama:llama3",
        panelSynthesizerProvider: "anthropic",
        panelSynthesizerModel: "claude-opus",
      },
      "competitor",
    );

    expect(config).toEqual({
      role: "competitor",
      participants: [
        { provider: "openai", model: "gpt-4.1" },
        { provider: "anthropic", model: "claude-3" },
      ],
      synthesizerProvider: "anthropic",
      synthesizerModel: "claude-opus",
    });
  });

  it("preserves final output and participant metadata", async () => {
    const panel = new PanelProvider({
      role: "analyst",
      baseProvider: provider("synth"),
      config: {
        role: "analyst",
        participants: [
          { provider: "openai", model: "gpt-4.1" },
          { provider: "anthropic", model: "claude-3" },
        ],
        synthesizerProvider: "",
        synthesizerModel: "",
      },
      providerFactory: (providerType) => provider(providerType),
    });

    const result = await panel.complete({
      systemPrompt: "",
      userPrompt: "review this strategy",
      model: "fallback-model",
    });

    expect(result.text).toBe("synth:fallback-model");
    expect(result.metadata?.panelRuntime).toBe(true);
    expect(result.metadata?.panelRole).toBe("analyst");
    expect(result.metadata?.panelParticipants).toMatchObject([
      { provider: "openai", model: "gpt-4.1", content: "openai:gpt-4.1" },
      { provider: "anthropic", model: "claude-3", content: "anthropic:claude-3" },
    ]);
    expect(result.costUsd).toBe(0.03);
  });

  it("fails closed when no-tools isolation is requested", () => {
    const panel = new PanelProvider({
      role: "analyst",
      baseProvider: provider("base"),
      config: {
        role: "analyst",
        participants: [{ provider: "runtime", model: "agent" }],
        synthesizerProvider: "",
        synthesizerModel: "",
      },
      providerFactory: () => provider("runtime"),
    });

    expect(() => panel.createIsolatedProvider({ noTools: true })).toThrow(
      /cannot guarantee no-tools isolation/i,
    );
  });

  it("reports benchmark deltas", () => {
    expect(comparePanelBenchmark({
      singleScore: 0.4,
      panelScore: 0.55,
      singleLatencyMs: 100,
      panelLatencyMs: 180,
      singleCostUsd: 0.02,
      panelCostUsd: 0.05,
    })).toEqual({
      scoreDelta: 0.15,
      latencyMsDelta: 80,
      costUsdDelta: 0.03,
      scorePerCostDelta: -9,
    });
  });
});
