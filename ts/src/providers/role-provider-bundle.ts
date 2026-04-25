import { ProviderError, type LLMProvider } from "../types/index.js";
import { createProvider, type CreateProviderOpts } from "./provider-factory.js";
import { resolveProviderConfig, type ProviderConfig } from "./provider-config-resolution.js";
import {
  ROUTED_GENERATION_ROLES,
  routeRoleProvider,
  type GenerationRole,
  type RoleRoutingContext,
  type RoleRoutingSettings,
  type RoutedProviderConfig,
} from "./role-routing.js";

export type { GenerationRole } from "./role-routing.js";

export interface RoleProviderSettings extends RoleRoutingSettings {
  agentProvider: string;
  roleRouting?: string;
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
  modelTranslator?: string;
  tierOpusModel?: string;
  tierSonnetModel?: string;
  tierHaikuModel?: string;
  mlxModelPath?: string;
  claudeModel?: string;
  claudeFallbackModel?: string;
  claudeTools?: string | null;
  claudePermissionMode?: string;
  claudeSessionPersistence?: boolean;
  claudeTimeout?: number;
  codexModel?: string;
  codexApprovalMode?: string;
  codexTimeout?: number;
  codexWorkspace?: string;
  codexQuiet?: boolean;
  piCommand?: string;
  piTimeout?: number;
  piWorkspace?: string;
  piModel?: string;
  piNoContextFiles?: boolean;
  piRpcEndpoint?: string;
  piRpcApiKey?: string;
  piRpcSessionPersistence?: boolean;
}

export interface BuildRoleProviderBundleOptions {
  routingContext?: RoleRoutingContext;
}

export interface RoleProviderBundle {
  defaultProvider: LLMProvider;
  defaultConfig: ProviderConfig;
  roleProviders: Partial<Record<GenerationRole, LLMProvider>>;
  roleModels: Partial<Record<GenerationRole, string>>;
  roleRoutes?: Partial<Record<GenerationRole, RoutedProviderConfig>>;
}

export function withRuntimeSettings(
  config: ProviderConfig,
  settings: Partial<RoleProviderSettings> = {},
): CreateProviderOpts {
  return {
    ...config,
    claudeModel: settings.claudeModel,
    claudeFallbackModel: settings.claudeFallbackModel,
    claudeTools: settings.claudeTools ?? undefined,
    claudePermissionMode: settings.claudePermissionMode,
    claudeSessionPersistence: settings.claudeSessionPersistence,
    claudeTimeout: settings.claudeTimeout,
    codexModel: settings.codexModel,
    codexApprovalMode: settings.codexApprovalMode,
    codexTimeout: settings.codexTimeout,
    codexWorkspace: settings.codexWorkspace,
    codexQuiet: settings.codexQuiet,
    piCommand: settings.piCommand,
    piTimeout: settings.piTimeout,
    piWorkspace: settings.piWorkspace,
    piModel: settings.piModel,
    piNoContextFiles: settings.piNoContextFiles,
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
  return resolveProviderConfig(
    {
      ...overrides,
      providerType: providerType ?? defaultConfig.providerType,
      model: model ?? defaultConfig.model,
      apiKey: apiKey ?? overrides.apiKey,
      baseUrl: baseUrl ?? overrides.baseUrl,
    },
    {
      preferProviderOverride: Boolean(providerType),
      preferModelOverride: Boolean(model),
      preferApiKeyOverride: Boolean(apiKey),
      preferBaseUrlOverride: Boolean(baseUrl),
    },
  );
}

function roleConfigInputForRole(
  role: GenerationRole,
  settings: RoleProviderSettings,
): RoleConfigInput {
  switch (role) {
    case "competitor":
      return {
        providerType: settings.competitorProvider,
        model: settings.modelCompetitor,
        apiKey: settings.competitorApiKey,
        baseUrl: settings.competitorBaseUrl,
      };
    case "analyst":
      return {
        providerType: settings.analystProvider,
        model: settings.modelAnalyst,
        apiKey: settings.analystApiKey,
        baseUrl: settings.analystBaseUrl,
      };
    case "coach":
      return {
        providerType: settings.coachProvider,
        model: settings.modelCoach,
        apiKey: settings.coachApiKey,
        baseUrl: settings.coachBaseUrl,
      };
    case "architect":
      return {
        providerType: settings.architectProvider,
        model: settings.modelArchitect,
        apiKey: settings.architectApiKey,
        baseUrl: settings.architectBaseUrl,
      };
    case "curator":
      return {
        model: settings.modelCurator,
      };
    case "translator":
      return {
        model: settings.modelTranslator,
      };
  }
}

function assertRoutedProviderIsExecutable(role: GenerationRole, routed: RoutedProviderConfig): void {
  if (routed.executableInTypeScript) {
    return;
  }

  const reason = routed.unsupportedReason
    ?? "TypeScript provider runtime does not support routed provider";
  throw new ProviderError(`${reason} for role ${JSON.stringify(role)}.`);
}

function resolveRoutedRoleConfig(
  overrides: Partial<ProviderConfig>,
  roleConfig: RoleConfigInput,
  routed: RoutedProviderConfig,
): ProviderConfig {
  const apiKey = normalizeOptionalOverride(roleConfig.apiKey);
  const baseUrl = normalizeOptionalOverride(roleConfig.baseUrl);

  return resolveProviderConfig(
    {
      ...overrides,
      providerType: routed.providerType,
      model: routed.model,
      apiKey: apiKey ?? overrides.apiKey,
      baseUrl: baseUrl ?? overrides.baseUrl,
    },
    {
      preferProviderOverride: true,
      preferModelOverride: Boolean(routed.model),
      preferApiKeyOverride: Boolean(apiKey ?? overrides.apiKey),
      preferBaseUrlOverride: Boolean(baseUrl ?? overrides.baseUrl),
    },
  );
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
  options: BuildRoleProviderBundleOptions = {},
): RoleProviderBundle {
  const defaultConfig = resolveProviderConfig({
    ...overrides,
    providerType: overrides.providerType ?? settings.agentProvider,
  });
  const defaultProvider = createProvider(withRuntimeSettings(defaultConfig, settings));

  const roleConfigs = {} as Record<GenerationRole, ProviderConfig>;
  const roleRoutes: Partial<Record<GenerationRole, RoutedProviderConfig>> = {};
  const effectiveRoutingSettings: RoleProviderSettings = {
    ...settings,
    agentProvider: defaultConfig.providerType,
  };

  for (const role of ROUTED_GENERATION_ROLES) {
    const roleConfig = roleConfigInputForRole(role, settings);
    if (settings.roleRouting === "auto") {
      const routed = routeRoleProvider(effectiveRoutingSettings, role, options.routingContext);
      roleRoutes[role] = routed;
      assertRoutedProviderIsExecutable(role, routed);
      roleConfigs[role] = resolveRoutedRoleConfig(overrides, roleConfig, routed);
    } else {
      roleConfigs[role] = resolveRoleConfig(defaultConfig, overrides, roleConfig);
    }
  }

  const roleProviders: Partial<Record<GenerationRole, LLMProvider>> = {};
  const roleModels: Partial<Record<GenerationRole, string>> = {};
  for (const role of ROUTED_GENERATION_ROLES) {
    roleProviders[role] = createProvider(withRuntimeSettings(roleConfigs[role], settings));
    roleModels[role] = roleConfigs[role].model;
  }

  return {
    defaultProvider,
    defaultConfig,
    roleProviders,
    roleModels,
    ...(settings.roleRouting === "auto" ? { roleRoutes } : {}),
  };
}
