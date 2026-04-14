import { afterEach, describe, expect, it } from "vitest";

import { resolveProviderConfig } from "../src/providers/provider-config-resolution.js";

const savedEnv: Record<string, string | undefined> = {};

function saveAndClear(): void {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("AUTOCONTEXT_") || key.endsWith("_API_KEY")) {
      savedEnv[key] = process.env[key];
      delete process.env[key];
    }
  }
}

afterEach(() => {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("AUTOCONTEXT_") || key.endsWith("_API_KEY")) {
      delete process.env[key];
    }
  }
  for (const [key, value] of Object.entries(savedEnv)) {
    if (value !== undefined) {
      process.env[key] = value;
    }
  }
});

describe("provider config resolution workflow", () => {
  it("prefers generic agent env keys over provider-specific keys", () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "openai";
    process.env.AUTOCONTEXT_AGENT_API_KEY = "generic-key";
    process.env.OPENAI_API_KEY = "provider-key";

    expect(resolveProviderConfig()).toMatchObject({
      providerType: "openai",
      apiKey: "generic-key",
    });
  });

  it("uses provider-specific env defaults for compat providers", () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "gemini";
    process.env.GEMINI_API_KEY = "gem-key";

    expect(resolveProviderConfig()).toMatchObject({
      providerType: "gemini",
      apiKey: "gem-key",
    });
  });

  it("keeps AUTOCONTEXT_ANTHROPIC_API_KEY as an Anthropic compatibility alias", () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "anthropic";
    process.env.AUTOCONTEXT_ANTHROPIC_API_KEY = "sk-compat-key";

    expect(resolveProviderConfig()).toMatchObject({
      providerType: "anthropic",
      apiKey: "sk-compat-key",
    });
  });

  it("preserves keyless provider families and anthropic guardrails", () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "pi";
    expect(resolveProviderConfig()).toMatchObject({ providerType: "pi" });

    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "anthropic";
    expect(() => resolveProviderConfig()).toThrow("ANTHROPIC_API_KEY");
  });
});
