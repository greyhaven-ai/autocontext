/**
 * Tests for AC-232: OpenAI-compatible provider support in the TypeScript CLI.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// ---------------------------------------------------------------------------
// 1. Anthropic Provider
// ---------------------------------------------------------------------------

describe("AnthropicProvider", () => {
  it("should implement LLMProvider interface", async () => {
    const { createAnthropicProvider } = await import("../src/providers/index.js");
    const provider = createAnthropicProvider({ apiKey: "test-key" });
    expect(provider.name).toBe("anthropic");
    expect(typeof provider.defaultModel).toBe("function");
    expect(typeof provider.complete).toBe("function");
  });

  it("should use default model when none specified", async () => {
    const { createAnthropicProvider } = await import("../src/providers/index.js");
    const provider = createAnthropicProvider({ apiKey: "test-key" });
    expect(provider.defaultModel()).toBe("claude-sonnet-4-20250514");
  });

  it("should use custom model when specified", async () => {
    const { createAnthropicProvider } = await import("../src/providers/index.js");
    const provider = createAnthropicProvider({ apiKey: "test-key", model: "claude-haiku-4-5-20251001" });
    expect(provider.defaultModel()).toBe("claude-haiku-4-5-20251001");
  });

  it("AC-298: should fall back to default model when empty string passed", async () => {
    const { createAnthropicProvider } = await import("../src/providers/index.js");
    const provider = createAnthropicProvider({ apiKey: "test-key", model: "" });
    expect(provider.defaultModel()).toBe("claude-sonnet-4-20250514");
    expect(provider.defaultModel().length).toBeGreaterThan(0);
  });

  it("AC-298: should send non-empty model to API even when callOpts.model is empty", async () => {
    const { createAnthropicProvider } = await import("../src/providers/index.js");
    const provider = createAnthropicProvider({ apiKey: "test-key" });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        content: [{ type: "text", text: "ok" }],
        model: "claude-sonnet-4-20250514",
        usage: { input_tokens: 1, output_tokens: 1 },
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await provider.complete({ systemPrompt: "s", userPrompt: "u", model: "" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.model.length).toBeGreaterThan(0);
    expect(body.model).toBe("claude-sonnet-4-20250514");

    vi.unstubAllGlobals();
  });

  it("should call Anthropic API with correct headers", async () => {
    const { createAnthropicProvider } = await import("../src/providers/index.js");
    const provider = createAnthropicProvider({ apiKey: "sk-ant-test" });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        content: [{ type: "text", text: "Hello" }],
        model: "claude-sonnet-4-20250514",
        usage: { input_tokens: 10, output_tokens: 5 },
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await provider.complete({ systemPrompt: "system", userPrompt: "hello" });

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("https://api.anthropic.com/v1/messages");
    expect(opts.headers["x-api-key"]).toBe("sk-ant-test");
    expect(opts.headers["anthropic-version"]).toBe("2023-06-01");

    vi.unstubAllGlobals();
  });

  it("should parse Anthropic response correctly", async () => {
    const { createAnthropicProvider } = await import("../src/providers/index.js");
    const provider = createAnthropicProvider({ apiKey: "test-key" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        content: [{ type: "text", text: "Test response" }],
        model: "claude-sonnet-4-20250514",
        usage: { input_tokens: 15, output_tokens: 8 },
      }),
    }));

    const result = await provider.complete({ systemPrompt: "sys", userPrompt: "test" });
    expect(result.text).toBe("Test response");
    expect(result.model).toBe("claude-sonnet-4-20250514");
    expect(result.usage).toEqual({ input: 15, output: 8 });

    vi.unstubAllGlobals();
  });

  it("should throw ProviderError on API failure", async () => {
    const { createAnthropicProvider } = await import("../src/providers/index.js");
    const provider = createAnthropicProvider({ apiKey: "test-key" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      text: async () => "Unauthorized",
    }));

    await expect(
      provider.complete({ systemPrompt: "sys", userPrompt: "test" }),
    ).rejects.toThrow("Anthropic API error 401");

    vi.unstubAllGlobals();
  });
});

// ---------------------------------------------------------------------------
// 2. OpenAI-Compatible Provider
// ---------------------------------------------------------------------------

describe("OpenAICompatibleProvider", () => {
  it("should implement LLMProvider interface", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({ apiKey: "test-key" });
    expect(provider.name).toBe("openai-compatible");
    expect(typeof provider.defaultModel).toBe("function");
    expect(typeof provider.complete).toBe("function");
  });

  it("should use default model gpt-4o when none specified", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({ apiKey: "test-key" });
    expect(provider.defaultModel()).toBe("gpt-4o");
  });

  it("should use custom model and base URL", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({
      apiKey: "test-key",
      model: "llama3.1",
      baseUrl: "http://localhost:11434/v1",
    });
    expect(provider.defaultModel()).toBe("llama3.1");
  });

  it("AC-298: should fall back to default model when empty string passed", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({ apiKey: "test-key", model: "" });
    expect(provider.defaultModel()).toBe("gpt-4o");
  });

  it("should call OpenAI chat completions endpoint", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({
      apiKey: "sk-test",
      baseUrl: "https://api.openai.com/v1",
    });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        choices: [{ message: { content: "Hello from GPT" } }],
        model: "gpt-4o",
        usage: { prompt_tokens: 10, completion_tokens: 5 },
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await provider.complete({ systemPrompt: "system", userPrompt: "hello" });

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("https://api.openai.com/v1/chat/completions");
    const body = JSON.parse(opts.body);
    expect(body.messages).toEqual([
      { role: "system", content: "system" },
      { role: "user", content: "hello" },
    ]);
    expect(opts.headers["Authorization"]).toBe("Bearer sk-test");

    vi.unstubAllGlobals();
  });

  it("should parse OpenAI response correctly", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({ apiKey: "test-key" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        choices: [{ message: { content: "GPT response" } }],
        model: "gpt-4o",
        usage: { prompt_tokens: 20, completion_tokens: 10 },
      }),
    }));

    const result = await provider.complete({ systemPrompt: "sys", userPrompt: "test" });
    expect(result.text).toBe("GPT response");
    expect(result.model).toBe("gpt-4o");
    expect(result.usage).toEqual({ input: 20, output: 10 });

    vi.unstubAllGlobals();
  });

  it("should throw ProviderError on API failure", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({ apiKey: "test-key" });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      text: async () => "Rate limited",
    }));

    await expect(
      provider.complete({ systemPrompt: "sys", userPrompt: "test" }),
    ).rejects.toThrow("OpenAI API error 429");

    vi.unstubAllGlobals();
  });

  it("should default base URL to OpenAI when not specified", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({ apiKey: "sk-test" });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        choices: [{ message: { content: "ok" } }],
        model: "gpt-4o",
        usage: { prompt_tokens: 1, completion_tokens: 1 },
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await provider.complete({ systemPrompt: "s", userPrompt: "u" });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe("https://api.openai.com/v1/chat/completions");

    vi.unstubAllGlobals();
  });

  it("should strip trailing slash from base URL", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({
      apiKey: "sk-test",
      baseUrl: "http://localhost:8000/v1/",
    });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        choices: [{ message: { content: "ok" } }],
        model: "local",
        usage: { prompt_tokens: 1, completion_tokens: 1 },
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await provider.complete({ systemPrompt: "s", userPrompt: "u" });

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe("http://localhost:8000/v1/chat/completions");

    vi.unstubAllGlobals();
  });

  it("should pass model override from complete() opts", async () => {
    const { createOpenAICompatibleProvider } = await import("../src/providers/index.js");
    const provider = createOpenAICompatibleProvider({
      apiKey: "test-key",
      model: "gpt-4o",
    });

    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        choices: [{ message: { content: "ok" } }],
        model: "gpt-4o-mini",
        usage: { prompt_tokens: 1, completion_tokens: 1 },
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await provider.complete({ systemPrompt: "s", userPrompt: "u", model: "gpt-4o-mini" });

    const body = JSON.parse(mockFetch.mock.calls[0][1].body);
    expect(body.model).toBe("gpt-4o-mini");

    vi.unstubAllGlobals();
  });
});

// ---------------------------------------------------------------------------
// 3. Provider Factory
// ---------------------------------------------------------------------------

describe("createProvider", () => {
  it("should create anthropic provider by default", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({ providerType: "anthropic", apiKey: "test-key" });
    expect(provider.name).toBe("anthropic");
  });

  it("should create openai-compatible provider", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({
      providerType: "openai-compatible",
      apiKey: "test-key",
      baseUrl: "https://api.openai.com/v1",
    });
    expect(provider.name).toBe("openai-compatible");
  });

  it("should create openai provider (alias for openai-compatible)", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({ providerType: "openai", apiKey: "test-key" });
    expect(provider.name).toBe("openai-compatible");
  });

  it("should create ollama provider with default base URL", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({ providerType: "ollama" });
    expect(provider.name).toBe("openai-compatible");
    expect(provider.defaultModel()).toBe("llama3.1");
  });

  it("should create vllm provider with default base URL", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({ providerType: "vllm" });
    expect(provider.name).toBe("openai-compatible");
    expect(provider.defaultModel()).toBe("default");
  });

  it("should throw ProviderError for unknown type", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    expect(() => createProvider({ providerType: "unknown" as any })).toThrow("Unknown provider type");
  });

  it("should pass model through to provider", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({
      providerType: "openai-compatible",
      apiKey: "k",
      model: "my-custom-model",
    });
    expect(provider.defaultModel()).toBe("my-custom-model");
  });
});

// ---------------------------------------------------------------------------
// 4. CLI getProvider integration (env var routing)
// ---------------------------------------------------------------------------

describe("CLI provider routing", () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    // Reset env for each test
    delete process.env.AUTOCONTEXT_PROVIDER;
    delete process.env.AUTOCONTEXT_BASE_URL;
    delete process.env.AUTOCONTEXT_API_KEY;
    delete process.env.OPENAI_API_KEY;
    delete process.env.ANTHROPIC_API_KEY;
    delete process.env.AUTOCONTEXT_MODEL;
  });

  afterEach(() => {
    // Restore original env
    process.env = { ...originalEnv };
  });

  it("should default to anthropic when AUTOCONTEXT_PROVIDER is unset", async () => {
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    process.env.ANTHROPIC_API_KEY = "sk-ant-test";

    const config = resolveProviderConfig();
    expect(config.providerType).toBe("anthropic");
    expect(config.apiKey).toBe("sk-ant-test");
  });

  it("should use AUTOCONTEXT_PROVIDER to select openai-compatible", async () => {
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    process.env.AUTOCONTEXT_PROVIDER = "openai-compatible";
    process.env.OPENAI_API_KEY = "sk-openai-test";

    const config = resolveProviderConfig();
    expect(config.providerType).toBe("openai-compatible");
    expect(config.apiKey).toBe("sk-openai-test");
  });

  it("should prefer AUTOCONTEXT_API_KEY over provider-specific keys", async () => {
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    process.env.AUTOCONTEXT_PROVIDER = "openai";
    process.env.AUTOCONTEXT_API_KEY = "generic-key";
    process.env.OPENAI_API_KEY = "specific-key";

    const config = resolveProviderConfig();
    expect(config.apiKey).toBe("generic-key");
  });

  it("should use AUTOCONTEXT_BASE_URL for custom endpoints", async () => {
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    process.env.AUTOCONTEXT_PROVIDER = "openai-compatible";
    process.env.AUTOCONTEXT_BASE_URL = "https://openrouter.ai/api/v1";
    process.env.AUTOCONTEXT_API_KEY = "key";

    const config = resolveProviderConfig();
    expect(config.baseUrl).toBe("https://openrouter.ai/api/v1");
  });

  it("should use AUTOCONTEXT_MODEL for model override", async () => {
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    process.env.AUTOCONTEXT_PROVIDER = "openai";
    process.env.AUTOCONTEXT_MODEL = "gpt-4o-mini";
    process.env.OPENAI_API_KEY = "key";

    const config = resolveProviderConfig();
    expect(config.model).toBe("gpt-4o-mini");
  });

  it("should error when anthropic selected but no API key", async () => {
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    process.env.AUTOCONTEXT_PROVIDER = "anthropic";

    expect(() => resolveProviderConfig()).toThrow("ANTHROPIC_API_KEY");
  });

  it("should error when openai selected but no API key", async () => {
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    process.env.AUTOCONTEXT_PROVIDER = "openai";

    expect(() => resolveProviderConfig()).toThrow("API key");
  });

  it("should not require API key for ollama", async () => {
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    process.env.AUTOCONTEXT_PROVIDER = "ollama";

    const config = resolveProviderConfig();
    expect(config.providerType).toBe("ollama");
  });
});
