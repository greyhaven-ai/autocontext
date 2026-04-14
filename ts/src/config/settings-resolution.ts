import process from "node:process";
import type { ProjectConfig } from "./project-config.js";

const MODEL_SETTING_KEYS = [
  "modelCompetitor",
  "modelAnalyst",
  "modelCoach",
  "modelArchitect",
  "modelTranslator",
  "modelCurator",
  "modelSkeptic",
] as const;

const SETTINGS_ENV_ALIASES: Partial<Record<string, string[]>> = {
  agentProvider: [
    "AUTOCONTEXT_AGENT_PROVIDER",
    "AUTOCONTEXT_PROVIDER",
  ],
  anthropicApiKey: [
    "ANTHROPIC_API_KEY",
    "AUTOCONTEXT_ANTHROPIC_API_KEY",
  ],
  modelCompetitor: [
    "AUTOCONTEXT_MODEL_COMPETITOR",
    "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
    "AUTOCONTEXT_MODEL",
  ],
  modelAnalyst: [
    "AUTOCONTEXT_MODEL_ANALYST",
    "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
    "AUTOCONTEXT_MODEL",
  ],
  modelCoach: [
    "AUTOCONTEXT_MODEL_COACH",
    "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
    "AUTOCONTEXT_MODEL",
  ],
  modelArchitect: [
    "AUTOCONTEXT_MODEL_ARCHITECT",
    "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
    "AUTOCONTEXT_MODEL",
  ],
  modelTranslator: [
    "AUTOCONTEXT_MODEL_TRANSLATOR",
    "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
    "AUTOCONTEXT_MODEL",
  ],
  modelCurator: [
    "AUTOCONTEXT_MODEL_CURATOR",
    "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
    "AUTOCONTEXT_MODEL",
  ],
  modelSkeptic: [
    "AUTOCONTEXT_MODEL_SKEPTIC",
    "AUTOCONTEXT_AGENT_DEFAULT_MODEL",
    "AUTOCONTEXT_MODEL",
  ],
};

export function camelToScreamingSnake(key: string): string {
  return key.replace(/([A-Z])/g, "_$1").toUpperCase();
}

export function getSettingEnvKeys(key: string): string[] {
  return SETTINGS_ENV_ALIASES[key] ?? [`AUTOCONTEXT_${camelToScreamingSnake(key)}`];
}

export function coerceEnvValue(val: string, fieldDefault: unknown): unknown {
  if (typeof fieldDefault === "number") {
    const parsed = Number(val);
    return Number.isNaN(parsed) ? val : parsed;
  }
  if (typeof fieldDefault === "boolean") {
    const lower = val.toLowerCase();
    if (lower === "true" || lower === "1") return true;
    if (lower === "false" || lower === "0") return false;
    return val;
  }
  return val;
}

export function resolveEnvSettingsOverrides(
  defaults: Record<string, unknown>,
  env: Record<string, string | undefined> = process.env,
): Record<string, unknown> {
  const overrides: Record<string, unknown> = {};

  for (const key of Object.keys(defaults)) {
    const envKeys = getSettingEnvKeys(key);
    const envValue = envKeys
      .map((envKey) => env[envKey])
      .find((value) => value !== undefined);

    if (envValue !== undefined) {
      overrides[key] = coerceEnvValue(envValue, defaults[key]);
    }
  }

  return overrides;
}

export function buildProjectConfigSettingsOverrides(
  projectConfig: ProjectConfig | null | undefined,
): Record<string, unknown> {
  if (!projectConfig) {
    return {};
  }

  const overrides: Record<string, unknown> = {};

  if (projectConfig.provider) {
    overrides.agentProvider = projectConfig.provider;
  }
  if (projectConfig.model) {
    for (const modelSettingKey of MODEL_SETTING_KEYS) {
      overrides[modelSettingKey] = projectConfig.model;
    }
  }
  if (projectConfig.knowledgeDir) {
    overrides.knowledgeRoot = projectConfig.knowledgeDir;
  }
  if (projectConfig.runsDir) {
    overrides.runsRoot = projectConfig.runsDir;
  }
  if (projectConfig.dbPath) {
    overrides.dbPath = projectConfig.dbPath;
  }
  if (projectConfig.gens !== undefined) {
    overrides.defaultGenerations = projectConfig.gens;
  }

  return overrides;
}
