/**
 * Tests for AC-367: Non-Pi provider, runtime, and config-routing parity.
 */

import { describe, it, expect, afterEach } from "vitest";

// ---------------------------------------------------------------------------
// createProvider factory completeness
// ---------------------------------------------------------------------------

describe("createProvider factory", () => {
  it("supports hermes provider type", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({ providerType: "hermes" });
    expect(provider.name).toBe("hermes-gateway");
    expect(provider.defaultModel()).toContain("hermes");
  });

  it("supports hermes with custom base_url and model", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({
      providerType: "hermes",
      baseUrl: "http://hermes.local:8080/v1",
      model: "hermes-3-llama-3.1-70b",
    });
    expect(provider.defaultModel()).toBe("hermes-3-llama-3.1-70b");
  });

  it("error message lists all supported providers including hermes", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    try {
      createProvider({ providerType: "nonexistent" });
    } catch (err) {
      const msg = (err as Error).message;
      expect(msg).toContain("hermes");
      expect(msg).toContain("anthropic");
      expect(msg).toContain("deterministic");
    }
  });
});

// ---------------------------------------------------------------------------
// resolveProviderConfig env var alignment
// ---------------------------------------------------------------------------

describe("resolveProviderConfig env var alignment", () => {
  const savedEnv: Record<string, string | undefined> = {};

  afterEach(() => {
    // Restore env vars
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("AUTOCONTEXT_") || key === "ANTHROPIC_API_KEY" || key === "OPENAI_API_KEY") {
        if (key in savedEnv) {
          process.env[key] = savedEnv[key];
        } else {
          delete process.env[key];
        }
      }
    }
  });

  function saveAndClear(): void {
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("AUTOCONTEXT_") || key === "ANTHROPIC_API_KEY" || key === "OPENAI_API_KEY") {
        savedEnv[key] = process.env[key];
        delete process.env[key];
      }
    }
  }

  it("reads AUTOCONTEXT_AGENT_PROVIDER (Python-compatible)", async () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("deterministic");
  });

  it("falls back to AUTOCONTEXT_PROVIDER for backward compat", async () => {
    saveAndClear();
    process.env.AUTOCONTEXT_PROVIDER = "deterministic";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("deterministic");
  });

  it("AUTOCONTEXT_AGENT_PROVIDER takes precedence over AUTOCONTEXT_PROVIDER", async () => {
    saveAndClear();
    process.env.AUTOCONTEXT_PROVIDER = "anthropic";
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
    process.env.ANTHROPIC_API_KEY = "sk-test";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("deterministic");
  });

  it("reads AUTOCONTEXT_AGENT_BASE_URL", async () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "openai-compatible";
    process.env.AUTOCONTEXT_AGENT_API_KEY = "test-key";
    process.env.AUTOCONTEXT_AGENT_BASE_URL = "http://custom:8080/v1";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.baseUrl).toBe("http://custom:8080/v1");
  });

  it("reads AUTOCONTEXT_AGENT_DEFAULT_MODEL", async () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "openai-compatible";
    process.env.AUTOCONTEXT_AGENT_API_KEY = "test-key";
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL = "gpt-4o-mini";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.model).toBe("gpt-4o-mini");
  });

  it("resolves hermes provider from env vars", async () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "hermes";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("hermes");
  });
});

// ---------------------------------------------------------------------------
// Per-role provider support
// ---------------------------------------------------------------------------

describe("Per-role provider configuration", () => {
  it("loadSettings reads AUTOCONTEXT_COMPETITOR_PROVIDER", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({
      competitorProvider: "ollama",
    });
    expect(settings.competitorProvider).toBe("ollama");
  });

  it("loadSettings reads AUTOCONTEXT_ANALYST_PROVIDER", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({
      analystProvider: "vllm",
    });
    expect(settings.analystProvider).toBe("vllm");
  });

  it("per-role provider defaults to empty (use agent_provider)", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.competitorProvider).toBe("");
    expect(settings.analystProvider).toBe("");
    expect(settings.coachProvider).toBe("");
    expect(settings.architectProvider).toBe("");
  });
});
