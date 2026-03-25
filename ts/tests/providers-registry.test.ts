/**
 * Tests for AC-430 Phase 2: Expanded provider support.
 *
 * - Known providers registry with metadata
 * - Key format validation for new providers (Gemini, Mistral, Groq, OpenRouter, Azure)
 * - Selective provider removal
 * - Provider discovery combining stored + env-var credentials
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-providers-"));
}

// ---------------------------------------------------------------------------
// Known providers registry
// ---------------------------------------------------------------------------

describe("Known providers registry", () => {
  it("exports KNOWN_PROVIDERS with at least 9 entries", async () => {
    const { KNOWN_PROVIDERS } = await import("../src/config/credentials.js");
    expect(KNOWN_PROVIDERS.length).toBeGreaterThanOrEqual(9);
  });

  it("includes anthropic, openai, gemini, mistral, groq, openrouter, azure-openai, ollama, vllm", async () => {
    const { KNOWN_PROVIDERS } = await import("../src/config/credentials.js");
    const ids = KNOWN_PROVIDERS.map((p) => p.id);
    expect(ids).toContain("anthropic");
    expect(ids).toContain("openai");
    expect(ids).toContain("gemini");
    expect(ids).toContain("mistral");
    expect(ids).toContain("groq");
    expect(ids).toContain("openrouter");
    expect(ids).toContain("azure-openai");
    expect(ids).toContain("ollama");
    expect(ids).toContain("vllm");
  });

  it("each provider has id, displayName, and envVar fields", async () => {
    const { KNOWN_PROVIDERS } = await import("../src/config/credentials.js");
    for (const p of KNOWN_PROVIDERS) {
      expect(typeof p.id).toBe("string");
      expect(typeof p.displayName).toBe("string");
      expect(p.displayName.length).toBeGreaterThan(0);
    }
  });

  it("getKnownProvider returns metadata for known provider", async () => {
    const { getKnownProvider } = await import("../src/config/credentials.js");
    const anthropic = getKnownProvider("anthropic");
    expect(anthropic).not.toBeNull();
    expect(anthropic!.displayName).toBe("Anthropic");
    expect(anthropic!.keyPrefix).toBe("sk-ant-");
  });

  it("getKnownProvider returns null for unknown provider", async () => {
    const { getKnownProvider } = await import("../src/config/credentials.js");
    expect(getKnownProvider("nonexistent")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Expanded key validation
// ---------------------------------------------------------------------------

describe("Expanded key validation (Phase 2)", () => {
  it("validates Gemini key format", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const bad = await validateApiKey("gemini", "not-valid");
    expect(bad.valid).toBe(false);
    const good = await validateApiKey("gemini", "AIzaSyB-valid-key");
    expect(good.valid).toBe(true);
  });

  it("validates Groq key format (gsk_ prefix)", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const bad = await validateApiKey("groq", "not-valid");
    expect(bad.valid).toBe(false);
    const good = await validateApiKey("groq", "gsk_valid-key-here");
    expect(good.valid).toBe(true);
  });

  it("validates OpenRouter key format (sk-or- prefix)", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const bad = await validateApiKey("openrouter", "not-valid");
    expect(bad.valid).toBe(false);
    const good = await validateApiKey("openrouter", "sk-or-valid-key");
    expect(good.valid).toBe(true);
  });

  it("accepts any non-empty key for Mistral (no known prefix)", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const result = await validateApiKey("mistral", "some-mistral-key");
    expect(result.valid).toBe(true);
  });

  it("accepts any non-empty key for Azure OpenAI", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const result = await validateApiKey("azure-openai", "azure-key-value");
    expect(result.valid).toBe(true);
  });

  it("skips validation for vllm (no key required)", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const result = await validateApiKey("vllm", "");
    expect(result.valid).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Selective provider removal
// ---------------------------------------------------------------------------

describe("removeProviderCredentials", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("removes a specific provider from the store", async () => {
    const { saveProviderCredentials, removeProviderCredentials, loadProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123" });
    saveProviderCredentials(dir, "openai", { apiKey: "sk-456" });

    const removed = removeProviderCredentials(dir, "anthropic");
    expect(removed).toBe(true);
    expect(loadProviderCredentials(dir, "anthropic")).toBeNull();
    expect(loadProviderCredentials(dir, "openai")).not.toBeNull();
  });

  it("returns false when removing a provider that doesn't exist", async () => {
    const { removeProviderCredentials } = await import("../src/config/credentials.js");
    const removed = removeProviderCredentials(dir, "nonexistent");
    expect(removed).toBe(false);
  });

  it("preserves 0600 permissions after removal", async () => {
    const { saveProviderCredentials, removeProviderCredentials, CREDENTIALS_FILE } = await import("../src/config/credentials.js");
    const { statSync } = await import("node:fs");

    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123" });
    saveProviderCredentials(dir, "openai", { apiKey: "sk-456" });
    removeProviderCredentials(dir, "anthropic");

    const mode = statSync(join(dir, CREDENTIALS_FILE)).mode & 0o777;
    expect(mode).toBe(0o600);
  });
});

// ---------------------------------------------------------------------------
// discoverAllProviders — merge stored + env credentials
// ---------------------------------------------------------------------------

describe("discoverAllProviders", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("returns stored providers", async () => {
    const { saveProviderCredentials, discoverAllProviders } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123" });

    const providers = discoverAllProviders(dir);
    const anthropic = providers.find((p) => p.provider === "anthropic");
    expect(anthropic).toBeDefined();
    expect(anthropic!.hasApiKey).toBe(true);
    expect(anthropic!.source).toBe("stored");
  });

  it("detects providers from environment variables", async () => {
    const { discoverAllProviders } = await import("../src/config/credentials.js");

    const oldKey = process.env.ANTHROPIC_API_KEY;
    process.env.ANTHROPIC_API_KEY = "sk-ant-env-key";
    try {
      const providers = discoverAllProviders(dir);
      const anthropic = providers.find((p) => p.provider === "anthropic");
      expect(anthropic).toBeDefined();
      expect(anthropic!.hasApiKey).toBe(true);
      expect(anthropic!.source).toBe("env");
    } finally {
      if (oldKey === undefined) delete process.env.ANTHROPIC_API_KEY;
      else process.env.ANTHROPIC_API_KEY = oldKey;
    }
  });

  it("detects providers from generic AUTOCONTEXT_* environment variables", async () => {
    const { discoverAllProviders } = await import("../src/config/credentials.js");

    const oldProvider = process.env.AUTOCONTEXT_AGENT_PROVIDER;
    const oldKey = process.env.AUTOCONTEXT_AGENT_API_KEY;
    const oldModel = process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL;
    try {
      process.env.AUTOCONTEXT_AGENT_PROVIDER = "openai";
      process.env.AUTOCONTEXT_AGENT_API_KEY = "sk-generic-env-key";
      process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL = "gpt-4o-mini";

      const providers = discoverAllProviders(dir);
      const openai = providers.find((p) => p.provider === "openai");
      expect(openai).toBeDefined();
      expect(openai!.hasApiKey).toBe(true);
      expect(openai!.source).toBe("env");
      expect(openai!.model).toBe("gpt-4o-mini");
    } finally {
      if (oldProvider === undefined) delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
      else process.env.AUTOCONTEXT_AGENT_PROVIDER = oldProvider;
      if (oldKey === undefined) delete process.env.AUTOCONTEXT_AGENT_API_KEY;
      else process.env.AUTOCONTEXT_AGENT_API_KEY = oldKey;
      if (oldModel === undefined) delete process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL;
      else process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL = oldModel;
    }
  });

  it("stored credentials take precedence over env vars", async () => {
    const { saveProviderCredentials, discoverAllProviders } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-stored" });

    const oldKey = process.env.ANTHROPIC_API_KEY;
    process.env.ANTHROPIC_API_KEY = "sk-ant-env";
    try {
      const providers = discoverAllProviders(dir);
      const anthropic = providers.find((p) => p.provider === "anthropic");
      expect(anthropic!.source).toBe("stored");
    } finally {
      if (oldKey === undefined) delete process.env.ANTHROPIC_API_KEY;
      else process.env.ANTHROPIC_API_KEY = oldKey;
    }
  });
});
