import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { ProviderError, type LLMProvider } from "../types/index.js";
import { createProvider, type CreateProviderOpts } from "./provider-factory.js";
import { resolveProviderConfig, type ProviderConfig } from "./provider-config-resolution.js";
import {
  createLocalWorkspaceEnv,
  type RuntimeCommandGrant,
  type RuntimeWorkspaceEnv,
} from "../runtimes/workspace-env.js";
import { RuntimeSession } from "../session/runtime-session.js";
import { RuntimeSessionEventStore } from "../session/runtime-events.js";
import type { RuntimeSessionEventSink } from "../session/runtime-session-notifications.js";
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
  piRpcPersistent?: boolean;
  dbPath?: string;
}

export interface ProviderRuntimeSessionOpts {
  sessionId?: string;
  goal: string;
  dbPath?: string;
  workspace?: RuntimeWorkspaceEnv;
  workspaceRoot?: string;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
  metadata?: Record<string, unknown>;
  eventSink?: RuntimeSessionEventSink;
}

export interface ProviderCompositionOpts {
  runtimeSession?: ProviderRuntimeSessionOpts;
  routingContext?: RoleRoutingContext;
}

export interface RoleProviderBundle {
  defaultProvider: LLMProvider;
  defaultConfig: ProviderConfig;
  roleProviders: Partial<Record<GenerationRole, LLMProvider>>;
  roleModels: Partial<Record<GenerationRole, string>>;
  roleRoutes?: Partial<Record<GenerationRole, RoutedProviderConfig>>;
  runtimeSession?: RuntimeSession;
  close?: () => void;
}

export function closeProviderBundle(
  bundle: Pick<RoleProviderBundle, "defaultProvider" | "roleProviders">,
): void {
  const closed = new Set<LLMProvider>();
  const closeProvider = (provider: LLMProvider | undefined): void => {
    if (!provider || closed.has(provider)) return;
    closed.add(provider);
    provider.close?.();
  };
  closeProvider(bundle.defaultProvider);
  for (const provider of Object.values(bundle.roleProviders)) {
    closeProvider(provider);
  }
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
    piRpcPersistent: settings.piRpcPersistent,
  };
}

function withRuntimeSession(
  config: ProviderConfig,
  settings: Partial<RoleProviderSettings>,
  runtimeSession: RuntimeSessionProvider | undefined,
  role: GenerationRole | "default",
): CreateProviderOpts {
  const base = withRuntimeSettings(config, settings);
  if (!runtimeSession) return base;
  return {
    ...base,
    runtimeSession: runtimeSession.session,
    runtimeSessionRole: role,
    runtimeSessionCwd: runtimeSession.cwd,
    runtimeSessionCommands: runtimeSession.commands,
  };
}

function withRoutedRuntimeModel(
  config: ProviderConfig,
  opts: CreateProviderOpts,
): CreateProviderOpts {
  if (config.providerType === "claude-cli") {
    return { ...opts, claudeModel: config.model };
  }
  if (config.providerType === "codex") {
    return { ...opts, codexModel: config.model };
  }
  return opts;
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

function assertRoutedProviderIsExecutable(
  role: GenerationRole,
  routed: RoutedProviderConfig,
): void {
  if (routed.executableInTypeScript) {
    return;
  }
  throw new ProviderError(
    `${routed.unsupportedReason ?? "TypeScript provider runtime does not support routed provider"} for role ${JSON.stringify(role)}.`,
  );
}

function resolveRoutedRoleConfig(
  defaultConfig: ProviderConfig,
  overrides: Partial<ProviderConfig>,
  roleConfig: RoleConfigInput,
  routed: RoutedProviderConfig,
): ProviderConfig {
  return resolveRoleConfig(defaultConfig, overrides, {
    providerType: routed.providerType,
    model: routed.model,
    apiKey: roleConfig.apiKey,
    baseUrl: roleConfig.baseUrl,
  });
}

export function createConfiguredProvider(
  overrides: Partial<ProviderConfig> = {},
  settings: Partial<RoleProviderSettings> = {},
  opts: ProviderCompositionOpts = {},
): {
  provider: LLMProvider;
  config: ProviderConfig;
  runtimeSession?: RuntimeSession;
  close?: () => void;
} {
  const config = resolveProviderConfig(overrides);
  const runtimeSession = createRuntimeSessionProvider(settings, opts.runtimeSession);
  const provider = createProvider(withRuntimeSession(config, settings, runtimeSession, "default"));
  let closed = false;
  return {
    provider,
    config,
    runtimeSession: runtimeSession?.session,
    close: () => {
      if (closed) return;
      closed = true;
      provider.close?.();
      runtimeSession?.eventStore.close();
    },
  };
}

export function buildRoleProviderBundle(
  settings: RoleProviderSettings,
  overrides: Partial<ProviderConfig> = {},
  opts: ProviderCompositionOpts = {},
): RoleProviderBundle {
  const defaultConfig = resolveProviderConfig({
    ...overrides,
    providerType: overrides.providerType ?? settings.agentProvider,
  });
  const roleRoutes = Object.fromEntries(
    ROUTED_GENERATION_ROLES.map((role) => [
      role,
      routeRoleProvider(settings, role, opts.routingContext),
    ]),
  ) as Record<GenerationRole, RoutedProviderConfig>;
  for (const role of ROUTED_GENERATION_ROLES) {
    assertRoutedProviderIsExecutable(role, roleRoutes[role]);
  }

  const roleConfigs: Record<GenerationRole, ProviderConfig> = {
    competitor: resolveRoutedRoleConfig(
      defaultConfig,
      overrides,
      roleConfigInputForRole("competitor", settings),
      roleRoutes.competitor,
    ),
    analyst: resolveRoutedRoleConfig(
      defaultConfig,
      overrides,
      roleConfigInputForRole("analyst", settings),
      roleRoutes.analyst,
    ),
    coach: resolveRoutedRoleConfig(
      defaultConfig,
      overrides,
      roleConfigInputForRole("coach", settings),
      roleRoutes.coach,
    ),
    architect: resolveRoutedRoleConfig(
      defaultConfig,
      overrides,
      roleConfigInputForRole("architect", settings),
      roleRoutes.architect,
    ),
    curator: resolveRoutedRoleConfig(
      defaultConfig,
      overrides,
      roleConfigInputForRole("curator", settings),
      roleRoutes.curator,
    ),
    translator: resolveRoutedRoleConfig(
      defaultConfig,
      overrides,
      roleConfigInputForRole("translator", settings),
      roleRoutes.translator,
    ),
  };

  const runtimeSession = createRuntimeSessionProvider(settings, opts.runtimeSession);
  const defaultProvider = createProvider(
    withRuntimeSession(defaultConfig, settings, runtimeSession, "default"),
  );
  const roleProviders = Object.fromEntries(
    ROUTED_GENERATION_ROLES.map((role) => [
      role,
      createProvider(
        withRoutedRuntimeModel(
          roleConfigs[role],
          withRuntimeSession(roleConfigs[role], settings, runtimeSession, role),
        ),
      ),
    ]),
  ) as Partial<Record<GenerationRole, LLMProvider>>;
  const roleModels = Object.fromEntries(
    ROUTED_GENERATION_ROLES.map((role) => [role, roleConfigs[role].model]),
  ) as Partial<Record<GenerationRole, string>>;
  const bundle: RoleProviderBundle = {
    defaultProvider,
    defaultConfig,
    roleProviders,
    roleModels,
    roleRoutes,
    runtimeSession: runtimeSession?.session,
  };
  let closed = false;
  return {
    ...bundle,
    close: () => {
      if (closed) return;
      closed = true;
      closeProviderBundle(bundle);
      runtimeSession?.eventStore.close();
    },
  };
}

interface RuntimeSessionProvider {
  session: RuntimeSession;
  eventStore: RuntimeSessionEventStore;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
}

function createRuntimeSessionProvider(
  settings: Partial<RoleProviderSettings>,
  opts?: ProviderRuntimeSessionOpts,
): RuntimeSessionProvider | undefined {
  if (!opts) return undefined;
  const dbPath = opts.dbPath ?? settings.dbPath;
  if (!dbPath) {
    throw new Error("Runtime session provider recording requires a dbPath");
  }
  const resolvedDbPath = resolve(dbPath);
  mkdirSync(dirname(resolvedDbPath), { recursive: true });
  const eventStore = new RuntimeSessionEventStore(resolvedDbPath);
  const workspace =
    opts.workspace ?? createLocalWorkspaceEnv({ root: opts.workspaceRoot ?? process.cwd() });
  const session = RuntimeSession.create({
    sessionId: opts.sessionId,
    goal: opts.goal,
    workspace,
    eventStore,
    eventSink: opts.eventSink,
    metadata: opts.metadata,
  });
  return {
    session,
    eventStore,
    cwd: opts.cwd,
    commands: opts.commands,
  };
}
