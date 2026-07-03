/**
 * `solve`, `tui`, `judge`, `improve`, `repl` command family
 * (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { resolve } from "node:path";
import { asDbPath } from "../../domain/ids.js";
import type { LLMProvider } from "../../types/index.js";
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
    plan = planSolveCommand({ ...values, positionals }, parsePositiveInteger);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
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
    console.error(errorMessage(error));
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
      headless: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { buildHeadlessTuiOutput, buildInteractiveTuiRequest, planTuiCommand, TUI_HELP_TEXT } =
    await import("../tui-command-workflow.js");

  if (values.help) {
    console.log(TUI_HELP_TEXT);
    process.exit(0);
  }

  const plan = planTuiCommand(values, !!process.stdout.isTTY);

  const { RunManager, InteractiveServer } = await import("../../server/index.js");
  const { loadSettings } = await import("../../config/index.js");
  const { resolveProviderConfig } = await import("../../providers/index.js");
  const settings = loadSettings();
  const providerConfig = resolveProviderConfig();
  const mgr = new RunManager({
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
  const { InteractiveTui } = await import("../../tui/app.js");

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
      createTask: (taskPrompt, rubric, taskProvider, taskModel, revisionPrompt, rlmConfig) =>
        new SimpleAgentTask(
          taskPrompt,
          rubric,
          taskProvider as LLMProvider,
          taskModel ?? undefined,
          revisionPrompt ?? undefined,
          rlmConfig,
        ),
      createLoop: (loopOpts) =>
        new ImprovementLoop(
          loopOpts as import("../../execution/improvement-loop.js").ImprovementLoopOpts,
        ),
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
