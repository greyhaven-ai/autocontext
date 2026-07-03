import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  DEFAULT_ROLE_ROUTING_TABLE,
  EXPLICIT_PROVIDER_CLASS,
  LOCAL_ELIGIBLE_ROLES,
  PROVIDER_CLASSES,
  PROVIDER_CLASS_COST_PER_1K_TOKENS,
  ROLE_ROUTING_MODES,
  buildRoleProviderBundle,
  estimateRoleRoutingCost,
  routeRoleProvider,
  type RoleProviderSettings,
} from "../src/providers/index.js";

type RoleRoutingContract = {
  cost_per_1k_tokens: Record<string, number>;
  default_routing_table: Record<string, string[]>;
  explicit_provider_classes: Record<string, string>;
  local_eligible_roles: string[];
  mode_values: string[];
  provider_classes: string[];
};

const CONTRACT = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "role-routing-contract.json"),
    "utf-8",
  ),
) as RoleRoutingContract;

function baseSettings(overrides: Partial<RoleProviderSettings> = {}): RoleProviderSettings {
  return {
    agentProvider: "deterministic",
    roleRouting: "auto",
    modelCompetitor: "competitor-role-model",
    modelAnalyst: "analyst-role-model",
    modelCoach: "coach-role-model",
    modelArchitect: "architect-role-model",
    modelCurator: "curator-role-model",
    modelTranslator: "translator-role-model",
    tierOpusModel: "opus-tier-model",
    tierSonnetModel: "sonnet-tier-model",
    tierHaikuModel: "haiku-tier-model",
    mlxModelPath: "/models/default-local",
    ...overrides,
  };
}

// AC-873: routeRoleProvider() reads AUTOCONTEXT_AGENT_PROVIDER/AUTOCONTEXT_PROVIDER live from
// process.env, so any test exercising its no-env-pinned precedence must isolate a dirty
// runner env (e.g. a real CI env var, or leakage from another test) or it can flip silently.
function withCleanProviderEnv(run: () => void): void {
  const previousAgentProvider = process.env.AUTOCONTEXT_AGENT_PROVIDER;
  const previousProvider = process.env.AUTOCONTEXT_PROVIDER;
  delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
  delete process.env.AUTOCONTEXT_PROVIDER;
  try {
    run();
  } finally {
    if (previousAgentProvider === undefined) {
      delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
    } else {
      process.env.AUTOCONTEXT_AGENT_PROVIDER = previousAgentProvider;
    }
    if (previousProvider === undefined) {
      delete process.env.AUTOCONTEXT_PROVIDER;
    } else {
      process.env.AUTOCONTEXT_PROVIDER = previousProvider;
    }
  }
}

describe("shared role routing contract", () => {
  it("keeps TypeScript routing constants aligned with the shared contract", () => {
    expect(PROVIDER_CLASSES).toEqual(CONTRACT.provider_classes);
    expect(ROLE_ROUTING_MODES).toEqual(CONTRACT.mode_values);
    expect(DEFAULT_ROLE_ROUTING_TABLE).toEqual(CONTRACT.default_routing_table);
    expect(LOCAL_ELIGIBLE_ROLES).toEqual(CONTRACT.local_eligible_roles);
    expect(PROVIDER_CLASS_COST_PER_1K_TOKENS).toEqual(CONTRACT.cost_per_1k_tokens);
    expect(EXPLICIT_PROVIDER_CLASS).toEqual(CONTRACT.explicit_provider_classes);
  });

  it("uses the default provider and configured role model when role routing is off", () => {
    const routed = routeRoleProvider(baseSettings({ roleRouting: "off" }), "competitor");

    expect(routed).toMatchObject({
      providerType: "deterministic",
      providerClass: "fast",
      model: "competitor-role-model",
      estimatedCostPer1kTokens: 0.001,
    });
  });

  it("lets explicit per-role provider overrides win over auto routing", () => {
    const routed = routeRoleProvider(baseSettings({ competitorProvider: "ollama" }), "competitor");

    expect(routed).toMatchObject({
      providerType: "ollama",
      providerClass: "mid_tier",
      model: "competitor-role-model",
    });
    expect(
      routeRoleProvider(baseSettings({ competitorProvider: "mlx" }), "competitor"),
    ).toMatchObject({
      providerType: "mlx",
      providerClass: "local",
      model: "/models/default-local",
      executableInTypeScript: false,
    });
  });

  it("routes auto mode through Python-compatible tier preferences", () => {
    expect(routeRoleProvider(baseSettings(), "competitor")).toMatchObject({
      providerClass: "frontier",
      model: "opus-tier-model",
    });
    expect(routeRoleProvider(baseSettings(), "analyst")).toMatchObject({
      providerClass: "mid_tier",
      model: "sonnet-tier-model",
    });
    expect(routeRoleProvider(baseSettings(), "translator")).toMatchObject({
      providerClass: "fast",
      model: "haiku-tier-model",
    });
    expect(routeRoleProvider(baseSettings(), "curator")).toMatchObject({
      providerClass: "fast",
      model: "haiku-tier-model",
    });
  });

  it("prefers available local artifacts for eligible roles and leaves frontier roles alone", () => {
    const context = { availableLocalModels: ["/models/distilled-grid-ctf"] };

    expect(routeRoleProvider(baseSettings(), "analyst", context)).toMatchObject({
      providerType: "mlx",
      providerClass: "local",
      model: "/models/distilled-grid-ctf",
      estimatedCostPer1kTokens: 0.0,
      executableInTypeScript: false,
    });
    expect(routeRoleProvider(baseSettings(), "architect", context)).toMatchObject({
      providerType: "deterministic",
      providerClass: "frontier",
      model: "opus-tier-model",
      executableInTypeScript: true,
    });
  });

  it("AC-873: applies a providerOverride when settings.agentProvider has no per-role setting", () => {
    withCleanProviderEnv(() => {
      const routed = routeRoleProvider(baseSettings({ roleRouting: "off" }), "analyst", {
        providerOverride: "deterministic",
      });

      expect(routed).toMatchObject({
        providerType: "deterministic",
        providerClass: "fast",
        model: "analyst-role-model",
      });
    });
  });

  it("AC-873: lets an explicit per-role provider setting win over a providerOverride", () => {
    withCleanProviderEnv(() => {
      const routed = routeRoleProvider(
        baseSettings({ competitorProvider: "ollama" }),
        "competitor",
        {
          providerOverride: "deterministic",
        },
      );

      expect(routed).toMatchObject({ providerType: "ollama", providerClass: "mid_tier" });
    });
  });

  it("AC-873: falls back to settings.agentProvider when no providerOverride is supplied", () => {
    withCleanProviderEnv(() => {
      const routed = routeRoleProvider(baseSettings({ roleRouting: "off" }), "analyst");

      expect(routed).toMatchObject({ providerType: "deterministic", providerClass: "fast" });
    });
  });

  it("AC-873: lets the AUTOCONTEXT_AGENT_PROVIDER env var win over a construction-time providerOverride", () => {
    withCleanProviderEnv(() => {
      process.env.AUTOCONTEXT_AGENT_PROVIDER = "ollama";
      const routed = routeRoleProvider(
        baseSettings({ roleRouting: "off", agentProvider: "ollama" }),
        "analyst",
        { providerOverride: "deterministic" },
      );

      expect(routed).toMatchObject({ providerType: "ollama", providerClass: "mid_tier" });
    });
  });

  it("AC-873: lets a preferProviderOverride win over a pinned AUTOCONTEXT_AGENT_PROVIDER (mid-session switch)", () => {
    withCleanProviderEnv(() => {
      process.env.AUTOCONTEXT_AGENT_PROVIDER = "ollama";
      const routed = routeRoleProvider(
        baseSettings({ roleRouting: "off", agentProvider: "ollama" }),
        "analyst",
        { providerOverride: "deterministic", preferProviderOverride: true },
      );

      expect(routed).toMatchObject({ providerType: "deterministic", providerClass: "fast" });
    });
  });

  it("keeps Python's mid-tier fallback for unknown role names", () => {
    const routed = routeRoleProvider(baseSettings(), "unknown_role");

    expect(routed).toMatchObject({
      providerType: "deterministic",
      providerClass: "mid_tier",
      model: "sonnet-tier-model",
    });
  });

  it("estimates one generation cycle with the same role set as Python", () => {
    const estimate = estimateRoleRoutingCost(baseSettings());

    expect(Object.keys(estimate.roles).sort()).toEqual(
      Object.keys(CONTRACT.default_routing_table).sort(),
    );
    expect(estimate.totalPer1kTokens).toBeGreaterThan(0);
    expect(estimate.savingsVsAllFrontier).toBeGreaterThanOrEqual(0);
  });
});

describe("role-routed provider bundles", () => {
  it("applies auto role routing to generated providers and models", () => {
    const bundle = buildRoleProviderBundle(baseSettings());

    expect(bundle.roleModels.competitor).toBe("opus-tier-model");
    expect(bundle.roleModels.analyst).toBe("sonnet-tier-model");
    expect(bundle.roleModels.translator).toBe("haiku-tier-model");
    expect(bundle.roleProviders.translator?.name).toBe("deterministic");
    expect(bundle.roleRoutes?.competitor).toMatchObject({ providerClass: "frontier" });
    bundle.close?.();
  });

  it("keeps explicit role providers as the highest-priority route", () => {
    const bundle = buildRoleProviderBundle(baseSettings({ competitorProvider: "deterministic" }));

    expect(bundle.roleModels.competitor).toBe("competitor-role-model");
    expect(bundle.roleRoutes?.competitor).toMatchObject({
      providerType: "deterministic",
      providerClass: "fast",
    });
    bundle.close?.();
  });

  it("AC-873: threads a providerType override into per-role routing, not just the default provider", () => {
    withCleanProviderEnv(() => {
      const bundle = buildRoleProviderBundle(
        baseSettings({ agentProvider: "anthropic", roleRouting: "off" }),
        { providerType: "deterministic" },
      );

      expect(bundle.defaultConfig.providerType).toBe("deterministic");
      expect(bundle.roleRoutes?.analyst).toMatchObject({ providerType: "deterministic" });
      expect(bundle.roleProviders.analyst?.name).toBe("deterministic");
      bundle.close?.();
    });
  });

  it("AC-873: preferProviderOverride lets a mid-session switch win over a pinned env var, for both the default provider and per-role routes", () => {
    withCleanProviderEnv(() => {
      process.env.AUTOCONTEXT_AGENT_PROVIDER = "anthropic";
      const bundle = buildRoleProviderBundle(
        baseSettings({ agentProvider: "anthropic", roleRouting: "off" }),
        { providerType: "deterministic" },
        { preferProviderOverride: true },
      );

      expect(bundle.defaultConfig.providerType).toBe("deterministic");
      expect(bundle.roleRoutes?.analyst).toMatchObject({ providerType: "deterministic" });
      expect(bundle.roleProviders.analyst?.name).toBe("deterministic");
      bundle.close?.();
    });
  });

  it("AC-873: without preferProviderOverride, a pinned env var still wins over the override for both default and per-role routes (construction-time precedent preserved)", () => {
    withCleanProviderEnv(() => {
      // "deterministic" needs no credentials, so a mistaken precedence bug here would surface
      // as a wrong providerType rather than being masked by an unrelated ANTHROPIC_API_KEY
      // ProviderError.
      process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
      const bundle = buildRoleProviderBundle(
        baseSettings({ agentProvider: "deterministic", roleRouting: "off" }),
        { providerType: "ollama" },
      );

      expect(bundle.defaultConfig.providerType).toBe("deterministic");
      expect(bundle.roleRoutes?.analyst).toMatchObject({ providerType: "deterministic" });
      bundle.close?.();
    });
  });

  it("uses routed tier models as CLI runtime defaults for role providers", () => {
    const claudeBundle = buildRoleProviderBundle(
      baseSettings({
        agentProvider: "claude-cli",
        claudeModel: "sonnet-cli",
      }),
    );
    expect(claudeBundle.defaultProvider.defaultModel()).toBe("sonnet-cli");
    expect(claudeBundle.roleModels.competitor).toBe("opus-tier-model");
    expect(claudeBundle.roleProviders.competitor?.defaultModel()).toBe("opus-tier-model");
    claudeBundle.close?.();

    const codexBundle = buildRoleProviderBundle(
      baseSettings({
        agentProvider: "codex",
        codexModel: "codex-global",
      }),
    );
    expect(codexBundle.defaultProvider.defaultModel()).toBe("codex-global");
    expect(codexBundle.roleModels.analyst).toBe("sonnet-tier-model");
    expect(codexBundle.roleProviders.analyst?.defaultModel()).toBe("sonnet-tier-model");
    codexBundle.close?.();
  });

  it("surfaces Python-local routes explicitly when TypeScript cannot execute them", () => {
    expect(() =>
      buildRoleProviderBundle(
        baseSettings(),
        {},
        { routingContext: { availableLocalModels: ["/models/distilled-grid-ctf"] } },
      ),
    ).toThrow("TypeScript provider runtime does not support routed provider");
  });
});
