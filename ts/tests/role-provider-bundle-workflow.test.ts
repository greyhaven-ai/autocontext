import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { saveProviderCredentials } from "../src/config/index.js";
import {
  buildRoleProviderBundle,
  closeProviderBundle,
} from "../src/providers/role-provider-bundle.js";

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

describe("role provider bundle workflow", () => {
  it("applies per-role provider and model overrides while preserving defaults", () => {
    saveAndClear();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "hermes";
    process.env.AUTOCONTEXT_AGENT_API_KEY = "hermes-key";
    process.env.AUTOCONTEXT_AGENT_BASE_URL = "http://hermes.local:8080/v1";
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL = "hermes-default";

    const bundle = buildRoleProviderBundle({
      agentProvider: "hermes",
      competitorProvider: "deterministic",
      modelAnalyst: "analyst-model",
      modelCoach: "coach-model",
    });

    expect(bundle.defaultProvider.name).toBe("hermes-gateway");
    expect(bundle.defaultConfig).toMatchObject({
      providerType: "hermes",
      apiKey: "hermes-key",
      baseUrl: "http://hermes.local:8080/v1",
      model: "hermes-default",
    });
    expect(bundle.roleProviders.competitor?.name).toBe("deterministic");
    expect(bundle.roleModels.analyst).toBe("analyst-model");
    expect(bundle.roleModels.coach).toBe("coach-model");
  });

  it("treats blank per-role credential overrides as unset", () => {
    saveAndClear();
    const configDir = mkdtempSync(join(tmpdir(), "role-provider-credentials-"));
    process.env.AUTOCONTEXT_CONFIG_DIR = configDir;
    saveProviderCredentials(configDir, "anthropic", { apiKey: "sk-ant-test-123" });

    try {
      const bundle = buildRoleProviderBundle({
        agentProvider: "anthropic",
        competitorApiKey: "",
        competitorBaseUrl: "",
      });

      expect(bundle.defaultConfig.apiKey).toBe("sk-ant-test-123");
      expect(bundle.roleProviders.competitor?.name).toBe("anthropic");
    } finally {
      rmSync(configDir, { recursive: true, force: true });
    }
  });

  it("treats a blank default provider setting as unset", () => {
    saveAndClear();
    process.env.ANTHROPIC_API_KEY = "sk-test";

    const bundle = buildRoleProviderBundle({ agentProvider: "   " });
    try {
      expect(bundle.defaultConfig.providerType).toBe("anthropic");
      expect(bundle.defaultProvider.name).toBe("anthropic");
      expect(bundle.roleRoutes?.competitor?.providerType).toBe("anthropic");
    } finally {
      closeProviderBundle(bundle);
    }
  });

  it("lets a blank CLI provider override fall back to the settings provider", () => {
    saveAndClear();

    const bundle = buildRoleProviderBundle(
      { agentProvider: "deterministic" },
      { providerType: "   " },
      { preferProviderOverride: true },
    );
    try {
      expect(bundle.defaultConfig.providerType).toBe("deterministic");
      expect(bundle.defaultProvider.name).toBe("deterministic");
    } finally {
      closeProviderBundle(bundle);
    }
  });

  it("wraps configured roles with the experimental panel provider", async () => {
    saveAndClear();

    const bundle = buildRoleProviderBundle({
      agentProvider: "deterministic",
      panelRoles: "analyst",
      panelParticipants: "analyst=deterministic:fast,deterministic:careful",
    });

    const result = await bundle.roleProviders.analyst?.complete({
      systemPrompt: "",
      userPrompt: "[analyst] review this strategy",
      model: bundle.roleModels.analyst,
    });

    expect(bundle.roleProviders.analyst?.name).toBe("panel:analyst");
    expect(result?.metadata?.panelRuntime).toBe(true);
    expect(result?.metadata?.panelParticipants).toMatchObject([
      { provider: "deterministic", model: "fast" },
      { provider: "deterministic", model: "careful" },
    ]);
  });

  it("closes each unique provider in a role bundle once", () => {
    const sharedProvider = {
      name: "shared",
      defaultModel: () => "shared-model",
      complete: vi.fn(),
      close: vi.fn(),
    };
    const roleProvider = {
      name: "role",
      defaultModel: () => "role-model",
      complete: vi.fn(),
      close: vi.fn(),
    };

    closeProviderBundle({
      defaultProvider: sharedProvider,
      roleProviders: {
        competitor: sharedProvider,
        analyst: roleProvider,
      },
    });

    expect(sharedProvider.close).toHaveBeenCalledOnce();
    expect(roleProvider.close).toHaveBeenCalledOnce();
  });
});
