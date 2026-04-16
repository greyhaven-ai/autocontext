import { z } from "zod";

export const costBudgetLimitPreprocess = z.preprocess((val) => {
  if (val === null || val === undefined || val === "") return null;
  const n = Number(val);
  return n > 0 ? n : null;
}, z.number().positive().nullable().default(null));

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

  // Claude CLI runtime
  claudeModel: z.string().default("sonnet"),
  claudeFallbackModel: z.string().default("haiku"),
  claudeTools: z.string().nullable().default(null),
  claudePermissionMode: z.string().default("bypassPermissions"),
  claudeSessionPersistence: z.boolean().default(false),
  claudeTimeout: z.number().min(1).default(120.0),

  // Codex CLI runtime
  codexModel: z.string().default("o4-mini"),
  codexTimeout: z.number().min(1).default(120.0),
  codexWorkspace: z.string().default(""),
  codexApprovalMode: z.string().default("full-auto"),
  codexQuiet: z.boolean().default(false),

  // Pi CLI runtime
  piCommand: z.string().default("pi"),
  piTimeout: z.number().min(1).default(120.0),
  piWorkspace: z.string().default(""),
  piModel: z.string().default(""),
  piNoContextFiles: z.boolean().default(false),

  // Pi RPC runtime (subprocess JSONL; endpoint/apiKey retained for backwards-compatible config parsing)
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
  competitorApiKey: z.string().default(""),
  competitorBaseUrl: z.string().default(""),
  analystApiKey: z.string().default(""),
  analystBaseUrl: z.string().default(""),
  coachApiKey: z.string().default(""),
  coachBaseUrl: z.string().default(""),
  architectApiKey: z.string().default(""),
  architectBaseUrl: z.string().default(""),

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
