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
 *   autoctx serve     — start HTTP API server
 *   autoctx mcp-serve — start MCP server on stdio
 */

import { parseArgs } from "node:util";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { emitEngineResult } from "./emit-engine-result.js";
import type { CampaignStatus } from "../mission/campaign.js";

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
  new-scenario     Create or scaffold a scenario
  capabilities     Show available scenarios, providers, and features (JSON)
  login            Store provider credentials persistently
  whoami           Show current auth status and provider
  logout           Clear stored provider credentials
  providers        List all known providers with auth status (JSON)
  models           List available models for authenticated providers (JSON)
  campaign         Manage multi-mission campaigns
  tui              Start interactive TUI (WebSocket server + Ink UI)
  judge            One-shot evaluation of output against a rubric
  improve          Run multi-round improvement loop
  repl             Run a direct REPL-loop session
  queue            Add a task to the background runner queue
  status           Show queue status
  serve            Start HTTP API server [--json]
  train            Train a distilled model from curated dataset (requires configured executor)
  simulate         Run a plain-language simulation with sweeps and analysis
  investigate      Run a plain-language investigation with evidence and hypotheses
  analyze          Analyze and compare runs, simulations, investigations, and missions
  mcp-serve        Start MCP server on stdio
  version          Show version

Control plane (Layer 7-9):
  candidate        Register/list/show/lineage/rollback control-plane artifacts
  eval             Attach/list EvalRuns on artifacts
  promotion        Decide/apply/history for promotion transitions
  registry         Repair/validate/migrate the control-plane registry
  emit-pr          Generate a promotion PR (or dry-run bundle) for a candidate
  production-traces Ingest/list/show/stats/build-dataset/export/policy/rotate-salt/prune (Foundation A — AC-539)
  instrument       Scan a repo for LLM clients and propose/apply Autocontext wrappers (A2-I — AC-540)

Python-only commands (not supported in npm package):
  ecosystem, ab-test, resume, wait, trigger-distillation

Run \`autoctx <command> --help\` for command-specific options.

Install: npm install -g autoctx
Note: The npm package is \`autoctx\`, not \`autocontext\` (different package).
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
      console.log(
        "\nTip: Run `autoctx init` to set up this project with a .autoctx.json config.",
      );
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
    case "mission":
      await cmdMission(await getDbPath());
      break;
    case "campaign":
      await cmdCampaign(await getDbPath());
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
    case "train":
      await cmdTrain();
      break;
    case "simulate":
      await cmdSimulate();
      break;
    case "investigate":
      await cmdInvestigate();
      break;
    case "analyze":
      await cmdAnalyze();
      break;
    case "blob":
      await cmdBlob();
      break;
    case "candidate":
    case "eval":
    case "promotion":
    case "registry":
    case "emit-pr":
      await cmdControlPlane(command);
      break;
    case "production-traces":
      await cmdProductionTraces();
      break;
    case "instrument":
      await cmdInstrument();
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

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
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

async function loadSavedAgentTaskScenario(
  name: string,
): Promise<SavedAgentTaskScenario | null> {
  const { loadSettings } = await import("../config/index.js");
  const { resolveCustomJudgeScenario, renderAgentTaskPrompt } =
    await import("../scenarios/custom-loader.js");

  const settings = loadSettings();
  const saved = resolveCustomJudgeScenario(
    resolve(settings.knowledgeRoot),
    name,
  );
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

async function resolveScenarioOption(
  explicit?: string,
): Promise<string | undefined> {
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

async function summarizeDirectory(
  root: string,
): Promise<{ exists: boolean; directories: number; files: number }> {
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

async function buildProjectConfigSummary(): Promise<Record<
  string,
  unknown
> | null> {
  const { findProjectConfigLocation, loadProjectConfig, loadSettings } =
    await import("../config/index.js");
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
      writeFileSync(
        agentsPath,
        updated.endsWith("\n") ? updated : updated + "\n",
        "utf-8",
      );
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
      throw new Error(
        `Ollama connection failed: ${response.status} ${response.statusText}`,
      );
    }
  } catch (err) {
    if (
      err instanceof Error &&
      err.message.startsWith("Ollama connection failed:")
    ) {
      throw err;
    }
    throw new Error(
      `Ollama connection failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

async function getProvider(
  overrides: {
    providerType?: string;
    apiKey?: string;
    baseUrl?: string;
    model?: string;
  } = {},
) {
  const { createConfiguredProvider } = await import("../providers/index.js");
  const { loadSettings } = await import("../config/index.js");

  try {
    const { provider, config } = createConfiguredProvider(
      overrides,
      loadSettings(),
    );
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

  const {
    executeRunCommandWorkflow,
    planRunCommand,
    renderRunResult,
    RUN_HELP_TEXT,
  } = await import("./run-command-workflow.js");

  if (values.help) {
    console.log(RUN_HELP_TEXT);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { GenerationRunner } = await import("../loop/generation-runner.js");
  const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
  const { assertFamilyContract } =
    await import("../scenarios/family-interfaces.js");
  const { loadSettings } = await import("../config/index.js");
  const { buildRoleProviderBundle } = await import("../providers/index.js");
  const { resolveRunnableScenarioClass } = await import("./runnable-scenario-resolution.js");

  const settings = loadSettings();
  let plan;
  try {
    plan = await planRunCommand(
      values,
      resolveScenarioOption,
      {
        defaultGenerations: settings.defaultGenerations,
        matchesPerGeneration: settings.matchesPerGeneration,
      },
      Date.now,
      parsePositiveInteger,
    );
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const providerBundle = buildRoleProviderBundle(
    settings,
    plan.providerType ? { providerType: plan.providerType } : {},
  );

  let ScenarioClass;
  try {
    ScenarioClass = resolveRunnableScenarioClass({
      scenarioName: plan.scenarioName,
      builtinScenarios: SCENARIO_REGISTRY,
      knowledgeRoot: resolve(settings.knowledgeRoot),
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const result = await executeRunCommandWorkflow({
    dbPath,
    migrationsDir: getMigrationsDir(),
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
    settings,
    plan,
    providerBundle,
    ScenarioClass,
    assertFamilyContract,
    createStore: (runDbPath) => new SQLiteStore(runDbPath),
    createRunner: (runnerOpts) =>
      new GenerationRunner(
        runnerOpts as import("../loop/generation-runner.js").GenerationRunnerOpts,
      ),
  });

  const rendered = renderRunResult(result, plan.json);
  if (rendered.stderr) {
    console.error(rendered.stderr);
  }
  console.log(rendered.stdout);
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

  const {
    buildHeadlessTuiOutput,
    buildInteractiveTuiRequest,
    planTuiCommand,
    TUI_HELP_TEXT,
  } = await import("./tui-command-workflow.js");

  if (values.help) {
    console.log(TUI_HELP_TEXT);
    process.exit(0);
  }

  const plan = planTuiCommand(values, !!process.stdout.isTTY);

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
  const server = new InteractiveServer({ runManager: mgr, port: plan.port });
  await server.start();

  if (plan.headless) {
    for (const line of buildHeadlessTuiOutput({
      serverUrl: server.url,
      scenarios: mgr.listScenarios(),
    })) {
      console.log(line);
    }
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

  const app = render(
    React.createElement(
      InteractiveTui,
      buildInteractiveTuiRequest({
        manager: mgr,
        serverUrl: server.url,
      }),
    ),
  );

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
      "from-stdin": { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    executeJudgeCommandWorkflow,
    getJudgeUsageExitCode,
    JUDGE_HELP_TEXT,
    parseDelegatedJudgeInput,
    planJudgeCommand,
    renderJudgeResult,
  } = await import("./judge-command-workflow.js");

  const usageExitCode = getJudgeUsageExitCode(values);
  if (usageExitCode !== null) {
    console.log(JUDGE_HELP_TEXT);
    process.exit(usageExitCode);
  }

  // AC-409: Agent-as-judge — accept pre-computed evaluation from stdin
  if (values["from-stdin"]) {
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) {
      chunks.push(chunk as Buffer);
    }
    const input = Buffer.concat(chunks).toString("utf-8").trim();
    try {
      console.log(renderJudgeResult(parseDelegatedJudgeInput(input)));
      process.exit(0);
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
  }

  const { provider, model } = await getProvider();
  const { LLMJudge } = await import("../judge/index.js");
  const savedScenario = values.scenario
    ? await loadSavedAgentTaskScenario(values.scenario)
    : null;
  if (values.scenario && !savedScenario) {
    console.error(`Unknown saved custom scenario: ${values.scenario}`);
    process.exit(1);
  }

  let plan;
  try {
    plan = planJudgeCommand(values, savedScenario);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const result = await executeJudgeCommandWorkflow({
    plan,
    provider,
    model: model ?? undefined,
    createJudge: (judgeOpts) => {
      const provider = judgeOpts.provider as import("../types/index.js").LLMProvider;
      return new LLMJudge({
        provider,
        model: judgeOpts.model ?? provider.defaultModel(),
        rubric: judgeOpts.rubric,
      });
    },
  });

  console.log(renderJudgeResult(result));
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

  const {
    executeImproveCommandWorkflow,
    getImproveUsageExitCode,
    IMPROVE_HELP_TEXT,
    planImproveCommand,
    renderImproveResult,
  } = await import("./improve-command-workflow.js");

  const usageExitCode = getImproveUsageExitCode(values);
  if (usageExitCode !== null) {
    console.log(IMPROVE_HELP_TEXT);
    process.exit(usageExitCode);
  }

  const { provider, model } = await getProvider();
  const { SimpleAgentTask } = await import("../execution/task-runner.js");
  const { ImprovementLoop } = await import("../execution/improvement-loop.js");
  const savedScenario = values.scenario
    ? await loadSavedAgentTaskScenario(values.scenario)
    : null;
  if (values.scenario && !savedScenario) {
    console.error(`Unknown saved custom scenario: ${values.scenario}`);
    process.exit(1);
  }

  let plan;
  try {
    plan = planImproveCommand(values, savedScenario, parsePositiveInteger);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const result = await executeImproveCommandWorkflow({
    plan,
    provider,
    model,
    savedScenario,
    createTask: (taskPrompt, rubric, taskProvider, taskModel, revisionPrompt, rlmConfig) =>
      new SimpleAgentTask(
        taskPrompt,
        rubric,
        taskProvider as import("../types/index.js").LLMProvider,
        taskModel ?? undefined,
        revisionPrompt ?? undefined,
        rlmConfig,
      ),
    createLoop: (loopOpts) =>
      new ImprovementLoop(
        loopOpts as import("../execution/improvement-loop.js").ImprovementLoopOpts,
      ),
    now: () => performance.now(),
  });

  const rendered = renderImproveResult(result, plan.verbose);
  for (const line of rendered.stderrLines) {
    console.error(line);
  }
  console.log(rendered.stdout);
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

  const {
    buildReplSessionRequest,
    getReplUsageExitCode,
    planReplCommand,
    REPL_HELP_TEXT,
  } = await import("./repl-command-workflow.js");

  if (values.help || (!values.scenario && (!values.prompt || !values.rubric))) {
    console.log(REPL_HELP_TEXT);
    process.exit(getReplUsageExitCode(!!values.help));
  }

  const { provider, model } = await getProvider();
  const { runAgentTaskRlmSession } = await import("../rlm/agent-task.js");
  const savedScenario = values.scenario
    ? await loadSavedAgentTaskScenario(values.scenario)
    : null;
  if (values.scenario && !savedScenario) {
    console.error(`Unknown saved custom scenario: ${values.scenario}`);
    process.exit(1);
  }
  let plan;
  try {
    plan = planReplCommand(values, savedScenario);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const result = await runAgentTaskRlmSession(
    buildReplSessionRequest({
      provider,
      model,
      plan,
    }),
  );

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

  const {
    getQueueUsageExitCode,
    planQueueCommand,
    QUEUE_HELP_TEXT,
    renderQueuedTaskResult,
  } = await import("./queue-status-command-workflow.js");

  if (values.help || !values.spec) {
    console.log(QUEUE_HELP_TEXT);
    process.exit(getQueueUsageExitCode(!!values.help));
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { enqueueTask } = await import("../execution/task-runner.js");
  const savedScenario = await loadSavedAgentTaskScenario(values.spec);

  const store = new SQLiteStore(dbPath);
  const migrationsDir = getMigrationsDir();
  store.migrate(migrationsDir);

  const plan = planQueueCommand(values, savedScenario);
  const id = enqueueTask(store, plan.specName, plan.request);

  console.log(renderQueuedTaskResult({ taskId: id, specName: plan.specName }));
  store.close();
}

async function cmdStatus(dbPath: string): Promise<void> {
  const { executeStatusCommandWorkflow, renderStatusResult } =
    await import("./queue-status-command-workflow.js");
  const { SQLiteStore } = await import("../storage/index.js");
  const store = new SQLiteStore(dbPath);

  console.log(
    renderStatusResult(
      executeStatusCommandWorkflow({
        store,
        migrationsDir: getMigrationsDir(),
      }),
    ),
  );
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

  const {
    planServeCommand,
    renderServeStartup,
    SERVE_HELP_TEXT,
  } = await import("./serve-command-workflow.js");

  if (values.help) {
    console.log(SERVE_HELP_TEXT);
    process.exit(0);
  }

  const plan = planServeCommand(values);

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
  const server = new InteractiveServer({
    runManager: mgr,
    port: plan.port,
    host: plan.host,
  });
  await server.start();

  const startupInfo = {
    url: `http://${plan.host}:${server.port}`,
    apiUrl: `http://${plan.host}:${server.port}/api/runs`,
    wsUrl: `ws://${plan.host}:${server.port}/ws/interactive`,
    host: plan.host,
    port: server.port,
    scenarios: mgr.listScenarios(),
  };

  for (const line of renderServeStartup(startupInfo, plan.json)) {
    console.log(line);
  }

  await new Promise<void>((res) => {
    const cleanup = () => {
      process.off("SIGINT", cleanup);
      process.off("SIGTERM", cleanup);
      res();
    };
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

  const { buildMcpServeRequest, MCP_SERVE_HELP_TEXT } =
    await import("./mcp-serve-command-workflow.js");

  if (values.help) {
    console.log(MCP_SERVE_HELP_TEXT);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { startServer } = await import("../mcp/server.js");
  const { loadSettings } = await import("../config/index.js");

  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());

  const { provider, model } = await getProvider();
  const settings = loadSettings();

  await startServer(
    buildMcpServeRequest({
      store,
      provider,
      model,
      dbPath,
      runsRoot: resolve(settings.runsRoot),
      knowledgeRoot: resolve(settings.knowledgeRoot),
    }),
  );
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

  const {
    executeListCommandWorkflow,
    LIST_HELP_TEXT,
    planListCommand,
  } = await import("./list-command-workflow.js");

  if (values.help) {
    console.log(LIST_HELP_TEXT);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());

  try {
    const plan = planListCommand(values);
    console.log(
      executeListCommandWorkflow({
        plan,
        listRuns: (limit, scenario) => store.listRuns(limit, scenario),
      }),
    );
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

  const {
    executeReplayCommandWorkflow,
    planReplayCommand,
    REPLAY_HELP_TEXT,
  } = await import("./replay-command-workflow.js");

  if (values.help) {
    console.log(REPLAY_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planReplayCommand(values);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { existsSync, readdirSync, readFileSync } = await import("node:fs");
  const { loadSettings } = await import("../config/index.js");

  const settings = loadSettings();
  try {
    const replay = executeReplayCommandWorkflow({
      runId: plan.runId,
      generation: plan.generation,
      runsRoot: settings.runsRoot,
      existsSync,
      readdirSync,
      readFileSync: (path, encoding) => readFileSync(path, encoding),
    });
    console.error(replay.stderr);
    console.log(replay.stdout);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }
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

  const {
    BENCHMARK_HELP_TEXT,
    executeBenchmarkCommandWorkflow,
    planBenchmarkCommand,
    renderBenchmarkResult,
  } = await import("./benchmark-command-workflow.js");

  if (values.help) {
    console.log(BENCHMARK_HELP_TEXT);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../storage/index.js");
  const { GenerationRunner } = await import("../loop/generation-runner.js");
  const { SCENARIO_REGISTRY } = await import("../scenarios/registry.js");
  const { assertFamilyContract } =
    await import("../scenarios/family-interfaces.js");
  const { loadSettings } = await import("../config/index.js");
  const { buildRoleProviderBundle } = await import("../providers/index.js");
  const { resolveRunnableScenarioClass } = await import("./runnable-scenario-resolution.js");

  const plan = await planBenchmarkCommand(values, resolveScenarioOption);

  const settings = loadSettings();
  let ScenarioClass;
  try {
    ScenarioClass = resolveRunnableScenarioClass({
      scenarioName: plan.scenarioName,
      builtinScenarios: SCENARIO_REGISTRY,
      knowledgeRoot: resolve(settings.knowledgeRoot),
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }
  const providerBundle = buildRoleProviderBundle(
    settings,
    plan.providerType ? { providerType: plan.providerType } : {},
  );
  const result = await executeBenchmarkCommandWorkflow({
    dbPath,
    migrationsDir: getMigrationsDir(),
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
    plan,
    providerBundle,
    ScenarioClass,
    assertFamilyContract,
    createStore: (benchmarkDbPath) => new SQLiteStore(benchmarkDbPath),
    createRunner: (runnerOpts) => new GenerationRunner(runnerOpts),
  });
  const rendered = renderBenchmarkResult(result, plan.json);
  if (rendered.stderr) {
    console.error(rendered.stderr);
  }
  console.log(rendered.stdout);
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

  const {
    executeExportCommandWorkflow,
    EXPORT_HELP_TEXT,
    planExportCommand,
  } = await import("./export-command-workflow.js");

  if (values.help) {
    console.log(EXPORT_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = await planExportCommand(values, resolveScenarioOption);
  } catch (error) {
    console.error(errorMessage(error));
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
    const { writeFileSync, mkdirSync } = await import("node:fs");
    console.log(
      executeExportCommandWorkflow({
        scenarioName: plan.scenarioName,
        output: plan.output,
        json: plan.json,
        exportStrategyPackage,
        artifacts,
        store,
        writeOutputFile: (path, content) => {
          mkdirSync(dirname(path), { recursive: true });
          writeFileSync(path, content, "utf-8");
        },
      }),
    );
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

  const {
    executeExportTrainingDataCommandWorkflow,
    EXPORT_TRAINING_DATA_HELP_TEXT,
    planExportTrainingDataCommand,
  } = await import("./export-training-data-command-workflow.js");

  if (values.help) {
    console.log(EXPORT_TRAINING_DATA_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planExportTrainingDataCommand(values);
  } catch (error) {
    console.error(errorMessage(error));
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
    const { writeFileSync, mkdirSync } = await import("node:fs");
    const result = executeExportTrainingDataCommandWorkflow({
      plan,
      store,
      artifacts,
      exportTrainingData,
      writeOutputFile: (path, content) => {
        mkdirSync(dirname(path), { recursive: true });
        writeFileSync(path, content, "utf-8");
      },
    });
    for (const line of result.stderrLines) {
      console.error(line);
    }
    console.log(result.stdout);
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

  const {
    executeImportPackageCommandWorkflow,
    IMPORT_PACKAGE_HELP_TEXT,
    planImportPackageCommand,
  } = await import("./import-package-command-workflow.js");

  if (values.help) {
    console.log(IMPORT_PACKAGE_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planImportPackageCommand(values);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { readFileSync } = await import("node:fs");
  const { loadSettings } = await import("../config/index.js");
  const { ArtifactStore } = await import("../knowledge/artifact-store.js");
  const { importStrategyPackage } = await import("../knowledge/package.js");

  const settings = loadSettings();
  const raw = readFileSync(plan.file, "utf-8");
  const artifacts = new ArtifactStore({
    runsRoot: resolve(settings.runsRoot),
    knowledgeRoot: resolve(settings.knowledgeRoot),
  });
  console.log(
    executeImportPackageCommandWorkflow({
      rawPackage: raw,
      artifacts,
      skillsRoot: resolve(settings.skillsRoot),
      scenarioOverride: plan.scenarioOverride,
      conflictPolicy: plan.conflictPolicy,
      importStrategyPackage,
    }),
  );
}

async function cmdNewScenario(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      list: { type: "boolean" },
      template: { type: "string" },
      name: { type: "string" },
      description: { type: "string", short: "d" },
      "from-spec": { type: "string" },
      "from-stdin": { type: "boolean" },
      "prompt-only": { type: "boolean" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    NEW_SCENARIO_HELP_TEXT,
    ensureNewScenarioDescription,
    executeCreatedScenarioMaterialization,
    executeImportedScenarioMaterialization,
    executeTemplateScaffoldWorkflow,
    renderTemplateList,
  } = await import("./new-scenario-command-workflow.js");

  if (values.help) {
    console.log(NEW_SCENARIO_HELP_TEXT);
    process.exit(0);
  }

  const {
    createScenarioFromDescription,
    buildScenarioCreationPrompt,
    detectScenarioFamily,
    isScenarioFamilyName,
  } = await import("../scenarios/scenario-creator.js");
  const { TemplateLoader } = await import("../scenarios/templates/index.js");
  const { SCENARIO_TYPE_MARKERS } = await import("../scenarios/families.js");
  const { loadSettings } = await import("../config/index.js");
  const validFamilies = Object.keys(SCENARIO_TYPE_MARKERS).sort();

  // Mode 0: --list
  if (values.list) {
    const loader = new TemplateLoader();
    const templates = loader.listTemplates();
    console.log(renderTemplateList({ templates, json: !!values.json }));
    return;
  }

  // Mode 0b: --template <name> --name <scenario>
  if (values.template || values.name) {
    const loader = new TemplateLoader();
    const settings = loadSettings();
    try {
      console.log(
        executeTemplateScaffoldWorkflow({
          template: values.template,
          name: values.name,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          json: !!values.json,
          templateLoader: loader,
        }),
      );
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    return;
  }

  // Mode 1: --from-spec <file>
  if (values["from-spec"]) {
    const { readFileSync } = await import("node:fs");
    const { materializeScenario } = await import("../scenarios/materialize.js");
    let spec: Record<string, unknown>;
    try {
      spec = JSON.parse(readFileSync(values["from-spec"], "utf-8"));
    } catch (err) {
      console.error(`Error reading spec file: ${errorMessage(err)}`);
      process.exit(1);
    }
    const settings = loadSettings();
    try {
      console.log(
        await executeImportedScenarioMaterialization({
          spec,
          detectScenarioFamily,
          isScenarioFamilyName,
          validFamilies,
          materializeScenario,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          json: !!values.json,
        }),
      );
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    return;
  }

  // Mode 2: --from-stdin
  if (values["from-stdin"]) {
    const { materializeScenario } = await import("../scenarios/materialize.js");
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
    const settings = loadSettings();
    try {
      console.log(
        await executeImportedScenarioMaterialization({
          spec,
          detectScenarioFamily,
          isScenarioFamilyName,
          validFamilies,
          materializeScenario,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          json: !!values.json,
        }),
      );
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    return;
  }

  // Mode 3: --prompt-only (output the prompt, no LLM call)
  if (values["prompt-only"]) {
    let description: string;
    try {
      description = ensureNewScenarioDescription({
        description: values.description,
        errorMessage: "Error: --description is required with --prompt-only",
      });
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    const prompt = buildScenarioCreationPrompt(description);
    console.log(prompt);
    return;
  }

  // Default: --description mode (requires LLM)
  let description: string;
  try {
    description = ensureNewScenarioDescription({
      description: values.description,
      errorMessage:
        "Error: --list, --template, --description, --from-spec, --from-stdin, or --prompt-only is required",
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  let provider;
  try {
    const result = await getProvider();
    provider = result.provider;
  } catch {
    const { DeterministicProvider } =
      await import("../providers/deterministic.js");
    provider = new DeterministicProvider();
  }

  const result = await createScenarioFromDescription(description, provider);

  // Materialize the created scenario to disk (AC-433)
  const { materializeScenario } = await import("../scenarios/materialize.js");
  const settings = loadSettings();
  try {
    console.log(
      await executeCreatedScenarioMaterialization({
        created: result,
        materializeScenario,
        knowledgeRoot: resolve(settings.knowledgeRoot),
        json: !!values.json,
      }),
    );
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
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

  const { buildInitSuccessMessages, INIT_HELP_TEXT, planInitCommand } =
    await import("./init-command-workflow.js");

  if (values.help) {
    console.log(INIT_HELP_TEXT);
    process.exit(0);
  }

  const { existsSync, mkdirSync, writeFileSync } = await import("node:fs");
  const { loadPersistedCredentials, loadProjectConfig } =
    await import("../config/index.js");
  const { resolveProviderConfig } = await import("../providers/index.js");

  let plan;
  try {
    const targetDir = resolve(values.dir ?? ".");
    plan = planInitCommand(values, {
      resolvePath: resolve,
      joinPath: join,
      configExists: existsSync(join(targetDir, ".autoctx.json")),
      projectDefaults: loadProjectConfig(targetDir),
      persistedCredentials: loadPersistedCredentials(),
      env: process.env,
      resolveProviderConfig,
      parsePositiveInteger,
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  mkdirSync(plan.targetDir, { recursive: true });
  mkdirSync(join(plan.targetDir, "runs"), { recursive: true });
  mkdirSync(join(plan.targetDir, "knowledge"), { recursive: true });
  writeFileSync(
    plan.configPath,
    JSON.stringify(plan.config, null, 2) + "\n",
    "utf-8",
  );

  const agentsMdUpdated = await writeAgentsGuide(plan.targetDir);

  for (const line of buildInitSuccessMessages({
    configPath: plan.configPath,
    agentsPath: join(plan.targetDir, "AGENTS.md"),
    agentsMdUpdated,
  })) {
    console.log(line);
  }
}

async function cmdCapabilities(): Promise<void> {
  const { buildCapabilitiesPayload } =
    await import("./capabilities-command-workflow.js");
  const { getCapabilities } = await import("../mcp/capabilities.js");
  const projectConfig = await buildProjectConfigSummary();
  const baseCapabilities = getCapabilities();

  console.log(
    JSON.stringify(
      buildCapabilitiesPayload(baseCapabilities, projectConfig),
      null,
      2,
    ),
  );
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

  const {
    buildLoginSuccessMessage,
    buildStoredProviderCredentials,
    LOGIN_HELP_TEXT,
    resolveLoginCommandRequest,
  } = await import("./auth-provider-command-workflow.js");

  if (values.help) {
    console.log(LOGIN_HELP_TEXT);
    process.exit(0);
  }

  const { resolveConfigDir } = await import("../config/index.js");
  let request;
  try {
    request = await resolveLoginCommandRequest(values, {
      promptForValue,
      normalizeOllamaBaseUrl,
      validateOllamaConnection,
      env: process.env,
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  // Validate API key format before saving (AC-430)
  if (request.apiKey) {
    const { validateApiKey, resolveApiKeyValue } =
      await import("../config/credentials.js");
    // Resolve shell-command escape hatch (e.g. "!security find-generic-password -ws 'anthropic'")
    const resolvedKey = resolveApiKeyValue(request.apiKey);
    const validation = await validateApiKey(request.provider, resolvedKey);
    if (!validation.valid) {
      console.error(`Warning: ${validation.error}`);
    }
  }

  // Save to multi-provider credential store with 0600 permissions (AC-430)
  const { saveProviderCredentials } = await import("../config/credentials.js");
  const configDir = resolveConfigDir(request.configDir);
  saveProviderCredentials(
    configDir,
    request.provider,
    buildStoredProviderCredentials(request),
  );

  console.log(buildLoginSuccessMessage(request));
}

async function cmdWhoami(): Promise<void> {
  const { buildWhoamiPayload } =
    await import("./auth-provider-command-workflow.js");
  const { loadPersistedCredentials, loadProjectConfig } =
    await import("../config/index.js");
  const { resolveProviderConfig } = await import("../providers/index.js");
  const { resolveConfigDir } = await import("../config/index.js");

  const projectConfig = loadProjectConfig();
  const configDir = resolveConfigDir();
  const defaultPersistedCredentials = loadPersistedCredentials(configDir);
  let resolvedConfig: {
    providerType: string;
    apiKey?: string;
    model?: string;
    baseUrl?: string;
  } | null = null;

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
  const authenticated =
    provider === "ollama" ||
    Boolean(
      resolvedConfig?.apiKey ??
      process.env.ANTHROPIC_API_KEY ??
      process.env.OPENAI_API_KEY ??
      persistedCredentials?.apiKey,
    );

  // Also list all configured providers (AC-430)
  const { listConfiguredProviders } = await import("../config/credentials.js");
  const configuredProviders = listConfiguredProviders(configDir);

  console.log(
    JSON.stringify(
      buildWhoamiPayload({
        provider,
        model,
        authenticated,
        baseUrl,
        configuredProviders,
      }),
      null,
      2,
    ),
  );
}

async function cmdLogout(): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      "config-dir": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { buildLogoutMessage, LOGOUT_HELP_TEXT } =
    await import("./auth-provider-command-workflow.js");

  if (values.help) {
    console.log(LOGOUT_HELP_TEXT);
    process.exit(0);
  }

  const { existsSync, unlinkSync } = await import("node:fs");
  const { loadPersistedCredentials, resolveConfigDir } =
    await import("../config/index.js");
  const configDir = resolveConfigDir(values["config-dir"]);
  const credentialsPath = join(configDir, "credentials.json");
  const existing = loadPersistedCredentials(configDir);

  if (!existsSync(credentialsPath)) {
    console.log("No stored credentials found.");
    return;
  }

  unlinkSync(credentialsPath);
  console.log(buildLogoutMessage(existing?.provider));
}

async function cmdProviders(): Promise<void> {
  const { buildProvidersPayload } =
    await import("./auth-provider-command-workflow.js");
  const { KNOWN_PROVIDERS, discoverAllProviders } =
    await import("../config/credentials.js");
  const { resolveConfigDir } = await import("../config/index.js");
  const configDir = resolveConfigDir();
  const discovered = discoverAllProviders(configDir);

  console.log(
    JSON.stringify(buildProvidersPayload(KNOWN_PROVIDERS, discovered), null, 2),
  );
}

async function cmdModels(): Promise<void> {
  const { renderModelsResult } =
    await import("./auth-provider-command-workflow.js");
  const { listAuthenticatedModels } = await import("../config/credentials.js");
  const { resolveConfigDir } = await import("../config/index.js");
  const configDir = resolveConfigDir();
  const models = listAuthenticatedModels(configDir);

  for (const line of renderModelsResult(models)) {
    console.log(line);
  }
}

// ---------------------------------------------------------------------------
// Mission CLI (AC-413)
// ---------------------------------------------------------------------------

async function cmdMission(dbPath: string): Promise<void> {
  const subcommand = process.argv[3];
  const { MissionManager } = await import("../mission/manager.js");
  const { createCodeMission } = await import("../mission/verifiers.js");
  const {
    buildMissionArtifactsPayload,
    buildMissionStatusPayload,
    requireMission,
    runMissionLoop,
    writeMissionCheckpoint,
  } = await import("../mission/control-plane.js");
  const {
    getMissionIdOrThrow,
    MISSION_HELP_TEXT,
    planMissionCreate,
    planMissionList,
    planMissionRun,
  } = await import("./mission-command-workflow.js");
  const {
    executeMissionArtifactsCommand,
    executeMissionCreateCommand,
    executeMissionLifecycleCommand,
    executeMissionListCommand,
    executeMissionRunCommand,
    executeMissionStatusCommand,
  } = await import("./mission-command-execution.js");
  const { loadSettings } = await import("../config/index.js");
  const settings = loadSettings();
  const runsRoot = resolve(settings.runsRoot);

  if (!subcommand || subcommand === "--help" || subcommand === "-h") {
    console.log(MISSION_HELP_TEXT);
    process.exit(0);
  }

  const manager = new MissionManager(dbPath);
  try {
    switch (subcommand) {
      case "create": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: {
            type: { type: "string" },
            name: { type: "string" },
            goal: { type: "string" },
            "max-steps": { type: "string" },
            "repo-path": { type: "string" },
            "test-command": { type: "string" },
            "lint-command": { type: "string" },
            "build-command": { type: "string" },
          },
        });
        let plan;
        try {
          plan = planMissionCreate(values, resolve);
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }

        console.log(
          JSON.stringify(
            executeMissionCreateCommand({
              manager,
              createCodeMission,
              buildMissionStatusPayload,
              writeMissionCheckpoint,
              runsRoot,
              plan,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "run": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: {
            id: { type: "string" },
            "max-iterations": { type: "string", default: "1" },
            "step-description": { type: "string" },
          },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission run --id <mission-id> [--max-iterations N] [--step-description <text>]",
        );
        const mission = requireMission(manager, missionId);
        const plan = planMissionRun(values, mission);
        const payload = await executeMissionRunCommand({
          manager,
          plan,
          runsRoot,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          createAdaptiveProvider: async () => {
            if (!plan.needsAdaptivePlanning) {
              return undefined;
            }
            const { createProvider, resolveProviderConfig } =
              await import("../providers/index.js");
            return createProvider(resolveProviderConfig());
          },
          runMissionLoop,
        });
        console.log(JSON.stringify(payload, null, 2));
        break;
      }
      case "status": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission status --id <mission-id>",
        );
        console.log(
          JSON.stringify(
            executeMissionStatusCommand({
              manager,
              missionId,
              buildMissionStatusPayload,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "list": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { status: { type: "string" } },
        });
        type MissionStatusParam = Parameters<typeof manager.list>[0];
        const plan = planMissionList(values);
        console.log(
          JSON.stringify(
            executeMissionListCommand({
              listMissions: (status) => manager.list(status as MissionStatusParam),
              status: plan.status as MissionStatusParam,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "artifacts": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission artifacts --id <mission-id>",
        );
        console.log(
          JSON.stringify(
            executeMissionArtifactsCommand({
              manager,
              missionId,
              runsRoot,
              buildMissionArtifactsPayload,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "pause": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission pause --id <mission-id>",
        );
        requireMission(manager, missionId);
        console.log(
          JSON.stringify(
            executeMissionLifecycleCommand({
              action: "pause",
              missionId,
              manager,
              buildMissionStatusPayload,
              writeMissionCheckpoint,
              runsRoot,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "resume": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission resume --id <mission-id>",
        );
        requireMission(manager, missionId);
        console.log(
          JSON.stringify(
            executeMissionLifecycleCommand({
              action: "resume",
              missionId,
              manager,
              buildMissionStatusPayload,
              writeMissionCheckpoint,
              runsRoot,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "cancel": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        const missionId = getMissionIdOrThrow(
          values,
          "Usage: autoctx mission cancel --id <mission-id>",
        );
        requireMission(manager, missionId);
        console.log(
          JSON.stringify(
            executeMissionLifecycleCommand({
              action: "cancel",
              missionId,
              manager,
              buildMissionStatusPayload,
              writeMissionCheckpoint,
              runsRoot,
            }),
            null,
            2,
          ),
        );
        break;
      }
      default:
        console.error(
          `Unknown mission subcommand: ${subcommand}. Run 'autoctx mission --help'.`,
        );
        process.exit(1);
    }
  } finally {
    manager.close();
  }
}

// ---------------------------------------------------------------------------
// campaign command (AC-533)
// ---------------------------------------------------------------------------

async function cmdCampaign(dbPath: string): Promise<void> {
  const subcommand = process.argv[3];
  const { MissionManager } = await import("../mission/manager.js");
  const { CampaignManager } = await import("../mission/campaign.js");
  const {
    CAMPAIGN_HELP_TEXT,
    getCampaignIdOrThrow,
    parseCampaignStatus,
    planCampaignAddMission,
    planCampaignCreate,
  } = await import("./campaign-command-workflow.js");
  const {
    executeCampaignAddMissionCommand,
    executeCampaignCreateCommand,
    executeCampaignLifecycleCommand,
    executeCampaignListCommand,
    executeCampaignProgressCommand,
    executeCampaignStatusCommand,
  } = await import("./campaign-command-execution.js");

  if (!subcommand || subcommand === "--help" || subcommand === "-h") {
    console.log(CAMPAIGN_HELP_TEXT);
    process.exit(0);
  }

  const missionManager = new MissionManager(dbPath);
  const manager = new CampaignManager(missionManager);

  function requireCampaign(id: string) {
    const campaign = manager.get(id);
    if (!campaign) {
      console.error(`Campaign not found: ${id}`);
      process.exit(1);
    }
    return campaign;
  }

  function parseCampaignPositiveInteger(
    raw: string | undefined,
    label: string,
  ): number {
    try {
      return parsePositiveInteger(raw, label);
    } catch (error) {
      console.error(formatFatalCliError(error));
      process.exit(1);
    }
  }

  try {
    switch (subcommand) {
      case "create": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: {
            name: { type: "string" },
            goal: { type: "string" },
            "max-missions": { type: "string" },
            "max-steps": { type: "string" },
          },
        });
        let plan;
        try {
          plan = planCampaignCreate(values, parseCampaignPositiveInteger);
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        console.log(
          JSON.stringify(
            executeCampaignCreateCommand({
              manager,
              plan,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "status": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        let id: string;
        try {
          id = getCampaignIdOrThrow(
            values,
            "Usage: autoctx campaign status --id <campaign-id>",
          );
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        requireCampaign(id);
        console.log(
          JSON.stringify(
            executeCampaignStatusCommand({
              campaignId: id,
              getCampaign: requireCampaign,
              getProgress: (campaignId) => manager.progress(campaignId),
              getMissions: (campaignId) => manager.missions(campaignId),
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "list": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { status: { type: "string" } },
        });
        let status: CampaignStatus | undefined;
        try {
          status = parseCampaignStatus(values.status);
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        console.log(
          JSON.stringify(
            executeCampaignListCommand({
              listCampaigns: (campaignStatus) => manager.list(campaignStatus),
              status,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "add-mission": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: {
            id: { type: "string" },
            "mission-id": { type: "string" },
            priority: { type: "string" },
            "depends-on": { type: "string" },
          },
        });
        let plan;
        try {
          plan = planCampaignAddMission(values, parseCampaignPositiveInteger);
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        requireCampaign(plan.campaignId);
        console.log(
          JSON.stringify(
            executeCampaignAddMissionCommand({
              addMission: (campaignId, missionId, options) =>
                manager.addMission(campaignId, missionId, options),
              plan,
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "progress": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        let id: string;
        try {
          id = getCampaignIdOrThrow(
            values,
            "Usage: autoctx campaign progress --id <campaign-id>",
          );
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        requireCampaign(id);
        console.log(
          JSON.stringify(
            executeCampaignProgressCommand({
              campaignId: id,
              getProgress: (campaignId) => manager.progress(campaignId),
              getBudgetUsage: (campaignId) => manager.budgetUsage(campaignId),
            }),
            null,
            2,
          ),
        );
        break;
      }
      case "pause":
      case "resume":
      case "cancel": {
        const { values } = parseArgs({
          args: process.argv.slice(4),
          options: { id: { type: "string" } },
        });
        let id: string;
        try {
          id = getCampaignIdOrThrow(
            values,
            `Usage: autoctx campaign ${subcommand} --id <campaign-id>`,
          );
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        requireCampaign(id);
        try {
          console.log(
            JSON.stringify(
              executeCampaignLifecycleCommand({
                action: subcommand,
                campaignId: id,
                manager: {
                  get: requireCampaign,
                  pause: (campaignId) => manager.pause(campaignId),
                  resume: (campaignId) => manager.resume(campaignId),
                  cancel: (campaignId) => manager.cancel(campaignId),
                },
              }),
              null,
              2,
            ),
          );
        } catch (error) {
          console.error(errorMessage(error));
          process.exit(1);
        }
        break;
      }
      default:
        console.error(
          `Unknown campaign subcommand: ${subcommand}. Run 'autoctx campaign --help'.`,
        );
        process.exit(1);
    }
  } finally {
    manager.close();
    missionManager.close();
  }
}

// ---------------------------------------------------------------------------
// simulate command (AC-446)
// ---------------------------------------------------------------------------

async function cmdSimulate(): Promise<void> {
  const { parseArgs } = await import("node:util");
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      description: { type: "string", short: "d" },
      replay: { type: "string" },
      "compare-left": { type: "string" },
      "compare-right": { type: "string" },
      export: { type: "string" },
      format: { type: "string" },
      "sweep-file": { type: "string" },
      preset: { type: "string" },
      "preset-file": { type: "string" },
      variables: { type: "string" },
      sweep: { type: "string" },
      runs: { type: "string" },
      "max-steps": { type: "string" },
      "save-as": { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    executeSimulateCompareWorkflow,
    executeSimulateExportWorkflow,
    executeSimulateReplayWorkflow,
    executeSimulateRunWorkflow,
    SIMULATE_HELP_TEXT,
    planSimulateCommand,
    planSimulateInputs,
    renderCompareSuccess,
    renderReplaySuccess,
    renderSimulationSuccess,
  } = await import("./simulate-command-workflow.js");

  if (values.help) {
    console.log(SIMULATE_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planSimulateCommand(values);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { SimulationEngine, parseVariableOverrides, parseSweepSpec } =
    await import("../simulation/engine.js");
  const { loadSettings } = await import("../config/index.js");
  const { resolve } = await import("node:path");
  const settings = loadSettings();

  // Export mode (AC-452)
  if (plan.mode === "export") {
    const { exportSimulation } = await import("../simulation/export.js");
    try {
      console.log(
        executeSimulateExportWorkflow({
          exportId: plan.exportId!,
          format: values.format,
          knowledgeRoot: resolve(settings.knowledgeRoot),
          json: !!values.json,
          exportSimulation,
        }),
      );
    } catch (error) {
      console.error(errorMessage(error));
      process.exit(1);
    }
    return;
  }

  // Compare mode (AC-451)
  if (plan.mode === "compare") {
    const result = await executeSimulateCompareWorkflow({
      compareLeft: plan.compareLeft!,
      compareRight: plan.compareRight!,
      knowledgeRoot: resolve(settings.knowledgeRoot),
      createEngine: (provider, knowledgeRoot) =>
        new SimulationEngine(
          provider as unknown as import("../types/index.js").LLMProvider,
          knowledgeRoot,
        ),
    });
    emitEngineResult(result, {
      json: !!values.json,
      label: "Compare",
      renderSuccess: (r) => {
        console.log(renderCompareSuccess(r));
      },
    });
    return;
  }

  // Replay mode (AC-450)
  if (plan.mode === "replay") {
    const result = await executeSimulateReplayWorkflow({
      replayId: plan.replayId!,
      knowledgeRoot: resolve(settings.knowledgeRoot),
      variables: values.variables,
      maxSteps: values["max-steps"],
      createEngine: (provider, knowledgeRoot) =>
        new SimulationEngine(
          provider as unknown as import("../types/index.js").LLMProvider,
          knowledgeRoot,
        ),
      parseVariableOverrides,
    });
    emitEngineResult(result, {
      json: !!values.json,
      label: "Replay",
      renderSuccess: (r) => {
        console.log(renderReplaySuccess(r));
      },
    });
    return;
  }

  // Build sweep from --sweep or --sweep-file, and variables from --variables/--preset (AC-454)
  const { loadSweepFile, parsePreset } = await import("../simulation/sweep-dsl.js");
  const { readFileSync: readFile } = await import("node:fs");

  let sweep;
  let variables;
  try {
    const inputPlan = await planSimulateInputs({
      values,
      parseSweepSpec,
      loadSweepFile,
      parseVariableOverrides,
      readPresetFile: (path) => readFile(path, "utf-8"),
      parsePreset,
    });
    sweep = inputPlan.sweep;
    variables = inputPlan.variables;
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { provider } = await getProvider();

  const result = await executeSimulateRunWorkflow({
    description: plan.description!,
    provider,
    knowledgeRoot: resolve(settings.knowledgeRoot),
    variables,
    sweep,
    runs: values.runs,
    maxSteps: values["max-steps"],
    saveAs: values["save-as"],
    createEngine: (runProvider, knowledgeRoot) =>
      new SimulationEngine(runProvider, knowledgeRoot),
  });

  emitEngineResult(result, {
    json: !!values.json,
    label: "Simulation",
    renderSuccess: (r) => {
      console.log(renderSimulationSuccess(r));
    },
  });
}

// ---------------------------------------------------------------------------
// investigate command (AC-447)
// ---------------------------------------------------------------------------

async function cmdInvestigate(): Promise<void> {
  const { parseArgs } = await import("node:util");
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      description: { type: "string", short: "d" },
      "max-steps": { type: "string" },
      hypotheses: { type: "string" },
      "save-as": { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    INVESTIGATE_HELP_TEXT,
    executeInvestigateCommandWorkflow,
    renderInvestigationSuccess,
  } = await import("./investigate-command-workflow.js");

  if (values.help) {
    console.log(INVESTIGATE_HELP_TEXT);
    process.exit(0);
  }

  const { InvestigationEngine } = await import("../investigation/engine.js");
  const { loadSettings } = await import("../config/index.js");
  const { resolve } = await import("node:path");

  const { provider } = await getProvider();

  const settings = loadSettings();
  const engine = new InvestigationEngine(
    provider,
    resolve(settings.knowledgeRoot),
  );

  let result;
  try {
    result = await executeInvestigateCommandWorkflow({ values, engine });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  emitEngineResult(result, {
    json: !!values.json,
    label: "Investigation",
    renderSuccess: (r) => {
      console.log(renderInvestigationSuccess(r));
    },
  });
}

// ---------------------------------------------------------------------------
// analyze command (AC-448)
// ---------------------------------------------------------------------------

async function cmdAnalyze(): Promise<void> {
  const { parseArgs } = await import("node:util");
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      id: { type: "string" },
      type: { type: "string" },
      left: { type: "string" },
      right: { type: "string" },
      focus: { type: "string" },
      "save-report": { type: "boolean" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  if (values.help) {
    console.log(`autoctx analyze — analyze and compare artifacts

Usage:
  autoctx analyze --id <artifact-id> --type <run|simulation|investigation|mission>
  autoctx analyze --left <id> --right <id> --type <type>

Options:
  --id <id>            Artifact to analyze (single mode)
  --left <id>          Left artifact for comparison
  --right <id>         Right artifact for comparison
  --type <type>        Artifact type: run, simulation, investigation, mission
  --focus <area>       Focus area: regression, sensitivity, attribution
  --json               Output as JSON
  -h, --help           Show this help

Examples:
  autoctx analyze --id deploy_sim --type simulation --json
  autoctx analyze --left sim_a --right sim_b --type simulation
  autoctx analyze --id checkout_rca --type investigation`);
    process.exit(0);
  }

  const { AnalysisEngine } = await import("../analysis/engine.js");
  const { loadSettings } = await import("../config/index.js");
  const { resolve } = await import("node:path");

  const settings = loadSettings();
  const engine = new AnalysisEngine({
    knowledgeRoot: resolve(settings.knowledgeRoot),
    runsRoot: resolve(settings.runsRoot),
    dbPath: resolve(settings.dbPath),
  });
  const type = (values.type ?? "simulation") as
    | "run"
    | "simulation"
    | "investigation"
    | "mission";

  let result;
  if (values.left && values.right) {
    result = engine.compare({
      left: { id: values.left, type },
      right: { id: values.right, type },
      focus: values.focus,
    });
  } else if (values.id) {
    result = engine.analyze({ id: values.id, type, focus: values.focus });
  } else {
    console.error(
      "Error: --id or --left/--right required. Run 'autoctx analyze --help'.",
    );
    process.exit(1);
  }

  if (values.json) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.log(`Analysis: ${result.summary.headline}`);
    console.log(`Confidence: ${result.summary.confidence.toFixed(2)}`);
    if (result.findings.length > 0) {
      console.log(`\nFindings:`);
      for (const f of result.findings) {
        const icon =
          f.kind === "improvement"
            ? "↑"
            : f.kind === "regression"
              ? "↓"
              : f.kind === "conclusion"
                ? "→"
                : "•";
        console.log(`  ${icon} [${f.kind}] ${f.statement}`);
      }
    }
    if (result.regressions.length > 0) {
      console.log(`\nRegressions:`);
      for (const reg of result.regressions) console.log(`  ↓ ${reg}`);
    }
    if (result.attribution) {
      console.log(`\nAttribution:`);
      for (const f of result.attribution.topFactors)
        console.log(`  ${f.name}: ${f.weight.toFixed(2)}`);
    }
    if (result.limitations.length > 0) {
      console.log(`\nLimitations:`);
      for (const l of result.limitations) console.log(`  ⚠ ${l}`);
    }
  }
}

// ---------------------------------------------------------------------------
// train command (AC-460 audit fix)
// ---------------------------------------------------------------------------

async function cmdTrain(): Promise<void> {
  const { parseArgs } = await import("node:util");
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      scenario: { type: "string", short: "s" },
      family: { type: "string" },
      dataset: { type: "string", short: "d" },
      "held-out": { type: "string" },
      backend: { type: "string", default: "cuda" },
      mode: { type: "string", default: "from_scratch" },
      "base-model": { type: "string" },
      output: { type: "string", short: "o" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    executeTrainCommandWorkflow,
    planTrainCommand,
    renderTrainSuccess,
    TRAIN_HELP_TEXT,
  } = await import("./train-command-workflow.js");

  if (values.help) {
    console.log(TRAIN_HELP_TEXT);
    process.exit(0);
  }

  const { loadSettings } = await import("../config/index.js");
  const { resolve } = await import("node:path");
  const settings = loadSettings();

  let plan;
  try {
    plan = planTrainCommand(values, settings.runsRoot, resolve);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { TrainingRunner } = await import("../training/backends.js");
  let result;
  try {
    result = await executeTrainCommandWorkflow({
      plan,
      createRunner: () => new TrainingRunner(),
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  emitEngineResult(result, {
    json: !!values.json,
    label: "Training",
    renderSuccess: (r) => {
      console.log(renderTrainSuccess(r));
    },
  });
}

// ---------------------------------------------------------------------------
// blob command (AC-518 Phase 4)
// ---------------------------------------------------------------------------

async function cmdBlob(): Promise<void> {
  const { parseArgs } = await import("node:util");
  const { resolve } = await import("node:path");
  const { loadSettings } = await import("../config/index.js");
  const {
    BLOB_HELP_TEXT,
    executeBlobHydrateWorkflow,
    executeBlobStatusWorkflow,
    executeBlobSyncWorkflow,
    getBlobSubcommand,
  } = await import("./blob-command-workflow.js");

  const subcommandPlan = getBlobSubcommand(process.argv[3]);

  if (subcommandPlan.kind === "help") {
    console.log(BLOB_HELP_TEXT);
    process.exit(0);
  }

  const subcommand = subcommandPlan.subcommand;

  const settings = loadSettings();

  if (!settings.blobStoreEnabled) {
    console.error(
      "Error: blob store is not enabled. Set AUTOCONTEXT_BLOB_STORE_ENABLED=true",
    );
    process.exit(1);
  }

  const { createBlobStore } = await import("../blobstore/factory.js");
  const store = createBlobStore({
    backend: settings.blobStoreBackend ?? "local",
    root: resolve(settings.blobStoreRoot ?? "./blobs"),
  });

  switch (subcommand) {
    case "status": {
      const { values } = parseArgs({
        args: process.argv.slice(4),
        options: { json: { type: "boolean" } },
      });
      const { SyncManager } = await import("../blobstore/sync.js");
      console.log(
        executeBlobStatusWorkflow({
          json: !!values.json,
          createSyncManager: () => new SyncManager(store, resolve(settings.runsRoot)),
        }),
      );
      break;
    }
    case "sync": {
      const { values } = parseArgs({
        args: process.argv.slice(4),
        options: {
          "run-id": { type: "string" },
          json: { type: "boolean" },
        },
      });
      try {
        const { SyncManager } = await import("../blobstore/sync.js");
        const result = executeBlobSyncWorkflow({
          runId: values["run-id"],
          json: !!values.json,
          createSyncManager: () => new SyncManager(store, resolve(settings.runsRoot)),
        });
        if (result.stderrLines) {
          for (const line of result.stderrLines) console.error(line);
        }
        console.log(result.stdout);
      } catch (error) {
        console.error(errorMessage(error));
        process.exit(1);
      }
      break;
    }
    case "hydrate": {
      const { values } = parseArgs({
        args: process.argv.slice(4),
        options: {
          key: { type: "string" },
          output: { type: "string", short: "o" },
        },
      });
      try {
        const { writeFileSync, mkdirSync } = await import("node:fs");
        const { dirname } = await import("node:path");
        const result = executeBlobHydrateWorkflow({
          key: values.key,
          output: values.output,
          store,
          writeOutputFile: (outputPath, data) => {
            mkdirSync(dirname(resolve(outputPath)), { recursive: true });
            writeFileSync(resolve(outputPath), data);
          },
        });
        if (result.stdoutBuffer) {
          process.stdout.write(result.stdoutBuffer);
        } else if (result.stdout) {
          console.log(result.stdout);
        }
      } catch (error) {
        console.error(errorMessage(error));
        process.exit(1);
      }
      break;
    }
    default:
      console.error(
        `Unknown blob subcommand: ${subcommand}. Run 'autoctx blob --help'`,
      );
      process.exit(1);
  }
}

// ---------------------------------------------------------------------------
// Control-plane commands (Layer 8 — candidate / eval / promotion / registry)
// ---------------------------------------------------------------------------

async function cmdControlPlane(topCommand: string): Promise<void> {
  const { runControlPlaneCommand } = await import(
    "../control-plane/cli/index.js"
  );
  const subArgs = process.argv.slice(3);
  const result = await runControlPlaneCommand([topCommand, ...subArgs]);
  if (result.stdout) process.stdout.write(result.stdout + "\n");
  if (result.stderr) process.stderr.write(result.stderr + "\n");
  process.exit(result.exitCode);
}

// ---------------------------------------------------------------------------
// Production-traces namespace (Foundation A / Layer 7 — AC-539)
// ---------------------------------------------------------------------------

async function cmdProductionTraces(): Promise<void> {
  const { runProductionTracesCommand } = await import(
    "../production-traces/cli/index.js"
  );
  const subArgs = process.argv.slice(3);
  const result = await runProductionTracesCommand(subArgs);
  if (result.stdout) process.stdout.write(result.stdout + "\n");
  if (result.stderr) process.stderr.write(result.stderr + "\n");
  process.exit(result.exitCode);
}

// Instrument namespace (A2-I / Layer 7 — AC-540)

async function cmdInstrument(): Promise<void> {
  const { runInstrumentCommand } = await import(
    "../control-plane/instrument/cli/index.js"
  );
  const subArgs = process.argv.slice(3);
  const result = await runInstrumentCommand(subArgs);
  if (result.stdout) process.stdout.write(result.stdout + "\n");
  if (result.stderr) process.stderr.write(result.stderr + "\n");
  process.exit(result.exitCode);
}

main().catch((err) => {
  console.error(formatFatalCliError(err));
  process.exit(1);
});
