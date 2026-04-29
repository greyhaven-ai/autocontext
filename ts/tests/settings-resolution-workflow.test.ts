import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { AppSettings } from "../src/config/index.js";
import type { ProjectConfig } from "../src/config/project-config.js";
import {
  buildProjectConfigSettingsOverrides,
  camelToScreamingSnake,
  coerceEnvValue,
  getSettingEnvKeys,
  resolveEnvSettingsOverrides,
} from "../src/config/settings-resolution.js";

describe("settings resolution workflow", () => {
  it("derives setting env keys with compatibility aliases", () => {
    expect(camelToScreamingSnake("agentProvider")).toBe("AGENT_PROVIDER");
    expect(getSettingEnvKeys("agentProvider")).toEqual([
      "AUTOCONTEXT_AGENT_PROVIDER",
      "AUTOCONTEXT_PROVIDER",
    ]);
    expect(getSettingEnvKeys("modelAnalyst")).toEqual([
      "AUTOCONTEXT_MODEL_ANALYST",
      "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
      "AUTOCONTEXT_MODEL",
    ]);
  });

  it("coerces env values based on field defaults", () => {
    expect(coerceEnvValue("7", 1)).toBe(7);
    expect(coerceEnvValue("false", true)).toBe(false);
    expect(coerceEnvValue("text", "default")).toBe("text");
  });

  it("resolves env overrides with alias precedence and generic model fallbacks", () => {
    const defaults = {
      agentProvider: "anthropic",
      modelCompetitor: "default",
      modelAnalyst: "default",
      modelCoach: "default",
      modelArchitect: "default",
      modelTranslator: "default",
      modelCurator: "default",
      modelSkeptic: "default",
      curatorEnabled: true,
    } satisfies Partial<AppSettings>;

    const overrides = resolveEnvSettingsOverrides(defaults, {
      AUTOCONTEXT_PROVIDER: "ollama",
      AUTOCONTEXT_AGENT_PROVIDER: "deterministic",
      AUTOCONTEXT_MODEL: "generic-model",
      AUTOCONTEXT_MODEL_ANALYST: "analyst-model",
      AUTOCONTEXT_CURATOR_ENABLED: "false",
    });

    expect(overrides).toMatchObject({
      agentProvider: "deterministic",
      modelCompetitor: "generic-model",
      modelAnalyst: "analyst-model",
      modelCoach: "generic-model",
      modelArchitect: "generic-model",
      modelTranslator: "generic-model",
      modelCurator: "generic-model",
      modelSkeptic: "generic-model",
      curatorEnabled: false,
    });
  });

  it("resolves TypeScript extension hook settings from env", () => {
    const defaults = {
      extensions: "",
      extensionFailFast: false,
    } satisfies Partial<AppSettings>;

    const overrides = resolveEnvSettingsOverrides(defaults, {
      AUTOCONTEXT_EXTENSIONS: "./hooks.mjs,autoctx-policy",
      AUTOCONTEXT_EXTENSION_FAIL_FAST: "true",
    });

    expect(overrides).toMatchObject({
      extensions: "./hooks.mjs,autoctx-policy",
      extensionFailFast: true,
    });
  });

  it("builds project-config overrides for provider, model, and artifact roots", () => {
    const overrides = buildProjectConfigSettingsOverrides({
      provider: "ollama",
      model: "llama3.2",
      knowledgeDir: "/tmp/knowledge",
      runsDir: "/tmp/runs",
      dbPath: "/tmp/runs/db.sqlite3",
      gens: 4,
    } satisfies ProjectConfig);

    expect(overrides).toMatchObject({
      agentProvider: "ollama",
      modelCompetitor: "llama3.2",
      modelAnalyst: "llama3.2",
      modelCoach: "llama3.2",
      modelArchitect: "llama3.2",
      modelTranslator: "llama3.2",
      modelCurator: "llama3.2",
      modelSkeptic: "llama3.2",
      knowledgeRoot: "/tmp/knowledge",
      runsRoot: "/tmp/runs",
      dbPath: "/tmp/runs/db.sqlite3",
      defaultGenerations: 4,
    });
  });
});

describe("loadSettings compatibility aliases", () => {
  const savedEnv = { ...process.env };

  beforeEach(() => {
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("AUTOCONTEXT_")) {
        delete process.env[key];
      }
    }
  });

  afterEach(() => {
    process.env = { ...savedEnv };
  });

  it("accepts AUTOCONTEXT_PROVIDER as a fallback for agentProvider", async () => {
    process.env.AUTOCONTEXT_PROVIDER = "deterministic";
    const { loadSettings } = await import("../src/config/index.js");

    expect(loadSettings().agentProvider).toBe("deterministic");
  });

  it("applies AUTOCONTEXT_AGENT_DEFAULT_MODEL to role models unless a role override is present", async () => {
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL = "generic-model";
    process.env.AUTOCONTEXT_MODEL_ANALYST = "analyst-model";
    const { loadSettings } = await import("../src/config/index.js");

    const settings = loadSettings();
    expect(settings.modelCompetitor).toBe("generic-model");
    expect(settings.modelAnalyst).toBe("analyst-model");
    expect(settings.modelCoach).toBe("generic-model");
    expect(settings.modelArchitect).toBe("generic-model");
  });
});
