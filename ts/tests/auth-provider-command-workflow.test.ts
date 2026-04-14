import { describe, expect, it, vi } from "vitest";

import {
  buildLoginSuccessMessage,
  buildLogoutMessage,
  buildProvidersPayload,
  buildStoredProviderCredentials,
  buildWhoamiPayload,
  LOGIN_HELP_TEXT,
  LOGOUT_HELP_TEXT,
  renderModelsResult,
  resolveLoginCommandRequest,
} from "../src/cli/auth-provider-command-workflow.js";

describe("auth/provider command workflow", () => {
  it("exposes stable login help text", () => {
    expect(LOGIN_HELP_TEXT).toContain("autoctx login");
    expect(LOGIN_HELP_TEXT).toContain("--provider");
    expect(LOGIN_HELP_TEXT).toContain("--key");
    expect(LOGIN_HELP_TEXT.toLowerCase()).toContain("see also");
  });

  it("exposes stable logout help text", () => {
    expect(LOGOUT_HELP_TEXT).toContain("autoctx logout");
    expect(LOGOUT_HELP_TEXT).toContain("--config-dir");
  });

  it("resolves prompted non-ollama login requests", async () => {
    const promptForValue = vi
      .fn(async (_label: string) => "")
      .mockResolvedValueOnce("Anthropic")
      .mockResolvedValueOnce("sk-test");
    const validateOllamaConnection = vi.fn();

    await expect(
      resolveLoginCommandRequest(
        {
          provider: undefined,
          key: undefined,
          model: "claude",
          "base-url": undefined,
          "config-dir": "/tmp/config",
        },
        {
          promptForValue,
          normalizeOllamaBaseUrl: (value?: string) => value ?? "http://localhost:11434",
          validateOllamaConnection,
          env: {},
        },
      ),
    ).resolves.toEqual({
      provider: "anthropic",
      apiKey: "sk-test",
      model: "claude",
      baseUrl: undefined,
      configDir: "/tmp/config",
    });

    expect(promptForValue).toHaveBeenNthCalledWith(1, "Provider");
    expect(promptForValue).toHaveBeenNthCalledWith(2, "API key");
    expect(validateOllamaConnection).not.toHaveBeenCalled();
  });

  it("rejects missing provider after prompting", async () => {
    await expect(
      resolveLoginCommandRequest(
        {
          provider: undefined,
          key: undefined,
          model: undefined,
          "base-url": undefined,
          "config-dir": undefined,
        },
        {
          promptForValue: vi.fn().mockResolvedValue(""),
          normalizeOllamaBaseUrl: (value?: string) => value ?? "http://localhost:11434",
          validateOllamaConnection: vi.fn(),
          env: {},
        },
      ),
    ).rejects.toThrow("Error: provider is required");
  });

  it("rejects missing API key for non-ollama providers", async () => {
    await expect(
      resolveLoginCommandRequest(
        {
          provider: "anthropic",
          key: undefined,
          model: undefined,
          "base-url": undefined,
          "config-dir": undefined,
        },
        {
          promptForValue: vi.fn().mockResolvedValue(""),
          normalizeOllamaBaseUrl: (value?: string) => value ?? "http://localhost:11434",
          validateOllamaConnection: vi.fn(),
          env: {},
        },
      ),
    ).rejects.toThrow("Error: --key is required for this provider");
  });

  it("normalizes and validates ollama base URLs without prompting for a key", async () => {
    const validateOllamaConnection = vi.fn().mockResolvedValue(undefined);
    const promptForValue = vi.fn();

    await expect(
      resolveLoginCommandRequest(
        {
          provider: "Ollama",
          key: undefined,
          model: undefined,
          "base-url": "http://127.0.0.1:11434/v1/",
          "config-dir": undefined,
        },
        {
          promptForValue,
          normalizeOllamaBaseUrl: (value?: string) => `normalized:${value}`,
          validateOllamaConnection,
          env: {},
        },
      ),
    ).resolves.toEqual({
      provider: "ollama",
      apiKey: undefined,
      model: undefined,
      baseUrl: "normalized:http://127.0.0.1:11434/v1/",
      configDir: undefined,
    });

    expect(promptForValue).not.toHaveBeenCalled();
    expect(validateOllamaConnection).toHaveBeenCalledWith(
      "normalized:http://127.0.0.1:11434/v1/",
    );
  });

  it("builds stored credentials from optional login fields", () => {
    expect(
      buildStoredProviderCredentials({
        apiKey: "sk-test",
        model: "claude",
        baseUrl: undefined,
      }),
    ).toEqual({ apiKey: "sk-test", model: "claude" });
  });

  it("builds login success messages", () => {
    expect(buildLoginSuccessMessage({ provider: "anthropic", baseUrl: undefined })).toBe(
      "Credentials saved for anthropic",
    );
    expect(
      buildLoginSuccessMessage({
        provider: "ollama",
        baseUrl: "http://127.0.0.1:11434",
      }),
    ).toBe("Connected to Ollama at http://127.0.0.1:11434");
  });

  it("builds whoami payload with configured providers and optional base URL", () => {
    expect(
      buildWhoamiPayload({
        provider: "anthropic",
        model: "claude",
        authenticated: true,
        baseUrl: "https://proxy.example",
        configuredProviders: [{ provider: "anthropic", hasApiKey: true }],
      }),
    ).toEqual({
      provider: "anthropic",
      model: "claude",
      authenticated: true,
      baseUrl: "https://proxy.example",
      configuredProviders: [{ provider: "anthropic", hasApiKey: true }],
    });
  });

  it("builds provider catalog entries from known and discovered providers", () => {
    expect(
      buildProvidersPayload(
        [
          {
            id: "anthropic",
            displayName: "Anthropic",
            requiresKey: true,
          },
          {
            id: "ollama",
            displayName: "Ollama",
            requiresKey: false,
          },
        ],
        [
          {
            provider: "anthropic",
            hasApiKey: true,
            source: "stored",
            model: "claude",
          },
        ],
      ),
    ).toEqual([
      {
        id: "anthropic",
        displayName: "Anthropic",
        requiresKey: true,
        authenticated: true,
        source: "stored",
        model: "claude",
      },
      {
        id: "ollama",
        displayName: "Ollama",
        requiresKey: false,
        authenticated: true,
      },
    ]);
  });

  it("renders empty and populated models output", () => {
    expect(renderModelsResult([])).toEqual([
      "[]",
      "\nNo authenticated providers found. Run `autoctx login` to configure a provider.",
    ]);
    expect(renderModelsResult([{ provider: "anthropic", model: "claude" }])).toEqual([
      JSON.stringify([{ provider: "anthropic", model: "claude" }], null, 2),
    ]);
  });

  it("builds logout messages", () => {
    expect(buildLogoutMessage("anthropic")).toBe("Logged out from anthropic");
    expect(buildLogoutMessage()).toBe("Logged out.");
  });
});
