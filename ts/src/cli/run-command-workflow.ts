export const RUN_HELP_TEXT = `autoctx run — Run the generation loop for a scenario

Usage: autoctx run [options]

Options:
  --scenario <name>    Scenario to run (built-in or saved custom agent_task)
  --gens N             Number of generations to run (default: from config or 1)
  --run-id <id>        Custom run identifier (default: auto-generated)
  --provider <type>    LLM provider: anthropic, openai, ollama, deterministic, etc.
  --matches N          Matches per generation (default: 3)
  --json               Output results as JSON

If project config (.autoctx.json) exists, --scenario and --gens default from it.

Examples:
  autoctx run --scenario grid_ctf --provider deterministic --gens 3
  autoctx run --scenario grid_ctf --gens 5 --matches 5
  autoctx run                          # uses defaults from .autoctx.json

See also: list, replay, export, benchmark`;

export interface RunCommandValues {
  scenario?: string;
  gens?: string;
  "run-id"?: string;
  provider?: string;
  matches?: string;
  json?: boolean;
}

export interface RunCommandPlan {
  scenarioName: string;
  gens: number;
  runId: string;
  providerType?: string;
  matches: number;
  json: boolean;
}

export interface RunCommandSettings {
  defaultGenerations: number;
  matchesPerGeneration: number;
}

export interface RunExecutionSettings {
  maxRetries: number;
  backpressureMinDelta: number;
  playbookMaxVersions: number;
  contextBudgetTokens: number;
  curatorEnabled: boolean;
  curatorConsolidateEveryNGens: number;
  skillMaxLessons: number;
  deadEndTrackingEnabled: boolean;
  deadEndMaxEntries: number;
  stagnationResetEnabled: boolean;
  stagnationRollbackThreshold: number;
  stagnationPlateauWindow: number;
  stagnationPlateauEpsilon: number;
  stagnationDistillTopLessons: number;
  explorationMode: unknown;
  notifyWebhookUrl: unknown;
  notifyOn: unknown;
}

export interface RunCommandResult {
  runId: string;
  generationsCompleted: number;
  bestScore: number;
  currentElo: number;
  provider: string;
  skillPackage?: Record<string, unknown>;
  synthetic?: true;
}

type AgentTaskSolveExecutor = (opts: {
  provider: unknown;
  created: { name: string; spec: Record<string, unknown> };
  generations: number;
}) => Promise<{ progress: number; result: Record<string, unknown> }>;

export interface AgentTaskRunStore {
  migrate(migrationsDir: string): void;
  createRun(
    runId: string,
    scenario: string,
    generations: number,
    executorMode: string,
    agentProvider?: string,
  ): void;
  updateRunStatus(runId: string, status: string): void;
  upsertGeneration(
    runId: string,
    generationIndex: number,
    opts: {
      meanScore: number;
      bestScore: number;
      elo: number;
      wins: number;
      losses: number;
      gateDecision: string;
      status: string;
      scoringBackend?: string;
      ratingUncertainty?: number | null;
    },
  ): void;
  close(): void;
}

export async function executeAgentTaskRunCommandWorkflow<TProviderBundle extends {
  defaultProvider: unknown;
  defaultConfig: { providerType: string };
}>(opts: {
  plan: RunCommandPlan;
  providerBundle: TProviderBundle;
  spec: Record<string, unknown>;
  executeAgentTaskSolve: AgentTaskSolveExecutor;
  dbPath?: string;
  migrationsDir?: string;
  createStore?: (dbPath: string) => AgentTaskRunStore;
}): Promise<RunCommandResult> {
  const provider = opts.providerBundle.defaultConfig.providerType;
  const migrationsDir = opts.migrationsDir;
  const store = opts.createStore && opts.dbPath && opts.migrationsDir
    ? opts.createStore(opts.dbPath)
    : null;

  try {
    if (store && migrationsDir) {
      store.migrate(migrationsDir);
    }
    store?.createRun(opts.plan.runId, opts.plan.scenarioName, opts.plan.gens, "agent_task", provider);

    const result = await opts.executeAgentTaskSolve({
      provider: opts.providerBundle.defaultProvider,
      created: {
        name: opts.plan.scenarioName,
        spec: opts.spec,
      },
      generations: opts.plan.gens,
    });
    const bestScore = typeof result.result.best_score === "number" ? result.result.best_score : 0;
    const generationsCompleted = normalizeCompletedGenerations(result.progress);

    for (let generation = 1; generation <= generationsCompleted; generation++) {
      store?.upsertGeneration(opts.plan.runId, generation, {
        meanScore: bestScore,
        bestScore,
        elo: 1000,
        wins: 0,
        losses: 0,
        gateDecision: "advance",
        status: "completed",
        scoringBackend: "agent_task",
      });
    }
    store?.updateRunStatus(opts.plan.runId, "completed");

    return {
      runId: opts.plan.runId,
      generationsCompleted,
      bestScore,
      currentElo: 1000,
      provider,
      skillPackage: result.result,
      ...(provider === "deterministic" ? { synthetic: true } : {}),
    };
  } catch (error) {
    store?.updateRunStatus(opts.plan.runId, "failed");
    throw error;
  } finally {
    store?.close();
  }
}

export async function planRunCommand(
  values: RunCommandValues,
  resolveScenarioOption: (scenario: string | undefined) => Promise<string | undefined>,
  settings: RunCommandSettings,
  now: () => number,
  parsePositiveInteger: (raw: string, label: string) => number,
): Promise<RunCommandPlan> {
  const scenarioName = await resolveScenarioOption(values.scenario);
  if (!scenarioName) {
    throw new Error(
      "Error: no scenario configured. Run `autoctx init` or pass --scenario <name>.",
    );
  }

  return {
    scenarioName,
    gens: values.gens
      ? parsePositiveInteger(values.gens, "--gens")
      : settings.defaultGenerations,
    runId: values["run-id"] ?? `run-${now()}`,
    providerType: values.provider,
    matches: parsePositiveInteger(
      values.matches ?? String(settings.matchesPerGeneration),
      "--matches",
    ),
    json: !!values.json,
  };
}

export function resolveRunScenario<TScenarioClass>(
  scenarioName: string,
  registry: Record<string, TScenarioClass>,
): TScenarioClass {
  const ScenarioClass = registry[scenarioName];
  if (!ScenarioClass) {
    const allScenarios = Object.keys(registry).sort();
    throw new Error(`Unknown scenario: ${scenarioName}. Available: ${allScenarios.join(", ")}`);
  }
  return ScenarioClass;
}

export async function executeRunCommandWorkflow<
  TProviderBundle extends {
    defaultProvider: unknown;
    roleProviders: unknown;
    roleModels: unknown;
    defaultConfig: { providerType: string };
  },
  TStore extends { migrate(path: string): void; close(): void },
  TRunner extends { run(runId: string, gens: number): Promise<{
    runId: string;
    generationsCompleted: number;
    bestScore: number;
    currentElo: number;
  }> },
  TScenario,
>(opts: {
  dbPath: string;
  migrationsDir: string;
  runsRoot: string;
  knowledgeRoot: string;
  settings: RunExecutionSettings;
  plan: RunCommandPlan;
  providerBundle: TProviderBundle;
  ScenarioClass: new () => TScenario;
  assertFamilyContract: (scenario: TScenario, family: "game", label: string) => void;
  createStore: (dbPath: string) => TStore;
  createRunner: (opts: {
    provider: TProviderBundle["defaultProvider"];
    roleProviders: TProviderBundle["roleProviders"];
    roleModels: TProviderBundle["roleModels"];
    scenario: TScenario;
    store: TStore;
    runsRoot: string;
    knowledgeRoot: string;
    matchesPerGeneration: number;
    maxRetries: number;
    minDelta: number;
    playbookMaxVersions: number;
    contextBudgetTokens: number;
    curatorEnabled: boolean;
    curatorConsolidateEveryNGens: number;
    skillMaxLessons: number;
    deadEndTrackingEnabled: boolean;
    deadEndMaxEntries: number;
    stagnationResetEnabled: boolean;
    stagnationRollbackThreshold: number;
    stagnationPlateauWindow: number;
    stagnationPlateauEpsilon: number;
    stagnationDistillTopLessons: number;
    explorationMode: unknown;
    notifyWebhookUrl: unknown;
    notifyOn: unknown;
  } | Record<string, unknown>) => TRunner;
}): Promise<RunCommandResult> {
  const scenario = new opts.ScenarioClass();
  opts.assertFamilyContract(scenario, "game", `scenario '${opts.plan.scenarioName}'`);

  const store = opts.createStore(opts.dbPath);
  try {
    store.migrate(opts.migrationsDir);
    const runner = opts.createRunner({
      provider: opts.providerBundle.defaultProvider,
      roleProviders: opts.providerBundle.roleProviders,
      roleModels: opts.providerBundle.roleModels,
      scenario,
      store,
      runsRoot: opts.runsRoot,
      knowledgeRoot: opts.knowledgeRoot,
      matchesPerGeneration: opts.plan.matches,
      maxRetries: opts.settings.maxRetries,
      minDelta: opts.settings.backpressureMinDelta,
      playbookMaxVersions: opts.settings.playbookMaxVersions,
      contextBudgetTokens: opts.settings.contextBudgetTokens,
      curatorEnabled: opts.settings.curatorEnabled,
      curatorConsolidateEveryNGens: opts.settings.curatorConsolidateEveryNGens,
      skillMaxLessons: opts.settings.skillMaxLessons,
      deadEndTrackingEnabled: opts.settings.deadEndTrackingEnabled,
      deadEndMaxEntries: opts.settings.deadEndMaxEntries,
      stagnationResetEnabled: opts.settings.stagnationResetEnabled,
      stagnationRollbackThreshold: opts.settings.stagnationRollbackThreshold,
      stagnationPlateauWindow: opts.settings.stagnationPlateauWindow,
      stagnationPlateauEpsilon: opts.settings.stagnationPlateauEpsilon,
      stagnationDistillTopLessons: opts.settings.stagnationDistillTopLessons,
      explorationMode: opts.settings.explorationMode,
      notifyWebhookUrl: opts.settings.notifyWebhookUrl,
      notifyOn: opts.settings.notifyOn,
    });
    const result = await runner.run(opts.plan.runId, opts.plan.gens);
    const provider = opts.providerBundle.defaultConfig.providerType;
    return {
      ...result,
      provider,
      ...(provider === "deterministic" ? { synthetic: true } : {}),
    };
  } finally {
    store.close();
  }
}

export function renderRunResult(
  result: RunCommandResult,
  json: boolean,
): { stdout: string; stderr?: string } {
  if (json) {
    return { stdout: JSON.stringify(result, null, 2) };
  }

  return {
    ...(result.synthetic
      ? { stderr: "Note: Running with deterministic provider — results are synthetic." }
      : {}),
    stdout: `Run ${result.runId}: ${result.generationsCompleted} generations, best score ${result.bestScore.toFixed(4)}, Elo ${result.currentElo.toFixed(1)}`,
  };
}

function normalizeCompletedGenerations(progress: number): number {
  return Number.isFinite(progress) ? Math.max(0, Math.floor(progress)) : 0;
}
