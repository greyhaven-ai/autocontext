import { describe, expect, it, vi } from "vitest";

import {
  applyResolvedAuthSelection,
  buildAuthStatusMessage,
  executeAuthCommand,
} from "../src/server/auth-command-workflow.js";

describe("auth command workflow", () => {
  it("builds auth status messages from auth status", () => {
    expect(buildAuthStatusMessage({
      provider: "deterministic",
      authenticated: true,
      model: "deterministic",
      configuredProviders: [{ provider: "deterministic", hasApiKey: false }],
    })).toEqual({
      type: "auth_status",
      provider: "deterministic",
      authenticated: true,
      model: "deterministic",
      configuredProviders: [{ provider: "deterministic", hasApiKey: false }],
    });
  });

  it("applies resolved auth selections to the run-manager session", () => {
    const runManager = {
      setActiveProvider: vi.fn(),
      clearActiveProvider: vi.fn(),
    };

    applyResolvedAuthSelection(runManager, {
      provider: "anthropic",
      authenticated: true,
      apiKey: "sk-test",
      model: "claude",
      baseUrl: "https://api.example.com",
      configuredProviders: [],
    });

    expect(runManager.setActiveProvider).toHaveBeenCalledWith({
      providerType: "anthropic",
      apiKey: "sk-test",
      model: "claude",
      baseUrl: "https://api.example.com",
    });

    applyResolvedAuthSelection(runManager, {
      provider: "none",
      authenticated: false,
      configuredProviders: [],
    });
    expect(runManager.clearActiveProvider).toHaveBeenCalledOnce();
  });

  it("logs in, updates the active provider, and returns auth status", async () => {
    const runManager = {
      getActiveProviderType: vi.fn(() => "openai"),
      setActiveProvider: vi.fn(),
      clearActiveProvider: vi.fn(),
    };

    const result = await executeAuthCommand({
      command: {
        type: "login",
        provider: "anthropic",
        apiKey: "sk-ant",
        model: "claude-sonnet",
        baseUrl: undefined,
      },
      runManager,
      deps: {
        resolveConfigDir: () => "/tmp/config",
        handleTuiLogin: vi.fn(async () => ({ saved: true, provider: "anthropic" })),
        resolveTuiAuthSelection: vi.fn(() => ({
          provider: "anthropic",
          authenticated: true,
          apiKey: "sk-ant",
          model: "claude-sonnet",
          configuredProviders: [{ provider: "anthropic", hasApiKey: true }],
        })),
        handleTuiWhoami: vi.fn(() => ({
          provider: "anthropic",
          authenticated: true,
          model: "claude-sonnet",
          configuredProviders: [{ provider: "anthropic", hasApiKey: true }],
        })),
      },
    });

    expect(runManager.setActiveProvider).toHaveBeenCalledWith({
      providerType: "anthropic",
      apiKey: "sk-ant",
      model: "claude-sonnet",
    });
    expect(result).toEqual({
      type: "auth_status",
      provider: "anthropic",
      authenticated: true,
      model: "claude-sonnet",
      configuredProviders: [{ provider: "anthropic", hasApiKey: true }],
    });
  });

  it("clears session overrides on full logout and reports status", async () => {
    const runManager = {
      getActiveProviderType: vi.fn(() => "anthropic"),
      setActiveProvider: vi.fn(),
      clearActiveProvider: vi.fn(),
    };

    const result = await executeAuthCommand({
      command: { type: "logout", provider: undefined },
      runManager,
      deps: {
        resolveConfigDir: () => "/tmp/config",
        handleTuiLogout: vi.fn(),
        resolveTuiAuthSelection: vi.fn(),
        handleTuiWhoami: vi.fn(() => ({
          provider: "none",
          authenticated: false,
          configuredProviders: [],
        })),
      },
    });

    expect(runManager.clearActiveProvider).toHaveBeenCalledOnce();
    expect(result).toEqual({
      type: "auth_status",
      provider: "none",
      authenticated: false,
      configuredProviders: [],
    });
  });

  it("switches providers using resolved persisted selection", async () => {
    const runManager = {
      getActiveProviderType: vi.fn(() => "anthropic"),
      setActiveProvider: vi.fn(),
      clearActiveProvider: vi.fn(),
    };

    const result = await executeAuthCommand({
      command: { type: "switch_provider", provider: "deterministic" },
      runManager,
      deps: {
        resolveConfigDir: () => "/tmp/config",
        handleTuiSwitchProvider: vi.fn(() => ({
          provider: "deterministic",
          authenticated: true,
          configuredProviders: [{ provider: "deterministic", hasApiKey: false }],
        })),
        resolveTuiAuthSelection: vi.fn(() => ({
          provider: "deterministic",
          authenticated: true,
          configuredProviders: [{ provider: "deterministic", hasApiKey: false }],
        })),
      },
    });

    expect(runManager.setActiveProvider).toHaveBeenCalledWith({
      providerType: "deterministic",
    });
    expect(result).toEqual({
      type: "auth_status",
      provider: "deterministic",
      authenticated: true,
      configuredProviders: [{ provider: "deterministic", hasApiKey: false }],
    });
  });
});
