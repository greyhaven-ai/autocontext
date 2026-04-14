export const BENCHMARK_HELP_TEXT = `autoctx benchmark — Run benchmark (multiple runs, aggregate stats)

Usage: autoctx benchmark [options]

Options:
  --scenario <name>    Scenario to benchmark (default: grid_ctf)
  --runs N             Number of independent runs (default: 3)
  --gens N             Generations per run (default: 1)
  --provider <type>    LLM provider to use
  --json               Output aggregate stats as JSON

Examples:
  autoctx benchmark --scenario grid_ctf --runs 5 --gens 3
  autoctx benchmark --provider deterministic --json

See also: run, list`;

export interface BenchmarkCommandValues {
  scenario?: string;
  runs?: string;
  gens?: string;
  provider?: string;
  json?: boolean;
}

export interface BenchmarkCommandPlan {
  scenarioName: string;
  numRuns: number;
  numGens: number;
  providerType?: string;
  json: boolean;
}

export interface BenchmarkResult {
  scenario: string;
  runs: number;
  generations: number;
  scores: number[];
  meanBestScore: number;
  provider: string;
  synthetic?: true;
}

export async function planBenchmarkCommand(
  values: BenchmarkCommandValues,
  resolveScenarioOption: (scenario: string | undefined) => Promise<string | undefined>,
): Promise<BenchmarkCommandPlan> {
  return {
    scenarioName: (await resolveScenarioOption(values.scenario)) ?? "grid_ctf",
    numRuns: Number.parseInt(values.runs ?? "3", 10),
    numGens: Number.parseInt(values.gens ?? "1", 10),
    providerType: values.provider,
    json: !!values.json,
  };
}

export async function executeBenchmarkCommandWorkflow<
  TProviderBundle extends {
    defaultProvider: unknown;
    roleProviders: unknown;
    roleModels: unknown;
    defaultConfig: { providerType: string };
  },
  TStore extends { migrate(path: string): void; close(): void },
  TRunner extends { run(runId: string, numGens: number): Promise<{ bestScore: number }> },
  TScenario,
>(opts: {
  dbPath: string;
  migrationsDir: string;
  runsRoot: string;
  knowledgeRoot: string;
  plan: BenchmarkCommandPlan;
  providerBundle: TProviderBundle;
  ScenarioClass: new () => TScenario;
  assertFamilyContract: (scenario: TScenario, family: "game", label: string) => void;
  createStore: (dbPath: string) => TStore;
  createRunner: (args: {
    provider: TProviderBundle["defaultProvider"];
    roleProviders: TProviderBundle["roleProviders"];
    roleModels: TProviderBundle["roleModels"];
    scenario: TScenario;
    store: TStore;
    runsRoot: string;
    knowledgeRoot: string;
  }) => TRunner;
  now?: () => number;
}): Promise<BenchmarkResult> {
  const scores: number[] = [];
  const now = opts.now ?? Date.now;

  for (let i = 0; i < opts.plan.numRuns; i++) {
    const store = opts.createStore(opts.dbPath);
    try {
      store.migrate(opts.migrationsDir);
      const scenario = new opts.ScenarioClass();
      opts.assertFamilyContract(scenario, "game", `scenario '${opts.plan.scenarioName}'`);
      const runner = opts.createRunner({
        provider: opts.providerBundle.defaultProvider,
        roleProviders: opts.providerBundle.roleProviders,
        roleModels: opts.providerBundle.roleModels,
        scenario,
        store,
        runsRoot: opts.runsRoot,
        knowledgeRoot: opts.knowledgeRoot,
      });
      const result = await runner.run(`bench_${now()}_${i}`, opts.plan.numGens);
      scores.push(result.bestScore);
    } finally {
      store.close();
    }
  }

  const provider = opts.providerBundle.defaultConfig.providerType;
  const synthetic = provider === "deterministic" ? true : undefined;

  return {
    scenario: opts.plan.scenarioName,
    runs: opts.plan.numRuns,
    generations: opts.plan.numGens,
    scores,
    meanBestScore: scores.reduce((sum, score) => sum + score, 0) / scores.length,
    provider,
    ...(synthetic ? { synthetic } : {}),
  };
}

export function renderBenchmarkResult(
  result: BenchmarkResult,
  json: boolean,
): { stdout: string; stderr?: string } {
  if (json) {
    return { stdout: JSON.stringify(result, null, 2) };
  }

  return {
    ...(result.synthetic
      ? { stderr: "Note: Running with deterministic provider — results are synthetic." }
      : {}),
    stdout: [
      `Benchmark: ${result.scenario}, ${result.runs} runs x ${result.generations} gens`,
      `Scores: ${result.scores.map((score) => score.toFixed(4)).join(", ")}`,
      `Mean best score: ${result.meanBestScore.toFixed(4)}`,
    ].join("\n"),
  };
}
