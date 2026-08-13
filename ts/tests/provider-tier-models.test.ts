/**
 * AC-935: providers that serve tiers get a model per tier, in TypeScript too.
 *
 * The twin of `autocontext/tests/test_provider_tier_models.py`, deliberately
 * case-for-case. `PROVIDER_TIER_MODELS` lives in the generated contract, so both
 * engines read one source; these two files are what prove they read it the same
 * way.
 *
 * `configuredFields` is the TypeScript counterpart of Python's
 * `model_fields_set`: it distinguishes "the user chose this" from "nobody
 * touched it". Passing an empty array is what makes every slot unset, which is
 * the state per-tier defaults exist to fill.
 */
import { describe, expect, it } from "vitest";

import { routeRoleProvider } from "../src/providers/role-routing.js";
import type { RoleRoutingSettings } from "../src/providers/role-routing.js";

function unsetSettings(overrides: Partial<RoleRoutingSettings> = {}): RoleRoutingSettings {
  return {
    agentProvider: "openai",
    roleRouting: "auto",
    // Every slot unset: this is the layer per-tier defaults fill.
    configuredFields: [],
    ...overrides,
  } as RoleRoutingSettings;
}

describe("per-provider tier models", () => {
  it.each([
    ["architect", "frontier", "gpt-5.6-sol"],
    ["competitor", "frontier", "gpt-5.6-sol"],
    ["analyst", "mid_tier", "gpt-5.6-terra"],
    ["coach", "mid_tier", "gpt-5.6-terra"],
    ["curator", "fast", "gpt-5.6-luna"],
    ["translator", "fast", "gpt-5.6-luna"],
  ])("routes %s to its tier", (role, providerClass, model) => {
    const routed = routeRoleProvider(unsetSettings(), role);
    expect([routed.providerClass, routed.model]).toEqual([providerClass, model]);
  });

  it("no longer gives the architect and the curator the same model", () => {
    // The stated acceptance criterion. Before this, both resolved to the single
    // provider default.
    const settings = unsetSettings();
    expect(routeRoleProvider(settings, "architect").model).not.toBe(
      routeRoleProvider(settings, "curator").model,
    );
  });

  it("keeps OpenRouter's vendor prefix", () => {
    // A table that dropped the prefix would 404 against OpenRouter.
    const settings = unsetSettings({ agentProvider: "openrouter" });
    expect(routeRoleProvider(settings, "architect").model).toBe("anthropic/claude-opus-5");
    expect(routeRoleProvider(settings, "curator").model).toBe("anthropic/claude-haiku-4.5");
  });

  it.each(["ollama", "vllm"])("keeps one id for every tier on %s", (provider) => {
    // Absence from the table is a decision, not an omission: these serve one
    // model, so asking for three ids would request two that are not loaded.
    const settings = unsetSettings({ agentProvider: provider });
    const models = new Set(
      ["architect", "analyst", "curator"].map((role) => routeRoleProvider(settings, role).model),
    );
    expect(models.size).toBe(1);
  });

  it("leaves Anthropic untouched", () => {
    const settings = unsetSettings({ agentProvider: "anthropic" });
    expect(routeRoleProvider(settings, "architect").model).toBe("claude-opus-5");
    expect(routeRoleProvider(settings, "curator").model).toBe("claude-haiku-4-5-20251001");
  });

  it("lets an explicit tier model win, while unset slots still get a tier default", () => {
    const settings = unsetSettings({
      tierOpusModel: "my-own-frontier-model",
      configuredFields: ["tierOpusModel"],
    });
    expect(routeRoleProvider(settings, "architect").model).toBe("my-own-frontier-model");
    expect(routeRoleProvider(settings, "curator").model).toBe("gpt-5.6-luna");
  });

  it("lets localModel override every slot", () => {
    // Ordering matters: localModel is checked BEFORE the per-tier table, so
    // pointing at one local endpoint does not start requesting three ids.
    const settings = unsetSettings({ localModel: "my-local-model" });
    const models = new Set(
      ["architect", "analyst", "curator"].map((role) => routeRoleProvider(settings, role).model),
    );
    expect(models).toEqual(new Set(["my-local-model"]));
  });
});
