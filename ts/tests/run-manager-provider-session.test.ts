import { describe, expect, it, vi } from "vitest";

import type { AppSettings } from "../src/config/index.js";
import type { RoleProviderBundle } from "../src/providers/index.js";
import { RunManagerProviderSession } from "../src/server/run-manager-provider-session.js";

function makeSettings(agentProvider = "anthropic"): AppSettings {
  return {
    ...({} as AppSettings),
    agentProvider,
  };
}

describe("run-manager provider session", () => {
  it("uses configured defaults when no session override has been set", () => {
    const session = new RunManagerProviderSession({
      providerType: "deterministic",
      model: "default-model",
    }, {
      loadSettings: () => makeSettings("anthropic"),
      buildRoleProviderBundle: vi.fn(() => ({
        defaultProvider: { name: "default", defaultModel: () => "default-model", complete: vi.fn() },
        defaultConfig: { providerType: "deterministic", apiKey: "", baseUrl: "", model: "default-model" },
        roleProviders: {},
        roleModels: {},
      } satisfies RoleProviderBundle)),
    });

    expect(session.getActiveProviderType()).toBe("deterministic");
  });

  it("normalizes and applies explicit active provider overrides", () => {
    const buildRoleProviderBundle = vi.fn(() => ({
      defaultProvider: { name: "default", defaultModel: () => "session-model", complete: vi.fn() },
      defaultConfig: { providerType: "deterministic", apiKey: "", baseUrl: "", model: "session-model" },
      roleProviders: {},
      roleModels: {},
    } satisfies RoleProviderBundle));

    const session = new RunManagerProviderSession({}, {
      loadSettings: () => makeSettings("anthropic"),
      buildRoleProviderBundle,
    });

    session.setActiveProvider({
      providerType: "  DETERMINISTIC  ",
      model: "session-model",
      baseUrl: "http://example.test",
    });

    expect(session.getActiveProviderType()).toBe("deterministic");
    session.resolveProviderBundle(makeSettings("anthropic"));
    expect(buildRoleProviderBundle).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        providerType: "deterministic",
        model: "session-model",
        baseUrl: "http://example.test",
      }),
    );
  });

  it("treats clearActiveProvider as an explicit unauthenticated session", () => {
    const session = new RunManagerProviderSession({ providerType: "deterministic" }, {
      loadSettings: () => makeSettings("anthropic"),
      buildRoleProviderBundle: vi.fn(),
    });

    session.clearActiveProvider();

    expect(session.getActiveProviderType()).toBeNull();
    expect(() => session.resolveProviderBundle(makeSettings("anthropic"))).toThrow(
      "No active provider configured for this session. Use /login or /provider.",
    );
  });

  it("builds role-aware providers from the resolved provider bundle", () => {
    const defaultProvider = { name: "default", defaultModel: () => "default-model", complete: vi.fn() };
    const analystProvider = { name: "analyst", defaultModel: () => "analyst-model", complete: vi.fn() };
    const session = new RunManagerProviderSession({ providerType: "deterministic" }, {
      loadSettings: () => makeSettings("deterministic"),
      buildRoleProviderBundle: vi.fn(() => ({
        defaultProvider,
        defaultConfig: { providerType: "deterministic", apiKey: "", baseUrl: "", model: "default-model" },
        roleProviders: { analyst: analystProvider },
        roleModels: { analyst: "analyst-model" },
      } satisfies RoleProviderBundle)),
    });

    expect(session.buildProvider()).toBe(defaultProvider);
    expect(session.buildProvider("analyst")).toBe(analystProvider);
    expect(session.buildProvider("coach")).toBe(defaultProvider);
  });
});
