import { afterEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { saveProviderCredentials } from "../src/config/index.js";
import { buildRoleProviderBundle } from "../src/providers/role-provider-bundle.js";

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
    saveProviderCredentials(configDir, "anthropic", { apiKey: "sk-test-123" });

    try {
      const bundle = buildRoleProviderBundle({
        agentProvider: "anthropic",
        competitorApiKey: "",
        competitorBaseUrl: "",
      });

      expect(bundle.defaultConfig.apiKey).toBe("sk-test-123");
      expect(bundle.roleProviders.competitor?.name).toBe("anthropic");
    } finally {
      rmSync(configDir, { recursive: true, force: true });
    }
  });
});
