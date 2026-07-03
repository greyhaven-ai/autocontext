/**
 * `queue` and `worker` commands (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { asDbPath } from "../../domain/ids.js";
import { getMigrationsDir, getProvider, loadSavedAgentTaskScenario } from "./shared.js";

export async function cmdQueue(dbPath: string): Promise<void> {
  // AC-697 slice 2: detect `autoctx queue status` subcommand before
  // the existing `queue -s <spec>` parseArgs runs. The status
  // subcommand reports the queue-pending count (the semantic that
  // used to live under top-level `status` in TypeScript).
  //
  // AC-697 slice 8: also detect `autoctx queue add` as the canonical
  // queue-add subcommand. The remaining args are handed to the
  // existing parseArgs / planQueueCommand workhorse, so the only
  // difference vs. the legacy `queue -s <spec>` form is the leading
  // `add` token. The legacy form is preserved for backward compat.
  let subArgs = process.argv.slice(3);
  if (subArgs[0] === "add") {
    subArgs = subArgs.slice(1);
  }
  if (subArgs[0] === "status") {
    const { values: statusValues } = parseArgs({
      args: subArgs.slice(1),
      options: { json: { type: "boolean" }, help: { type: "boolean", short: "h" } },
    });
    if (statusValues.help) {
      console.log(
        "autoctx queue status [--json]\n\nShow the count of pending tasks in the background queue.",
      );
      process.exit(0);
    }
    const { executeStatusCommandWorkflow, renderStatusResult } =
      await import("../queue-status-command-workflow.js");
    const { SQLiteStore } = await import("../../storage/index.js");
    const store = new SQLiteStore(asDbPath(dbPath));
    const result = executeStatusCommandWorkflow({
      store,
      migrationsDir: getMigrationsDir(),
    });
    if (statusValues.json) {
      console.log(renderStatusResult(result));
    } else {
      console.log(`Pending queued tasks: ${result.pendingCount}`);
    }
    return;
  }

  const { values } = parseArgs({
    args: subArgs,
    options: {
      spec: { type: "string", short: "s" },
      prompt: { type: "string", short: "p" },
      rubric: { type: "string", short: "r" },
      "browser-url": { type: "string" },
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

  const { getQueueUsageExitCode, planQueueCommand, QUEUE_HELP_TEXT, renderQueuedTaskResult } =
    await import("../queue-status-command-workflow.js");

  if (values.help || !values.spec) {
    console.log(QUEUE_HELP_TEXT);
    process.exit(getQueueUsageExitCode(!!values.help));
  }

  const { SQLiteStore } = await import("../../storage/index.js");
  const { enqueueTask } = await import("../../execution/task-runner.js");
  const savedScenario = await loadSavedAgentTaskScenario(values.spec);

  const store = new SQLiteStore(asDbPath(dbPath));
  const migrationsDir = getMigrationsDir();
  store.migrate(migrationsDir);

  const plan = planQueueCommand(values, savedScenario);
  const id = enqueueTask(store, plan.specName, plan.request);

  console.log(renderQueuedTaskResult({ taskId: id, specName: plan.specName }));
  store.close();
}

export async function cmdWorker(dbPath: string): Promise<void> {
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: {
      "poll-interval": { type: "string", default: "60" },
      concurrency: { type: "string", default: "1" },
      "max-empty-polls": { type: "string", default: "0" },
      model: { type: "string" },
      once: { type: "boolean" },
      json: { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });

  const { planWorkerCommand, renderWorkerResult, resolveWorkerConcurrency, WORKER_HELP_TEXT } =
    await import("../worker-command-workflow.js");

  if (values.help) {
    console.log(WORKER_HELP_TEXT);
    process.exit(0);
  }

  const plan = planWorkerCommand(values);
  const { SQLiteStore } = await import("../../storage/index.js");
  const { createTaskRunnerFromSettings } = await import("../../execution/task-runner.js");
  const { loadSettings } = await import("../../config/index.js");
  const { initializeHookBus } = await import("../../extensions/index.js");

  const settings = loadSettings();
  const store = new SQLiteStore(asDbPath(dbPath));
  store.migrate(getMigrationsDir());
  const { provider, model } = await getProvider(plan.model ? { model: plan.model } : {});
  const concurrency = resolveWorkerConcurrency(provider, plan.concurrency);

  const { hookBus } = await initializeHookBus({
    extensions: settings.extensions,
    failFast: settings.extensionFailFast,
  });

  const runner = createTaskRunnerFromSettings({
    settings,
    store,
    provider,
    model: plan.model ?? model,
    pollInterval: plan.pollInterval,
    maxConsecutiveEmpty: plan.maxEmptyPolls,
    concurrency,
    hookBus,
  });

  const handleShutdown = () => runner.shutdown();
  process.once("SIGINT", handleShutdown);
  process.once("SIGTERM", handleShutdown);

  try {
    const tasksProcessed = plan.once ? await runner.runBatch(concurrency) : await runner.run();
    console.log(
      renderWorkerResult({
        mode: plan.once ? "once" : "daemon",
        tasksProcessed,
        pollInterval: plan.pollInterval,
        concurrency,
        json: plan.json,
      }),
    );
  } finally {
    process.off("SIGINT", handleShutdown);
    process.off("SIGTERM", handleShutdown);
    provider.close?.();
    store.close();
  }
}
