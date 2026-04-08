/**
 * Config/Settings — Full AppSettings Zod schema with AUTOCONTEXT_* env var loading.
 * Mirrors Python's autocontext/config/settings.py + presets.py.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { z } from "zod";
import { resolveApiKeyValue } from "./credentials.js";

// ---------------------------------------------------------------------------
// Presets (mirrors Python config/presets.py)
// ---------------------------------------------------------------------------

const LONG_RUN_PRESET_SETTINGS: Record<string, unknown> = {
  stagnationResetEnabled: true,
  deadEndTrackingEnabled: true,
  curatorEnabled: true,
  twoTierGatingEnabled: true,
  maxRetries: 3,
  stagnationRollbackThreshold: 5,
  stagnationPlateauWindow: 3,
  crossRunInheritance: true,
};

const SHORT_RUN_PRESET_SETTINGS: Record<string, unknown> = {
  stagnationResetEnabled: false,
  deadEndTrackingEnabled: false,
  curatorEnabled: false,
  twoTierGatingEnabled: false,
  maxRetries: 2,
};

export const PRESETS: Map<string, Record<string, unknown>> = new Map([
  [
    "quick",
    {
      matchesPerGeneration: 2,
      curatorEnabled: false,
      probeMatches: 0,
      coherenceCheckEnabled: false,
      maxRetries: 0,
    },
  ],
  [
    "standard",
    {
      matchesPerGeneration: 3,
      curatorEnabled: true,
      backpressureMode: "trend",
      crossRunInheritance: true,
    },
  ],
  [
    "deep",
    {
      matchesPerGeneration: 5,
      curatorEnabled: true,
      curatorConsolidateEveryNGens: 3,
      probeMatches: 2,
      coherenceCheckEnabled: true,
    },
  ],
  [
    "rapid",
    {
      backpressureMinDelta: 0.0,
      backpressureMode: "simple",
      curatorEnabled: false,
      maxRetries: 0,
      matchesPerGeneration: 2,
      rlmMaxTurns: 5,
      probeMatches: 0,
      coherenceCheckEnabled: false,
      constraintPromptsEnabled: false,
    },
  ],
  ["long_run", { ...LONG_RUN_PRESET_SETTINGS }],
  ["short_run", { ...SHORT_RUN_PRESET_SETTINGS }],
]);

export function applyPreset(name: string): Record<string, unknown> {
  if (!name) return {};
  const preset = PRESETS.get(name);
  if (!preset) {
    throw new Error(
      `Unknown preset '${name}'. Valid presets: ${[...PRESETS.keys()].sort().join(", ")}`,
    );
  }
  return { ...preset };
}

// ---------------------------------------------------------------------------
// costBudgetLimit preprocessor: treat 0 or empty string as null
// ---------------------------------------------------------------------------

const costBudgetLimitPreprocess = z.preprocess((val) => {
  if (val === null || val === undefined || val === "") return null;
  const n = Number(val);
  return n > 0 ? n : null;
}, z.number().positive().nullable().default(null));

// ---------------------------------------------------------------------------
// AppSettingsSchema
// ---------------------------------------------------------------------------

export const AppSettingsSchema = z.object({
  // Paths
  dbPath: z.string().default("runs/autocontext.sqlite3"),
  runsRoot: z.string().default("runs"),
  knowledgeRoot: z.string().default("knowledge"),
  skillsRoot: z.string().default("skills"),
  claudeSkillsPath: z.string().default(".claude/skills"),
  eventStreamPath: z.string().default("runs/events.ndjson"),

  // Core
  executorMode: z.string().default("local"),
  agentProvider: z.string().default("anthropic"),
  anthropicApiKey: z.string().nullable().default(null),

  // Models
  modelCompetitor: z.string().default("claude-sonnet-4-5-20250929"),
  modelAnalyst: z.string().default("claude-sonnet-4-5-20250929"),
  modelCoach: z.string().default("claude-opus-4-6"),
  modelArchitect: z.string().default("claude-opus-4-6"),
  modelTranslator: z.string().default("claude-sonnet-4-5-20250929"),
  modelCurator: z.string().default("claude-opus-4-6"),
  modelSkeptic: z.string().default("claude-opus-4-6"),

  // Loop tuning
  architectEveryNGens: z.number().int().min(1).default(3),
  matchesPerGeneration: z.number().int().min(1).default(3),
  backpressureMinDelta: z.number().default(0.005),
  backpressureMode: z.string().default("simple"),
  backpressurePlateauWindow: z.number().int().min(1).default(3),
  backpressurePlateauRelaxation: z.number().min(0).max(1).default(0.5),
  defaultGenerations: z.number().int().min(1).default(1),
  seedBase: z.number().int().default(1000),
  maxRetries: z.number().int().min(0).default(2),
  retryBackoffSeconds: z.number().min(0).default(0.25),

  // Scoring
  scoringBackend: z.string().default("elo"),
  scoringDimensionRegressionThreshold: z.number().min(0).max(1).default(0.1),
  selfPlayEnabled: z.boolean().default(false),
  selfPlayPoolSize: z.number().int().min(1).default(3),
  selfPlayWeight: z.number().min(0).max(1).default(0.5),

  // Hint volume
  hintVolumeEnabled: z.boolean().default(true),
  hintVolumeMaxHints: z.number().int().min(1).default(7),
  hintVolumeArchiveRotated: z.boolean().default(true),

  // Evidence freshness
  evidenceFreshnessEnabled: z.boolean().default(true),
  evidenceFreshnessMaxAgeGens: z.number().int().min(1).default(10),
  evidenceFreshnessMinConfidence: z.number().min(0).max(1).default(0.4),
  evidenceFreshnessMinSupport: z.number().int().min(0).default(1),

  // Regression fixtures
  regressionFixturesEnabled: z.boolean().default(true),
  regressionFixtureMinOccurrences: z.number().int().min(1).default(2),
  prevalidationRegressionFixturesEnabled: z.boolean().default(true),
  prevalidationRegressionFixtureLimit: z.number().int().min(1).default(5),

  // Holdout
  holdoutEnabled: z.boolean().default(true),
  holdoutSeeds: z.number().int().min(1).default(5),
  holdoutMinScore: z.number().min(0).max(1).default(0.0),
  holdoutMaxRegressionGap: z.number().min(0).max(1).default(0.2),
  holdoutSeedOffset: z.number().int().min(1).default(10000),

  // Time budget
  generationTimeBudgetSeconds: z.number().int().min(0).default(0),
  generationScaffoldingBudgetRatio: z.number().min(0).max(1).default(0.4),
  generationPhaseBudgetRolloverEnabled: z.boolean().default(true),

  // PrimeIntellect
  primeintellectApiBase: z.string().default("https://api.primeintellect.ai"),
  primeintellectApiKey: z.string().nullable().default(null),

  // OpenClaw runtime
  openclawRuntimeKind: z.string().default("factory"),
  openclawAgentFactory: z.string().default(""),
  openclawAgentCommand: z.string().default(""),
  openclawAgentHttpEndpoint: z.string().default(""),
  openclawAgentHttpHeaders: z.string().default(""),
  openclawCompatibilityVersion: z.string().default("1.0"),
  openclawTimeoutSeconds: z.number().min(1.0).default(30.0),
  openclawMaxRetries: z.number().int().min(0).default(2),
  openclawRetryBaseDelay: z.number().min(0.0).default(0.25),
  openclawDistillSidecarFactory: z.string().default(""),
  openclawDistillSidecarCommand: z.string().default(""),

  // Pi CLI runtime
  piCommand: z.string().default("pi"),
  piTimeout: z.number().min(1).default(120.0),
  piWorkspace: z.string().default(""),
  piModel: z.string().default(""),

  // Pi RPC runtime
  piRpcEndpoint: z.string().default(""),
  piRpcApiKey: z.string().default(""),
  piRpcSessionPersistence: z.boolean().default(true),

  // Feature flags
  ablationNoFeedback: z.boolean().default(false),
  rlmEnabled: z.boolean().default(false),
  rlmMaxTurns: z.number().int().min(1).max(50).default(25),
  rlmMaxStdoutChars: z.number().int().min(1024).default(8192),
  rlmSubModel: z.string().default("claude-haiku-4-5-20251001"),
  rlmCodeTimeoutSeconds: z.number().min(1).default(10.0),
  rlmBackend: z.string().default("exec"),
  rlmCompetitorEnabled: z.boolean().default(false),

  // Knowledge
  playbookMaxVersions: z.number().int().min(1).default(5),
  crossRunInheritance: z.boolean().default(true),

  // Curator
  curatorEnabled: z.boolean().default(true),
  curatorConsolidateEveryNGens: z.number().int().min(1).default(3),
  skillMaxLessons: z.number().int().min(1).default(30),

  // Skeptic
  skepticEnabled: z.boolean().default(false),
  skepticCanBlock: z.boolean().default(false),

  // Code strategies
  codeStrategiesEnabled: z.boolean().default(false),
  policyRefinementEnabled: z.boolean().default(false),

  // Cost
  auditEnabled: z.boolean().default(true),
  costTrackingEnabled: z.boolean().default(true),
  costBudgetLimit: costBudgetLimitPreprocess,
  costPerGenerationLimit: z.number().min(0).default(0.0),
  costThrottleAboveTotal: z.number().min(0).default(0.0),
  costMaxPerDeltaPoint: z.number().positive().default(10.0),

  // Judge
  judgeModel: z.string().default("claude-sonnet-4-20250514"),
  judgeSamples: z.number().int().min(1).default(1),
  judgeTemperature: z.number().min(0).default(0.0),
  judgeProvider: z.string().default("anthropic"),
  judgeBaseUrl: z.string().nullable().default(null),
  judgeApiKey: z.string().nullable().default(null),
  judgeDisagreementThreshold: z.number().min(0).max(1).default(0.15),
  judgeBiasProbesEnabled: z.boolean().default(false),

  // Notifications
  notifyWebhookUrl: z.string().nullable().default(null),
  notifyOn: z.string().default("threshold_met,failure"),

  // Stagnation
  stagnationResetEnabled: z.boolean().default(false),
  stagnationRollbackThreshold: z.number().int().min(1).default(5),
  stagnationPlateauWindow: z.number().int().min(2).default(5),
  stagnationPlateauEpsilon: z.number().min(0).default(0.01),
  stagnationDistillTopLessons: z.number().int().min(1).default(5),

  // Progress & constraints
  progressJsonEnabled: z.boolean().default(true),
  constraintPromptsEnabled: z.boolean().default(true),
  contextBudgetTokens: z.number().int().min(0).default(100_000),
  coherenceCheckEnabled: z.boolean().default(true),

  // Prevalidation
  prevalidationEnabled: z.boolean().default(false),
  prevalidationMaxRetries: z.number().int().min(0).max(5).default(2),
  prevalidationDryRunEnabled: z.boolean().default(true),

  // Harness
  harnessValidatorsEnabled: z.boolean().default(false),
  harnessTimeoutSeconds: z.number().min(0.5).max(60).default(5.0),
  harnessInheritanceEnabled: z.boolean().default(true),
  harnessMode: z.enum(["none", "filter", "verify", "policy"]).default("none"),
  probeMatches: z.number().int().min(0).default(0),

  // Ecosystem
  ecosystemConvergenceEnabled: z.boolean().default(false),
  ecosystemDivergenceThreshold: z.number().min(0).max(1).default(0.3),
  ecosystemOscillationWindow: z.number().int().min(2).default(3),

  // Dead-end tracking
  deadEndTrackingEnabled: z.boolean().default(false),
  deadEndMaxEntries: z.number().int().min(1).default(20),

  // Exploration
  explorationMode: z.enum(["linear", "rapid", "tree"]).default("linear"),
  rapidGens: z.number().int().min(0).default(0),
  noveltyEnabled: z.boolean().default(true),
  noveltyWeight: z.number().min(0).max(1).default(0.1),
  noveltyHistoryWindow: z.number().int().min(1).default(5),
  divergentCompetitorEnabled: z.boolean().default(true),
  divergentRollbackThreshold: z.number().int().min(1).default(5),
  divergentTemperature: z.number().min(0).max(2).default(0.7),
  multiBasinEnabled: z.boolean().default(false),
  multiBasinTriggerRollbacks: z.number().int().min(1).default(3),
  multiBasinCandidates: z.number().int().min(1).max(3).default(3),
  multiBasinPeriodicEveryN: z.number().int().min(0).default(0),

  // Two-tier gating
  twoTierGatingEnabled: z.boolean().default(false),
  validityMaxRetries: z.number().int().min(0).default(3),

  // Per-role provider overrides
  competitorProvider: z.string().default(""),
  analystProvider: z.string().default(""),
  coachProvider: z.string().default(""),
  architectProvider: z.string().default(""),

  // Monitor
  monitorEnabled: z.boolean().default(true),
  monitorHeartbeatTimeout: z.number().min(1).default(300.0),
  monitorMaxConditions: z.number().int().min(1).default(100),
  // Blob store (AC-518)
  blobStoreEnabled: z.boolean().default(false),
  blobStoreBackend: z.string().default("local"),
  blobStoreRoot: z.string().default("./blobs"),
  blobStoreRepo: z.string().default(""),
  blobStoreCacheMaxMb: z.number().int().min(1).default(500),
  blobStoreMinSizeBytes: z.number().int().min(0).default(1024),
});

export type AppSettings = z.infer<typeof AppSettingsSchema>;

// ---------------------------------------------------------------------------
// camelCase <-> SCREAMING_SNAKE mapping for env vars
// ---------------------------------------------------------------------------

function camelToScreamingSnake(key: string): string {
  return key.replace(/([A-Z])/g, "_$1").toUpperCase();
}

function coerceEnvValue(val: string, fieldDefault: unknown): unknown {
  if (typeof fieldDefault === "number") {
    const n = Number(val);
    return Number.isNaN(n) ? val : n;
  }
  if (typeof fieldDefault === "boolean") {
    const lower = val.toLowerCase();
    if (lower === "true" || lower === "1") return true;
    if (lower === "false" || lower === "0") return false;
    return val;
  }
  return val;
}

// ---------------------------------------------------------------------------
// Project config + persisted credentials
// ---------------------------------------------------------------------------

const PROJECT_CONFIG_FILE = ".autoctx.json";
const CREDENTIALS_FILE = "credentials.json";

export interface ProjectConfig {
  defaultScenario?: string;
  provider?: string;
  model?: string;
  gens?: number;
  knowledgeDir?: string;
  runsDir?: string;
  dbPath?: string;
}

export interface ProjectConfigLocation {
  path: string;
  source: "autoctx_json" | "package_json";
}

export interface StoredCredentials {
  provider?: string;
  apiKey?: string;
  model?: string;
  baseUrl?: string;
  savedAt?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readJsonObject(path: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, "utf-8"));
  } catch (err) {
    throw new Error(`Invalid ${label}: ${(err as Error).message}`);
  }
  if (!isRecord(parsed)) {
    throw new Error(`Invalid ${label}: expected a JSON object`);
  }
  return parsed;
}

function coercePositiveInt(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isInteger(value) && value > 0) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number.parseInt(value, 10);
    if (Number.isInteger(parsed) && parsed > 0) {
      return parsed;
    }
  }
  return undefined;
}

export function findProjectConfigPath(startDir = process.cwd()): string | null {
  let current = resolve(startDir);
  while (true) {
    const candidate = join(current, PROJECT_CONFIG_FILE);
    if (existsSync(candidate)) {
      return candidate;
    }
    const parent = dirname(current);
    if (parent === current) {
      return null;
    }
    current = parent;
  }
}

function findProjectConfigSource(startDir = process.cwd()): {
  location: ProjectConfigLocation;
  raw: Record<string, unknown>;
} | null {
  let current = resolve(startDir);
  while (true) {
    const configPath = join(current, PROJECT_CONFIG_FILE);
    if (existsSync(configPath)) {
      return {
        location: { path: configPath, source: "autoctx_json" },
        raw: readJsonObject(configPath, PROJECT_CONFIG_FILE),
      };
    }

    const pkgJsonPath = join(current, "package.json");
    if (existsSync(pkgJsonPath)) {
      const pkg = readJsonObject(pkgJsonPath, "package.json");
      if (isRecord(pkg.autoctx)) {
        return {
          location: { path: pkgJsonPath, source: "package_json" },
          raw: pkg.autoctx as Record<string, unknown>,
        };
      }
    }

    const parent = dirname(current);
    if (parent === current) {
      return null;
    }
    current = parent;
  }
}

export function findProjectConfigLocation(
  startDir = process.cwd(),
): ProjectConfigLocation | null {
  return findProjectConfigSource(startDir)?.location ?? null;
}

export function loadProjectConfig(
  startDir = process.cwd(),
): ProjectConfig | null {
  const configSource = findProjectConfigSource(startDir);
  if (!configSource) {
    return null;
  }
  return parseProjectConfigRaw(
    configSource.raw,
    dirname(configSource.location.path),
  );
}

function parseProjectConfigRaw(
  raw: Record<string, unknown>,
  rootDir: string,
): ProjectConfig {
  const config: ProjectConfig = {};

  if (typeof raw.default_scenario === "string" && raw.default_scenario.trim()) {
    config.defaultScenario = raw.default_scenario.trim();
  }
  // Also accept camelCase (for package.json convention)
  if (
    !config.defaultScenario &&
    typeof raw.defaultScenario === "string" &&
    (raw.defaultScenario as string).trim()
  ) {
    config.defaultScenario = (raw.defaultScenario as string).trim();
  }
  if (typeof raw.provider === "string" && raw.provider.trim()) {
    config.provider = raw.provider.trim();
  }
  if (typeof raw.model === "string" && raw.model.trim()) {
    config.model = raw.model.trim();
  }
  if (typeof raw.knowledge_dir === "string" && raw.knowledge_dir.trim()) {
    config.knowledgeDir = resolve(rootDir, raw.knowledge_dir.trim());
  }
  if (
    !config.knowledgeDir &&
    typeof raw.knowledgeDir === "string" &&
    raw.knowledgeDir.trim()
  ) {
    config.knowledgeDir = resolve(rootDir, raw.knowledgeDir.trim());
  }
  if (typeof raw.runs_dir === "string" && raw.runs_dir.trim()) {
    config.runsDir = resolve(rootDir, raw.runs_dir.trim());
  }
  if (
    !config.runsDir &&
    typeof raw.runsDir === "string" &&
    raw.runsDir.trim()
  ) {
    config.runsDir = resolve(rootDir, raw.runsDir.trim());
  }
  if (typeof raw.db_path === "string" && raw.db_path.trim()) {
    config.dbPath = resolve(rootDir, raw.db_path.trim());
  }
  if (!config.dbPath && typeof raw.dbPath === "string" && raw.dbPath.trim()) {
    config.dbPath = resolve(rootDir, raw.dbPath.trim());
  }
  if (!config.dbPath && config.runsDir) {
    config.dbPath = join(config.runsDir, "autocontext.sqlite3");
  }
  config.gens = coercePositiveInt(raw.gens);

  return config;
}

export function resolveConfigDir(explicit?: string): string {
  return (
    explicit ??
    process.env.AUTOCONTEXT_CONFIG_DIR ??
    join(process.env.HOME ?? "~", ".config", "autoctx")
  );
}

function readStoredCredentialEntry(
  providerName: string,
  entry: Record<string, unknown>,
): StoredCredentials {
  const credentials: StoredCredentials = { provider: providerName };
  if (typeof entry.apiKey === "string" && entry.apiKey.trim()) {
    credentials.apiKey = resolveApiKeyValue(entry.apiKey.trim());
  }
  if (typeof entry.model === "string" && entry.model.trim()) {
    credentials.model = entry.model.trim();
  }
  if (typeof entry.baseUrl === "string" && entry.baseUrl.trim()) {
    credentials.baseUrl = entry.baseUrl.trim();
  }
  if (typeof entry.savedAt === "string" && entry.savedAt.trim()) {
    credentials.savedAt = entry.savedAt.trim();
  }
  return credentials;
}

export function loadPersistedCredentials(
  configDir = resolveConfigDir(),
  provider?: string,
): StoredCredentials | null {
  const credentialsPath = join(configDir, CREDENTIALS_FILE);
  if (!existsSync(credentialsPath)) {
    return null;
  }

  const raw = readJsonObject(credentialsPath, CREDENTIALS_FILE);
  const requestedProvider = provider?.trim().toLowerCase();

  // New multi-provider format (AC-430): { providers: { anthropic: { apiKey, ... } } }
  if (isRecord(raw.providers)) {
    const providers = raw.providers as Record<string, Record<string, unknown>>;
    const names = Object.keys(providers);
    if (names.length === 0) return null;

    if (requestedProvider) {
      const matchedName = names.find(
        (name) => name.toLowerCase() === requestedProvider,
      );
      if (!matchedName) {
        return null;
      }
      return readStoredCredentialEntry(matchedName, providers[matchedName]);
    }

    // Return the first provider as the "active" one for backward compat
    const firstName = names[0];
    return readStoredCredentialEntry(firstName, providers[firstName]);
  }

  // Legacy single-provider format
  const credentials: StoredCredentials = {};
  if (typeof raw.provider === "string" && raw.provider.trim()) {
    credentials.provider = raw.provider.trim();
  }
  if (
    requestedProvider &&
    credentials.provider &&
    credentials.provider.toLowerCase() !== requestedProvider
  ) {
    return null;
  }
  if (requestedProvider && !credentials.provider) {
    credentials.provider = requestedProvider;
  }
  if (typeof raw.apiKey === "string" && raw.apiKey.trim()) {
    credentials.apiKey = resolveApiKeyValue(raw.apiKey.trim());
  }
  if (typeof raw.model === "string" && raw.model.trim()) {
    credentials.model = raw.model.trim();
  }
  if (typeof raw.baseUrl === "string" && raw.baseUrl.trim()) {
    credentials.baseUrl = raw.baseUrl.trim();
  }
  if (typeof raw.savedAt === "string" && raw.savedAt.trim()) {
    credentials.savedAt = raw.savedAt.trim();
  }

  return credentials;
}

// ---------------------------------------------------------------------------
// loadSettings — read from AUTOCONTEXT_* env vars + optional preset
// ---------------------------------------------------------------------------

// Re-export credential hardening module (AC-430)
export {
  resolveApiKeyValue,
  saveProviderCredentials,
  loadProviderCredentials,
  removeProviderCredentials,
  listConfiguredProviders,
  discoverAllProviders,
  validateApiKey,
  getKnownProvider,
  getModelsForProvider,
  resolveModel,
  listAuthenticatedModels,
  KNOWN_PROVIDERS,
  PROVIDER_MODELS,
  CREDENTIALS_FILE as CREDENTIALS_STORE_FILE,
} from "./credentials.js";
export type {
  ProviderCredentials,
  ProviderAuthStatus,
  DiscoveredProvider,
  KnownProvider,
  KnownModel,
  AuthenticatedModel,
  ResolveModelOpts,
  ValidationResult as ApiKeyValidationResult,
} from "./credentials.js";

// OAuth (AC-430 Phase 4)
export {
  generatePKCE,
  generateState,
  buildAuthorizationUrl,
  waitForCallback,
  isOAuthProvider,
  saveOAuthTokens,
  loadOAuthTokens,
  isTokenExpired,
  OAUTH_PROVIDERS,
} from "./oauth.js";
export type {
  PKCEPair,
  OAuthFlow,
  OAuthProviderConfig,
  CallbackResult,
  WaitForCallbackOpts,
  OAuthTokens,
} from "./oauth.js";

export function loadSettings(): AppSettings {
  const presetName = process.env.AUTOCONTEXT_PRESET ?? "";
  const presetOverrides = applyPreset(presetName);

  // Derive defaults from the schema to understand field types
  const defaults = AppSettingsSchema.parse({});

  const kwargs: Record<string, unknown> = {};

  // Apply preset overrides first
  for (const [key, value] of Object.entries(presetOverrides)) {
    kwargs[key] = value;
  }

  // Then apply project config (.autoctx.json) if present
  const projectConfig = loadProjectConfig();
  if (projectConfig) {
    if (projectConfig.provider) {
      kwargs.agentProvider = projectConfig.provider;
    }
    if (projectConfig.model) {
      kwargs.modelCompetitor = projectConfig.model;
      kwargs.modelAnalyst = projectConfig.model;
      kwargs.modelCoach = projectConfig.model;
      kwargs.modelArchitect = projectConfig.model;
      kwargs.modelTranslator = projectConfig.model;
      kwargs.modelCurator = projectConfig.model;
      kwargs.modelSkeptic = projectConfig.model;
    }
    if (projectConfig.knowledgeDir) {
      kwargs.knowledgeRoot = projectConfig.knowledgeDir;
    }
    if (projectConfig.runsDir) {
      kwargs.runsRoot = projectConfig.runsDir;
    }
    if (projectConfig.dbPath) {
      kwargs.dbPath = projectConfig.dbPath;
    }
    if (projectConfig.gens !== undefined) {
      kwargs.defaultGenerations = projectConfig.gens;
    }
  }

  // Then apply env vars (env wins over preset)
  for (const key of Object.keys(AppSettingsSchema.shape)) {
    const envKey = `AUTOCONTEXT_${camelToScreamingSnake(key)}`;
    const envVal = process.env[envKey];
    if (envVal !== undefined) {
      kwargs[key] = coerceEnvValue(
        envVal,
        (defaults as Record<string, unknown>)[key],
      );
    }
  }

  return AppSettingsSchema.parse(kwargs);
}
