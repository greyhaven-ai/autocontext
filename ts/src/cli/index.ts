#!/usr/bin/env node
/**
 * autocontext CLI — command-line interface for the evaluation harness.
 *
 * Commands:
 *   autoctx judge     — one-shot evaluation
 *   autoctx improve   — run improvement loop
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
  run         Run generation loop for a scenario
  tui         Start interactive TUI (WebSocket server + Ink UI)
  judge       One-shot evaluation of output against a rubric
  improve     Run multi-round improvement loop
  queue       Add a task to the background runner queue
  status      Show queue status
  serve       Start MCP server on stdio
  version     Show version

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
    case "tui":
      await cmdTui(dbPath);
      break;
    case "judge":
      await cmdJudge(dbPath);
      break;
    case "improve":
      await cmdImprove(dbPath);
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
  const matches = parseInt(values.matches ?? "3", 10);

  const runner = new GenerationRunner({
    provider,
    scenario,
    store,
    runsRoot: resolve("runs"),
    knowledgeRoot: resolve("knowledge"),
    matchesPerGeneration: matches,
    maxRetries: 2,
    minDelta: 0.005,
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
      verbose: { type: "boolean", short: "v" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.prompt || !values.output || !values.rubric) {
    console.log("autoctx improve -p <task-prompt> -o <initial-output> -r <rubric> [-n rounds] [-t threshold] [--min-rounds N] [-v]");
    process.exit(values.help ? 0 : 1);
  }

  const { provider, model } = await getProvider();
  const { SimpleAgentTask } = await import("../execution/task-runner.js");
  const { ImprovementLoop } = await import("../execution/improvement-loop.js");

  const task = new SimpleAgentTask(values.prompt, values.rubric, provider, model);
  const loop = new ImprovementLoop({
    task,
    maxRounds: parseInt(values.rounds ?? "5", 10),
    qualityThreshold: parseFloat(values.threshold ?? "0.9"),
    minRounds: parseInt(values["min-rounds"] ?? "1", 10),
  });

  const startTime = performance.now();
  const result = await loop.run({ initialOutput: values.output, state: {} });
  const durationMs = Math.round(performance.now() - startTime);

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
  }, null, 2));
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
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.spec) {
    console.log("autoctx queue -s <spec-name> [-p prompt] [-r rubric] [--priority N] [--min-rounds N]");
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

  const store = new SQLiteStore(dbPath);
  const migrationsDir = getMigrationsDir();
  store.migrate(migrationsDir);

  const { provider, model } = await getProvider();

  await startServer({ store, provider, model });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
