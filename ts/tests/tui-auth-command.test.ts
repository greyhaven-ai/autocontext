import { describe, expect, it, vi } from "vitest";

import {
  executeTuiAuthLoginCommandPlan,
  executeTuiAuthLogoutCommandPlan,
  executeTuiAuthStatusCommandPlan,
  executeTuiPendingLoginSubmission,
  formatTuiWhoamiLines,
  planTuiAuthCommand,
} from "../src/tui/auth-command.js";
import { handleInteractiveTuiCommand } from "../src/tui/commands.js";

describe("TUI auth command planner", () => {
  it("plans login commands with normalized provider and optional credential fields", () => {
    expect(planTuiAuthCommand("/login Anthropic sk-ant-test claude http://localhost:11434")).toEqual({
      kind: "login",
      provider: "anthropic",
      apiKey: "sk-ant-test",
      model: "claude",
      baseUrl: "http://localhost:11434",
    });
    expect(planTuiAuthCommand("  /login ollama  ")).toEqual({
      kind: "login",
      provider: "ollama",
    });
  });

  it("reports usage for login without a provider", () => {
    expect(planTuiAuthCommand("/login")).toEqual({
      kind: "usage",
      usageLine: "usage: /login <provider> [apiKey] [model] [baseUrl]",
    });
    expect(planTuiAuthCommand("  /login  ")).toEqual({
      kind: "usage",
      usageLine: "usage: /login <provider> [apiKey] [model] [baseUrl]",
    });
  });

  it("plans logout commands with optional normalized provider", () => {
    expect(planTuiAuthCommand("/logout")).toEqual({ kind: "logout" });
    expect(planTuiAuthCommand("/logout OpenAI")).toEqual({
      kind: "logout",
      provider: "openai",
    });
  });

  it("plans provider switches and whoami readback", () => {
    expect(planTuiAuthCommand("/provider Deterministic")).toEqual({
      kind: "switchProvider",
      provider: "deterministic",
    });
    expect(planTuiAuthCommand("/whoami")).toEqual({ kind: "whoami" });
  });

  it("reports usage for provider without a name", () => {
    expect(planTuiAuthCommand("/provider")).toEqual({
      kind: "usage",
      usageLine: "usage: /provider <name>",
    });
  });

  it("leaves similarly prefixed commands unhandled", () => {
    expect(planTuiAuthCommand("/loginx anthropic")).toEqual({ kind: "unhandled" });
    expect(planTuiAuthCommand("/logoutx")).toEqual({ kind: "unhandled" });
    expect(planTuiAuthCommand("/providerx deterministic")).toEqual({ kind: "unhandled" });
    expect(planTuiAuthCommand("/whoami?")).toEqual({ kind: "unhandled" });
  });

  it("formats auth status lines consistently", () => {
    expect(formatTuiWhoamiLines({
      provider: "openai",
      authenticated: true,
      model: "gpt-5.2",
      configuredProviders: [
        { provider: "openai", hasApiKey: true },
        { provider: "anthropic", hasApiKey: true },
      ],
    })).toEqual([
      "provider: openai",
      "authenticated: yes",
      "model: gpt-5.2",
      "configured providers: openai, anthropic",
    ]);
  });
});

describe("TUI auth login command executor", () => {
  it("prompts for an API key when the provider requires one", async () => {
    const effects = {
      providerRequiresKey: vi.fn(() => true),
      login: vi.fn(),
      selectProvider: vi.fn(),
    };

    await expect(executeTuiAuthLoginCommandPlan({
      kind: "login",
      provider: "anthropic",
      model: "claude",
      baseUrl: "https://example.test",
    }, effects)).resolves.toEqual({
      logLines: ["enter API key for anthropic on the next line, or /cancel"],
      pendingLogin: {
        provider: "anthropic",
        model: "claude",
        baseUrl: "https://example.test",
      },
    });
    expect(effects.providerRequiresKey).toHaveBeenCalledWith("anthropic");
    expect(effects.login).not.toHaveBeenCalled();
    expect(effects.selectProvider).not.toHaveBeenCalled();
  });

  it("logs in immediately when credentials are supplied and preserves warnings", async () => {
    const effects = {
      providerRequiresKey: vi.fn(),
      login: vi.fn(async () => ({
        saved: true,
        provider: "anthropic",
        validationWarning: "key format looks unusual",
      })),
      selectProvider: vi.fn(() => ({ provider: "anthropic", authenticated: true })),
    };

    await expect(executeTuiAuthLoginCommandPlan({
      kind: "login",
      provider: "anthropic",
      apiKey: "sk-ant-test",
      model: "claude",
      baseUrl: "https://example.test",
    }, effects)).resolves.toEqual({
      logLines: [
        "logged in to anthropic",
        "warning: key format looks unusual",
      ],
      pendingLogin: null,
    });
    expect(effects.providerRequiresKey).not.toHaveBeenCalled();
    expect(effects.login).toHaveBeenCalledWith(
      "anthropic",
      "sk-ant-test",
      "claude",
      "https://example.test",
    );
    expect(effects.selectProvider).toHaveBeenCalledWith("anthropic");
  });

  it("logs in immediately for providers that do not require an API key", async () => {
    const effects = {
      providerRequiresKey: vi.fn(() => false),
      login: vi.fn(async () => ({ saved: true, provider: "ollama" })),
      selectProvider: vi.fn(() => ({ provider: "ollama", authenticated: true })),
    };

    await expect(executeTuiAuthLoginCommandPlan({
      kind: "login",
      provider: "ollama",
    }, effects)).resolves.toEqual({
      logLines: ["logged in to ollama"],
      pendingLogin: null,
    });
    expect(effects.providerRequiresKey).toHaveBeenCalledWith("ollama");
    expect(effects.login).toHaveBeenCalledWith("ollama", undefined, undefined, undefined);
    expect(effects.selectProvider).toHaveBeenCalledWith("ollama");
  });

  it("reports failed immediate saves without selecting a provider", async () => {
    const effects = {
      providerRequiresKey: vi.fn(),
      login: vi.fn(async () => ({
        saved: false,
        provider: "anthropic",
        validationWarning: "bad key",
      })),
      selectProvider: vi.fn(),
    };

    await expect(executeTuiAuthLoginCommandPlan({
      kind: "login",
      provider: "anthropic",
      apiKey: "bad-key",
    }, effects)).resolves.toEqual({
      logLines: ["bad key"],
      pendingLogin: null,
    });
    expect(effects.selectProvider).not.toHaveBeenCalled();
  });

  it("leaves non-login plans for narrower executors", async () => {
    const effects = {
      providerRequiresKey: vi.fn(),
      login: vi.fn(),
      selectProvider: vi.fn(),
    };

    await expect(executeTuiAuthLoginCommandPlan({
      kind: "logout",
      provider: "anthropic",
    }, effects)).resolves.toBeNull();
    await expect(executeTuiAuthLoginCommandPlan({
      kind: "switchProvider",
      provider: "openai",
    }, effects)).resolves.toBeNull();
    await expect(executeTuiAuthLoginCommandPlan({ kind: "unhandled" }, effects)).resolves.toBeNull();
    expect(effects.providerRequiresKey).not.toHaveBeenCalled();
    expect(effects.login).not.toHaveBeenCalled();
    expect(effects.selectProvider).not.toHaveBeenCalled();
  });
});

describe("TUI pending login submission executor", () => {
  it("saves the submitted API key and clears the pending login on success", async () => {
    const effects = {
      login: vi.fn(async () => ({ saved: true, provider: "anthropic" })),
      selectProvider: vi.fn(() => ({ provider: "anthropic", authenticated: true })),
    };
    const pendingLogin = {
      provider: "anthropic",
      model: "claude",
      baseUrl: "https://example.test",
    };

    await expect(executeTuiPendingLoginSubmission(
      pendingLogin,
      "sk-ant-test",
      effects,
    )).resolves.toEqual({
      logLines: ["logged in to anthropic"],
      pendingLogin: null,
    });
    expect(effects.login).toHaveBeenCalledWith(
      "anthropic",
      "sk-ant-test",
      "claude",
      "https://example.test",
    );
    expect(effects.selectProvider).toHaveBeenCalledWith("anthropic");
  });

  it("keeps the pending login when submitted credentials cannot be saved", async () => {
    const effects = {
      login: vi.fn(async () => ({
        saved: false,
        provider: "anthropic",
        validationWarning: "try another key",
      })),
      selectProvider: vi.fn(),
    };
    const pendingLogin = { provider: "anthropic" };

    await expect(executeTuiPendingLoginSubmission(
      pendingLogin,
      "bad-key",
      effects,
    )).resolves.toEqual({
      logLines: ["try another key"],
      pendingLogin,
    });
    expect(effects.selectProvider).not.toHaveBeenCalled();
  });
});

describe("TUI auth logout command executor", () => {
  it("clears all credentials and active provider for bare logout", () => {
    const effects = {
      logout: vi.fn(),
      clearActiveProvider: vi.fn(),
      getActiveProvider: vi.fn(),
      selectProvider: vi.fn(),
      readWhoami: vi.fn(() => ({ provider: "none", authenticated: false })),
    };

    expect(executeTuiAuthLogoutCommandPlan({ kind: "logout" }, effects)).toEqual({
      logLines: [
        "cleared stored credentials",
        "provider: none",
        "authenticated: no",
      ],
    });
    expect(effects.logout).toHaveBeenCalledWith(undefined);
    expect(effects.clearActiveProvider).toHaveBeenCalledOnce();
    expect(effects.readWhoami).toHaveBeenCalledWith();
    expect(effects.getActiveProvider).not.toHaveBeenCalled();
    expect(effects.selectProvider).not.toHaveBeenCalled();
  });

  it("keeps the logged-out provider selected when it was active", () => {
    const effects = {
      logout: vi.fn(),
      clearActiveProvider: vi.fn(),
      getActiveProvider: vi.fn(() => "anthropic"),
      selectProvider: vi.fn(() => ({ provider: "anthropic", authenticated: false })),
      readWhoami: vi.fn(),
    };

    expect(executeTuiAuthLogoutCommandPlan({
      kind: "logout",
      provider: "anthropic",
    }, effects)).toEqual({
      logLines: [
        "logged out of anthropic",
        "provider: anthropic",
        "authenticated: no",
      ],
    });
    expect(effects.logout).toHaveBeenCalledWith("anthropic");
    expect(effects.getActiveProvider).toHaveBeenCalledOnce();
    expect(effects.selectProvider).toHaveBeenCalledWith("anthropic");
    expect(effects.clearActiveProvider).not.toHaveBeenCalled();
    expect(effects.readWhoami).not.toHaveBeenCalled();
  });

  it("reselects the previous active provider when logging out a different provider", () => {
    const effects = {
      logout: vi.fn(),
      clearActiveProvider: vi.fn(),
      getActiveProvider: vi.fn(() => "openai"),
      selectProvider: vi.fn(() => ({ provider: "openai", authenticated: true })),
      readWhoami: vi.fn(),
    };

    expect(executeTuiAuthLogoutCommandPlan({
      kind: "logout",
      provider: "anthropic",
    }, effects)).toEqual({
      logLines: [
        "logged out of anthropic",
        "provider: openai",
        "authenticated: yes",
      ],
    });
    expect(effects.logout).toHaveBeenCalledWith("anthropic");
    expect(effects.selectProvider).toHaveBeenCalledWith("openai");
  });

  it("leaves non-logout plans for narrower executors", () => {
    const effects = {
      logout: vi.fn(),
      clearActiveProvider: vi.fn(),
      getActiveProvider: vi.fn(),
      selectProvider: vi.fn(),
      readWhoami: vi.fn(),
    };

    expect(executeTuiAuthLogoutCommandPlan({
      kind: "login",
      provider: "anthropic",
    }, effects)).toBeNull();
    expect(executeTuiAuthLogoutCommandPlan({
      kind: "switchProvider",
      provider: "openai",
    }, effects)).toBeNull();
    expect(executeTuiAuthLogoutCommandPlan({ kind: "unhandled" }, effects)).toBeNull();
    expect(effects.logout).not.toHaveBeenCalled();
    expect(effects.clearActiveProvider).not.toHaveBeenCalled();
    expect(effects.getActiveProvider).not.toHaveBeenCalled();
    expect(effects.selectProvider).not.toHaveBeenCalled();
    expect(effects.readWhoami).not.toHaveBeenCalled();
  });
});

describe("TUI auth status command executor", () => {
  it("switches provider and renders a fresh whoami readback", () => {
    const effects = {
      selectProvider: vi.fn(() => ({ provider: "deterministic", authenticated: true })),
      readWhoami: vi.fn(() => ({
        provider: "deterministic",
        authenticated: true,
        model: "det-model",
        configuredProviders: [{ provider: "deterministic" }],
      })),
      getActiveProvider: vi.fn(),
    };

    expect(executeTuiAuthStatusCommandPlan({
      kind: "switchProvider",
      provider: "deterministic",
    }, effects)).toEqual({
      logLines: [
        "active provider: deterministic",
        "provider: deterministic",
        "authenticated: yes",
        "model: det-model",
        "configured providers: deterministic",
      ],
    });
    expect(effects.selectProvider).toHaveBeenCalledWith("deterministic");
    expect(effects.readWhoami).toHaveBeenCalledWith("deterministic");
    expect(effects.getActiveProvider).not.toHaveBeenCalled();
  });

  it("reads whoami with the currently active provider", () => {
    const effects = {
      selectProvider: vi.fn(),
      readWhoami: vi.fn(() => ({
        provider: "anthropic",
        authenticated: false,
      })),
      getActiveProvider: vi.fn(() => "anthropic"),
    };

    expect(executeTuiAuthStatusCommandPlan({ kind: "whoami" }, effects)).toEqual({
      logLines: [
        "provider: anthropic",
        "authenticated: no",
      ],
    });
    expect(effects.getActiveProvider).toHaveBeenCalledOnce();
    expect(effects.readWhoami).toHaveBeenCalledWith("anthropic");
    expect(effects.selectProvider).not.toHaveBeenCalled();
  });

  it("reports usage without touching provider state", () => {
    const effects = {
      selectProvider: vi.fn(),
      readWhoami: vi.fn(),
      getActiveProvider: vi.fn(),
    };

    expect(executeTuiAuthStatusCommandPlan({
      kind: "usage",
      usageLine: "usage: /provider <name>",
    }, effects)).toEqual({
      logLines: ["usage: /provider <name>"],
    });
    expect(effects.selectProvider).not.toHaveBeenCalled();
    expect(effects.readWhoami).not.toHaveBeenCalled();
    expect(effects.getActiveProvider).not.toHaveBeenCalled();
  });

  it("leaves login, logout, and unhandled plans for narrower executors", () => {
    const effects = {
      selectProvider: vi.fn(),
      readWhoami: vi.fn(),
      getActiveProvider: vi.fn(),
    };

    expect(executeTuiAuthStatusCommandPlan({
      kind: "login",
      provider: "anthropic",
    }, effects)).toBeNull();
    expect(executeTuiAuthStatusCommandPlan({
      kind: "logout",
      provider: "anthropic",
    }, effects)).toBeNull();
    expect(executeTuiAuthStatusCommandPlan({ kind: "unhandled" }, effects)).toBeNull();
    expect(effects.selectProvider).not.toHaveBeenCalled();
    expect(effects.readWhoami).not.toHaveBeenCalled();
    expect(effects.getActiveProvider).not.toHaveBeenCalled();
  });
});

describe("TUI auth command handler", () => {
  it("reports provider usage before selecting a provider", async () => {
    const manager = {
      setActiveProvider: vi.fn(),
    };

    await expect(handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/provider",
      pendingLogin: null,
    })).resolves.toMatchObject({ logLines: ["usage: /provider <name>"] });
    expect(manager.setActiveProvider).not.toHaveBeenCalled();
  });

  it("does not treat similarly prefixed logout commands as logout", async () => {
    const manager = {
      clearActiveProvider: vi.fn(),
    };

    await expect(handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: "/logoutx",
      pendingLogin: null,
    })).resolves.toMatchObject({ logLines: ["unknown command; use /help"] });
    expect(manager.clearActiveProvider).not.toHaveBeenCalled();
  });
});
