/**
 * `solve`, `tui`, `judge`, `improve`, `repl` command family
 * (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { resolve } from "node:path";
import { asDbPath } from "../../domain/ids.js";
import type { AgentTaskInterface, LLMProvider } from "../../types/index.js";
import {
  errorMessage,
  getMigrationsDir,
  getProvider,
  loadSavedAgentTaskScenario,
  parsePositiveInteger,
} from "./shared.js";

export async function cmdSolve(dbPath: string): Promise<void> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(3),
    allowPositionals: true,
    options: {
      description: { type: "string", short: "d" },
      gens: { type: "string", short: "g" },
      generations: { type: "string" },
      iterations: { type: "string" },
      timeout: { type: "string" },
      "generation-time-budget": { type: "string" },
      family: { type: "string" },
      output: { type: "string" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    executeSolveCommandWorkflow,
    planSolveCommand,
    renderSolveCommandSummary,
    SOLVE_HELP_TEXT,
    writeSolveOutputFile,
  } = await import("../solve-command-workflow.js");

  if (values.help) {
    console.log(SOLVE_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planSolveCommand(
      { ...values, gens: values.gens ?? values.generations, positionals },
      parsePositiveInteger,
    );
  } catch (error) {
    const message = errorMessage(error).replace(/^Error:\s*/, "");
    if (values.json) {
      process.stderr.write(`${JSON.stringify({ error: message })}\n`);
    } else {
      console.error(`Error: ${message}`);
    }
    process.exit(2);
  }

  const { SQLiteStore } = await import("../../storage/index.js");
  const { loadSettings } = await import("../../config/index.js");
  const { SolveManager } = await import("../../knowledge/solver.js");

  const settings = loadSettings();
  const store = new SQLiteStore(asDbPath(dbPath));
  store.migrate(getMigrationsDir());

  let provider: LLMProvider | undefined;
  try {
    provider = (await getProvider()).provider;
    const summary = await executeSolveCommandWorkflow({
      manager: new SolveManager({
        provider,
        agentProvider: settings.agentProvider,
        store,
        runsRoot: resolve(settings.runsRoot),
        knowledgeRoot: resolve(settings.knowledgeRoot),
      }),
      plan,
    });
    if (plan.outputPath) {
      writeSolveOutputFile(summary.result, resolve(plan.outputPath));
      summary.outputPath = resolve(plan.outputPath);
    }
    console.log(renderSolveCommandSummary(summary, plan.json));
  } catch (error) {
    const message = errorMessage(error).replace(/^Error:\s*/, "");
    if (plan.json) {
      process.stderr.write(`${JSON.stringify({ error: message })}\n`);
    } else {
      console.error(`Error: ${message}`);
    }
    provider?.close?.();
    process.exit(1);
  } finally {
    provider?.close?.();
    store.close();
  }
}

export async function cmdTui(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      port: { type: "string", default: "8000" },
      connect: { type: "string" },
      headless: { type: "boolean" },
      admin: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { buildHeadlessTuiOutput, planTuiCommand, TUI_HELP_TEXT } =
    await import("../tui-command-workflow.js");

  if (values.help) {
    console.log(TUI_HELP_TEXT);
    process.exit(0);
  }

  const plan = planTuiCommand(values, !!process.stdout.isTTY);

  const { InteractiveServer, RunManager } = await import("../../server/index.js");
  const { resolveServerAuthToken } = await import("../../server/server-auth.js");
  const { loadSettings } = await import("../../config/index.js");
  const settings = loadSettings();
  let serverAuthToken = resolveServerAuthToken();
  if (!plan.connect && serverAuthToken === null) {
    const { randomBytes } = await import("node:crypto");
    // The embedded local server and TUI share this process-only key. It is not
    // exported to the environment or persisted, so child agent CLIs cannot
    // reuse the operator's control-plane authority.
    serverAuthToken = randomBytes(32).toString("hex");
  }
  let mgr: InstanceType<typeof RunManager> | null = null;
  if (!plan.connect) {
    const { resolveProviderConfig } = await import("../../providers/index.js");
    const providerConfig = resolveProviderConfig();
    mgr = new RunManager({
      dbPath,
      migrationsDir: getMigrationsDir(),
      runsRoot: resolve(settings.runsRoot),
      knowledgeRoot: resolve(settings.knowledgeRoot),
      skillsRoot: resolve(settings.skillsRoot),
      providerType: providerConfig.providerType,
      apiKey: providerConfig.apiKey,
      baseUrl: providerConfig.baseUrl,
      model: providerConfig.model,
    });
  }
  const server = mgr
    ? new InteractiveServer({ runManager: mgr, port: plan.port, authToken: serverAuthToken ?? undefined })
    : null;
  if (server) await server.start();
  const endpoint = plan.connect ?? server!.url;

  if (plan.headless) {
    for (const line of buildHeadlessTuiOutput({
      serverUrl: endpoint,
      scenarios: mgr?.listScenarios() ?? [],
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
    if (server) await server.stop();
    return;
  }

  try {
    const { mkdirSync } = await import("node:fs");
    const { join } = await import("node:path");
    const { startInteractiveTui } = await import("../../tui/app.js");
    const { TuiReadModelClient } = await import("../../tui/read-model-client.js");
    const { TuiSession } = await import("../../tui/session.js");
    const { WebSocketTuiTransport } = await import("../../tui/transport.js");
    const logDirectory = join(resolve(settings.runsRoot), "_tui", "logs");
    mkdirSync(logDirectory, { recursive: true });
    const session = new TuiSession(new WebSocketTuiTransport(endpoint, {
      authToken: serverAuthToken,
      authCapabilities: plan.admin
        ? ["content:read", "control:admin", "host:execute"]
        : ["content:read", "control:operate", "host:execute"],
    }));
    const app = startInteractiveTui({
      session,
      readModels: new TuiReadModelClient(endpoint, { authToken: serverAuthToken }),
      logDirectory,
    });
    const stopOnSignal = () => app.stop();
    process.once("SIGINT", stopOnSignal);
    process.once("SIGTERM", stopOnSignal);
    try {
      await app.done;
    } finally {
      process.off("SIGINT", stopOnSignal);
      process.off("SIGTERM", stopOnSignal);
      app.stop();
    }
  } finally {
    if (server) await server.stop();
  }
}

export async function cmdJudge(_dbPath: string): Promise<void> {
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
  } = await import("../judge-command-workflow.js");

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

  const { loadSettings } = await import("../../config/index.js");
  const { initializeHookBus } = await import("../../extensions/index.js");
  const settings = loadSettings();
  const { hookBus } = await initializeHookBus({
    extensions: settings.extensions,
    failFast: settings.extensionFailFast,
  });
  const { provider, model } = await getProvider();
  try {
    const { LLMJudge } = await import("../../judge/index.js");
    const { createAgentTask: createNativeAgentTask } =
      await import("../../scenarios/agent-task-factory.js");
    const savedScenario = values.scenario
      ? await loadSavedAgentTaskScenario(values.scenario)
      : null;
    if (values.scenario && !savedScenario) {
      throw new Error(`Unknown saved custom scenario: ${values.scenario}`);
    }

    const plan = planJudgeCommand(values, savedScenario);

    const result = await executeJudgeCommandWorkflow({
      plan,
      provider,
      model: model ?? undefined,
      createJudge: (judgeOpts) => {
        const provider = judgeOpts.provider as LLMProvider;
        return new LLMJudge({
          provider,
          model: judgeOpts.model ?? provider.defaultModel(),
          rubric: judgeOpts.rubric,
          hookBus,
        });
      },
      createAgentTask: (taskOpts) =>
        createNativeAgentTask({
          name: taskOpts.name,
          spec:
            taskOpts.model && !taskOpts.spec.judgeModel
              ? { ...taskOpts.spec, judgeModel: taskOpts.model }
              : taskOpts.spec,
          provider: taskOpts.provider as LLMProvider,
          hookBus,
        }),
    });

    console.log(renderJudgeResult(result));
  } catch (error) {
    console.error(errorMessage(error));
    provider.close?.();
    process.exit(1);
  } finally {
    provider.close?.();
  }
}

export async function cmdImprove(_dbPath: string): Promise<void> {
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
  } = await import("../improve-command-workflow.js");

  const usageExitCode = getImproveUsageExitCode(values);
  if (usageExitCode !== null) {
    console.log(IMPROVE_HELP_TEXT);
    process.exit(usageExitCode);
  }

  const { provider, model } = await getProvider();
  try {
    const { SimpleAgentTask } = await import("../../execution/task-runner.js");
    const { ImprovementLoop } = await import("../../execution/improvement-loop.js");
    const { createStructuredAgentTaskWorkflow } =
      await import("../../execution/structured-agent-task-workflow.js");
    const savedScenario = values.scenario
      ? await loadSavedAgentTaskScenario(values.scenario)
      : null;
    if (values.scenario && !savedScenario) {
      throw new Error(`Unknown saved custom scenario: ${values.scenario}`);
    }

    const plan = planImproveCommand(values, savedScenario, parsePositiveInteger);

    const result = await executeImproveCommandWorkflow({
      plan,
      provider,
      model,
      savedScenario,
      createTask: (
        taskPrompt,
        rubric,
        taskProvider,
        taskModel,
        revisionPrompt,
        rlmConfig,
        candidateGrounding,
      ) =>
        new SimpleAgentTask(
          taskPrompt,
          rubric,
          taskProvider as LLMProvider,
          taskModel ?? undefined,
          revisionPrompt ?? undefined,
          rlmConfig,
          undefined,
          undefined,
          candidateGrounding,
        ),
      createStructuredTask: (taskOpts) =>
        createStructuredAgentTaskWorkflow({
          ...taskOpts,
          provider: taskOpts.provider as LLMProvider,
        }),
      createLoop: (loopOpts) =>
        new ImprovementLoop({
          ...loopOpts,
          task: loopOpts.task as AgentTaskInterface,
        }),
      now: () => performance.now(),
    });

    const rendered = renderImproveResult(result, plan.verbose);
    for (const line of rendered.stderrLines) {
      console.error(line);
    }
    console.log(rendered.stdout);
  } catch (error) {
    console.error(errorMessage(error));
    provider.close?.();
    process.exit(1);
  } finally {
    provider.close?.();
  }
}

export async function cmdRepl(_dbPath: string): Promise<void> {
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

  const { buildReplSessionRequest, getReplUsageExitCode, planReplCommand, REPL_HELP_TEXT } =
    await import("../repl-command-workflow.js");

  if (values.help || (!values.scenario && (!values.prompt || !values.rubric))) {
    console.log(REPL_HELP_TEXT);
    process.exit(getReplUsageExitCode(!!values.help));
  }

  const { provider, model } = await getProvider();
  try {
    const { runAgentTaskRlmSession } = await import("../../rlm/agent-task.js");
    const savedScenario = values.scenario
      ? await loadSavedAgentTaskScenario(values.scenario)
      : null;
    if (values.scenario && !savedScenario) {
      throw new Error(`Unknown saved custom scenario: ${values.scenario}`);
    }
    const plan = planReplCommand(values, savedScenario);

    const result = await runAgentTaskRlmSession(
      buildReplSessionRequest({
        provider,
        model,
        plan,
      }),
    );

    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(errorMessage(error));
    provider.close?.();
    process.exit(1);
  } finally {
    provider.close?.();
  }
}
