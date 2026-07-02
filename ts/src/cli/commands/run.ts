/**
 * Run inspection command family: `run`, `list`, `runtime-sessions`, `replay`,
 * `show`, `watch`, `status`, `benchmark` (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { resolve, join } from "node:path";
import {
  errorMessage,
  getMigrationsDir,
  resolveScenarioOption,
  parsePositiveInteger,
} from "./shared.js";

export async function cmdRun(dbPath: string): Promise<void> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(3),
    allowPositionals: true,
    options: {
      scenario: { type: "string", short: "s" },
      gens: { type: "string", short: "g" },
      iterations: { type: "string" },
      "run-id": { type: "string" },
      provider: { type: "string" },
      matches: { type: "string", default: "3" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    executeAgentTaskRunCommandWorkflow,
    executeRunCommandWorkflow,
    planRunCommand,
    renderRunResult,
    RUN_HELP_TEXT,
  } = await import("../run-command-workflow.js");

  if (values.help) {
    console.log(RUN_HELP_TEXT);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../../storage/index.js");
  const { GenerationRunner } = await import("../../loop/generation-runner.js");
  const { SCENARIO_REGISTRY } = await import("../../scenarios/registry.js");
  const { assertFamilyContract } = await import("../../scenarios/family-interfaces.js");
  const { loadSettings } = await import("../../config/index.js");
  const { buildRoleProviderBundle } = await import("../../providers/index.js");
  const { initializeHookBus } = await import("../../extensions/index.js");
  const { resolveRunnableScenarioClass } = await import("../runnable-scenario-resolution.js");
  const { runtimeSessionIdForRun } = await import("../../session/runtime-session-ids.js");

  const settings = loadSettings();
  let plan;
  try {
    plan = await planRunCommand(
      { ...values, positionals },
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

  const { hookBus, loadedExtensions } = await initializeHookBus({
    extensions: settings.extensions,
    failFast: settings.extensionFailFast,
  });
  const providerBundle = buildRoleProviderBundle(
    settings,
    plan.providerType ? { providerType: plan.providerType } : {},
    {
      runtimeSession: {
        sessionId: runtimeSessionIdForRun(plan.runId),
        goal: `autoctx run ${plan.scenarioName}`,
        dbPath,
        workspaceRoot: process.cwd(),
        metadata: {
          command: "run",
          runId: plan.runId,
          scenarioName: plan.scenarioName,
        },
      },
    },
  );

  if (!SCENARIO_REGISTRY[plan.scenarioName]) {
    const { resolveCustomAgentTask } = await import("../../scenarios/custom-loader.js");
    const savedAgentTask = resolveCustomAgentTask(
      resolve(settings.knowledgeRoot),
      plan.scenarioName,
    );
    if (savedAgentTask) {
      const { executeAgentTaskSolve } =
        await import("../../knowledge/agent-task-solve-execution.js");
      const result = await executeAgentTaskRunCommandWorkflow({
        plan,
        providerBundle,
        spec: savedAgentTask.spec,
        executeAgentTaskSolve: executeAgentTaskSolve as never,
        hookBus,
        dbPath,
        migrationsDir: getMigrationsDir(),
        createStore: (runDbPath) => new SQLiteStore(runDbPath),
      });
      const rendered = renderRunResult(result, plan.json);
      if (rendered.stderr) {
        console.error(rendered.stderr);
      }
      console.log(rendered.stdout);
      return;
    }
  }

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
      new GenerationRunner({
        ...(runnerOpts as import("../../loop/generation-runner.js").GenerationRunnerOpts),
        hookBus,
        loadedExtensions,
      }),
  });

  const rendered = renderRunResult(result, plan.json);
  if (rendered.stderr) {
    console.error(rendered.stderr);
  }
  console.log(rendered.stdout);
}

export async function cmdList(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      limit: { type: "string", default: "50" },
      scenario: { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { executeListCommandWorkflow, LIST_HELP_TEXT, planListCommand } =
    await import("../list-command-workflow.js");

  if (values.help) {
    console.log(LIST_HELP_TEXT);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../../storage/index.js");
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

export async function cmdRuntimeSessions(dbPath: string): Promise<void> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(3),
    allowPositionals: true,
    options: {
      id: { type: "string" },
      "run-id": { type: "string" },
      limit: { type: "string", default: "50" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    executeRuntimeSessionsCommandWorkflow,
    planRuntimeSessionsCommand,
    RUNTIME_SESSIONS_HELP_TEXT,
  } = await import("../runtime-session-command-workflow.js");

  if (values.help) {
    console.log(RUNTIME_SESSIONS_HELP_TEXT);
    process.exit(0);
  }

  const { RuntimeSessionEventStore } = await import("../../session/runtime-events.js");
  const store = new RuntimeSessionEventStore(dbPath);
  try {
    const plan = planRuntimeSessionsCommand(values, positionals);
    console.log(executeRuntimeSessionsCommandWorkflow({ plan, store }));
  } finally {
    store.close();
  }
}

export async function cmdReplay(_dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      "run-id": { type: "string" },
      generation: { type: "string", default: "1" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { executeReplayCommandWorkflow, planReplayCommand, REPLAY_HELP_TEXT } =
    await import("../replay-command-workflow.js");

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
  const { loadSettings } = await import("../../config/index.js");

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

export async function cmdShow(dbPath: string): Promise<void> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(3),
    allowPositionals: true,
    options: {
      "run-id": { type: "string" },
      generation: { type: "string" },
      best: { type: "boolean" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { renderRunShow, resolveRunId, SHOW_HELP_TEXT } =
    await import("../run-inspection-command-workflow.js");

  if (values.help) {
    console.log(SHOW_HELP_TEXT);
    process.exit(0);
  }

  let runId;
  try {
    runId = resolveRunId(values, positionals, "show");
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { SQLiteStore } = await import("../../storage/index.js");
  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());
  try {
    const run = store.getRun(runId);
    if (!run) {
      throw new Error(`Error: run '${runId}' not found`);
    }
    const runtimeSession = await loadRuntimeSessionSummaryForRun(dbPath, runId);
    console.log(renderRunShow(run, store.getGenerations(runId), values, runtimeSession));
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  } finally {
    store.close();
  }
}

export async function cmdWatch(dbPath: string): Promise<void> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(3),
    allowPositionals: true,
    options: {
      "run-id": { type: "string" },
      interval: { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    parseWatchIntervalSeconds,
    renderRunStatus,
    renderRunStatusJsonLine,
    resolveRunId,
    WATCH_HELP_TEXT,
  } = await import("../run-inspection-command-workflow.js");

  if (values.help) {
    console.log(WATCH_HELP_TEXT);
    process.exit(0);
  }

  let runId;
  let intervalSeconds;
  try {
    runId = resolveRunId(values, positionals, "watch");
    intervalSeconds = parseWatchIntervalSeconds(values.interval);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { SQLiteStore } = await import("../../storage/index.js");
  const store = new SQLiteStore(dbPath);
  store.migrate(getMigrationsDir());
  try {
    while (true) {
      const run = store.getRun(runId);
      if (!run) {
        throw new Error(`Error: run '${runId}' not found`);
      }
      const generations = store.getGenerations(runId);
      const runtimeSession = await loadRuntimeSessionSummaryForRun(dbPath, runId);
      const progressReport = await loadProgressReportForRun(run);
      console.log(
        values.json
          ? renderRunStatusJsonLine(run, generations, runtimeSession, progressReport)
          : renderRunStatus(run, generations, false, runtimeSession, progressReport),
      );
      if (run.status !== "running") {
        return;
      }
      await new Promise((resolveSleep) => setTimeout(resolveSleep, intervalSeconds * 1000));
    }
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  } finally {
    store.close();
  }
}

async function loadRuntimeSessionSummaryForRun(dbPath: string, runId: string) {
  const { RuntimeSessionEventStore } = await import("../../session/runtime-events.js");
  const { runtimeSessionIdForRun } = await import("../../session/runtime-session-ids.js");
  const { summarizeRuntimeSession } = await import("../../session/runtime-session-read-model.js");
  const eventStore = new RuntimeSessionEventStore(dbPath);
  try {
    const log = eventStore.load(runtimeSessionIdForRun(runId));
    return log ? summarizeRuntimeSession(log) : null;
  } finally {
    eventStore.close();
  }
}

async function loadProgressReportForRun(run: { run_id: string; scenario: string }) {
  const { existsSync, readFileSync } = await import("node:fs");
  const { loadSettings } = await import("../../config/index.js");
  const { parseRunProgressReport } = await import("../../analytics/progress-report.js");
  const settings = loadSettings();
  const path = join(
    resolve(settings.knowledgeRoot),
    run.scenario,
    "progress_reports",
    `${run.run_id}.json`,
  );
  if (!existsSync(path)) return null;
  try {
    return parseRunProgressReport(JSON.parse(readFileSync(path, "utf-8")) as unknown);
  } catch {
    return null;
  }
}

export async function cmdStatus(dbPath: string): Promise<void> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(3),
    allowPositionals: true,
    options: {
      "run-id": { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const runId = values["run-id"]?.trim() || positionals[0]?.trim();
  if (values.help) {
    const { RUN_STATUS_HELP_TEXT } = await import("../run-inspection-command-workflow.js");
    console.log(RUN_STATUS_HELP_TEXT);
    process.exit(0);
  }

  // AC-697 slice 2: top-level `status` is run-status only, matching the
  // Python CLI and the slice-1 contract (`docs/cli-contract.json` pins
  // `status.domain_concept = "Run"`). The previous fall-through that
  // rendered queue-pending counts moved to `autoctx queue status`.
  if (!runId) {
    const message =
      "Error: `autoctx status` requires <run-id>. Use `autoctx queue status` for the queue-pending count.";
    if (values.json) {
      process.stdout.write(JSON.stringify({ error: message }) + "\n");
    } else {
      console.error(message);
    }
    process.exit(1);
  }

  const { SQLiteStore } = await import("../../storage/index.js");
  const store = new SQLiteStore(dbPath);
  const { renderRunStatus } = await import("../run-inspection-command-workflow.js");
  store.migrate(getMigrationsDir());
  const run = store.getRun(runId);
  if (!run) {
    console.error(`Error: run '${runId}' not found`);
    store.close();
    process.exit(1);
  }
  const runtimeSession = await loadRuntimeSessionSummaryForRun(dbPath, runId);
  const progressReport = await loadProgressReportForRun(run);
  console.log(
    renderRunStatus(
      run,
      store.getGenerations(runId),
      !!values.json,
      runtimeSession,
      progressReport,
    ),
  );
  store.close();
}

export async function cmdBenchmark(dbPath: string): Promise<void> {
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
  } = await import("../benchmark-command-workflow.js");

  if (values.help) {
    console.log(BENCHMARK_HELP_TEXT);
    process.exit(0);
  }

  const { SQLiteStore } = await import("../../storage/index.js");
  const { GenerationRunner } = await import("../../loop/generation-runner.js");
  const { SCENARIO_REGISTRY } = await import("../../scenarios/registry.js");
  const { assertFamilyContract } = await import("../../scenarios/family-interfaces.js");
  const { loadSettings } = await import("../../config/index.js");
  const { buildRoleProviderBundle } = await import("../../providers/index.js");
  const { initializeHookBus } = await import("../../extensions/index.js");
  const { resolveRunnableScenarioClass } = await import("../runnable-scenario-resolution.js");

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
  const { hookBus, loadedExtensions } = await initializeHookBus({
    extensions: settings.extensions,
    failFast: settings.extensionFailFast,
  });
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
    createRunner: (runnerOpts) =>
      new GenerationRunner({
        ...runnerOpts,
        hookBus,
        loadedExtensions,
      }),
  });
  const rendered = renderBenchmarkResult(result, plan.json);
  if (rendered.stderr) {
    console.error(rendered.stderr);
  }
  console.log(rendered.stdout);
}
