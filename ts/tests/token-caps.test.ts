/**
 * AC-905: model-aware output-token clamp.
 * Mirrors Python's tests/test_output_token_budgets.py TestClampOutputTokens.
 */

import { describe, it, expect } from "vitest";
import { clampOutputTokens } from "../src/providers/token-caps.js";

describe("clampOutputTokens", () => {
  it("clamps a known-capped model", () => {
    expect(clampOutputTokens(100_000, "claude-3-haiku-20240307")).toBe(4096);
  });

  it("passes requested below the cap", () => {
    expect(clampOutputTokens(2000, "claude-3-haiku-20240307")).toBe(2000);
  });

  it("longest prefix wins", () => {
    expect(clampOutputTokens(100_000, "claude-3-5-sonnet-20241022")).toBe(8192);
  });

  it("knows current-generation hard caps", () => {
    expect(clampOutputTokens(200_000, "claude-sonnet-5")).toBe(128_000);
    expect(clampOutputTokens(200_000, "gpt-5.6-terra")).toBe(128_000);
    expect(clampOutputTokens(200_000, "openai/gpt-5.6-terra")).toBe(128_000);
  });

  it("unknown and absent models pass through", () => {
    expect(clampOutputTokens(100_000, "future-model-9000")).toBe(100_000);
    expect(clampOutputTokens(5000, undefined)).toBe(5000);
  });
});

describe("provider-factory applies the clamp (AC-905)", () => {
  it("anthropic path clamps the effective max_tokens", async () => {
    const { createProvider } = await import("../src/providers/provider-factory.js");
    const bodies: Array<Record<string, unknown>> = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (_url: unknown, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return new Response(
        JSON.stringify({
          content: [{ type: "text", text: "ok" }],
          model: "claude-3-haiku-20240307",
          usage: { input_tokens: 1, output_tokens: 1 },
        }),
        { status: 200 },
      );
    }) as typeof fetch;
    try {
      const provider = createProvider({
        providerType: "anthropic",
        apiKey: "test-key",
        model: "claude-3-haiku-20240307",
      });
      await provider.complete({
        systemPrompt: "s",
        userPrompt: "u",
        maxTokens: 100_000,
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
    expect(bodies[0].max_tokens).toBe(4096);
  });
});
