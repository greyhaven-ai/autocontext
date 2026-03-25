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
 *   autoctx serve     — start HTTP dashboard + API server
 *   autoctx mcp-serve — start MCP server on stdio
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
  init             Scaffold project config and AGENTS guidance
  run              Run generation loop for a scenario
  list             List recent runs
  replay           Print replay JSON for a generation
  benchmark        Run benchmark (multiple runs, aggregate stats)
  export           Export strategy package for a scenario
  export-training-data  Export training data as JSONL
  import-package   Import a strategy package from file
  new-scenario     Create a scenario from natural language description
  capabilities     Show available scenarios, providers, and features (JSON)
  login            Store provider credentials persistently
  whoami           Show current auth status and provider
  logout           Clear stored provider credentials
  providers        List all known providers with auth status (JSON)
  models           List available models for authenticated providers (JSON)
  tui              Start interactive TUI (WebSocket server + Ink UI)
  judge            One-shot evaluation of output against a rubric
  improve          Run multi-round improvement loop
  repl             Run a direct REPL-loop session
  queue            Add a task to the background runner queue
  status           Show queue status
  serve            Start HTTP dashboard + API server [--json]
  mcp-serve        Start MCP server on stdio
  version          Show version

Python-only commands (not supported in npm package):
  train, ecosystem, ab-test, resume, wait, trigger-distillation

Run \`autoctx <command> --help\` for command-specific options.
`.trim();

async function main(): Promise<void> {
  const command = process.argv[2];

  if (command === "--help" || command === "-h") {
    console.log(HELP);
    process.exit(0);
  }

  // AC-394: Smart no-args — show project status if config exists, suggest init otherwise
  if (!command) {
    const projectConfig = await buildProjectConfigSummary();
    if (projectConfig) {
      console.log(JSON.stringify(projectConfig, null, 2));
    } else {
      console.log(HELP);
      console.log("\nTip: Run `autoctx init` to set up this project with a .autoctx.json config.");
    }
    process.exit(0);
  }

  if (command === "version" || command === "--version") {
    const pkg = await import("../../package.json", { with: { type: "json" } });
    console.log(pkg.default.version);
    process.exit(0);
  }

  switch (command) {
    case "init":
      await cmdInit();
      break;
    case "capabilities":
      await cmdCapabilities();
      break;
    case "login":
      await cmdLogin();
      break;
    case "whoami":
      await cmdWhoami();
      break;
    case "logout":
      await cmdLogout();
      break;
    case "providers":
      await cmdProviders();
      break;
    case "models":
      await cmdModels();
      break;
    case "run":
      await cmdRun(await getDbPath());
      break;
    case "list":
      await cmdList(await getDbPath());
      break;
    case "replay":
      await cmdReplay(await getDbPath());
      break;
    case "benchmark":
      await cmdBenchmark(await getDbPath());
      break;
    case "export":
      await cmdExport(await getDbPath());
      break;
    case "export-training-data":
      await cmdExportTrainingData(await getDbPath());
      break;
    case "import-package":
      await cmdImportPackage(await getDbPath());
      break;
    case "new-scenario":
      await cmdNewScenario(await getDbPath());
      break;
    case "tui":
      await cmdTui(await getDbPath());
      break;
    case "judge":
      await cmdJudge(await getDbPath());
      break;
    case "improve":
      await cmdImprove(await getDbPath());
      break;
    case "repl":
      await cmdRepl(await getDbPath());
      break;
    case "queue":
      await cmdQueue(await getDbPath());
      break;
    case "status":
      await cmdStatus(await getDbPath());
      break;
    case "serve":
      await cmdServeHttp(await getDbPath());
      break;
    case "mcp-serve":
      await cmdMcpServe(await getDbPath());
      break;
    default:
      console.error(`Unknown command: ${command}\n`);
      console.log(HELP);
      process.exit(1);
  }
}

function formatFatalCliError(err: unknown): string {
  if (err instanceof Error) {
    // Clean message only — no stack traces unless DEBUG is set
    if (process.env.DEBUG) {
      return err.stack ?? err.message;
    }
    return `Error: ${err.message}`;
  }
  return String(err);
}

function parsePositiveInteger(raw: string | undefined, label: string): number {
  const parsed = Number.parseInt(raw ?? "", 10);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${label} must be a positive integer`);
  }
  return parsed;
}

async function getDbPath(): Promise<string> {
  const { loadSettings } = await import("../config/index.js");
  const { mkdirSync } = await import("node:fs");
  const dbPath = resolve(loadSettings().dbPath);
  mkdirSync(dirname(dbPath), { recursive: true });
  return dbPath;
}

async function loadProjectDefaults() {
  const { loadProjectConfig } = await import("../config/index.js");
  return loadProjectConfig();
}

interface SavedAgentTaskScenario {
  name: string;
  taskPrompt: string;
  rubric: string;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  revisionPrompt?: string;
  maxRounds?: number;
  qualityThreshold?: number;
}

function mergeUniqueStrings(
  primary?: string[],
  secondary?: string[],
): string[] | undefined {
  const merged = [...(primary ?? []), ...(secondary ?? [])]
    .map((value) => value.trim())
    .filter(Boolean);
  return merged.length > 0 ? [...new Set(merged)] : undefined;
}

async function loadSavedAgentTaskScenario(name: string): Promise<SavedAgentTaskScenario | null> {
  const { loadSettings } = await import("../config/index.js");
  const { resolveCustomAgentTask, renderAgentTaskPrompt } = await import("../scenarios/custom-loader.js");

  const settings = loadSettings();
  const saved = resolveCustomAgentTask(resolve(settings.knowledgeRoot), name);
  if (!saved) {
    return null;
  }

  return {
    name: saved.name,
    taskPrompt: renderAgentTaskPrompt(saved.spec),
    rubric: saved.spec.judgeRubric,
    referenceContext: saved.spec.referenceContext ?? undefined,
    requiredConcepts: saved.spec.requiredConcepts ?? undefined,
    calibrationExamples: saved.spec.calibrationExamples ?? undefined,
    revisionPrompt: saved.spec.revisionPrompt ?? undefined,
    maxRounds: saved.spec.maxRounds,
    qualityThreshold: saved.spec.qualityThreshold,
  };
}

async function resolveScenarioOption(explicit?: string): Promise<string | undefined> {
  if (explicit?.trim()) {
    return explicit.trim();
  }
  return (await loadProjectDefaults())?.defaultScenario;
}

async function promptForValue(label: string): Promise<string> {
  const { createInterface } = await import("node:readline/promises");
  const rl = createInterface({ input: process.stdin, output: process.stderr });
  try {
    return (await rl.question(`${label}: `)).trim();
  } finally {
    rl.close();
  }
}

async function summarizeDirectory(root: string): Promise<{ exists: boolean; directories: number; files: number }> {
  const { existsSync, readdirSync } = await import("node:fs");
  if (!existsSync(root)) {
    return { exists: false, directories: 0, files: 0 };
  }

  let directories = 0;
  let files = 0;
  const stack = [root];

  while (stack.length > 0) {
    const current = stack.pop()!;
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        directories += 1;
        stack.push(join(current, entry.name));
      } else {
        files += 1;
      }
    }
  }

  return { exists: true, directories, files };
}

async function buildProjectConfigSummary(): Promise<Record<string, unknown> | null> {
  const { findProjectConfigLocation, loadProjectConfig, loadSettings } = await import("../config/index.js");
  const projectConfig = loadProjectConfig();
  if (!projectConfig) {
    return null;
  }

  const configLocation = findProjectConfigLocation();
  const settings = loadSettings();
  const dbPath = resolve(settings.dbPath);
  const knowledgeRoot = resolve(settings.knowledgeRoot);
  const { existsSync } = await import("node:fs");

  let totalRuns = 0;
  let activeRuns = 0;
  if (existsSync(dbPath)) {
    const { SQLiteStore } = await import("../storage/index.js");
    const store = new SQLiteStore(dbPath);
    try {
      store.migrate(getMigrationsDir());
      const runs = store.listRuns(1000);
      totalRuns = runs.length;
      activeRuns = runs.filter((run) => run.status === "running").length;
    } finally {
      store.close();
    }
  }

  return {
    path: configLocation?.path ?? null,
    config_source: configLocation?.source ?? null,
    default_scenario: projectConfig.defaultScenario ?? null,
    provider: projectConfig.provider ?? null,
    model: projectConfig.model ?? null,
    gens: projectConfig.gens ?? null,
    runs_root: settings.runsRoot,
    knowledge_root: settings.knowledgeRoot,
    db_path: settings.dbPath,
    active_runs: activeRuns,
    total_runs: totalRuns,
    knowledge_state: await summarizeDirectory(knowledgeRoot),
  };
}

async function writeAgentsGuide(targetDir: string): Promise<boolean> {
  const { existsSync, readFileSync, writeFileSync } = await import("node:fs");
  const agentsPath = join(targetDir, "AGENTS.md");
  const block = [
    "<!-- AUTOCTX_GUIDE_START -->",
    "## AutoContext",
    "",
    "- Use `autoctx capabilities` to inspect supported commands and project state.",
    "- Use `autoctx whoami` to confirm provider credentials before running evaluations.",
    "- Run `autoctx run` from this directory to use the defaults stored in `.autoctx.json`.",
    "<!-- AUTOCTX_GUIDE_END -->",
  ].join("\n");

  if (existsSync(agentsPath)) {
    const existing = readFileSync(agentsPath, "utf-8");
    const start = existing.indexOf("<!-- AUTOCTX_GUIDE_START -->");
    const end = existing.indexOf("<!-- AUTOCTX_GUIDE_END -->");
    if (start !== -1 && end !== -1 && end > start) {
      const replacementEnd = end + "<!-- AUTOCTX_GUIDE_END -->".length;
      const updated = `${existing.slice(0, start)}${block}${existing.slice(replacementEnd)}`;
      writeFileSync(agentsPath, updated.endsWith("\n") ? updated : updated + "\n", "utf-8");
      return true;
    }
    if (existing.includes("## AutoContext")) {
      return false;
    }
    writeFileSync(agentsPath, `${existing.trimEnd()}\n\n${block}\n`, "utf-8");
    return true;
  }

  writeFileSync(agentsPath, `# Agent Guide\n\n${block}\n`, "utf-8");
  return true;
}

function normalizeOllamaBaseUrl(baseUrl?: string): string {
  const normalized = (baseUrl ?? "http://localhost:11434").replace(/\/+$/, "");
  return normalized.endsWith("/v1") ? normalized.slice(0, -3) : normalized;
}

async function validateOllamaConnection(baseUrl: string): Promise<void> {
  try {
    const response = await fetch(`${normalizeOllamaBaseUrl(baseUrl)}/api/tags`);
    if (!response.ok) {
      throw new Error(`Ollama connection failed: ${response.status} ${response.statusText}`);
    }
  } catch (err) {
    if (err instanceof Error && err.message.startsWith("Ollama connection failed:")) {
      throw err;
    }
    throw new Error(
      `Ollama connection failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

async function getProvider(overrides: { providerType?: string; apiKey?: string; baseUrl?: string; model?: string } = {}) {
  const { createConfiguredProvider } = await import("../providers/index.js");
  const { loadSettings } = await import("../config/index.js");

  try {
    const { provider, config } = createConfiguredProvider(overrides, loadSettings());
    const model = config.model ?? provider.defaultModel();
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
      gens: { type: "string", short: "g" },
      "run-id": { type: "string" },
      provider: { type: "string" },
      matches: { type: "string", default: "3" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const scenarioName = await resolveScenarioOption(values.scenario);
  if (values.help) {
    console.log(`autoctx run — Run the generation loop for a scenario

Usage: autoctx run [options]

Options:
  --scenario <name>    Scenario to run (current built-in: grid_ctf)
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

See also: list, replay, export, benchmark`);
    process.exit(0);
  }
  if (!scenarioName) {
    console.error("Error: no scenario configured. Run `autoctx init` or pass --scenario <name>.");
    process.exit(1);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { GenerationRunner } = await import("../loop/generation-runner.js");
  const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
  const { assertFamilyContract } = await import("../scenarios/family-interfaces.js");
  const { loadSettings } = await import("../config/index.js");
  const { buildRoleProviderBundle } = await import("../providers/index.js");

  const settings = loadSettings();
  const providerBundle = buildRoleProviderBundle(
    settings,
    values.provider ? { providerType: values.provider } : {},
  );

  // Resolve scenario
  const ScenarioClass = SCENARIO_REGISTRY[scenarioName];
  if (!ScenarioClass) {
    console.error(`Unknown scenario: ${scenarioName}. Available: ${Object.keys(SCENARIO_REGISTRY).join(", ")}`);
    process.exit(1);
  }
  const scenario = new ScenarioClass();
  assertFamilyContract(scenario, "game", `scenario '${scenarioName}'`);

  // Setup storage
  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());

  const runId = values["run-id"] ?? `run-${Date.now()}`;
  const gens = values.gens
    ? parsePositiveInteger(values.gens, "--gens")
    : settings.defaultGenerations;
  const matches = parsePositiveInteger(values.matches ?? String(settings.matchesPerGeneration), "--matches");

  const runner = new GenerationRunner({
    provider: providerBundle.defaultProvider,
    roleProviders: providerBundle.roleProviders,
    roleModels: providerBundle.roleModels,
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

  const resolvedProvider = providerBundle.defaultConfig.providerType;
  const isSynthetic = resolvedProvider === "deterministic";

  if (isSynthetic && !values.json) {
    console.error("Note: Running with deterministic provider — results are synthetic.");
  }

  try {
    const result = await runner.run(runId, gens);
    if (values.json) {
      console.log(JSON.stringify({
        ...result,
        provider: resolvedProvider,
        ...(isSynthetic ? { synthetic: true } : {}),
      }, null, 2));
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
  const { loadSettings } = await import("../config/index.js");
  const { resolveProviderConfig } = await import("../providers/index.js");
  const settings = loadSettings();
  const providerConfig = resolveProviderConfig();
  const mgr = new RunManager({
    dbPath,
    migrationsDir: getMigrationsDir(),
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
    providerType: providerConfig.providerType,
    apiKey: providerConfig.apiKey,
    baseUrl: providerConfig.baseUrl,
    model: providerConfig.model,
  });
  const server = new InteractiveServer({ runManager: mgr, port });
  await server.start();

  const headless = values.headless || !process.stdout.isTTY;
  if (headless) {
    console.log(`autocontext interactive server listening at ${server.url}`);
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
      scenario: { type: "string", short: "s" },
      prompt: { type: "string", short: "p" },
      output: { type: "string", short: "o" },
      rubric: { type: "string", short: "r" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help || !values.output || (!values.scenario && (!values.prompt || !values.rubric))) {
    console.log(`autoctx judge — One-shot evaluation of output against a rubric

Usage: autoctx judge [options]

Options:
  -s, --scenario <name>  Use a saved custom scenario (provides prompt + rubric)
  -p, --prompt <text>    Task prompt (what was asked of the agent)
  -o, --output <text>    Agent output to evaluate (required)
  -r, --rubric <text>    Evaluation rubric/criteria

Provide either --scenario or both --prompt and --rubric.

Examples:
  autoctx judge -p "Summarize this doc" -o "The doc covers..." -r "Score clarity 0-1"
  autoctx judge -s my_saved_task -o "Agent response here"

See also: improve, queue, run`);
    process.exit(values.help ? 0 : 1);
  }

  const { provider, model } = await getProvider();
  const { LLMJudge } = await import("../judge/index.js");
  const savedScenario = values.scenario ? await loadSavedAgentTaskScenario(values.scenario) : null;
  if (values.scenario && !savedScenario) {
    console.error(`Unknown saved custom scenario: ${values.scenario}`);
    process.exit(1);
  }
  const taskPrompt = values.prompt ?? savedScenario?.taskPrompt;
  const rubric = values.rubric ?? savedScenario?.rubric;
  if (!taskPrompt || !rubric) {
    console.error("Error: judge requires either --scenario <name> or both --prompt and --rubric.");
    process.exit(1);
  }

  const judge = new LLMJudge({ provider, model, rubric });
  const result = await judge.evaluate({
    taskPrompt,
    agentOutput: values.output,
    referenceContext: savedScenario?.referenceContext,
    requiredConcepts: savedScenario?.requiredConcepts,
    calibrationExamples: savedScenario?.calibrationExamples,
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
      scenario: { type: "string", short: "s" },
      prompt: { type: "string", short: "p" },
      output: { type: "string", short: "o" },
      rubric: { type: "string", short: "r" },
      rounds: { type: "string", short: "n" },
      threshold: { type: "string", short: "t" },
      "min-rounds": { type: "string" },
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

  if (values.help || (!values.scenario && (!values.prompt || !values.rubric)) || (!values.output && !values.rlm && !values.scenario)) {
    console.log(`autoctx improve — Run multi-round improvement loop

Usage: autoctx improve [options]

Options:
  -s, --scenario <name>   Use a saved custom scenario (provides prompt + rubric)
  -p, --prompt <text>     Task prompt
  -o, --output <text>     Initial agent output to improve
  -r, --rubric <text>     Evaluation rubric/criteria
  -n, --rounds N          Maximum improvement rounds (default: 5)
  -t, --threshold N       Quality threshold to stop early (default: 0.9)
  --min-rounds N          Minimum rounds before early stop (default: 1)
  --rlm                   Use REPL-loop mode (agent writes + runs code)
  --rlm-turns N           Max REPL turns per round
  -v, --verbose           Show detailed round-by-round output

Provide either --scenario or both --prompt and --rubric.

Examples:
  autoctx improve -p "Write a summary" -o "Draft here" -r "Score clarity" -n 3
  autoctx improve -s my_task -o "Initial draft" --threshold 0.95

See also: judge, queue, run`);
    process.exit(values.help ? 0 : 1);
  }

  const { provider, model } = await getProvider();
  const { SimpleAgentTask } = await import("../execution/task-runner.js");
  const { ImprovementLoop } = await import("../execution/improvement-loop.js");
  const savedScenario = values.scenario ? await loadSavedAgentTaskScenario(values.scenario) : null;
  if (values.scenario && !savedScenario) {
    console.error(`Unknown saved custom scenario: ${values.scenario}`);
    process.exit(1);
  }
  const taskPrompt = values.prompt ?? savedScenario?.taskPrompt;
  const rubric = values.rubric ?? savedScenario?.rubric;
  if (!taskPrompt || !rubric) {
    console.error("Error: improve requires either --scenario <name> or both --prompt and --rubric.");
    process.exit(1);
  }
  const maxRounds = values.rounds
    ? parsePositiveInteger(values.rounds, "--rounds")
    : savedScenario?.maxRounds ?? 5;
  const qualityThreshold = values.threshold
    ? parseFloat(values.threshold)
    : savedScenario?.qualityThreshold ?? 0.9;
  const minRounds = values["min-rounds"]
    ? parsePositiveInteger(values["min-rounds"], "--min-rounds")
    : 1;

  const task = new SimpleAgentTask(
    taskPrompt,
    rubric,
    provider,
    model,
    savedScenario?.revisionPrompt,
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
    maxRounds,
    qualityThreshold,
    minRounds,
  });

  const startTime = performance.now();
  const initialOutput = values.output ?? await task.generateOutput({
    referenceContext: savedScenario?.referenceContext,
    requiredConcepts: savedScenario?.requiredConcepts,
  });
  const result = await loop.run({
    initialOutput,
    state: {},
    referenceContext: savedScenario?.referenceContext,
    requiredConcepts: savedScenario?.requiredConcepts,
    calibrationExamples: savedScenario?.calibrationExamples,
  });
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
      scenario: { type: "string", short: "s" },
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

  if (values.help || (!values.scenario && (!values.prompt || !values.rubric))) {
    console.log(
      "autoctx repl (-s <saved-scenario> | -p <task-prompt>) [-r <rubric>] " +
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
  const savedScenario = values.scenario ? await loadSavedAgentTaskScenario(values.scenario) : null;
  if (values.scenario && !savedScenario) {
    console.error(`Unknown saved custom scenario: ${values.scenario}`);
    process.exit(1);
  }
  const taskPrompt = values.prompt ?? savedScenario?.taskPrompt;
  const rubric = values.rubric ?? savedScenario?.rubric;
  if (!taskPrompt || !rubric) {
    console.error("Error: repl requires either --scenario <name> or both --prompt and --rubric.");
    process.exit(1);
  }
  const requiredConcepts = mergeUniqueStrings(
    savedScenario?.requiredConcepts,
    values["required-concept"],
  );

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
    taskPrompt,
    rubric,
    currentOutput: values.output,
    referenceContext: values["reference-context"] ?? savedScenario?.referenceContext,
    requiredConcepts,
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
  const savedScenario = await loadSavedAgentTaskScenario(values.spec);

  const store = new SQLiteStore(dbPath);
  const migrationsDir = getMigrationsDir();
  store.migrate(migrationsDir);

  const id = enqueueTask(store, values.spec, {
    taskPrompt: values.prompt ?? savedScenario?.taskPrompt,
    rubric: values.rubric ?? savedScenario?.rubric,
    referenceContext: savedScenario?.referenceContext,
    requiredConcepts: savedScenario?.requiredConcepts,
    maxRounds: savedScenario?.maxRounds,
    qualityThreshold: savedScenario?.qualityThreshold,
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

async function cmdServeHttp(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      port: { type: "string", default: "8000" },
      host: { type: "string", default: "127.0.0.1" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx serve [--port 8000] [--host 127.0.0.1] [--json]");
    console.log("Starts the HTTP dashboard + API server (matches Python 'autoctx serve').");
    console.log("With --json, prints a machine-parseable JSON line on startup.");
    process.exit(0);
  }

  const port = parseInt(values.port ?? "8000", 10);
  const host = values.host ?? "127.0.0.1";

  const { RunManager, InteractiveServer } = await import("../server/index.js");
  const { loadSettings } = await import("../config/index.js");
  const settings = loadSettings();

  const mgr = new RunManager({
    dbPath,
    migrationsDir: getMigrationsDir(),
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
    providerType: settings.agentProvider,
  });
  const server = new InteractiveServer({ runManager: mgr, port, host });
  await server.start();

  const startupInfo = {
    url: `http://${host}:${server.port}`,
    apiUrl: `http://${host}:${server.port}/api/runs`,
    wsUrl: `ws://${host}:${server.port}/ws/interactive`,
    host,
    port: server.port,
    scenarios: mgr.listScenarios(),
  };

  if (values.json) {
    console.log(JSON.stringify(startupInfo));
  } else {
    console.log(`autocontext server listening at ${startupInfo.url}`);
    console.log(`API: ${startupInfo.apiUrl}`);
    console.log(`WebSocket: ${startupInfo.wsUrl}`);
    console.log(`Scenarios: ${startupInfo.scenarios.join(", ")}`);
  }

  await new Promise<void>((res) => {
    const cleanup = () => { process.off("SIGINT", cleanup); process.off("SIGTERM", cleanup); res(); };
    process.on("SIGINT", cleanup);
    process.on("SIGTERM", cleanup);
  });
  await server.stop();
}

async function cmdMcpServe(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log(`autoctx mcp-serve — Start MCP server on stdio

Starts the Model Context Protocol server on stdio for integration with
Claude Code, Cursor, and other MCP-compatible editors.

Core exported tools:
  evaluate_output       Evaluate output against a rubric
  run_improvement_loop  Multi-round improvement loop
  queue_task            Enqueue a task for background evaluation
  get_queue_status      Check task queue status
  list_runs             List recent runs
  get_run_status        Get detailed run status
  run_replay            Replay a generation
  list_scenarios        List available scenarios
  export_package        Export strategy package data
  create_agent_task     Create a saved agent-task scenario

Additional tools cover playbooks, sandboxing, tournaments, and package import/export.

Transport: stdio (JSON-RPC over stdin/stdout)

See also: serve, judge, improve`);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { startServer } = await import("../mcp/server.js");
  const { loadSettings } = await import("../config/index.js");

  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());

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
    console.log(`autoctx list — List recent runs

Usage: autoctx list [options]

Options:
  --limit N            Maximum number of runs to show (default: 50)
  --scenario <name>    Filter runs by scenario name
  --json               Output as JSON array

See also: run, replay, status`);
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
    console.log(`autoctx replay — Print replay JSON for a generation

Usage: autoctx replay [options]

Options:
  --run-id <id>        Run to replay (required)
  --generation N       Generation number to replay (default: 1)

See also: run, list, export`);
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
  const generationsDir = join(
    resolve(settings.runsRoot),
    values["run-id"],
    "generations",
  );
  const availableGenerations = existsSync(generationsDir)
    ? readdirSync(generationsDir)
      .map((name) => {
        const match = /^gen_(\d+)$/.exec(name);
        return match ? parseInt(match[1] ?? "", 10) : null;
      })
      .filter((value): value is number => value !== null)
      .sort((a, b) => a - b)
    : [];
  const replayDir = join(
    resolve(settings.runsRoot),
    values["run-id"],
    "generations",
    `gen_${gen}`,
    "replays",
  );
  if (!existsSync(replayDir)) {
    const available = availableGenerations.length > 0
      ? ` Available generations: ${availableGenerations.join(", ")}.`
      : "";
    console.error(`No replay files found under ${replayDir}.${available}`);
    process.exit(1);
  }
  const replayFiles = readdirSync(replayDir)
    .filter((name) => name.endsWith(".json"))
    .sort();
  if (replayFiles.length === 0) {
    const available = availableGenerations.length > 0
      ? ` Available generations: ${availableGenerations.join(", ")}.`
      : "";
    console.error(`No replay files found under ${replayDir}.${available}`);
    process.exit(1);
  }
  const payload = JSON.parse(readFileSync(join(replayDir, replayFiles[0]), "utf-8"));
  const available = availableGenerations.length > 0 ? availableGenerations.join(", ") : String(gen);
  console.error(`Replaying generation ${gen}. Available generations: ${available}`);
  console.log(JSON.stringify(payload, null, 2));
}

async function cmdBenchmark(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      scenario: { type: "string", default: "grid_ctf" },
      runs: { type: "string", default: "3" },
      gens: { type: "string", default: "1" },
      provider: { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log(`autoctx benchmark — Run benchmark (multiple runs, aggregate stats)

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

See also: run, list`);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { GenerationRunner } = await import("../loop/generation-runner.js");
  const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
  const { assertFamilyContract } = await import("../scenarios/family-interfaces.js");
  const { loadSettings } = await import("../config/index.js");
  const { buildRoleProviderBundle } = await import("../providers/index.js");

  const scenarioName = (await resolveScenarioOption(values.scenario)) ?? "grid_ctf";
  const ScenarioClass = SCENARIO_REGISTRY[scenarioName];
  if (!ScenarioClass) {
    console.error(`Unknown scenario: ${scenarioName}`);
    process.exit(1);
  }

  const numRuns = parseInt(values.runs ?? "3", 10);
  const numGens = parseInt(values.gens ?? "1", 10);
  const settings = loadSettings();
  const providerBundle = buildRoleProviderBundle(
    settings,
    values.provider ? { providerType: values.provider } : {},
  );
  const scores: number[] = [];

  for (let i = 0; i < numRuns; i++) {
    const store = new SQLiteStore(dbPath);
    store.migrate(getMigrationsDir());
    const runId = `bench_${Date.now()}_${i}`;
    const scenario = new ScenarioClass();
    assertFamilyContract(scenario, "game", `scenario '${scenarioName}'`);
    const runner = new GenerationRunner({
      provider: providerBundle.defaultProvider,
      roleProviders: providerBundle.roleProviders,
      roleModels: providerBundle.roleModels,
      scenario,
      store,
      runsRoot: resolve(settings.runsRoot),
      knowledgeRoot: resolve(settings.knowledgeRoot),
    });
    const result = await runner.run(runId, numGens);
    scores.push(result.bestScore);
    store.close();
  }

  const resolvedBenchProvider = providerBundle.defaultConfig.providerType;
  const isBenchSynthetic = resolvedBenchProvider === "deterministic";

  const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
  const output = {
    scenario: scenarioName,
    runs: numRuns,
    generations: numGens,
    scores,
    meanBestScore: mean,
    provider: resolvedBenchProvider,
    ...(isBenchSynthetic ? { synthetic: true } : {}),
  };
  if (values.json) {
    console.log(JSON.stringify(output, null, 2));
  } else {
    if (isBenchSynthetic) {
      console.error("Note: Running with deterministic provider — results are synthetic.");
    }
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
    console.log(`autoctx export — Export strategy package for a scenario

Usage: autoctx export [options]

Options:
  --scenario <name>    Scenario to export (required)
  --output <file>      Output file path (default: stdout)
  --json               Force JSON output format

See also: import-package, run, replay`);
    process.exit(0);
  }

  const scenarioName = await resolveScenarioOption(values.scenario);
  if (!scenarioName) {
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
      scenarioName,
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

async function cmdExportTrainingData(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      "run-id": { type: "string" },
      scenario: { type: "string" },
      "all-runs": { type: "boolean" },
      output: { type: "string", short: "o" },
      "include-matches": { type: "boolean" },
      "kept-only": { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx export-training-data --run-id <id> [--scenario <name> --all-runs] [--output <file>] [--include-matches] [--kept-only]");
    console.log("\nExports training data as JSONL with Python-compatible snake_case fields.");
    console.log("\nUnsupported Python commands: train, trigger-distillation (require MLX/CUDA backends)");
    process.exit(0);
  }

  if (!values["run-id"] && !values.scenario) {
    console.error("Error: --run-id or --scenario is required");
    process.exit(1);
  }

  if (values.scenario && !values["run-id"] && !values["all-runs"]) {
    console.error("Error: --all-runs is required with --scenario");
    process.exit(1);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { loadSettings } = await import("../config/index.js");
  const { ArtifactStore } = await import("../knowledge/artifact-store.js");
  const { exportTrainingData } = await import("../training/export.js");

  const settings = loadSettings();
  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());
  const artifacts = new ArtifactStore({
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
  });

  try {
    console.error(`Exporting training data${values["run-id"] ? ` for run ${values["run-id"]}` : ` for scenario ${values.scenario}` }...`);
    const records = exportTrainingData(store, artifacts, {
      runId: values["run-id"],
      scenario: values.scenario,
      includeMatches: values["include-matches"],
      keptOnly: values["kept-only"],
      onProgress: (progress) => {
        if (progress.phase === "start") {
          console.error(`Scanning ${progress.totalRuns} run(s)...`);
          return;
        }
        if (progress.phase === "generation" && progress.generationIndex !== undefined) {
          console.error(
            `Processed run ${progress.runId} generation ${progress.generationIndex} (${progress.recordsEmitted} records)`,
          );
        }
      },
    });

    const jsonl = records.map((r) => JSON.stringify(r)).join("\n");
    console.error(`Exported ${records.length} record(s).`);

    if (values.output) {
      const { writeFileSync, mkdirSync } = await import("node:fs");
      mkdirSync(dirname(values.output), { recursive: true });
      writeFileSync(values.output, jsonl + "\n", "utf-8");
      console.log(JSON.stringify({ output: values.output, records: records.length }));
    } else {
      console.log(jsonl);
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
      "from-spec": { type: "string" },
      "from-stdin": { type: "boolean" },
      "prompt-only": { type: "boolean" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log(`autoctx new-scenario — create a scenario

Modes:
  --description <text>    Generate scenario from natural language (requires LLM provider)
  --from-spec <file>      Register a scenario from a JSON spec file (no LLM needed)
  --from-stdin            Read a JSON spec from stdin (no LLM needed)
  --prompt-only           Output the generation prompt without calling an LLM

Spec schema (for --from-spec and --from-stdin):
  {
    "name": "...",
    "family": "agent_task|simulation|artifact_editing|investigation|workflow|schema_evolution|tool_fragility|negotiation|operator_loop|coordination|game",
    "taskPrompt": "...",
    "rubric": "...",
    "description": "..."
  }
  If family is omitted, autoctx derives the best-fit family from the spec text.

Options:
  --json                  Output as JSON
  -h, --help              Show this help`);
    process.exit(0);
  }

  const {
    createScenarioFromDescription,
    buildScenarioCreationPrompt,
    detectScenarioFamily,
    isScenarioFamilyName,
  } = await import("../scenarios/scenario-creator.js");
  const { SCENARIO_TYPE_MARKERS } = await import("../scenarios/families.js");
  const validFamilies = Object.keys(SCENARIO_TYPE_MARKERS).sort();

  const normalizeImportedScenario = (spec: Record<string, unknown>) => {
    const name = typeof spec.name === "string" ? spec.name.trim() : "";
    const taskPrompt = typeof spec.taskPrompt === "string" ? spec.taskPrompt.trim() : "";
    const rubric = typeof spec.rubric === "string" ? spec.rubric.trim() : "";
    const description = typeof spec.description === "string" ? spec.description : "";

    if (!name || !taskPrompt || !rubric) {
      console.error("Error: spec must contain name, taskPrompt, and rubric fields");
      process.exit(1);
    }

    let family = detectScenarioFamily([description, taskPrompt].filter(Boolean).join("\n"));
    if (typeof spec.family === "string" && spec.family.trim()) {
      const requestedFamily = spec.family.trim();
      if (!isScenarioFamilyName(requestedFamily)) {
        console.error(`Error: family must be one of ${validFamilies.join(", ")}`);
        process.exit(1);
      }
      family = requestedFamily;
    }

    const { name: _ignoredName, family: _ignoredFamily, ...specFields } = spec;
    return {
      name,
      family,
      spec: {
        ...specFields,
        taskPrompt,
        rubric,
        description,
      },
    };
  };

  // Mode 1: --from-spec <file>
  if (values["from-spec"]) {
    const { readFileSync } = await import("node:fs");
    let spec: Record<string, unknown>;
    try {
      spec = JSON.parse(readFileSync(values["from-spec"], "utf-8"));
    } catch (err) {
      console.error(`Error reading spec file: ${(err as Error).message}`);
      process.exit(1);
    }
    const result = normalizeImportedScenario(spec);
    console.log(values.json ? JSON.stringify(result, null, 2) : `Registered scenario: ${result.name}`);
    return;
  }

  // Mode 2: --from-stdin
  if (values["from-stdin"]) {
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) {
      chunks.push(chunk as Buffer);
    }
    const raw = Buffer.concat(chunks).toString("utf-8");
    let spec: Record<string, unknown>;
    try {
      spec = JSON.parse(raw);
    } catch {
      console.error("Error: stdin must contain valid JSON");
      process.exit(1);
    }
    const result = normalizeImportedScenario(spec);
    console.log(values.json ? JSON.stringify(result, null, 2) : `Registered scenario: ${result.name}`);
    return;
  }

  // Mode 3: --prompt-only (output the prompt, no LLM call)
  if (values["prompt-only"]) {
    if (!values.description) {
      console.error("Error: --description is required with --prompt-only");
      process.exit(1);
    }
    const prompt = buildScenarioCreationPrompt(values.description);
    console.log(prompt);
    return;
  }

  // Default: --description mode (requires LLM)
  if (!values.description) {
    console.error("Error: --description, --from-spec, --from-stdin, or --prompt-only is required");
    process.exit(1);
  }

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

// ---------------------------------------------------------------------------
// New DX commands (AC-393, AC-405, AC-407)
// ---------------------------------------------------------------------------

async function cmdInit(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      dir: { type: "string", default: "." },
      scenario: { type: "string" },
      provider: { type: "string" },
      model: { type: "string" },
      gens: { type: "string", default: "3" },
      "agents-md": { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log(`autoctx init — Scaffold project config and AGENTS guidance

Usage: autoctx init [options]

Options:
  --dir <path>         Directory to initialize (default: current directory)
  --scenario <name>    Default scenario (default: grid_ctf)
  --provider <type>    Default provider (default: deterministic)
  --model <name>       Default model for the provider
  --gens N             Default generations per run (default: 3)

Creates .autoctx.json, AGENTS.md, runs/, and knowledge/ directories.

Examples:
  autoctx init
  autoctx init --scenario grid_ctf --provider anthropic --gens 5
  autoctx init --dir ./my-project

See also: run, login, capabilities`);
    process.exit(0);
  }

  const { existsSync, mkdirSync, writeFileSync } = await import("node:fs");
  const { loadPersistedCredentials, loadProjectConfig } = await import("../config/index.js");
  const { resolveProviderConfig } = await import("../providers/index.js");
  const targetDir = resolve(values.dir ?? ".");
  const configPath = join(targetDir, ".autoctx.json");
  const projectDefaults = loadProjectConfig(targetDir);
  const persistedCredentials = loadPersistedCredentials();

  if (existsSync(configPath)) {
    console.error("Error: .autoctx.json already exists in " + targetDir);
    process.exit(1);
  }

  mkdirSync(targetDir, { recursive: true });
  let detectedProvider =
    values.provider?.trim() ??
    projectDefaults?.provider ??
    process.env.AUTOCONTEXT_AGENT_PROVIDER?.trim() ??
    process.env.AUTOCONTEXT_PROVIDER?.trim() ??
    persistedCredentials?.provider;
  let detectedModel =
    values.model?.trim() ??
    projectDefaults?.model ??
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL?.trim() ??
    process.env.AUTOCONTEXT_MODEL?.trim() ??
    persistedCredentials?.model;
  try {
    const resolved = resolveProviderConfig();
    detectedProvider = detectedProvider ?? resolved.providerType;
    detectedModel = detectedModel ?? resolved.model;
  } catch {
    detectedProvider = detectedProvider ?? "deterministic";
  }

  const config: Record<string, unknown> = {
    default_scenario: values.scenario ?? projectDefaults?.defaultScenario ?? "grid_ctf",
    provider: detectedProvider ?? "deterministic",
    gens: parsePositiveInteger(values.gens ?? "3", "--gens"),
    knowledge_dir: "./knowledge",
    runs_dir: "./runs",
  };
  if (detectedModel) {
    config.model = detectedModel;
  }
  mkdirSync(join(targetDir, "runs"), { recursive: true });
  mkdirSync(join(targetDir, "knowledge"), { recursive: true });
  writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n", "utf-8");

  const agentsMdUpdated = await writeAgentsGuide(targetDir);

  console.log(`Created ${configPath}`);
  console.log(agentsMdUpdated ? `Updated ${join(targetDir, "AGENTS.md")}` : `AGENTS.md already contained AutoContext guidance`);
}

async function cmdCapabilities(): Promise<void> {
  const pkg = await import("../../package.json", { with: { type: "json" } });
  const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
  const projectConfig = await buildProjectConfigSummary();

  const capabilities = {
    version: pkg.default.version,
    commands: [
      "init", "run", "list", "replay", "benchmark", "export",
      "export-training-data", "import-package", "new-scenario",
      "capabilities", "login", "whoami", "logout", "providers", "models",
      "tui", "judge", "improve",
      "repl", "queue", "status", "serve", "mcp-serve", "version",
    ],
    scenarios: Object.keys(SCENARIO_REGISTRY).sort(),
    providers: [
      "anthropic", "openai", "openai-compatible", "ollama", "vllm",
      "hermes", "pi", "pi-rpc", "deterministic",
    ],
    features: {
      mcp_server: true,
      training_export: true,
      custom_scenarios: true,
      interactive_server: true,
      playbook_versioning: true,
    },
    pythonOnly: [
      "train", "ecosystem", "ab-test", "resume", "wait", "trigger-distillation",
    ],
    project_config: projectConfig,
  };
  console.log(JSON.stringify(capabilities, null, 2));
}

async function cmdLogin(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      provider: { type: "string" },
      key: { type: "string" },
      model: { type: "string" },
      "base-url": { type: "string" },
      "config-dir": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log(`autoctx login — Store provider credentials persistently

Usage: autoctx login [options]

Options:
  --provider <type>    Provider name: anthropic, openai, gemini, ollama, groq, etc.
  --key <api-key>      API key (omit to be prompted interactively)
  --model <name>       Default model for this provider
  --base-url <url>     Custom base URL (for Ollama, vLLM, proxies)
  --config-dir <path>  Config directory (default: ~/.config/autoctx)

Without flags, prompts interactively for provider and key.
Keys starting with ! are executed as shell commands (e.g. !security find-generic-password).

Examples:
  autoctx login --provider anthropic --key sk-ant-...
  autoctx login --provider ollama --base-url http://localhost:11434
  autoctx login                            # interactive prompt

See also: whoami, logout, providers, models`);
    process.exit(0);
  }

  let provider = values.provider?.trim();
  if (!provider) {
    provider = await promptForValue("Provider");
  }
  if (!provider) {
    console.error("Error: provider is required");
    process.exit(1);
  }
  provider = provider.toLowerCase();

  const { resolveConfigDir } = await import("../config/index.js");
  let apiKey = values.key?.trim();
  let baseUrl = values["base-url"]?.trim();
  const model = values.model?.trim();

  if (provider === "ollama") {
    baseUrl = normalizeOllamaBaseUrl(
      baseUrl ??
      process.env.AUTOCONTEXT_AGENT_BASE_URL ??
      process.env.AUTOCONTEXT_BASE_URL ??
      "http://localhost:11434",
    );
    await validateOllamaConnection(baseUrl);
  } else {
    if (!apiKey) {
      apiKey = await promptForValue("API key");
    }
    if (!apiKey) {
      console.error("Error: --key is required for this provider");
      process.exit(1);
    }
  }

  // Validate API key format before saving (AC-430)
  if (apiKey) {
    const { validateApiKey, resolveApiKeyValue } = await import("../config/credentials.js");
    // Resolve shell-command escape hatch (e.g. "!security find-generic-password -ws 'anthropic'")
    const resolvedKey = resolveApiKeyValue(apiKey);
    const validation = await validateApiKey(provider, resolvedKey);
    if (!validation.valid) {
      console.error(`Warning: ${validation.error}`);
    }
  }

  // Save to multi-provider credential store with 0600 permissions (AC-430)
  const { saveProviderCredentials } = await import("../config/credentials.js");
  const configDir = resolveConfigDir(values["config-dir"]);
  const creds: Record<string, string | undefined> = {};
  if (apiKey) creds.apiKey = apiKey;
  if (model) creds.model = model;
  if (baseUrl) creds.baseUrl = baseUrl;
  saveProviderCredentials(configDir, provider, creds);

  if (provider === "ollama") {
    console.log(`Connected to Ollama at ${baseUrl}`);
  } else {
    console.log(`Credentials saved for ${provider}`);
  }
}

async function cmdWhoami(): Promise<void> {
  const { loadPersistedCredentials, loadProjectConfig } = await import("../config/index.js");
  const { resolveProviderConfig } = await import("../providers/index.js");
  const { resolveConfigDir } = await import("../config/index.js");

  const projectConfig = loadProjectConfig();
  const configDir = resolveConfigDir();
  const defaultPersistedCredentials = loadPersistedCredentials(configDir);
  let resolvedConfig: { providerType: string; apiKey?: string; model?: string; baseUrl?: string } | null = null;

  try {
    resolvedConfig = resolveProviderConfig();
  } catch {
    resolvedConfig = null;
  }

  const provider =
    resolvedConfig?.providerType ??
    projectConfig?.provider ??
    defaultPersistedCredentials?.provider ??
    "not configured";
  const persistedCredentials =
    provider !== "not configured"
      ? loadPersistedCredentials(configDir, provider)
      : defaultPersistedCredentials;
  const model =
    resolvedConfig?.model ??
    projectConfig?.model ??
    persistedCredentials?.model ??
    process.env.AUTOCONTEXT_MODEL ??
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL ??
    "default";
  const baseUrl =
    resolvedConfig?.baseUrl ??
    persistedCredentials?.baseUrl ??
    process.env.AUTOCONTEXT_AGENT_BASE_URL ??
    process.env.AUTOCONTEXT_BASE_URL;
  const authenticated = provider === "ollama" || Boolean(
    resolvedConfig?.apiKey ??
    process.env.ANTHROPIC_API_KEY ??
    process.env.OPENAI_API_KEY ??
    persistedCredentials?.apiKey,
  );

  // Also list all configured providers (AC-430)
  const { listConfiguredProviders } = await import("../config/credentials.js");
  const configuredProviders = listConfiguredProviders(configDir);

  console.log(JSON.stringify({
    provider,
    model,
    authenticated,
    ...(baseUrl ? { baseUrl } : {}),
    ...(configuredProviders.length > 0 ? { configuredProviders } : {}),
  }, null, 2));
}

async function cmdLogout(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      "config-dir": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log("autoctx logout [--config-dir <path>]");
    console.log("Clears stored provider credentials.");
    process.exit(0);
  }

  const { existsSync, unlinkSync } = await import("node:fs");
  const { loadPersistedCredentials, resolveConfigDir } = await import("../config/index.js");
  const configDir = resolveConfigDir(values["config-dir"]);
  const credentialsPath = join(configDir, "credentials.json");
  const existing = loadPersistedCredentials(configDir);

  if (!existsSync(credentialsPath)) {
    console.log("No stored credentials found.");
    return;
  }

  unlinkSync(credentialsPath);
  console.log(existing?.provider ? `Logged out from ${existing.provider}` : "Logged out.");
}

async function cmdProviders(): Promise<void> {
  const { KNOWN_PROVIDERS, discoverAllProviders } = await import("../config/credentials.js");
  const { resolveConfigDir } = await import("../config/index.js");
  const configDir = resolveConfigDir();
  const discovered = discoverAllProviders(configDir);
  const discoveredMap = new Map(discovered.map((d) => [d.provider, d]));

  const result = KNOWN_PROVIDERS.map((p) => {
    const d = discoveredMap.get(p.id);
    return {
      id: p.id,
      displayName: p.displayName,
      requiresKey: p.requiresKey,
      authenticated: d ? (d.hasApiKey || !p.requiresKey) : !p.requiresKey,
      ...(d?.source ? { source: d.source } : {}),
      ...(d?.model ? { model: d.model } : {}),
      ...(d?.baseUrl ? { baseUrl: d.baseUrl } : {}),
    };
  });

  console.log(JSON.stringify(result, null, 2));
}

async function cmdModels(): Promise<void> {
  const { listAuthenticatedModels } = await import("../config/credentials.js");
  const { resolveConfigDir } = await import("../config/index.js");
  const configDir = resolveConfigDir();
  const models = listAuthenticatedModels(configDir);

  if (models.length === 0) {
    console.log(JSON.stringify([]));
    console.log("\nNo authenticated providers found. Run `autoctx login` to configure a provider.");
    return;
  }

  console.log(JSON.stringify(models, null, 2));
}

main().catch((err) => {
  console.error(formatFatalCliError(err));
  process.exit(1);
});
