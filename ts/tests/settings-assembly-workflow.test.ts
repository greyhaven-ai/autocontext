import { describe, expect, it } from "vitest";

import { AppSettingsSchema } from "../src/config/app-settings-schema.js";
import {
  buildSettingsAssemblyInput,
  getDefaultSettingsRecord,
  parseAppSettings,
} from "../src/config/settings-assembly-workflow.js";
import { routeRoleProvider } from "../src/providers/role-routing.js";

describe("settings assembly workflow", () => {
  it("exposes the same default settings record as the schema", () => {
    expect(getDefaultSettingsRecord()).toEqual(AppSettingsSchema.parse({}));
  });

  it("assembles preset, project-config, and env overrides with env taking precedence", () => {
    const input = buildSettingsAssemblyInput({
      presetName: "quick",
      projectConfig: {
        provider: "ollama",
        model: "llama3.2",
        knowledgeDir: "/tmp/knowledge",
        runsDir: "/tmp/runs",
        dbPath: "/tmp/runs/db.sqlite3",
        gens: 4,
      },
      env: {
        AUTOCONTEXT_AGENT_PROVIDER: "deterministic",
        AUTOCONTEXT_MODEL_ANALYST: "analyst-model",
        AUTOCONTEXT_PI_NO_CONTEXT_FILES: "true",
      },
      defaults: getDefaultSettingsRecord(),
    });

    expect(input).toMatchObject({
      agentProvider: "deterministic",
      modelCompetitor: "llama3.2",
      modelAnalyst: "analyst-model",
      knowledgeRoot: "/tmp/knowledge",
      runsRoot: "/tmp/runs",
      dbPath: "/tmp/runs/db.sqlite3",
      defaultGenerations: 4,
      piNoContextFiles: true,
    });
    const settings = parseAppSettings(input);
    expect(settings.agentProvider).toBe("deterministic");
    expect(settings.piNoContextFiles).toBe(true);
  });

  it("loads endpoint declarations and the local model through the production assembly path", () => {
    const input = buildSettingsAssemblyInput({
      env: {
        AUTOCONTEXT_AGENT_PROVIDER: "openai-compatible",
        AUTOCONTEXT_LOCAL_MODEL: "qwen-test",
        AUTOCONTEXT_PROVIDER_CAPABILITY: "fast",
        AUTOCONTEXT_PROVIDER_HOSTING: "local",
        AUTOCONTEXT_COMPETITOR_PROVIDER_CAPABILITY: "mid_tier",
        AUTOCONTEXT_COMPETITOR_PROVIDER_HOSTING: "remote",
      },
      defaults: getDefaultSettingsRecord(),
    });
    const settings = parseAppSettings(input);

    expect(settings.configuredFields).toEqual(
      expect.arrayContaining([
        "agentProvider",
        "localModel",
        "providerCapability",
        "providerHosting",
        "competitorProviderCapability",
        "competitorProviderHosting",
      ]),
    );
    expect(settings.localModel).toBe("qwen-test");
    expect(settings.providerCapability).toBe("fast");
    expect(settings.providerHosting).toBe("local");
    expect(settings.competitorProviderCapability).toBe("mid_tier");
    expect(settings.competitorProviderHosting).toBe("remote");
    expect(
      routeRoleProvider(settings, "competitor", {
        providerOverride: "openai-compatible",
        preferProviderOverride: true,
      }),
    ).toMatchObject({
      providerClass: "fast",
      model: "qwen-test",
      estimatedCostPer1kTokens: 0,
    });
  });

  it("uses the OpenAI-compatible factory model default through production settings", () => {
    const settings = parseAppSettings(
      buildSettingsAssemblyInput({
        env: {
          AUTOCONTEXT_AGENT_PROVIDER: "openai-compatible",
          AUTOCONTEXT_ROLE_ROUTING: "auto",
        },
        defaults: getDefaultSettingsRecord(),
      }),
    );

    expect(
      routeRoleProvider(settings, "competitor", {
        providerOverride: "openai-compatible",
        preferProviderOverride: true,
      }).model,
    ).toBe("gpt-5.6-terra");
  });

  it("rejects invalid endpoint declarations", () => {
    expect(() => AppSettingsSchema.parse({ providerCapability: "turbo" })).toThrow();
    expect(() => AppSettingsSchema.parse({ providerHosting: "somewhere" })).toThrow();
  });
});
