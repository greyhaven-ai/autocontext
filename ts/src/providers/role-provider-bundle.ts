import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type { LLMProvider } from "../types/index.js";
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
}

export interface RoleProviderBundle {
  defaultProvider: LLMProvider;
  defaultConfig: ProviderConfig;
  roleProviders: Partial<Record<GenerationRole, LLMProvider>>;
  roleModels: Partial<Record<GenerationRole, string>>;
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
  const runtimeSession = createRuntimeSessionProvider(settings, opts.runtimeSession);
  const defaultConfig = resolveProviderConfig({
    ...overrides,
    providerType: overrides.providerType ?? settings.agentProvider,
  });
  const defaultProvider = createProvider(
    withRuntimeSession(defaultConfig, settings, runtimeSession, "default"),
  );

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

  const roleProviders: Partial<Record<GenerationRole, LLMProvider>> = {
    competitor: createProvider(
      withRuntimeSession(roleConfigs.competitor, settings, runtimeSession, "competitor"),
    ),
    analyst: createProvider(
      withRuntimeSession(roleConfigs.analyst, settings, runtimeSession, "analyst"),
    ),
    coach: createProvider(withRuntimeSession(roleConfigs.coach, settings, runtimeSession, "coach")),
    architect: createProvider(
      withRuntimeSession(roleConfigs.architect, settings, runtimeSession, "architect"),
    ),
    curator: createProvider(
      withRuntimeSession(roleConfigs.curator, settings, runtimeSession, "curator"),
    ),
  };
  const bundle: RoleProviderBundle = {
    defaultProvider,
    defaultConfig,
    roleProviders,
    roleModels: {
      competitor: roleConfigs.competitor.model,
      analyst: roleConfigs.analyst.model,
      coach: roleConfigs.coach.model,
      architect: roleConfigs.architect.model,
      curator: roleConfigs.curator.model,
    },
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
  const workspace = opts.workspace
    ?? createLocalWorkspaceEnv({ root: opts.workspaceRoot ?? process.cwd() });
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
