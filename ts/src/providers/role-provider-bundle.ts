import type { LLMProvider } from "../types/index.js";
import {
  createProvider,
  type CreateProviderOpts,
} from "./provider-factory.js";
import {
  resolveProviderConfig,
  type ProviderConfig,
} from "./provider-config-resolution.js";

export type GenerationRole = "competitor" | "analyst" | "coach" | "architect" | "curator";

export interface RoleProviderSettings {
  agentProvider: string;
  competitorProvider?: string;
  analystProvider?: string;
  coachProvider?: string;
  architectProvider?: string;
  competitorApiKey?: string;
  competitorBaseUrl?: string;
  analystApiKey?: string;
  analystBaseUrl?: string;
  coachApiKey?: string;
  coachBaseUrl?: string;
  architectApiKey?: string;
  architectBaseUrl?: string;
  modelCompetitor?: string;
  modelAnalyst?: string;
  modelCoach?: string;
  modelArchitect?: string;
  modelCurator?: string;
  piCommand?: string;
  piTimeout?: number;
  piWorkspace?: string;
  piModel?: string;
  piRpcEndpoint?: string;
  piRpcApiKey?: string;
  piRpcSessionPersistence?: boolean;
}

export interface RoleProviderBundle {
  defaultProvider: LLMProvider;
  defaultConfig: ProviderConfig;
  roleProviders: Partial<Record<GenerationRole, LLMProvider>>;
  roleModels: Partial<Record<GenerationRole, string>>;
}

export function withRuntimeSettings(
  config: ProviderConfig,
  settings: Partial<RoleProviderSettings> = {},
): CreateProviderOpts {
  return {
    ...config,
    piCommand: settings.piCommand,
    piTimeout: settings.piTimeout,
    piWorkspace: settings.piWorkspace,
    piModel: settings.piModel,
    piRpcEndpoint: settings.piRpcEndpoint,
    piRpcApiKey: settings.piRpcApiKey,
    piRpcSessionPersistence: settings.piRpcSessionPersistence,
  };
}

interface RoleConfigInput {
  providerType?: string;
  model?: string;
  apiKey?: string;
  baseUrl?: string;
}

function normalizeOptionalOverride(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function resolveRoleConfig(
  defaultConfig: ProviderConfig,
  overrides: Partial<ProviderConfig>,
  roleConfig: RoleConfigInput,
): ProviderConfig {
  const providerType = normalizeOptionalOverride(roleConfig.providerType);
  const model = normalizeOptionalOverride(roleConfig.model);
  const apiKey = normalizeOptionalOverride(roleConfig.apiKey);
  const baseUrl = normalizeOptionalOverride(roleConfig.baseUrl);
  return resolveProviderConfig({
    ...overrides,
    providerType: providerType ?? defaultConfig.providerType,
    model: model ?? defaultConfig.model,
    apiKey: apiKey ?? overrides.apiKey,
    baseUrl: baseUrl ?? overrides.baseUrl,
  }, {
    preferProviderOverride: Boolean(providerType),
    preferModelOverride: Boolean(model),
    preferApiKeyOverride: Boolean(apiKey),
    preferBaseUrlOverride: Boolean(baseUrl),
  });
}

export function createConfiguredProvider(
  overrides: Partial<ProviderConfig> = {},
  settings: Partial<RoleProviderSettings> = {},
): {
  provider: LLMProvider;
  config: ProviderConfig;
} {
  const config = resolveProviderConfig(overrides);
  return {
    provider: createProvider(withRuntimeSettings(config, settings)),
    config,
  };
}

export function buildRoleProviderBundle(
  settings: RoleProviderSettings,
  overrides: Partial<ProviderConfig> = {},
): RoleProviderBundle {
  const defaultConfig = resolveProviderConfig({
    ...overrides,
    providerType: overrides.providerType ?? settings.agentProvider,
  });
  const defaultProvider = createProvider(withRuntimeSettings(defaultConfig, settings));

  const roleConfigs: Record<GenerationRole, ProviderConfig> = {
    competitor: resolveRoleConfig(defaultConfig, overrides, {
      providerType: settings.competitorProvider,
      model: settings.modelCompetitor,
      apiKey: settings.competitorApiKey,
      baseUrl: settings.competitorBaseUrl,
    }),
    analyst: resolveRoleConfig(defaultConfig, overrides, {
      providerType: settings.analystProvider,
      model: settings.modelAnalyst,
      apiKey: settings.analystApiKey,
      baseUrl: settings.analystBaseUrl,
    }),
    coach: resolveRoleConfig(defaultConfig, overrides, {
      providerType: settings.coachProvider,
      model: settings.modelCoach,
      apiKey: settings.coachApiKey,
      baseUrl: settings.coachBaseUrl,
    }),
    architect: resolveRoleConfig(defaultConfig, overrides, {
      providerType: settings.architectProvider,
      model: settings.modelArchitect,
      apiKey: settings.architectApiKey,
      baseUrl: settings.architectBaseUrl,
    }),
    curator: resolveRoleConfig(defaultConfig, overrides, {
      model: settings.modelCurator,
    }),
  };

  return {
    defaultProvider,
    defaultConfig,
    roleProviders: {
      competitor: createProvider(withRuntimeSettings(roleConfigs.competitor, settings)),
      analyst: createProvider(withRuntimeSettings(roleConfigs.analyst, settings)),
      coach: createProvider(withRuntimeSettings(roleConfigs.coach, settings)),
      architect: createProvider(withRuntimeSettings(roleConfigs.architect, settings)),
      curator: createProvider(withRuntimeSettings(roleConfigs.curator, settings)),
    },
    roleModels: {
      competitor: roleConfigs.competitor.model,
      analyst: roleConfigs.analyst.model,
      coach: roleConfigs.coach.model,
      architect: roleConfigs.architect.model,
      curator: roleConfigs.curator.model,
    },
  };
}
