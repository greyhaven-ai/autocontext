#!/usr/bin/env node
/**
 * autocontext CLI — command-line interface for the evaluation harness.
 *
 * Commands:
 *   autoctx judge     — one-shot evaluation
 *   autoctx improve   — run improvement loop
 *   autoctx repl      — run a direct REPL-loop session
 *   autoctx queue     — add task to background queue
 *   autoctx status    — check queue status
 *   autoctx serve     — start MCP server on stdio
 */

import { parseArgs } from "node:util";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

function getMigrationsDir(): string {
  const thisDir = dirname(fileURLToPath(import.meta.url));
  return join(thisDir, "..", "..", "migrations");
}

const HELP = `
autoctx — always-on agent evaluation harness

Commands:
  run              Run generation loop for a scenario
  list             List recent runs
  replay           Print replay JSON for a generation
  benchmark        Run benchmark (multiple runs, aggregate stats)
  export           Export strategy package for a scenario
  import-package   Import a strategy package from file
  new-scenario     Create a scenario from natural language description
  tui              Start interactive TUI (WebSocket server + Ink UI)
  judge            One-shot evaluation of output against a rubric
  improve          Run multi-round improvement loop
  repl             Run a direct REPL-loop session
  queue            Add a task to the background runner queue
  status           Show queue status
  serve            Start MCP server on stdio
  version          Show version

Run \`autoctx <command> --help\` for command-specific options.
`.trim();

async function main(): Promise<void> {
  const command = process.argv[2];

  if (!command || command === "--help" || command === "-h") {
    console.log(HELP);
    process.exit(0);
  }

  if (command === "version" || command === "--version") {
    const pkg = await import("../../package.json", { with: { type: "json" } });
    console.log(pkg.default.version);
    process.exit(0);
  }

  // All commands need a database
  const dbPath = process.env.AUTOCONTEXT_DB_PATH ?? resolve("autocontext.db");

  switch (command) {
    case "run":
      await cmdRun(dbPath);
      break;
    case "list":
      await cmdList(dbPath);
      break;
    case "replay":
      await cmdReplay(dbPath);
      break;
    case "benchmark":
      await cmdBenchmark(dbPath);
      break;
    case "export":
      await cmdExport(dbPath);
      break;
    case "import-package":
      await cmdImportPackage(dbPath);
      break;
    case "new-scenario":
      await cmdNewScenario(dbPath);
      break;
    case "tui":
      await cmdTui(dbPath);
      break;
    case "judge":
      await cmdJudge(dbPath);
      break;
    case "improve":
      await cmdImprove(dbPath);
      break;
    case "repl":
      await cmdRepl(dbPath);
      break;
    case "queue":
      await cmdQueue(dbPath);
      break;
    case "status":
      await cmdStatus(dbPath);
      break;
    case "serve":
      await cmdServe(dbPath);
      break;
    default:
      console.error(`Unknown command: ${command}\n`);
      console.log(HELP);
      process.exit(1);
  }
}

async function getProvider() {
  const { resolveProviderConfig, createProvider } = await import("../providers/index.js");

  try {
    const config = resolveProviderConfig();
    const provider = createProvider(config);
    const model = provider.defaultModel();
    return { provider, model };
  } catch (err) {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  }
}

async function cmdRun(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      scenario: { type: "string", short: "s" },
      gens: { type: "string", short: "g", default: "1" },
      "run-id": { type: "string" },
      provider: { type: "string" },
      matches: { type: "string", default: "3" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.scenario) {
    console.log("autoctx run --scenario <name> [--gens N] [--run-id ID] [--provider deterministic] [--matches N] [--json]");
    process.exit(values.help ? 0 : 1);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { GenerationRunner } = await import("../loop/generation-runner.js");
  const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
  const { loadSettings } = await import("../config/index.js");

  // Resolve provider
  let provider;
  if (values.provider) {
    const { createProvider } = await import("../providers/index.js");
    provider = createProvider({ providerType: values.provider });
  } else {
    const result = await getProvider();
    provider = result.provider;
  }

  // Resolve scenario
  const ScenarioClass = SCENARIO_REGISTRY[values.scenario];
  if (!ScenarioClass) {
    console.error(`Unknown scenario: ${values.scenario}. Available: ${Object.keys(SCENARIO_REGISTRY).join(", ")}`);
    process.exit(1);
  }
  const scenario = new ScenarioClass();

  // Setup storage
  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());

  const runId = values["run-id"] ?? `run-${Date.now()}`;
  const gens = parseInt(values.gens ?? "1", 10);
  const settings = loadSettings();
  const matches = parseInt(values.matches ?? String(settings.matchesPerGeneration), 10);

  const runner = new GenerationRunner({
    provider,
    scenario,
    store,
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
    matchesPerGeneration: matches,
    maxRetries: settings.maxRetries,
    minDelta: settings.backpressureMinDelta,
    playbookMaxVersions: settings.playbookMaxVersions,
    contextBudgetTokens: settings.contextBudgetTokens,
    curatorEnabled: settings.curatorEnabled,
    curatorConsolidateEveryNGens: settings.curatorConsolidateEveryNGens,
    skillMaxLessons: settings.skillMaxLessons,
    deadEndTrackingEnabled: settings.deadEndTrackingEnabled,
    deadEndMaxEntries: settings.deadEndMaxEntries,
    stagnationResetEnabled: settings.stagnationResetEnabled,
    stagnationRollbackThreshold: settings.stagnationRollbackThreshold,
    stagnationPlateauWindow: settings.stagnationPlateauWindow,
    stagnationPlateauEpsilon: settings.stagnationPlateauEpsilon,
    stagnationDistillTopLessons: settings.stagnationDistillTopLessons,
    explorationMode: settings.explorationMode,
    notifyWebhookUrl: settings.notifyWebhookUrl,
    notifyOn: settings.notifyOn,
  });

  try {
    const result = await runner.run(runId, gens);
    if (values.json) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      console.log(`Run ${result.runId}: ${result.generationsCompleted} generations, best score ${result.bestScore.toFixed(4)}, Elo ${result.currentElo.toFixed(1)}`);
    }
  } finally {
    store.close();
  }
}

async function cmdTui(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      port: { type: "string", default: "8000" },
      headless: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx tui [--port 8000] [--headless]");
    console.log("Starts the interactive WebSocket server and bundled terminal UI.");
    process.exit(0);
  }

  const port = parseInt(values.port ?? "8000", 10);

  const { RunManager, InteractiveServer } = await import("../server/index.js");
  const mgr = new RunManager({
    dbPath,
    migrationsDir: getMigrationsDir(),
    runsRoot: resolve("runs"),
    knowledgeRoot: resolve("knowledge"),
    providerType: process.env.AUTOCONTEXT_PROVIDER ?? "deterministic",
    apiKey: process.env.AUTOCONTEXT_API_KEY,
    baseUrl: process.env.AUTOCONTEXT_BASE_URL,
    model: process.env.AUTOCONTEXT_MODEL,
  });
  const server = new InteractiveServer({ runManager: mgr, port });
  await server.start();

  const headless = values.headless || !process.stdout.isTTY;
  if (headless) {
    console.log(`AutoContext interactive server listening at ${server.url}`);
    console.log(`Scenarios: ${mgr.listScenarios().join(", ")}`);
    await new Promise<void>((resolve) => {
      const cleanup = () => {
        process.off("SIGINT", cleanup);
        process.off("SIGTERM", cleanup);
        resolve();
      };
      process.on("SIGINT", cleanup);
      process.on("SIGTERM", cleanup);
    });
    await server.stop();
    return;
  }

  const React = await import("react");
  const { render } = await import("ink");
  const { InteractiveTui } = await import("../tui/app.js");

  const app = render(React.createElement(InteractiveTui, {
    manager: mgr,
    serverUrl: server.url,
  }));

  try {
    await app.waitUntilExit();
  } finally {
    await server.stop();
  }
}

async function cmdJudge(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      prompt: { type: "string", short: "p" },
      output: { type: "string", short: "o" },
      rubric: { type: "string", short: "r" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.prompt || !values.output || !values.rubric) {
    console.log("autoctx judge -p <task-prompt> -o <agent-output> -r <rubric>");
    process.exit(values.help ? 0 : 1);
  }

  const { provider, model } = await getProvider();
  const { LLMJudge } = await import("../judge/index.js");

  const judge = new LLMJudge({ provider, model, rubric: values.rubric });
  const result = await judge.evaluate({
    taskPrompt: values.prompt,
    agentOutput: values.output,
  });

  console.log(JSON.stringify({
    score: result.score,
    reasoning: result.reasoning,
    dimensionScores: result.dimensionScores,
  }, null, 2));
}

async function cmdImprove(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      prompt: { type: "string", short: "p" },
      output: { type: "string", short: "o" },
      rubric: { type: "string", short: "r" },
      rounds: { type: "string", short: "n", default: "5" },
      threshold: { type: "string", short: "t", default: "0.9" },
      "min-rounds": { type: "string", default: "1" },
      rlm: { type: "boolean" },
      "rlm-model": { type: "string" },
      "rlm-turns": { type: "string" },
      "rlm-max-tokens": { type: "string" },
      "rlm-temperature": { type: "string" },
      "rlm-max-stdout": { type: "string" },
      "rlm-timeout-ms": { type: "string" },
      "rlm-memory-mb": { type: "string" },
      verbose: { type: "boolean", short: "v" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.prompt || !values.rubric || (!values.output && !values.rlm)) {
    console.log(
      "autoctx improve -p <task-prompt> [-o <initial-output>] -r <rubric> " +
      "[-n rounds] [-t threshold] [--min-rounds N] [--rlm] [--rlm-turns N] [-v]",
    );
    process.exit(values.help ? 0 : 1);
  }

  const { provider, model } = await getProvider();
  const { SimpleAgentTask } = await import("../execution/task-runner.js");
  const { ImprovementLoop } = await import("../execution/improvement-loop.js");

  const task = new SimpleAgentTask(
    values.prompt,
    values.rubric,
    provider,
    model,
    undefined,
    {
      enabled: values.rlm ?? false,
      model: values["rlm-model"],
      ...(values["rlm-turns"] ? { maxTurns: parseInt(values["rlm-turns"], 10) } : {}),
      ...(values["rlm-max-tokens"] ? { maxTokensPerTurn: parseInt(values["rlm-max-tokens"], 10) } : {}),
      ...(values["rlm-temperature"] ? { temperature: parseFloat(values["rlm-temperature"]) } : {}),
      ...(values["rlm-max-stdout"] ? { maxStdoutChars: parseInt(values["rlm-max-stdout"], 10) } : {}),
      ...(values["rlm-timeout-ms"] ? { codeTimeoutMs: parseInt(values["rlm-timeout-ms"], 10) } : {}),
      ...(values["rlm-memory-mb"] ? { memoryLimitMb: parseInt(values["rlm-memory-mb"], 10) } : {}),
    },
  );
  const loop = new ImprovementLoop({
    task,
    maxRounds: parseInt(values.rounds ?? "5", 10),
    qualityThreshold: parseFloat(values.threshold ?? "0.9"),
    minRounds: parseInt(values["min-rounds"] ?? "1", 10),
  });

  const startTime = performance.now();
  const initialOutput = values.output ?? await task.generateOutput();
  const result = await loop.run({ initialOutput, state: {} });
  const durationMs = Math.round(performance.now() - startTime);
  const rlmSessions = task.getRlmSessions();

  if (values.verbose) {
    for (const round of result.rounds) {
      console.error(JSON.stringify({
        round: round.roundNumber,
        score: round.score,
        dimensionScores: round.dimensionScores,
        reasoning: round.reasoning.length > 200 ? round.reasoning.slice(0, 200) + "..." : round.reasoning,
        isRevision: round.isRevision,
        judgeFailed: round.judgeFailed,
      }));
    }
  }

  console.log(JSON.stringify({
    totalRounds: result.totalRounds,
    metThreshold: result.metThreshold,
    bestScore: result.bestScore,
    bestRound: result.bestRound,
    judgeFailures: result.judgeFailures,
    terminationReason: result.terminationReason,
    totalInternalRetries: result.totalInternalRetries,
    dimensionTrajectory: result.dimensionTrajectory,
    bestOutput: result.bestOutput,
    durationMs,
    ...(rlmSessions.length > 0 ? { rlmSessions } : {}),
  }, null, 2));
}

async function cmdRepl(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      prompt: { type: "string", short: "p" },
      rubric: { type: "string", short: "r" },
      output: { type: "string", short: "o" },
      phase: { type: "string", default: "generate" },
      "reference-context": { type: "string" },
      "required-concept": { type: "string", multiple: true },
      model: { type: "string", short: "m" },
      turns: { type: "string", short: "n", default: "6" },
      "max-tokens": { type: "string", default: "2048" },
      temperature: { type: "string", short: "t", default: "0.2" },
      "max-stdout": { type: "string", default: "8192" },
      "timeout-ms": { type: "string", default: "10000" },
      "memory-mb": { type: "string", default: "64" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.prompt || !values.rubric) {
    console.log(
      "autoctx repl -p <task-prompt> -r <rubric> " +
      "[--phase generate|revise] [-o <current-output>] [--reference-context TEXT] " +
      "[--required-concept C]... [-m model] [-n turns]",
    );
    process.exit(values.help ? 0 : 1);
  }

  const phase = values.phase === "revise" ? "revise" : "generate";
  if (phase === "revise" && !values.output) {
    console.error("autoctx repl --phase revise requires -o/--output");
    process.exit(1);
  }

  const { provider, model } = await getProvider();
  const { runAgentTaskRlmSession } = await import("../rlm/index.js");

  const result = await runAgentTaskRlmSession({
    provider,
    model,
    config: {
      enabled: true,
      model: values.model,
      maxTurns: parseInt(values.turns ?? "6", 10),
      maxTokensPerTurn: parseInt(values["max-tokens"] ?? "2048", 10),
      temperature: parseFloat(values.temperature ?? "0.2"),
      maxStdoutChars: parseInt(values["max-stdout"] ?? "8192", 10),
      codeTimeoutMs: parseInt(values["timeout-ms"] ?? "10000", 10),
      memoryLimitMb: parseInt(values["memory-mb"] ?? "64", 10),
    },
    phase,
    taskPrompt: values.prompt,
    rubric: values.rubric,
    currentOutput: values.output,
    referenceContext: values["reference-context"],
    requiredConcepts: values["required-concept"],
  });

  console.log(JSON.stringify(result, null, 2));
}

async function cmdQueue(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      spec: { type: "string", short: "s" },
      prompt: { type: "string", short: "p" },
      rubric: { type: "string", short: "r" },
      priority: { type: "string", default: "0" },
      "min-rounds": { type: "string" },
      rlm: { type: "boolean" },
      "rlm-model": { type: "string" },
      "rlm-turns": { type: "string" },
      "rlm-max-tokens": { type: "string" },
      "rlm-temperature": { type: "string" },
      "rlm-max-stdout": { type: "string" },
      "rlm-timeout-ms": { type: "string" },
      "rlm-memory-mb": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.spec) {
    console.log(
      "autoctx queue -s <spec-name> [-p prompt] [-r rubric] [--priority N] " +
      "[--min-rounds N] [--rlm] [--rlm-turns N]",
    );
    process.exit(values.help ? 0 : 1);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { enqueueTask } = await import("../execution/task-runner.js");

  const store = new SQLiteStore(dbPath);
  const migrationsDir = getMigrationsDir();
  store.migrate(migrationsDir);

  const id = enqueueTask(store, values.spec, {
    taskPrompt: values.prompt,
    rubric: values.rubric,
    priority: parseInt(values.priority!, 10),
    ...(values["min-rounds"] ? { minRounds: parseInt(values["min-rounds"], 10) } : {}),
    rlmEnabled: values.rlm,
    rlmModel: values["rlm-model"],
    ...(values["rlm-turns"] ? { rlmMaxTurns: parseInt(values["rlm-turns"], 10) } : {}),
    ...(values["rlm-max-tokens"] ? { rlmMaxTokensPerTurn: parseInt(values["rlm-max-tokens"], 10) } : {}),
    ...(values["rlm-temperature"] ? { rlmTemperature: parseFloat(values["rlm-temperature"]) } : {}),
    ...(values["rlm-max-stdout"] ? { rlmMaxStdoutChars: parseInt(values["rlm-max-stdout"], 10) } : {}),
    ...(values["rlm-timeout-ms"] ? { rlmCodeTimeoutMs: parseInt(values["rlm-timeout-ms"], 10) } : {}),
    ...(values["rlm-memory-mb"] ? { rlmMemoryLimitMb: parseInt(values["rlm-memory-mb"], 10) } : {}),
  });

  console.log(JSON.stringify({ taskId: id, specName: values.spec, status: "queued" }));
  store.close();
}

async function cmdStatus(dbPath: string): Promise<void> {
  const { SQLiteStore } = await import("../storage/index.js");
  const store = new SQLiteStore(dbPath);

  try {
    const migrationsDir = getMigrationsDir();
    store.migrate(migrationsDir);
    const pending = store.pendingTaskCount();
    console.log(JSON.stringify({ pendingCount: pending }));
  } finally {
    store.close();
  }
}

async function cmdServe(dbPath: string): Promise<void> {
  const { SQLiteStore } = await import("../storage/index.js");
  const { startServer } = await import("../mcp/server.js");
  const { loadSettings } = await import("../config/index.js");

  const store = new SQLiteStore(dbPath);
  const migrationsDir = getMigrationsDir();
  store.migrate(migrationsDir);

  const { provider, model } = await getProvider();
  const settings = loadSettings();

  await startServer({
    store,
    provider,
    model,
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
  });
}

// ---------------------------------------------------------------------------
// New parity commands (AC-363)
// ---------------------------------------------------------------------------

async function cmdList(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      limit: { type: "string", default: "50" },
      scenario: { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx list [--limit N] [--scenario <name>] [--json]");
    process.exit(0);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());

  try {
    const runs = store.listRuns(parseInt(values.limit ?? "50", 10), values.scenario);
    if (values.json) {
      console.log(JSON.stringify(runs, null, 2));
    } else {
      if (runs.length === 0) {
        console.log("No runs found.");
      } else {
        for (const run of runs) {
          console.log(`${run.run_id}  ${run.scenario}  ${run.status}  ${run.created_at}`);
        }
      }
    }
  } finally {
    store.close();
  }
}

async function cmdReplay(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      "run-id": { type: "string" },
      generation: { type: "string", default: "1" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx replay --run-id <id> [--generation N]");
    process.exit(0);
  }

  if (!values["run-id"]) {
    console.error("Error: --run-id is required");
    process.exit(1);
  }

  const { existsSync, readdirSync, readFileSync } = await import("node:fs");
  const { loadSettings } = await import("../config/index.js");

  const gen = parseInt(values.generation ?? "1", 10);
  const settings = loadSettings();
  const replayDir = join(
    resolve(settings.runsRoot),
    values["run-id"],
    "generations",
    `gen_${gen}`,
    "replays",
  );
  if (!existsSync(replayDir)) {
    console.error(`No replay files found under ${replayDir}`);
    process.exit(1);
  }
  const replayFiles = readdirSync(replayDir)
    .filter((name) => name.endsWith(".json"))
    .sort();
  if (replayFiles.length === 0) {
    console.error(`No replay files found under ${replayDir}`);
    process.exit(1);
  }
  const payload = JSON.parse(readFileSync(join(replayDir, replayFiles[0]), "utf-8"));
  console.log(JSON.stringify(payload, null, 2));
}

async function cmdBenchmark(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      scenario: { type: "string", default: "grid_ctf" },
      runs: { type: "string", default: "3" },
      gens: { type: "string", default: "1" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx benchmark [--scenario <name>] [--runs N] [--gens N] [--json]");
    process.exit(0);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { GenerationRunner } = await import("../loop/generation-runner.js");
  const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
  const { loadSettings } = await import("../config/index.js");

  const scenarioName = values.scenario ?? "grid_ctf";
  const ScenarioClass = SCENARIO_REGISTRY[scenarioName];
  if (!ScenarioClass) {
    console.error(`Unknown scenario: ${scenarioName}`);
    process.exit(1);
  }

  let provider;
  if (process.env.AUTOCONTEXT_AGENT_PROVIDER) {
    const { createProvider } = await import("../providers/index.js");
    provider = createProvider({ providerType: process.env.AUTOCONTEXT_AGENT_PROVIDER });
  } else {
    const result = await getProvider();
    provider = result.provider;
  }

  const numRuns = parseInt(values.runs ?? "3", 10);
  const numGens = parseInt(values.gens ?? "1", 10);
  const settings = loadSettings();
  const scores: number[] = [];

  for (let i = 0; i < numRuns; i++) {
    const store = new SQLiteStore(dbPath);
    store.migrate(getMigrationsDir());
    const runId = `bench_${Date.now()}_${i}`;
    const runner = new GenerationRunner({
      provider,
      scenario: new ScenarioClass(),
      store,
      runsRoot: resolve(settings.runsRoot),
      knowledgeRoot: resolve(settings.knowledgeRoot),
    });
    const result = await runner.run(runId, numGens);
    scores.push(result.bestScore);
    store.close();
  }

  const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
  const output = { scenario: scenarioName, runs: numRuns, generations: numGens, scores, meanBestScore: mean };
  if (values.json) {
    console.log(JSON.stringify(output, null, 2));
  } else {
    console.log(`Benchmark: ${scenarioName}, ${numRuns} runs x ${numGens} gens`);
    console.log(`Scores: ${scores.map(s => s.toFixed(4)).join(", ")}`);
    console.log(`Mean best score: ${mean.toFixed(4)}`);
  }
}

async function cmdExport(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      scenario: { type: "string", short: "s" },
      output: { type: "string", short: "o" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx export --scenario <name> [--output <file>] [--json]");
    process.exit(0);
  }

  if (!values.scenario) {
    console.error("Error: --scenario is required");
    process.exit(1);
  }

  const { loadSettings } = await import("../config/index.js");
  const { ArtifactStore } = await import("../knowledge/artifact-store.js");
  const { exportStrategyPackage } = await import("../knowledge/package.js");
  const { SQLiteStore } = await import("../storage/index.js");

  const settings = loadSettings();
  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());
  const artifacts = new ArtifactStore({
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
  });
  try {
    const result = exportStrategyPackage({
      scenarioName: values.scenario,
      artifacts,
      store,
    });

    if (values.output) {
      const { writeFileSync, mkdirSync } = await import("node:fs");
      mkdirSync(dirname(values.output), { recursive: true });
      writeFileSync(values.output, JSON.stringify(result, null, 2), "utf-8");
      console.log(values.json ? JSON.stringify({ output: values.output }) : `Exported to ${values.output}`);
    } else {
      console.log(JSON.stringify(result, null, 2));
    }
  } finally {
    store.close();
  }
}

async function cmdImportPackage(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      file: { type: "string", short: "f" },
      scenario: { type: "string", short: "s" },
      conflict: { type: "string", default: "overwrite" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx import-package --file <path> [--scenario <name>] [--conflict overwrite|merge|skip] [--json]");
    process.exit(0);
  }

  if (!values.file) {
    console.error("Error: --file is required");
    process.exit(1);
  }

  const { readFileSync } = await import("node:fs");
  const { loadSettings } = await import("../config/index.js");
  const { ArtifactStore } = await import("../knowledge/artifact-store.js");
  const { importStrategyPackage } = await import("../knowledge/package.js");

  const settings = loadSettings();
  const raw = readFileSync(values.file, "utf-8");
  const artifacts = new ArtifactStore({
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
  });
  const conflict = (values.conflict ?? "overwrite") as "overwrite" | "merge" | "skip";
  if (!["overwrite", "merge", "skip"].includes(conflict)) {
    console.error("Error: --conflict must be one of overwrite, merge, skip");
    process.exit(1);
  }
  const result = importStrategyPackage({
    rawPackage: JSON.parse(raw) as Record<string, unknown>,
    artifacts,
    skillsRoot: resolve(settings.skillsRoot),
    scenarioOverride: values.scenario,
    conflictPolicy: conflict,
  });
  console.log(JSON.stringify(result, null, 2));
}

async function cmdNewScenario(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      description: { type: "string", short: "d" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx new-scenario --description <text> [--json]");
    process.exit(0);
  }

  if (!values.description) {
    console.error("Error: --description is required");
    process.exit(1);
  }

  const { createScenarioFromDescription } = await import("../scenarios/scenario-creator.js");

  let provider;
  try {
    const result = await getProvider();
    provider = result.provider;
  } catch {
    const { DeterministicProvider } = await import("../providers/deterministic.js");
    provider = new DeterministicProvider();
  }

  const result = await createScenarioFromDescription(values.description, provider);
  if (values.json) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.log(`Created scenario: ${result.name} (family: ${result.family})`);
    console.log(`Task prompt: ${result.spec.taskPrompt}`);
    console.log(`Rubric: ${result.spec.rubric}`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
